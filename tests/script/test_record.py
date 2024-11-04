import pytest
from hypothesis import given
from hypothesis import strategies as st

from sonolus.script.debug import assert_true
from sonolus.script.num import Num
from sonolus.script.record import Record
from tests.script.conftest import validate_dual_run


class Simple(Record):
    value: float


class Generic[T](Record):
    value: T

    def __add__(self, other):
        return Generic(self.value + other.value)

    def __isub__(self, other):
        self.value = 123
        return self


class Pair[T, U](Record):
    first: T
    second: U


class ConcreteCompound(Record):
    a: Pair[Num, Num]
    b: Pair[Num, Num]


@given(a=st.floats(allow_nan=False, allow_infinity=False))
def test_simple_record(a):
    def fn():
        r = Simple(a)
        assert_true(r.value == a)
        return 1

    assert validate_dual_run(fn) == 1


@given(a=st.floats(allow_nan=False, allow_infinity=False))
def test_generic_record_inference(a):
    def fn():
        r = Generic(a)
        assert_true(r.value == a)
        return 1

    assert validate_dual_run(fn) == 1


@given(a=st.floats(allow_nan=False, allow_infinity=False))
def test_generic_record_explicit(a):
    def fn():
        r = Generic[Num](a)
        assert_true(r.value == a)
        return 1

    assert validate_dual_run(fn) == 1


def test_concrete_compound_record():
    def fn():
        r = ConcreteCompound(Pair(1, 2), Pair(3, 4))
        r @= ConcreteCompound(Pair(5, 6), Pair(7, 8))
        return r

    assert validate_dual_run(fn) == ConcreteCompound(Pair(5, 6), Pair(7, 8))


def test_record_rejects_wrong_type():
    with pytest.raises(TypeError):
        Simple(Simple(1))


def test_concrete_generic_record_rejects_wrong_type():
    with pytest.raises(TypeError):
        Generic[Simple](1)


def test_nested_generic():
    def fn():
        r = Generic[Generic[Num]](Generic[Num](1))
        assert_true(r.value.value == 1)
        r2 = Generic[Generic[Num]](Generic(2))
        assert_true(r2.value.value == 2)
        return 1

    assert validate_dual_run(fn) == 1


def test_value_record_members_are_independent():
    def fn():
        v = 1
        r = Generic[Num](v)
        assert_true(r.value == v)
        r.value = 2
        assert_true(r.value == 2)
        assert_true(v == 1)
        return 1

    assert validate_dual_run(fn) == 1


def test_reference_record_members_are_shared():
    def fn():
        inner = Generic[Num](1)
        outer = Generic(inner)
        assert_true(outer.value.value == 1)
        inner.value = 2
        assert_true(outer.value.value == 2)
        outer.value.value = 3
        assert_true(inner.value == 3)
        return 1

    assert validate_dual_run(fn) == 1


def test_automatic_record_copy_assign():
    def fn():
        inner = Generic(1)
        other = Generic(2)
        outer = Generic(inner)
        outer.value = other
        assert_true(outer.value.value == 2)
        assert_true(inner.value == 2)
        return 1

    assert validate_dual_run(fn) == 1


def test_record_operator_overloading():
    def fn():
        r1 = Generic(1)
        r2 = Generic(2)
        r = r1 + r2
        assert_true(r == Generic(3))
        return 1

    assert validate_dual_run(fn) == 1


def test_record_inplace_operator_generation():
    def fn():
        r = Generic(1)
        r_ref = r
        r += Generic(2)
        assert_true(r == Generic(3))
        assert_true(r_ref == Generic(3))
        return 1

    assert validate_dual_run(fn) == 1


def test_record_explicit_inplace_operator():
    def fn():
        r = Generic(1)
        r_ref = r
        r -= Generic(2)
        assert_true(r == Generic(123))
        assert_true(r_ref == Generic(123))
        return 1

    assert validate_dual_run(fn) == 1


def test_record_equality():
    def fn():
        r1 = Pair(1, 2)
        r2 = Pair(1, 2)
        r3 = Pair(3, 4)
        r4 = Pair(Simple(1), Simple(2))
        r5 = Pair(Simple(1), Simple(2))
        r6 = Pair(Simple(3), Simple(4))

        assert_true(r1 == r2)
        assert_true(r1 != r3)
        assert_true(r4 == r5)
        assert_true(r4 != r6)

        return 1

    assert validate_dual_run(fn) == 1
