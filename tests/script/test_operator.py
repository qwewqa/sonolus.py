# ruff: noqa: PLW1641, PT017
import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from sonolus.script.debug import debug_log
from sonolus.script.internal.error import CompilationError
from sonolus.script.record import Record
from tests.script.conftest import run_and_validate, run_compiled


class DefaultsOnly(Record):
    pass


class AddOnly(Record):
    def __add__(self, other):
        debug_log(1)
        return AddOnly()


class RAddOnly(Record):
    def __radd__(self, other):
        debug_log(2)
        return RAddOnly()


class IAddOnly(Record):
    def __iadd__(self, other):
        debug_log(3)
        return self


class AddAndRAdd(Record):
    def __add__(self, other):
        debug_log(4)
        return AddAndRAdd()

    def __radd__(self, other):
        debug_log(5)
        return AddAndRAdd()


class AddAndIAdd(Record):
    def __add__(self, other):
        debug_log(6)
        return AddAndIAdd()

    def __iadd__(self, other):
        debug_log(7)
        return self


class RAddAndIAdd(Record):
    def __radd__(self, other):
        debug_log(8)
        return RAddAndIAdd()

    def __iadd__(self, other):
        debug_log(9)
        return self


class AllAddOps(Record):
    def __add__(self, other):
        debug_log(10)
        return AllAddOps()

    def __radd__(self, other):
        debug_log(11)
        return AllAddOps()

    def __iadd__(self, other):
        debug_log(12)
        return self


class AllAddNotImplemented(Record):
    def __add__(self, other):
        debug_log(13)
        return NotImplemented

    def __radd__(self, other):
        debug_log(14)
        return NotImplemented

    def __iadd__(self, other):
        debug_log(15)
        return NotImplemented


class EqOnly(Record):
    def __eq__(self, other):
        debug_log(16)
        return True


class EqNotImplemented(Record):
    def __eq__(self, other):
        debug_log(17)
        return NotImplemented

    def __ne__(self, other):
        debug_log(18)
        return NotImplemented


class LtOnly(Record):
    def __lt__(self, other):
        debug_log(19)
        return True


class GtOnly(Record):
    def __gt__(self, other):
        debug_log(20)
        return True


class LtGt(Record):
    def __lt__(self, other):
        debug_log(21)
        return True

    def __gt__(self, other):
        debug_log(22)
        return True


class LtNotImplemented(Record):
    def __lt__(self, other):
        debug_log(23)
        return NotImplemented


class GtNotImplemented(Record):
    def __gt__(self, other):
        debug_log(24)
        return NotImplemented


class LtGtNotImplemented(Record):
    def __lt__(self, other):
        debug_log(25)
        return NotImplemented

    def __gt__(self, other):
        debug_log(26)
        return NotImplemented


class HasCall(Record):
    def __call__(self):
        debug_log(27)
        return 123


class BoolTrue(Record):
    def __bool__(self):
        debug_log(28)
        return True


class BoolFalse(Record):
    def __bool__(self):
        debug_log(29)
        return False


bin_values = [
    AllAddOps(),
    AllAddNotImplemented(),
    AddOnly(),
    RAddOnly(),
    IAddOnly(),
    AddAndRAdd(),
    AddAndIAdd(),
    RAddAndIAdd(),
    DefaultsOnly(),
]

eq_values = [
    EqOnly(),
    EqNotImplemented(),
    DefaultsOnly(),
]

comp_values = [
    LtGt(),
    LtGtNotImplemented(),
    LtOnly(),
    GtOnly(),
    LtNotImplemented(),
    GtNotImplemented(),
    DefaultsOnly(),
]


@given(
    st.one_of(*[st.just(value) for value in bin_values]),
    st.one_of(*[st.just(value) for value in bin_values]),
)
def test_bin_op(left, right):
    def fn():
        return left + right

    try:
        run_and_validate(fn)
    except TypeError as e:
        assert "unsupported operand type(s)" in str(e)


@given(
    st.one_of(*[st.just(value) for value in bin_values]),
    st.one_of(*[st.just(value) for value in bin_values]),
)
def test_iop(left, right):
    def fn():
        x = left
        y = right
        x += y

    try:
        run_and_validate(fn)
    except TypeError as e:
        assert "unsupported operand type(s)" in str(e)


@given(
    st.one_of(*[st.just(value) for value in eq_values]),
    st.one_of(*[st.just(value) for value in eq_values]),
)
def test_eq_op(left, right):
    assume(not (isinstance(left, EqNotImplemented) and isinstance(right, EqNotImplemented)))  # See the next test

    def fn():
        return left == right

    run_and_validate(fn)


def test_eq_not_implemented():
    def fn():
        x = EqNotImplemented()
        y = EqNotImplemented()
        return x == y

    assert not fn()
    with pytest.raises(CompilationError, match="not supported between instances"):
        run_compiled(fn)


def test_not_eq_not_implemented():
    def fn():
        x = EqNotImplemented()
        y = EqNotImplemented()
        return x != y

    assert fn()
    with pytest.raises(CompilationError, match="not supported between instances"):
        run_compiled(fn)


@given(
    st.one_of(*[st.just(value) for value in comp_values]),
    st.one_of(*[st.just(value) for value in comp_values]),
)
def test_comp_op(left, right):
    def fn():
        return left < right

    try:
        run_and_validate(fn)
    except TypeError as e:
        assert "not supported between instances" in str(e)


def test_call_op():
    def fn():
        x = HasCall()
        return x()

    run_and_validate(fn)


def test_unsupported_call():
    def fn():
        x = DefaultsOnly()
        return x()

    try:
        run_and_validate(fn)
    except TypeError as e:
        assert "not callable" in str(e)


def test_unsupported_unary():
    def fn():
        x = DefaultsOnly()
        return -x  # type: ignore

    try:
        run_and_validate(fn)
    except TypeError as e:
        assert "bad operand type" in str(e)


def test_bool_true_truthiness():
    def fn():
        x = BoolTrue()
        return 1 if x else 0

    assert run_and_validate(fn) == 1


def test_bool_false_truthiness():
    def fn():
        x = BoolFalse()
        return 1 if x else 0

    assert run_and_validate(fn) == 0


def test_bool_true_match_case():
    def fn():
        x = BoolTrue()
        match x:
            case _ if x:
                return 1
            case _:
                return 0

    assert run_and_validate(fn) == 1


def test_bool_false_match_case():
    def fn():
        x = BoolFalse()
        match x:
            case _ if x:
                return 1
            case _:
                return 0

    assert run_and_validate(fn) == 0


def test_bool_true_while_condition():
    def fn():
        x = BoolTrue()
        while x:
            return 1
        return 0

    assert run_and_validate(fn) == 1


def test_bool_false_while_condition():
    def fn():
        x = BoolFalse()
        while x:
            return 1
        return 0

    assert run_and_validate(fn) == 0


def test_bool_true_not_operator():
    def fn():
        x = BoolTrue()
        return 1 if not x else 0

    assert run_and_validate(fn) == 0


def test_bool_false_not_operator():
    def fn():
        x = BoolFalse()
        return 1 if not x else 0

    assert run_and_validate(fn) == 1


def test_bool_call_true():
    def fn():
        x = BoolTrue()
        return bool(x)

    assert run_and_validate(fn)


def test_bool_call_false():
    def fn():
        x = BoolFalse()
        return bool(x)

    assert not run_and_validate(fn)
