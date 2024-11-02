from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from types import FunctionType, MethodType, NoneType, NotImplementedType
from typing import TYPE_CHECKING, Annotated, Any, Literal, TypeVar, get_origin, overload

if TYPE_CHECKING:
    from sonolus.script.internal.value import Value


@overload
def meta_fn[T: Callable](fn: T) -> T: ...


@overload
def meta_fn[T: Callable]() -> Callable[[T], T]: ...


def meta_fn(fn=None):
    # noinspection PyShadowingNames
    def decorator(fn):
        fn._meta_fn_ = True
        return fn

    if fn is None:
        return decorator
    return decorator(fn)


def validate_value(value: Any) -> Value:
    result = try_validate_value(value)
    if result is None:
        raise TypeError(f"Unsupported value: {value!r}")
    return result


def try_validate_value(value: Any) -> Value | None:
    from sonolus.script.comptime import Comptime
    from sonolus.script.globals import GlobalPlaceholder
    from sonolus.script.internal.generic import PartialGeneric
    from sonolus.script.internal.value import Value
    from sonolus.script.num import Num

    match value:
        case Enum():
            return validate_value(value.value)
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
        case PartialGeneric() | TypeVar() | FunctionType() | MethodType() | NotImplementedType() | str() | NoneType():
            return Comptime.accept_unchecked(value)
        case other_type if get_origin(value) in {Literal, Annotated}:
            return Comptime.accept_unchecked(other_type)
        case GlobalPlaceholder():
            return value.get()
        case comptime_value if getattr(comptime_value, "_is_comptime_value_", False):
            return Comptime.accept_unchecked(comptime_value)
        case _:
            return None
