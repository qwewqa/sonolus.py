import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from sonolus.script.array import Array
from sonolus.script.internal.context import RuntimeChecks
from sonolus.script.internal.error import CompilationError
from sonolus.script.interval import (
    Interval,
    clamp,
    interp,
    interp_clamped,
    lerp,
    lerp_clamped,
    remap,
    remap_clamped,
)
from sonolus.script.record import Record
from sonolus.script.vec import Vec2
from tests.script.conftest import implies, is_close, run_and_validate, run_compiled

ints = st.integers(min_value=-99999, max_value=99999)
floats = st.floats(min_value=-99999, max_value=99999, allow_infinity=False, allow_nan=False)
positive_deltas = st.floats(min_value=1e-4, max_value=999, allow_infinity=False, allow_nan=False)
floats_0_1 = st.floats(min_value=0, max_value=1, allow_infinity=False, allow_nan=False)
divisor_floats = floats.filter(lambda x: abs(x) > 1e-6)
lerp_floats = st.floats(min_value=-999, max_value=999, allow_infinity=False, allow_nan=False)


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
def test_interval_inplace_intersection(start, end, other_start, other_end):
    def fn():
        interval = Interval(start, end)
        other = Interval(other_start, other_end)
        interval &= other
        return interval

    assert run_and_validate(fn) == Interval(max(start, other_start), min(end, other_end))


# The branch in __add__ is the point of this Record: a straight-line body compiles either way, so only a branching
# one catches the auto-generated __iadd__ running its base operator as host Python.
class ClampedSum(Record):
    value: float

    def __add__(self, other):
        total = self.value + other.value
        if total > 10.0:  # noqa: PLR1730
            total = 10.0
        return ClampedSum(total)


@given(floats, floats)
def test_record_inplace_op_traces_branching_base_op(first, second):
    def fn():
        acc = ClampedSum(first)
        acc += ClampedSum(second)
        return acc.value

    assert run_and_validate(fn) == min(first + second, 10.0)


def test_inplace_ops_are_generated():
    # With no in-place form at all, visit_AugAssign falls back to the base operator and rebinds, so the two
    # augmented-assignment tests here would pass whether or not the generated form traces its base operator.
    assert "__iand__" in Interval.__dict__
    assert "__iadd__" in ClampedSum.__dict__


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
    assert (
        min_fp <= result <= max_fp or is_close(result, min_fp, abs_tol=1e-4) or is_close(result, max_fp, abs_tol=1e-4)
    )


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
    assert (
        min_fp <= result <= max_fp or is_close(result, min_fp, abs_tol=1e-4) or is_close(result, max_fp, abs_tol=1e-4)
    )


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


def test_interp_len2_out_of_order_fails():
    def fn():
        v = 0.0
        for _ in range(3):
            v += 1.0
        xp = Array(v, v - 1.0)
        fp = Array(0.0, 1.0)
        return interp(xp, fp, 0.5)

    log_calls = []
    result = run_compiled(
        fn,
        runtime_checks=RuntimeChecks.NOTIFY_AND_TERMINATE,
        log_callback=log_calls.append,
    )
    assert result == 0
    assert len(log_calls) == 1


def test_interp_bad_final_segment_fails():
    def fn():
        v = 0.0
        for _ in range(3):
            v += 1.0
        xp = Array(1.0, 2.0, 2.0 - v)
        fp = Array(0.0, 1.0, 2.0)
        # x is past xp[1], so interp reaches the assert on the fall-through path, the only check of the final pair.
        return interp(xp, fp, 2.5)

    log_calls = []
    result = run_compiled(
        fn,
        runtime_checks=RuntimeChecks.NOTIFY_AND_TERMINATE,
        log_callback=log_calls.append,
    )
    assert result == 0
    assert len(log_calls) == 1


def test_interp_final_segment_good_input_does_not_false_fire():
    def fn():
        xp = Array(1.0, 2.0, 4.0)
        fp = Array(10.0, 20.0, 40.0)
        # x is beyond xp[-1], forcing the fall-through path through the assert.
        return interp(xp, fp, 5.0)

    log_calls = []
    result = run_compiled(
        fn,
        runtime_checks=RuntimeChecks.NOTIFY_AND_TERMINATE,
        log_callback=log_calls.append,
    )
    assert is_close(result, 50.0, abs_tol=1e-4)
    assert len(log_calls) == 0


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


class Point(Record):
    x: float
    y: float


class Weights(Record):
    """A two-field record carrying only the arithmetic the generic lerp arm needs."""

    a: float
    b: float

    def __add__(self, other):
        return Weights(self.a + other.a, self.b + other.b)

    def __sub__(self, other):
        return Weights(self.a - other.a, self.b - other.b)

    def __mul__(self, other):
        return Weights(self.a * other, self.b * other)


def test_interval_zero():
    def fn():
        return Interval.zero()

    assert run_and_validate(fn) == Interval(0.0, 0.0)


def test_interval_shrink_concrete():
    def fn():
        return Interval(2.0, 10.0).shrink(3.0)

    assert run_and_validate(fn) == Interval(5.0, 7.0)


def test_interval_expand_concrete():
    def fn():
        return Interval(2.0, 10.0).expand(3.0)

    assert run_and_validate(fn) == Interval(-1.0, 13.0)


@given(floats, floats, positive_deltas)
def test_interval_expand_grows_and_shrink_narrows(start, end, value):
    # One-sided and signed on purpose: shrink and expand are exact mirrors, so an expand/shrink round
    # trip holds just as well with the two swapped.
    def fn():
        interval = Interval(start, end)
        return Array(interval.expand(value).length, interval.shrink(value).length, interval.length)

    expanded_length, shrunk_length, length = run_and_validate(fn)
    assert is_close(expanded_length, length + 2 * value, abs_tol=1e-3)
    assert is_close(shrunk_length, length - 2 * value, abs_tol=1e-3)


def test_interval_shrink_past_the_midpoint_is_empty():
    def fn():
        interval = Interval(0.0, 4.0)
        shrunk = interval.shrink(3.0)
        return Array(shrunk.start, shrunk.end, 1 if shrunk.is_empty else 0)

    shrunk_start, shrunk_end, is_empty = run_and_validate(fn)
    assert (shrunk_start, shrunk_end) == (3.0, 1.0)
    assert is_empty == 1


def test_interval_clamp_concrete():
    def fn():
        interval = Interval(2.0, 10.0)
        return Array(interval.clamp(-5.0), interval.clamp(6.0), interval.clamp(50.0))

    below, inside, above = run_and_validate(fn)
    assert (below, inside, above) == (2.0, 6.0, 10.0)


@given(floats, floats, floats)
def test_interval_clamp_matches_free_clamp(start, end, value):
    def fn():
        interval = Interval(start, end)
        return Array(interval.clamp(value), clamp(value, start, end))

    method_result, free_result = run_and_validate(fn)
    assert method_result == free_result


def test_interval_contains_rejects_other_types():
    def fn():
        return Point(0.5, 0.5) in Interval(0.0, 1.0)

    # run_compiled, not run_and_validate: the guard is a compile-time static_error, so the host leg
    # raises a bare RuntimeError instead of the CompilationError the compiled leg reports.
    with pytest.raises(CompilationError, match="Invalid type for interval check"):
        run_compiled(fn)


def test_lerp_vec2_endpoints_and_midpoint():
    # Hand-computed rather than compared against a formula: run_and_validate is differential, so an
    # operand swap inside the generic lerp arm would be wrong identically on the host and compiled legs.
    def fn():
        a = Vec2(1.0, 2.0)
        b = Vec2(5.0, 10.0)
        return Array(*lerp(a, b, 0.0).tuple, *lerp(a, b, 0.5).tuple, *lerp(a, b, 1.0).tuple)

    at_0_x, at_0_y, at_half_x, at_half_y, at_1_x, at_1_y = run_and_validate(fn)
    assert (at_0_x, at_0_y) == (1.0, 2.0)
    assert (at_half_x, at_half_y) == (3.0, 6.0)
    assert (at_1_x, at_1_y) == (5.0, 10.0)


def test_lerp_vec2_extrapolates_outside_unit_interval():
    def fn():
        a = Vec2(1.0, 2.0)
        b = Vec2(5.0, 10.0)
        return Array(*lerp(a, b, 2.0).tuple, *lerp(a, b, -1.0).tuple)

    above_x, above_y, below_x, below_y = run_and_validate(fn)
    assert (above_x, above_y) == (9.0, 18.0)
    assert (below_x, below_y) == (-3.0, -6.0)


def test_lerp_clamped_vec2_clamps_the_factor_not_the_endpoints():
    def fn():
        a = Vec2(1.0, 2.0)
        b = Vec2(5.0, 10.0)
        return Array(*lerp_clamped(a, b, 2.0).tuple, *lerp_clamped(a, b, -1.0).tuple)

    above_x, above_y, below_x, below_y = run_and_validate(fn)
    assert (above_x, above_y) == (5.0, 10.0)
    assert (below_x, below_y) == (1.0, 2.0)


def test_lerp_generic_record_endpoints():
    def fn():
        a = Weights(0.0, 100.0)
        b = Weights(10.0, 0.0)
        mid = lerp(a, b, 0.25)
        return Array(mid.a, mid.b)

    a, b = run_and_validate(fn)
    assert (a, b) == (2.5, 75.0)


@given(lerp_floats, lerp_floats, lerp_floats, lerp_floats)
def test_lerp_vec2_agrees_componentwise_with_num_lerp(ax, ay, bx, by):
    # The Num arm is the independently tested one, so agreeing with it component by component pins the
    # generic arm's operand order without restating its formula.
    def fn():
        a = Vec2(ax, ay)
        b = Vec2(bx, by)
        v = lerp(a, b, 0.375)
        return Array(v.x, v.y, lerp(ax, bx, 0.375), lerp(ay, by, 0.375))

    vx, vy, expected_x, expected_y = run_and_validate(fn)
    assert is_close(vx, expected_x, abs_tol=1e-4)
    assert is_close(vy, expected_y, abs_tol=1e-4)


@given(lerp_floats, lerp_floats, lerp_floats, lerp_floats, lerp_floats)
def test_lerp_clamped_vec2_agrees_componentwise_with_num_lerp_clamped(ax, ay, bx, by, x):
    def fn():
        a = Vec2(ax, ay)
        b = Vec2(bx, by)
        v = lerp_clamped(a, b, x)
        return Array(v.x, v.y, lerp_clamped(ax, bx, x), lerp_clamped(ay, by, x))

    vx, vy, expected_x, expected_y = run_and_validate(fn)
    assert is_close(vx, expected_x, abs_tol=1e-4)
    assert is_close(vy, expected_y, abs_tol=1e-4)
