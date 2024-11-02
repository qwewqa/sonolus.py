from hypothesis import assume, given
from hypothesis import strategies as st

from sonolus.script.interval import Interval, remap, remap_clamped
from tests.script.conftest import implies, is_close, validate_dual_run

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


@given(FLOATS, FLOATS, FLOATS, FLOATS, FLOATS, FLOATS)
def test_interval_transitive_contains(left1, right1, left2, right2, left3, right3):
    def fn():
        interval1 = Interval(left1, right1)
        interval2 = Interval(left2, right2)
        interval3 = Interval(left3, right3)
        return implies(interval1 in interval2 in interval3, interval1 in interval3)

    assert validate_dual_run(fn)


@given(FLOATS, FLOATS, FLOATS, FLOATS, FLOATS)
def test_remap_inverse(left, right, other_left, other_right, value):
    assume(abs(right - left) > 1e-6 and abs(other_right - other_left) > 1e-6)

    def fn():
        remapped = remap(left, right, other_left, other_right, value)
        return remap(other_left, other_right, left, right, remapped)

    assert is_close(validate_dual_run(fn), value)


@given(FLOATS, FLOATS, FLOATS, FLOATS, FLOATS)
def test_remap_clamped_inverse(left, right, other_left, other_right, value):
    assume(abs(right - left) > 1e-6 and abs(other_right - other_left) > 1e-6)

    def fn():
        remapped = remap_clamped(left, right, other_left, other_right, value)
        return remap_clamped(other_left, other_right, left, right, remapped)

    assert is_close(validate_dual_run(fn), sorted([left, value, right])[1])


@given(FLOATS, FLOATS, FLOATS)
def test_lerp_unlerp_inverse(left, right, value):
    assume(abs(right - left) > 1e-6)

    def fn():
        interval = Interval(left, right)
        lerped = interval.lerp(value)
        return interval.unlerp(lerped)

    assert is_close(validate_dual_run(fn), value)


@given(FLOATS, FLOATS, FLOATS)
def test_unlerp_lerp_inverse(left, right, value):
    assume(abs(right - left) > 1e-6)

    def fn():
        interval = Interval(left, right)
        unlerped = interval.unlerp(value)
        return interval.lerp(unlerped)

    assert is_close(validate_dual_run(fn), value)


@given(FLOATS, FLOATS, FLOATS)
def test_lerp_clamped_unlerp_clamped_inverse(left, right, value):
    assume(abs(right - left) > 1e-6)

    def fn():
        interval = Interval(left, right)
        lerped_clamped = interval.lerp_clamped(value)
        return interval.unlerp_clamped(lerped_clamped)

    assert is_close(validate_dual_run(fn), sorted([0, value, 1])[1])


@given(FLOATS, FLOATS, FLOATS)
def test_unlerp_clamped_lerp_clamped_inverse(left, right, value):
    assume(abs(right - left) > 1e-6)

    def fn():
        interval = Interval(left, right)
        unlerped_clamped = interval.unlerp_clamped(value)
        return interval.lerp_clamped(unlerped_clamped)

    assert is_close(validate_dual_run(fn), sorted([left, value, right])[1])
