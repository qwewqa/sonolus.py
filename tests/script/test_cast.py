"""Typing cast preserves values and argument evaluation in compiled functions.

PYTEST_DONT_REWRITE
"""

import random
import traceback
import typing
from typing import cast

import pytest

from sonolus.script.debug import debug_log
from sonolus.script.internal.error import CompilationError
from sonolus.script.num import Num
from sonolus.script.record import Record
from tests.script.conftest import compile_fn, run_and_validate


class CastBox[T](Record):
    value: T


def test_cast_accepts_positional_keyword_and_unpacked_arguments():
    def fn():
        value = 13 + random.randrange(0, 1)
        first = cast(Num, value)
        second = cast(typ=Num, val=value)
        third = cast(val=value, typ=Num)
        fourth = cast(Num, val=value)
        fifth = cast(*(Num, value))
        sixth = typing.cast(**{"typ": Num, "val": value})  # ruff: ignore[unnecessary-dict-kwargs]
        return first + second + third + fourth + fifth + sixth

    assert run_and_validate(fn) == 78


def test_cast_runtime_num_snapshot_survives_source_mutation():
    def fn():
        original = 13 + random.randrange(0, 1)
        box = CastBox[Num](original)
        saved = cast(Num, box.value)
        alias = saved
        box.value = 29
        saved += 2
        debug_log(alias)
        debug_log(saved)
        debug_log(box.value)
        return alias * 100 + saved

    assert run_and_validate(fn) == 1315


def test_cast_num_argument_is_read_before_later_argument_mutates_source():
    def combine(first, second):
        return first * 100 + second

    def mutate(box):
        box.value = 29
        return box.value

    def fn():
        box = CastBox[Num](13 + random.randrange(0, 1))
        return combine(cast(Num, box.value), mutate(box))

    assert run_and_validate(fn) == 1329


def test_cast_preserves_record_identity_and_mutation():
    def fn():
        original = CastBox[Num](13 + random.randrange(0, 1))
        alias = cast(CastBox[Num], original)
        alias.value = 23
        assert original.value == 23
        original.value += 4
        return alias.value

    assert run_and_validate(fn) == 27


def test_cast_evaluates_ignored_type_and_value_in_argument_order():
    def type_argument():
        debug_log(1)
        return 123

    def value_argument():
        debug_log(2)
        return 17 + random.randrange(0, 1)

    def fn():
        first = cast(type_argument(), value_argument())
        second = cast(val=value_argument(), typ=type_argument())
        return first + second

    assert run_and_validate(fn) == 34


def test_nested_cast_supports_generic_type_arguments_and_constants():
    def fn():
        original = CastBox[Num](7 + random.randrange(0, 1))
        alias = typing.cast(CastBox[Num], cast(CastBox[Num], original))
        alias.value += 2
        assert original.value == 9
        original.value += 3
        assert cast(str, "tag") == "tag"
        assert cast(None, None) is None
        return alias.value + cast(Num, typing.cast(str, 5))

    assert run_and_validate(fn) == 17


def test_cast_preserves_callable_function_and_class_values():
    def increment(value):
        return value + 1

    def fn():
        constructor = cast(type, CastBox[Num])
        helper = cast(None, increment)
        box = constructor(4 + random.randrange(0, 1))
        return helper(box.value)

    assert run_and_validate(fn) == 5


def test_cast_in_runtime_loop_and_generator_preserves_values():
    def fn():
        box = CastBox[Num](random.randrange(0, 1))
        total = 0
        for _ in range(3):
            total += cast(Num, box.value)
            box.value += 1
        values = (cast(Num, i + total) for i in range(3))
        return sum(values)

    assert run_and_validate(fn) == 12


def missing_both():
    return cast()


def missing_value():
    return cast(Num)


def missing_type():
    return cast(val=1)


def extra_positional():
    return cast(Num, 1, 2)


def unexpected_keyword():
    return cast(Num, 1, extra=2)


def duplicate_value():
    return cast(Num, 1, val=2)


def duplicate_keyword_mapping():
    return cast(Num, val=1, **{"val": 2})  # ruff: ignore[unnecessary-dict-kwargs, repeated-keyword-argument]


@pytest.mark.parametrize(
    "fn",
    [
        missing_both,
        missing_value,
        missing_type,
        extra_positional,
        unexpected_keyword,
        duplicate_value,
        duplicate_keyword_mapping,
    ],
    ids=lambda fn: fn.__name__,
)
def test_cast_malformed_calls_keep_public_name_typeerror_and_call_site(fn, record_property):
    with pytest.raises(TypeError, match=r"\bcast\b") as python_error:
        fn()
    with pytest.raises(CompilationError, match=r"\bcast\b") as compiled_error:
        compile_fn(fn)

    cause = compiled_error.value.__cause__
    assert type(cause) is TypeError
    assert "cast" in str(cause)
    assert "_cast" not in str(cause)
    frames = traceback.extract_tb(compiled_error.value.__traceback__)
    assert any(frame.filename == fn.__code__.co_filename and frame.name == fn.__name__ for frame in frames)
    record_property("python_binding_error", str(python_error.value))
    record_property("compiled_binding_error", str(cause))
