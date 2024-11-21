from datetime import timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from sonolus.script.array import Array
from sonolus.script.containers import VarArray
from sonolus.script.debug import assert_true, debug_log
from tests.script.conftest import validate_dual_run

ints = st.integers(min_value=-999, max_value=999)
lists = st.lists(ints, min_size=1, max_size=20)
sets = st.sets(ints, min_size=1, max_size=20)


from hypothesis import strategies as st


@st.composite
def list_with_duplicates(draw):
    size = draw(st.integers(min_value=1, max_value=20))
    base_values = draw(st.lists(st.integers(min_value=-10, max_value=10), min_size=size // 2, max_size=size // 2))

    duplicates = draw(
        st.lists(
            st.sampled_from(base_values) if base_values else st.integers(-10, 10),
            min_size=size - len(base_values),
            max_size=size - len(base_values),
        )
    )

    values = base_values + duplicates
    values = draw(st.permutations(values))

    target = draw(st.sampled_from(values))
    return values, target


@st.composite
def list_and_maybe_missing_value(draw):
    size = draw(st.integers(min_value=1, max_value=20))
    values = draw(st.lists(st.integers(min_value=-10, max_value=10), min_size=size, max_size=size))

    if draw(st.booleans()):
        target = draw(st.integers(min_value=-20, max_value=20))
    else:
        target = draw(st.sampled_from(values))

    return values, target


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
@settings(deadline=timedelta(seconds=1))
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
@settings(deadline=timedelta(seconds=1))
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
@settings(deadline=timedelta(seconds=1))
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
@settings(deadline=timedelta(seconds=1))
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


@given(list_and_index(), st.booleans())
@settings(deadline=timedelta(seconds=1))
def test_array_with_possible_uninitialized_access(args, run):
    values_list, index = args
    values = Array(*values_list)
    run = Array(run)  # We use an array so the compiler doesn't know the value
    value_count = len(values_list)

    def fn():
        va = VarArray[int, value_count].new()
        if run[0]:
            va.extend(values)
        debug_log(0)
        if run[0]:
            return va[index]
        else:
            return -1

    result = validate_dual_run(fn)
    if run[0]:
        assert result == values_list[index]
    else:
        assert result == -1


def test_clear():
    def fn():
        va = VarArray[int, 4].new()
        va.extend(Array(2, 4, 6, 8))
        va.clear()
        return va

    assert list(validate_dual_run(fn)) == []


@given(set_and_present_value())
@settings(deadline=timedelta(seconds=1))
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
@settings(deadline=timedelta(seconds=1))
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
@settings(deadline=timedelta(seconds=1))
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
@settings(deadline=timedelta(seconds=1))
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
@settings(deadline=timedelta(seconds=1))
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
@settings(deadline=timedelta(seconds=1))
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


@given(list_with_duplicates())
@settings(deadline=timedelta(seconds=1))
def test_var_array_count(args):
    values_list, target = args
    values = Array(*values_list)
    value_count = len(values_list)
    expected_count = values_list.count(target)

    def fn():
        va = VarArray[int, value_count].new()
        va.extend(values)
        count_result = va.count(target)
        assert_true(count_result == expected_count)
        return count_result

    assert validate_dual_run(fn) == expected_count


@given(list_with_duplicates())
@settings(deadline=timedelta(seconds=1))
def test_var_array_count_present(args):
    values_list, target = args
    values = Array(*values_list)
    value_count = len(values_list)
    expected_count = values_list.count(target)

    def fn():
        va = VarArray[int, value_count].new()
        va.extend(values)
        count_result = va.count(target)
        assert_true(count_result == expected_count)
        return count_result

    assert validate_dual_run(fn) == expected_count


@given(list_and_maybe_missing_value())
@settings(deadline=timedelta(seconds=1))
def test_var_array_count_maybe_missing(args):
    values_list, target = args
    values = Array(*values_list)
    value_count = len(values_list)
    expected_count = values_list.count(target)

    def fn():
        va = VarArray[int, value_count].new()
        va.extend(values)
        count_result = va.count(target)
        assert_true(count_result == expected_count)
        return count_result

    assert validate_dual_run(fn) == expected_count
