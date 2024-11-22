from datetime import timedelta
from itertools import starmap

from hypothesis import given, settings
from hypothesis import strategies as st

from sonolus.script.array import Array
from sonolus.script.containers import ArrayMap, Pair, VarArray
from sonolus.script.debug import assert_false, assert_true
from tests.script.conftest import validate_dual_run

ints = st.integers(min_value=-999, max_value=999)
maps = st.dictionaries(ints, ints, min_size=1, max_size=20)


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
@settings(deadline=timedelta(seconds=2))
def test_insertion(args):
    values, key = args
    pairs = Array(*starmap(Pair, values.items()))
    count = len(values)

    def fn():
        am = ArrayMap[int, int, count].new()
        for pair in pairs:
            am[pair.first] = pair.second
        return am[key]

    assert validate_dual_run(fn) == values[key]


@given(map_and_key(), ints)
@settings(deadline=timedelta(seconds=2))
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

    assert validate_dual_run(fn) == new_value


@given(maps)
@settings(deadline=timedelta(seconds=2))
def test_keys(values):
    pairs = Array(*starmap(Pair, values.items()))
    count = len(values)

    def fn():
        am = ArrayMap[int, int, count].new()
        for pair in pairs:
            am[pair.first] = pair.second
        return am.keys()

    assert sorted(validate_dual_run(fn)) == sorted(values.keys())


@given(maps)
@settings(deadline=timedelta(seconds=2))
def test_values(values):
    pairs = Array(*starmap(Pair, values.items()))
    count = len(values)

    def fn():
        am = ArrayMap[int, int, count].new()
        for pair in pairs:
            am[pair.first] = pair.second
        return am.values()

    assert sorted(validate_dual_run(fn)) == sorted(values.values())


@given(maps)
@settings(deadline=timedelta(seconds=2))
def test_items(values):
    pairs = Array(*starmap(Pair, values.items()))
    count = len(values)

    def fn():
        am = ArrayMap[int, int, count].new()
        for pair in pairs:
            am[pair.first] = pair.second
        return am.items()

    assert sorted(validate_dual_run(fn)) == sorted(values.items())


@given(map_and_key())
@settings(deadline=timedelta(seconds=2))
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

    assert sorted(validate_dual_run(fn).items()) == sorted(target_values.items())


@given(map_and_missing_key(), ints)
@settings(deadline=timedelta(seconds=2))
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

    assert sorted(validate_dual_run(fn).items()) == sorted(values.items())


@given(map_and_key())
@settings(deadline=timedelta(seconds=2))
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

    assert sorted(validate_dual_run(fn).items()) == sorted(values.items())


@given(maps)
@settings(deadline=timedelta(seconds=2))
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

    assert len(validate_dual_run(fn)) == 0


@given(map_and_key())
@settings(deadline=timedelta(seconds=2))
def test_contains_existing(args):
    values, key = args
    pairs = Array(*starmap(Pair, values.items()))
    count = len(values)

    def fn():
        am = ArrayMap[int, int, count].new()
        for pair in pairs:
            am[pair.first] = pair.second
        return key in am

    assert validate_dual_run(fn)


@given(map_and_missing_key())
@settings(deadline=timedelta(seconds=2))
def test_contains_missing(args):
    values, key = args
    pairs = Array(*starmap(Pair, values.items()))
    count = len(values)

    def fn():
        am = ArrayMap[int, int, count].new()
        for pair in pairs:
            am[pair.first] = pair.second
        return key in am

    assert not validate_dual_run(fn)


@given(map_and_key())
@settings(deadline=timedelta(seconds=2))
def test_not_contains_existing(args):
    values, key = args
    pairs = Array(*starmap(Pair, values.items()))
    count = len(values)

    def fn():
        am = ArrayMap[int, int, count].new()
        for pair in pairs:
            am[pair.first] = pair.second
        return key not in am

    assert not validate_dual_run(fn)


@given(map_and_missing_key())
@settings(deadline=timedelta(seconds=2))
def test_not_contains_missing(args):
    values, key = args
    pairs = Array(*starmap(Pair, values.items()))
    count = len(values)

    def fn():
        am = ArrayMap[int, int, count].new()
        for pair in pairs:
            am[pair.first] = pair.second
        return key not in am

    assert validate_dual_run(fn)
