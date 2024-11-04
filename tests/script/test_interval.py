from hypothesis import assume, given
from hypothesis import strategies as st

from sonolus.script.interval import Interval, remap, remap_clamped
from tests.script.conftest import implies, is_close, validate_dual_run

ints = st.integers(min_value=-999999, max_value=999999)
floats = st.floats(min_value=-999999, max_value=999999, allow_infinity=False, allow_nan=False)
divisor_floats = floats.filter(lambda x: abs(x) > 1e-6)


@given(floats, floats)
def test_interval_length(start, end):
    def fn():
        interval = Interval(start, end)
        return interval.length

    assert validate_dual_run(fn) == end - start


@given(floats, floats)
def test_interval_mid(start, end):
    def fn():
        interval = Interval(start, end)
        return interval.mid

    assert validate_dual_run(fn) == (start + end) / 2


@given(floats, floats)
def test_interval_is_empty(start, end):
    def fn():
        interval = Interval(start, end)
        return interval.is_empty

    assert validate_dual_run(fn) == (start > end)


@given(floats, floats, floats)
def test_interval_contains_float(start, end, value):
    def fn():
        interval = Interval(start, end)
        return value in interval

    assert validate_dual_run(fn) == (start <= value <= end)


@given(floats, floats, floats, floats)
def test_interval_contains_interval(start, end, other_start, other_end):
    def fn():
        interval = Interval(start, end)
        other = Interval(other_start, other_end)
        return other in interval

    assert validate_dual_run(fn) == (start <= other_start and other_end <= end)


@given(floats, floats, floats)
def test_interval_add(start, end, value):
    def fn():
        interval = Interval(start, end)
        return interval + value

    assert validate_dual_run(fn) == Interval(start + value, end + value)


@given(floats, floats, floats)
def test_interval_sub(start, end, value):
    def fn():
        interval = Interval(start, end)
        return interval - value

    assert validate_dual_run(fn) == Interval(start - value, end - value)


@given(floats, floats, floats)
def test_interval_mul(start, end, value):
    def fn():
        interval = Interval(start, end)
        return interval * value

    assert validate_dual_run(fn) == Interval(start * value, end * value)


@given(floats, floats, divisor_floats)
def test_interval_truediv(start, end, value):
    def fn():
        interval = Interval(start, end)
        return interval / value

    assert validate_dual_run(fn) == Interval(start / value, end / value)


@given(ints, ints, ints.filter(lambda x: x != 0))
def test_interval_floordiv(start, end, value):
    def fn():
        interval = Interval(start, end)
        return interval // value

    assert validate_dual_run(fn) == Interval(start // value, end // value)


@given(floats, floats, floats, floats)
def test_interval_intersection(start, end, other_start, other_end):
    def fn():
        interval = Interval(start, end)
        other = Interval(other_start, other_end)
        return interval & other

    assert validate_dual_run(fn) == Interval(max(start, other_start), min(end, other_end))


@given(floats, floats, floats, floats)
def test_interval_intersection_is_no_longer_than_original(start, end, other_start, other_end):
    def fn():
        interval = Interval(start, end)
        other = Interval(other_start, other_end)
        intersection = interval & other
        return intersection.length <= interval.length and intersection.length <= other.length

    assert validate_dual_run(fn)


@given(floats, floats, floats, floats, floats)
def test_interval_intersection_consistent_with_contains(start, end, other_start, other_end, value):
    def fn():
        interval = Interval(start, end)
        other = Interval(other_start, other_end)
        return ((value in interval) and (value in other)) == (value in (interval & other))

    assert validate_dual_run(fn)


@given(floats, floats, floats, floats, floats, floats)
def test_interval_transitive_contains(start1, end1, start2, end2, start3, end3):
    def fn():
        interval1 = Interval(start1, end1)
        interval2 = Interval(start2, end2)
        interval3 = Interval(start3, end3)
        return implies(interval1 in interval2 in interval3, interval1 in interval3)

    assert validate_dual_run(fn)


@given(floats, floats, floats, floats)
def test_interval_intersection_is_in_original(start, end, other_start, other_end):
    def fn():
        interval = Interval(start, end)
        other = Interval(other_start, other_end)
        intersection = interval & other
        return intersection in interval and intersection in other

    assert validate_dual_run(fn)


@given(floats, floats, floats, floats, floats)
def test_remap_inverse(start, end, other_start, other_end, value):
    assume(abs(end - start) > 1e-6 and abs(other_end - other_start) > 1e-6)

    def fn():
        remapped = remap(start, end, other_start, other_end, value)
        return remap(other_start, other_end, start, end, remapped)

    assert is_close(validate_dual_run(fn), value, abs_tol=1e-4)


@given(floats, floats, floats, floats, floats)
def test_remap_clamped_inverse(start, end, other_start, other_end, value):
    assume(abs(end - start) > 1e-6 and abs(other_end - other_start) > 1e-6)

    def fn():
        remapped = remap_clamped(start, end, other_start, other_end, value)
        return remap_clamped(other_start, other_end, start, end, remapped)

    assert is_close(validate_dual_run(fn), sorted([start, value, end])[1], abs_tol=1e-4)


@given(floats, floats, floats)
def test_lerp_unlerp_inverse(start, end, value):
    assume(abs(end - start) > 1e-6)

    def fn():
        interval = Interval(start, end)
        lerped = interval.lerp(value)
        return interval.unlerp(lerped)

    assert is_close(validate_dual_run(fn), value, abs_tol=1e-4)


@given(floats, floats, floats)
def test_unlerp_lerp_inverse(start, end, value):
    assume(abs(end - start) > 1e-6)

    def fn():
        interval = Interval(start, end)
        unlerped = interval.unlerp(value)
        return interval.lerp(unlerped)

    assert is_close(validate_dual_run(fn), value, abs_tol=1e-4)


@given(floats, floats, floats)
def test_lerp_clamped_unlerp_clamped_inverse(start, end, value):
    assume(abs(end - start) > 1e-6)

    def fn():
        interval = Interval(start, end)
        lerped_clamped = interval.lerp_clamped(value)
        return interval.unlerp_clamped(lerped_clamped)

    assert is_close(validate_dual_run(fn), sorted([0, value, 1])[1], abs_tol=1e-4)


@given(floats, floats, floats)
def test_unlerp_clamped_lerp_clamped_inverse(start, end, value):
    assume(abs(end - start) > 1e-6)

    def fn():
        interval = Interval(start, end)
        unlerped_clamped = interval.unlerp_clamped(value)
        return interval.lerp_clamped(unlerped_clamped)

    assert is_close(validate_dual_run(fn), sorted([start, value, end])[1], abs_tol=1e-4)
