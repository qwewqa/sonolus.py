from __future__ import annotations

from collections.abc import Callable
from types import FunctionType, MethodType, NoneType, NotImplementedType
from typing import Any, TypeVar, overload, TYPE_CHECKING

if TYPE_CHECKING:
    from sonolus.script.comptime import Comptime
    from sonolus.script.internal.generic import PartialGeneric
    from sonolus.script.internal.value import Value
    from sonolus.script.num import Num


@overload
def nocompile[T: Callable](fn: T) -> T: ...


@overload
def nocompile[T: Callable]() -> Callable[[T], T]: ...


def nocompile(fn=None):
    # noinspection PyShadowingNames
    def decorator(fn):
        fn.nocompile = True
        return fn

    if fn is None:
        return decorator
    return decorator(fn)


def validate_value(value: Any) -> Value:
    match value:
        case Value():
            return value
        case type():
            if value in {int, float, bool}:
                return Comptime.accept_unchecked(Num)
            return Comptime.accept_unchecked(value)
        case int() | float() | bool():
            return Num._accept_(value)
        case tuple():
            return Comptime.accept_unchecked(tuple(validate_value(v) for v in value))
        case PartialGeneric() | TypeVar() | FunctionType() | MethodType() | NotImplementedType() | str() | NoneType():
            return Comptime.accept_unchecked(value)
        case _:
            raise TypeError(f"Unsupported value: {value!r}")


from sonolus.script.comptime import Comptime
from sonolus.script.internal.generic import PartialGeneric
from sonolus.script.internal.value import Value
from sonolus.script.num import Num
