from typing import Annotated, Any, Final

import pytest
from hypothesis import given
from hypothesis import strategies as st

from sonolus.script.array import Array
from sonolus.script.containers import VarArray
from sonolus.script.debug import assert_false, assert_true
from sonolus.script.internal.error import CompilationError
from sonolus.script.num import Num
from sonolus.script.record import Record
from sonolus.script.vec import Vec2
from tests.script.conftest import run_and_validate
from tests.script.test_record import Simple

# A permutation of 0..19, defined here since tuples can't be built inside a compiled function.
_SHUFFLED_20 = tuple((i * 7) % 20 for i in range(20))


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


def test_array_with_next():
    def fn():
        array = Array(1, 2, 3)
        iterator = iter(array)

        result = Array[int, 3](next(iterator), next(iterator), next(iterator))
        return result

    assert list(run_and_validate(fn)) == [1, 2, 3]


def test_array_with_iter():
    def fn():
        array = Array(1, 2, 3)
        iterator = iter(array)
        total = 0

        for x in iterator:
            total += x

        return total

    assert run_and_validate(fn) == 6


def test_array_reversed_negative_index():
    # Indexing a reversed view must normalize/bounds-check the caller's index before the
    # reversal transform. The buggy reverser applied the transform first, so valid negative
    # indices raised and out-of-range indices silently wrapped.
    def fn():
        array = Array(10, 20, 30, 40, 50)
        rev = reversed(array)  # logical view [50, 40, 30, 20, 10]
        return Array(rev[-1], rev[-2], rev[-5])

    assert list(run_and_validate(fn)) == [10, 20, 50]


def test_array_reversed_negative_index_set():
    def fn():
        array = Array(10, 20, 30, 40, 50)
        rev = reversed(array)
        rev[-1] = 99  # first element of the underlying array
        rev[0] = 77  # last element of the underlying array
        return array

    assert list(run_and_validate(fn)) == [99, 20, 30, 40, 77]


def test_array_reversed_iteration():
    def fn():
        array = Array(10, 20, 30, 40)
        total = 0
        for x in reversed(array):
            total = total * 100 + x
        return total

    assert run_and_validate(fn) == 40302010


def test_var_array_reversed_iteration():
    # The container's length isn't a compile-time constant, so this must use the runtime length.
    def fn():
        v = VarArray[int, 8].new()
        v.append(10)
        v.append(20)
        v.append(30)
        total = 0
        for x in reversed(v):
            total = total * 100 + x
        return total

    assert run_and_validate(fn) == 302010


def test_array_reversed_sort():
    def fn():
        array = Array(3, 1, 2)
        reversed(array).sort()
        return array

    assert list(run_and_validate(fn)) == [3, 2, 1]


def test_array_reversed_heap_sort():
    # With 20 constant-length elements, sort() takes the heap sort branch, which indexes the
    # unchecked view directly; this guards against bounds checks creeping back into that path.
    def fn():
        array = Array[int, 20](*_SHUFFLED_20)
        reversed(array).sort()
        return array

    assert list(run_and_validate(fn)) == list(range(19, -1, -1))


def test_var_array_reversed_sort():
    # A VarArray's length is dynamic, so both sort branches are compiled; heap sort is taken at 16 elements.
    def fn():
        v = VarArray[int, 16].new()
        for i in range(16):
            v.append((i * 7) % 16)
        reversed(v).sort()
        return v

    assert list(run_and_validate(fn)) == list(range(15, -1, -1))


def test_var_array_reversed_insertion_sort():
    # Fewer than 15 elements, so the insertion sort branch is the one taken at runtime.
    def fn():
        v = VarArray[int, 8].new()
        v.append(3)
        v.append(1)
        v.append(2)
        reversed(v).sort()
        return v

    assert list(run_and_validate(fn)) == [3, 2, 1]


def test_var_array_double_reversed_iteration():
    def fn():
        v = VarArray[int, 8].new()
        v.append(10)
        v.append(20)
        v.append(30)
        total = 0
        for x in reversed(reversed(v)):
            total = total * 100 + x
        return total

    assert run_and_validate(fn) == 102030


def test_get_unchecked_out_of_bounds_raises_index_error():
    # A constant out-of-bounds index to get_unchecked (outside a compilation context) is an
    # IndexError, not the misleading InternalError("Unexpected non-constant index").
    a = Array(1, 2, 3)
    with pytest.raises(IndexError, match="out of range"):
        a.get_unchecked(3)  # one past the end
    with pytest.raises(IndexError, match="out of range"):
        a.get_unchecked(5)  # positive out of bounds
    with pytest.raises(IndexError, match="out of range"):
        a.get_unchecked(-1)  # negative (not normalized by get_unchecked)


def test_array_negative_size_rejected():
    with pytest.raises(ValueError, match="size must be non-negative"):
        Array[int, -1]
    with pytest.raises(ValueError, match="size must be non-negative"):
        Array[int, -5]

    # The rejected type must not have been cached, so valid sizes still work afterwards.
    assert Array[int, 3].size() == 3


def test_array_negative_size_rejected_inside_function_body():
    def fn():
        return +Array[int, -1]

    with pytest.raises(ValueError, match="size must be non-negative"):
        run_and_validate(fn)


def test_array_non_integer_size_rejected():
    with pytest.raises(TypeError, match="size must be an integer"):
        Array[int, 0.5]
    with pytest.raises(TypeError, match="size must be an integer"):
        Array[int, "3"]
    with pytest.raises(TypeError, match="size must be an integer"):
        Array[int, None]

    # An integral float is still accepted since it's normalized to an int.
    assert Array[int, 3.0].size() == 3


def test_array_zero_size_still_supported():
    assert Array[int, 0].size() == 0
    assert Array[int, 0]._size_() == 0

    def fn():
        return len(Array[int, 0]())

    assert run_and_validate(fn) == 0


def test_array_positive_size_still_supported():
    assert Array[int, 3].size() == 3
    assert Array[int, 3]._size_() == 3

    def fn():
        return Array[int, 3](1, 2, 3)

    assert list(run_and_validate(fn)) == [1, 2, 3]


def test_record_with_array_field_layout():
    class WithArray(Record):
        a: Array[int, 3]
        b: int
        c: Array[Array[int, 2], 2]

    assert [(f.name, f.offset, f.type._size_()) for f in WithArray._fields_] == [
        ("a", 0, 3),
        ("b", 3, 1),
        ("c", 4, 4),
    ]
    assert WithArray._size_() == 8

    def fn():
        r = WithArray(Array(1, 2, 3), 4, Array(Array(5, 6), Array(7, 8)))
        r.a[1] = 9
        return r.a[0] * 10000 + r.a[1] * 1000 + r.a[2] * 100 + r.b * 10 + r.c[1][0]

    assert run_and_validate(fn) == 1 * 10000 + 9 * 1000 + 3 * 100 + 4 * 10 + 7


def test_var_array_negative_capacity_rejected():
    # VarArray and friends hold an Array field, so they're covered by the same check.
    with pytest.raises(ValueError, match="size must be non-negative"):
        VarArray[int, -1]

    assert VarArray[int, 0].capacity() == 0
    assert VarArray[int, 4].capacity() == 4


def test_array_wrong_type_arg_count_still_rejected():
    with pytest.raises(TypeError, match="Array expects 2 type arguments, got 1"):
        Array[int]


def test_array_generic_element_type_rejected():
    with pytest.raises(TypeError, match="Invalid element type for"):
        Array[Array, 2]
    with pytest.raises(TypeError, match="Invalid element type for"):
        Array[VarArray, 2]

    # The rejected type must not have been cached, so valid element types still work afterwards.
    assert Array[Array[int, 3], 2].element_type() is Array[int, 3]


def test_array_unsupported_element_type_rejected():
    with pytest.raises(TypeError, match="Invalid element type for"):
        Array[str, 3]
    with pytest.raises(TypeError, match="Invalid element type for"):
        Array[Any, 3]
    with pytest.raises(TypeError, match="Invalid element type for"):
        Array[None, 3]
    # A union only normalizes when all members agree, so a mixed union is still rejected.
    with pytest.raises(TypeError, match="Invalid element type for"):
        Array[int | str, 3]


def test_array_generic_element_type_rejected_inside_function_body():
    def fn():
        return +Array[Array, 2]

    with pytest.raises(TypeError, match="Invalid element type for"):
        run_and_validate(fn)


def test_array_element_type_deferred_for_type_params():
    # T is only known once Holder is parameterized, so the element type can't be checked yet.
    class Holder[T](Record):
        values: Array[T, 3]

    assert Holder[int]._size_() == 3

    def fn():
        return Holder(Array(1, 2, 3)).values[1]

    assert run_and_validate(fn) == 2


def test_array_element_type_checked_when_type_params_resolved():
    class Holder[T](Record):
        values: Array[T, 3]

    with pytest.raises(TypeError, match="Invalid element type for"):
        Holder[str]


def test_array_concrete_element_types_still_supported():
    assert Array[int, 3].element_type() is Num
    assert Array[Simple, 2].element_type() is Simple
    assert Array[Array[int, 2], 2].element_type() is Array[int, 2]


def test_array_element_type_spec_is_normalized():
    # Validating the element type also normalizes it, so equivalent spellings share one parameterization.
    assert Array[int | float, 3].element_type() is Num
    assert Array[Final[int], 3].element_type() is Num
    assert Array[Annotated[int, "x"], 3].element_type() is Num

    assert Array[int | float, 3]._size_() == 3
    assert Array[Final[int], 3]._size_() == 3
    assert Array[Annotated[int, "x"], 3]._size_() == 3

    assert Array[int | float, 3] is Array[int, 3]
    assert Array[Final[int], 3] is Array[int, 3]
    assert Array[Annotated[int, "x"], 3] is Array[int, 3]

    # The normalized element type is the cache key, so nested arrays collapse too.
    assert Array[Array[int | float, 2], 2] is Array[Array[int, 2], 2]


def test_array_normalized_element_type_usable():
    def fn():
        a = +Array[int | float, 3]
        a[1] = 5
        return a[0] + a[1] + a[2]

    assert run_and_validate(fn) == 5


def test_array_element_type_normalized_when_type_params_resolved():
    class Holder[T](Record):
        values: Array[T, 3]

    assert Holder[int | float]._size_() == 3
    assert Holder[int | float]._fields_[0].type is Array[int, 3]


# --- min()/max() with default= --------------------------------------------------------------------
#
# A VarArray built by appending in a loop has a length known only at runtime, so choosing `default` vs an
# element needs a runtime branch. Arrays and other constant-length containers are handled at compile time.


def test_array_max_default_when_compile_time_empty():
    def fn():
        return max(Array[int, 0](), default=42)

    assert run_and_validate(fn) == 42


def test_array_min_default_when_compile_time_empty():
    def fn():
        return min(Array[int, 0](), default=-5)

    assert run_and_validate(fn) == -5


def test_array_max_default_when_compile_time_non_empty():
    def fn():
        return max(Array(1, 2, 3), default=42)

    assert run_and_validate(fn) == 3


def test_array_min_default_when_compile_time_non_empty():
    def fn():
        return min(Array(1, 2, 3), default=-5)

    assert run_and_validate(fn) == 1


def test_var_array_max_default_when_runtime_empty():
    def fn():
        array = VarArray[int, 8].new()
        for i in range(0):
            array.append(i)
        return max(array, default=42)

    assert run_and_validate(fn) == 42


def test_var_array_min_default_when_runtime_empty():
    def fn():
        array = VarArray[int, 8].new()
        for i in range(0):
            array.append(i)
        return min(array, default=-5)

    assert run_and_validate(fn) == -5


def test_var_array_max_default_when_runtime_non_empty():
    def fn():
        array = VarArray[int, 8].new()
        for i in range(5):
            array.append(i * 3 - 4)
        return max(array, default=42)

    assert run_and_validate(fn) == 8


def test_var_array_min_default_when_runtime_non_empty():
    def fn():
        array = VarArray[int, 8].new()
        for i in range(5):
            array.append(i * 3 - 4)
        return min(array, default=42)

    assert run_and_validate(fn) == -4


def test_var_array_max_default_with_key_when_runtime_empty():
    def fn():
        array = VarArray[int, 8].new()
        for i in range(0):
            array.append(i)
        return max(array, default=99, key=lambda x: -x)

    assert run_and_validate(fn) == 99


def test_var_array_max_default_with_key_when_runtime_non_empty():
    def fn():
        array = VarArray[int, 8].new()
        for i in range(4):
            array.append(i - 2)
        return max(array, default=99, key=lambda x: -x)

    assert run_and_validate(fn) == -2


def test_var_array_min_default_with_key_when_runtime_non_empty():
    def fn():
        array = VarArray[int, 8].new()
        for i in range(4):
            array.append(i - 2)
        return min(array, default=99, key=lambda x: -x)

    assert run_and_validate(fn) == 1


def test_range_max_default_when_empty():
    def fn():
        return max(range(0), default=42)

    assert run_and_validate(fn) == 42


def test_range_min_default_when_empty():
    def fn():
        return min(range(0), default=-5)

    assert run_and_validate(fn) == -5


def test_range_max_default_when_non_empty():
    def fn():
        return max(range(3), default=42)

    assert run_and_validate(fn) == 2


def test_range_min_default_when_non_empty():
    def fn():
        return min(range(3), default=-5)

    assert run_and_validate(fn) == 0


def test_var_array_max_runtime_default_when_runtime_empty():
    # The default is itself a runtime value here, so neither side of the branch is a constant.
    def fn():
        array = VarArray[int, 8].new()
        for i in range(0):
            array.append(i)
        default = 0
        for _i in range(3):
            default += 14
        return max(array, default=default)

    assert run_and_validate(fn) == 42


def test_var_array_max_runtime_default_when_runtime_non_empty():
    def fn():
        array = VarArray[int, 8].new()
        for i in range(3):
            array.append(i)
        default = 0
        for _i in range(3):
            default += 14
        return max(array, default=default)

    assert run_and_validate(fn) == 2


def test_var_array_min_runtime_default_when_runtime_empty():
    def fn():
        array = VarArray[int, 8].new()
        for i in range(0):
            array.append(i)
        default = 0
        for _i in range(3):
            default -= 14
        return min(array, default=default)

    assert run_and_validate(fn) == -42


def test_var_array_min_runtime_default_when_runtime_non_empty():
    def fn():
        array = VarArray[int, 8].new()
        for i in range(3):
            array.append(i)
        default = 0
        for _i in range(3):
            default -= 14
        return min(array, default=default)

    assert run_and_validate(fn) == 0


def test_range_max_default_when_runtime_empty():
    # The bound comes from a container length, so whether the range is empty is only known at runtime.
    def fn():
        v = VarArray[int, 8].new()
        for i in range(0):
            v.append(i)
        return max(range(len(v)), default=42)

    assert run_and_validate(fn) == 42


def test_range_min_default_when_runtime_empty():
    def fn():
        v = VarArray[int, 8].new()
        for i in range(0):
            v.append(i)
        return min(range(len(v)), default=-5)

    assert run_and_validate(fn) == -5


def test_range_max_default_when_runtime_non_empty():
    def fn():
        v = VarArray[int, 8].new()
        for i in range(4):
            v.append(i)
        return max(range(len(v)), default=42)

    assert run_and_validate(fn) == 3


def test_range_min_default_when_runtime_non_empty():
    def fn():
        v = VarArray[int, 8].new()
        for i in range(4):
            v.append(i)
        return min(range(len(v)), default=-5)

    assert run_and_validate(fn) == 0


# A runtime-length container of a non-`Num` element type can't produce a `default` this way: the result would have
# to be either the default or an element depending on a runtime condition, and only numbers can merge out of a
# branch. That has to be a clear compile-time error rather than a wrong value.


def test_var_array_max_default_record_when_runtime_length_fails():
    def fn():
        array = VarArray[Vec2, 4].new()
        for i in range(3):
            array.append(Vec2(i, -i))
        return max(array, default=Vec2(9, 9), key=lambda v: v.x)

    with pytest.raises(CompilationError, match="conflicting return values"):
        run_and_validate(fn)


def test_var_array_min_default_record_when_runtime_length_fails():
    def fn():
        array = VarArray[Vec2, 4].new()
        for i in range(3):
            array.append(Vec2(i, -i))
        return min(array, default=Vec2(9, 9), key=lambda v: v.x)

    with pytest.raises(CompilationError, match="conflicting return values"):
        run_and_validate(fn)


def test_var_array_max_default_record_when_runtime_empty_fails():
    # Appending inside a loop makes the length dynamic even though the loop never runs, so this is the same
    # unsupported case rather than the compile-time-empty one.
    def fn():
        array = VarArray[Vec2, 4].new()
        for i in range(0):
            array.append(Vec2(i, i))
        return max(array, default=Vec2(9, 9), key=lambda v: v.x)

    with pytest.raises(CompilationError, match="conflicting return values"):
        run_and_validate(fn)


def test_var_array_max_default_array_element_when_runtime_length_fails():
    def fn():
        array = VarArray[Array[int, 2], 4].new()
        for i in range(3):
            array.append(Array(i, i * 2))
        return max(array, default=Array(7, 8), key=lambda a: a[0])

    with pytest.raises(CompilationError, match="conflicting return values"):
        run_and_validate(fn)


def test_array_max_default_record_when_compile_time_empty():
    # Emptiness known at compile time needs no branch, so a non-`Num` element type is fine here.
    def fn():
        return max(Array[Vec2, 0](), default=Vec2(9, 9), key=lambda v: v.x)

    assert run_and_validate(fn) == Vec2(9, 9)


def test_array_max_default_record_when_compile_time_non_empty():
    def fn():
        return max(Array(Vec2(1, -1), Vec2(2, -2)), default=Vec2(9, 9), key=lambda v: v.x)

    assert run_and_validate(fn) == Vec2(2, -2)


def test_array_min_default_record_when_compile_time_non_empty():
    def fn():
        return min(Array(Vec2(1, -1), Vec2(2, -2)), default=Vec2(9, 9), key=lambda v: v.x)

    assert run_and_validate(fn) == Vec2(1, -1)


def test_var_array_max_without_default_when_runtime_empty_still_fails():
    def fn():
        array = VarArray[int, 8].new()
        for i in range(0):
            array.append(i)
        return max(array)

    with pytest.raises(ValueError, match="empty"):
        run_and_validate(fn)


def test_var_array_min_without_default_when_runtime_empty_still_fails():
    def fn():
        array = VarArray[int, 8].new()
        for i in range(0):
            array.append(i)
        return min(array)

    with pytest.raises(ValueError, match="empty"):
        run_and_validate(fn)


def test_var_array_max_default_type_mismatch_fails():
    def fn():
        array = VarArray[int, 8].new()
        for i in range(3):
            array.append(i)
        return max(array, default=Vec2(1, 1))

    with pytest.raises(CompilationError, match="incompatible with the element type"):
        run_and_validate(fn)


def test_var_array_min_default_type_mismatch_fails():
    def fn():
        array = VarArray[Vec2, 4].new()
        for i in range(3):
            array.append(Vec2(i, i))
        return min(array, default=0, key=lambda v: v.x)

    with pytest.raises(CompilationError, match="incompatible with the element type"):
        run_and_validate(fn)


def test_array_max_default_type_mismatch_when_compile_time_non_empty_fails():
    def fn():
        return max(Array(1, 2, 3), default=Vec2(1, 1))

    with pytest.raises(CompilationError, match="incompatible with the element type"):
        run_and_validate(fn)


def test_var_array_max_default_unsupported_type_fails():
    def fn():
        array = VarArray[int, 8].new()
        for i in range(3):
            array.append(i)
        return max(array, default=None)

    with pytest.raises(CompilationError, match="must be a number, record, or array"):
        run_and_validate(fn)


def test_var_array_min_default_unsupported_type_fails():
    def fn():
        array = VarArray[int, 8].new()
        for i in range(3):
            array.append(i)
        return min(array, default=(1, 2))

    with pytest.raises(CompilationError, match="must be a number, record, or array"):
        run_and_validate(fn)
