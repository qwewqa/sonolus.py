from datetime import timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from sonolus.script.array import Array
from sonolus.script.containers import ArraySet
from sonolus.script.debug import assert_true
from tests.script.conftest import validate_dual_run

ints = st.integers(min_value=-999, max_value=999)
sets = st.sets(ints, min_size=1, max_size=20)


@st.composite
def set_and_present_value(draw):
    values = draw(sets)
    value = draw(st.sampled_from(list(values)))
    return values, value


@st.composite
def set_and_missing_value(draw):
    values = draw(sets)
    missing = draw(ints.filter(lambda x: x not in values))
    return values, missing


def test_array_set_add_basic():
    def fn():
        s = ArraySet[int, 4].new()
        assert_true(s.add(2))
        assert_true(s.add(4))
        assert_true(2 in s)
        assert_true(4 in s)
        assert_true(6 not in s)
        return s

    assert sorted(validate_dual_run(fn)) == [2, 4]


def test_array_set_clear():
    def fn():
        s = ArraySet[int, 4].new()
        s.add(2)
        s.add(4)
        s.add(6)
        s.clear()
        return s._values

    assert list(validate_dual_run(fn)) == []


@given(set_and_present_value())
@settings(deadline=timedelta(seconds=1))
def test_array_set_add_present(args):
    value_set, value = args
    values = Array(*value_set)
    value_count = len(value_set)

    def fn():
        s = ArraySet[int, value_count + 1].new()
        for v in values:
            s.add(v)
        assert_true(not s.add(value))
        return s

    assert sorted(validate_dual_run(fn)) == sorted(values)


@given(set_and_missing_value())
@settings(deadline=timedelta(seconds=1))
def test_array_set_add_missing(args):
    value_set, missing = args
    values = Array(*value_set)
    value_count = len(value_set)

    def fn():
        s = ArraySet[int, value_count + 1].new()
        for v in values:
            s.add(v)
        assert_true(s.add(missing))
        return s

    assert sorted(validate_dual_run(fn)) == sorted([*list(values), missing])


@given(set_and_present_value())
@settings(deadline=timedelta(seconds=1))
def test_array_set_remove_present(args):
    value_set, value = args
    values = Array(*value_set)
    value_count = len(value_set)

    def fn():
        s = ArraySet[int, value_count].new()
        for v in values:
            s.add(v)
        assert_true(s.remove(value))
        return s

    assert sorted(validate_dual_run(fn)) == sorted(value_set - {value})


@given(set_and_missing_value())
@settings(deadline=timedelta(seconds=1))
def test_array_set_remove_missing(args):
    value_set, missing = args
    values = Array(*value_set)
    value_count = len(value_set)

    def fn():
        s = ArraySet[int, value_count].new()
        for v in values:
            s.add(v)
        assert_true(not s.remove(missing))
        return s

    assert sorted(validate_dual_run(fn)) == sorted(value_set)


@given(set_and_missing_value())
@settings(deadline=timedelta(seconds=1))
def test_array_set_add_remove_round_trip(args):
    value_set, value = args
    values = Array(*value_set)
    value_count = len(value_set)

    def fn():
        s = ArraySet[int, value_count + 1].new()
        for v in values:
            s.add(v)
        assert_true(s.add(value))
        assert_true(s.remove(value))
        return s

    assert sorted(validate_dual_run(fn)) == sorted(value_set)


def test_array_set_full():
    def fn():
        s = ArraySet[int, 4].new()
        assert_true(s.add(2))
        assert_true(s.add(4))
        assert_true(s.add(6))
        assert_true(s.add(8))
        assert_true(not s.add(10))  # Set is full
        return s

    assert list(validate_dual_run(fn)) == [2, 4, 6, 8]
