from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, Self, final

from sonolus.backend.place import BlockPlace
from sonolus.script.internal.generic import GenericValue


@final
class _Comptime[T, V](GenericValue):
    _instance: Self | None = None

    def __init__(self):
        super().__init__()
        raise TypeError("Comptime cannot be instantiated")

    @classmethod
    def _get_parameterized(cls, args: tuple[Any, ...]) -> type[Self]:
        result = super()._get_parameterized(args)
        result._instance = object.__new__(result)
        return result

    @classmethod
    def size_(cls) -> int:
        return 0

    @classmethod
    def is_value_type_(cls) -> bool:
        return False

    @classmethod
    def from_place_(cls, place: BlockPlace) -> Self:
        return cls._instance

    @classmethod
    def accepts_(cls, value: Any) -> bool:
        value = validate_value(value)
        if not value.is_py_():
            return False
        if cls.type_args_ is None:
            return True
        return value.as_py_() == cls.type_args_[1]

    @classmethod
    def accept_(cls, value: Any) -> Self:
        if not cls.accepts_(value):
            raise TypeError("Value does not match this Comptime instance")
        return cls._instance

    def is_py_(self) -> bool:
        return True

    def as_py_(self) -> Any:
        return self.type_args_[1]

    @classmethod
    def from_list_(cls, values: Iterable[float]) -> Self:
        return cls._instance

    def to_list_(self) -> list[float]:
        return []

    def get_(self) -> Self:
        return self

    def set_(self, value: Self):
        if value is not self:
            raise TypeError("Comptime value cannot be changed")

    def copy_from_(self, value: Self):
        if value is not self:
            raise TypeError("Comptime value cannot be changed")

    def copy_(self) -> Self:
        return self

    @classmethod
    def accept_unchecked(cls, value: Any) -> Self:
        args = (type(value), value)
        if args not in cls.parameterized_:
            cls.parameterized_[args] = cls._get_parameterized(args)
        return cls.parameterized_[args]._instance


if not TYPE_CHECKING:
    Comptime = _Comptime
    Comptime.__name__ = "Comptime"
    Comptime.__qualname__ = "Comptime"
else:
    type Comptime[T, V] = T | V

from sonolus.script.internal.impl import validate_value
