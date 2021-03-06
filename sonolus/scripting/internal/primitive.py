from __future__ import annotations

import warnings
from typing import Optional, Any, Sequence, Type, TYPE_CHECKING, Union

from sonolus.backend.ir import (
    IRSet,
    IRNode,
    IRGet,
    IRConst,
    IRFunc,
    Location,
    IRValueType,
)
from sonolus.scripting.internal.sls_func import sls_func
from sonolus.backend.evaluation import CompilationInfo
from sonolus.scripting.internal.control_flow import Execute, If
from sonolus.scripting.internal.value import Value, convert_value
from sonolus.scripting.internal.void import Void


class Primitive(Value):
    _size_: int = 1

    def __init__(self, value: float | IRNode = 0):
        super().__init__()
        value = isinstance(value, IRNode) and value.constant() or value
        self._value_ = value
        self._check_readable()
        self._attributes_.is_static = isinstance(value, (float, int, bool))

    def _assign_(self, value: Primitive) -> Void:
        value = convert_value(value, type(self))
        match self._value_:
            case Location() as loc:
                self._check_writable()
                return Void(IRSet(loc, value.ir()))._set_parent_(Execute(self, value))
            case _:
                raise ValueError("Cannot assign to a constant.")

    def _flatten_(self) -> list[IRNode]:
        return [self.ir()]

    @classmethod
    def _from_flat_(cls, flat):
        return cls(flat[0])

    def constant(self):
        match self._value_:
            case int() | float() | bool() as constant:
                return constant
            case IRNode() as node:
                return node.constant()
            case _:
                return None

    def ir(self) -> IRValueType:
        match self._value_:
            case Location() as loc:
                return IRGet(loc)
            case int() | float() | bool() as constant:
                return IRConst(float(constant))
            case IRNode() as node:
                return node
            case _:
                raise TypeError("Unexpected value.")

    def __eq__(self, other):
        other = convert_value(other, type(self))
        truthiness = self is other
        result = (
            Bool._create_(IRFunc("Equal", [self.ir(), other.ir()]))
            ._set_parent_(Execute(self, other))
            .copy()
        )
        result.override_truthiness = truthiness
        return result

    def __ne__(self, other):
        other = convert_value(other, type(self))
        truthiness = self is not other
        result = (
            Bool._create_(IRFunc("NotEqual", [self.ir(), other.ir()]))
            ._set_parent_(Execute(self, other))
            .copy()
        )
        result.override_truthiness = truthiness
        return result

    def __hash__(self):
        return id(self)

    @classmethod
    def _create_(cls, value: Location | Any, attributes=None):
        result: cls = super()._create_(value, attributes)  # type: ignore
        result._check_readable()
        return result

    def _const_evaluate_(self, runner):
        self._was_evaluated_ = True
        return type(self)(runner(self.ir()))._set_static_()

    def _check_readable(self):
        if CompilationInfo._current is None:
            return
        if not isinstance(self._value_, Location):
            return
        callback_type = CompilationInfo._current.callback
        if (
            isinstance(self._value_.ref, int)
            and self._value_.ref not in callback_type.readable_blocks
        ):
            warnings.warn(
                f"Potential invalid read from block {self._value_.ref} in callback {callback_type.name}."
            )

    def _check_writable(self):
        if CompilationInfo._current is None:
            return
        if not isinstance(self._value_, Location):
            return
        callback_type = CompilationInfo._current.callback
        if (
            isinstance(self._value_.ref, int)
            and self._value_.ref not in callback_type.writable_blocks
        ):
            warnings.warn(
                f"Potential invalid write to block {self._value_.ref} in callback {callback_type.name}."
            )

    def __str__(self):
        return f"{self._value_}"


class Boolean(Primitive):
    _is_concrete_ = True

    def __init__(self, value=False, /, *, override_truthiness: Optional[bool] = None):
        if not isinstance(value, (bool, IRNode)) and value not in (0, 1):
            raise TypeError("Expected a bool.")
        super().__init__(value)
        self.override_truthiness = override_truthiness

    @classmethod
    def _create_(cls, value: Location | Any, attributes=None):
        result = super()._create_(value, attributes)
        result.override_truthiness = None
        return result

    @classmethod
    def _convert_(cls, value):
        match value:
            case cls():
                return value
            case bool():
                return cls(value)
            case _:
                return NotImplemented

    def __bool__(self):
        return True if self.override_truthiness is None else self.override_truthiness

    def and_(self, other):
        lhs = convert_value(self, Bool)
        rhs = convert_value(other, Bool)
        result = Execute(
            result := +Bool(), If(lhs, result @ rhs, result @ False), result
        )
        result.override_float_value = bool(lhs.constant() and rhs.constant())
        return result

    def or_(self, other):
        lhs = convert_value(self, Bool)
        rhs = convert_value(other, Bool)
        result = Execute(
            result := +Bool(), If(lhs, result @ True, result @ rhs), result
        )
        result.override_float_value = bool(lhs.constant() or rhs.constant())
        return result

    @classmethod
    def not_(cls, value):
        if isinstance(value, cls):
            return ~value
        else:
            return not Value

    @sls_func(ast=False)
    def __and__(self, other) -> Bool:
        return invoke_builtin("And", [self, other], Bool)

    @sls_func(ast=False)
    def __rand__(self, other) -> Bool:
        return invoke_builtin("And", [other, self], Bool)

    @sls_func(ast=False)
    def __or__(self, other) -> Bool:
        return invoke_builtin("Or", [self, other], Bool)

    @sls_func(ast=False)
    def __ror__(self, other) -> Bool:
        return invoke_builtin("Or", [other, self], Bool)

    @sls_func(ast=False)
    def __invert__(self) -> Bool:
        # Note: differs from typical Python behavior (which treats bools as numbers)
        result = invoke_builtin("Not", [self], Bool)
        result.override_float_value = (
            not self.override_truthiness
            if self.override_truthiness is not None
            else None
        )
        return result

    def _dump_(self):
        value = self.constant()
        if value is None:
            raise ValueError("Boolean does not have a constant value.")
        return bool(value)


class Number(Primitive):
    """
    A floating-point number.
    NaN and infinities may be represented, but behavior is undefined
    for some operations.
    """

    _is_concrete_ = True

    def __init__(self, value=0, /, *, override_float_value: Optional[float] = None):
        if not isinstance(value, (int, float, IRNode)):
            raise TypeError("Expected an int or float.")
        super().__init__(value)
        self.override_float_value = override_float_value

    @classmethod
    def _create_(cls, value: Location | Any, attributes=None):
        result = super()._create_(value, attributes)
        result.override_float_value = None
        return result

    @classmethod
    def _convert_(cls, value):
        match value:
            case cls():
                return value
            case float() | int():
                return cls(value)
            case _:
                return NotImplemented

    def __int__(self):
        return int(float(self))

    def __float__(self):
        if self.override_float_value is not None:
            value = self.override_float_value
        elif const_value := self.constant():
            value = const_value
        else:
            raise ValueError("Number does not have an associated value.")
        return value

    def __index__(self):
        value = float(self)
        if not value.is_integer():
            raise ValueError("Number has a non-integer value.")
        return int(value)

    @sls_func(ast=False)
    def __add__(self: Num, other: Num) -> Num:
        return invoke_builtin("Add", [self, other], Num)

    @sls_func(ast=False)
    def __radd__(self: Num, other: Num) -> Num:
        return invoke_builtin("Add", [other, self], Num)

    @sls_func(ast=False)
    def __sub__(self: Num, other: Num) -> Num:
        return invoke_builtin("Subtract", [self, other], Num)

    @sls_func(ast=False)
    def __rsub__(self: Num, other: Num) -> Num:
        return invoke_builtin("Subtract", [other, self], Num)

    @sls_func(ast=False)
    def __mul__(self: Num, other: Num) -> Num:
        return invoke_builtin("Multiply", [self, other], Num)

    @sls_func(ast=False)
    def __rmul__(self: Num, other: Num) -> Num:
        return invoke_builtin("Multiply", [other, self], Num)

    @sls_func(ast=False)
    def __truediv__(self: Num, other: Num) -> Num:
        return invoke_builtin("Divide", [self, other], Num)

    @sls_func(ast=False)
    def __rtruediv__(self: Num, other: Num) -> Num:
        return invoke_builtin("Divide", [other, self], Num)

    @sls_func(ast=False)
    def __floordiv__(self: Num, other: Num) -> Num:
        return invoke_builtin("Floor", [self / other], Num)

    @sls_func(ast=False)
    def __rfloordiv__(self: Num, other: Num) -> Num:
        return invoke_builtin("Floor", [other / self], Num)

    @sls_func(ast=False)
    def __mod__(self: Num, other: Num) -> Num:
        return invoke_builtin("Mod", [self, other], Num)

    @sls_func(ast=False)
    def __rmod__(self: Num, other: Num) -> Num:
        return invoke_builtin("Mod", [other, self], Num)

    @sls_func(ast=False)
    def __pow__(self: Num, other: Num) -> Num:
        return invoke_builtin("Power", [self, other], Num)

    @sls_func(ast=False)
    def __rpow__(self: Num, other: Num) -> Num:
        return invoke_builtin("Power", [other, self], Num)

    @sls_func(ast=False)
    def __gt__(self: Num, other: Num) -> Bool:
        return invoke_builtin("Greater", [self, other], Bool)

    @sls_func(ast=False)
    def __ge__(self: Num, other: Num) -> Bool:
        return invoke_builtin("GreaterOr", [self, other], Bool)

    @sls_func(ast=False)
    def __lt__(self: Num, other: Num) -> Bool:
        return invoke_builtin("Less", [self, other], Bool)

    @sls_func(ast=False)
    def __le__(self: Num, other: Num) -> Bool:
        return invoke_builtin("LessOr", [self, other], Bool)

    @sls_func(ast=False)
    def __neg__(self) -> Num:
        return 0 - self

    @sls_func(ast=False)
    def __abs__(self) -> Num:
        return invoke_builtin("Abs", [self], Num)

    @sls_func(ast=False)
    def __round__(self, n=None):
        if n not in {None, 0}:
            raise NotImplementedError("Rounding with a precision is not supported.")
        return invoke_builtin("Round", [self], Num)

    @sls_func(ast=False)
    def to_boolean(self) -> Bool:
        return self != 0

    def _dump_(self):
        value = self.constant()
        if value is None:
            raise ValueError("Number does not have a constant value.")
        return float(value)


def invoke_builtin(
    name: str, arguments: Sequence[Primitive], type_: Type[Primitive] = None
) -> Any:
    node = IRFunc(name=name, args=[arg.ir() for arg in arguments])
    if type_ is None:
        return Void(node=node)._set_parent_(Execute(*arguments))
    else:
        return type_._allocate_(
            type_._create_(value=node)._set_parent_(Execute(*arguments))
        )


if not TYPE_CHECKING:
    Bool = Boolean
    Num = Number
else:
    # Pycharm doesn't like the | syntax for some reason here
    # and fails to recognize the result properly.
    Bool = Union[Boolean, bool]
    Num = Union[Number, float, int]
