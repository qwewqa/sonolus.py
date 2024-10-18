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
    def value(cls):
        return cls._type_args_[1]

    @classmethod
    def _get_parameterized(cls, args: tuple[Any, ...]) -> type[Self]:
        result = super()._get_parameterized(args)
        result._instance = object.__new__(result)
        return result

    @classmethod
    def _size_(cls) -> int:
        return 0

    @classmethod
    def _is_value_type_(cls) -> bool:
        return False

    @classmethod
    def _from_place_(cls, place: BlockPlace) -> Self:
        return cls._instance

    @classmethod
    def _accepts_(cls, value: Any) -> bool:
        value = validate_value(value)
        if not value._is_py_():
            return False
        if cls._type_args_ is None:
            return True
        return value._as_py_() == cls.value()

    @classmethod
    def _accept_(cls, value: Any) -> Self:
        if not cls._accepts_(value):
            raise TypeError("Value does not match this Comptime instance")
        return validate_value(value)

    def _is_py_(self) -> bool:
        return True

    def _as_py_(self) -> Any:
        return self.value()

    @classmethod
    def _from_list_(cls, values: Iterable[float]) -> Self:
        return cls._instance

    def _to_list_(self) -> list[float]:
        return []

    def _get_(self) -> Self:
        return self

    def _set_(self, value: Self):
        if value is not self:
            raise TypeError("Comptime value cannot be changed")

    def _copy_from_(self, value: Self):
        if value is not self:
            raise TypeError("Comptime value cannot be changed")

    def _copy_(self) -> Self:
        return self

    def __getitem__(self, item):
        item = validate_value(item)
        match self.value():
            case tuple():
                if not item._is_py_():
                    raise TypeError("Tuple index must be a compile time constant")
                index = item._as_py_()
                if isinstance(index, float) and not index.is_integer():
                    raise TypeError("Tuple index must be an integer")
                index = int(index)
                return self.value()[index]
            case Dict():
                if not item._is_py_():
                    raise TypeError("Dict key must be a compile time constant")
                return self.value()[item._as_py_()]

    @classmethod
    def accept_unchecked(cls, value: Any) -> Self:
        args = (type(value), value)
        if args not in cls._parameterized_:
            cls._parameterized_[args] = cls._get_parameterized(args)
        return cls._parameterized_[args]._instance


if not TYPE_CHECKING:
    Comptime = _Comptime
    Comptime.__name__ = "Comptime"
    Comptime.__qualname__ = "Comptime"
else:
    type Comptime[T, V] = T | V

from sonolus.script.internal.impl import Dict, validate_value
