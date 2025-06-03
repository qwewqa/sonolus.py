from collections.abc import Iterable
from typing import Any, ClassVar, Self

from sonolus.backend.place import BlockPlace
from sonolus.script.internal.impl import meta_fn
from sonolus.script.internal.value import DataValue, Value


class _Missing:
    def __repr__(self) -> str:
        return "MISSING"

    def __bool__(self) -> bool:
        return False


_MISSING = _Missing()


class ConstantValue(Value):
    """Wraps a python constant value usable in Sonolus scripts."""

    _parameterized_: ClassVar[dict[Any, type[Self]]] = {}
    _value: ClassVar[Any] = _MISSING
    instance: ClassVar[Self | _Missing] = _MISSING

    def __new__(cls) -> Self:
        if cls.value() is _MISSING:
            raise TypeError(f"Class {cls.__name__} is not parameterized")
        return cls.instance

    @classmethod
    def value(cls):
        # We need this to avoid descriptors getting in the way
        return cls._value[0] if cls._value is not _MISSING else _MISSING

    @classmethod
    def of(cls, value: Any) -> Self:
        if value in cls._parameterized_:
            return cls._parameterized_[value]()

        parameterized = cls._get_parameterized(value)
        cls._parameterized_[value] = parameterized
        return parameterized()

    @classmethod
    def _get_parameterized(cls, parameter: Any) -> type[Self]:
        class Parameterized(cls):
            _value = (parameter,)

        Parameterized.__name__ = f"{parameter}"
        Parameterized.__qualname__ = f"{parameter}"
        Parameterized.__module__ = cls.__module__
        Parameterized.instance = object.__new__(Parameterized)
        return Parameterized

    @classmethod
    def _is_concrete_(cls) -> bool:
        return True

    @classmethod
    def _size_(cls) -> int:
        return 0

    @classmethod
    def _is_value_type_(cls) -> bool:
        return False

    @classmethod
    def _from_place_(cls, place: BlockPlace) -> Self:
        if cls.value() is _MISSING:
            raise TypeError(f"Class {cls.__name__} is not parameterized")
        return cls()

    @classmethod
    def _accepts_(cls, value: Any) -> bool:
        from sonolus.script.internal.impl import validate_value

        # We rely on validate_value to create the correct instance
        return isinstance(validate_value(value), cls)

    @classmethod
    def _accept_(cls, value: Any) -> Self:
        from sonolus.script.internal.impl import validate_value

        # We rely on validate_value to create the correct instance
        value = validate_value(value)
        if not isinstance(value, cls):
            raise ValueError(f"Value {value} is not of type {cls}")
        return value

    def _is_py_(self) -> bool:
        return True

    def _as_py_(self) -> Any:
        return self.value()

    @classmethod
    def _from_list_(cls, values: Iterable[DataValue]) -> Self:
        return cls()

    def _to_list_(self, level_refs: dict[Any, str] | None = None) -> list[DataValue | str]:
        return []

    @classmethod
    def _flat_keys_(cls, prefix: str) -> list[str]:
        return []

    def _get_(self) -> Self:
        return self

    def _set_(self, value: Any):
        if value is not self:
            raise ValueError(f"{type(self).__name__} is immutable")

    def _copy_from_(self, value: Self):
        if value is not self:
            raise ValueError(f"{type(self).__name__} is immutable")

    def _copy_(self) -> Self:
        return self

    @classmethod
    def _alloc_(cls) -> Self:
        return cls()

    @classmethod
    def _zero_(cls) -> Self:
        return cls()

    @meta_fn
    def __eq__(self, other):
        return self is other

    @meta_fn
    def __ne__(self, other):
        return self is not other

    @meta_fn
    def __hash__(self):
        return hash(self.value())


class BasicConstantValue(ConstantValue):
    """For constants without any special behavior."""
