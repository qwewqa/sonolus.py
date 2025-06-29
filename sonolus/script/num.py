from __future__ import annotations

import operator
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any, Self, TypeGuard, final, runtime_checkable

from sonolus.backend.ir import IRConst, IRExpr, IRGet, IRPureInstr, IRSet
from sonolus.backend.ops import Op
from sonolus.backend.place import BlockPlace
from sonolus.script.internal.context import ctx
from sonolus.script.internal.error import InternalError
from sonolus.script.internal.impl import meta_fn
from sonolus.script.internal.value import BackingValue, DataValue, ExprBackingValue, Value


class _NumMeta(type):
    def __instancecheck__(cls, instance):
        return isinstance(instance, float | int | bool) or _is_num(instance)


def _is_num(value: Any) -> TypeGuard[Num]:
    """Check if a value is a precisely Num instance."""
    return type.__instancecheck__(Num, value)  # type: ignore # noqa: PLC2801


@final
class _Num(Value, metaclass=_NumMeta):
    # This works for ints, floats, and bools
    # Since we don't support complex numbers, real is equal to the original number
    __match_args__ = ("real",)

    data: DataValue

    def __init__(self, data: DataValue | IRExpr):
        if isinstance(data, complex):
            raise TypeError("Cannot create a Num from a complex number")
        if isinstance(data, int):
            data = float(data)
        if isinstance(data, IRConst | IRPureInstr | IRGet):
            data = ExprBackingValue(data)
        if _is_num(data):
            raise InternalError("Cannot create a Num from a Num")
        if not isinstance(data, BlockPlace | BackingValue | float | int | bool):
            raise TypeError(f"Cannot create a Num from {type(data)}")
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
        return cls(place)

    @classmethod
    def _accepts_(cls, value: Value) -> bool:
        return isinstance(value, Num | float | int | bool)

    @classmethod
    def _accept_(cls, value: Any) -> Self:
        if not cls._accepts_(value):
            raise TypeError(f"Cannot accept {value}")
        if _is_num(value):
            return value
        return cls(value)

    def _is_rom_constant(self) -> bool:
        return (
            ctx()
            and isinstance(self.data, BlockPlace)
            and self.data.block == ctx().blocks.EngineRom
            and isinstance(self.data.index, int)
        )

    def _is_py_(self) -> bool:
        return isinstance(self.data, float | int | bool) or self._is_rom_constant()

    def _as_py_(self) -> Any:
        if not self._is_py_():
            raise ValueError("Not a compile time constant Num")
        if self._is_rom_constant():
            return ctx().rom.get_value(self.data.index + self.data.offset)
        if self.data.is_integer():
            return int(self.data)
        return self.data

    @classmethod
    def _from_list_(cls, values: Iterable[DataValue]) -> Self:
        value = next(iter(values))
        return Num(value)

    def _to_list_(self, level_refs: dict[Any, str] | None = None) -> list[DataValue]:
        return [self.data]

    @classmethod
    def _flat_keys_(cls, prefix: str) -> list[str]:
        return [prefix]

    def _get_(self) -> Self:
        if ctx():
            place = ctx().alloc(size=1)
            if isinstance(self.data, BlockPlace):
                ctx().check_readable(self.data)
            ctx().add_statements(IRSet(place, self.ir()))
            return Num(place)
        else:
            return Num(self.data)

    def _set_(self, value: Self):
        value = Num._accept_(value)
        if ctx():
            match self.data:
                case BackingValue():
                    self.data.write(value.ir())
                case BlockPlace():
                    ctx().check_writable(self.data)
                    ctx().add_statements(IRSet(self.data, value.ir()))
                case _:
                    raise ValueError("Cannot set a read-only value")
        else:
            self.data = value.data

    def _copy_from_(self, value: Self):
        raise ValueError("Cannot assign to a number")

    def _copy_(self) -> Self:
        if ctx():
            return self._get_()
        else:
            return Num(self.data)

    @classmethod
    def _alloc_(cls) -> Self:
        if ctx():
            return Num(ctx().alloc(size=1))
        else:
            return Num(-1)

    @classmethod
    def _zero_(cls) -> Self:
        if ctx():
            result_place = ctx().alloc(size=1)
            ctx().add_statements(IRSet(result_place, IRConst(0)))
            return cls(result_place)
        else:
            return cls(0)

    def ir(self):
        match self.data:
            case BlockPlace():
                return IRGet(self.data)
            case BackingValue():
                return self.data.read()
            case _:
                return IRConst(self.data)

    def index(self) -> int | BlockPlace:
        if isinstance(self.data, BlockPlace):
            return self._get_().data
        return self.data

    def _bin_op(self, other: Self, const_fn: Callable[[Self, Self], Self | None], ir_op: Op) -> Self:
        if not Num._accepts_(other):
            return NotImplemented
        other = Num._accept_(other)
        const_value = const_fn(self, other)
        if const_value is not None:
            return const_value
        if ctx():
            result_place = ctx().alloc(size=1)
            ctx().add_statements(IRSet(result_place, IRPureInstr(ir_op, [self.ir(), other.ir()])))
            return Num(result_place)
        else:
            raise InternalError("Unexpected call on non-comptime Num instance outside a compilation context")

    def _unary_op(self, py_fn: Callable[[float], float], ir_op: Op) -> Self:
        if self._is_py_():
            return Num(py_fn(self._as_py_()))
        elif ctx():
            result_place = ctx().alloc(size=1)
            ctx().add_statements(IRSet(result_place, IRPureInstr(ir_op, [self.ir()])))
            return Num(result_place)
        else:
            raise InternalError("Unexpected call on non-comptime Num instance outside a compilation context")

    @meta_fn
    def __eq__(self, other) -> Self:
        def const_fn(a: Self, b: Self) -> Num | None:
            if a._is_py_() and b._is_py_():
                return Num(a._as_py_() == b._as_py_())
            if a.data == b.data:
                return Num(True)
            return None

        return self._bin_op(other, const_fn, Op.Equal)

    def __hash__(self):
        if self._is_py_():
            return hash(self._as_py_())
        raise ValueError("Cannot hash non compile time constant Num")

    @meta_fn
    def __ne__(self, other) -> Self:
        def const_fn(a: Self, b: Self) -> Num | None:
            if a._is_py_() and b._is_py_():
                return Num(a._as_py_() != b._as_py_())
            if a.data == b.data:
                return Num(False)
            return None

        return self._bin_op(other, const_fn, Op.NotEqual)

    @meta_fn
    def __lt__(self, other) -> Self:
        def const_fn(a: Self, b: Self) -> Num | None:
            if a._is_py_() and b._is_py_():
                return Num(a._as_py_() < b._as_py_())
            if a.data == b.data:
                return Num(False)
            return None

        return self._bin_op(other, const_fn, Op.Less)

    @meta_fn
    def __le__(self, other) -> Self:
        def const_fn(a: Self, b: Self) -> Num | None:
            if a._is_py_() and b._is_py_():
                return Num(a._as_py_() <= b._as_py_())
            if a.data == b.data:
                return Num(True)
            return None

        return self._bin_op(other, const_fn, Op.LessOr)

    @meta_fn
    def __gt__(self, other) -> Self:
        def const_fn(a: Self, b: Self) -> Num | None:
            if a._is_py_() and b._is_py_():
                return Num(a._as_py_() > b._as_py_())
            if a.data == b.data:
                return Num(False)
            return None

        return self._bin_op(other, const_fn, Op.Greater)

    @meta_fn
    def __ge__(self, other) -> Self:
        def const_fn(a: Self, b: Self) -> Num | None:
            if a._is_py_() and b._is_py_():
                return Num(a._as_py_() >= b._as_py_())
            if a.data == b.data:
                return Num(True)
            return None

        return self._bin_op(other, const_fn, Op.GreaterOr)

    @meta_fn
    def __add__(self, other) -> Self:
        def const_fn(a: Self, b: Self) -> Num | None:
            if a._is_py_() and b._is_py_():
                return Num(a._as_py_() + b._as_py_())
            if a._is_py_() and a._as_py_() == 0:
                return b
            if b._is_py_() and b._as_py_() == 0:
                return a
            return None

        return self._bin_op(other, const_fn, Op.Add)

    @meta_fn
    def __sub__(self, other) -> Self:
        def const_fn(a: Self, b: Self) -> Num | None:
            if a._is_py_() and b._is_py_():
                return Num(a._as_py_() - b._as_py_())
            if a._is_py_() and a._as_py_() == 0:
                return -b
            if b._is_py_() and b._as_py_() == 0:
                return a
            return None

        return self._bin_op(other, const_fn, Op.Subtract)

    @meta_fn
    def __mul__(self, other) -> Self:
        def const_fn(a: Self, b: Self) -> Num | None:
            if a._is_py_() and b._is_py_():
                return Num(a._as_py_() * b._as_py_())
            if a._is_py_():
                if a._as_py_() == 0:
                    return Num(0)
                if a._as_py_() == 1:
                    return b
            if b._is_py_():
                if b._as_py_() == 0:
                    return Num(0)
                if b._as_py_() == 1:
                    return a
            return None

        return self._bin_op(other, const_fn, Op.Multiply)

    @meta_fn
    def __truediv__(self, other) -> Self:
        def const_fn(a: Self, b: Self) -> Num | None:
            if a._is_py_() and b._is_py_():
                if b._as_py_() == 0:
                    return None
                return Num(a._as_py_() / b._as_py_())
            if b._is_py_():
                if b._as_py_() == 1:
                    return a
                if b._as_py_() == -1:
                    return -a
            return None

        return self._bin_op(other, const_fn, Op.Divide)

    @meta_fn
    def __floordiv__(self, other) -> Self:
        def const_fn(a: Self, b: Self) -> Num | None:
            if a._is_py_() and b._is_py_():
                if b._as_py_() == 0:
                    return None
                return Num(a._as_py_() // b._as_py_())
            if b._is_py_():
                if b._as_py_() == 1:
                    return a
                if b._as_py_() == -1:
                    return -a
            return None

        return self._bin_op(other, const_fn, Op.Divide)._unary_op(lambda x: x, Op.Floor)

    @meta_fn
    def __mod__(self, other) -> Self:
        def const_fn(a: Self, b: Self) -> Num | None:
            if a._is_py_() and b._is_py_():
                if b._as_py_() == 0:
                    return None
                return Num(a._as_py_() % b._as_py_())
            return None

        return self._bin_op(other, const_fn, Op.Mod)

    @meta_fn
    def __pow__(self, other) -> Self:
        def const_fn(a: Self, b: Self) -> Num | None:
            if a._is_py_() and b._is_py_():
                try:
                    return Num(a._as_py_() ** b._as_py_())
                except OverflowError:
                    return None
            if b._is_py_():
                if b._as_py_() == 0:
                    return Num(1)
                if b._as_py_() == 1:
                    return a
            return None

        return self._bin_op(other, const_fn, Op.Power)

    @meta_fn
    def __neg__(self) -> Self:
        return self._unary_op(operator.neg, Op.Negate)

    @meta_fn
    def __pos__(self) -> Self:
        return self

    @meta_fn
    def __abs__(self) -> Self:
        return self._unary_op(abs, Op.Abs)

    @meta_fn
    def __bool__(self):
        if ctx():
            return self != 0
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


if TYPE_CHECKING:
    from typing import Protocol

    @runtime_checkable
    class Num[T](Protocol, int, bool, float):
        def __add__(self, other: T, /) -> Num: ...
        def __sub__(self, other: T, /) -> Num: ...
        def __mul__(self, other: T, /) -> Num: ...
        def __truediv__(self, other: T, /) -> Num: ...
        def __floordiv__(self, other: T, /) -> Num: ...
        def __mod__(self, other: T, /) -> Num: ...
        def __pow__(self, other: T, /) -> Num: ...

        def __neg__(self, /) -> Num: ...
        def __pos__(self, /) -> Num: ...
        def __abs__(self, /) -> Num: ...

        def __eq__(self, other: Any, /) -> bool: ...
        def __ne__(self, other: Any, /) -> bool: ...
        def __lt__(self, other: T, /) -> bool: ...
        def __le__(self, other: T, /) -> bool: ...
        def __gt__(self, other: T, /) -> bool: ...
        def __ge__(self, other: T, /) -> bool: ...

        def __hash__(self, /) -> int: ...

        @property
        def real(self) -> Num: ...

        @property
        def imag(self) -> Num: ...
else:
    # Need to do this to satisfy type checkers (especially Pycharm)
    _Num.__name__ = "Num"
    _Num.__qualname__ = "Num"
    globals()["Num"] = _Num
