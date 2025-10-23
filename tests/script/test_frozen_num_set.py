# ruff: noqa: FURB171
import random

from hypothesis import given
from hypothesis import strategies as st

from sonolus.script.array import Array
from sonolus.script.debug import assert_true
from tests.script.conftest import run_and_validate

nums = st.one_of(
    st.integers(min_value=-999, max_value=999),
    st.floats(min_value=-999.0, max_value=999.0, allow_nan=False, allow_infinity=False),
)


@st.composite
def set_and_present_value(draw, fixed_size: int | None = None):
    values = draw(
        st.sets(
            nums, min_size=1 if fixed_size is None else fixed_size, max_size=20 if fixed_size is None else fixed_size
        )
    )
    value = draw(st.sampled_from(list(values)))
    return values, value


@st.composite
def set_and_missing_value(draw, fixed_size: int | None = None):
    values = draw(
        st.sets(
            nums, min_size=1 if fixed_size is None else fixed_size, max_size=20 if fixed_size is None else fixed_size
        )
    )
    missing = draw(nums.filter(lambda x: x not in values))
    return values, missing


def test_contains_basic():
    def fn():
        assert_true(2 in {1, 2, 3, 4, 5})
        assert_true(5 in {1, 2, 3, 4, 5})
        assert_true(6 not in {1, 2, 3, 4, 5})
        assert_true(0 not in {1, 2, 3, 4, 5})
        return 1

    assert run_and_validate(fn) == 1


def test_contains_out_of_order():
    def fn():
        assert_true(3 in {5, 1, 4, 2, 3})
        assert_true(1 in {5, 1, 4, 2, 3})
        assert_true(6 not in {5, 1, 4, 2, 3})
        assert_true(0 not in {5, 1, 4, 2, 3})
        return 1

    assert run_and_validate(fn) == 1


def test_contains_non_literal_contents():
    def fn():
        a = 5 * (random.random() != -1)
        b = 4 * (random.random() != -1)
        c = 3 * (random.random() != -1)
        d = 2 * (random.random() != -1)
        e = 1 * (random.random() != -1)
        test_set = {a, b, c, d, e}
        assert_true(2 in test_set)
        assert_true(5 in test_set)
        assert_true(6 not in test_set)
        assert_true(0 not in test_set)
        return 1

    assert run_and_validate(fn) == 1


def test_defined_outside():
    test_set = {10, 20, 30, 40, 50}

    def fn():
        assert_true(10 in test_set)
        assert_true(30 in test_set)
        assert_true(50 in test_set)
        assert_true(5 not in test_set)
        assert_true(60 not in test_set)
        return 1

    assert run_and_validate(fn) == 1


def test_defined_outside_frozenset():
    test_set = frozenset({10, 20, 30, 40, 50})

    def fn():
        assert_true(10 in test_set)
        assert_true(30 in test_set)
        assert_true(50 in test_set)
        assert_true(5 not in test_set)
        assert_true(60 not in test_set)
        return 1

    assert run_and_validate(fn) == 1


def test_empty():
    empty_set = set()

    def fn():
        assert_true(0 not in empty_set)
        assert_true(1 not in empty_set)
        assert_true(0.0 not in empty_set)
        return 1

    assert run_and_validate(fn) == 1


def test_single_element_set():
    def fn():
        assert_true(42 in {42})
        assert_true(0 not in {42})
        assert_true(41 not in {42})
        assert_true(43 not in {42})
        return 1

    assert run_and_validate(fn) == 1


def test_negative_numbers():
    def fn():
        assert_true(-5 in {-10, -5, 0, 5, 10})
        assert_true(-10 in {-10, -5, 0, 5, 10})
        assert_true(0 in {-10, -5, 0, 5, 10})
        assert_true(-3 not in {-10, -5, 0, 5, 10})
        assert_true(-15 not in {-10, -5, 0, 5, 10})
        return 1

    assert run_and_validate(fn) == 1


@given(set_and_present_value())
def test_set_contains_present(args):
    value_set, value = args

    def fn():
        return value in value_set

    assert run_and_validate(fn)


@given(set_and_missing_value())
def test_set_contains_missing(args):
    value_set, missing = args

    def fn():
        return missing in value_set

    assert not run_and_validate(fn)


@given(set_and_present_value(fixed_size=20))
def test_dyn_set_contains_present(args):
    value_set, value = args

    def fn():
        values = +Array[float, 20]
        for i, v in enumerate(value_set):
            values[i] = v * (random.random() != -1)
        return value in {
            values[0],
            values[1],
            values[2],
            values[3],
            values[4],
            values[5],
            values[6],
            values[7],
            values[8],
            values[9],
            values[10],
            values[11],
            values[12],
            values[13],
            values[14],
            values[15],
            values[16],
            values[17],
            values[18],
            values[19],
        }

    assert run_and_validate(fn)


@given(set_and_missing_value(fixed_size=20))
def test_dyn_set_contains_missing(args):
    value_set, missing = args

    def fn():
        values = +Array[float, 20]
        for i, v in enumerate(value_set):
            values[i] = v * (random.random() != -1)
        return missing in {
            values[0],
            values[1],
            values[2],
            values[3],
            values[4],
            values[5],
            values[6],
            values[7],
            values[8],
            values[9],
            values[10],
            values[11],
            values[12],
            values[13],
            values[14],
            values[15],
            values[16],
            values[17],
            values[18],
            values[19],
        }

    assert not run_and_validate(fn)
