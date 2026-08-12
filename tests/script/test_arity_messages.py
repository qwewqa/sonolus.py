"""Wording of the argument-binding errors the compiler and the level-data constructor raise.

`run_compiled` rather than `run_and_validate` for the compiled cases: plain Python rejects these calls too, but
with its own wording, so the oracle's exact-message comparison would fail on a message that is not what is
pinned. The archetype constructor runs on the host only, so its cases call it directly.
"""

import math
import re

import pytest

from sonolus.backend.mode import Mode
from sonolus.backend.ops import Op
from sonolus.build.compile import callback_to_cfg
from sonolus.script.archetype import PlayArchetype, entity_memory, imported
from sonolus.script.array import Array
from sonolus.script.internal.context import ModeContextState, ProjectContextState, RuntimeChecks
from sonolus.script.internal.error import CompilationError
from sonolus.script.internal.native import native_function
from sonolus.script.record import Record
from tests.script.conftest import run_compiled


class Point(Record):
    x: float
    y: float

    def scaled(self, factor: float) -> float:
        return (self.x + self.y) * factor


class Arch(PlayArchetype):
    name = "Arch"

    # Two of each: a call omitting the second is what separates the partial binding these two sites do from the
    # ordinary binding every other site does.
    x: int = imported()
    y: int = imported()
    m: int = entity_memory()
    n: int = entity_memory()


def compile_with_arch(callback):
    """Compile `callback` with `Arch` registered, which `spawn` needs to resolve an archetype id."""
    project_state = ProjectContextState(runtime_checks=RuntimeChecks.NONE)
    mode_state = ModeContextState(Mode.PLAY, [Arch])
    return callback_to_cfg(project_state, mode_state, callback, "updateSequential")


def test_unexpected_keyword_to_a_user_function_names_the_keyword_and_the_callee():
    def user_fn(a, b):
        return a + b

    def fn():
        return user_fn(1.0, c=2.0)

    with pytest.raises(CompilationError) as exc_info:
        run_compiled(fn)

    message = str(exc_info.value)
    assert "got an unexpected keyword argument 'c'" in message
    assert message.startswith(f"{user_fn.__qualname__}(")


def test_missing_argument_to_a_user_function_names_the_callee():
    def user_fn(a, b):
        return a + b

    def fn():
        return user_fn(1.0)

    with pytest.raises(CompilationError) as exc_info:
        run_compiled(fn)

    message = str(exc_info.value)
    assert message.startswith(f"{user_fn.__qualname__}(")
    assert "'b'" in message


def test_too_many_arguments_to_a_user_function_names_the_callee():
    def user_fn(a, b):
        return a + b

    def fn():
        return user_fn(1.0, 2.0, 3.0)

    with pytest.raises(CompilationError) as exc_info:
        run_compiled(fn)

    assert str(exc_info.value).startswith(f"{user_fn.__qualname__}(")


def test_unexpected_keyword_to_a_record_method_names_the_keyword_and_the_callee():
    def fn():
        return Point(1.0, 2.0).scaled(f=2.0)

    with pytest.raises(CompilationError) as exc_info:
        run_compiled(fn)

    message = str(exc_info.value)
    assert message.startswith("Point.scaled(")
    assert "got an unexpected keyword argument 'f'" in message


def test_unexpected_keyword_to_a_builtin_with_a_plain_impl_names_the_builtin():
    # The prefix a traced call adds is the impl's private name; the builtin-naming rewrite is what turns it
    # into the public one, so this also pins that the two stay compatible.
    def fn():
        return math.sqrt(y=1.0)

    with pytest.raises(CompilationError) as exc_info:
        run_compiled(fn)

    message = str(exc_info.value)
    assert message.startswith("sqrt(")
    assert "got an unexpected keyword argument 'y'" in message
    assert "_sqrt" not in message


def test_unexpected_keyword_leaving_no_parameter_unfilled_names_the_keyword():
    def fn():
        return round(1.0, digits=2)

    with pytest.raises(CompilationError) as exc_info:
        run_compiled(fn)

    message = str(exc_info.value)
    assert message.startswith("round(")
    assert "got an unexpected keyword argument 'digits'" in message


def test_a_keyword_a_record_method_does_accept_still_binds():
    def fn():
        return Point(1.0, 2.0).scaled(factor=3.0)

    assert run_compiled(fn) == 9.0


def test_a_keyword_a_user_function_does_accept_still_binds():
    def user_fn(a, b):
        return a - b

    def fn():
        return user_fn(b=1.0, a=Array(5.0, 6.0)[0])

    assert run_compiled(fn) == 4.0


def test_unexpected_keyword_to_a_record_constructor_names_the_keyword_and_the_callee():
    def fn():
        return Point(x=1.0, z=2.0).x

    with pytest.raises(CompilationError) as exc_info:
        run_compiled(fn)

    message = str(exc_info.value)
    assert message.startswith("Point(")
    assert "got an unexpected keyword argument 'z'" in message


def test_a_keyword_a_record_constructor_does_accept_still_binds():
    def fn():
        return Point(y=1.0, x=Array(5.0, 6.0)[0]).x

    assert run_compiled(fn) == 5.0


def test_unexpected_keyword_to_a_compiled_lambda_names_the_keyword_and_the_callee():
    def fn():
        f = lambda a, b: a + b  # noqa: E731
        return f(a=1.0, c=2.0)

    with pytest.raises(CompilationError) as exc_info:
        run_compiled(fn)

    message = str(exc_info.value)
    assert message.startswith("<lambda>(")
    assert "got an unexpected keyword argument 'c'" in message


def test_unexpected_keyword_to_a_nested_def_names_the_keyword_and_the_callee():
    def fn():
        def inner(a, b):
            return a + b

        return inner(a=1.0, c=2.0)

    with pytest.raises(CompilationError) as exc_info:
        run_compiled(fn)

    message = str(exc_info.value)
    assert message.startswith("inner(")
    assert "got an unexpected keyword argument 'c'" in message


def test_unexpected_keyword_to_a_level_data_constructor_names_the_keyword_and_the_callee():
    with pytest.raises(TypeError) as exc_info:
        Arch(x=1, c=2)

    message = str(exc_info.value)
    assert message.startswith("Arch(")
    assert "got an unexpected keyword argument 'c'" in message


def test_a_level_data_constructor_still_zero_fills_the_imports_it_is_not_passed():
    # The constructor binds partially by design: an import the call omits is zero-filled, not rejected.
    entity = Arch(x=1)

    assert [entry["value"] for entry in entity._level_data_entries()] == [1, 0]


def test_unexpected_keyword_to_spawn_names_the_keyword_and_the_callee():
    def fn():
        Arch.spawn(nope=1)

    with pytest.raises(CompilationError) as exc_info:
        compile_with_arch(fn)

    message = str(exc_info.value)
    assert message.startswith("Arch.spawn(")
    assert "got an unexpected keyword argument 'nope'" in message


def test_spawn_still_accepts_a_call_that_omits_a_memory_field():
    # spawn binds partially too: the memory fields the call omits are zero-filled rather than rejected.
    def fn():
        Arch.spawn(m=1)

    compile_with_arch(fn)


def test_too_few_arguments_to_a_native_function_names_the_builtin():
    def fn():
        return math.sin()

    with pytest.raises(CompilationError) as exc_info:
        run_compiled(fn)

    message = str(exc_info.value)
    assert message.startswith("sin(")
    assert "1 argument," in message
    assert "_sin" not in message


def test_too_few_arguments_to_a_two_parameter_native_function_names_the_builtin():
    def fn():
        return math.atan2(1.0)

    with pytest.raises(CompilationError) as exc_info:
        run_compiled(fn)

    message = str(exc_info.value)
    assert message.startswith("atan2(")
    assert "2 arguments," in message
    assert "_atan2" not in message


def test_too_many_runtime_arguments_to_a_native_function_names_the_builtin():
    # Array indexing keeps the first argument runtime-valued, so the call cannot fold and takes the emitting
    # path, where a different raise site reports the arity than the constant-folded call does.
    def fn():
        values = Array(1.0, 2.0)
        return math.sin(values[0], 2.0)

    with pytest.raises(CompilationError) as exc_info:
        run_compiled(fn)

    message = str(exc_info.value)
    assert message.startswith("sin(")
    assert "_sin" not in message


def test_too_few_arguments_to_a_native_function_with_a_default_counts_the_required_ones():
    # No shipped native function has a defaulted parameter, so the count the guard reports can only be
    # separated from the parameter count by a synthetic one.
    @native_function(Op.Add)
    def two_or_three(a, b, c=0.0):
        return a + b + c

    def fn():
        return two_or_three(1.0)

    with pytest.raises(CompilationError) as exc_info:
        run_compiled(fn)

    message = str(exc_info.value)
    assert message.startswith(f"{two_or_three.__qualname__}(")
    assert "2 arguments," in message


def _given_count(message: str) -> int:
    match = re.search(r"but (\d+) (?:was|were) given", message)
    assert match is not None, message
    return int(match.group(1))


@pytest.mark.parametrize(
    ("make_fn", "written"),
    [
        (lambda: bool(1.0, 2.0, 3.0), 3),
        (lambda: float(1.0, 2.0, 3.0), 3),
        (lambda: int(1.0, 2.0, 3.0), 3),
        (lambda: dict(1.0, 2.0, 3.0), 3),
        (lambda: set(1.0, 2.0, 3.0), 3),
        (lambda: type(1.0, 2.0), 2),
        (lambda: type(1.0, 2.0, 3.0, 4.0), 4),
        (lambda: len(range(1.0, 2.0, 3.0, 4.0, 5.0)), 5),
        (lambda: dict(1.0, 2.0, 3.0, a=1.0), 3),
    ],
    ids=["bool", "float", "int", "dict", "set", "type2", "type4", "range", "dict_kwargs"],
)
def test_a_builtin_arity_error_counts_the_arguments_the_author_wrote(make_fn, written):
    # The invariant rather than nine literal strings: any impl later given a receiver would otherwise join the
    # miscounting set without failing anything.
    with pytest.raises(CompilationError) as exc_info:
        run_compiled(make_fn)

    assert _given_count(str(exc_info.value)) == written


@pytest.mark.parametrize(
    ("make_fn", "declared"),
    [
        (lambda: bool(1.0, 2.0, 3.0), "takes from 0 to 1 positional arguments"),
        (lambda: type(1.0, 2.0), "takes 1 positional argument"),
        (lambda: len(range(1.0, 2.0, 3.0, 4.0, 5.0)), "takes from 1 to 3 positional arguments"),
    ],
    ids=["bool", "type", "range"],
)
def test_a_builtin_arity_error_declares_the_range_the_builtin_accepts(make_fn, declared):
    # The subset's own range, which for int and type is narrower than CPython's.
    with pytest.raises(CompilationError) as exc_info:
        run_compiled(make_fn)

    assert declared in str(exc_info.value)


def test_too_many_arguments_to_a_builtin_with_a_plain_impl_names_the_builtin():
    # A plain-function impl is traced rather than called, so its arity error carries no count at all and only
    # needs the name; the counted family above is exactly the impls a receiver is passed to.
    def fn():
        return sum(Array(1.0, 2.0), 0.0, 1.0)

    with pytest.raises(CompilationError) as exc_info:
        run_compiled(fn)

    message = str(exc_info.value)
    assert message.startswith("sum(")
    assert "_sum" not in message


def test_a_builtin_missing_argument_error_is_left_alone():
    def fn():
        return type()

    with pytest.raises(CompilationError) as exc_info:
        run_compiled(fn)

    message = str(exc_info.value)
    assert message.startswith("type() missing 1 required positional argument")
    assert "_Type" not in message


def test_a_builtin_arity_error_never_prints_a_sentinel_default():
    def fn():
        return set(1.0, 2.0, 3.0)

    with pytest.raises(CompilationError) as exc_info:
        run_compiled(fn)

    message = str(exc_info.value)
    assert "0x" not in message
    assert "object object" not in message


def _helper(a, b=0, c=0):
    return a * 100 + b * 10 + c


def test_a_keyword_supplied_after_a_dict_splat_is_rejected():
    def fn():
        return _helper(1, **{"b": 1}, b=2)  # noqa: PIE804, PLE1132

    with pytest.raises(CompilationError, match=re.escape("got multiple values for keyword argument 'b'")):
        run_compiled(fn)


def test_a_dict_splat_over_an_earlier_keyword_is_rejected():
    def fn():
        return _helper(1, b=2, **{"b": 1})  # noqa: PIE804, PLE1132

    with pytest.raises(CompilationError, match=re.escape("got multiple values for keyword argument 'b'")):
        run_compiled(fn)


def test_two_dict_splats_sharing_a_key_are_rejected():
    def fn():
        d1 = {"b": 1}
        d2 = {"b": 2}
        return _helper(1, **d1, **d2)

    with pytest.raises(CompilationError, match=re.escape("got multiple values for keyword argument 'b'")):
        run_compiled(fn)


def test_a_duplicate_keyword_to_a_record_constructor_is_rejected():
    def fn():
        return Point(**{"x": 1.0}, x=2.0, y=0.0).x  # noqa: PIE804, PLE1132

    with pytest.raises(CompilationError, match=re.escape("got multiple values for keyword argument 'x'")):
        run_compiled(fn)


def test_a_duplicate_keyword_to_a_builtin_is_rejected():
    def fn():
        return max(3.0, 1.0, **{"key": abs}, key=abs)  # noqa: PIE804, PLE1132

    with pytest.raises(CompilationError, match=re.escape("got multiple values for keyword argument 'key'")):
        run_compiled(fn)


def test_a_dict_splat_with_no_collision_is_still_accepted():
    def fn():
        return _helper(1, **{"b": 2}, c=3)  # noqa: PIE804

    assert run_compiled(fn) == 123
