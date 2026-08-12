"""Tests for the ordering operators of `sonolus.script.containers.Pair`.

Python's own tuple comparison is an independent reference for lexicographic order, so `run_and_validate`
is a real oracle here rather than a differential one. `ArrayLike.sort` compares only with `<` and `>`, so
`__le__` and `__ge__` need direct cases of their own.
"""

from itertools import starmap

from hypothesis import given
from hypothesis import strategies as st

from sonolus.script.array import Array
from sonolus.script.containers import Pair
from tests.script.conftest import run_and_validate

small_ints = st.integers(min_value=-3, max_value=3)
pair_lists = st.lists(st.tuples(small_ints, small_ints), min_size=2, max_size=8)


def _flags(a_first, a_second, b_first, b_second):
    def fn():
        a = Pair(a_first, a_second)
        b = Pair(b_first, b_second)
        return Array(
            1 if a < b else 0,
            1 if a <= b else 0,
            1 if a > b else 0,
            1 if a >= b else 0,
        )

    lt, le, gt, ge = run_and_validate(fn)
    return lt, le, gt, ge


def test_pair_ordering_first_decides():
    # `second` points the other way in both cases, so a self.second-vs-other.first mix-up shows up here.
    assert _flags(1, 9, 2, 0) == (1, 1, 0, 0)
    assert _flags(2, 0, 1, 9) == (0, 0, 1, 1)


def test_pair_ordering_equal_firsts_second_decides():
    assert _flags(1, 0, 1, 5) == (1, 1, 0, 0)
    assert _flags(1, 5, 1, 0) == (0, 0, 1, 1)


def test_pair_ordering_fully_equal():
    # The only case that separates the strict operators from the non-strict ones.
    assert _flags(1, 2, 1, 2) == (0, 1, 0, 1)


@given(small_ints, small_ints, small_ints, small_ints)
def test_pair_ordering_matches_python_tuple_order(a_first, a_second, b_first, b_second):
    lt, le, gt, ge = _flags(a_first, a_second, b_first, b_second)
    left = (a_first, a_second)
    right = (b_first, b_second)
    assert bool(lt) == (left < right)
    assert bool(le) == (left <= right)
    assert bool(gt) == (left > right)
    assert bool(ge) == (left >= right)


@given(pair_lists, st.booleans())
def test_sorting_pairs_matches_sorted_tuples(pair_tuples, reverse):
    pair_args = tuple(starmap(Pair, pair_tuples))
    count = len(pair_tuples)

    def fn():
        values = Array[Pair[int, int], count](*pair_args)
        values.sort(reverse=reverse)
        return values

    result = [(p.first, p.second) for p in run_and_validate(fn)]
    assert result == sorted(pair_tuples, reverse=reverse)


def test_pair_tuple_property_inside_compiled_code():
    def fn():
        first, second = Pair(3, 4).tuple
        return Array(first, second)

    assert list(run_and_validate(fn)) == [3, 4]
