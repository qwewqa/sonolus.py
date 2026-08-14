import math
import re

import pytest

from sonolus.backend.mode import Mode
from sonolus.backend.optimize.flow import cfg_to_text
from sonolus.build.compile import callback_to_cfg
from sonolus.script.archetype import PlayArchetype
from sonolus.script.array import Array
from sonolus.script.globals import level_memory
from sonolus.script.internal.builtin_impls import BUILTIN_IMPL_NAMES, BUILTIN_IMPLS
from sonolus.script.internal.context import ModeContextState, ProjectContextState
from sonolus.script.internal.error import CompilationError
from sonolus.script.internal.impl import validate_value
from sonolus.script.internal.visitor import clear_frontend_caches
from sonolus.script.maybe import Some
from sonolus.script.record import Record
from sonolus.script.runtime import level_score
from sonolus.script.stream import streams
from tests.script.conftest import run_and_validate, run_compiled


def compile_in(mode: Mode, callback, archetypes: list[type] | None = None):
    clear_frontend_caches()
    return callback_to_cfg(ProjectContextState(), ModeContextState(mode, archetypes), callback, "preprocess")


class _Plain:
    """A host object with the default repr, which is `<Cls object at 0xADDRESS>`."""


class Point(Record):
    x: int

    @property
    def doubled(self) -> int:
        return self.x * 2

    @property
    def rw(self) -> int:
        return self.x

    @rw.setter
    def rw(self, value: int):
        self.x = value


class Helper(Record):
    v: int

    @classmethod
    def make(cls, v: int) -> "Helper":
        return Helper(v)

    @staticmethod
    def twice(v: int) -> int:
        return v * 2


def test_validate_value_unhashable_gives_unsupported_error():
    # An unhashable, unsupported value (e.g. a list) must produce the intended
    # "Unsupported value" TypeError, not "unhashable type: 'list'" from a set-membership test.
    with pytest.raises(TypeError, match="Unsupported value"):
        validate_value([1, 2, 3])


def test_validate_value_names_the_type_of_a_value_with_no_repr_of_its_own():
    with pytest.raises(TypeError, match="Unsupported value") as exc_info:
        validate_value(_Plain())

    message = str(exc_info.value)
    assert "_Plain" in message
    assert "0x" not in message


def test_validate_value_keeps_a_repr_that_says_more_than_the_type():
    with pytest.raises(TypeError, match=r"Unsupported value: \[1, 2, 3\]"):
        validate_value([1, 2, 3])


def test_a_value_whose_initialization_fails_names_the_type_of_the_value():
    # A streams class is the reachable case: its fields are checked by the `_init_` that validate_value calls,
    # and the decorator hands back an instance, not the class.
    @streams
    class _BadStreams:
        offender: _Plain

    with pytest.raises(RuntimeError, match="Error initializing value") as exc_info:
        validate_value(_BadStreams)

    message = str(exc_info.value)
    assert "_BadStreams" in message
    assert "0x" not in message


def test_reading_a_field_off_the_record_class_names_the_field():
    # run_compiled, not run_and_validate: plain Python answers this read with the descriptor object rather
    # than raising, so there is no exception for the oracle to compare against.
    def fn():
        return Point.x

    with pytest.raises(CompilationError, match="Field 'x' must be accessed on an instance of Point"):
        run_compiled(fn)


def test_reading_a_property_off_the_record_class_names_the_property():
    def fn():
        return Point.doubled

    with pytest.raises(CompilationError, match="Property 'doubled' must be accessed on an instance of Point"):
        run_compiled(fn)


def test_getattr_on_the_record_class_names_the_field():
    # The getattr builtin has its own copy of the attribute lookup, so it needs its own case.
    def fn():
        return getattr(Point, "x")  # ruff: ignore[get-attr-with-constant]

    with pytest.raises(CompilationError, match="Field 'x' must be accessed on an instance of Point"):
        run_compiled(fn)


def test_writing_a_field_on_the_record_class_names_the_field():
    # run_compiled, not run_and_validate: plain Python just replaces the class attribute rather than raising,
    # so there is no exception for the oracle to compare against.
    def fn():
        Point.x = 5

    with pytest.raises(CompilationError, match="Field 'x' must be accessed on an instance of Point"):
        run_compiled(fn)


def test_writing_a_property_on_the_record_class_names_the_property():
    def fn():
        Point.doubled = 5

    with pytest.raises(CompilationError, match="Property 'doubled' must be accessed on an instance of Point"):
        run_compiled(fn)


def test_writing_a_settable_property_on_the_record_class_names_the_property():
    # `rw` has a working setter, so this is the case that proves the class-level write is rejected rather than
    # answered by calling the setter.
    def fn():
        Point.rw = 5

    with pytest.raises(CompilationError, match="Property 'rw' must be accessed on an instance of Point"):
        run_compiled(fn)


def test_setattr_on_the_record_class_names_the_field():
    # The setattr builtin has its own copy of the attribute lookup, so it needs its own case.
    def fn():
        setattr(Point, "x", 5)  # ruff: ignore[set-attr-with-constant]

    with pytest.raises(CompilationError, match="Field 'x' must be accessed on an instance of Point"):
        run_compiled(fn)


class _ScoreProbe(PlayArchetype):
    name = "ImplScoreProbe"
    is_scored = True


def test_a_class_level_write_the_metaclass_answers_still_works():
    # archetype_score_multiplier is declared on the archetype metaclass, so this write is one of the few a
    # class object legitimately accepts and must reach the descriptor rather than a diagnostic.
    def cb():
        _ScoreProbe.archetype_score_multiplier = 2.0

    cfg = compile_in(Mode.PLAY, cb, [_ScoreProbe])

    assert "ArchetypeScore[0] <- 2" in cfg_to_text(cfg)


def test_writing_a_field_on_an_instance_still_works():
    def fn():
        p = Point(3)
        p.x = 5
        return p.x

    assert run_and_validate(fn) == 5


def test_writing_a_settable_property_on_an_instance_still_works():
    def fn():
        p = Point(3)
        p.rw = 7
        return p.x

    assert run_and_validate(fn) == 7


def test_reading_a_property_off_an_instance_still_works():
    def fn():
        return Point(3).doubled

    assert run_and_validate(fn) == 6


def test_classmethod_and_staticmethod_through_the_class_still_work():
    def fn():
        return Helper.make(3).v * 10 + Helper.twice(4)

    assert run_and_validate(fn) == 38


@level_memory
class _Counters:
    hits: int


_COUNTER_ARRAY = level_memory(Array[int, 4])


def test_a_declared_global_unavailable_in_the_mode_names_the_declaration():
    # Level memory has no preview block, so preview is the mode that cannot answer for it.
    def cb():
        _Counters.hits = 1

    with pytest.raises(CompilationError, match="is not available in 'PREVIEW' mode") as exc_info:
        compile_in(Mode.PREVIEW, cb)

    message = str(exc_info.value)
    assert "_Counters" in message
    assert "0x" not in message


def test_a_global_of_a_bare_type_unavailable_in_the_mode_names_its_type():
    def cb():
        _COUNTER_ARRAY[0] = 1

    with pytest.raises(CompilationError, match="is not available in 'PREVIEW' mode") as exc_info:
        compile_in(Mode.PREVIEW, cb)

    message = str(exc_info.value)
    assert "Array" in message
    assert "0x" not in message


def test_a_runtime_global_unavailable_in_the_mode_names_the_modes_it_has():
    # level_score is public and documented as play and watch only, so tutorial is the wall an author hits.
    def cb():
        return level_score().perfect_multiplier

    with pytest.raises(CompilationError, match="is not available in 'TUTORIAL' mode") as exc_info:
        compile_in(Mode.TUTORIAL, cb)

    message = str(exc_info.value)
    assert "PLAY" in message
    assert "WATCH" in message
    assert "0x" not in message


_OVER_F32_TABLE = Array(1e39, 2.0, 3.0, 4.0)


def test_out_of_range_constant_is_reported_the_way_it_was_written():
    # A constant array read at a runtime index is interned into ROM, which is where the range check lives, so
    # the index has to be accumulated rather than written as a literal.
    def fn():
        index = 0
        for _ in range(3):
            index += 1
        return _OVER_F32_TABLE[index]

    with pytest.raises(CompilationError, match="out of range for engine data") as exc_info:
        run_and_validate(fn)

    message = str(exc_info.value)
    assert "1e+39" in message
    assert "999999999999999939709166371603178586112" not in message


def test_an_array_of_differing_constants_names_the_values_without_an_address():
    # Each compile-time constant is wrapped in a class of its own, and that class is what the message lists,
    # so the class name has to be readable on its own.
    def fn():
        return Array("a", "b")

    with pytest.raises(TypeError, match="values of the same type") as exc_info:
        run_and_validate(fn)

    message = str(exc_info.value)
    assert "'a'" in message
    assert "'b'" in message
    assert "0x" not in message


def test_starred_unpack_target_with_extra_values_names_the_unsupported_construct():
    # run_compiled, not run_and_validate: CPython unpacks this statement without raising, so there is no
    # exception for the oracle to compare against. A starred target absorbs any number of values, so the
    # arity messages would claim a count CPython does not require.
    def fn():
        a, *b = 1, 2, 3  # ruff: ignore[unused-variable]
        return a

    with pytest.raises(CompilationError, match="Starred assignment is not supported"):
        run_compiled(fn)


def test_starred_unpack_target_with_too_few_values_names_the_unsupported_construct():
    def fn():
        a, b, *c = (1,)  # ruff: ignore[unused-variable]
        return a

    with pytest.raises(CompilationError, match="Starred assignment is not supported"):
        run_compiled(fn)


def test_starred_for_target_names_the_unsupported_construct():
    # `for` targets go through the same assignment handling, so the same message must reach them.
    def fn():
        total = 0
        for a, *rest in ((1, 2, 3), (4, 5, 6)):  # ruff: ignore[unused-loop-control-variable]
            total += a
        return total

    with pytest.raises(CompilationError, match="Starred assignment is not supported"):
        run_compiled(fn)


def test_unpack_array_names_the_type_that_cannot_be_unpacked():
    # run_compiled, not run_and_validate: plain CPython unpacks an Array without raising, so the oracle has no
    # exception of its own to compare against.
    def fn():
        a, b = Array(1.0, 2.0)
        return a + b

    with pytest.raises(CompilationError, match=re.escape("Cannot unpack a value of type Array[Num, 2]")):
        run_compiled(fn)


def test_unpack_range_names_the_type_that_cannot_be_unpacked():
    def fn():
        a, b, c = range(3)
        return a + b + c

    with pytest.raises(CompilationError, match=re.escape("Cannot unpack a value of type Range")):
        run_compiled(fn)


def test_unpack_record_names_the_type_that_cannot_be_unpacked():
    def fn():
        x, y = Point(1)
        return x + y

    with pytest.raises(CompilationError, match=re.escape("Cannot unpack a value of type Point")):
        run_compiled(fn)


def test_unpack_for_target_names_the_type_that_cannot_be_unpacked():
    # `for` targets go through the same assignment handling, so the same message must reach them.
    def fn():
        total = 0.0
        for a, b in Array(1.0, 2.0):
            total += a + b
        return total

    with pytest.raises(CompilationError, match=re.escape("Cannot unpack a value of type Num")):
        run_compiled(fn)


def test_starred_call_argument_still_names_the_starred_expression():
    # The starred-expression message keeps serving the two sites that really do contain a star.
    def fn():
        return max(*Array(1.0, 2.0))

    with pytest.raises(CompilationError, match="Unsupported starred expression"):
        run_compiled(fn)


def test_starred_tuple_element_still_names_the_starred_expression():
    def fn():
        t = (*Array(1.0, 2.0), 3.0)
        return t[0]

    with pytest.raises(CompilationError, match="Unsupported starred expression"):
        run_compiled(fn)


def test_a_string_used_as_an_index_names_the_string_without_an_address():
    # A compile-time constant is wrapped in a class of its own, and here the message interpolates the wrapped
    # instance rather than its class, so the instance has to be readable on its own too.
    def fn():
        a = Array(1.0, 2.0, 3.0)
        return a["x"]

    with pytest.raises(CompilationError, match="Cannot accept") as exc_info:
        run_compiled(fn)

    message = str(exc_info.value)
    assert "Const['x']" in message
    assert "0x" not in message


def test_a_string_passed_as_a_record_field_names_the_string_without_an_address():
    def fn():
        return Point("x")

    with pytest.raises(CompilationError, match="Cannot accept") as exc_info:
        run_compiled(fn)

    message = str(exc_info.value)
    assert "Const['x']" in message
    assert "0x" not in message


def test_a_long_string_constant_is_shortened_in_a_message():
    long_text = "y" * 120

    def fn():
        a = Array(1.0, 2.0, 3.0)
        return a[long_text]

    with pytest.raises(CompilationError, match="Cannot accept") as exc_info:
        run_compiled(fn)

    message = str(exc_info.value)
    assert long_text not in message
    assert "yyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy..." in message


def test_a_tuple_used_as_an_index_names_no_address_and_the_expected_type():
    def fn():
        a = Array(1.0, 2.0, 3.0)
        return a[1, 2]

    with pytest.raises(CompilationError, match="Cannot accept") as exc_info:
        run_compiled(fn)

    message = str(exc_info.value)
    assert "0x" not in message
    assert "as Num" in message


def test_a_dict_passed_as_a_record_field_names_no_address():
    def fn():
        return Point({1: 2})

    with pytest.raises(CompilationError, match="Cannot accept") as exc_info:
        run_compiled(fn)

    message = str(exc_info.value)
    assert "0x" not in message
    assert "as Num" in message


def test_a_maybe_passed_as_a_record_field_names_no_address():
    def fn():
        return Point(Some(1.0))

    with pytest.raises(CompilationError, match="Cannot accept") as exc_info:
        run_compiled(fn)

    message = str(exc_info.value)
    assert "0x" not in message
    assert "as Num" in message


def test_a_generator_passed_as_a_record_field_names_no_address():
    def fn():
        def gen():
            yield 1

        return Point(gen())

    with pytest.raises(CompilationError, match="Cannot accept") as exc_info:
        run_compiled(fn)

    message = str(exc_info.value)
    assert "0x" not in message
    assert "as Num" in message


def test_constant_value_type_mismatch_names_both_constants_without_an_address():
    x_value = validate_value("x")
    z_type = type(validate_value("z"))

    with pytest.raises(ValueError, match="is not of type") as exc_info:
        z_type._accept_(x_value)

    message = str(exc_info.value)
    assert "Const['x']" in message
    assert "Const['z']" in message
    assert "0x" not in message


def test_a_keyword_argument_to_a_builtin_names_the_builtin():
    # run_compiled, not run_and_validate: CPython rejects this call too, but with its own wording, so the
    # oracle's exact-message comparison would fail on a message that is not the defect.
    def fn():
        return abs(x=-1.0)

    with pytest.raises(CompilationError) as exc_info:
        run_compiled(fn)

    message = str(exc_info.value)
    assert message.startswith("abs()")
    assert "_abs" not in message


def test_a_keyword_argument_to_a_converting_builtin_names_the_builtin():
    def fn():
        return bool(x=1.0)

    with pytest.raises(CompilationError) as exc_info:
        run_compiled(fn)

    message = str(exc_info.value)
    assert message.startswith("bool()")
    assert "_Bool" not in message


def test_a_keyword_argument_to_range_names_range():
    def fn():
        return len(range(stop=3))

    with pytest.raises(CompilationError) as exc_info:
        run_compiled(fn)

    message = str(exc_info.value)
    assert message.startswith("range()")
    assert "Range.frozen" not in message


def test_a_keyword_argument_to_a_math_function_names_the_function():
    def fn():
        return math.sin(x=1.0)

    with pytest.raises(CompilationError) as exc_info:
        run_compiled(fn)

    message = str(exc_info.value)
    assert message.startswith("sin()")
    assert "_sin" not in message


def test_an_error_raised_inside_a_builtin_is_left_alone():
    # Only a binding error names the impl. One raised from inside the impl body already names the builtin and
    # must not be rewritten.
    def fn():
        return abs(Array(1.0, 2.0))

    with pytest.raises(CompilationError, match=re.escape("bad operand type for abs()")) as exc_info:
        run_compiled(fn)

    assert "Array[Num, 2]" in str(exc_info.value)


def test_every_builtin_impl_has_a_public_name():
    # An impl missing from the reverse map leaks its private name out of a binding error, which is the whole
    # defect, so the map has to cover the registry rather than the cases anyone thought to test.
    missing = sorted(
        getattr(impl, "__qualname__", repr(impl))
        for impl in BUILTIN_IMPLS.values()
        if id(impl) not in BUILTIN_IMPL_NAMES
    )
    assert missing == []
