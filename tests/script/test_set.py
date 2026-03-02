# ruff: noqa: SIM113, PLC2701

import pytest

from sonolus.script.array import Array
from sonolus.script.internal.context import ctx
from sonolus.script.internal.impl import validate_value
from sonolus.script.internal.math_impls import _floor
from sonolus.script.internal.meta_fn import meta_fn
from sonolus.script.internal.random import _random
from sonolus.script.internal.set_impl import SetImpl
from sonolus.script.internal.tuple_impl import TupleImpl
from sonolus.script.num import _is_num
from tests.script.conftest import run_and_validate


@meta_fn
def bb(*x):
    if len(x) == 1:
        x = x[0]
    if not ctx():
        return x
    x = validate_value(x)
    if _is_num(x):
        return x + _floor(_random())
    elif isinstance(x, TupleImpl):
        return TupleImpl(tuple(bb(e) for e in x.value))
    else:
        return x


# __contains__


def test_contains_present_small_size_string_key():
    def fn():
        s = {"a", "b"}
        return Array(
            "a" in s,
            "b" in s,
        )

    assert run_and_validate(fn) == Array(True, True)


def test_contains_present_small_size_numeric_key():
    def fn():
        s = {1, 2}
        return Array(1 in s, 2 in s)

    assert run_and_validate(fn) == Array(True, True)


def test_contains_present_small_size_tuple_key():
    def fn():
        s = {(1, 1), (2, 2)}
        return Array((1, 1) in s, (2, 2) in s)

    assert run_and_validate(fn) == Array(True, True)


def test_contains_present_small_size_mixed_key():
    def fn():
        s = {"a", 2, (3, 3)}
        return Array("a" in s, 2 in s, (3, 3) in s)

    assert run_and_validate(fn) == Array(True, True, True)


def test_contains_present_large_size_numeric_key():
    s = {20, 3, 15, 7, 25, 1, 18, 9, 22, 5, 12, 16, 8, 24, 2, 19, 11, 14, 6, 23, 4, 17, 13, 21, 10}

    def fn():
        return Array(1 in s, 13 in s, 25 in s)

    assert run_and_validate(fn) == Array(True, True, True)


def test_contains_present_large_size_string_key():
    s = set("azbycxdwevfugthsirjqkplomn")

    def fn():
        return Array("a" in s, "m" in s, "z" in s)

    assert run_and_validate(fn) == Array(True, True, True)


def test_contains_present_large_size_tuple_key():
    s = {(k, k) for k in [20, 3, 15, 7, 25, 1, 18, 9, 22, 5, 12, 16, 8, 24, 2, 19, 11, 14, 6, 23, 4, 17, 13, 21, 10]}

    def fn():
        return Array((1, 1) in s, (13, 13) in s, (25, 25) in s)

    assert run_and_validate(fn) == Array(True, True, True)


def test_contains_present_large_size_mixed_key():
    s = {"a", "b", "c", "d", "e", "f", "g", 1, 2, 3, 4, 5, 6, 7, (1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (6, 6), (7, 7)}

    def fn():
        return Array("a" in s, 4 in s, (4, 4) in s)

    assert run_and_validate(fn) == Array(True, True, True)


def test_contains_absent_empty():
    s = set()

    def fn():
        return "a" in s

    assert not run_and_validate(fn)


def test_contains_absent_small_size_string_key():
    def fn():
        s = {"a", "b"}
        return Array("c" in s, "d" in s)

    assert run_and_validate(fn) == Array(False, False)


def test_contains_absent_small_size_numeric_key():
    def fn():
        s = {1, 2}
        return Array(3 in s, 4 in s)

    assert run_and_validate(fn) == Array(False, False)


def test_contains_absent_large_size_numeric_key():
    s = {20, 3, 15, 7, 25, 1, 18, 9, 22, 5, 12, 16, 8, 24, 2, 19, 11, 14, 6, 23, 4, 17, 13, 21, 10}

    def fn():
        return Array(0 in s, 26 in s, -1 in s)

    assert run_and_validate(fn) == Array(False, False, False)


def test_contains_with_runtime_key_small_size_numeric():
    s = {1, 2, 3}

    def fn():
        return Array(bb(1) in s, bb(2) in s, bb(4) in s)

    assert run_and_validate(fn) == Array(True, True, False)


def test_contains_with_runtime_key_large_size_numeric():
    s = {20, 3, 15, 7, 25, 1, 18, 9, 22, 5, 12, 16, 8, 24, 2, 19, 11, 14, 6, 23, 4, 17, 13, 21, 10}

    def fn():
        return Array(bb(1) in s, bb(13) in s, bb(26) in s)

    assert run_and_validate(fn) == Array(True, True, False)


# __len__


def test_len_empty():
    s = set()

    def fn():
        return len(s)

    assert run_and_validate(fn) == 0


def test_len_small():
    def fn():
        s = {"a", "b", "c"}
        return len(s)

    assert run_and_validate(fn) == 3


def test_len_large():
    s = set(range(25))

    def fn():
        return len(s)

    assert run_and_validate(fn) == 25


# __iter__


def test_iter_small_size_numeric_key():
    s = {1, 2, 3}

    def fn():
        results = +Array[int, 3]
        i = 0
        for k in s:
            results[i] = k
            i += 1
        return results

    assert sorted(run_and_validate(fn)) == sorted(s)


def test_iter_small_size_string_key():
    s = {"a", "b", "c"}

    def fn():
        results = +Array[int, 3]
        i = 0
        for _k in s:
            results[i] = "a" in s
            i += 1
        return results

    assert run_and_validate(fn) == Array(True, True, True)


def test_iter_large_size_numeric_key():
    s = {20, 3, 15, 7, 25, 1, 18, 9, 22, 5, 12, 16, 8, 24, 2, 19, 11, 14, 6, 23, 4, 17, 13, 21, 10}

    def fn():
        results = +Array[int, 25]
        i = 0
        for k in s:
            results[i] = k
            i += 1
        return results

    assert sorted(run_and_validate(fn)) == sorted(s)


def test_iter_large_size_mixed_key():
    s = {"a", "b", "c", "d", "e", "f", "g", 1, 2, 3, 4, 5, 6, 7, (1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (6, 6), (7, 7)}

    def fn():
        count = 0
        for _ in s:
            count += 1
        return count

    assert run_and_validate(fn) == len(s)


def test_iter_empty():
    s = set()

    def fn():
        count = 0
        for _ in s:
            count += 1
        return count

    assert run_and_validate(fn) == 0


# __or__


def test_or_disjoint():
    s1 = {1, 2, 3}
    s2 = {4, 5, 6}

    def fn():
        s3 = s1 | s2
        results = +Array[int, 6]
        i = 0
        for k in s3:
            results[i] = k
            i += 1
        return results

    assert sorted(run_and_validate(fn)) == sorted(s1 | s2)


def test_or_overlapping():
    s1 = {1, 2, 3}
    s2 = {2, 3, 4}

    def fn():
        s3 = s1 | s2
        results = +Array[int, 4]
        i = 0
        for k in s3:
            results[i] = k
            i += 1
        return results

    assert sorted(run_and_validate(fn)) == sorted(s1 | s2)


def test_or_with_empty():
    s1 = {1, 2, 3}
    s2: frozenset = frozenset()

    def fn():
        s3 = s1 | s2
        results = +Array[int, 3]
        i = 0
        for k in s3:
            results[i] = k
            i += 1
        return results

    assert sorted(run_and_validate(fn)) == sorted(s1)


def test_or_contains_check():
    s1 = {1, 2, 3}
    s2 = {4, 5, 6}

    def fn():
        s3 = s1 | s2
        return Array(1 in s3, 4 in s3, 7 in s3)

    assert run_and_validate(fn) == Array(True, True, False)


def test_or_large():
    s1 = {20, 3, 15, 7, 25, 1, 18, 9, 22, 5, 12}
    s2 = {16, 8, 24, 2, 19, 11, 14, 6, 23, 4, 17, 13, 21, 10}

    def fn():
        s3 = s1 | s2
        total = 0
        for k in s3:
            total += k
        return total

    assert run_and_validate(fn) == sum(s1 | s2)


# __eq__


def test_eq_raises():
    # Use SetImpl instances directly so Python mode also raises TypeError
    s1 = SetImpl.from_set({1, 2})
    s2 = SetImpl.from_set({1, 2})

    def fn():
        return s1 == s2

    with pytest.raises(TypeError):
        run_and_validate(fn)


# isinstance


def test_isinstance_set():
    s = {"a", "b"}

    def fn():
        return isinstance(s, set)

    assert run_and_validate(fn)


def test_isinstance_set_not_dict():
    d = {"a": 1}

    def fn():
        return isinstance(d, set)

    assert not run_and_validate(fn)
