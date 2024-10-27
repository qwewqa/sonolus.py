from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, Self, TypeVar, final

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
        _, value = cls._type_args_
        if isinstance(value, Identity):
            return value.value
        return value

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
        from sonolus.script.internal.impl import validate_value

        value = validate_value(value)
        if not value._is_py_():
            return False
        if cls._type_args_ is None:
            return True
        return value._as_py_() == cls.value()

    @classmethod
    def _accept_(cls, value: Any) -> Self:
        from sonolus.script.internal.impl import validate_value

        if not cls._accepts_(value):
            raise TypeError("Value does not match this Comptime instance")
        # This might not actually return a Comptime instance, but it will be a compile-time constant
        return validate_value(value)

    def _is_py_(self) -> bool:
        return True

    def _as_py_(self) -> Any:
        return self.value()

    @classmethod
    def _from_list_(cls, values: Iterable[float | BlockPlace]) -> Self:
        return cls._instance

    def _to_list_(self) -> list[float | BlockPlace]:
        return []

    @classmethod
    def _flat_keys_(cls, prefix: str) -> list[str]:
        return []

    def _get_(self) -> Self:
        from sonolus.script.internal.impl import validate_value

        # Converts numbers out of comptime, although _accept_ may end up returning a non-comptime instance anyway
        return validate_value(self.value())

    def _set_(self, value: Self):
        if value is not self:
            raise TypeError("Comptime value cannot be changed")

    def _copy_from_(self, value: Self):
        if value is not self:
            raise TypeError("Comptime value cannot be changed")

    def _copy_(self) -> Self:
        return self

    @classmethod
    def _alloc_(cls) -> Self:
        return cls._instance

    @classmethod
    def _validate__type_args_(cls, args: tuple[Any, ...]) -> tuple[Any, ...]:
        if len(args) == 2:
            _, value = args
            # We want the type to be there for documentation,
            # but not enforced since they might not match up, e.g. a Callable is really FunctionType
            if isinstance(value, TypeVar):
                args = Any, value
            else:
                args = type(value), value
        return super()._validate__type_args_(args)

    @classmethod
    def accept_unchecked(cls, value: Any) -> Self:
        if isinstance(value, dict | tuple):
            args = type(value), Identity(value)
        else:
            args = type(value), value
        if args not in cls._parameterized_:
            cls._parameterized_[args] = cls._get_parameterized(args)
        return cls._parameterized_[args]._instance


class Identity[T]:  # This is to allow accepting potentially unhashable values by using identity comparison
    value: T

    def __init__(self, value: T):
        self.value = value

    def __eq__(self, other):
        return self is other

    def __hash__(self):
        return id(self)

    def __str__(self):
        return f"{type(self).__name__}({self.value})"

    def __repr__(self):
        return f"{type(self).__name__}({self.value!r})"


if TYPE_CHECKING:
    type Comptime[T, V] = T | V
else:
    _Comptime.__name__ = "Comptime"
    _Comptime.__qualname__ = "Comptime"
    globals()["Comptime"] = _Comptime
