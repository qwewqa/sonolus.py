from collections.abc import Iterable, MutableMapping
from typing import Any, ClassVar, Self
from weakref import WeakValueDictionary

from sonolus.backend.place import BlockPlace
from sonolus.script.internal.simple_meta_fn import simple_meta_fn
from sonolus.script.internal.value import DataValue, Value


class _Missing:
    def __repr__(self) -> str:
        return "MISSING"

    def __bool__(self) -> bool:
        return False


_MISSING = _Missing()

_MAX_PARAMETER_NAME_LENGTH = 80


def _parameter_name(parameter: Any) -> str:
    """A short label for a wrapped constant, used to name the class that carries it."""
    name = getattr(parameter, "__name__", None)
    if isinstance(name, str):
        return name
    if type(parameter).__repr__ is object.__repr__:
        return f"{type(parameter).__name__} object"
    text = repr(parameter)
    if len(text) > _MAX_PARAMETER_NAME_LENGTH:
        text = f"{text[: _MAX_PARAMETER_NAME_LENGTH - 3]}..."
    return text


class ConstantValue(Value):
    """Wraps a python constant value usable in Sonolus scripts."""

    # Weakly valued, with the key reachable from the class it maps to, so an entry lives exactly as long as
    # something holds the class minted for its value.
    _parameterized_: ClassVar[MutableMapping[Any, type[Self]]] = WeakValueDictionary()
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
        parameterized = cls._parameterized_.get(value)
        if parameterized is None:
            parameterized = cls._get_parameterized(value)
            cls._parameterized_[value] = parameterized
        # Parameterized classes always have _value set, so calling parameterized()
        # would just return parameterized.instance; return it directly.
        return parameterized.instance

    @classmethod
    def _get_parameterized(cls, parameter: Any) -> type[Self]:
        class Parameterized(cls):
            _value = (parameter,)

        Parameterized.__name__ = f"Const[{_parameter_name(parameter)}]"
        Parameterized.__qualname__ = Parameterized.__name__
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
            raise ValueError(f"Value {value} is not of type {cls.__name__}")
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

    def _copy_from_(self, value: Any, *, initializing: bool = False):
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

    @simple_meta_fn
    def __eq__(self, other):
        return self is other

    @simple_meta_fn
    def __ne__(self, other):
        return self is not other

    @simple_meta_fn
    def __hash__(self):
        return hash(self.value())

    def __repr__(self) -> str:
        return type(self).__name__


class BasicConstantValue(ConstantValue):
    """For constants without any special behavior."""


class TypingSpecialFormConstant(ConstantValue):
    """For constants that are typing special forms that have a [] operator."""

    @simple_meta_fn
    def __getitem__(self, item: Any) -> Self:
        if not item._is_py_():
            raise TypeError(f"Invalid value for type parameter: {item}")
        return self.value()[item._as_py_()]
