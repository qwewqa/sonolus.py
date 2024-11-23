from hypothesis import given
from hypothesis import strategies as st

from sonolus.script.array import Array
from sonolus.script.containers import VarArray
from tests.script.conftest import validate_dual_run

ints = st.integers(min_value=-10, max_value=10)


def test_tuple_destructure():
    def fn():
        t = (1, 2), (3, 4), 5
        (a, b), (c, d), e = t
        return Array(a, b, c, d, e)

    assert validate_dual_run(fn) == Array(1, 2, 3, 4, 5)


def test_tuple_addition():
    def fn():
        t1 = 1, 2
        t2 = 3, 4
        (a, b), (c, d) = t1, t2
        return Array(a, b, c, d)

    assert validate_dual_run(fn) == Array(1, 2, 3, 4)


@given(
    t1_list=st.lists(ints, min_size=0, max_size=10),
    t2_list=st.lists(ints, min_size=0, max_size=10),
)
def test_tuple_comparison(t1_list, t2_list):
    t1 = tuple(t1_list)
    t2 = tuple(t2_list)

    def fn():
        return Array(t1 == t2, t1 != t2, t1 < t2, t1 <= t2, t1 > t2, t1 >= t2)

    assert validate_dual_run(fn) == Array(t1 == t2, t1 != t2, t1 < t2, t1 <= t2, t1 > t2, t1 >= t2)


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

    assert list(validate_dual_run(fn)) == list(t)


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

    assert list(validate_dual_run(fn)) == [1, 2, 3, 4, 5]
