# ruff: file-ignore[import-private-name, unnecessary-map]

import re
from enum import Enum

import pytest
from hypothesis import given
from hypothesis import strategies as st

from sonolus.script.array import Array
from sonolus.script.containers import VarArray
from sonolus.script.debug import debug_log
from sonolus.script.internal.context import RuntimeChecks, ctx
from sonolus.script.internal.error import CompilationError
from sonolus.script.internal.impl import validate_value
from sonolus.script.internal.math_impls import _floor
from sonolus.script.internal.meta_fn import meta_fn
from sonolus.script.internal.random import _random
from sonolus.script.internal.range import range_or_tuple
from sonolus.script.internal.tuple_impl import TupleImpl
from sonolus.script.num import _is_num
from sonolus.script.record import Record
from tests.script.conftest import compile_fn, run_and_validate, run_compiled


@meta_fn
def bb(*x):
    """Black box: returns its argument as a value that is not known at compile time."""
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


ints = st.integers(min_value=-10, max_value=10)
floats = st.floats(min_value=-99999, max_value=99999, allow_nan=False, allow_infinity=False)


class NonComplementaryEquality(Record):
    value: int
    __hash__ = None

    def __eq__(self, other):
        return True

    def __ne__(self, other):
        return True

    def __lt__(self, other):
        return True

    def __gt__(self, other):
        return True


class NeverEqual(Record):
    value: int
    __hash__ = None

    def __eq__(self, other):
        return False


def test_tuple_destructure():
    def fn():
        t = (1, 2), (3, 4), 5
        (a, b), (c, d), e = t
        return Array(a, b, c, d, e)

    assert run_and_validate(fn) == Array(1, 2, 3, 4, 5)


def test_tuple_packing():
    def fn():
        t1 = 1, 2
        t2 = 3, 4
        (a, b), (c, d) = t1, t2
        return Array(a, b, c, d)

    assert run_and_validate(fn) == Array(1, 2, 3, 4)


def test_tuple_addition():
    def fn():
        t1 = (1, 2)
        t2 = (3, 4, 5)
        t3 = t1 + t2
        a, b, c, d, e = t3
        return Array(a, b, c, d, e)

    assert run_and_validate(fn) == Array(1, 2, 3, 4, 5)


def test_tuple_addition_empty():
    def fn():
        t1 = ()
        t2 = (1, 2, 3)
        a, b, c = t1 + t2
        d, e, f = t2 + t1
        return Array(a, b, c, d, e, f)

    assert run_and_validate(fn) == Array(1, 2, 3, 1, 2, 3)


def test_tuple_addition_heterogeneous():
    def fn():
        t1 = (1, (2, 3))
        t2 = ((4, 5), 6)
        t3 = t1 + t2
        a, (b, c), (d, e), f = t3
        return Array(a, b, c, d, e, f)

    assert run_and_validate(fn) == Array(1, 2, 3, 4, 5, 6)


def test_tuple_addition_chained():
    def fn():
        t = (1,) + (2, 3) + (4,) + (5, 6, 7)  # ruff: ignore[collection-literal-concatenation]
        a, b, c, d, e, f, g = t
        return Array(a, b, c, d, e, f, g)

    assert run_and_validate(fn) == Array(1, 2, 3, 4, 5, 6, 7)


def test_tuple_addition_with_runtime_values():
    def fn():
        x = 10
        y = 20
        t1 = (x, y)
        t2 = (x + 1, y + 1)
        a, b, c, d = t1 + t2
        return Array(a, b, c, d)

    assert run_and_validate(fn) == Array(10, 20, 11, 21)


def test_tuple_addition_iteration():
    def fn():
        results = VarArray[int, 5].new()
        for v in (1, 2) + (3, 4, 5):  # ruff: ignore[collection-literal-concatenation]
            results.append(v)
        return results

    assert list(run_and_validate(fn)) == [1, 2, 3, 4, 5]


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


def test_tuple_comparison_uses_element_equality_not_inequality():
    def fn():
        left = (NonComplementaryEquality(1),)
        right = (NonComplementaryEquality(2),)
        return Array(left == right, left != right, left < right, left <= right, left > right, left >= right)

    assert run_and_validate(fn) == Array(True, False, False, True, False, True)


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


def test_tuple_contains_basic():
    def fn():
        t = (1, 2, 3, 4, 5)
        return Array(
            1 in t,
            5 in t,
            0 in t,
            6 in t,
            3 in t,
        )

    assert tuple(run_and_validate(fn)) == (True, True, False, False, True)


def test_tuple_contains_empty():
    def fn():
        t = ()
        return Array(
            1 in t,
            0 in t,
        )

    assert tuple(run_and_validate(fn)) == (False, False)


def test_tuple_contains_single_element():
    def fn():
        t = (42,)
        return Array(
            42 in t,
            0 in t,
            1 in t,
        )

    assert tuple(run_and_validate(fn)) == (True, False, False)


def test_tuple_contains_duplicates():
    def fn():
        t = (1, 2, 2, 3, 3, 3)
        return Array(
            1 in t,
            2 in t,
            3 in t,
            4 in t,
        )

    assert tuple(run_and_validate(fn)) == (True, True, True, False)


def test_tuple_contains_heterogeneous():
    def fn():
        t = ((1, 2), (3, 4), 5)
        inner1 = (1, 2)
        inner2 = (3, 4)
        inner3 = (1, 3)
        return Array(
            5 in t,
            0 in t,
            inner1 in t,
            inner2 in t,
            inner3 in t,
        )

    assert tuple(run_and_validate(fn)) == (True, False, True, True, False)


def test_enumerate_tuple_with_start():
    # enumerate() over a compile-time tuple with an explicit start must offset indices by
    # start exactly once. The buggy impl passed the (Num) start to builtin enumerate (a
    # TypeError) and also double-counted it.
    def fn():
        t = (10, 20, 30)
        results = VarArray[int, 3].new()
        for i, v in enumerate(t, 5):
            results.append(i * 100 + v)
        return results

    assert list(run_and_validate(fn)) == [510, 620, 730]


# map() over compile-time tuples


def test_map_tuple_single():
    def fn():
        t = (1, 2, 3)
        return sum(map(lambda x: x * 2, t))

    assert run_and_validate(fn) == 12


def test_map_tuple_has_no_len():
    # As in plain Python, map() returns a lazy iterator rather than a sequence, so it has no len().
    def fn():
        t = (1, 2, 3, 4)
        return len(map(lambda x: x + 1, t))

    with pytest.raises(CompilationError, match=re.escape("has no len()")):
        compile_fn(fn)


def test_map_tuple_for_loop():
    def fn():
        t = (1, 2, 3)
        results = VarArray[int, 3].new()
        for v in map(lambda x: x * 10, t):
            results.append(v)
        return results

    assert list(run_and_validate(fn)) == [10, 20, 30]


def test_map_tuple_not_subscriptable():
    # As in plain Python, the result of map() is a lazy iterator and cannot be indexed.
    def fn():
        t = (5, 6, 7)
        m = map(lambda x: x - 1, t)
        return m[0]

    with pytest.raises(CompilationError, match="object is not subscriptable"):
        compile_fn(fn)


def test_map_tuple_empty():
    def fn():
        t = ()
        return sum(map(lambda x: x * 2, t))

    assert run_and_validate(fn) == 0


def test_map_tuple_with_runtime_values():
    # The element count is compile time even though the elements themselves are not.
    def fn():
        t = (bb(2), bb(4))
        return sum(map(lambda x: x * 3, t))

    assert run_and_validate(fn) == 18


def test_map_tuple_fn_emits_runtime_code():
    def fn():
        results = VarArray[int, 3].new()

        def push(x):
            results.append(x * 10)
            return x + 1

        t = (1, 2, 3)
        total = sum(map(push, t))
        return Array(total, results[0], results[1], results[2])

    assert run_and_validate(fn) == Array(9, 10, 20, 30)


def test_map_tuple_two_iterables():
    def fn():
        a = (1, 2, 3)
        b = (10, 20, 30)
        return sum(map(lambda x, y: x * y, a, b))

    assert run_and_validate(fn) == 140


def test_map_tuple_three_iterables():
    def fn():
        a = (1, 2)
        b = (3, 4)
        c = (5, 6)
        return sum(map(lambda x, y, z: x + y + z, a, b, c))

    assert run_and_validate(fn) == 21


def test_map_tuple_stops_at_shortest():
    def fn():
        a = (1, 2, 3, 4)
        b = (10, 20)
        return sum(map(lambda x, y: x * y, a, b))

    assert run_and_validate(fn) == 50


def test_map_tuple_of_tuples():
    def fn():
        t = ((1, 2), (3, 4))
        return sum(map(lambda p: p[0] + p[1], t))

    assert run_and_validate(fn) == 10


def test_map_mixing_tuple_and_array_raises():
    def fn():
        return sum(map(lambda x, y: x * y, (1, 2, 3), Array(4, 5, 6)))

    with pytest.raises(CompilationError, match=r"Cannot mix compile-time iterables .* with other types in map"):
        compile_fn(fn)


def test_map_array_still_works():
    def fn():
        return sum(map(lambda x: x * 2, Array(1, 2, 3)))

    assert run_and_validate(fn) == 12


def test_map_range_still_works():
    def fn():
        return sum(map(lambda x: x + 1, range(4)))

    assert run_and_validate(fn) == 10


def test_map_two_arrays_still_works():
    def fn():
        return sum(map(lambda x, y: x * y, Array(1, 2, 3), Array(4, 5, 6)))

    assert run_and_validate(fn) == 32


# filter() over compile-time tuples


def test_filter_tuple_comptime_predicate():
    def fn():
        t = (1, 2, 3, 4)
        return sum(filter(lambda x: x > 2, t))

    assert run_and_validate(fn) == 7


def test_filter_tuple_has_no_len():
    # As in plain Python, filter() returns a lazy iterator rather than a sequence, so it has no len().
    def fn():
        t = (1, 2, 3, 4, 5)
        return len(filter(lambda x: x % 2 == 0, t))

    with pytest.raises(CompilationError, match=re.escape("has no len()")):
        compile_fn(fn)


def test_filter_tuple_for_loop():
    def fn():
        t = (1, 2, 3, 4, 5)
        results = VarArray[int, 5].new()
        for v in filter(lambda x: x != 3, t):
            results.append(v)
        return results

    assert list(run_and_validate(fn)) == [1, 2, 4, 5]


def test_filter_tuple_none_predicate():
    def fn():
        t = (0, 1, 0, 2, 3)
        return sum(filter(None, t))

    assert run_and_validate(fn) == 6


def test_filter_tuple_empty_result():
    def fn():
        t = (1, 2, 3)
        return sum(filter(lambda x: x > 100, t))

    assert run_and_validate(fn) == 0


def test_filter_tuple_empty_input():
    def fn():
        t = ()
        return sum(filter(lambda x: x > 0, t))

    assert run_and_validate(fn) == 0


def test_filter_tuple_keeps_runtime_elements():
    # The predicate result is compile time even though the kept elements are not.
    def fn():
        t = ((bb(2), 1), (bb(4), 0))
        return sum(map(lambda p: p[0], filter(lambda p: p[1] == 1, t)))

    assert run_and_validate(fn) == 2


def test_filter_tuple_runtime_predicate():
    # The condition need not be a compile-time constant: filtering compiles to the generator protocol,
    # so whether an element survives can be a runtime branch.
    def fn():
        t = (1, 2, 3)
        return sum(filter(lambda x: x > bb(1), t))

    assert run_and_validate(fn) == 5


def test_filter_tuple_none_predicate_runtime_element():
    def fn():
        t = (bb(0), bb(1), bb(2))
        return sum(filter(None, t))

    assert run_and_validate(fn) == 3


def test_filter_tuple_non_num_predicate_result():
    # A non-Num condition goes through the ordinary truthiness protocol, exactly as an `if` would: an Array is
    # truthy when it is non-empty, so every element survives, matching plain Python.
    def fn():
        t = (1, 2, 3)
        return sum(filter(lambda x: Array(x, x), t))

    assert run_and_validate(fn) == 6


def test_filter_array_still_works():
    def fn():
        return sum(filter(lambda x: x > 1, Array(1, 2, 3)))

    assert run_and_validate(fn) == 5


def test_filter_array_none_predicate_still_works():
    def fn():
        return sum(filter(None, Array(0, 1, 2)))

    assert run_and_validate(fn) == 3


def test_filter_array_runtime_predicate_still_works():
    def fn():
        return sum(filter(lambda x: x > bb(1), Array(1, 2, 3)))

    assert run_and_validate(fn) == 5


def test_map_filter_tuple_composed():
    def fn():
        t = (1, 2, 3, 4, 5)
        return sum(map(lambda x: x * 2, filter(lambda x: x % 2 == 1, t)))

    assert run_and_validate(fn) == 18


def test_map_over_range_or_tuple():
    def fn():
        return sum(map(lambda x: x * 2, range_or_tuple(4)))

    assert run_and_validate(fn) == 12


def test_map_mixing_array_and_tuple_raises_either_order():
    # Reversed argument order relative to test_map_mixing_tuple_and_array_raises. Before the fix this leaked
    # zip's message ("... in zip") even though the user wrote map.
    def fn():
        return sum(map(lambda x, y: x * y, Array(4, 5, 6), (1, 2, 3)))

    with pytest.raises(CompilationError, match=r"Cannot mix compile-time iterables .* with other types in map"):
        compile_fn(fn)


def test_map_tuple_and_dict():
    # has_tuple_iter() is also true for dicts, so mixing a tuple with a dict is not "mixing" and iterates the keys.
    d = {1: 10, 2: 20, 3: 30}

    def fn():
        return sum(map(lambda x, y: x * y, (1, 2, 3), d))

    assert run_and_validate(fn) == 14


def test_map_dict_keys():
    d = {1: 10, 2: 20, 3: 30}

    def fn():
        return sum(map(lambda k: k * 2, d))

    assert run_and_validate(fn) == 12


def test_filter_dict_keys():
    d = {1: 10, 2: 20, 3: 30}

    def fn():
        return sum(filter(lambda k: k > 1, d))

    assert run_and_validate(fn) == 5


def test_filter_dict_keys_has_no_len():
    d = {1: 10, 2: 20, 3: 30}

    def fn():
        return len(filter(lambda k: k > 1, d))

    with pytest.raises(CompilationError, match=re.escape("has no len()")):
        compile_fn(fn)


# map() and filter() over a compile-time iterable must behave exactly like the equivalent generator expression.
#
# Both compile to the same generator protocol, so the mapped function and the filter condition run once per
# element actually consumed, interleaved with the consumer's body, and the condition may be a runtime value.


def _logged_double(x):
    debug_log(200 + x)
    return x * 2


def _logged_mul(x, y):
    debug_log(200 + x)
    return x * y


def _logged_is_odd(x):
    debug_log(200 + x)
    return x % 2 == 1


def _compiled_with_log(fn):
    """Compile and run fn, returning its result together with the debug_log entries it produced."""
    log = []
    result = run_compiled(fn, log_callback=log.append)
    return result, log


def _map_tuple_via_genexpr():
    total = 0
    for v in (_logged_double(x) for x in (1, 2, 3)):
        debug_log(300 + v)
        total += v
    return total


def _map_tuple_via_map():
    total = 0
    for v in map(_logged_double, (1, 2, 3)):
        debug_log(300 + v)
        total += v
    return total


def test_map_tuple_matches_genexpr():
    assert _compiled_with_log(_map_tuple_via_map) == _compiled_with_log(_map_tuple_via_genexpr)


def test_map_tuple_is_lazy_like_python():
    # run_and_validate also checks the debug_log order against plain Python's lazy map().
    assert run_and_validate(_map_tuple_via_map) == 12
    assert _compiled_with_log(_map_tuple_via_map) == (12, [201, 302, 202, 304, 203, 306])


def _map_two_tuples_via_genexpr():
    total = 0
    for v in (_logged_mul(x, y) for x, y in zip((1, 2, 3), (10, 20, 30))):  # ruff: ignore[zip-without-explicit-strict, reimplemented-starmap]
        debug_log(300 + v)
        total += v
    return total


def _map_two_tuples_via_map():
    total = 0
    for v in map(_logged_mul, (1, 2, 3), (10, 20, 30)):
        debug_log(300 + v)
        total += v
    return total


def test_map_two_tuples_matches_genexpr():
    assert _compiled_with_log(_map_two_tuples_via_map) == _compiled_with_log(_map_two_tuples_via_genexpr)


def test_map_two_tuples_is_lazy_like_python():
    assert run_and_validate(_map_two_tuples_via_map) == 140


def _filter_tuple_via_genexpr():
    total = 0
    for v in (x for x in (1, 2, 3) if _logged_is_odd(x)):
        debug_log(300 + v)
        total += v
    return total


def _filter_tuple_via_filter():
    total = 0
    for v in filter(_logged_is_odd, (1, 2, 3)):
        debug_log(300 + v)
        total += v
    return total


def test_filter_tuple_matches_genexpr():
    assert _compiled_with_log(_filter_tuple_via_filter) == _compiled_with_log(_filter_tuple_via_genexpr)


def test_filter_tuple_is_lazy_like_python():
    assert run_and_validate(_filter_tuple_via_filter) == 4
    assert _compiled_with_log(_filter_tuple_via_filter) == (4, [201, 301, 202, 203, 303])


def _filter_tuple_dynamic_via_genexpr():
    n = bb(1)
    total = 0
    for v in (x for x in (1, 2, 3) if x > n):
        debug_log(300 + v)
        total += v
    return total


def _filter_tuple_dynamic_via_filter():
    n = bb(1)
    total = 0
    for v in filter(lambda x: x > n, (1, 2, 3)):
        debug_log(300 + v)
        total += v
    return total


def test_filter_tuple_dynamic_condition_matches_genexpr():
    assert _compiled_with_log(_filter_tuple_dynamic_via_filter) == _compiled_with_log(_filter_tuple_dynamic_via_genexpr)


def test_filter_tuple_dynamic_condition_is_lazy_like_python():
    assert run_and_validate(_filter_tuple_dynamic_via_filter) == 5
    assert _compiled_with_log(_filter_tuple_dynamic_via_filter) == (5, [302, 303])


def _filter_dict_dynamic_via_genexpr():
    d = {1: 10, 2: 20, 3: 30}
    n = bb(1)
    total = 0
    for k in (k for k in d if k > n):
        debug_log(300 + k)
        total += k
    return total


def _filter_dict_dynamic_via_filter():
    d = {1: 10, 2: 20, 3: 30}
    n = bb(1)
    total = 0
    for k in filter(lambda k: k > n, d):
        debug_log(300 + k)
        total += k
    return total


def test_filter_dict_dynamic_condition_matches_genexpr():
    assert _compiled_with_log(_filter_dict_dynamic_via_filter) == _compiled_with_log(_filter_dict_dynamic_via_genexpr)


def _filter_set_dynamic_via_filter():
    n = bb(1)
    total = 0
    for v in filter(lambda v: v > n, {1, 2, 3}):
        total += v
    return total


def test_filter_set_dynamic_condition():
    assert run_and_validate(_filter_set_dynamic_via_filter) == 5


def _map_filter_composed_via_genexpr():
    total = 0
    for v in (_logged_double(x) for x in (x for x in (1, 2, 3, 4) if _logged_is_odd(x))):
        debug_log(300 + v)
        total += v
    return total


def _map_filter_composed_via_map_filter():
    total = 0
    for v in map(_logged_double, filter(_logged_is_odd, (1, 2, 3, 4))):
        debug_log(300 + v)
        total += v
    return total


def test_map_over_filter_matches_genexpr():
    assert _compiled_with_log(_map_filter_composed_via_map_filter) == _compiled_with_log(
        _map_filter_composed_via_genexpr
    )


def test_map_over_filter_is_lazy_like_python():
    assert run_and_validate(_map_filter_composed_via_map_filter) == 8


def _map_array_via_genexpr():
    total = 0
    for v in (_logged_double(x) for x in Array(1, 2, 3)):
        debug_log(300 + v)
        total += v
    return total


def _map_array_via_map():
    total = 0
    for v in map(_logged_double, Array(1, 2, 3)):
        debug_log(300 + v)
        total += v
    return total


def test_map_array_matches_genexpr():
    # The runtime-iterator path was already lazy; check that the two agree there too.
    assert _compiled_with_log(_map_array_via_map) == _compiled_with_log(_map_array_via_genexpr)
    assert run_and_validate(_map_array_via_map) == 12


def _map_tuple_break_via_genexpr():
    total = 0
    for v in (_logged_double(x) for x in (1, 2, 3)):
        debug_log(300 + v)
        total += v
        if v >= 4:
            break
    return total


def _map_tuple_break_via_map():
    total = 0
    for v in map(_logged_double, (1, 2, 3)):
        debug_log(300 + v)
        total += v
        if v >= 4:
            break
    return total


def test_map_tuple_break_matches_genexpr():
    # The payoff of laziness: the third element is never mapped, because it is never consumed.
    assert _compiled_with_log(_map_tuple_break_via_map) == _compiled_with_log(_map_tuple_break_via_genexpr)


def test_map_tuple_break_is_lazy_like_python():
    assert run_and_validate(_map_tuple_break_via_map) == 6
    assert _compiled_with_log(_map_tuple_break_via_map) == (6, [201, 302, 202, 304])


def test_map_tuple_is_not_reversible():
    # As in plain Python, a lazy map() iterator is not reversible.
    def fn():
        return sum(reversed(map(lambda x: x, (1, 2, 3))))

    with pytest.raises(CompilationError, match="not reversible"):
        compile_fn(fn)


class _Color(Enum):
    RED = 1
    GREEN = 2
    BLUE = 3


def test_map_enum_class():
    # An enum class is a compile-time iterable too, and its members compile to their values.
    def fn():
        return sum(map(lambda c: c * 10, _Color))

    assert run_compiled(fn) == 60


def test_filter_enum_class_dynamic_condition():
    def fn():
        n = bb(1)
        return sum(filter(lambda c: c > n, _Color))

    assert run_compiled(fn) == 5


def test_tuple_unpack_too_many_values_counts_both_sides():
    def fn():
        a, b = 1, 2, 3
        return a + b

    # run_compiled with an explicit match rather than run_and_validate: run_and_validate needs exact message
    # parity with CPython, and CPython prints the got-count only for a sequence source, and only from 3.14 on.
    with pytest.raises(CompilationError, match=re.escape("too many values to unpack (expected 2, got 3)")):
        run_compiled(fn)


def test_enum_class_unpack_too_many_values_counts_both_sides():
    def fn():
        a, b = _Color
        return a + b

    # run_compiled for a stronger reason than above: an enum class is not a sequence, so CPython takes its
    # generic-iterable path and omits the got-count on every version, 3.14 included.
    with pytest.raises(CompilationError, match=re.escape("too many values to unpack (expected 2, got 3)")):
        run_compiled(fn)


def test_tuple_unpack_not_enough_values_counts_both_sides():
    def fn():
        a, b, c = 1, 2
        return a + b + c

    with pytest.raises(ValueError, match=re.escape("not enough values to unpack (expected 3, got 2)")):
        run_and_validate(fn)


def test_tuple_index_present():
    def fn():
        t = (10, 20, 30)
        return t.index(20)

    assert run_and_validate(fn) == (10, 20, 30).index(20)


def test_tuple_index_first_occurrence():
    def fn():
        t = (1, 2, 3, 2, 1)
        return t.index(2)

    assert run_and_validate(fn) == (1, 2, 3, 2, 1).index(2)


# The match= patterns in the missing-value tests below document parity with CPython, which raises this exact
# message. They do not pin the emitted message: run_and_validate runs the plain-Python leg first and re-raises
# its exception, so the compiled string never reaches pytest.raises.
@given(t_list=st.lists(ints, min_size=1, max_size=8), value=ints)
def test_tuple_index_matches_python(t_list, value):
    t = tuple(t_list)

    def fn():
        return t.index(value)

    if value in t:
        assert run_and_validate(fn) == t.index(value)
    else:
        with pytest.raises(ValueError, match=re.escape("tuple.index(x): x not in tuple")):
            run_and_validate(fn)


def test_tuple_index_runtime_elements():
    # The comparisons are runtime, so each unrolled candidate index is returned out of a runtime branch.
    def fn():
        t = (bb(10), bb(20), bb(30))
        return t.index(bb(20))

    assert run_and_validate(fn) == 1


def test_tuple_index_missing_terminates():
    def fn():
        t = (10, 20, 30)
        return t.index(40)

    with pytest.raises(ValueError, match=re.escape("tuple.index(x): x not in tuple")):
        run_and_validate(fn)


def test_tuple_index_missing_runtime_elements_terminates():
    def fn():
        t = (bb(10), bb(20), bb(30))
        return t.index(bb(40))

    with pytest.raises(ValueError, match=re.escape("tuple.index(x): x not in tuple")):
        run_and_validate(fn)


def test_tuple_count_runtime_values():
    def fn():
        return (bb(1), bb(2), bb(1), bb(3)).count(bb(1))

    assert run_and_validate(fn) == 2


def test_tuple_count_missing_value():
    def fn():
        return (bb(1), bb(2), bb(3)).count(bb(4))

    assert run_and_validate(fn) == 0


def test_tuple_count_nested_tuple():
    def fn():
        return ((bb(1), bb(2)), (bb(3), bb(4)), (bb(1), bb(2))).count((bb(1), bb(2)))

    assert run_and_validate(fn) == 2


def test_tuple_count_does_not_assume_object_identity():
    # run_compiled is intentional: traced object identity is unsupported, so tuple.count may call equality where
    # Python would take its identity shortcut.
    def fn():
        value = NeverEqual(1)
        return (value,).count(value)

    assert run_compiled(fn) == 0


def test_tuple_index_empty_terminates():
    def fn():
        t = ()
        return t.index(1)

    with pytest.raises(ValueError, match=re.escape("tuple.index(x): x not in tuple")):
        run_and_validate(fn)


def test_tuple_index_of_nested_tuple():
    def fn():
        t = ((1, 2), (3, 4), (5, 6))
        return t.index((3, 4))

    assert run_and_validate(fn) == ((1, 2), (3, 4), (5, 6)).index((3, 4))


@given(
    t_list=st.lists(ints, min_size=1, max_size=8),
    value=ints,
    start=st.integers(-10, 10),
    stop=st.integers(-10, 10),
)
def test_tuple_index_bounded_matches_python(t_list, value, start, stop):
    t = tuple(t_list)

    def fn():
        return t.index(value, start, stop)

    try:
        expected = t.index(value, start, stop)
    except ValueError:
        with pytest.raises(ValueError, match=re.escape("tuple.index(x): x not in tuple")):
            run_and_validate(fn)
    else:
        assert run_and_validate(fn) == expected


def test_tuple_index_with_runtime_start():
    def fn():
        t = (1, 2, 1, 2)
        return t.index(2, bb(2))

    assert run_and_validate(fn) == (1, 2, 1, 2).index(2, 2)


@pytest.mark.parametrize(("start", "stop"), [(1.5, None), (-1.5, None), (0, 1.5), (0, -1.5)])
def test_tuple_index_rejects_fractional_bounds(start, stop):
    def fn(start_value, stop_value):
        if stop_value is None:
            return (1, 2, 3).index(2, bb(start_value))
        return (1, 2, 3).index(2, bb(start_value), bb(stop_value))

    with pytest.raises(TypeError):
        run_and_validate(fn, start, stop)


def test_tuple_index_fractional_bound_in_dead_runtime_branch_compiles():
    def fn(take_branch):
        if bb(take_branch):
            return (1, 2, 3).index(2, 1.5)
        return 42

    assert run_and_validate(fn, False) == 42


def test_tuple_index_bounds_are_positional_only():
    def fn():
        return (1, 2, 3).index(2, start=1)

    # run_compiled checks the compiler's binding diagnostic rather than CPython's builtin-specific message.
    with pytest.raises(CompilationError, match="got some positional-only arguments passed as keyword arguments"):
        run_compiled(fn)


# These two pin an accepted divergence from Python, the same shape as test_range.py's
# test_range_index_runtime_checks_disabled_unchanged: the miss is reported by a runtime check, so with runtime
# checks disabled it is not reported at all and -1 surfaces instead.
def test_tuple_index_runtime_checks_disabled_returns_minus_one():
    def fn():
        t = (10, 20, 30)
        return t.index(40)

    assert run_compiled(fn, runtime_checks=RuntimeChecks.NONE) == -1


def test_tuple_index_runtime_elements_runtime_checks_disabled_returns_minus_one():
    def fn():
        t = (bb(10), bb(20), bb(30))
        return t.index(bb(40))

    assert run_compiled(fn, runtime_checks=RuntimeChecks.NONE) == -1
