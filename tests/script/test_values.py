from hypothesis import given
from hypothesis import strategies as st

from sonolus.script.array import Array
from sonolus.script.num import Num
from sonolus.script.values import alloc, copy, sizeof, swap, zeros
from sonolus.script.vec import Vec2
from tests.script.conftest import run_and_validate

floats = st.floats(min_value=-1000, max_value=1000, allow_nan=False, allow_infinity=False)


def test_alloc_num():
    def fn():
        # The returned value is actually pretty useless since Num is immutable
        alloc(Num)
        return 1

    assert run_and_validate(fn) == 1


def test_alloc_vec2():
    def fn():
        v = alloc(Vec2)
        v @= Vec2(1, 2)
        return v

    assert run_and_validate(fn) == Vec2(1, 2)


def test_alloc_array():
    def fn():
        arr = alloc(Array[Num, 3])
        arr[0] = 1
        arr[1] = 2
        arr[2] = 3
        return arr

    result = run_and_validate(fn)
    assert result[0] == 1
    assert result[1] == 2
    assert result[2] == 3


def test_zeros_num():
    def fn():
        return zeros(Num)

    assert run_and_validate(fn) == 0


def test_zeros_vec2():
    def fn():
        return zeros(Vec2)

    assert run_and_validate(fn) == Vec2(0, 0)


def test_zeros_array():
    def fn():
        return zeros(Array[Num, 3])

    result = run_and_validate(fn)
    assert result[0] == 0
    assert result[1] == 0
    assert result[2] == 0


@given(value=floats)
def test_copy_num(value):
    def fn():
        original = value
        copied = copy(original)
        return Array(original, copied)

    result = run_and_validate(fn)
    assert result[0] == value
    assert result[1] == value


def test_copy_vec2():
    def fn():
        original = Vec2(3, 4)
        copied = copy(original)
        return Array(original.x, original.y, copied.x, copied.y)

    result = run_and_validate(fn)
    assert result[0] == 3
    assert result[1] == 4
    assert result[2] == 3
    assert result[3] == 4


def test_copy_array():
    def fn():
        original = Array(1, 2, 3)
        copied = copy(original)
        original[0] = 99
        return Array(original[0], copied[0])

    result = run_and_validate(fn)
    assert result[0] == 99
    assert result[1] == 1


def test_copy_independence():
    def fn():
        original = Vec2(1, 2)
        copied = copy(original)
        copied @= Vec2(99, 2)
        return Array(original.x, copied.x)

    result = run_and_validate(fn)
    assert result[0] == 1
    assert result[1] == 99


def test_swap_vec2():
    def fn():
        v1 = Vec2(1, 2)
        v2 = Vec2(3, 4)
        swap(v1, v2)
        return Array(v1.x, v1.y, v2.x, v2.y)

    result = run_and_validate(fn)
    assert result[0] == 3
    assert result[1] == 4
    assert result[2] == 1
    assert result[3] == 2


def test_swap_array():
    def fn():
        arr1 = Array(1, 2, 3)
        arr2 = Array(4, 5, 6)
        swap(arr1, arr2)
        return Array(arr1[0], arr1[1], arr1[2], arr2[0], arr2[1], arr2[2])

    result = run_and_validate(fn)
    assert result[0] == 4
    assert result[1] == 5
    assert result[2] == 6
    assert result[3] == 1
    assert result[4] == 2
    assert result[5] == 3


def test_sizeof_num():
    def fn():
        return sizeof(Num)

    result = run_and_validate(fn)
    assert result == 1


def test_sizeof_vec2():
    def fn():
        return sizeof(Vec2)

    result = run_and_validate(fn)
    assert result == 2


def test_sizeof_array():
    def fn():
        return sizeof(Array[Num, 5])

    result = run_and_validate(fn)
    assert result == 5
