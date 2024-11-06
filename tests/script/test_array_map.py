from datetime import timedelta
from itertools import starmap

from hypothesis import given, settings
from hypothesis import strategies as st

from sonolus.script.array import Array
from sonolus.script.containers import ArrayMap, Pair
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
@settings(deadline=timedelta(milliseconds=500))
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
@settings(deadline=timedelta(milliseconds=500))
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
