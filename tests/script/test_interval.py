from hypothesis import given
from hypothesis import strategies as st

from sonolus.script.interval import Interval
from tests.script.conftest import validate_dual_run

INTS = st.integers(min_value=-999999, max_value=999999)
FLOATS = st.floats(min_value=-999999, max_value=999999, allow_infinity=False, allow_nan=False)
DIVISOR_FLOATS = FLOATS.filter(lambda x: abs(x) > 1e-6)


@given(FLOATS, FLOATS)
def test_interval_length(left, right):
    def fn():
        interval = Interval(left, right)
        return interval.length

    assert validate_dual_run(fn) == right - left


@given(FLOATS, FLOATS)
def test_interval_mid(left, right):
    def fn():
        interval = Interval(left, right)
        return interval.mid

    assert validate_dual_run(fn) == (left + right) / 2


@given(FLOATS, FLOATS)
def test_interval_is_empty(left, right):
    def fn():
        interval = Interval(left, right)
        return interval.is_empty

    assert validate_dual_run(fn) == (left > right)


@given(FLOATS, FLOATS, FLOATS)
def test_interval_contains_float(left, right, value):
    def fn():
        interval = Interval(left, right)
        return value in interval

    assert validate_dual_run(fn) == (left <= value <= right)


@given(FLOATS, FLOATS, FLOATS, FLOATS)
def test_interval_contains_interval(left, right, other_left, other_right):
    def fn():
        interval = Interval(left, right)
        other = Interval(other_left, other_right)
        return other in interval

    assert validate_dual_run(fn) == (left <= other_left and other_right <= right)


@given(FLOATS, FLOATS, FLOATS)
def test_interval_add(left, right, value):
    def fn():
        interval = Interval(left, right)
        return interval + value

    assert validate_dual_run(fn) == Interval(left + value, right + value)


@given(FLOATS, FLOATS, FLOATS)
def test_interval_sub(left, right, value):
    def fn():
        interval = Interval(left, right)
        return interval - value

    assert validate_dual_run(fn) == Interval(left - value, right - value)


@given(FLOATS, FLOATS, FLOATS)
def test_interval_mul(left, right, value):
    def fn():
        interval = Interval(left, right)
        return interval * value

    assert validate_dual_run(fn) == Interval(left * value, right * value)


@given(FLOATS, FLOATS, DIVISOR_FLOATS)
def test_interval_truediv(left, right, value):
    def fn():
        interval = Interval(left, right)
        return interval / value

    assert validate_dual_run(fn) == Interval(left / value, right / value)


@given(INTS, INTS, INTS.filter(lambda x: x != 0))
def test_interval_floordiv(left, right, value):
    def fn():
        interval = Interval(left, right)
        return interval // value

    assert validate_dual_run(fn) == Interval(left // value, right // value)


@given(FLOATS, FLOATS, FLOATS, FLOATS)
def test_interval_and(left, right, other_left, other_right):
    def fn():
        interval = Interval(left, right)
        other = Interval(other_left, other_right)
        return interval & other

    assert validate_dual_run(fn) == Interval(max(left, other_left), min(right, other_right))


@given(FLOATS, FLOATS, FLOATS, FLOATS, FLOATS)
def test_interval_and_consistent_with_contains(left, right, other_left, other_right, value):
    def fn():
        interval = Interval(left, right)
        other = Interval(other_left, other_right)
        return ((value in interval) and (value in other)) == (value in (interval & other))

    assert validate_dual_run(fn)
