from hypothesis import given
from hypothesis import strategies as st

from sonolus.script.array import Array
from sonolus.script.containers import VarArray
from tests.script.conftest import run_and_validate
from tests.script.test_record import Pair


@given(n=st.integers(min_value=0, max_value=100))
def test_basic_range_iteration(n):
    def fn():
        total = 0
        for i in range(n):
            total += i
        return total

    expected = sum(range(n))
    result = run_and_validate(fn)
    assert result == expected


@given(start=st.integers(-100, 100), stop=st.integers(-100, 100))
def test_range_iteration_with_start(start, stop):
    def fn():
        total = 0
        for i in range(start, stop):
            total += i
        return total

    expected = sum(range(start, stop))
    result = run_and_validate(fn)
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
    result = run_and_validate(fn)
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
    result = run_and_validate(fn)
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
    result = run_and_validate(fn)
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
    result = run_and_validate(fn)
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
    result = run_and_validate(fn)
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
    result = run_and_validate(fn)
    assert result == expected


def test_range_negative_indexing():
    def fn():
        r = range(10, 50, 5)

        return Array(
            r[-1],
            r[-2],
            r[-3],
            r[-4],
            r[-5],
            r[-6],
            r[-7],
            r[-8],
        )

    assert list(run_and_validate(fn)) == [45, 40, 35, 30, 25, 20, 15, 10]


@given(
    start=st.integers(-50, 50),
    stop=st.integers(-50, 50),
    step=st.integers(-10, 10).filter(lambda x: x != 0),
)
def test_range_negative_positive_indexing_equivalence(start, stop, step):
    length = len(range(start, stop, step))

    def fn():
        r = range(start, stop, step)

        if length == 0:
            return Array(True)

        results = VarArray[bool, length].new()
        for i in range(1, length + 1):
            if i <= length:
                results.append(r[-i] == r[length - i])

        return results

    assert all(run_and_validate(fn))


def test_range_indexing_with_arrays():
    def fn():
        r = range(5, 25, 3)
        results = VarArray[int, 7].new()

        for i in range(len(r)):
            results.append(r[i])

        return results

    assert list(run_and_validate(fn)) == [5, 8, 11, 14, 17, 20, 23]


def test_range_truthiness_empty():
    def fn():
        x = range(0)
        return 1 if x else 0

    assert run_and_validate(fn) == 0


def test_range_truthiness_non_empty():
    def fn():
        x = range(5)
        return 1 if x else 0

    assert run_and_validate(fn) == 1
