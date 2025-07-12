import pytest
from hypothesis import given
from hypothesis import strategies as st

from sonolus.script.array import Array
from sonolus.script.containers import VarArray
from sonolus.script.debug import assert_false, assert_true
from sonolus.script.record import Record
from tests.script.conftest import run_and_validate
from tests.script.test_record import Simple


def test_array_constructor():
    def fn():
        return Array(1, 2, 3)

    assert list(run_and_validate(fn)) == [1, 2, 3]


@given(args=st.lists(st.integers(min_value=-9999, max_value=9999), min_size=1, max_size=10))
def test_array_spread(args):
    tuple_args = tuple(args)  # lists are not supported

    def fn():
        return Array(*tuple_args)

    assert list(run_and_validate(fn)) == list(args)


def test_array_constructor_with_type():
    def fn():
        return Array[int, 3](1, 2, 3)

    assert list(run_and_validate(fn)) == [1, 2, 3]


def test_array_constructor_with_type_mismatch_fails():
    def fn():
        return Array[Simple, 3](1, 2, 3)

    with pytest.raises(TypeError):
        run_and_validate(fn)


def test_array_constructor_with_size_mismatch_fails():
    def fn():
        return Array[int, 3](1, 2)

    with pytest.raises(ValueError, match="should be used with 3 values, got 2"):
        run_and_validate(fn)


def test_array_constructor_with_no_args_fails():
    def fn():
        return Array()

    with pytest.raises(ValueError, match="constructor should be used with at least one value"):
        run_and_validate(fn)


def test_array_constructor_with_heterogeneous_args_fails():
    def fn():
        return Array(1, Simple(2), 3)

    with pytest.raises(TypeError):
        run_and_validate(fn)


def test_array_set():
    def fn():
        array = Array[int, 3](1, 2, 3)
        array[1] = 4
        return array

    assert list(run_and_validate(fn)) == [1, 4, 3]


def test_array_equality():
    def fn():
        a1 = Array(1, 2, 3)
        a2 = Array(1, 2, 3)
        a3 = Array(4, 5, 6)
        a4 = Array(Simple(1), Simple(2), Simple(3))
        a5 = Array(Simple(1), Simple(2), Simple(3))
        a6 = Array(Simple(4), Simple(5), Simple(6))

        assert_true(a1 == a2)
        assert_false(a1 != a2)
        assert_true(a1 != a3)
        assert_false(a1 == a3)
        assert_true(a4 == a5)
        assert_false(a4 != a5)
        assert_true(a4 != a6)
        assert_false(a4 == a6)

        return 1

    assert run_and_validate(fn) == 1


def test_array_equality_of_different_lengths():
    def fn():
        a1 = Array(1, 2, 3)
        a2 = Array(1, 2)

        return a1 == a2

    assert not run_and_validate(fn)


def test_array_record_item_operations():
    def fn():
        array = Array[Simple, 3](Simple(1), Simple(2), Simple(3))
        other1 = Simple(4)
        other2 = Simple(5)

        # Both of these should work the same
        array[1] = other1
        array[2] @= other2

        assert_true(array == Array[Simple, 3](Simple(1), Simple(4), Simple(5)))
        array[1].value = 6
        array[2].value = 7

        assert_true(other1.value == 4)
        assert_true(other2.value == 5)
        assert_true(array[1].value == 6)
        assert_true(array[2].value == 7)

        return array

    assert list(run_and_validate(fn)) == [Simple(1), Simple(6), Simple(7)]


def test_array_contains():
    def fn():
        array = Array(1, 2, 3)

        assert_true(1 in array)
        assert_true(2 in array)
        assert_true(3 in array)
        assert_false(4 in array)

        return 1

    assert run_and_validate(fn) == 1


def test_array_reversed():
    def fn():
        array = Array(1, 2, 3)

        return reversed(array)

    assert list(run_and_validate(fn)) == [3, 2, 1]


def test_array_iteration():
    def fn():
        array = Array(1, 2, 3)
        total = 0

        for i in array:
            total += i

        return total

    assert run_and_validate(fn) == 6


def test_array_enumerate():
    def fn():
        array = Array(1, 3, 5)

        for i, v in enumerate(array):
            assert_true(v == array[i])  # noqa: PLR1736

        return 1

    assert run_and_validate(fn) == 1


def test_array_negative_indexing():
    def fn():
        array = Array(10, 20, 30, 40, 50)

        return Array(array[-1], array[-2], array[-3], array[-4], array[-5])

    assert list(run_and_validate(fn)) == [50, 40, 30, 20, 10]


def test_array_negative_indexing_set():
    def fn():
        array = Array(10, 20, 30, 40, 50)

        array[-1] = 99
        array[-2] = 88
        array[-3] = 77

        return array

    assert list(run_and_validate(fn)) == [10, 20, 77, 88, 99]


@given(args=st.lists(st.integers(min_value=-100, max_value=100), min_size=1, max_size=20))
def test_array_negative_positive_indexing_equivalence(args):
    tuple_args = tuple(args)
    n = len(args)

    def fn():
        array = Array[int, n](*tuple_args)

        results = VarArray[bool, n].new()
        for i in range(n):
            results.append(array[i] == array[i - n])

        return results

    assert all(run_and_validate(fn))


def test_array_index():
    def fn():
        array = Array(1, 2, 3)

        assert_true(array.index(1) == 0)
        assert_true(array.index(2) == 1)
        assert_true(array.index(3) == 2)
        assert_true(array.index(4) == -1)

        return 1

    assert run_and_validate(fn) == 1


def test_array_max():
    def fn():
        array = Array(1, 2, 3)

        return max(array)

    assert run_and_validate(fn) == 3


def test_array_min():
    def fn():
        array = Array(1, 2, 3)

        return min(array)

    assert run_and_validate(fn) == 1


def test_array_count():
    def fn():
        array = Array(1, 2, 3, 2, 1)

        return array.count(1)

    assert run_and_validate(fn) == 2


@given(
    args=st.lists(st.integers(min_value=-9999, max_value=9999), min_size=0, max_size=100),
    reverse=st.booleans(),
)
def test_array_sort(args, reverse: bool):
    tuple_args = tuple(args)
    n = len(args)

    def fn():
        array = Array[int, n](*tuple_args)

        array.sort(reverse=reverse)
        return array

    assert list(run_and_validate(fn)) == sorted(args, reverse=reverse)


@given(
    args=st.lists(st.integers(min_value=-999, max_value=999), min_size=0, max_size=100),
    reverse=st.booleans(),
    a=st.integers(min_value=-9, max_value=9),
    b=st.integers(min_value=-99, max_value=99),
    c=st.integers(min_value=-999, max_value=999),
)
def test_array_sort_with_key(args, reverse: bool, a: int, b: int, c: int):
    tuple_args = tuple(args)
    n = len(args)

    def fn():
        array = Array[int, n](*tuple_args)

        array.sort(key=lambda x: a * x * x + b * x + c, reverse=reverse)
        return array

    assert list(run_and_validate(fn)) == sorted(args, key=lambda x: a * x * x + b * x + c, reverse=reverse)


@given(
    args=st.lists(st.integers(min_value=-999, max_value=999), min_size=1, max_size=100),
    a=st.integers(min_value=-9, max_value=9),
    b=st.integers(min_value=-99, max_value=99),
    c=st.integers(min_value=-999, max_value=999),
)
def test_array_max_with_key(args, a: int, b: int, c: int):
    tuple_args = tuple(args)
    n = len(args)

    def fn():
        array = Array[int, n](*tuple_args)

        return max(array, key=lambda x: a * x * x + b * x + c)

    assert run_and_validate(fn) == max(args, key=lambda x: a * x * x + b * x + c)


@given(
    args=st.lists(st.integers(min_value=-999, max_value=999), min_size=1, max_size=100),
    a=st.integers(min_value=-9, max_value=9),
    b=st.integers(min_value=-99, max_value=99),
    c=st.integers(min_value=-999, max_value=999),
)
def test_array_min_with_key(args, a: int, b: int, c: int):
    tuple_args = tuple(args)
    n = len(args)

    def fn():
        array = Array[int, n](*tuple_args)

        return min(array, key=lambda x: a * x * x + b * x + c)

    assert run_and_validate(fn) == min(args, key=lambda x: a * x * x + b * x + c)


@given(
    args=st.lists(st.integers(min_value=-9999, max_value=9999), min_size=0, max_size=100),
)
def test_array_reverse(args):
    tuple_args = tuple(args)
    n = len(args)

    def fn():
        array = Array[int, n](*tuple_args)

        array.reverse()
        return array

    assert list(run_and_validate(fn)) == list(reversed(args))


class Ele(Record):
    value: int

    def __eq__(self, other):
        return self.value == other.value

    def __ne__(self, other):
        return self.value != other.value

    def __hash__(self):
        return hash(self.value)

    def __lt__(self, other):
        return self.value < other.value

    def __le__(self, other):
        return self.value <= other.value

    def __gt__(self, other):
        return self.value > other.value

    def __ge__(self, other):
        return self.value >= other.value


@given(
    args=st.lists(st.integers(min_value=-9999, max_value=9999), min_size=0, max_size=100),
    reverse=st.booleans(),
)
def test_array_sort_records(args, reverse: bool):
    tuple_args = tuple(Ele(value=v) for v in args)
    n = len(args)

    def fn():
        array = Array[Ele, n](*tuple_args)

        array.sort(reverse=reverse)
        return array

    assert list(run_and_validate(fn)) == sorted(tuple_args, reverse=reverse)


def test_array_truthiness_empty():
    def fn():
        x = Array[int, 0]()
        return 1 if x else 0

    assert run_and_validate(fn) == 0


def test_array_truthiness_non_empty():
    def fn():
        x = Array(1, 2, 3)
        return 1 if x else 0

    assert run_and_validate(fn) == 1
