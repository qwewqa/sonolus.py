# ruff: noqa: SIM113, PLC2701

import pytest

from sonolus.script.array import Array
from sonolus.script.containers import Box
from sonolus.script.internal.context import ctx
from sonolus.script.internal.impl import validate_value
from sonolus.script.internal.math_impls import _floor
from sonolus.script.internal.meta_fn import meta_fn
from sonolus.script.internal.random import _random
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


def test_dict_get_present_small_size_string_key():
    d = {"a": 10, "b": 20}

    def fn():
        return Array(d[bb("a")], d[bb("b")])

    assert run_and_validate(fn) == Array(10, 20)


def test_dict_get_present_small_size_numeric_key():
    d = {1: 10, 2: 20}

    def fn():
        return Array(d[bb(1)], d[bb(2)])

    assert run_and_validate(fn) == Array(10, 20)


def test_dict_get_present_small_size_tuple_key():
    d = {(1, 1): 10, (3, 3): 30, (2, 2): 20}

    def fn():
        return Array(d[bb(1, 1)], d[bb(2, 2)], d[bb(3, 3)])

    assert run_and_validate(fn) == Array(10, 20, 30)


def test_dict_get_present_small_size_mixed_key():
    d = {
        "a": 10,
        (
            3,
            3,
        ): 30,
        2: 20,
    }

    def fn():
        return Array(d[bb("a")], d[bb(2)], d[bb(3, 3)])

    assert run_and_validate(fn) == Array(10, 20, 30)


def test_dict_get_present_large_size_string_key():
    d = {k: i * 10 for i, k in enumerate("azbycxdwevfugthsirjqkplomn", start=1)}

    def fn():
        return Array(d[bb("a")], d[bb("m")], d[bb("z")])

    assert run_and_validate(fn) == Array(10, 250, 20)


def test_dict_get_present_large_size_numeric_key():
    d = {k: k * 10 for k in [20, 3, 15, 7, 25, 1, 18, 9, 22, 5, 12, 16, 8, 24, 2, 19, 11, 14, 6, 23, 4, 17, 13, 21, 10]}

    def fn():
        return Array(d[bb(1)], d[bb(13)], d[bb(25)])

    assert run_and_validate(fn) == Array(10, 130, 250)


def test_dict_get_present_large_size_tuple_key():
    d = {
        (k, k): k * 10
        for k in [20, 3, 15, 7, 25, 1, 18, 9, 22, 5, 12, 16, 8, 24, 2, 19, 11, 14, 6, 23, 4, 17, 13, 21, 10]
    }

    def fn():
        return Array(d[bb(1, 1)], d[bb(13, 13)], d[bb(25, 25)])

    assert run_and_validate(fn) == Array(10, 130, 250)


def test_dict_get_present_large_size_mixed_key():
    d = {
        "a": 10,
        "b": 20,
        "c": 30,
        "d": 40,
        "e": 50,
        "f": 60,
        "g": 70,
        1: 80,
        2: 90,
        3: 100,
        4: 110,
        5: 120,
        6: 130,
        7: 140,
        (1, 1): 150,
        (2, 2): 160,
        (3, 3): 170,
        (4, 4): 180,
        (5, 5): 190,
        (6, 6): 200,
        (7, 7): 210,
    }

    def fn():
        return Array(d[bb("a")], d[bb(4)], d[bb(4, 4)])

    assert run_and_validate(fn) == Array(10, 110, 180)


def test_dict_get_absent_small_size_string_key():
    d = {"a": 10, "b": 20}

    def fn():
        return d[bb("A")]

    with pytest.raises(KeyError):
        run_and_validate(fn)


def test_dict_get_absent_small_size_numeric_key():
    d = {1: 10, 2: 20}

    def fn():
        return d[bb(3)]

    with pytest.raises(KeyError):
        run_and_validate(fn)


def test_dict_get_absent_small_size_tuple_key():
    d = {(1, 1): 10, (2, 2): 20}

    def fn():
        return d[bb(3, 3)]

    with pytest.raises(KeyError):
        run_and_validate(fn)


def test_dict_get_absent_small_size_mixed_key():
    d = {"a": 10, 2: 20, (3, 3): 30}

    def fn():
        return d[bb("b")]

    with pytest.raises(KeyError):
        run_and_validate(fn)


@pytest.mark.parametrize("key", ["A", "Z", "aa", "1"])
def test_dict_get_absent_large_size_string_key(key):
    d = {k: i * 10 for i, k in enumerate("azbycxdwevfugthsirjqkplomn", start=1)}

    def fn():
        return d[bb(key)]

    with pytest.raises(KeyError):
        run_and_validate(fn)


@pytest.mark.parametrize("key", [0, 26, 100, -1, 1.5])
def test_dict_get_absent_large_size_numeric_key(key):
    d = {k: k * 10 for k in [20, 3, 15, 7, 25, 1, 18, 9, 22, 5, 12, 16, 8, 24, 2, 19, 11, 14, 6, 23, 4, 17, 13, 21, 10]}

    def fn():
        return d[bb(key)]

    with pytest.raises(KeyError):
        run_and_validate(fn)


@pytest.mark.parametrize("key", [(0, 0), (26, 26), (1, 2), (100, 100)])
def test_dict_get_absent_large_size_tuple_key(key):
    d = {
        (k, k): k * 10
        for k in [20, 3, 15, 7, 25, 1, 18, 9, 22, 5, 12, 16, 8, 24, 2, 19, 11, 14, 6, 23, 4, 17, 13, 21, 10]
    }

    def fn():
        return d[bb(key)]

    with pytest.raises(KeyError):
        run_and_validate(fn)


@pytest.mark.parametrize("key", ["h", 8, (8, 8)])
def test_dict_get_absent_large_size_mixed_key(key):
    d = {
        "a": 1,
        "b": 1,
        "c": 1,
        "d": 1,
        "e": 1,
        "f": 1,
        "g": 1,
        1: 2,
        2: 2,
        3: 2,
        4: 2,
        5: 2,
        6: 2,
        7: 2,
        (1, 1): 3,
        (2, 2): 3,
        (3, 3): 3,
        (4, 4): 3,
        (5, 5): 3,
        (6, 6): 3,
        (7, 7): 3,
    }

    def fn():
        return d[bb(key)]

    with pytest.raises(KeyError):
        run_and_validate(fn)


def test_dict_get_present_and_modify_small_size_string_key():
    def fn():
        v1 = Box(1)
        v2 = Box(2)
        d = {"a": v1, "b": v2}
        d["a"].value = 10
        d["b"].value = 20
        return Array(v1.value, v2.value)

    assert run_and_validate(fn) == Array(10, 20)


def test_dict_get_present_and_modify_small_size_numeric_key():
    def fn():
        v1 = Box(1)
        v2 = Box(2)
        d = {1: v1, 2: v2}
        d[1].value = 10
        d[2].value = 20
        return Array(v1.value, v2.value)

    assert run_and_validate(fn) == Array(10, 20)


def test_dict_get_present_and_modify_small_size_tuple_key():
    def fn():
        v1 = Box(1)
        v2 = Box(2)
        d = {(1, 1): v1, (2, 2): v2}
        d[1, 1].value = 10
        d[2, 2].value = 20
        return Array(v1.value, v2.value)

    assert run_and_validate(fn) == Array(10, 20)


def test_dict_get_present_and_modify_small_size_mixed_key():
    def fn():
        v1 = Box(1)
        v2 = Box(2)
        v3 = Box(3)
        d = {"a": v1, 2: v2, (3, 3): v3}
        d["a"].value = 10
        d[2].value = 20
        d[3, 3].value = 30
        return Array(v1.value, v2.value, v3.value)

    assert run_and_validate(fn) == Array(10, 20, 30)


def test_dict_get_present_and_modify_large_size_mixed_key():
    def fn():
        v1 = Box(1)
        v2 = Box(2)
        v3 = Box(3)
        d = {
            "a": v1,
            "b": v1,
            "c": v1,
            "d": v1,
            "e": v1,
            "f": v1,
            (1, 1): v3,
            (2, 2): v3,
            (3, 3): v3,
            (4, 4): v3,
            (5, 5): v3,
            (6, 6): v3,
            1: v2,
            2: v2,
            3: v2,
            4: v2,
            5: v2,
            6: v2,
        }
        d["a"].value = 10
        d[2].value = 20
        d[3, 3].value = 30
        return Array(v1.value, v2.value, v3.value)

    assert run_and_validate(fn) == Array(10, 20, 30)


def test_dict_get_present_and_modify_large_size_string_key():
    def fn():
        v1 = Box(1)
        v2 = Box(2)
        d = {
            "a": v1,
            "z": v2,
            "b": v1,
            "y": v2,
            "c": v1,
            "x": v2,
            "d": v1,
            "w": v2,
            "e": v1,
            "v": v2,
            "f": v1,
            "u": v2,
            "g": v1,
            "t": v2,
            "h": v1,
            "s": v2,
            "i": v1,
            "r": v2,
            "j": v1,
            "q": v2,
            "k": v1,
            "p": v2,
            "l": v1,
            "o": v2,
            "m": v1,
            "n": v2,
        }
        d["a"].value = 10
        d["z"].value = 20
        return Array(v1.value, v2.value)

    assert run_and_validate(fn) == Array(10, 20)


def test_dict_get_present_and_modify_large_size_numeric_key():
    def fn():
        v1 = Box(1)
        v2 = Box(2)
        d = {
            20: v1,
            3: v1,
            15: v2,
            7: v1,
            25: v2,
            1: v1,
            18: v2,
            9: v1,
            22: v2,
            5: v1,
            12: v2,
            16: v1,
            8: v2,
            24: v1,
            2: v2,
            19: v1,
            11: v2,
            14: v1,
            6: v2,
            23: v1,
            4: v2,
            17: v1,
            13: v2,
            21: v1,
            10: v2,
        }
        d[1].value = 10
        d[10].value = 20
        return Array(v1.value, v2.value)

    assert run_and_validate(fn) == Array(10, 20)


def test_dict_get_present_and_modify_large_size_tuple_key():
    def fn():
        v1 = Box(1)
        v2 = Box(2)
        d = {
            (20, 20): v1,
            (3, 3): v1,
            (15, 15): v2,
            (7, 7): v1,
            (25, 25): v2,
            (1, 1): v1,
            (18, 18): v2,
            (9, 9): v1,
            (22, 22): v2,
            (5, 5): v1,
            (12, 12): v2,
            (16, 16): v1,
            (8, 8): v2,
            (24, 24): v1,
            (2, 2): v2,
            (19, 19): v1,
            (11, 11): v2,
            (14, 14): v1,
            (6, 6): v2,
            (23, 23): v1,
            (4, 4): v2,
            (17, 17): v1,
            (13, 13): v2,
            (21, 21): v1,
            (10, 10): v2,
        }
        d[1, 1].value = 10
        d[10, 10].value = 20
        return Array(v1.value, v2.value)

    assert run_and_validate(fn) == Array(10, 20)


def test_dict_contains_present_small_size_string_key():
    def fn():
        d = {"a": 1, "b": 2}
        return Array(
            "a" in d,
            "b" in d,
        )

    assert run_and_validate(fn) == Array(True, True)


def test_dict_contains_present_small_size_numeric_key():
    def fn():
        d = {1: 10, 2: 20}
        return Array(1 in d, 2 in d)

    assert run_and_validate(fn) == Array(True, True)


def test_dict_contains_present_small_size_tuple_key():
    def fn():
        d = {(1, 1): 10, (2, 2): 20}
        return Array((1, 1) in d, (2, 2) in d)

    assert run_and_validate(fn) == Array(True, True)


def test_dict_contains_present_small_size_mixed_key():
    def fn():
        d = {"a": 10, 2: 20, (3, 3): 30}
        return Array("a" in d, 2 in d, (3, 3) in d)

    assert run_and_validate(fn) == Array(True, True, True)


def test_dict_contains_present_large_size_string_key():
    d = {k: i * 10 for i, k in enumerate("azbycxdwevfugthsirjqkplomn", start=1)}

    def fn():
        return Array("a" in d, "m" in d, "z" in d)

    assert run_and_validate(fn) == Array(True, True, True)


def test_dict_contains_present_large_size_numeric_key():
    d = {k: k * 10 for k in [20, 3, 15, 7, 25, 1, 18, 9, 22, 5, 12, 16, 8, 24, 2, 19, 11, 14, 6, 23, 4, 17, 13, 21, 10]}

    def fn():
        return Array(1 in d, 13 in d, 25 in d)

    assert run_and_validate(fn) == Array(True, True, True)


def test_dict_contains_present_large_size_tuple_key():
    d = {
        (k, k): k * 10
        for k in [20, 3, 15, 7, 25, 1, 18, 9, 22, 5, 12, 16, 8, 24, 2, 19, 11, 14, 6, 23, 4, 17, 13, 21, 10]
    }

    def fn():
        return Array((1, 1) in d, (13, 13) in d, (25, 25) in d)

    assert run_and_validate(fn) == Array(True, True, True)


def test_dict_contains_present_large_size_mixed_key():
    d = {
        "a": 1,
        "b": 1,
        "c": 1,
        "d": 1,
        "e": 1,
        "f": 1,
        "g": 1,
        1: 2,
        2: 2,
        3: 2,
        4: 2,
        5: 2,
        6: 2,
        7: 2,
        (1, 1): 3,
        (2, 2): 3,
        (3, 3): 3,
        (4, 4): 3,
        (5, 5): 3,
        (6, 6): 3,
        (7, 7): 3,
    }

    def fn():
        return Array("a" in d, 4 in d, (4, 4) in d)

    assert run_and_validate(fn) == Array(True, True, True)


def test_dict_contains_absent_small_size_string_key():
    def fn():
        d = {"a": 1, "b": 2}
        return Array("c" in d, "d" in d)

    assert run_and_validate(fn) == Array(False, False)


def test_dict_contains_absent_small_size_numeric_key():
    def fn():
        d = {1: 10, 2: 20}
        return Array(3 in d, 4 in d)

    assert run_and_validate(fn) == Array(False, False)


def test_dict_contains_absent_small_size_tuple_key():
    def fn():
        d = {(1, 1): 10, (2, 2): 20}
        return Array((3, 3) in d, (4, 4) in d)

    assert run_and_validate(fn) == Array(False, False)


def test_dict_contains_absent_small_size_mixed_key():
    def fn():
        d = {"a": 10, 2: 20, (3, 3): 30}
        return Array("b" in d, 3 in d, (4, 4) in d)

    assert run_and_validate(fn) == Array(False, False, False)


def test_dict_contains_absent_large_size_string_key():
    d = {k: i * 10 for i, k in enumerate("azbycxdwevfugthsirjqkplomn", start=1)}

    def fn():
        return Array("A" in d, "Z" in d, "1" in d)

    assert run_and_validate(fn) == Array(False, False, False)


def test_dict_contains_absent_large_size_numeric_key():
    d = {k: k * 10 for k in [20, 3, 15, 7, 25, 1, 18, 9, 22, 5, 12, 16, 8, 24, 2, 19, 11, 14, 6, 23, 4, 17, 13, 21, 10]}

    def fn():
        return Array(0 in d, 26 in d, -1 in d)

    assert run_and_validate(fn) == Array(False, False, False)


def test_dict_contains_absent_large_size_tuple_key():
    d = {
        (k, k): k * 10
        for k in [20, 3, 15, 7, 25, 1, 18, 9, 22, 5, 12, 16, 8, 24, 2, 19, 11, 14, 6, 23, 4, 17, 13, 21, 10]
    }

    def fn():
        return Array((0, 0) in d, (26, 26) in d, (1, 2) in d)

    assert run_and_validate(fn) == Array(False, False, False)


def test_dict_contains_absent_large_size_mixed_key():
    d = {
        "a": 1,
        "b": 1,
        "c": 1,
        "d": 1,
        "e": 1,
        "f": 1,
        "g": 1,
        1: 2,
        2: 2,
        3: 2,
        4: 2,
        5: 2,
        6: 2,
        7: 2,
        (1, 1): 3,
        (2, 2): 3,
        (3, 3): 3,
        (4, 4): 3,
        (5, 5): 3,
        (6, 6): 3,
        (7, 7): 3,
    }

    def fn():
        return Array("h" in d, 8 in d, (8, 8) in d)

    assert run_and_validate(fn) == Array(False, False, False)


def test_dict_union_disjoint_small_size_string_key():
    def fn():
        d1 = {"a": 1}
        d2 = {"b": 2}
        d3 = d1 | d2
        return Array(d3["a"], d3["b"])

    assert run_and_validate(fn) == Array(1, 2)


def test_dict_union_disjoint_small_size_numeric_key():
    def fn():
        d1 = {1: 10}
        d2 = {2: 20}
        d3 = d1 | d2
        return Array(d3[1], d3[2])

    assert run_and_validate(fn) == Array(10, 20)


def test_dict_union_disjoint_small_size_tuple_key():
    def fn():
        d1 = {(1, 1): 10}
        d2 = {(2, 2): 20}
        d3 = d1 | d2
        return Array(d3[1, 1], d3[2, 2])

    assert run_and_validate(fn) == Array(10, 20)


def test_dict_union_disjoint_small_size_mixed_key():
    def fn():
        d1 = {"a": 10, 2: 20}
        d2 = {(3, 3): 30}
        d3 = d1 | d2
        return Array(d3["a"], d3[2], d3[3, 3])

    assert run_and_validate(fn) == Array(10, 20, 30)


def test_dict_union_disjoint_large_size_string_key():
    d1 = {k: i * 10 for i, k in enumerate("azbycxdwevfugthsirjqkplomn", start=1)}
    d2 = {k: i * 10 for i, k in enumerate("AZBYCXDWEVFUGTHSIRJQKPLOMN", start=1)}

    def fn():
        d3 = d1 | d2
        return Array(d3["a"], d3["z"], d3["A"], d3["Z"])

    assert run_and_validate(fn) == Array(10, 20, 10, 20)


def test_dict_union_disjoint_large_size_numeric_key():
    d1 = {k: k for k in [20, 3, 15, 7, 25, 1, 18, 9, 22, 5, 12, 16, 8, 24, 2, 19, 11, 14, 6, 23, 4, 17, 13, 21, 10]}
    d2 = {
        k: k
        for k in [45, 28, 40, 32, 50, 26, 43, 34, 47, 30, 37, 41, 33, 49, 27, 44, 36, 39, 31, 48, 29, 42, 38, 46, 35]
    }

    def fn():
        d3 = d1 | d2
        return Array(d3[1], d3[25], d3[26], d3[50])

    assert run_and_validate(fn) == Array(1, 25, 26, 50)


def test_dict_union_disjoint_large_size_tuple_key():
    d1 = {
        (k, k): k for k in [20, 3, 15, 7, 25, 1, 18, 9, 22, 5, 12, 16, 8, 24, 2, 19, 11, 14, 6, 23, 4, 17, 13, 21, 10]
    }
    d2 = {
        (k, k): k
        for k in [45, 28, 40, 32, 50, 26, 43, 34, 47, 30, 37, 41, 33, 49, 27, 44, 36, 39, 31, 48, 29, 42, 38, 46, 35]
    }

    def fn():
        d3 = d1 | d2
        return Array(d3[1, 1], d3[25, 25], d3[26, 26], d3[50, 50])

    assert run_and_validate(fn) == Array(1, 25, 26, 50)


def test_dict_union_disjoint_large_size_mixed_key():
    d1 = {
        "a": 1,
        "b": 2,
        "c": 3,
        "d": 4,
        "e": 5,
        "f": 6,
        "g": 7,
        1: 8,
        2: 9,
        3: 10,
        4: 11,
        5: 12,
        6: 13,
        7: 14,
        (1, 1): 15,
        (2, 2): 16,
        (3, 3): 17,
        (4, 4): 18,
        (5, 5): 19,
        (6, 6): 20,
        (7, 7): 21,
    }
    d2 = {
        "h": 22,
        "i": 23,
        "j": 24,
        "k": 25,
        "l": 26,
        "m": 27,
        "n": 28,
        8: 29,
        9: 30,
        10: 31,
        11: 32,
        12: 33,
        13: 34,
        14: 35,
        (8, 8): 36,
        (9, 9): 37,
        (10, 10): 38,
        (11, 11): 39,
        (12, 12): 40,
        (13, 13): 41,
        (14, 14): 42,
    }

    def fn():
        d3 = d1 | d2
        return Array(d3["a"], d3[4], d3[4, 4], d3["n"], d3[14], d3[14, 14])

    assert run_and_validate(fn) == Array(1, 11, 18, 28, 35, 42)


def test_dict_union_overlapping_small_size_string_key():
    def fn():
        d1 = {"a": 1, "b": 2}
        d2 = {"b": 3, "c": 4}
        d3 = d1 | d2
        return Array(d3["a"], d3["b"], d3["c"])

    assert run_and_validate(fn) == Array(1, 3, 4)


def test_dict_union_overlapping_small_size_numeric_key():
    def fn():
        d1 = {1: 10, 2: 20}
        d2 = {2: 30, 3: 40}
        d3 = d1 | d2
        return Array(d3[1], d3[2], d3[3])

    assert run_and_validate(fn) == Array(10, 30, 40)


def test_dict_union_overlapping_small_size_tuple_key():
    def fn():
        d1 = {(1, 1): 10, (2, 2): 20}
        d2 = {(2, 2): 30, (3, 3): 40}
        d3 = d1 | d2
        return Array(d3[1, 1], d3[2, 2], d3[3, 3])

    assert run_and_validate(fn) == Array(10, 30, 40)


def test_dict_union_overlapping_small_size_mixed_key():
    def fn():
        d1 = {"a": 10, 2: 20, (3, 3): 30}
        d2 = {"a": 40, 5: 50, (3, 3): 60}
        d3 = d1 | d2
        return Array(d3["a"], d3[2], d3[3, 3], d3[5])

    assert run_and_validate(fn) == Array(40, 20, 60, 50)


def test_dict_union_overlapping_large_size_string_key():
    d1 = {k: i * 10 for i, k in enumerate("azbycxdwevfugthsirjqkplomn", start=1)}
    d2 = {
        "a": 100,
        "z": 200,
        "b": 110,
        "y": 210,
        "c": 120,
        "x": 220,
        "aa": 1000,
        "bb": 1010,
        "cc": 1020,
        "dd": 1030,
        "ee": 1040,
        "ff": 1050,
        "gg": 1060,
        "hh": 1070,
        "ii": 1080,
        "jj": 1090,
        "kk": 1100,
        "ll": 1110,
        "mm": 1120,
        "nn": 1130,
        "oo": 1140,
        "pp": 1150,
        "qq": 1160,
        "rr": 1170,
        "ss": 1180,
        "tt": 1190,
    }

    def fn():
        d3 = d1 | d2
        return Array(d3["a"], d3["z"], d3["b"], d3["m"], d3["aa"], d3["tt"])

    assert run_and_validate(fn) == Array(100, 200, 110, 250, 1000, 1190)


def test_dict_union_overlapping_large_size_numeric_key():
    d1 = {k: k for k in [20, 3, 15, 7, 25, 1, 18, 9, 22, 5, 12, 16, 8, 24, 2, 19, 11, 14, 6, 23, 4, 17, 13, 21, 10]}
    d2 = {
        k: k * 100
        for k in [35, 18, 30, 22, 11, 27, 33, 15, 25, 20, 26, 31, 13, 28, 17, 34, 24, 29, 12, 32, 14, 16, 19, 23, 21]
    }

    def fn():
        d3 = d1 | d2
        return Array(d3[1], d3[10], d3[11], d3[25], d3[26], d3[35])

    assert run_and_validate(fn) == Array(1, 10, 1100, 2500, 2600, 3500)


def test_dict_union_overlapping_large_size_tuple_key():
    d1 = {
        (k, k): k for k in [20, 3, 15, 7, 25, 1, 18, 9, 22, 5, 12, 16, 8, 24, 2, 19, 11, 14, 6, 23, 4, 17, 13, 21, 10]
    }
    d2 = {
        (k, k): k * 100
        for k in [35, 18, 30, 22, 11, 27, 33, 15, 25, 20, 26, 31, 13, 28, 17, 34, 24, 29, 12, 32, 14, 16, 19, 23, 21]
    }

    def fn():
        d3 = d1 | d2
        return Array(d3[1, 1], d3[10, 10], d3[11, 11], d3[25, 25], d3[26, 26], d3[35, 35])

    assert run_and_validate(fn) == Array(1, 10, 1100, 2500, 2600, 3500)


def test_dict_union_overlapping_large_size_mixed_key():
    d1 = {
        "a": 1,
        "b": 2,
        "c": 3,
        "d": 4,
        "e": 5,
        "f": 6,
        "g": 7,
        1: 8,
        2: 9,
        3: 10,
        4: 11,
        5: 12,
        6: 13,
        7: 14,
        (1, 1): 15,
        (2, 2): 16,
        (3, 3): 17,
        (4, 4): 18,
        (5, 5): 19,
        (6, 6): 20,
        (7, 7): 21,
    }
    d2 = {
        "a": 100,
        "b": 200,
        "c": 300,
        "d": 400,
        "e": 500,
        "f": 600,
        "g": 700,
        8: 800,
        9: 900,
        10: 1000,
        11: 1100,
        12: 1200,
        13: 1300,
        14: 1400,
        (8, 8): 1500,
        (9, 9): 1600,
        (10, 10): 1700,
        (11, 11): 1800,
        (12, 12): 1900,
        (13, 13): 2000,
        (14, 14): 2100,
    }

    def fn():
        d3 = d1 | d2
        return Array(d3["a"], d3[4], d3[4, 4], d3["g"], d3[14], d3[14, 14])

    assert run_and_validate(fn) == Array(100, 11, 18, 700, 1400, 2100)


def test_dict_union_disjoint_iter_small_size_string_key():
    def fn():
        d1 = {"a": 1}
        d2 = {"b": 2}
        d3 = d1 | d2
        results = +Array[int, 2]
        i = 0
        for k in d3:
            results[i] = d3[k]
            i += 1
        return results

    assert run_and_validate(fn) == Array(1, 2)


def test_dict_union_disjoint_iter_small_size_numeric_key():
    def fn():
        d1 = {1: 10}
        d2 = {2: 20}
        d3 = d1 | d2
        results = +Array[int, 2]
        i = 0
        for k in d3:
            results[i] = k
            i += 1
        return results

    assert run_and_validate(fn) == Array(1, 2)


def test_dict_union_disjoint_iter_small_size_tuple_key():
    def fn():
        d1 = {(1, 1): 10}
        d2 = {(2, 2): 20}
        d3 = d1 | d2
        results = +Array[int, 2]
        i = 0
        for k in d3:
            results[i] = d3[k]
            i += 1
        return results

    assert run_and_validate(fn) == Array(10, 20)


def test_dict_union_disjoint_iter_small_size_mixed_key():
    def fn():
        d1 = {"a": 10, 2: 20}
        d2 = {(3, 3): 30}
        d3 = d1 | d2
        results = +Array[int, 3]
        i = 0
        for k in d3:
            results[i] = d3[k]
            i += 1
        return results

    assert run_and_validate(fn) == Array(10, 20, 30)


def test_dict_union_disjoint_iter_large_size_string_key():
    d1 = {k: i * 10 for i, k in enumerate("azbycxdwevfugthsirjqkplomn", start=1)}
    d2 = {k: i * 10 for i, k in enumerate("AZBYCXDWEVFUGTHSIRJQKPLOMN", start=1)}

    def fn():
        d3 = d1 | d2
        results = +Array[int, 52]
        i = 0
        for k in d3:
            results[i] = d3[k]
            i += 1
        return results

    assert run_and_validate(fn) == Array(*(d1 | d2).values())


def test_dict_union_disjoint_iter_large_size_numeric_key():
    d1 = {k: k for k in [20, 3, 15, 7, 25, 1, 18, 9, 22, 5, 12, 16, 8, 24, 2, 19, 11, 14, 6, 23, 4, 17, 13, 21, 10]}
    d2 = {
        k: k
        for k in [45, 28, 40, 32, 50, 26, 43, 34, 47, 30, 37, 41, 33, 49, 27, 44, 36, 39, 31, 48, 29, 42, 38, 46, 35]
    }

    def fn():
        d3 = d1 | d2
        results = +Array[int, 50]
        i = 0
        for k in d3:
            results[i] = k
            i += 1
        return results

    assert run_and_validate(fn) == Array(*(d1 | d2).keys())


def test_dict_union_disjoint_iter_large_size_tuple_key():
    d1 = {
        (k, k): k for k in [20, 3, 15, 7, 25, 1, 18, 9, 22, 5, 12, 16, 8, 24, 2, 19, 11, 14, 6, 23, 4, 17, 13, 21, 10]
    }
    d2 = {
        (k, k): k
        for k in [45, 28, 40, 32, 50, 26, 43, 34, 47, 30, 37, 41, 33, 49, 27, 44, 36, 39, 31, 48, 29, 42, 38, 46, 35]
    }

    def fn():
        d3 = d1 | d2
        results = +Array[int, 50]
        i = 0
        for k in d3:
            results[i] = d3[k]
            i += 1
        return results

    assert run_and_validate(fn) == Array(*(d1 | d2).values())


def test_dict_union_disjoint_iter_large_size_mixed_key():
    d1 = {
        "a": 1,
        "b": 2,
        "c": 3,
        "d": 4,
        "e": 5,
        "f": 6,
        "g": 7,
        1: 8,
        2: 9,
        3: 10,
        4: 11,
        5: 12,
        6: 13,
        7: 14,
        (1, 1): 15,
        (2, 2): 16,
        (3, 3): 17,
        (4, 4): 18,
        (5, 5): 19,
        (6, 6): 20,
        (7, 7): 21,
    }
    d2 = {
        "h": 22,
        "i": 23,
        "j": 24,
        "k": 25,
        "l": 26,
        "m": 27,
        "n": 28,
        8: 29,
        9: 30,
        10: 31,
        11: 32,
        12: 33,
        13: 34,
        14: 35,
        (8, 8): 36,
        (9, 9): 37,
        (10, 10): 38,
        (11, 11): 39,
        (12, 12): 40,
        (13, 13): 41,
        (14, 14): 42,
    }

    def fn():
        d3 = d1 | d2
        results = +Array[int, 42]
        i = 0
        for k in d3:
            results[i] = d3[k]
            i += 1
        return results

    assert run_and_validate(fn) == Array(*(d1 | d2).values())


def test_dict_union_overlapping_iter_small_size_string_key():
    def fn():
        d1 = {"a": 1, "b": 2}
        d2 = {"b": 3, "c": 4}
        d3 = d1 | d2
        results = +Array[int, 3]
        i = 0
        for k in d3:
            results[i] = d3[k]
            i += 1
        return results

    assert run_and_validate(fn) == Array(1, 3, 4)


def test_dict_union_overlapping_iter_small_size_numeric_key():
    def fn():
        d1 = {1: 10, 2: 20}
        d2 = {2: 30, 3: 40}
        d3 = d1 | d2
        results = +Array[int, 3]
        i = 0
        for k in d3:
            results[i] = k
            i += 1
        return results

    assert run_and_validate(fn) == Array(1, 2, 3)


def test_dict_union_overlapping_iter_small_size_tuple_key():
    def fn():
        d1 = {(1, 1): 10, (2, 2): 20}
        d2 = {(2, 2): 30, (3, 3): 40}
        d3 = d1 | d2
        results = +Array[int, 3]
        i = 0
        for k in d3:
            results[i] = d3[k]
            i += 1
        return results

    assert run_and_validate(fn) == Array(10, 30, 40)


def test_dict_union_overlapping_iter_small_size_mixed_key():
    def fn():
        d1 = {"a": 10, 2: 20, (3, 3): 30}
        d2 = {"a": 40, 5: 50, (3, 3): 60}
        d3 = d1 | d2
        results = +Array[int, 4]
        i = 0
        for k in d3:
            results[i] = d3[k]
            i += 1
        return results

    assert run_and_validate(fn) == Array(40, 20, 60, 50)


def test_dict_union_overlapping_iter_large_size_string_key():
    d1 = {k: i * 10 for i, k in enumerate("azbycxdwevfugthsirjqkplomn", start=1)}
    d2 = {
        "a": 100,
        "z": 200,
        "b": 110,
        "y": 210,
        "c": 120,
        "x": 220,
        "aa": 1000,
        "bb": 1010,
        "cc": 1020,
        "dd": 1030,
        "ee": 1040,
        "ff": 1050,
        "gg": 1060,
        "hh": 1070,
        "ii": 1080,
        "jj": 1090,
        "kk": 1100,
        "ll": 1110,
        "mm": 1120,
        "nn": 1130,
        "oo": 1140,
        "pp": 1150,
        "qq": 1160,
        "rr": 1170,
        "ss": 1180,
        "tt": 1190,
    }

    def fn():
        d3 = d1 | d2
        results = +Array[int, 46]
        i = 0
        for k in d3:
            results[i] = d3[k]
            i += 1
        return results

    assert run_and_validate(fn) == Array(*(d1 | d2).values())


def test_dict_union_overlapping_iter_large_size_numeric_key():
    d1 = {k: k for k in [20, 3, 15, 7, 25, 1, 18, 9, 22, 5, 12, 16, 8, 24, 2, 19, 11, 14, 6, 23, 4, 17, 13, 21, 10]}
    d2 = {
        k: k * 100
        for k in [35, 18, 30, 22, 11, 27, 33, 15, 25, 20, 26, 31, 13, 28, 17, 34, 24, 29, 12, 32, 14, 16, 19, 23, 21]
    }

    def fn():
        d3 = d1 | d2
        results = +Array[int, 35]
        i = 0
        for k in d3:
            results[i] = d3[k]
            i += 1
        return results

    assert run_and_validate(fn) == Array(*(d1 | d2).values())


def test_dict_union_overlapping_iter_large_size_tuple_key():
    d1 = {
        (k, k): k for k in [20, 3, 15, 7, 25, 1, 18, 9, 22, 5, 12, 16, 8, 24, 2, 19, 11, 14, 6, 23, 4, 17, 13, 21, 10]
    }
    d2 = {
        (k, k): k * 100
        for k in [35, 18, 30, 22, 11, 27, 33, 15, 25, 20, 26, 31, 13, 28, 17, 34, 24, 29, 12, 32, 14, 16, 19, 23, 21]
    }

    def fn():
        d3 = d1 | d2
        results = +Array[int, 35]
        i = 0
        for k in d3:
            results[i] = d3[k]
            i += 1
        return results

    assert run_and_validate(fn) == Array(*(d1 | d2).values())


def test_dict_union_overlapping_iter_large_size_mixed_key():
    d1 = {
        "a": 1,
        "b": 2,
        "c": 3,
        "d": 4,
        "e": 5,
        "f": 6,
        "g": 7,
        1: 8,
        2: 9,
        3: 10,
        4: 11,
        5: 12,
        6: 13,
        7: 14,
        (1, 1): 15,
        (2, 2): 16,
        (3, 3): 17,
        (4, 4): 18,
        (5, 5): 19,
        (6, 6): 20,
        (7, 7): 21,
    }
    d2 = {
        "a": 100,
        "b": 200,
        "c": 300,
        "d": 400,
        "e": 500,
        "f": 600,
        "g": 700,
        8: 800,
        9: 900,
        10: 1000,
        11: 1100,
        12: 1200,
        13: 1300,
        14: 1400,
        (8, 8): 1500,
        (9, 9): 1600,
        (10, 10): 1700,
        (11, 11): 1800,
        (12, 12): 1900,
        (13, 13): 2000,
        (14, 14): 2100,
    }

    def fn():
        d3 = d1 | d2
        results = +Array[int, 35]
        i = 0
        for k in d3:
            results[i] = d3[k]
            i += 1
        return results

    assert run_and_validate(fn) == Array(*(d1 | d2).values())


def test_dict_iter_small_size_string_key():
    d = {"a": 10, "b": 20}

    def fn():
        results = +Array[int, 2]
        i = 0
        for k in d:
            results[i] = d[bb(k)]
            i += 1
        return results

    assert run_and_validate(fn) == Array(10, 20)


def test_dict_iter_small_size_numeric_key():
    d = {1: 10, 2: 20}

    def fn():
        results = +Array[int, 2]
        i = 0
        for k in d:
            results[i] = k
            i += 1
        return results

    assert run_and_validate(fn) == Array(1, 2)


def test_dict_iter_small_size_mixed_key():
    d = {"a": 10, 2: 20, (3, 3): 30}

    def fn():
        results = +Array[int, 3]
        i = 0
        for k in d:
            results[i] = d[bb(k)]
            i += 1
        return results

    assert run_and_validate(fn) == Array(10, 20, 30)


def test_dict_keys_iter_small_size_string_key():
    d = {"a": 10, "b": 20}

    def fn():
        results = +Array[int, 2]
        i = 0
        for k in d:
            results[i] = d[bb(k)]
            i += 1
        return results

    assert run_and_validate(fn) == Array(10, 20)


def test_dict_keys_iter_small_size_numeric_key():
    d = {1: 10, 2: 20}

    def fn():
        results = +Array[int, 2]
        i = 0
        for k in d:
            results[i] = k
            i += 1
        return results

    assert run_and_validate(fn) == Array(1, 2)


def test_dict_keys_contains_present_small_size_string_key():
    d = {"a": 10, "b": 20}

    def fn():
        return Array("a" in d, "b" in d)

    assert run_and_validate(fn) == Array(True, True)


def test_dict_keys_contains_absent_small_size_string_key():
    d = {"a": 10, "b": 20}

    def fn():
        return Array("c" in d, "d" in d)

    assert run_and_validate(fn) == Array(False, False)


def test_dict_keys_contains_present_small_size_numeric_key():
    d = {1: 10, 2: 20}

    def fn():
        return Array(1 in d, 2 in d)

    assert run_and_validate(fn) == Array(True, True)


def test_dict_keys_contains_absent_small_size_numeric_key():
    d = {1: 10, 2: 20}

    def fn():
        return Array(3 in d, 4 in d)

    assert run_and_validate(fn) == Array(False, False)


def test_dict_values_iter_small_size_string_key():
    d = {"a": 10, "b": 20}

    def fn():
        results = +Array[int, 2]
        i = 0
        for v in d.values():
            results[i] = v
            i += 1
        return results

    assert run_and_validate(fn) == Array(10, 20)


def test_dict_values_iter_small_size_numeric_key():
    d = {1: 10, 2: 20}

    def fn():
        results = +Array[int, 2]
        i = 0
        for v in d.values():
            results[i] = v
            i += 1
        return results

    assert run_and_validate(fn) == Array(10, 20)


def test_dict_values_contains_present_small_size_string_key():
    d = {"a": 10, "b": 20}

    def fn():
        return Array(10 in d.values(), 20 in d.values())

    assert run_and_validate(fn) == Array(True, True)


def test_dict_values_contains_absent_small_size_string_key():
    d = {"a": 10, "b": 20}

    def fn():
        return Array(30 in d.values(), 40 in d.values())

    assert run_and_validate(fn) == Array(False, False)


def test_dict_values_contains_present_small_size_numeric_key():
    d = {1: 10, 2: 20}

    def fn():
        return Array(10 in d.values(), 20 in d.values())

    assert run_and_validate(fn) == Array(True, True)


def test_dict_values_contains_absent_small_size_numeric_key():
    d = {1: 10, 2: 20}

    def fn():
        return Array(30 in d.values(), 40 in d.values())

    assert run_and_validate(fn) == Array(False, False)


def test_dict_items_iter_small_size_string_key():
    d = {"a": 10, "b": 20}

    def fn():
        results = +Array[int, 4]
        i = 0
        for k, v in d.items():
            results[i] = d[bb(k)]
            results[i + 1] = v
            i += 2
        return results

    assert run_and_validate(fn) == Array(10, 10, 20, 20)


def test_dict_items_iter_small_size_numeric_key():
    d = {1: 10, 2: 20}

    def fn():
        results = +Array[int, 4]
        i = 0
        for k, v in d.items():
            results[i] = k
            results[i + 1] = v
            i += 2
        return results

    assert run_and_validate(fn) == Array(1, 10, 2, 20)


def test_dict_items_contains_present_small_size_string_key():
    d = {"a": 10, "b": 20}

    def fn():
        return Array(("a", 10) in d.items(), ("b", 20) in d.items())

    assert run_and_validate(fn) == Array(True, True)


def test_dict_items_contains_absent_small_size_string_key():
    d = {"a": 10, "b": 20}

    def fn():
        return Array(("a", 20) in d.items(), ("c", 10) in d.items())

    assert run_and_validate(fn) == Array(False, False)


def test_dict_items_contains_present_small_size_numeric_key():
    d = {1: 10, 2: 20}

    def fn():
        return Array((1, 10) in d.items(), (2, 20) in d.items())

    assert run_and_validate(fn) == Array(True, True)


def test_dict_items_contains_absent_small_size_numeric_key():
    d = {1: 10, 2: 20}

    def fn():
        return Array((1, 20) in d.items(), (3, 10) in d.items())

    assert run_and_validate(fn) == Array(False, False)


def test_dict_iter_large_size_string_key():
    d = {k: i * 10 for i, k in enumerate("azbycxdwevfugthsirjqkplomn", start=1)}

    def fn():
        results = +Array[int, 26]
        i = 0
        for k in d:
            results[i] = d[bb(k)]
            i += 1
        return results

    assert run_and_validate(fn) == Array(*d.values())


def test_dict_iter_large_size_numeric_key():
    d = {k: k * 10 for k in [20, 3, 15, 7, 25, 1, 18, 9, 22, 5, 12, 16, 8, 24, 2, 19, 11, 14, 6, 23, 4, 17, 13, 21, 10]}

    def fn():
        results = +Array[int, 25]
        i = 0
        for k in d:
            results[i] = k
            i += 1
        return results

    assert run_and_validate(fn) == Array(*d.keys())


def test_dict_iter_large_size_mixed_key():
    d = {
        "a": 1,
        "b": 2,
        "c": 3,
        "d": 4,
        "e": 5,
        "f": 6,
        "g": 7,
        1: 8,
        2: 9,
        3: 10,
        4: 11,
        5: 12,
        6: 13,
        7: 14,
        (1, 1): 15,
        (2, 2): 16,
        (3, 3): 17,
        (4, 4): 18,
        (5, 5): 19,
        (6, 6): 20,
        (7, 7): 21,
    }

    def fn():
        results = +Array[int, 21]
        i = 0
        for k in d:
            results[i] = d[bb(k)]
            i += 1
        return results

    assert run_and_validate(fn) == Array(*d.values())


def test_dict_keys_iter_large_size_string_key():
    d = {k: i * 10 for i, k in enumerate("azbycxdwevfugthsirjqkplomn", start=1)}

    def fn():
        results = +Array[int, 26]
        i = 0
        for k in d:
            results[i] = d[bb(k)]
            i += 1
        return results

    assert run_and_validate(fn) == Array(*d.values())


def test_dict_keys_iter_large_size_numeric_key():
    d = {k: k * 10 for k in [20, 3, 15, 7, 25, 1, 18, 9, 22, 5, 12, 16, 8, 24, 2, 19, 11, 14, 6, 23, 4, 17, 13, 21, 10]}

    def fn():
        results = +Array[int, 25]
        i = 0
        for k in d:
            results[i] = k
            i += 1
        return results

    assert run_and_validate(fn) == Array(*d.keys())


def test_dict_keys_contains_present_large_size_string_key():
    d = {k: i * 10 for i, k in enumerate("azbycxdwevfugthsirjqkplomn", start=1)}

    def fn():
        return Array("a" in d, "m" in d, "n" in d)

    assert run_and_validate(fn) == Array(True, True, True)


def test_dict_keys_contains_absent_large_size_string_key():
    d = {k: i * 10 for i, k in enumerate("azbycxdwevfugthsirjqkplomn", start=1)}

    def fn():
        return Array("A" in d, "1" in d, "Z" in d)

    assert run_and_validate(fn) == Array(False, False, False)


def test_dict_keys_contains_present_large_size_numeric_key():
    d = {k: k * 10 for k in [20, 3, 15, 7, 25, 1, 18, 9, 22, 5, 12, 16, 8, 24, 2, 19, 11, 14, 6, 23, 4, 17, 13, 21, 10]}

    def fn():
        return Array(1 in d, 13 in d, 25 in d)

    assert run_and_validate(fn) == Array(True, True, True)


def test_dict_keys_contains_absent_large_size_numeric_key():
    d = {k: k * 10 for k in [20, 3, 15, 7, 25, 1, 18, 9, 22, 5, 12, 16, 8, 24, 2, 19, 11, 14, 6, 23, 4, 17, 13, 21, 10]}

    def fn():
        return Array(0 in d, 26 in d, 50 in d)

    assert run_and_validate(fn) == Array(False, False, False)


def test_dict_values_iter_large_size_string_key():
    d = {k: i * 10 for i, k in enumerate("azbycxdwevfugthsirjqkplomn", start=1)}

    def fn():
        results = +Array[int, 26]
        i = 0
        for v in d.values():
            results[i] = v
            i += 1
        return results

    assert run_and_validate(fn) == Array(*d.values())


def test_dict_values_iter_large_size_numeric_key():
    d = {k: k * 10 for k in [20, 3, 15, 7, 25, 1, 18, 9, 22, 5, 12, 16, 8, 24, 2, 19, 11, 14, 6, 23, 4, 17, 13, 21, 10]}

    def fn():
        results = +Array[int, 25]
        i = 0
        for v in d.values():
            results[i] = v
            i += 1
        return results

    assert run_and_validate(fn) == Array(*d.values())


def test_dict_values_contains_present_large_size_string_key():
    d = {k: i * 10 for i, k in enumerate("azbycxdwevfugthsirjqkplomn", start=1)}

    def fn():
        return Array(10 in d.values(), 130 in d.values(), 260 in d.values())

    assert run_and_validate(fn) == Array(True, True, True)


def test_dict_values_contains_absent_large_size_string_key():
    d = {k: i * 10 for i, k in enumerate("azbycxdwevfugthsirjqkplomn", start=1)}

    def fn():
        return Array(5 in d.values(), 265 in d.values(), 0 in d.values())

    assert run_and_validate(fn) == Array(False, False, False)


def test_dict_values_contains_present_large_size_numeric_key():
    d = {k: k * 10 for k in [20, 3, 15, 7, 25, 1, 18, 9, 22, 5, 12, 16, 8, 24, 2, 19, 11, 14, 6, 23, 4, 17, 13, 21, 10]}

    def fn():
        return Array(10 in d.values(), 130 in d.values(), 250 in d.values())

    assert run_and_validate(fn) == Array(True, True, True)


def test_dict_values_contains_absent_large_size_numeric_key():
    d = {k: k * 10 for k in [20, 3, 15, 7, 25, 1, 18, 9, 22, 5, 12, 16, 8, 24, 2, 19, 11, 14, 6, 23, 4, 17, 13, 21, 10]}

    def fn():
        return Array(5 in d.values(), 255 in d.values(), 0 in d.values())

    assert run_and_validate(fn) == Array(False, False, False)


def test_dict_items_iter_large_size_string_key():
    d = {k: i * 10 for i, k in enumerate("azbycxdwevfugthsirjqkplomn", start=1)}

    def fn():
        results = +Array[int, 52]
        i = 0
        for k, v in d.items():
            results[i] = d[bb(k)]
            results[i + 1] = v
            i += 2
        return results

    assert run_and_validate(fn) == Array(*[x for v in d.values() for x in (v, v)])


def test_dict_items_iter_large_size_numeric_key():
    d = {k: k * 10 for k in [20, 3, 15, 7, 25, 1, 18, 9, 22, 5, 12, 16, 8, 24, 2, 19, 11, 14, 6, 23, 4, 17, 13, 21, 10]}

    def fn():
        results = +Array[int, 50]
        i = 0
        for k, v in d.items():
            results[i] = k
            results[i + 1] = v
            i += 2
        return results

    assert run_and_validate(fn) == Array(*[x for k, v in d.items() for x in (k, v)])


def test_dict_items_contains_present_large_size_string_key():
    d = {k: i * 10 for i, k in enumerate("azbycxdwevfugthsirjqkplomn", start=1)}

    def fn():
        return Array(("a", 10) in d.items(), ("m", 250) in d.items(), ("n", 260) in d.items())

    assert run_and_validate(fn) == Array(True, True, True)


def test_dict_items_contains_absent_large_size_string_key():
    d = {k: i * 10 for i, k in enumerate("azbycxdwevfugthsirjqkplomn", start=1)}

    def fn():
        return Array(("a", 20) in d.items(), ("A", 10) in d.items(), ("m", 130) in d.items())

    assert run_and_validate(fn) == Array(False, False, False)


def test_dict_items_contains_present_large_size_numeric_key():
    d = {k: k * 10 for k in [20, 3, 15, 7, 25, 1, 18, 9, 22, 5, 12, 16, 8, 24, 2, 19, 11, 14, 6, 23, 4, 17, 13, 21, 10]}

    def fn():
        return Array((1, 10) in d.items(), (13, 130) in d.items(), (25, 250) in d.items())

    assert run_and_validate(fn) == Array(True, True, True)


def test_dict_items_contains_absent_large_size_numeric_key():
    d = {k: k * 10 for k in [20, 3, 15, 7, 25, 1, 18, 9, 22, 5, 12, 16, 8, 24, 2, 19, 11, 14, 6, 23, 4, 17, 13, 21, 10]}

    def fn():
        return Array((1, 20) in d.items(), (26, 260) in d.items(), (13, 150) in d.items())

    assert run_and_validate(fn) == Array(False, False, False)


# Dicts are actually used as sets in these cases, so these are here.
# These are less comprehensive since the implementation is already tested above.


def test_dict_set_contains_present_small_size_string_key():
    def fn():
        d = {"a", "b"}
        return Array(
            "a" in d,
            "b" in d,
        )

    assert run_and_validate(fn) == Array(True, True)


def test_dict_set_contains_present_large_size_string_key():
    def fn():
        d = {
            "a",
            "z",
            "b",
            "y",
            "c",
            "x",
            "d",
            "w",
            "e",
            "v",
            "f",
            "u",
            "g",
            "t",
            "h",
            "s",
            "i",
            "r",
            "j",
            "q",
            "k",
            "p",
            "l",
            "o",
            "m",
            "n",
        }
        return Array(
            "a" in d,
            "m" in d,
            "z" in d,
        )

    assert run_and_validate(fn) == Array(True, True, True)


def test_dict_set_contains_present_small_size_tuple_key():
    def fn():
        d = {(1, 1), (2, 2), (3, 3)}
        return Array(
            (1, 1) in d,
            (2, 2) in d,
            (3, 3) in d,
        )

    assert run_and_validate(fn) == Array(True, True, True)


def test_dict_set_contains_present_large_size_tuple_key():
    d = {(k, k) for k in [20, 3, 15, 7, 25, 1, 18, 9, 22, 5, 12, 16, 8, 24, 2, 19, 11, 14, 6, 23, 4, 17, 13, 21, 10]}

    def fn():
        return Array(
            (1, 1) in d,
            (13, 13) in d,
            (25, 25) in d,
        )

    assert run_and_validate(fn) == Array(True, True, True)


def test_dict_set_contains_present_small_size_mixed_key():
    def fn():
        d = {"a", 2, (3, 3)}
        return Array(
            "a" in d,
            2 in d,
            (3, 3) in d,
        )

    assert run_and_validate(fn) == Array(True, True, True)


def test_dict_set_contains_present_large_size_mixed_key():
    def fn():
        d = {
            "a",
            "b",
            "c",
            "d",
            "e",
            "f",
            "g",
            1,
            2,
            3,
            4,
            5,
            6,
            7,
            (1, 1),
            (2, 2),
            (3, 3),
            (4, 4),
            (5, 5),
            (6, 6),
            (7, 7),
        }
        return Array(
            "a" in d,
            4 in d,
            (4, 4) in d,
        )

    assert run_and_validate(fn) == Array(True, True, True)
