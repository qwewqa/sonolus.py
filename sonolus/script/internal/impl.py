from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from types import EllipsisType, FunctionType, MethodType, ModuleType, NoneType, NotImplementedType, UnionType
from typing import TYPE_CHECKING, Annotated, Any, Literal, TypeVar, get_origin, overload

if TYPE_CHECKING:
    from sonolus.script.internal.value import Value


@overload
def meta_fn[T: Callable](fn: T) -> T: ...


@overload
def meta_fn[T: Callable]() -> Callable[[T], T]: ...


def meta_fn(fn=None):
    """Marks a function as a meta function to be called directly without the AST visitor.

    This can also improve performance in some cases by avoiding the overhead of the AST visitor.
    """

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
    from sonolus.script.globals import _GlobalPlaceholder
    from sonolus.script.internal.constant import BasicConstantValue
    from sonolus.script.internal.dict_impl import DictImpl
    from sonolus.script.internal.generic import PartialGeneric
    from sonolus.script.internal.tuple_impl import TupleImpl
    from sonolus.script.internal.value import Value
    from sonolus.script.num import Num

    try:
        # Unfortunately this is called during import, so this may fail
        from sonolus.script.internal.builtin_impls import BUILTIN_IMPLS

        if id(value) in BUILTIN_IMPLS:
            return validate_value(BUILTIN_IMPLS[id(value)])
    except ImportError:
        pass

    match value:
        case Enum():
            return validate_value(value.value)
        case Value():
            return value
        case type():
            if value in {int, float, bool}:
                return BasicConstantValue.of(Num)
            return BasicConstantValue.of(value)
        case int() | float() | bool():
            return Num._accept_(value)
        case tuple():
            return TupleImpl._accept_(value)
        case dict():
            return DictImpl._accept_(value)
        case (
            PartialGeneric()
            | TypeVar()
            | FunctionType()
            | MethodType()
            | str()
            | ModuleType()
            | NoneType()
            | NotImplementedType()
            | EllipsisType()
        ):
            return BasicConstantValue.of(value)
        case other_type if get_origin(value) in {Literal, Annotated, UnionType, tuple}:
            return BasicConstantValue.of(other_type)
        case _GlobalPlaceholder():
            return value.get()
        case comptime_value if getattr(comptime_value, "_is_comptime_value_", False):
            return BasicConstantValue.of(comptime_value)
        case _:
            return None
