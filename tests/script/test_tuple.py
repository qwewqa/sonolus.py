from hypothesis import given
from hypothesis import strategies as st

from sonolus.script.array import Array
from sonolus.script.containers import VarArray
from sonolus.script.internal.range import range_or_tuple
from tests.script.conftest import run_and_validate

ints = st.integers(min_value=-10, max_value=10)
floats = st.floats(min_value=-99999, max_value=99999, allow_nan=False, allow_infinity=False)


def test_tuple_destructure():
    def fn():
        t = (1, 2), (3, 4), 5
        (a, b), (c, d), e = t
        return Array(a, b, c, d, e)

    assert run_and_validate(fn) == Array(1, 2, 3, 4, 5)


def test_tuple_addition():
    def fn():
        t1 = 1, 2
        t2 = 3, 4
        (a, b), (c, d) = t1, t2
        return Array(a, b, c, d)

    assert run_and_validate(fn) == Array(1, 2, 3, 4)


@given(
    t1_list=st.lists(ints, min_size=0, max_size=10),
    t2_list=st.lists(ints, min_size=0, max_size=10),
)
def test_tuple_comparison(t1_list, t2_list):
    t1 = tuple(t1_list)
    t2 = tuple(t2_list)

    def fn():
        return Array(t1 == t2, t1 != t2, t1 < t2, t1 <= t2, t1 > t2, t1 >= t2)

    assert run_and_validate(fn) == Array(t1 == t2, t1 != t2, t1 < t2, t1 <= t2, t1 > t2, t1 >= t2)


@given(
    t_list=st.lists(ints, min_size=0, max_size=10),
)
def test_tuple_iteration(t_list):
    t = tuple(t_list)

    def fn():
        results = VarArray[int, len(t)].new()
        for v in t:
            results.append(v)
        return results

    assert list(run_and_validate(fn)) == list(t)


def test_heterogeneous_tuple_iteration():
    def fn():
        results = VarArray[int, 5].new()
        for v in ((1, 2), (3, 4), 5):
            if isinstance(v, tuple):
                for i in v:
                    results.append(i)
            else:
                results.append(v)
        return results

    assert list(run_and_validate(fn)) == [1, 2, 3, 4, 5]


def test_tuple_negative_indexing():
    def fn():
        t = (10, 20, 30, 40, 50)

        return Array(t[-1], t[-2], t[-3], t[-4], t[-5])

    assert tuple(run_and_validate(fn)) == (50, 40, 30, 20, 10)


def test_tuple_mixed_indexing():
    def fn():
        t = (100, 200, 300, 400, 500)

        return Array(t[0], t[-5], t[2], t[-3], t[4], t[-1])

    assert tuple(run_and_validate(fn)) == (100, 100, 300, 300, 500, 500)


@given(
    t_list=st.lists(ints, min_size=1, max_size=10),
)
def test_tuple_negative_positive_indexing_equivalence(t_list):
    t = tuple(t_list)
    n = len(t_list)

    def fn():
        results = VarArray[bool, n].new()
        for i in range_or_tuple(n):
            results.append(t[i] == t[i - n])

        return results

    assert all(run_and_validate(fn))


def test_nested_tuple_indexing():
    def fn():
        t = ((1, 2), (3, 4), (5, 6))

        return Array(t[0][0], t[-1][-1], t[1][-1], t[-2][0])

    assert tuple(run_and_validate(fn)) == (1, 6, 4, 3)


@given(
    values_list=st.lists(floats, min_size=1, max_size=10),
)
def test_max_tuples(values_list):
    values = tuple(values_list)

    def fn():
        return max(values)

    assert run_and_validate(fn) == max(values)


@given(
    values_list=st.lists(floats, min_size=1, max_size=10),
)
def test_min_tuples(values_list):
    values = tuple(values_list)

    def fn():
        return min(values)

    assert run_and_validate(fn) == min(values)
