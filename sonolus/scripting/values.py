from __future__ import annotations

from typing import TypeVar, overload, Type, Any, Callable, ParamSpec

from sonolus.scripting.internal.array import Array
from sonolus.scripting.internal.generic_struct import GenericStruct, generic_method
from sonolus.scripting.internal.pointer import Pointer
from sonolus.scripting.internal.primitive import Bool, Num
from sonolus.scripting.internal.statement import Statement
from sonolus.scripting.internal.struct import Struct, Empty
from sonolus.scripting.internal.tuple import TupleStruct
from sonolus.scripting.internal.value import Value, convert_value
from sonolus.scripting.internal.void import Void

__all__ = (
    "Statement",
    "Value",
    "Struct",
    "TupleStruct",
    "Empty",
    "Array",
    "Pointer",
    "Void",
    "GenericStruct",
    "generic_method",
    "new",
    "alloc",
    "default_factory",
)

T = TypeVar("T", bound=Value)
P = ParamSpec("P")


@overload
def new() -> Any:
    pass


@overload
def new(value: float, /) -> Num:
    pass


@overload
def new(value: bool, /) -> Bool:
    pass


@overload
def new(value: T, /) -> T:
    pass


@overload
def new(value: Type[T], /) -> T:
    pass


@overload
def new(value: list[float], /) -> Array[Num]:
    pass


@overload
def new(value: list[bool], /) -> Array[Bool]:
    pass


@overload
def new(value: list[T], /) -> Array[T]:
    pass


def new(value=None, /):
    match value:
        case None:
            return _NewValue()
        case list():
            return Array.of(*value).copy()
        case type() if Value.is_value_class(value):
            return value._default_().copy()
        case Value():
            return value.copy()
        case bool() as boolean:
            return Bool(boolean).copy()
        case int() | float() as number:
            return Num(number).copy()
        case _:
            raise TypeError(f"Cannot create new value from {value}.")


def alloc(type_: Type[T], /) -> T:
    return type_._allocate_()


def default_factory(factory: Callable[[], Any]) -> Any:
    return _DefaultFactory(factory)


class _NewValue:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    def _convert_to_(self, type_):
        return type_(*self.args, **self.kwargs).copy()


class _DefaultFactory:
    def __init__(self, factory):
        self.factory = factory

    def _convert_to_(self, type_):
        return convert_value(self.factory(), type_)
