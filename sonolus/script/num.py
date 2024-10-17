# ruff: noqa: N801
import operator
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any, Self, final

from sonolus.backend.ir import IRConst, IRGet, IRPureInstr, IRSet
from sonolus.backend.ops import Op
from sonolus.backend.place import BlockPlace, Place
from sonolus.script.internal.context import ctx
from sonolus.script.internal.error import InternalError
from sonolus.script.internal.impl import self_impl
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
    def is_concrete_(cls) -> bool:
        return True

    @classmethod
    def size_(cls) -> int:
        return 1

    @classmethod
    def is_value_type_(cls) -> bool:
        return True

    @classmethod
    def from_place_(cls, place: BlockPlace) -> Self:
        return cls(place)

    @classmethod
    def accepts_(cls, value: Value) -> bool:
        return isinstance(value, Num | float | int | bool)

    @classmethod
    def accept_(cls, value: Any) -> Self:
        if not cls.accepts_(value):
            raise ValueError(f"Cannot accept {value}")
        if isinstance(value, Num):
            return value
        return cls(value)

    def is_py_(self) -> bool:
        return not isinstance(self.data, BlockPlace)

    def as_py_(self) -> Any:
        if not self.is_py_():
            raise ValueError("Not a python value")
        if self.data.is_integer():
            return int(self.data)
        return self.data

    @classmethod
    def from_list_(cls, values: Iterable[float]) -> Self:
        (value,) = values
        return Num(value)

    def to_list_(self) -> list[float]:
        return [self.data]

    def get_(self) -> Self:
        if ctx():
            place = ctx().alloc(size=1)
            if isinstance(self.data, BlockPlace):
                ctx().check_readable(self.data)
            ctx().add_statements(IRSet(place, self.ir()))
            return Num(place)
        else:
            return self

    def set_(self, value: Self):
        if ctx():
            if not isinstance(self.data, BlockPlace):
                raise ValueError("Cannot set a compile time constant value")
            ctx().check_writable(self.data)
            ctx().add_statements(IRSet(self.data, value.ir()))
        else:
            self.data = value.data

    def copy_from_(self, value: Self):
        raise ValueError("Cannot assign to a number")

    def copy_(self) -> Self:
        return self

    def ir(self):
        if isinstance(self.data, BlockPlace):
            return IRGet(self.data)
        else:
            return IRConst(self.data)

    def index(self) -> int | BlockPlace:
        return self.data

    def _bin_op(self, other: Self, py_fn: Callable[[float, float], float], ir_op: Op) -> Self:
        other = Num.accept_(other)
        if self.is_py_() and other.is_py_():
            return Num(py_fn(self.as_py_(), other.as_py_()))
        elif ctx():
            result_place = ctx().alloc(size=1)
            ctx().add_statements(IRSet(result_place, IRPureInstr(ir_op, [self.ir(), other.ir()])))
            return Num(result_place)
        else:
            raise InternalError("Unexpected call on non-comptime Num instance outside a compilation context")

    def _unary_op(self, py_fn: Callable[[float], float], ir_op: Op) -> Self:
        if self.is_py_():
            return Num(py_fn(self.as_py_()))
        elif ctx():
            result_place = ctx().alloc(size=1)
            ctx().add_statements(IRSet(result_place, IRPureInstr(ir_op, [self.ir()])))
            return Num(result_place)
        else:
            raise InternalError("Unexpected call on non-comptime Num instance outside a compilation context")

    @self_impl
    def __eq__(self, other) -> Self:
        return self._bin_op(other, operator.eq, Op.Equal)

    def __hash__(self):
        raise TypeError("unhashable type: 'Num'")

    @self_impl
    def __ne__(self, other) -> Self:
        return self._bin_op(other, operator.ne, Op.NotEqual)

    @self_impl
    def __lt__(self, other) -> Self:
        return self._bin_op(other, operator.lt, Op.Less)

    @self_impl
    def __le__(self, other) -> Self:
        return self._bin_op(other, operator.le, Op.LessOr)

    @self_impl
    def __gt__(self, other) -> Self:
        return self._bin_op(other, operator.gt, Op.Greater)

    @self_impl
    def __ge__(self, other) -> Self:
        return self._bin_op(other, operator.ge, Op.GreaterOr)

    @self_impl
    def __add__(self, other) -> Self:
        return self._bin_op(other, operator.add, Op.Add)

    @self_impl
    def __sub__(self, other) -> Self:
        return self._bin_op(other, operator.sub, Op.Subtract)

    @self_impl
    def __mul__(self, other) -> Self:
        return self._bin_op(other, operator.mul, Op.Multiply)

    @self_impl
    def __truediv__(self, other) -> Self:
        return self._bin_op(other, operator.truediv, Op.Divide)

    @self_impl
    def __floordiv__(self, other) -> Self:
        return self._bin_op(other, operator.floordiv, Op.Divide)._unary_op(lambda x: x, Op.Floor)

    @self_impl
    def __mod__(self, other) -> Self:
        return self._bin_op(other, operator.mod, Op.Mod)

    @self_impl
    def __pow__(self, other) -> Self:
        return self._bin_op(other, operator.pow, Op.Power)

    @self_impl
    def __neg__(self) -> Self:
        return self._unary_op(operator.neg, Op.Negate)

    @self_impl
    def __pos__(self) -> Self:
        return self

    @self_impl
    def __abs__(self) -> Self:
        return self._unary_op(abs, Op.Abs)

    def and_(self, other) -> Self:
        return self._bin_op(other, lambda a, b: a and b, Op.And)

    def or_(self, other) -> Self:
        return self._bin_op(other, lambda a, b: a or b, Op.Or)

    def not_(self) -> Self:
        return self._unary_op(operator.not_, Op.Not)


if TYPE_CHECKING:

    class __Num(float, int, bool, _Num):  # type: ignore
        pass

    Num: type[__Num | float | int | bool] | _Num
else:
    # Need to do this to satisfy type checkers (especially Pycharm)
    _Num.__name__ = "Num"
    globals()["Num"] = _Num
