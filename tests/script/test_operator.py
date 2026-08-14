# ruff: noqa: PLW1641, PT017
import inspect
import re
from abc import ABCMeta

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from sonolus.script.array import Array
from sonolus.script.debug import debug_log, error, static_error
from sonolus.script.internal.error import CompilationError
from sonolus.script.num import Num
from sonolus.script.record import Record
from sonolus.script.vec import Vec2
from tests.script.conftest import run_and_validate, run_compiled


class DefaultsOnly(Record):
    pass


class SynthesizedSpecialMethods(Record):
    def __getattr__(self, name):
        if name == "__add__":
            return lambda other: 12
        if name == "__eq__":
            return lambda other: True
        if name == "__contains__":
            return lambda value: True
        if name == "__iter__":
            return lambda: iter(Array(1))
        raise AttributeError(name)


class UnsupportedPlainClass:
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


class ComparisonResult(Record):
    value: float


class RecordValuedEq(Record):
    value: float

    def __eq__(self, other):
        return ComparisonResult(self.value + other.value)


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


class HasCallWithCustomGetattribute(Record):
    def __call__(self):
        return 124

    def __getattribute__(self, name):
        if name == "__call__":
            return lambda: 999
        return object.__getattribute__(self, name)


class DynamicallyMarkedCallable:
    def __call__(self):
        return 125


DYNAMICALLY_MARKED_CALLABLE = DynamicallyMarkedCallable()
DYNAMICALLY_MARKED_CALLABLE._is_comptime_value_ = True


class BoolTrue(Record):
    def __bool__(self):
        debug_log(28)
        return True


class BoolFalse(Record):
    def __bool__(self):
        debug_log(29)
        return False


class PropertyBackedBool(Record):
    value: float

    @property
    def __bool__(self):
        return self.true if self.value else self.false

    def true(self):
        return True

    def false(self):
        return False


class PropertyBackedEq(Record):
    value: float

    @property
    def __eq__(self):  # noqa: PLE0302
        return self.equal if self.value else self.not_equal

    def equal(self, other):
        return self.value == other.value

    def not_equal(self, other):
        return self.value != other.value


class SimplePropertyBackedSpecials(Record):
    @property
    def __bool__(self):
        return self.true

    @property
    def __eq__(self):  # noqa: PLE0302
        return self.equal

    def true(self):
        return True

    def equal(self, other):
        return True


class TerminatingPropertyGetter(Record):
    @property
    def __bool__(self):  # noqa: PLE0304
        error("property getter stopped")


class TerminatingPropertyMethod(Record):
    @property
    def __bool__(self):
        return self.stop

    def stop(self):
        error("property method stopped")


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


class PriorityBase(Num):
    __slots__ = ()

    def __add__(self, other):
        debug_log(34)
        return 1

    def __radd__(self, other):
        debug_log(35)
        return 2

    def __eq__(self, other):
        debug_log(36)
        return False

    __hash__ = Num.__hash__


class PrioritySub(PriorityBase):
    __slots__ = ()

    def __radd__(self, other):
        debug_log(37)
        return 3

    def __eq__(self, other):
        debug_log(38)
        return True

    __hash__ = Num.__hash__


class InheritedEqualityBase(Num):
    __slots__ = ()

    def __eq__(self, other):
        debug_log(39)
        return isinstance(self, InheritedEqualitySub)

    __hash__ = Num.__hash__


class InheritedEqualitySub(InheritedEqualityBase):
    __slots__ = ()


class CompileTimeOpBase:
    _is_comptime_value_ = True

    def __or__(self, other):
        return 40

    def __ror__(self, other):
        return 41


class CompileTimeOpSub(CompileTimeOpBase):
    def __ror__(self, other):
        return 42


class CompileTimeOpDeclines:
    _is_comptime_value_ = True

    def __or__(self, other):
        return NotImplemented


class CompileTimeReflected:
    _is_comptime_value_ = True

    def __ror__(self, other):
        return 43


class CompileTimeClassMethod:
    _is_comptime_value_ = True

    @classmethod
    def __or__(cls, other):
        return 44


class CompileTimeStaticMethod:
    _is_comptime_value_ = True

    @staticmethod
    def __or__(other):
        return 45


class CompileTimeInheritedClassMethodBase:
    _is_comptime_value_ = True

    @classmethod
    def __or__(cls, other):
        return 40

    @classmethod
    def __ior__(cls, other):
        return NotImplemented

    @classmethod
    def __ror__(cls, other):
        return 42 if cls is CompileTimeInheritedClassMethodSub else 41


class CompileTimeInheritedClassMethodSub(CompileTimeInheritedClassMethodBase):
    pass


class CompileTimeVirtualBase(metaclass=ABCMeta):  # noqa: B024, FURB180
    _is_comptime_value_ = True

    def __or__(self, other):
        return 50


class CompileTimeVirtualSub:
    _is_comptime_value_ = True

    def __ror__(self, other):
        return 51


CompileTimeVirtualBase.register(CompileTimeVirtualSub)


class CompileTimeNoneReflectedBase:
    _is_comptime_value_ = True

    def __or__(self, other):
        return 46

    def __ror__(self, other):
        return 47


class CompileTimeNoneReflectedSub(CompileTimeNoneReflectedBase):
    __ror__ = None


COMPTIME_BASE = CompileTimeOpBase()
COMPTIME_SUB = CompileTimeOpSub()
COMPTIME_DECLINES = CompileTimeOpDeclines()
COMPTIME_REFLECTED = CompileTimeReflected()
COMPTIME_CLASS_METHOD = CompileTimeClassMethod()
COMPTIME_STATIC_METHOD = CompileTimeStaticMethod()
COMPTIME_INHERITED_CLASS_METHOD_BASE = CompileTimeInheritedClassMethodBase()
COMPTIME_INHERITED_CLASS_METHOD_SUB = CompileTimeInheritedClassMethodSub()
COMPTIME_VIRTUAL_BASE = CompileTimeVirtualBase()
COMPTIME_VIRTUAL_SUB = CompileTimeVirtualSub()
COMPTIME_NONE_REFLECTED_BASE = CompileTimeNoneReflectedBase()
COMPTIME_NONE_REFLECTED_SUB = CompileTimeNoneReflectedSub()


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


def test_standalone_comparison_returns_record_result():
    def fn():
        result = RecordValuedEq(1) == RecordValuedEq(2)
        return result.value

    assert run_and_validate(fn) == 3


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


def test_call_uses_type_protocol_instead_of_custom_getattribute():
    def fn():
        return HasCallWithCustomGetattribute()()

    assert run_and_validate(fn) == 124


def test_call_preserves_instance_level_compile_time_marker():
    def fn():
        return DYNAMICALLY_MARKED_CALLABLE()

    assert run_and_validate(fn) == 125


def test_unsupported_call():
    def fn():
        x = DefaultsOnly()
        return x()

    try:
        run_and_validate(fn)
    except TypeError as e:
        assert "not callable" in str(e)


def test_unsupported_plain_class_call_names_class():
    def fn():
        return UnsupportedPlainClass()

    with pytest.raises(CompilationError, match="Calling class 'UnsupportedPlainClass' is not supported"):
        run_compiled(fn)


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


class ClassMethodBool(Record):
    @classmethod
    def __bool__(cls):
        return True


class StaticMethodLen(Record):
    @staticmethod
    def __len__():
        return 1


def test_classmethod_bool_is_bound_before_tracing():
    def fn():
        return 1 if ClassMethodBool() else 0

    assert run_and_validate(fn) == 1


def test_staticmethod_len_is_bound_before_tracing():
    def fn():
        return 1 if StaticMethodLen() else 0

    assert run_and_validate(fn) == 1


def property_backed_implicit_bool():
    value = Array(1)[0]
    return 1 if PropertyBackedBool(value) else 0


def property_backed_implicit_eq():
    value = Array(1)[0]
    return PropertyBackedEq(value) == PropertyBackedEq(value)


@pytest.mark.parametrize(
    ("fn", "method_name"),
    [
        (property_backed_implicit_bool, "__bool__"),
        (property_backed_implicit_eq, "__eq__"),
    ],
)
def test_property_backed_implicit_special_method_is_rejected_at_the_operator(fn, method_name):
    source_lines, first_line = inspect.getsourcelines(fn)
    expected_line = first_line + next(i for i, line in enumerate(source_lines) if line.lstrip().startswith("return "))

    with pytest.raises(
        CompilationError,
        match=rf"Using property '{method_name}' as an implicit protocol method .* is not supported",
    ) as exc_info:
        run_compiled(fn)

    assert type(exc_info.value.__cause__) is TypeError
    reported_lines = []
    exception = exc_info.value
    while exception is not None:
        frame = exception.__traceback__
        while frame is not None:
            if frame.tb_frame.f_code.co_filename == __file__:
                reported_lines.append(frame.tb_lineno)
            frame = frame.tb_next
        exception = exception.__cause__
    assert expected_line in reported_lines


def test_property_backed_special_methods_remain_available_explicitly():
    def fn():
        record = SimplePropertyBackedSpecials()
        return record.__bool__() and record.__eq__(record)  # noqa: PLC2801

    assert run_and_validate(fn)


def test_fixed_property_backed_special_methods_compile_implicitly():
    def fn():
        record = SimplePropertyBackedSpecials()
        other = SimplePropertyBackedSpecials()
        return (1 if record else 0) * 10 + (record == other)

    assert run_and_validate(fn) == 11


@pytest.mark.parametrize(
    ("record_type", "message"),
    [
        (TerminatingPropertyGetter, "property getter stopped"),
        (TerminatingPropertyMethod, "property method stopped"),
    ],
)
def test_property_backed_special_method_preserves_termination(record_type, message):
    def fn():
        return 1 if record_type() else 0

    with pytest.raises(RuntimeError, match=message):
        run_and_validate(fn)


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


def test_strict_subclass_reflected_binop_has_priority():
    def fn():
        return PriorityBase(1) + PrioritySub(2)

    assert run_and_validate(fn) == 3


def test_compile_time_strict_subclass_reflected_binop_has_priority():
    def fn():
        return COMPTIME_BASE | COMPTIME_SUB

    assert run_and_validate(fn) == 42


def test_compile_time_binop_falls_back_after_not_implemented():
    def fn():
        return COMPTIME_DECLINES | COMPTIME_REFLECTED

    assert run_and_validate(fn) == 43


def test_compile_time_classmethod_binop_is_bound_like_python():
    def fn():
        return COMPTIME_CLASS_METHOD | COMPTIME_BASE

    assert run_and_validate(fn) == 44


def test_compile_time_staticmethod_binop_is_bound_like_python():
    def fn():
        return COMPTIME_STATIC_METHOD | COMPTIME_BASE

    assert run_and_validate(fn) == 45


def test_compile_time_inherited_classmethod_reflected_binop_has_priority():
    def fn():
        return COMPTIME_INHERITED_CLASS_METHOD_BASE | COMPTIME_INHERITED_CLASS_METHOD_SUB

    assert run_and_validate(fn) == 42


def test_compile_time_augmented_assignment_uses_python_negotiation():
    def fn():
        value = int
        value |= str
        return value == int | str

    assert run_and_validate(fn)


def test_compile_time_virtual_subclass_does_not_get_reflected_priority():
    def fn():
        return COMPTIME_VIRTUAL_BASE | COMPTIME_VIRTUAL_SUB

    assert run_and_validate(fn) == 50


def test_compile_time_augmented_assignment_falls_back_to_prioritized_reflected_op():
    def fn():
        value = COMPTIME_INHERITED_CLASS_METHOD_BASE
        value |= COMPTIME_INHERITED_CLASS_METHOD_SUB
        return value

    assert run_and_validate(fn) == 42


def test_compile_time_none_reflected_binop_is_called_like_python():
    def fn():
        return COMPTIME_NONE_REFLECTED_BASE | COMPTIME_NONE_REFLECTED_SUB

    with pytest.raises(TypeError, match="'NoneType' object is not callable"):
        run_and_validate(fn)


def test_builtin_numeric_alias_union_is_symmetric():
    def fn():
        left = Array[Num | int, 1](1)
        right = Array[int | Num, 1](2)
        return left[0] + right[0]

    assert run_and_validate(fn) == 3


def test_strict_subclass_reflected_comparison_has_priority():
    def fn():
        return PriorityBase(1) == PrioritySub(2)

    assert run_and_validate(fn)


def test_strict_subclass_inherited_comparison_has_priority():
    def fn():
        return InheritedEqualityBase(1) == InheritedEqualitySub(1)

    assert run_and_validate(fn)


def test_strict_subclass_reflected_binop_has_priority_after_inplace_fallback():
    def fn():
        value = PriorityBase(1)
        value += PrioritySub(2)
        return value

    assert run_and_validate(fn) == 3


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


class DecliningContains(Record):
    values: Array[int, 3]

    def __contains__(self, value):
        return NotImplemented

    def __iter__(self):
        static_error("membership fell back to iteration")
        return iter(self.values)


class TruthyContainsResult(Record):
    pass


class TruthyContains(Record):
    def __contains__(self, value):
        return TruthyContainsResult()


class ConstantContains(Record):
    def __contains__(self, value):
        return "present" if value else None


class NumericContains(Record):
    result: float

    def __contains__(self, value):
        return self.result


class ConstantNumericContains(Record):
    def __contains__(self, value):
        return 7


class TerminatingContainsResult(Record):
    def __bool__(self):  # noqa: PLE0304
        error("membership truth failed")


class TerminatingContains(Record):
    def __contains__(self, value):
        return TerminatingContainsResult()


@pytest.mark.parametrize(
    ("operation", "message"),
    [
        (lambda value: value + 1, "unsupported operand type"),
        (lambda value: value < 1, "not supported between instances"),
        (lambda value: 1 in value, "is not a container or iterable"),
    ],
)
def test_getattr_does_not_supply_implicit_operator_protocols(operation, message):
    def fn():
        return operation(SynthesizedSpecialMethods())

    # Plain Python rejects each operation too. Use run_compiled because the compiler calls numeric values Num
    # in the otherwise equivalent TypeError, while Python calls the literal operand int.
    with pytest.raises(CompilationError, match=message):
        run_compiled(fn)


def _logged(v):
    debug_log(v)
    return v


def test_in_falls_back_to_iteration_over_a_generator_expression():
    def fn():
        return 6 in (v * 2 for v in Array(1, 2, 3))

    assert run_and_validate(fn)


def test_membership_truth_converts_contains_result():
    def fn():
        return 1 in TruthyContains()

    assert run_and_validate(fn)


def test_membership_truth_converts_constant_contains_result():
    def fn():
        return 1 in ConstantContains() and 0 not in ConstantContains()

    assert run_and_validate(fn)


def test_not_in_truth_converts_contains_result_before_inverting():
    def fn():
        return 1 not in TruthyContains()

    assert not run_and_validate(fn)


def test_membership_normalizes_runtime_numeric_contains_result():
    def fn():
        result = Array(7)[0]
        return 123 in NumericContains(result)

    assert run_and_validate(fn) is True


def test_membership_normalizes_constant_numeric_contains_result():
    def fn():
        return 123 in ConstantNumericContains()

    assert run_and_validate(fn) is True


def test_not_in_normalizes_runtime_numeric_contains_result_before_inverting():
    def fn():
        result = Array(7)[0]
        return 123 not in NumericContains(result)

    assert run_and_validate(fn) is False


def test_nested_membership_is_normalized_inside_an_outer_truth_test():
    def fn():
        result = Array(7)[0]
        return 1 if (123 in NumericContains(result)) == 1 else 0

    assert run_and_validate(fn) == 1


def test_membership_traces_compile_time_record_result_truthiness():
    def fn():
        return 123 in TerminatingContains()

    with pytest.raises(RuntimeError, match="membership truth failed"):
        run_and_validate(fn)


def test_unsupported_matrix_multiplication_reports_operator_error():
    def fn():
        return Plain(1) @ Plain(2)

    with pytest.raises(TypeError, match=re.escape("unsupported operand type(s) for @: 'Plain' and 'Plain'")):
        run_and_validate(fn)


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


def declining_contains_in():
    return 2 in DecliningContains(Array(1, 2, 3))


def declining_contains_not_in():
    return 2 not in DecliningContains(Array(1, 2, 3))


@pytest.mark.parametrize("fn", [declining_contains_in, declining_contains_not_in])
def test_membership_rejects_contains_returning_not_implemented(fn):
    # Python before 3.14 treats NotImplemented as true here, so use the compiler directly to pin the project's
    # version-independent 3.14 semantics.
    with pytest.raises(CompilationError, match="NotImplemented should not be used in a boolean context"):
        run_compiled(fn)


def test_in_short_circuits_at_the_first_match():
    # The log is what pins the short circuit: the third element is never produced.
    def fn():
        return 2 in (_logged(v) for v in Array(1, 2, 3))

    assert run_and_validate(fn)


def test_in_consumes_a_one_shot_iterator_up_to_the_match():
    # This pins _ArrayIterator specifically; the public iterator contract does not promise reuse behavior.
    def fn():
        it = iter(Array(1, 2, 3, 4))
        found = 2 in it
        total = 0
        for v in it:
            total = total * 10 + v
        return (1 if found else 0) * 1000 + total

    assert run_and_validate(fn) == 1034


class MembershipElement(Record):
    value: int

    def __eq__(self, other):
        return isinstance(other, MembershipNeedle) and self.value == other.value


class MembershipNeedle(Record):
    value: int

    def __eq__(self, other):
        return False


def test_iterative_membership_calls_element_equality_first():
    def fn():
        return MembershipNeedle(2) in (value for value in Array(MembershipElement(1), MembershipElement(2)))

    assert run_and_validate(fn)


@pytest.mark.parametrize(
    "fn",
    [
        lambda: callable(range | None),
        lambda: callable(None | range),  # noqa: RUF036 - Reflected-union regression.
        lambda: callable(int | str | range),
    ],
)
def test_compile_time_union_expressions_accept_none_and_existing_unions(fn):
    assert run_and_validate(fn) is False


def test_compile_time_augmented_union_accepts_an_existing_union():
    def fn():
        value = int | str
        value |= range
        return callable(value)

    assert run_and_validate(fn) is False
