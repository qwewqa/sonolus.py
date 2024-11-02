# ruff: noqa: N801
import operator
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any, Self, final

from sonolus.backend.ir import IRConst, IRGet, IRPureInstr, IRSet
from sonolus.backend.ops import Op
from sonolus.backend.place import BlockPlace, Place
from sonolus.script.internal.context import ctx
from sonolus.script.internal.error import InternalError
from sonolus.script.internal.impl import meta_fn
from sonolus.script.internal.value import Value


@final
class _Num(Value):
    data: BlockPlace | float | int | bool

    def __init__(self, data: Place | float | int | bool):
        if isinstance(data, int):
            data = float(data)
        if isinstance(data, _Num):
            raise InternalError("Cannot create a Num from a Num")
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
        if isinstance(value, Num):
            return value
        return cls(value)

    def _is_py_(self) -> bool:
        return not isinstance(self.data, BlockPlace)

    def _as_py_(self) -> Any:
        if not self._is_py_():
            raise ValueError("Not a compile time constant Num")
        if self.data.is_integer():
            return int(self.data)
        return self.data

    @classmethod
    def _from_list_(cls, values: Iterable[float | BlockPlace]) -> Self:
        value = next(iter(values))
        return Num(value)

    def _to_list_(self) -> list[float | BlockPlace]:
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
        if ctx():
            if not isinstance(self.data, BlockPlace):
                raise ValueError("Cannot set a compile time constant value")
            ctx().check_writable(self.data)
            ctx().add_statements(IRSet(self.data, value.ir()))
        else:
            self.data = value.data

    def _copy_from_(self, value: Self):
        raise ValueError("Cannot assign to a number")

    def _copy_(self) -> Self:
        return self

    @classmethod
    def _alloc_(cls) -> Self:
        if ctx():
            return Num(ctx().alloc(size=1))
        else:
            return Num(-1)

    def ir(self):
        if isinstance(self.data, BlockPlace):
            return IRGet(self.data)
        else:
            return IRConst(self.data)

    def index(self) -> int | BlockPlace:
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
                return Num(a.data == b.data)
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
                return Num(a.data != b.data)
            if a.data == b.data:
                return Num(False)
            return None

        return self._bin_op(other, const_fn, Op.NotEqual)

    @meta_fn
    def __lt__(self, other) -> Self:
        def const_fn(a: Self, b: Self) -> Num | None:
            if a._is_py_() and b._is_py_():
                return Num(a.data < b.data)
            if a.data == b.data:
                return Num(False)
            return None

        return self._bin_op(other, const_fn, Op.Less)

    @meta_fn
    def __le__(self, other) -> Self:
        def const_fn(a: Self, b: Self) -> Num | None:
            if a._is_py_() and b._is_py_():
                return Num(a.data <= b.data)
            return None

        return self._bin_op(other, const_fn, Op.LessOr)

    @meta_fn
    def __gt__(self, other) -> Self:
        def const_fn(a: Self, b: Self) -> Num | None:
            if a._is_py_() and b._is_py_():
                return Num(a.data > b.data)
            if a.data == b.data:
                return Num(False)
            return None

        return self._bin_op(other, const_fn, Op.Greater)

    @meta_fn
    def __ge__(self, other) -> Self:
        def const_fn(a: Self, b: Self) -> Num | None:
            if a._is_py_() and b._is_py_():
                return Num(a.data >= b.data)
            return None

        return self._bin_op(other, const_fn, Op.GreaterOr)

    @meta_fn
    def __add__(self, other) -> Self:
        def const_fn(a: Self, b: Self) -> Num | None:
            if a._is_py_() and b._is_py_():
                return Num(a.data + b.data)
            if a._is_py_() and a.data == 0:
                return b
            if b._is_py_() and b.data == 0:
                return a
            return None

        return self._bin_op(other, const_fn, Op.Add)

    @meta_fn
    def __sub__(self, other) -> Self:
        def const_fn(a: Self, b: Self) -> Num | None:
            if a._is_py_() and b._is_py_():
                return Num(a.data - b.data)
            if a._is_py_() and a.data == 0:
                return -b
            if b._is_py_() and b.data == 0:
                return a
            return None

        return self._bin_op(other, const_fn, Op.Subtract)

    @meta_fn
    def __mul__(self, other) -> Self:
        def const_fn(a: Self, b: Self) -> Num | None:
            if a._is_py_() and b._is_py_():
                return Num(a.data * b.data)
            if a._is_py_():
                if a.data == 0:
                    return Num(0)
                if a.data == 1:
                    return b
            if b._is_py_():
                if b.data == 0:
                    return Num(0)
                if b.data == 1:
                    return a
            return None

        return self._bin_op(other, const_fn, Op.Multiply)

    @meta_fn
    def __truediv__(self, other) -> Self:
        def const_fn(a: Self, b: Self) -> Num | None:
            if a._is_py_() and b._is_py_():
                if b.data == 0:
                    return None
                return Num(a.data / b.data)
            if b._is_py_():
                if b.data == 1:
                    return a
                if b.data == -1:
                    return -a
            return None

        return self._bin_op(other, const_fn, Op.Divide)

    @meta_fn
    def __floordiv__(self, other) -> Self:
        def const_fn(a: Self, b: Self) -> Num | None:
            if a._is_py_() and b._is_py_():
                if b.data == 0:
                    return None
                return Num(a.data // b.data)
            if b._is_py_():
                if b.data == 1:
                    return a
                if b.data == -1:
                    return -a
            return None

        return self._bin_op(other, const_fn, Op.Divide)._unary_op(lambda x: x, Op.Floor)

    @meta_fn
    def __mod__(self, other) -> Self:
        def const_fn(a: Self, b: Self) -> Num | None:
            if a._is_py_() and b._is_py_():
                if b.data == 0:
                    return None
                return Num(a.data % b.data)
            return None

        return self._bin_op(other, const_fn, Op.Mod)

    @meta_fn
    def __pow__(self, other) -> Self:
        def const_fn(a: Self, b: Self) -> Num | None:
            if a._is_py_() and b._is_py_():
                try:
                    return Num(a.data**b.data)
                except OverflowError:
                    return None
            if b._is_py_():
                if b.data == 0:
                    return Num(1)
                if b.data == 1:
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
                return Num(a.data and b.data)
            if a._is_py_():
                if a.data == 0:
                    return a
                else:
                    return b
            return None

        return self._bin_op(other, const_fn, Op.And)

    def or_(self, other) -> Self:
        def const_fn(a: Self, b: Self) -> Num | None:
            if a._is_py_() and b._is_py_():
                return Num(a.data or b.data)
            if a._is_py_():
                if a.data == 0:
                    return b
                else:
                    return a
            return None

        return self._bin_op(other, const_fn, Op.Or)

    def not_(self) -> Self:
        return self._unary_op(operator.not_, Op.Not)


if TYPE_CHECKING:

    class __Num(float, int, bool, _Num):  # type: ignore
        pass

    Num = __Num | _Num | float | int | bool
else:
    # Need to do this to satisfy type checkers (especially Pycharm)
    _Num.__name__ = "Num"
    _Num.__qualname__ = "Num"
    globals()["Num"] = _Num
