from hypothesis import given
from hypothesis import strategies as st

from tests.script.conftest import validate_dual_run
from tests.script.test_record import Pair


@given(n=st.integers(min_value=0, max_value=100))
def test_basic_range_iteration(n):
    def fn():
        total = 0
        for i in range(n):
            total += i
        return total

    expected = sum(range(n))
    result = validate_dual_run(fn)
    assert result == expected


@given(start=st.integers(-100, 100), stop=st.integers(-100, 100))
def test_range_iteration_with_start(start, stop):
    def fn():
        total = 0
        for i in range(start, stop):
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
        for i in range(start, stop, step):
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
        for i in range(start, stop, step):
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
        return value in range(start, stop, step)

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
        return len(range(start, stop, step))

    expected = len(range(start, stop, step))
    result = validate_dual_run(fn)
    assert result == expected


@given(
    start1=st.integers(-100, 100),
    stop1=st.integers(-100, 100),
    step1=st.integers(-10, 10).filter(lambda x: x != 0),
    start2=st.integers(-100, 100),
    stop2=st.integers(-100, 100),
    step2=st.integers(-10, 10).filter(lambda x: x != 0),
)
def test_range_equality(start1, stop1, step1, start2, stop2, step2):
    def fn():
        a = range(start1, stop1, step1)
        b = range(start2, stop2, step2)
        return Pair(a == b, b == a)

    range_a = range(start1, stop1, step1)
    range_b = range(start2, stop2, step2)
    expected = Pair(range_a == range_b, range_b == range_a)
    result = validate_dual_run(fn)
    assert result == expected


@given(
    start=st.integers(-100, 100),
    stop=st.integers(-100, 100),
    step=st.integers(-10, 10).filter(lambda x: x != 0),
)
def test_identical_range_equality(start, stop, step):
    def fn():
        a = range(start, stop, step)
        b = range(start, stop, step)
        return Pair(a == b, b == a)

    expected = Pair(True, True)
    result = validate_dual_run(fn)
    assert result == expected
