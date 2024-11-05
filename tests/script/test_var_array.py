from datetime import timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from sonolus.script.array import Array
from sonolus.script.containers import VarArray
from sonolus.script.debug import assert_true
from tests.script.conftest import validate_dual_run

ints = st.integers(min_value=-999, max_value=999)
lists = st.lists(ints, min_size=1, max_size=20)
sets = st.sets(ints, min_size=1, max_size=20)


@st.composite
def list_and_index(draw):
    values = draw(lists)
    index = draw(st.integers(min_value=0, max_value=len(values) - 1))
    return values, index


@st.composite
def list_and_insert_index(draw):
    values = draw(lists)
    index = draw(st.integers(min_value=0, max_value=len(values)))
    return values, index


@st.composite
def set_and_present_value(draw):
    values = draw(lists)
    index = draw(st.integers(min_value=0, max_value=len(values) - 1))
    return set(values), values[index]


@st.composite
def set_and_missing_value(draw):
    values = draw(sets)
    missing = draw(ints.filter(lambda x: x not in values))
    return values, missing


def test_var_array_insertion():
    def fn():
        values = Array(2, 4, 6, 8)
        va = VarArray[int, 4].new()
        for i, v in enumerate(values):
            va.append(v)
            assert_true(va[i] == v)
            assert_true(len(va) == i + 1)
        return va

    assert list(validate_dual_run(fn)) == [2, 4, 6, 8]


def test_var_array_pop():
    def fn():
        va = VarArray[int, 4].new()
        va.append(2)
        va.append(4)
        va.append(6)
        va.append(8)
        assert_true(len(va) == 4)
        assert_true(va.pop() == 8)
        assert_true(len(va) == 3)
        assert_true(va.pop(1) == 4)
        return va

    assert list(validate_dual_run(fn)) == [2, 6]


def test_var_array_insert():
    def fn():
        va = VarArray[int, 4].new()
        va.append(2)
        va.append(6)
        va.insert(1, 4)
        va.insert(3, 8)
        return va

    assert list(validate_dual_run(fn)) == [2, 4, 6, 8]


def test_var_array_extend():
    def fn():
        va = VarArray[int, 4].new()
        va.extend(Array(2, 4, 6, 8))
        return va

    assert list(validate_dual_run(fn)) == [2, 4, 6, 8]


@given(list_and_index())
@settings(deadline=timedelta(milliseconds=500))
def test_var_array_pop_insert_round_trip(args):
    values_list, index = args

    values = Array(*values_list)
    value_count = len(values_list)

    def fn():
        va = VarArray[int, value_count].new()
        va.extend(values)
        assert_true(len(va) == value_count)
        pop_result = va.pop(index)
        assert_true(len(va) == value_count - 1)
        assert_true(pop_result == values[index])
        va.insert(index, pop_result)
        assert_true(len(va) == value_count)
        return va

    assert list(validate_dual_run(fn)) == values_list


@given(list_and_insert_index())
@settings(deadline=timedelta(milliseconds=500))
def test_var_array_insert_pop_round_trip(args):
    values_list, index = args

    values = Array(*values_list)
    value_count = len(values_list)

    def fn():
        va = VarArray[int, value_count + 1].new()
        va.extend(values)
        assert_true(len(va) == value_count)
        insert_value = 100
        va.insert(index, insert_value)
        assert_true(len(va) == value_count + 1)
        assert_true(va[index] == insert_value)
        pop_result = va.pop(index)
        assert_true(len(va) == value_count)
        assert_true(pop_result == insert_value)
        return va

    assert list(validate_dual_run(fn)) == values_list


@given(list_and_index())
@settings(deadline=timedelta(milliseconds=500))
def test_remove_present_value(args):
    values_list, index = args

    values = Array(*values_list)
    value_count = len(values_list)
    to_remove = values[index]
    expected = list(values)
    expected.remove(to_remove)

    def fn():
        va = VarArray[int, value_count].new()
        va.extend(values)
        assert_true(va.remove(to_remove))
        return va

    assert list(validate_dual_run(fn)) == expected


@given(lists)
@settings(deadline=timedelta(milliseconds=500))
def test_remove_missing_value(values_list):
    values = Array(*values_list)
    value_count = len(values_list)
    to_remove = max(values) + 1
    expected = list(values)

    def fn():
        va = VarArray[int, value_count].new()
        va.extend(values)
        assert_true(not va.remove(to_remove))
        return va

    assert list(validate_dual_run(fn)) == expected


def test_clear():
    def fn():
        va = VarArray[int, 4].new()
        va.extend(Array(2, 4, 6, 8))
        va.clear()
        return va

    assert list(validate_dual_run(fn)) == []


@given(set_and_present_value())
@settings(deadline=timedelta(milliseconds=500))
def test_set_add_present(args):
    value_set, value = args
    values = Array(*value_set)
    value_count = len(value_set)

    def fn():
        va = VarArray[int, value_count + 1].new()
        va.extend(values)
        assert_true(not va.set_add(value))
        return va

    # Use sorted to check that there are no duplicates
    assert sorted(validate_dual_run(fn)) == sorted(values)


@given(set_and_missing_value())
@settings(deadline=timedelta(milliseconds=500))
def test_set_add_missing(args):
    value_set, missing = args
    values = Array(*value_set)
    value_count = len(value_set)

    def fn():
        va = VarArray[int, value_count + 1].new()
        va.extend(values)
        assert_true(va.set_add(missing))
        return va

    # Use sorted to check that there are no duplicates
    assert sorted(validate_dual_run(fn)) == sorted([*list(values), missing])


def test_set_add_full():
    def fn():
        va = VarArray[int, 4].new()
        va.extend(Array(2, 4, 6, 8))
        assert_true(not va.set_add(2))
        assert_true(not va.set_add(10))
        return va

    assert list(validate_dual_run(fn)) == [2, 4, 6, 8]


@given(set_and_present_value())
@settings(deadline=timedelta(milliseconds=500))
def test_set_remove_present(args):
    value_set, value = args
    values = Array(*value_set)
    value_count = len(value_set)

    def fn():
        va = VarArray[int, value_count].new()
        va.extend(values)
        assert_true(va.remove(value))
        return va

    assert sorted(validate_dual_run(fn)) == sorted(value_set - {value})


@given(set_and_missing_value())
@settings(deadline=timedelta(milliseconds=500))
def test_set_remove_missing(args):
    value_set, missing = args
    values = Array(*value_set)
    value_count = len(value_set)

    def fn():
        va = VarArray[int, value_count].new()
        va.extend(values)
        assert_true(not va.remove(missing))
        return va

    assert sorted(validate_dual_run(fn)) == sorted(value_set)


@given(set_and_missing_value())
@settings(deadline=timedelta(milliseconds=500))
def test_set_add_remove_round_trip(args):
    value_set, value = args
    values = Array(*value_set)
    value_count = len(value_set)

    def fn():
        va = VarArray[int, value_count + 1].new()
        va.extend(values)
        assert_true(va.set_add(value))
        assert_true(va.remove(value))
        return va

    assert sorted(validate_dual_run(fn)) == sorted(value_set)


@given(set_and_present_value())
@settings(deadline=timedelta(milliseconds=500))
def test_set_remove_add_round_trip(args):
    value_set, value = args
    values = Array(*value_set)
    value_count = len(value_set)

    def fn():
        va = VarArray[int, value_count + 1].new()
        va.extend(values)
        assert_true(va.remove(value))
        assert_true(not va.remove(value))
        assert_true(va.set_add(value))
        return va

    assert sorted(validate_dual_run(fn)) == sorted(value_set)
