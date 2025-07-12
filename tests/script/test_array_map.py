from itertools import starmap

from hypothesis import given
from hypothesis import strategies as st

from sonolus.script.array import Array
from sonolus.script.containers import ArrayMap, Pair, VarArray
from sonolus.script.debug import assert_false, assert_true
from tests.script.conftest import run_and_validate

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
