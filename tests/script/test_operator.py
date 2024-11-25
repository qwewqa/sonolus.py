# ruff: noqa
from hypothesis import given
from hypothesis import strategies as st

from sonolus.script.debug import debug_log
from sonolus.script.internal.error import CompilationError
from sonolus.script.record import Record
from tests.script.conftest import validate_dual_run


class NothingImplemented(Record):
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


class LtOnly(Record):
    def __lt__(self, other):
        debug_log(18)
        return True


class GtOnly(Record):
    def __gt__(self, other):
        debug_log(19)
        return True


class LtGt(Record):
    def __lt__(self, other):
        debug_log(20)
        return True

    def __gt__(self, other):
        debug_log(21)
        return True


class LtNotImplemented(Record):
    def __lt__(self, other):
        debug_log(22)
        return NotImplemented


class GtNotImplemented(Record):
    def __gt__(self, other):
        debug_log(23)
        return NotImplemented


class LtGtNotImplemented(Record):
    def __lt__(self, other):
        debug_log(24)
        return NotImplemented

    def __gt__(self, other):
        debug_log(25)
        return NotImplemented


class HasCall(Record):
    def __call__(self):
        debug_log(26)
        return 123


bin_values = [
    AllAddOps(),
    AllAddNotImplemented(),
    AddOnly(),
    RAddOnly(),
    IAddOnly(),
    AddAndRAdd(),
    AddAndIAdd(),
    RAddAndIAdd(),
    NothingImplemented(),
]

eq_values = [
    EqOnly(),
    EqNotImplemented(),
    NothingImplemented(),
]

comp_values = [
    LtGt(),
    LtGtNotImplemented(),
    LtOnly(),
    GtOnly(),
    LtNotImplemented(),
    GtNotImplemented(),
    NothingImplemented(),
]


@given(
    st.one_of(*[st.just(value) for value in bin_values]),
    st.one_of(*[st.just(value) for value in bin_values]),
)
def test_bin_op(left, right):
    def fn():
        return left + right

    try:
        validate_dual_run(fn)
    except TypeError as e:
        assert "unsupported operand type(s)" in str(e)  # noqa: PT017


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
        validate_dual_run(fn)
    except TypeError as e:
        assert "unsupported operand type(s)" in str(e)


@given(
    st.one_of(*[st.just(value) for value in eq_values]),
    st.one_of(*[st.just(value) for value in eq_values]),
)
def test_eq_op(left, right):
    def fn():
        return left == right

    validate_dual_run(fn)


@given(
    st.one_of(*[st.just(value) for value in comp_values]),
    st.one_of(*[st.just(value) for value in comp_values]),
)
def test_comp_op(left, right):
    def fn():
        return left < right

    try:
        validate_dual_run(fn)
    except TypeError as e:
        assert "not supported between instances" in str(e)


def test_call_op():
    def fn():
        x = HasCall()
        return x()

    validate_dual_run(fn)


def test_unsupported_call():
    def fn():
        x = NothingImplemented()
        return x()

    try:
        validate_dual_run(fn)
    except TypeError as e:
        assert "not callable" in str(e)


def test_unsupported_unary():
    def fn():
        x = NothingImplemented()
        return -x  # type: ignore

    try:
        validate_dual_run(fn)
    except TypeError as e:
        assert "bad operand type" in str(e)
