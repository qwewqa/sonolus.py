# ruff: noqa: B905, C417

from hypothesis import given
from hypothesis import strategies as st

from sonolus.script.array import Array
from sonolus.script.containers import VarArray
from sonolus.script.debug import assert_true, debug_log
from tests.script.conftest import run_and_validate

ints = st.integers(min_value=-999, max_value=999)
lists = st.lists(ints, min_size=1, max_size=20)
sets = st.sets(ints, min_size=1, max_size=20)


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
    index = draw(st.integers(min_value=-len(values), max_value=len(values) - 1))
    return values, index


@st.composite
def list_and_insert_index(draw):
    values = draw(lists)
    index = draw(st.integers(min_value=-len(values), max_value=len(values)))
    return values, index


@st.composite
def set_and_present_value(draw):
    values = draw(lists)
    index = draw(st.integers(min_value=-len(values), max_value=len(values) - 1))
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

    assert list(run_and_validate(fn)) == [2, 4, 6, 8]


def test_var_array_setitem():
    def fn():
        va = VarArray[int, 4].new()
        va.extend(Array(2, 4, 6, 8))
        va[1] = 10
        return va

    assert list(run_and_validate(fn)) == [2, 10, 6, 8]


def test_var_array_negative_indexing():
    def fn():
        va = VarArray[int, 5].new()
        va.extend(Array(10, 20, 30, 40, 50))

        return Array(
            va[-1],
            va[-2],
            va[-3],
            va[-4],
            va[-5],
        )

    assert list(run_and_validate(fn)) == [50, 40, 30, 20, 10]


def test_var_array_negative_setitem():
    def fn():
        va = VarArray[int, 5].new()
        va.extend(Array(10, 20, 30, 40, 50))

        va[-1] = 99
        va[-2] = 88
        va[-3] = 77

        return va

    assert list(run_and_validate(fn)) == [10, 20, 77, 88, 99]


def test_var_array_negative_pop():
    def fn():
        va = VarArray[int, 5].new()
        va.extend(Array(10, 20, 30, 40, 50))

        pop1 = va.pop(-1)
        pop2 = va.pop(-2)
        length = len(va)

        results = VarArray[int, 6].new()
        results.append(pop1)
        results.append(pop2)
        results.append(length)

        for item in va:
            results.append(item)

        return results

    p1, p2, new_len, *rem = run_and_validate(fn)
    assert p1 == 50
    assert p2 == 30
    assert new_len == 3
    assert list(rem) == [10, 20, 40]


def test_var_array_del():
    def fn():
        va = VarArray[int, 4].new()
        va.extend(Array(2, 4, 6, 8))
        del va[1]
        return va

    assert list(run_and_validate(fn)) == [2, 6, 8]


def test_var_array_negative_del():
    def fn():
        va = VarArray[int, 4].new()
        va.extend(Array(2, 4, 6, 8))
        del va[-1]
        del va[-2]
        return va

    assert list(run_and_validate(fn)) == [2, 6]


def test_var_array_iadd():
    def fn():
        va = VarArray[int, 6].new()
        va.extend(Array(2, 4, 6, 8))
        va += Array(10, 12)
        return va

    assert list(run_and_validate(fn)) == [2, 4, 6, 8, 10, 12]


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

    assert list(run_and_validate(fn)) == [2, 6]


def test_var_array_insert():
    def fn():
        va = VarArray[int, 4].new()
        va.append(2)
        va.append(6)
        va.insert(1, 4)
        va.insert(3, 8)
        return va

    assert list(run_and_validate(fn)) == [2, 4, 6, 8]


def test_var_array_extend():
    def fn():
        va = VarArray[int, 4].new()
        va.extend(Array(2, 4, 6, 8))
        return va

    assert list(run_and_validate(fn)) == [2, 4, 6, 8]


@given(list_and_index())
def test_var_array_pop_insert_round_trip(args):
    values_list, index = args

    values = Array(*values_list)
    value_count = len(values_list)

    def fn():
        va = VarArray[int, value_count].new()
        va.extend(values)
        assert_true(len(va) == value_count)
        expected_pop_result = values[index]
        pop_result = va.pop(index)
        assert_true(pop_result == expected_pop_result)
        assert_true(len(va) == value_count - 1)
        if index >= 0:
            insert_index = index
        elif index == -1:
            insert_index = value_count - 1
        else:
            insert_index = index + 1
        va.insert(insert_index, pop_result)
        assert_true(len(va) == value_count)
        return va

    assert list(run_and_validate(fn)) == values_list


@given(list_and_insert_index())
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
        assert_true(va[index if index >= 0 else index - 1] == insert_value)
        pop_result = va.pop(index if index >= 0 else index - 1)
        assert_true(len(va) == value_count)
        assert_true(pop_result == insert_value)
        return va

    assert list(run_and_validate(fn)) == values_list


@given(list_and_index())
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

    assert list(run_and_validate(fn)) == expected


@given(lists)
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

    assert list(run_and_validate(fn)) == expected


@given(list_and_index(), st.booleans())
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

    result = run_and_validate(fn)
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

    assert list(run_and_validate(fn)) == []


@given(set_and_present_value())
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
    assert sorted(run_and_validate(fn)) == sorted(values)


@given(set_and_missing_value())
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
    assert sorted(run_and_validate(fn)) == sorted([*list(values), missing])


def test_set_add_full():
    def fn():
        va = VarArray[int, 4].new()
        va.extend(Array(2, 4, 6, 8))
        assert_true(not va.set_add(2))
        assert_true(not va.set_add(10))
        return va

    assert list(run_and_validate(fn)) == [2, 4, 6, 8]


@given(set_and_present_value())
def test_set_remove_present(args):
    value_set, value = args
    values = Array(*value_set)
    value_count = len(value_set)

    def fn():
        va = VarArray[int, value_count].new()
        va.extend(values)
        assert_true(va.remove(value))
        return va

    assert sorted(run_and_validate(fn)) == sorted(value_set - {value})


@given(set_and_missing_value())
def test_set_remove_missing(args):
    value_set, missing = args
    values = Array(*value_set)
    value_count = len(value_set)

    def fn():
        va = VarArray[int, value_count].new()
        va.extend(values)
        assert_true(not va.remove(missing))
        return va

    assert sorted(run_and_validate(fn)) == sorted(value_set)


@given(set_and_missing_value())
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

    assert sorted(run_and_validate(fn)) == sorted(value_set)


@given(set_and_present_value())
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

    assert sorted(run_and_validate(fn)) == sorted(value_set)


@given(list_with_duplicates())
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

    assert run_and_validate(fn) == expected_count


@given(list_with_duplicates())
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

    assert run_and_validate(fn) == expected_count


@given(list_and_maybe_missing_value())
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

    assert run_and_validate(fn) == expected_count


def test_zip_empty():
    def fn():
        va_1 = VarArray[int, 0].new()
        va_2 = VarArray[int, 0].new()
        results = VarArray[int, 0].new()
        for v1, v2 in zip(va_1, va_2):
            results.append(v1)
            results.append(v2)
        return results

    assert list(run_and_validate(fn)) == []


@given(
    lists,
)
def test_zip_single(values_list_1):
    values_1 = Array(*values_list_1)
    value_count_1 = len(values_list_1)
    result_count = value_count_1

    def fn():
        va_1 = VarArray[int, value_count_1].new()
        va_1.extend(values_1)
        results = VarArray[int, result_count].new()
        for (v1,) in zip(va_1):
            results.append(v1)
        return results

    assert list(run_and_validate(fn)) == values_list_1


@given(
    lists,
    lists,
)
def test_zip_two(values_list_1, values_list_2):
    values_1 = Array(*values_list_1)
    values_2 = Array(*values_list_2)
    value_count_1 = len(values_list_1)
    value_count_2 = len(values_list_2)
    result_count = min(value_count_1, value_count_2) * 2

    def fn():
        va_1 = VarArray[int, value_count_1].new()
        va_1.extend(values_1)
        va_2 = VarArray[int, value_count_2].new()
        va_2.extend(values_2)
        results = VarArray[int, result_count].new()
        for v1, v2 in zip(va_1, va_2):
            results.append(v1)
            results.append(v2)
        return results

    assert list(run_and_validate(fn)) == [e for pair in zip(values_list_1, values_list_2) for e in pair]


@given(
    lists,
    lists,
    lists,
)
def test_zip_three(values_list_1, values_list_2, values_list_3):
    values_1 = Array(*values_list_1)
    values_2 = Array(*values_list_2)
    values_3 = Array(*values_list_3)
    value_count_1 = len(values_list_1)
    value_count_2 = len(values_list_2)
    value_count_3 = len(values_list_3)
    result_count = min(value_count_1, value_count_2, value_count_3) * 3

    def fn():
        va_1 = VarArray[int, value_count_1].new()
        va_1.extend(values_1)
        va_2 = VarArray[int, value_count_2].new()
        va_2.extend(values_2)
        va_3 = VarArray[int, value_count_3].new()
        va_3.extend(values_3)
        results = VarArray[int, result_count].new()
        for v1, v2, v3 in zip(va_1, va_2, va_3):
            results.append(v1)
            results.append(v2)
            results.append(v3)
        return results

    assert list(run_and_validate(fn)) == [
        e for triple in zip(values_list_1, values_list_2, values_list_3) for e in triple
    ]


def test_map_single():
    def fn():
        va = VarArray[int, 4].new()
        va.extend(Array(1, 2, 3, 4))
        results = VarArray[int, 4].new()
        for x in map(lambda x: x * 2, va):
            results.append(x)
        return results

    assert list(run_and_validate(fn)) == [2, 4, 6, 8]


def test_map_two_args():
    def fn():
        va1 = VarArray[int, 4].new()
        va2 = VarArray[int, 4].new()
        va1.extend(Array(1, 2, 3, 4))
        va2.extend(Array(10, 20, 30, 40))
        results = VarArray[int, 4].new()
        for x in map(lambda x, y: x + y, va1, va2):  # noqa: FURB118
            results.append(x)
        return results

    assert list(run_and_validate(fn)) == [11, 22, 33, 44]


def test_map_three_args():
    def fn():
        va1 = VarArray[int, 3].new()
        va2 = VarArray[int, 3].new()
        va3 = VarArray[int, 3].new()
        va1.extend(Array(1, 2, 3))
        va2.extend(Array(10, 20, 30))
        va3.extend(Array(100, 200, 300))
        results = VarArray[int, 3].new()
        for x in map(lambda x, y, z: x + y + z, va1, va2, va3):
            results.append(x)
        return results

    assert list(run_and_validate(fn)) == [111, 222, 333]


def test_filter_basic():
    def fn():
        va = VarArray[int, 6].new()
        va.extend(Array(1, 2, 3, 4, 5, 6))
        results = VarArray[int, 6].new()
        for x in filter(lambda x: x % 2 == 0, va):
            results.append(x)
        return results

    assert list(run_and_validate(fn)) == [2, 4, 6]


@given(lists)
def test_filter_property(values_list):
    values = Array(*values_list)
    value_count = len(values_list)

    def fn():
        va = VarArray[int, value_count].new()
        va.extend(values)
        results = VarArray[int, value_count].new()
        for x in filter(lambda x: x > 0, va):
            results.append(x)
        return results

    assert list(run_and_validate(fn)) == [x for x in values_list if x > 0]


def test_map_filter_combination():
    def fn():
        va = VarArray[int, 6].new()
        va.extend(Array(1, 2, 3, 4, 5, 6))
        results = VarArray[int, 6].new()
        for x in map(lambda x: x * 2, filter(lambda x: x % 2 == 0, va)):
            results.append(x)
        return results

    assert list(run_and_validate(fn)) == [4, 8, 12]


def test_filter_map_combination():
    def fn():
        va = VarArray[int, 6].new()
        va.extend(Array(1, 2, 3, 4, 5, 6))
        results = VarArray[int, 6].new()
        for x in filter(lambda x: x > 5, map(lambda x: x * 2, va)):
            results.append(x)
        return results

    assert list(run_and_validate(fn)) == [6, 8, 10, 12]


def test_any_true():
    def fn():
        va = VarArray[bool, 6].new()
        va.extend(Array(True, False, True, False, True, False))
        return any(va)

    assert run_and_validate(fn)


def test_any_false():
    def fn():
        va = VarArray[bool, 6].new()
        va.extend(Array(False, False, False, False, False, False))
        return any(va)

    assert not run_and_validate(fn)


def test_any_empty():
    def fn():
        va = VarArray[bool, 0].new()
        return any(va)

    assert not run_and_validate(fn)


def test_all_true():
    def fn():
        va = VarArray[bool, 6].new()
        va.extend(Array(True, True, True, True, True, True))
        return all(va)

    assert run_and_validate(fn)


def test_all_false():
    def fn():
        va = VarArray[bool, 6].new()
        va.extend(Array(True, False, True, False, True, False))
        return all(va)

    assert not run_and_validate(fn)


def test_all_empty():
    def fn():
        va = VarArray[bool, 0].new()
        return all(va)

    assert run_and_validate(fn)


def test_var_array_truthiness_empty():
    def fn():
        x = VarArray[int, 1].new()
        return 1 if x else 0

    assert run_and_validate(fn) == 0


def test_var_array_truthiness_non_empty():
    def fn():
        x = VarArray[int, 3].new()
        x.append(1)
        return 1 if x else 0

    assert run_and_validate(fn) == 1
