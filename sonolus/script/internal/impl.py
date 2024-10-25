from __future__ import annotations

from collections.abc import Callable
from types import FunctionType, MethodType, NoneType, NotImplementedType
from typing import TYPE_CHECKING, Any, TypeVar, overload

from sonolus.script.archetype import Archetype

if TYPE_CHECKING:
    from sonolus.script.comptime import Comptime
    from sonolus.script.internal.generic import PartialGeneric
    from sonolus.script.internal.value import Value


@overload
def self_impl[T: Callable](fn: T) -> T: ...


@overload
def self_impl[T: Callable]() -> Callable[[T], T]: ...


def self_impl(fn=None):
    # noinspection PyShadowingNames
    def decorator(fn):
        fn._self_impl_ = True
        return fn

    if fn is None:
        return decorator
    return decorator(fn)


def validate_value(value: Any) -> Value:
    from sonolus.script.num import Num

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
        case dict():
            return Comptime.accept_unchecked({validate_value(k)._as_py_(): validate_value(v) for k, v in value.items()})
        case PartialGeneric() | TypeVar() | FunctionType() | MethodType() | NotImplementedType() | str() | NoneType() | Archetype():
            return Comptime.accept_unchecked(value)
        case global_value if getattr(global_value, "_global_info_", None):
            return Comptime.accept_unchecked(value)
        case _:
            raise TypeError(f"Unsupported value: {value!r}")


from sonolus.script.comptime import Comptime
from sonolus.script.internal.generic import PartialGeneric
from sonolus.script.internal.value import Value
