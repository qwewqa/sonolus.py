from itertools import starmap

import pytest
from hypothesis import given
from hypothesis import strategies as st

from sonolus.script.array import Array
from sonolus.script.containers import ArrayMap, Pair, VarArray
from sonolus.script.debug import assert_false, assert_true
from sonolus.script.internal.context import RuntimeChecks
from tests.script.conftest import run_and_validate, run_compiled

ints = st.integers(min_value=-999, max_value=999)
maps = st.dictionaries(ints, ints, min_size=1, max_size=20)


def test_array_map_of_infers_types_and_capacity():
    def fn():
        return ArrayMap.of((2, 3), (4, 5))

    result = run_and_validate(fn)
    assert dict(result.items()) == {2: 3, 4: 5}
    assert type(result) is ArrayMap[int, int, 2]


def test_array_map_of_with_parameterized_types_and_spare_capacity():
    def fn():
        return ArrayMap[int, int, 4].of((2, 3), (4, 5))

    result = run_and_validate(fn)
    assert dict(result.items()) == {2: 3, 4: 5}
    assert type(result) is ArrayMap[int, int, 4]


def test_array_map_of_with_no_items_when_parameterized():
    def fn():
        return ArrayMap[int, int, 2].of()

    assert dict(run_and_validate(fn).items()) == {}


def test_array_map_of_rejects_too_many_items():
    def fn():
        return ArrayMap[int, int, 1].of((2, 3), (4, 5))

    with pytest.raises(ValueError, match="capacity 1, got 2 items"):
        run_and_validate(fn)


def test_array_map_of_rejects_wrong_key_or_value_type():
    def fn():
        return ArrayMap[int, int, 2].of((2, 3), (4, None))

    with pytest.raises(TypeError, match=r"Cannot accept value Const\[None\] as Num"):
        run_and_validate(fn)


def test_array_map_of_rejects_non_pair():
    def fn():
        return ArrayMap[int, int, 1].of((2, 3, 4))

    with pytest.raises(TypeError, match="two-item tuple"):
        run_and_validate(fn)


def test_array_map_of_requires_an_item_to_infer_types():
    def fn():
        return ArrayMap.of()

    with pytest.raises(ValueError, match="at least one item if types are not specified"):
        run_and_validate(fn)


def test_array_map_of_rejects_heterogeneous_inferred_key_types():
    def fn():
        return ArrayMap.of((2, 3), (None, 4))

    with pytest.raises(TypeError, match=r"keys of the same type, got Const\[None\], Num"):
        run_and_validate(fn)


def test_array_map_of_rejects_heterogeneous_inferred_value_types():
    def fn():
        return ArrayMap.of((2, 3), (4, None))

    with pytest.raises(TypeError, match=r"values of the same type, got Const\[None\], Num"):
        run_and_validate(fn)


def test_array_map_of_updates_duplicate_keys():
    def fn():
        return ArrayMap.of((2, 3), (2, 4))

    result = run_and_validate(fn)
    assert dict(result.items()) == {2: 4}
    assert type(result) is ArrayMap[int, int, 2]


def test_array_map_of_copies_keys_and_values():
    def fn():
        key = Pair(2, 3)
        value = Pair(4, 5)
        result = ArrayMap.of((key, value))
        key.first = 6
        value.first = 7
        return result[Pair(2, 3)].first

    assert run_and_validate(fn) == 4


@st.composite
def map_and_key(draw):
    values = draw(maps)
    key = draw(st.sampled_from(list(values.keys())))
    return values, key


@st.composite
def map_and_missing_key(draw):
    values = draw(maps)
    key = draw(ints.filter(lambda x: x not in values))
    return values, key


@given(map_and_key())
def test_insertion(args):
    values, key = args
    pairs = Array(*starmap(Pair, values.items()))
    count = len(values)

    def fn():
        am = ArrayMap[int, int, count].new()
        for pair in pairs:
            am[pair.first] = pair.second
        return am[key]

    assert run_and_validate(fn) == values[key]


@given(map_and_key(), ints)
def test_update(args, new_value):
    values, key = args
    pairs = Array(*starmap(Pair, values.items()))
    count = len(values)

    def fn():
        am = ArrayMap[int, int, count].new()
        for pair in pairs:
            am[pair.first] = pair.second
        am[key] = new_value
        return am[key]

    assert run_and_validate(fn) == new_value


@given(maps)
def test_keys(values):
    pairs = Array(*starmap(Pair, values.items()))
    count = len(values)

    def fn():
        am = ArrayMap[int, int, count].new()
        for pair in pairs:
            am[pair.first] = pair.second
        return am.keys()

    assert sorted(run_and_validate(fn)) == sorted(values.keys())


@given(maps)
def test_values(values):
    pairs = Array(*starmap(Pair, values.items()))
    count = len(values)

    def fn():
        am = ArrayMap[int, int, count].new()
        for pair in pairs:
            am[pair.first] = pair.second
        return am.values()

    assert sorted(run_and_validate(fn)) == sorted(values.values())


@given(maps)
def test_items(values):
    pairs = Array(*starmap(Pair, values.items()))
    count = len(values)

    def fn():
        am = ArrayMap[int, int, count].new()
        for pair in pairs:
            am[pair.first] = pair.second
        return am.items()

    assert sorted(run_and_validate(fn)) == sorted(values.items())


@given(map_and_key())
def test_pop(args):
    values, key = args
    pairs = Array(*starmap(Pair, values.items()))
    count = len(values)
    target_value = values[key]
    target_values = {k: v for k, v in values.items() if k != key}

    def fn():
        am = ArrayMap[int, int, count].new()
        for pair in pairs:
            am[pair.first] = pair.second
        assert_true(am.pop(key) == target_value)
        assert_false(key in am)
        return am

    assert sorted(run_and_validate(fn).items()) == sorted(target_values.items())


@given(map_and_missing_key(), ints)
def test_insert_pop_round_trip(args, new_value):
    values, key = args
    pairs = Array(*starmap(Pair, values.items()))
    count = len(values)

    def fn():
        am = ArrayMap[int, int, count + 1].new()
        for pair in pairs:
            am[pair.first] = pair.second
        am[key] = new_value
        assert_true(am.pop(key) == new_value)
        assert_false(key in am)
        return am

    assert sorted(run_and_validate(fn).items()) == sorted(values.items())


@given(map_and_key())
def test_pop_insert_round_trip(args):
    values, key = args
    pairs = Array(*starmap(Pair, values.items()))
    count = len(values)
    target_value = values[key]

    def fn():
        am = ArrayMap[int, int, count].new()
        for pair in pairs:
            am[pair.first] = pair.second
        assert_true(am.pop(key) == target_value)
        am[key] = target_value
        return am

    assert sorted(run_and_validate(fn).items()) == sorted(values.items())


@given(map_and_key())
def test_delitem(args):
    values, key = args
    pairs = Array(*starmap(Pair, values.items()))
    count = len(values)
    target_values = {k: v for k, v in values.items() if k != key}

    def fn():
        am = ArrayMap[int, int, count].new()
        for pair in pairs:
            am[pair.first] = pair.second
        del am[key]
        assert_false(key in am)
        return am

    assert sorted(run_and_validate(fn).items()) == sorted(target_values.items())


@given(map_and_missing_key(), ints)
def test_insert_delitem_round_trip(args, new_value):
    values, key = args
    pairs = Array(*starmap(Pair, values.items()))
    count = len(values)

    def fn():
        am = ArrayMap[int, int, count + 1].new()
        for pair in pairs:
            am[pair.first] = pair.second
        am[key] = new_value
        del am[key]
        assert_false(key in am)
        return am

    assert sorted(run_and_validate(fn).items()) == sorted(values.items())


@given(map_and_key())
def test_delitem_insert_round_trip(args):
    values, key = args
    pairs = Array(*starmap(Pair, values.items()))
    count = len(values)
    target_value = values[key]

    def fn():
        am = ArrayMap[int, int, count].new()
        for pair in pairs:
            am[pair.first] = pair.second
        del am[key]
        am[key] = target_value
        return am

    assert sorted(run_and_validate(fn).items()) == sorted(values.items())


@given(maps)
def test_size(values):
    pairs = Array(*starmap(Pair, values.items()))
    count = len(values)

    def fn():
        am = ArrayMap[int, int, count].new()
        for i, pair in enumerate(pairs):
            assert_false(am.is_full())
            am[pair.first] = pair.second
            assert_true(len(am) == i + 1)
        keys = VarArray[int, count].new()
        for key in am:
            keys.append(key)
        assert_true(len(keys) == count)
        assert_true(am.is_full())
        for i, key in enumerate(keys):
            am.pop(key)
            assert_true(len(am) == count - i - 1)
            assert_false(am.is_full())
        return am

    assert len(run_and_validate(fn)) == 0


@given(maps)
def test_delitem_size(values):
    pairs = Array(*starmap(Pair, values.items()))
    count = len(values)

    def fn():
        am = ArrayMap[int, int, count].new()
        for i, pair in enumerate(pairs):
            assert_false(am.is_full())
            am[pair.first] = pair.second
            assert_true(len(am) == i + 1)
        keys = VarArray[int, count].new()
        for key in am:
            keys.append(key)
        assert_true(len(keys) == count)
        assert_true(am.is_full())
        for i, key in enumerate(keys):
            del am[key]
            assert_true(len(am) == count - i - 1)
            assert_false(am.is_full())
        return am

    assert len(run_and_validate(fn)) == 0


@given(map_and_key())
def test_contains_existing(args):
    values, key = args
    pairs = Array(*starmap(Pair, values.items()))
    count = len(values)

    def fn():
        am = ArrayMap[int, int, count].new()
        for pair in pairs:
            am[pair.first] = pair.second
        return key in am

    assert run_and_validate(fn)


@given(map_and_missing_key())
def test_contains_missing(args):
    values, key = args
    pairs = Array(*starmap(Pair, values.items()))
    count = len(values)

    def fn():
        am = ArrayMap[int, int, count].new()
        for pair in pairs:
            am[pair.first] = pair.second
        return key in am

    assert not run_and_validate(fn)


@given(map_and_key())
def test_not_contains_existing(args):
    values, key = args
    pairs = Array(*starmap(Pair, values.items()))
    count = len(values)

    def fn():
        am = ArrayMap[int, int, count].new()
        for pair in pairs:
            am[pair.first] = pair.second
        return key not in am

    assert not run_and_validate(fn)


@given(map_and_missing_key())
def test_not_contains_missing(args):
    values, key = args
    pairs = Array(*starmap(Pair, values.items()))
    count = len(values)

    def fn():
        am = ArrayMap[int, int, count].new()
        for pair in pairs:
            am[pair.first] = pair.second
        return key not in am

    assert run_and_validate(fn)


def test_array_map_truthiness_empty():
    def fn():
        x = ArrayMap[int, int, 5].new()
        return 1 if x else 0

    assert run_and_validate(fn) == 0


def test_array_map_truthiness_non_empty():
    def fn():
        x = ArrayMap[int, int, 5].new()
        x[1] = 100
        return 1 if x else 0

    assert run_and_validate(fn) == 1


MISSING_KEY = 3


def _filled_map():
    am = ArrayMap[int, int, 4].new()
    am[1] = 10
    am[2] = 20
    return am


# The missing-key paths call debug.error(), which terminates the callback instead of raising, so there is no
# plain-Python behaviour for run_and_validate to compare against: a dict-backed reference raising KeyError would
# describe something the compiled code never does. run_compiled with an explicit RuntimeChecks is the tool that
# observes termination, following tests/script/test_assert.py.
def test_getitem_missing_key_terminates():
    def fn():
        am = _filled_map()
        value = am[MISSING_KEY]
        # Not reached: error() terminates the callback.
        return value + 1000

    log_calls = []
    assert run_compiled(fn, runtime_checks=RuntimeChecks.TERMINATE, log_callback=log_calls.append) == 0
    assert log_calls == []

    log_calls = []
    assert run_compiled(fn, runtime_checks=RuntimeChecks.NOTIFY_AND_TERMINATE, log_callback=log_calls.append) == 0
    assert len(log_calls) == 1


def test_delitem_missing_key_terminates():
    def fn():
        am = _filled_map()
        del am[MISSING_KEY]
        return 1

    log_calls = []
    assert run_compiled(fn, runtime_checks=RuntimeChecks.TERMINATE, log_callback=log_calls.append) == 0
    assert log_calls == []

    log_calls = []
    assert run_compiled(fn, runtime_checks=RuntimeChecks.NOTIFY_AND_TERMINATE, log_callback=log_calls.append) == 0
    assert len(log_calls) == 1


def test_pop_missing_key_terminates():
    def fn():
        am = _filled_map()
        return am.pop(MISSING_KEY) + 1000

    log_calls = []
    assert run_compiled(fn, runtime_checks=RuntimeChecks.TERMINATE, log_callback=log_calls.append) == 0
    assert log_calls == []

    log_calls = []
    assert run_compiled(fn, runtime_checks=RuntimeChecks.NOTIFY_AND_TERMINATE, log_callback=log_calls.append) == 0
    assert len(log_calls) == 1


def test_present_key_lookups_do_not_false_fire():
    # The counterpart the three terminating tests need: a mutation that always terminates would pass without it.
    def fn():
        am = _filled_map()
        # Both reads finish before anything is removed: __getitem__ returns storage that stays part of the map.
        total = am[2] + am[1]
        am.pop(1)
        del am[2]
        return total + len(am)

    log_calls = []
    assert run_compiled(fn, runtime_checks=RuntimeChecks.NOTIFY_AND_TERMINATE, log_callback=log_calls.append) == 30
    assert log_calls == []


def test_setitem_new_key_when_full_terminates():
    def fn():
        am = ArrayMap[int, int, 2].new()
        am[1] = 10
        am[2] = 20
        am[3] = 30
        return 1

    log_calls = []
    assert run_compiled(fn, runtime_checks=RuntimeChecks.TERMINATE, log_callback=log_calls.append) == 0
    assert log_calls == []

    log_calls = []
    assert run_compiled(fn, runtime_checks=RuntimeChecks.NOTIFY_AND_TERMINATE, log_callback=log_calls.append) == 0
    assert len(log_calls) == 1


def test_setitem_existing_key_when_full_succeeds():
    def fn():
        am = ArrayMap[int, int, 2].new()
        am[1] = 10
        am[2] = 20
        assert_true(am.is_full())
        am[1] = 99
        assert_true(am.is_full())
        assert_true(len(am) == 2)
        return am[1]

    assert run_and_validate(fn) == 99


def test_array_map_clear():
    def fn():
        am = ArrayMap[int, int, 4].new()
        am[1] = 10
        am[2] = 20
        am.clear()
        assert_false(am.is_full())
        assert_false(1 in am)
        assert_true(len(am) == 0)
        am[5] = 50
        return am

    assert sorted(run_and_validate(fn).items()) == [(5, 50)]


def test_var_array_is_full():
    def fn():
        va = VarArray[int, 3].new()
        empty = 1 if va.is_full() else 0
        va.append(7)
        va.append(8)
        partial = 1 if va.is_full() else 0
        va.append(9)
        full = 1 if va.is_full() else 0
        va.pop(0)
        after_pop = 1 if va.is_full() else 0
        return Array(empty, partial, full, after_pop)

    assert list(run_and_validate(fn)) == [0, 0, 1, 0]
