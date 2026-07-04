# type: ignore
from __future__ import annotations

import operator
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any, Self, TypeGuard, final, runtime_checkable

from sonolus.backend.blocks import BlockData
from sonolus.backend.ir import IRConst, IRExpr, IRGet, IRPureInstr, IRSet
from sonolus.backend.ops import Op
from sonolus.backend.place import BlockPlace
from sonolus.script.internal.context import ctx
from sonolus.script.internal.error import InternalError
from sonolus.script.internal.simple_meta_fn import simple_meta_fn
from sonolus.script.internal.value import BackingValue, DataValue, ExprBackingValue, Value

# Hoisted tuples for isinstance checks; building `A | B` unions inline allocates a new
# types.UnionType on every call, while tuples are constant and semantically identical.
_IR_EXPR_TYPES = (IRConst, IRPureInstr, IRGet)
_FLOAT_INT_BOOL = (float, int, bool)
_FLOAT_INT = (float, int)


class _NumMeta(type):
    def __instancecheck__(cls, instance):
        return isinstance(instance, _FLOAT_INT_BOOL) or _is_num(instance)


def _is_num(value: Any) -> TypeGuard[Num]:
    """Check if a value is a precisely Num instance."""
    return type(value) is _Num  # _Num is final, so an exact type check is equivalent


def _coerce_num(other: Any) -> Num | None:
    """Coerce an operand to a Num for arithmetic, or return None if it is not an accepted numeric type."""
    if type(other) is Num:
        return other
    if isinstance(other, _FLOAT_INT_BOOL):
        return Num(other)
    return None


@final
class _Num(Value, metaclass=_NumMeta):
    __slots__ = ("data",)

    # This works for ints, floats, and bools
    # Since we don't support complex numbers, real is equal to the original number
    __match_args__ = ("real",)

    data: DataValue

    def __init__(self, data: DataValue | IRExpr):
        if type(data) is not float:  # Fast path: exact floats need no normalization
            if isinstance(data, int):
                data = float(data)
            elif isinstance(data, _IR_EXPR_TYPES):
                data = ExprBackingValue(data)
        self.data = data

    def __str__(self) -> str:
        if isinstance(self.data, float) and self.data.is_integer():
            return str(int(self.data))
        return str(self.data)

    def __repr__(self) -> str:
        return f"Num({self.data})"

    @classmethod
    def _is_concrete_(cls) -> bool:
        return True

    @classmethod
    def _size_(cls) -> int:
        return 1

    @classmethod
    def _is_value_type_(cls) -> bool:
        return True

    @classmethod
    def _from_place_(cls, place: BlockPlace) -> Self:
        return _num_of(place)

    @classmethod
    def _accepts_(cls, value: Value) -> bool:
        return type(value) is Num or isinstance(value, _FLOAT_INT_BOOL)

    @classmethod
    def _accept_(cls, value: Any) -> Self:
        if type(value) is Num:
            return value
        if isinstance(value, _FLOAT_INT_BOOL):
            return cls(value)
        raise TypeError(f"Cannot accept {value}")

    def _is_rom_constant(self) -> bool:
        d = self.data
        if type(d) is not BlockPlace:
            return False
        c = ctx()
        return c is not None and d.block == c.blocks.EngineRom and isinstance(d.index, int)

    def _is_py_(self) -> bool:
        d = self.data
        return type(d) is float or isinstance(d, _FLOAT_INT) or self._is_rom_constant()

    def _as_py_(self) -> Any:
        d = self.data
        if type(d) is float:
            return int(d) if d.is_integer() else d
        if self._is_rom_constant():
            return ctx().rom.get_value(d.index + d.offset)
        if isinstance(d, _FLOAT_INT):
            return int(d) if d.is_integer() else d
        raise ValueError("Not a compile time constant Num")

    def _as_py_or_none(self) -> int | bool | float | None:
        d = self.data
        if type(d) is float:
            return int(d) if d.is_integer() else d
        if self._is_py_():
            return self._as_py_()
        return None

    @classmethod
    def _from_list_(cls, values: Iterable[DataValue]) -> Self:
        value = next(iter(values))
        return Num(value)

    def _to_list_(self, level_refs: dict[Any, str] | None = None) -> list[DataValue]:
        return [self._as_py_() if self._is_py_() else self.data]

    @classmethod
    def _flat_keys_(cls, prefix: str) -> list[str]:
        return [prefix]

    def _get_(self) -> Self:
        c = ctx()
        if c:
            d = self.data
            if isinstance(d, BlockPlace):
                c.check_readable(d)
            place = c.alloc(size=1)
            c.add_statements(IRSet(place, self.ir()))
            return _num_of(place)
        else:
            return Num(self.data)

    def _get_readonly_(self) -> Self:
        c = ctx()
        if c:
            d = self.data
            if isinstance(d, BlockPlace):
                c.check_readable(d)
                if isinstance(d.block, BlockData) and not c.is_writable(d):
                    # This block is immutable in the current callback, so no need to copy it in case it changes.
                    return _num_of(d)
            if type(d) is float:
                return _num_of(d)
            if isinstance(d, _FLOAT_INT_BOOL):
                return Num(d)
            place = c.alloc(size=1)
            c.add_statements(IRSet(place, self.ir()))
            return _num_of(place)
        else:
            return Num(self.data)

    def _set_(self, value: Any):
        value = Num._accept_(value)
        c = ctx()
        if c:
            match self.data:
                case BackingValue():
                    self.data.write(value.ir())
                case BlockPlace():
                    c.check_writable(self.data)
                    c.add_statements(IRSet(self.data, value.ir()))
                case _:
                    raise ValueError("Cannot set a read-only value")
        else:
            self.data = value.data

    def _copy_from_(self, value: Any):
        raise ValueError("Cannot assign to a number")

    def _copy_(self) -> Self:
        if ctx():
            return self._get_()
        else:
            return Num(self.data)

    @classmethod
    def _alloc_(cls) -> Self:
        c = ctx()
        if c:
            return _num_of(c.alloc(size=1))
        else:
            return Num(-1)

    @classmethod
    def _zero_(cls) -> Self:
        c = ctx()
        if c:
            result_place = c.alloc(size=1)
            c.add_statements(IRSet(result_place, IRConst(0)))
            return _num_of(result_place)
        else:
            return cls(0)

    def ir(self):
        d = self.data
        if isinstance(d, BlockPlace):
            return IRGet(d)
        if isinstance(d, BackingValue):
            return d.read()
        return IRConst(d)

    def index(self) -> int | BlockPlace:
        if isinstance(self.data, BlockPlace):
            return self._get_().data
        return self.data

    def _bin_op(
        self, other: Self, const_fn: Callable[[Self, Self], Self | None], ir_op: Op, fallback=NotImplemented
    ) -> Self:
        # Same coercion as _coerce_num, kept inline: this is the hottest compile-time path and the
        # extra function call measures as a regression here (unlike the reflected ops).
        if type(other) is not Num:
            if not isinstance(other, _FLOAT_INT_BOOL):
                return fallback
            other = Num(other)
        const_value = const_fn(self, other)
        if const_value is not None:
            return const_value
        c = ctx()
        if c:
            result_place = c.alloc(size=1)
            c.add_statements(IRSet(result_place, IRPureInstr(ir_op, [self.ir(), other.ir()])))
            return _num_of(result_place)
        else:
            raise InternalError("Unexpected call on non-comptime Num instance outside a compilation context")

    def _unary_op(self, py_fn: Callable[[float], float], ir_op: Op) -> Self:
        if self._is_py_():
            return Num(py_fn(self._as_py_()))
        c = ctx()
        if c:
            result_place = c.alloc(size=1)
            c.add_statements(IRSet(result_place, IRPureInstr(ir_op, [self.ir()])))
            return _num_of(result_place)
        else:
            raise InternalError("Unexpected call on non-comptime Num instance outside a compilation context")

    @simple_meta_fn
    def __eq__(self, other) -> Self:
        def const_fn(a: Self, b: Self) -> Num | None:
            if a._is_py_() and b._is_py_():
                return Num(a._as_py_() == b._as_py_())
            if a.data == b.data:
                return Num(True)
            return None

        return self._bin_op(other, const_fn, Op.Equal, fallback=0)

    def __hash__(self):
        if self._is_py_():
            return hash(self._as_py_())
        raise ValueError("Cannot hash non compile time constant Num")

    @simple_meta_fn
    def __ne__(self, other) -> Self:
        def const_fn(a: Self, b: Self) -> Num | None:
            if a._is_py_() and b._is_py_():
                return Num(a._as_py_() != b._as_py_())
            if a.data == b.data:
                return Num(False)
            return None

        return self._bin_op(other, const_fn, Op.NotEqual, fallback=1)

    @simple_meta_fn
    def __lt__(self, other) -> Self:
        def const_fn(a: Self, b: Self) -> Num | None:
            if a._is_py_() and b._is_py_():
                return Num(a._as_py_() < b._as_py_())
            if a.data == b.data:
                return Num(False)
            return None

        return self._bin_op(other, const_fn, Op.Less)

    @simple_meta_fn
    def __le__(self, other) -> Self:
        def const_fn(a: Self, b: Self) -> Num | None:
            if a._is_py_() and b._is_py_():
                return Num(a._as_py_() <= b._as_py_())
            if a.data == b.data:
                return Num(True)
            return None

        return self._bin_op(other, const_fn, Op.LessOr)

    @simple_meta_fn
    def __gt__(self, other) -> Self:
        def const_fn(a: Self, b: Self) -> Num | None:
            if a._is_py_() and b._is_py_():
                return Num(a._as_py_() > b._as_py_())
            if a.data == b.data:
                return Num(False)
            return None

        return self._bin_op(other, const_fn, Op.Greater)

    @simple_meta_fn
    def __ge__(self, other) -> Self:
        def const_fn(a: Self, b: Self) -> Num | None:
            if a._is_py_() and b._is_py_():
                return Num(a._as_py_() >= b._as_py_())
            if a.data == b.data:
                return Num(True)
            return None

        return self._bin_op(other, const_fn, Op.GreaterOr)

    @simple_meta_fn
    def __add__(self, other) -> Self:
        def const_fn(a: Self, b: Self) -> Num | None:
            a_py = a._as_py_or_none()
            b_py = b._as_py_or_none()
            if a_py is not None and b_py is not None:
                return Num(a_py + b_py)
            if a_py == 0:
                return b
            if b_py == 0:
                return a
            return None

        return self._bin_op(other, const_fn, Op.Add)

    @simple_meta_fn
    def __sub__(self, other) -> Self:
        def const_fn(a: Self, b: Self) -> Num | None:
            a_py = a._as_py_or_none()
            b_py = b._as_py_or_none()
            if a_py is not None and b_py is not None:
                return Num(a_py - b_py)
            if a_py == 0:
                return -b
            if b_py == 0:
                return a
            return None

        return self._bin_op(other, const_fn, Op.Subtract)

    @simple_meta_fn
    def __mul__(self, other) -> Self:
        def const_fn(a: Self, b: Self) -> Num | None:
            a_py = a._as_py_or_none()
            b_py = b._as_py_or_none()
            if a_py is not None and b_py is not None:
                return Num(a_py * b_py)
            if a_py == 0 or b_py == 0:
                return Num(0)
            if a_py == 1:
                return b
            if b_py == 1:
                return a
            return None

        return self._bin_op(other, const_fn, Op.Multiply)

    @simple_meta_fn
    def __truediv__(self, other) -> Self:
        def const_fn(a: Self, b: Self) -> Num | None:
            a_py = a._as_py_or_none()
            b_py = b._as_py_or_none()
            if a_py is not None and b_py is not None:
                if b_py == 0:
                    return None
                return Num(a_py / b_py)
            if b_py == 1:
                return a
            if b_py == -1:
                return -a
            return None

        return self._bin_op(other, const_fn, Op.Divide)

    @simple_meta_fn
    def __floordiv__(self, other) -> Self:
        def const_fn(a: Self, b: Self) -> Num | None:
            a_py = a._as_py_or_none()
            b_py = b._as_py_or_none()
            if a_py is not None and b_py is not None:
                if b_py == 0:
                    return None
                return Num(a_py // b_py)
            if b_py == 1:
                return a
            if b_py == -1:
                return -a
            return None

        return self._bin_op(other, const_fn, Op.Divide)._unary_op(lambda x: x, Op.Floor)

    @simple_meta_fn
    def __mod__(self, other) -> Self:
        def const_fn(a: Self, b: Self) -> Num | None:
            a_py = a._as_py_or_none()
            b_py = b._as_py_or_none()
            if a_py is not None and b_py is not None:
                if b_py == 0:
                    return None
                return Num(a_py % b_py)
            return None

        return self._bin_op(other, const_fn, Op.Mod)

    @simple_meta_fn
    def __pow__(self, other) -> Self:
        def const_fn(a: Self, b: Self) -> Num | None:
            a_py = a._as_py_or_none()
            b_py = b._as_py_or_none()
            if a_py is not None and b_py is not None:
                try:
                    return Num(a_py**b_py)
                except OverflowError:
                    return None
            if b_py == 0:
                return Num(1)
            if b_py == 1:
                return a
            return None

        return self._bin_op(other, const_fn, Op.Power)

    @simple_meta_fn
    def __radd__(self, other) -> Self:
        other = _coerce_num(other)
        if other is None:
            return NotImplemented
        return other.__add__(self)

    @simple_meta_fn
    def __rsub__(self, other) -> Self:
        other = _coerce_num(other)
        if other is None:
            return NotImplemented
        return other.__sub__(self)

    @simple_meta_fn
    def __rmul__(self, other) -> Self:
        other = _coerce_num(other)
        if other is None:
            return NotImplemented
        return other.__mul__(self)

    @simple_meta_fn
    def __rtruediv__(self, other) -> Self:
        other = _coerce_num(other)
        if other is None:
            return NotImplemented
        return other.__truediv__(self)

    @simple_meta_fn
    def __rfloordiv__(self, other) -> Self:
        other = _coerce_num(other)
        if other is None:
            return NotImplemented
        return other.__floordiv__(self)

    @simple_meta_fn
    def __rmod__(self, other) -> Self:
        other = _coerce_num(other)
        if other is None:
            return NotImplemented
        return other.__mod__(self)

    @simple_meta_fn
    def __rpow__(self, other) -> Self:
        other = _coerce_num(other)
        if other is None:
            return NotImplemented
        return other.__pow__(self)

    @simple_meta_fn
    def __neg__(self) -> Self:
        return self._unary_op(operator.neg, Op.Negate)

    @simple_meta_fn
    def __pos__(self) -> Self:
        return self

    @simple_meta_fn
    def __abs__(self) -> Self:
        return self._unary_op(abs, Op.Abs)

    @simple_meta_fn
    def __bool__(self):
        if ctx():
            result = self != 0
            if result._is_py_():
                return bool(result._as_py_())
            else:
                return result
        else:
            if self._is_py_():
                return bool(self._as_py_())
            raise ValueError("Cannot convert non compile time constant Num to bool")

    def and_(self, other) -> Self:
        def const_fn(a: Self, b: Self) -> Num | None:
            if a._is_py_() and b._is_py_():
                return Num(a._as_py_() and b._as_py_())
            if a._is_py_():
                if a._as_py_() == 0:
                    return a
                else:
                    return b
            return None

        return self._bin_op(other, const_fn, Op.And)

    def or_(self, other) -> Self:
        def const_fn(a: Self, b: Self) -> Num | None:
            if a._is_py_() and b._is_py_():
                return Num(a._as_py_() or b._as_py_())
            if a._is_py_():
                if a._as_py_() == 0:
                    return b
                else:
                    return a
            return None

        return self._bin_op(other, const_fn, Op.Or)

    def not_(self) -> Self:
        return self._unary_op(operator.not_, Op.Not)

    @property
    def real(self) -> Self:
        return self

    @property
    def imag(self) -> Self:
        return 0


def _create_num_raw(i: int) -> Num:
    result = object.__new__(_Num)
    result.data = float(i)
    return result


def _num_of(data: DataValue) -> Num:
    """Create a Num from data known to already be normalized (a float, BlockPlace, or BackingValue).

    Trusted fast path for internal callers; bypasses the validation in __init__.
    """
    result = object.__new__(_Num)
    result.data = data
    return result


if TYPE_CHECKING:
    from typing import Protocol

    @runtime_checkable
    class Num(Protocol, int, bool, float):
        def __add__(self, other: Any, /) -> Num | int | bool | float: ...
        def __sub__(self, other: Any, /) -> Num | int | bool | float: ...
        def __mul__(self, other: Any, /) -> Num | int | bool | float: ...
        def __truediv__(self, other: Any, /) -> Num | int | bool | float: ...
        def __floordiv__(self, other: Any, /) -> Num | int | bool | float: ...
        def __mod__(self, other: Any, /) -> Num | int | bool | float: ...
        def __pow__(self, other: Any, /) -> Num | int | bool | float: ...

        def __neg__(self, /) -> Num | int | bool | float: ...
        def __pos__(self, /) -> Num | int | bool | float: ...
        def __abs__(self, /) -> Num | int | bool | float: ...

        def __eq__(self, other: Any, /) -> bool: ...
        def __ne__(self, other: Any, /) -> bool: ...
        def __lt__(self, other: Any, /) -> bool: ...
        def __le__(self, other: Any, /) -> bool: ...
        def __gt__(self, other: Any, /) -> bool: ...
        def __ge__(self, other: Any, /) -> bool: ...

        def __hash__(self, /) -> int: ...

        @property
        def real(self) -> Num | int | bool | float: ...

        @property
        def imag(self) -> Num | int | bool | float: ...
else:
    # Need to do this to satisfy type checkers (especially Pycharm)
    _Num.__name__ = "Num"
    _Num.__qualname__ = "Num"
    globals()["Num"] = _Num
