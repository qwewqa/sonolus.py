from __future__ import annotations

from collections.abc import Callable
from types import FunctionType, MethodType, NoneType, NotImplementedType
from typing import TYPE_CHECKING, Any, TypeVar, overload

if TYPE_CHECKING:
    from sonolus.script.comptime import Comptime
    from sonolus.script.internal.generic import PartialGeneric
    from sonolus.script.internal.value import Value
    from sonolus.script.num import Num


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


# class Dict(Mapping):  # pseudo-dict since regular dict is not hashable
#     def __init__(self, entries: Iterable[tuple[Any, Any]]):
#         self.data = dict(entries)
#
#     def __getitem__(self, key, /):
#         return self.data[key]
#
#     def __len__(self):
#         return len(self.data)
#
#     def __iter__(self):
#         return iter(self.data)
#
#     def __eq__(self, other):
#         return self is other
#
#     def __hash__(self):
#         return id(self)
#
#     def __str__(self):
#         return f"{{{', '.join(f'{k}: {v}' for k, v in self.data.items())}}}"
#
#     def __repr__(self):
#         return f"{type(self).__name__}({self.data!r})"
#
#
# class Tuple(tuple):  # pseudo-tuple to allow identity comparison
#     def __eq__(self, other):
#         return self is other
#
#     def __hash__(self):
#         return id(self)
#
#     def __repr__(self):
#         return f"{type(self).__name__}({super().__repr__()})"


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
        case dict():
            return Comptime.accept_unchecked({validate_value(k)._as_py_(): validate_value(v) for k, v in value.items()})
        case PartialGeneric() | TypeVar() | FunctionType() | MethodType() | NotImplementedType() | str() | NoneType():
            return Comptime.accept_unchecked(value)
        case _:
            raise TypeError(f"Unsupported value: {value!r}")


from sonolus.script.comptime import Comptime
from sonolus.script.internal.generic import PartialGeneric
from sonolus.script.internal.value import Value
from sonolus.script.num import Num
