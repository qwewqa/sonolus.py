from hypothesis import given
from hypothesis import strategies as st

from sonolus.script.range import Range
from tests.script.conftest import validate_dual_run


@given(n=st.integers(min_value=0, max_value=100))
def test_basic_range_iteration(n):
    def fn():
        total = 0
        for i in Range(n):
            total += i
        return total

    expected = sum(range(n))
    result = validate_dual_run(fn)
    assert result == expected


@given(start=st.integers(-100, 100), stop=st.integers(-100, 100))
def test_range_iteration_with_start(start, stop):
    def fn():
        total = 0
        for i in Range(start, stop):
            total += i
        return total

    expected = sum(range(start, stop))
    result = validate_dual_run(fn)
    assert result == expected


@given(
    start=st.integers(-100, 100),
    stop=st.integers(-100, 100),
    step=st.integers(-10, 10).filter(lambda x: x != 0),
)
def test_range_iteration_with_step(start, stop, step):
    def fn():
        total = 0
        for i in Range(start, stop, step):
            total += i
        return total

    expected = sum(range(start, stop, step))
    result = validate_dual_run(fn)
    assert result == expected


@given(
    start=st.integers(-100, 100),
    stop=st.integers(-100, 100),
    step=st.integers(-10, -1),
)
def test_range_iteration_with_negative_step(start, stop, step):
    def fn():
        total = 0
        for i in Range(start, stop, step):
            total += i
        return total

    expected = sum(range(start, stop, step))
    result = validate_dual_run(fn)
    assert result == expected


@given(
    start=st.integers(-100, 100),
    stop=st.integers(-100, 100),
    step=st.integers(-10, 10).filter(lambda x: x != 0),
    value=st.integers(-200, 200),
)
def test_range_contains(start, stop, step, value):
    def fn():
        return value in Range(start, stop, step)

    expected = value in range(start, stop, step)
    result = validate_dual_run(fn)
    assert result == expected


@given(
    start=st.integers(-100, 100),
    stop=st.integers(-100, 100),
    step=st.integers(-10, 10).filter(lambda x: x != 0),
)
def test_range_size(start, stop, step):
    def fn():
        return Range(start, stop, step).size()

    expected = len(range(start, stop, step))
    result = validate_dual_run(fn)
    assert result == expected
