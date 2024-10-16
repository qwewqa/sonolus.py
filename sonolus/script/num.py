from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, Self, final

from sonolus.backend.ir import IRConst, IRGet, IRSet
from sonolus.backend.place import BlockPlace, Place
from sonolus.script.internal.context import ctx
from sonolus.script.internal.value import Value


@final
class _Num(Value):
    data: BlockPlace | float | int | bool

    def __init__(self, data: Place | float | int | bool):
        self.data = data

    def __str__(self) -> str:
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
    def from_place_(cls, place: Place) -> Self:
        return cls(place)

    @classmethod
    def accepts_(cls, value: Value) -> bool:
        return isinstance(value, Num | float | int | bool)

    @classmethod
    def accept_(cls, value: Any) -> Self:
        if not cls.accepts_(value):
            raise ValueError(f"Cannot accept {value}")
        return cls(value)

    @classmethod
    def is_py_(cls) -> bool:
        return not isinstance(cls.data, BlockPlace)

    def as_py_(self) -> Any:
        if not self.is_py_():
            raise ValueError("Not a python value")
        return self.data

    @classmethod
    def from_list_(cls, values: Iterable[float]) -> Self:
        (value,) = values
        return Num(value)

    def to_list_(self) -> list[float]:
        return [self.data]

    def get_(self) -> Self:
        if ctx():
            place = ctx().alloc()
            ctx().add_statements(IRSet(place, self.ir()))
            return Num(place)
        else:
            return self

    def set_(self, value: Self):
        if ctx():
            if not isinstance(self.data, BlockPlace):
                raise ValueError("Cannot set a compile time constant value")
            ctx().add_statements(IRSet(self.data, value.ir()))
        else:
            self.data = value.data

    def copy_from_(self, value: Self):
        raise ValueError("Cannot assign to a number")

    def ir(self):
        if isinstance(self.data, BlockPlace):
            return IRGet(self.data)
        else:
            return IRConst(self.data)


if TYPE_CHECKING:
    # Some type checks complain if we use Num in the class definition then redefine it
    # so we use _Num instead
    Num = float | int | bool | _Num
else:
    _Num.__name__ = "Num"
    Num = _Num
