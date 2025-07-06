from hypothesis import assume, given
from hypothesis import strategies as st

from sonolus.script.array import Array
from sonolus.script.interval import Interval, interp, interp_clamped, lerp, remap, remap_clamped
from tests.script.conftest import implies, is_close, run_and_validate

ints = st.integers(min_value=-99999, max_value=99999)
floats = st.floats(min_value=-99999, max_value=99999, allow_infinity=False, allow_nan=False)
positive_deltas = st.floats(min_value=1e-4, max_value=999, allow_infinity=False, allow_nan=False)
floats_0_1 = st.floats(min_value=0, max_value=1, allow_infinity=False, allow_nan=False)
divisor_floats = floats.filter(lambda x: abs(x) > 1e-6)


@st.composite
def xp_fp_pairs(draw):
    size = draw(st.integers(min_value=2, max_value=10))
    xp_start = draw(floats)
    deltas = draw(st.lists(positive_deltas, min_size=size - 1, max_size=size - 1))
    fp_values = draw(st.lists(floats, min_size=size, max_size=size))

    xp_tuple = (xp_start, *tuple(xp_start + sum(deltas[: i + 1]) for i in range(len(deltas))))
    fp_tuple = tuple(fp_values)

    return xp_tuple, fp_tuple


@st.composite
def xp_fp_pairs_monotonic(draw):
    size = draw(st.integers(min_value=2, max_value=10))
    xp_start = draw(floats)
    fp_start = draw(floats)
    xp_deltas = draw(st.lists(positive_deltas, min_size=size - 1, max_size=size - 1))
    fp_deltas = draw(st.lists(positive_deltas, min_size=size - 1, max_size=size - 1))

    xp_tuple = (xp_start, *tuple(xp_start + sum(xp_deltas[: i + 1]) for i in range(len(xp_deltas))))
    fp_tuple = (fp_start, *tuple(fp_start + sum(fp_deltas[: i + 1]) for i in range(len(fp_deltas))))

    return xp_tuple, fp_tuple


@given(floats, floats)
def test_interval_length(start, end):
    def fn():
        interval = Interval(start, end)
        return interval.length

    assert run_and_validate(fn) == end - start


@given(floats, floats)
def test_interval_mid(start, end):
    def fn():
        interval = Interval(start, end)
        return interval.mid

    assert run_and_validate(fn) == (start + end) / 2


@given(floats, floats)
def test_interval_is_empty(start, end):
    def fn():
        interval = Interval(start, end)
        return interval.is_empty

    assert run_and_validate(fn) == (start > end)


@given(floats, floats, floats)
def test_interval_contains_float(start, end, value):
    def fn():
        interval = Interval(start, end)
        return value in interval

    assert run_and_validate(fn) == (start <= value <= end)


@given(floats, floats, floats, floats)
def test_interval_contains_interval(start, end, other_start, other_end):
    def fn():
        interval = Interval(start, end)
        other = Interval(other_start, other_end)
        return other in interval

    assert run_and_validate(fn) == (start <= other_start and other_end <= end)


@given(floats, floats, floats)
def test_interval_add(start, end, value):
    def fn():
        interval = Interval(start, end)
        return interval + value

    assert run_and_validate(fn) == Interval(start + value, end + value)


@given(floats, floats, floats)
def test_interval_sub(start, end, value):
    def fn():
        interval = Interval(start, end)
        return interval - value

    assert run_and_validate(fn) == Interval(start - value, end - value)


@given(floats, floats, floats)
def test_interval_mul(start, end, value):
    def fn():
        interval = Interval(start, end)
        return interval * value

    assert run_and_validate(fn) == Interval(start * value, end * value)


@given(floats, floats, divisor_floats)
def test_interval_truediv(start, end, value):
    def fn():
        interval = Interval(start, end)
        return interval / value

    assert run_and_validate(fn) == Interval(start / value, end / value)


@given(ints, ints, ints.filter(lambda x: x != 0))
def test_interval_floordiv(start, end, value):
    def fn():
        interval = Interval(start, end)
        return interval // value

    assert run_and_validate(fn) == Interval(start // value, end // value)


@given(floats, floats, floats, floats)
def test_interval_intersection(start, end, other_start, other_end):
    def fn():
        interval = Interval(start, end)
        other = Interval(other_start, other_end)
        return interval & other

    assert run_and_validate(fn) == Interval(max(start, other_start), min(end, other_end))


@given(floats, floats, floats, floats)
def test_interval_intersection_is_no_longer_than_original(start, end, other_start, other_end):
    def fn():
        interval = Interval(start, end)
        other = Interval(other_start, other_end)
        intersection = interval & other
        return intersection.length <= interval.length and intersection.length <= other.length

    assert run_and_validate(fn)


@given(floats, floats, floats, floats, floats)
def test_interval_intersection_consistent_with_contains(start, end, other_start, other_end, value):
    def fn():
        interval = Interval(start, end)
        other = Interval(other_start, other_end)
        return ((value in interval) and (value in other)) == (value in (interval & other))

    assert run_and_validate(fn)


@given(floats, floats, floats, floats, floats, floats)
def test_interval_transitive_contains(start1, end1, start2, end2, start3, end3):
    def fn():
        interval1 = Interval(start1, end1)
        interval2 = Interval(start2, end2)
        interval3 = Interval(start3, end3)
        return implies(interval1 in interval2 in interval3, interval1 in interval3)

    assert run_and_validate(fn)


@given(floats, floats, floats, floats)
def test_interval_intersection_is_in_original(start, end, other_start, other_end):
    def fn():
        interval = Interval(start, end)
        other = Interval(other_start, other_end)
        intersection = interval & other
        return intersection in interval and intersection in other

    assert run_and_validate(fn)


@given(floats, floats, floats, floats, floats)
def test_remap_inverse(start, end, other_start, other_end, value):
    assume(abs(end - start) > 1e-4 and abs(other_end - other_start) > 1e-4)

    def fn():
        remapped = remap(start, end, other_start, other_end, value)
        return remap(other_start, other_end, start, end, remapped)

    assert is_close(run_and_validate(fn), value, abs_tol=1e-3)


@given(floats, floats, floats, floats, floats)
def test_remap_clamped_inverse(start, end, other_start, other_end, value):
    assume(abs(end - start) > 1e-6 and abs(other_end - other_start) > 1e-6)

    def fn():
        remapped = remap_clamped(start, end, other_start, other_end, value)
        return remap_clamped(other_start, other_end, start, end, remapped)

    assert is_close(run_and_validate(fn), sorted([start, value, end])[1], abs_tol=1e-4)


@given(floats, floats, floats)
def test_lerp_unlerp_inverse(start, end, value):
    assume(abs(end - start) > 1e-6)

    def fn():
        interval = Interval(start, end)
        lerped = interval.lerp(value)
        return interval.unlerp(lerped)

    assert is_close(run_and_validate(fn), value, abs_tol=1e-4)


@given(floats, floats, floats)
def test_unlerp_lerp_inverse(start, end, value):
    assume(abs(end - start) > 1e-6)

    def fn():
        interval = Interval(start, end)
        unlerped = interval.unlerp(value)
        return interval.lerp(unlerped)

    assert is_close(run_and_validate(fn), value, abs_tol=1e-4)


@given(floats, floats, floats)
def test_lerp_clamped_unlerp_clamped_inverse(start, end, value):
    assume(abs(end - start) > 1e-6)

    def fn():
        interval = Interval(start, end)
        lerped_clamped = interval.lerp_clamped(value)
        return interval.unlerp_clamped(lerped_clamped)

    assert is_close(run_and_validate(fn), sorted([0, value, 1])[1], abs_tol=1e-4)


@given(floats, floats, floats)
def test_unlerp_clamped_lerp_clamped_inverse(start, end, value):
    assume(abs(end - start) > 1e-6)

    def fn():
        interval = Interval(start, end)
        unlerped_clamped = interval.unlerp_clamped(value)
        return interval.lerp_clamped(unlerped_clamped)

    assert is_close(run_and_validate(fn), sorted([start, value, end])[1], abs_tol=1e-4)


@given(xp_fp_pairs(), positive_deltas)
def test_interp_bounds_arrays(xp_fp_pair, offset):
    xp_tuple, fp_tuple = xp_fp_pair

    def fn():
        xp = Array(*xp_tuple)
        fp = Array(*fp_tuple)

        x_below = xp_tuple[0] - offset
        result_below = interp(xp, fp, x_below)
        expected_below = remap(xp_tuple[0], xp_tuple[1], fp_tuple[0], fp_tuple[1], x_below)

        x_above = xp_tuple[-1] + offset
        result_above = interp(xp, fp, x_above)
        expected_above = remap(xp_tuple[-2], xp_tuple[-1], fp_tuple[-2], fp_tuple[-1], x_above)

        return Array(result_below, expected_below, result_above, expected_above)

    result_below, expected_below, result_above, expected_above = run_and_validate(fn)
    assert is_close(result_below, expected_below, abs_tol=1e-4)
    assert is_close(result_above, expected_above, abs_tol=1e-4)


@given(xp_fp_pairs(), positive_deltas)
def test_interp_bounds_tuples(xp_fp_pair, offset):
    xp_tuple, fp_tuple = xp_fp_pair

    def fn():
        xp = xp_tuple
        fp = fp_tuple

        x_below = xp_tuple[0] - offset
        result_below = interp(xp, fp, x_below)
        expected_below = remap(xp_tuple[0], xp_tuple[1], fp_tuple[0], fp_tuple[1], x_below)

        x_above = xp_tuple[-1] + offset
        result_above = interp(xp, fp, x_above)
        expected_above = remap(xp_tuple[-2], xp_tuple[-1], fp_tuple[-2], fp_tuple[-1], x_above)

        return Array(result_below, expected_below, result_above, expected_above)

    result_below, expected_below, result_above, expected_above = run_and_validate(fn)
    assert is_close(result_below, expected_below, abs_tol=1e-4)
    assert is_close(result_above, expected_above, abs_tol=1e-4)


@given(xp_fp_pairs(), positive_deltas.filter(lambda x: abs(x) > 1e-6))
def test_interp_clamped_bounds_arrays(xp_fp_pair, offset):
    xp_tuple, fp_tuple = xp_fp_pair

    def fn():
        xp = Array(*xp_tuple)
        fp = Array(*fp_tuple)

        x_below = xp_tuple[0] - offset
        result_below = interp_clamped(xp, fp, x_below)

        x_above = xp_tuple[-1] + offset
        result_above = interp_clamped(xp, fp, x_above)

        return Array(result_below, fp_tuple[0], result_above, fp_tuple[-1])

    result_below, expected_below, result_above, expected_above = run_and_validate(fn)
    assert is_close(result_below, expected_below, abs_tol=1e-4)
    assert is_close(result_above, expected_above, abs_tol=1e-4)


@given(xp_fp_pairs(), positive_deltas)
def test_interp_clamped_bounds_tuples(xp_fp_pair, offset):
    xp_tuple, fp_tuple = xp_fp_pair

    def fn():
        xp = xp_tuple
        fp = fp_tuple

        x_below = xp_tuple[0] - offset
        result_below = interp_clamped(xp, fp, x_below)

        x_above = xp_tuple[-1] + offset
        result_above = interp_clamped(xp, fp, x_above)

        return Array(result_below, fp_tuple[0], result_above, fp_tuple[-1])

    result_below, expected_below, result_above, expected_above = run_and_validate(fn)
    assert is_close(result_below, expected_below, abs_tol=1e-4)
    assert is_close(result_above, expected_above, abs_tol=1e-4)


@given(xp_fp_pairs(), floats_0_1)
def test_interp_within_bounds_arrays(xp_fp_pair, rel_x):
    xp_tuple, fp_tuple = xp_fp_pair

    x = lerp(xp_tuple[0], xp_tuple[-1], rel_x)

    def fn():
        xp = Array(*xp_tuple)
        fp = Array(*fp_tuple)
        result = interp(xp, fp, x)
        return Array(result, min(fp_tuple), max(fp_tuple))

    result, min_fp, max_fp = run_and_validate(fn)
    assert (
        min_fp <= result <= max_fp or is_close(result, min_fp, abs_tol=1e-4) or is_close(result, max_fp, abs_tol=1e-4)
    )


@given(xp_fp_pairs(), floats_0_1)
def test_interp_within_bounds_tuples(xp_fp_pair, rel_x):
    xp_tuple, fp_tuple = xp_fp_pair

    x = lerp(xp_tuple[0], xp_tuple[-1], rel_x)

    def fn():
        xp = xp_tuple
        fp = fp_tuple
        result = interp(xp, fp, x)
        return Array(result, min(fp_tuple), max(fp_tuple))

    result, min_fp, max_fp = run_and_validate(fn)
    assert min_fp <= result <= max_fp


@given(xp_fp_pairs(), floats_0_1)
def test_interp_clamped_within_bounds_arrays(xp_fp_pair, rel_x):
    xp_tuple, fp_tuple = xp_fp_pair

    x = lerp(xp_tuple[0], xp_tuple[-1], rel_x)

    def fn():
        xp = Array(*xp_tuple)
        fp = Array(*fp_tuple)
        result = interp_clamped(xp, fp, x)
        return Array(result, min(fp_tuple), max(fp_tuple))

    result, min_fp, max_fp = run_and_validate(fn)
    assert min_fp <= result <= max_fp


@given(xp_fp_pairs(), floats)
def test_interp_clamped_within_bounds_tuples(xp_fp_pair, rel_x):
    xp_tuple, fp_tuple = xp_fp_pair

    # Ensure x is within bounds
    x = xp_tuple[0] + (xp_tuple[-1] - xp_tuple[0]) * max(0, min(1, (rel_x + 1) / 2))

    def fn():
        xp = xp_tuple
        fp = fp_tuple
        result = interp_clamped(xp, fp, x)
        return Array(result, min(fp_tuple), max(fp_tuple))

    result, min_fp, max_fp = run_and_validate(fn)
    assert (
        min_fp <= result <= max_fp or is_close(result, min_fp, abs_tol=1e-4) or is_close(result, max_fp, abs_tol=1e-4)
    )


def test_interp_simple_cases():
    def fn():
        # Test middle point
        xp = Array(0.0, 10.0)
        fp = Array(5.0, 15.0)
        middle = interp(xp, fp, 5.0)

        # Test exact point match
        exact = interp(xp, fp, 0.0)

        # Test outside bounds
        outside = interp(xp, fp, 20.0)

        return Array(middle, exact, outside)

    middle, exact, outside = run_and_validate(fn)
    assert is_close(middle, 10.0, abs_tol=1e-4)  # Midpoint between 5 and 15
    assert is_close(exact, 5.0, abs_tol=1e-4)  # Exact match at x=0
    assert is_close(outside, 25.0, abs_tol=1e-4)  # Extrapolation: 15 + (15-5) * (20-10)/(10-0)


def test_interp_clamped_simple_cases():
    def fn():
        # Test middle point
        xp = Array(0.0, 10.0)
        fp = Array(5.0, 15.0)
        middle = interp_clamped(xp, fp, 5.0)

        # Test exact point match
        exact = interp_clamped(xp, fp, 0.0)

        # Test outside bounds (should clamp)
        outside_low = interp_clamped(xp, fp, -5.0)
        outside_high = interp_clamped(xp, fp, 20.0)

        return Array(middle, exact, outside_low, outside_high)

    middle, exact, outside_low, outside_high = run_and_validate(fn)
    assert is_close(middle, 10.0, abs_tol=1e-4)  # Midpoint between 5 and 15
    assert is_close(exact, 5.0, abs_tol=1e-4)  # Exact match at x=0
    assert is_close(outside_low, 5.0, abs_tol=1e-4)  # Clamped to first value
    assert is_close(outside_high, 15.0, abs_tol=1e-4)  # Clamped to last value


def test_interp_tuple_simple_cases():
    def fn():
        # Test middle point
        xp = (0.0, 10.0)
        fp = (5.0, 15.0)
        middle = interp(xp, fp, 5.0)

        # Test exact point match
        exact = interp(xp, fp, 0.0)

        # Test outside bounds
        outside = interp(xp, fp, 20.0)

        return Array(middle, exact, outside)

    middle, exact, outside = run_and_validate(fn)
    assert is_close(middle, 10.0, abs_tol=1e-4)  # Midpoint between 5 and 15
    assert is_close(exact, 5.0, abs_tol=1e-4)  # Exact match at x=0
    assert is_close(outside, 25.0, abs_tol=1e-4)  # Extrapolation: 15 + (15-5) * (20-10)/(10-0)


def test_interp_clamped_tuple_simple_cases():
    def fn():
        # Test middle point
        xp = (0.0, 10.0)
        fp = (5.0, 15.0)
        middle = interp_clamped(xp, fp, 5.0)

        # Test exact point match
        exact = interp_clamped(xp, fp, 0.0)

        # Test outside bounds (should clamp)
        outside_low = interp_clamped(xp, fp, -5.0)
        outside_high = interp_clamped(xp, fp, 20.0)

        return Array(middle, exact, outside_low, outside_high)

    middle, exact, outside_low, outside_high = run_and_validate(fn)
    assert is_close(middle, 10.0, abs_tol=1e-4)  # Midpoint between 5 and 15
    assert is_close(exact, 5.0, abs_tol=1e-4)  # Exact match at x=0
    assert is_close(outside_low, 5.0, abs_tol=1e-4)  # Clamped to first value
    assert is_close(outside_high, 15.0, abs_tol=1e-4)  # Clamped to last value


@given(xp_fp_pairs_monotonic(), floats)
def test_interp_inverse_arrays(xp_fp_pair, x):
    xp_tuple, fp_tuple = xp_fp_pair

    def fn():
        xp = Array(*xp_tuple)
        fp = Array(*fp_tuple)

        y = interp(xp, fp, x)
        x_recovered = interp(fp, xp, y)

        return Array(x, x_recovered)

    original_x, recovered_x = run_and_validate(fn)
    assert is_close(original_x, recovered_x, abs_tol=1e-3)


@given(xp_fp_pairs_monotonic(), floats)
def test_interp_inverse_tuples(xp_fp_pair, x):
    xp_tuple, fp_tuple = xp_fp_pair

    def fn():
        xp = xp_tuple
        fp = fp_tuple

        y = interp(xp, fp, x)
        x_recovered = interp(fp, xp, y)

        return Array(x, x_recovered)

    original_x, recovered_x = run_and_validate(fn)
    assert is_close(original_x, recovered_x, abs_tol=1e-3)


@given(xp_fp_pairs_monotonic(), floats_0_1)
def test_interp_clamped_inverse_arrays(xp_fp_pair, rel_x):
    xp_tuple, fp_tuple = xp_fp_pair

    x = lerp(xp_tuple[0], xp_tuple[-1], rel_x)

    def fn():
        xp = Array(*xp_tuple)
        fp = Array(*fp_tuple)

        y = interp_clamped(xp, fp, x)
        x_recovered = interp_clamped(fp, xp, y)

        return Array(x, x_recovered)

    original_x, recovered_x = run_and_validate(fn)
    assert is_close(original_x, recovered_x, abs_tol=1e-3)


@given(xp_fp_pairs_monotonic(), floats_0_1)
def test_interp_clamped_inverse_tuples(xp_fp_pair, rel_x):
    xp_tuple, fp_tuple = xp_fp_pair

    x = lerp(xp_tuple[0], xp_tuple[-1], rel_x)

    def fn():
        xp = xp_tuple
        fp = fp_tuple

        y = interp_clamped(xp, fp, x)
        x_recovered = interp_clamped(fp, xp, y)

        return Array(x, x_recovered)

    original_x, recovered_x = run_and_validate(fn)
    assert is_close(original_x, recovered_x, abs_tol=1e-3)
