# ruff: noqa: PLW1641, PT017
import re

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from sonolus.script.array import Array
from sonolus.script.debug import debug_log
from sonolus.script.internal.error import CompilationError
from sonolus.script.record import Record
from sonolus.script.vec import Vec2
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


class AddNotImplementedOnly(Record):
    # No hand-written __iadd__, so Record synthesizes one from __add__. That synthesized operator is what
    # has to decline when __add__ does, instead of trying to store the NotImplemented sentinel.
    def __add__(self, other):
        debug_log(30)
        return NotImplemented


class Acc(Record):
    total: int

    def __iadd__(self, other):
        debug_log(31)
        return Acc(self.total + other.total)


class Scaler(Record):
    factor: float

    def __rmul__(self, other):
        debug_log(32)
        return other.x * self.factor

    def __rtruediv__(self, other):
        debug_log(33)
        return other.x / self.factor


class Plain(Record):
    n: float


bin_values = [
    AllAddOps(),
    AllAddNotImplemented(),
    AddNotImplementedOnly(),
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


def test_max_two_arg_key_tie_returns_first():
    # On a key tie, max() must return the FIRST maximal argument (Python semantics and the
    # library's own single-iterable path). The buggy _max2_generic returned the second.
    def fn():
        return max(3, -3, key=lambda x: x * x)

    assert run_and_validate(fn) == 3


def test_min_two_arg_key_tie_returns_first():
    def fn():
        return min(-3, 3, key=lambda x: x * x)

    assert run_and_validate(fn) == -3


class RFloorDivOnly(Record):
    def __rfloordiv__(self, other):
        debug_log(30)
        return RFloorDivOnly()


def test_floordiv_falls_back_to_reflected_op():
    # Num.__floordiv__ must return NotImplemented for a non-numeric operand so the reflected
    # __rfloordiv__ is tried. The buggy version chained ._unary_op onto NotImplemented and
    # raised AttributeError, aborting compilation.
    def fn():
        x = 5
        y = RFloorDivOnly()
        return x // y

    run_and_validate(fn)


def test_eq_falls_back_to_reflected_op():
    # Num.__eq__ must return NotImplemented for a non-numeric operand so the reflected __eq__ is
    # tried. EqOnly.__eq__ logs debug_log(16), so the oracle's log check pins that the reflected op
    # actually ran rather than the return value matching by chance.
    def fn():
        x = 5
        y = EqOnly()
        return x == y

    assert run_and_validate(fn)


def test_ne_falls_back_to_reflected_op():
    # The mirror for Num.__ne__. EqNotImplemented.__ne__ logs debug_log(18) and then declines,
    # leaving the different-types rule to supply the True.
    def fn():
        x = 5
        y = EqNotImplemented()
        return x != y

    assert run_and_validate(fn)


def test_inplace_op_may_return_a_new_object():
    # A hand-written __iadd__ may return anything __add__ may, and augmented assignment rebinds the name
    # to whatever comes back, exactly as the __add__ arm does.
    def fn():
        x = Acc(1)
        x += Acc(2)
        return x.total

    assert run_and_validate(fn) == 3


def test_inplace_op_returning_a_new_object_leaves_an_alias_alone():
    def fn():
        x = Acc(1)
        alias = x
        x += Acc(2)
        return x.total * 10 + alias.total

    assert run_and_validate(fn) == 31


def test_inplace_op_returning_a_new_object_stores_into_a_subscript_target():
    def fn():
        arr = Array(Acc(1), Acc(2))
        arr[0] += Acc(5)
        return arr[0].total * 10 + arr[1].total

    assert run_and_validate(fn) == 62


def test_synthesized_inplace_op_declines_and_the_reflected_op_runs():
    # Vec2.__mul__ returns NotImplemented for a Scaler, so `v *= s` has to reach Scaler.__rmul__ the way
    # `v = v * s` already does.
    def fn():
        v = Vec2(1.0, 2.0)
        v *= Scaler(3.0)
        return v

    assert run_and_validate(fn) == 3.0


def test_synthesized_inplace_op_declines_for_truediv_too():
    def fn():
        v = Vec2(6.0, 2.0)
        v /= Scaler(3.0)
        return v

    assert run_and_validate(fn) == 2.0


def test_synthesized_inplace_op_reports_the_augmented_operator_for_a_bad_operand():
    def fn():
        v = Vec2(1.0, 2.0)
        v *= Plain(3.0)
        return v.x

    with pytest.raises(TypeError, match=re.escape("unsupported operand type(s) for *=: 'Vec2' and 'Plain'")):
        run_and_validate(fn)


def test_synthesized_inplace_op_declines_for_a_user_record():
    def fn():
        x = AddNotImplementedOnly()
        y = RAddOnly()
        x += y
        return x

    run_and_validate(fn)


class Holder(Record):
    values: Array[int, 3]

    def __iter__(self):
        return iter(self.values)


def _logged(v):
    debug_log(v)
    return v


def test_in_falls_back_to_iteration_over_a_generator_expression():
    def fn():
        return 6 in (v * 2 for v in Array(1, 2, 3))

    assert run_and_validate(fn)


def test_not_in_falls_back_to_iteration_over_a_generator_expression():
    def fn():
        return 5 not in (v * 2 for v in Array(1, 2, 3))

    assert run_and_validate(fn)


def test_in_falls_back_to_iteration_over_map_and_filter():
    def fn():
        a = 3 in map(lambda v: v + 1, Array(1, 2, 3))  # noqa: C417
        b = 4 in filter(lambda v: v > 2, Array(1, 2, 3))
        return (1 if a else 0) * 10 + (1 if b else 0)

    assert run_and_validate(fn) == 10


def test_in_falls_back_to_iteration_over_a_record_defining_only_iter():
    def fn():
        h = Holder(Array(4, 5, 6))
        return (1 if 5 in h else 0) * 10 + (1 if 7 in h else 0)

    assert run_and_validate(fn) == 10


def test_in_short_circuits_at_the_first_match():
    # The log is what pins the short circuit: the third element is never produced.
    def fn():
        return 2 in (_logged(v) for v in Array(1, 2, 3))

    assert run_and_validate(fn)


def test_in_consumes_a_one_shot_iterator_up_to_the_match():
    # Membership over an iterator consumes it, so the loop that follows resumes after the match.
    def fn():
        it = iter(Array(1, 2, 3, 4))
        found = 2 in it
        total = 0
        for v in it:
            total = total * 10 + v
        return (1 if found else 0) * 1000 + total

    assert run_and_validate(fn) == 1034
