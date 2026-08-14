# ruff: file-ignore[enumerate-for-loop, import-private-name, unnecessary-literal-set]

import pytest

from sonolus.script.array import Array
from sonolus.script.debug import debug_log
from sonolus.script.internal.context import ctx
from sonolus.script.internal.error import CompilationError
from sonolus.script.internal.impl import validate_value
from sonolus.script.internal.math_impls import _floor
from sonolus.script.internal.meta_fn import meta_fn
from sonolus.script.internal.random import _random
from sonolus.script.internal.set_impl import SetImpl
from sonolus.script.internal.tuple_impl import TupleImpl
from sonolus.script.num import _is_num
from sonolus.script.record import Record
from tests.script.conftest import run_and_validate, run_compiled


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


def logged_set_member(value):
    debug_log(value)
    return value


def logged_set_members(value):
    debug_log(value)
    return (value, value + 1)


class UnhashableCompiledMember(Record):  # noqa: PLW1641
    value: int

    def __eq__(self, other):
        return isinstance(other, UnhashableCompiledMember) and self.value == other.value


UNHASHABLE_COMPILED_MEMBER = UnhashableCompiledMember(1)
EQUAL_UNHASHABLE_COMPILED_MEMBER = UnhashableCompiledMember(1)


def test_set_literal_starred_tuple_unpacking():
    def fn():
        s = {0, *(1, 2), 2, *(3,)}
        return Array(len(s), 0 in s, 1 in s, 2 in s, 3 in s, 4 in s)

    assert run_and_validate(fn) == Array(4, True, True, True, True, False)


def test_set_literal_starred_mapping_unpacks_keys():
    def fn():
        s = {*{"a": 1, "b": 2}}
        return Array(len(s), "a" in s, "b" in s, 1 in s)

    assert run_and_validate(fn) == Array(2, True, True, False)


def test_set_literal_starred_entries_preserve_evaluation_order():
    def fn():
        s = {
            logged_set_member(10),
            *logged_set_members(20),
            logged_set_member(30),
            *logged_set_members(40),
        }
        return len(s)

    assert run_and_validate(fn) == 6


# __contains__


def test_set_accepts_unhashable_compile_time_record_member():
    # run_compiled is intentional: compiled sets use equality-based lookup and do not require Python hashability.
    def fn():
        values = {UNHASHABLE_COMPILED_MEMBER, EQUAL_UNHASHABLE_COMPILED_MEMBER}
        return len(values) * 10 + (EQUAL_UNHASHABLE_COMPILED_MEMBER in values)

    assert run_compiled(fn) == 11


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


# Runtime members are rejected with the documented rule. run_compiled because the host leg builds these
# sets happily: only the compiled leg rejects them.


def test_set_literal_with_runtime_element_reports_the_membership_rule():
    def fn():
        s = {bb(1.0), 2.0}
        return 1.0 in s

    with pytest.raises(CompilationError, match="Set members must be compile time constants"):
        run_compiled(fn)


def test_set_builtin_with_runtime_element_reports_the_membership_rule():
    def fn():
        s = set((bb(1.0), 2.0))
        return 1.0 in s

    with pytest.raises(CompilationError, match="Set members must be compile time constants"):
        run_compiled(fn)


def test_dict_literal_with_runtime_key_keeps_its_own_message():
    # Pinned beside the set messages so the two spellings stay distinct.
    def fn():
        d = {bb(1.0): 2.0}
        return 1.0 in d

    with pytest.raises(CompilationError, match="Dict keys must be compile time constants"):
        run_compiled(fn)


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


def test_set_empty():
    def fn():
        s = set()
        return len(s)

    assert run_and_validate(fn) == 0


def test_set_from_tuple():
    def fn():
        s = set((1, 2, 3))
        return Array(1 in s, 2 in s, 4 in s)

    assert run_and_validate(fn) == Array(True, True, False)


def test_set_from_string_tuple():
    def fn():
        s = set(("a", "b", "c"))
        return Array("a" in s, "b" in s, "d" in s)

    assert run_and_validate(fn) == Array(True, True, False)


def test_set_with_type_params():
    def fn():
        s = set[int]((1, 2, 3))
        return Array(1 in s, 2 in s, 4 in s)

    assert run_and_validate(fn) == Array(True, True, False)


def test_set_copy():
    def fn():
        s1 = {1, 2, 3}
        s2 = set(s1)
        return Array(1 in s2, 2 in s2, 4 in s2)

    assert run_and_validate(fn) == Array(True, True, False)


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
