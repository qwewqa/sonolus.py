"""Tests for `math` module functions as implemented for sonolus scripts."""

import math

from hypothesis import given
from hypothesis import strategies as st

from sonolus.script.array import Array
from sonolus.script.internal.context import ctx
from sonolus.script.internal.impl import validate_value
from sonolus.script.internal.math_impls import _floor  # noqa: PLC2701
from sonolus.script.internal.meta_fn import meta_fn
from sonolus.script.internal.random import _random  # noqa: PLC2701
from sonolus.script.num import _is_num  # noqa: PLC2701
from tests.script.conftest import run_and_validate

angles = st.floats(min_value=-99999, max_value=99999, allow_nan=False, allow_infinity=False)


@meta_fn
def bb(x):
    """Return x as a value the compiler cannot constant fold, so the runtime op is emitted rather than folded."""
    if not ctx():
        return x
    x = validate_value(x)
    if _is_num(x):
        # floor(random()) is 0 at runtime but is not a compile-time constant.
        return x + _floor(_random())
    return x


@given(x=angles)
def test_degrees(x):
    def fn():
        return math.degrees(x)

    assert run_and_validate(fn) == math.degrees(x)


@given(x=angles)
def test_radians(x):
    def fn():
        return math.radians(x)

    assert run_and_validate(fn) == math.radians(x)


# The tests above fold at compile time and never emit Op.Degree/Op.Radian.


@given(x=angles)
def test_degrees_runtime(x):
    def fn():
        return math.degrees(bb(x))

    assert run_and_validate(fn) == math.degrees(x)


@given(x=angles)
def test_radians_runtime(x):
    def fn():
        return math.radians(bb(x))

    assert run_and_validate(fn) == math.radians(x)


def test_round_accepts_the_ndigits_keyword():
    def fn():
        return round(2.567, ndigits=2)

    assert run_and_validate(fn) == round(2.567, ndigits=2)


@given(x=angles)
def test_degrees_radians_round_trip(x):
    def fn():
        return Array(math.degrees(math.radians(x)), math.radians(math.degrees(x)))

    assert run_and_validate(fn) == Array(math.degrees(math.radians(x)), math.radians(math.degrees(x)))


@given(x=angles)
def test_degrees_radians_round_trip_runtime(x):
    def fn():
        return Array(math.degrees(math.radians(bb(x))), math.radians(math.degrees(bb(x))))

    assert run_and_validate(fn) == Array(math.degrees(math.radians(x)), math.radians(math.degrees(x)))
