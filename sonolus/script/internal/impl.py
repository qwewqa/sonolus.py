from __future__ import annotations

from enum import Enum
from types import EllipsisType, FunctionType, MethodType, ModuleType, NoneType, NotImplementedType, UnionType
from typing import TYPE_CHECKING, Annotated, Final, Literal, TypeVar, Union, get_origin

if TYPE_CHECKING:
    from sonolus.script.internal.value import Value


def validate_value[T](value: T) -> Value | T:
    if isinstance(value, Value):
        return value
    if isinstance(value, int | float):
        return Num._accept_(value)
    if isinstance(value, Enum):
        return validate_value(value.value)
    if id(value) in BUILTIN_IMPLS:
        return validate_value(BUILTIN_IMPLS[id(value)])
    if isinstance(value, type):
        if value in {int, float, bool}:
            return constant.BasicConstantValue.of(Num)
        return constant.BasicConstantValue.of(value)

    if hasattr(value, "_init_") and callable(value._init_):
        try:
            value._init_()
        except Exception as e:
            raise RuntimeError(f"Error initializing value {value}: {e}") from e

    value_type = type(value)
    if value_type in {
        generic.PartialGeneric,
        TypeVar,
        FunctionType,
        MethodType,
        str,
        ModuleType,
        NoneType,
        NotImplementedType,
        EllipsisType,
        super,
    }:
        return constant.BasicConstantValue.of(value)
    if value_type is tuple:
        return tuple_impl.TupleImpl._accept_(value)
    if value_type is dict:
        return dict_impl.DictImpl._accept_(value)
    if value_type in {set, frozenset}:
        from sonolus.script.containers import FrozenNumSet

        return FrozenNumSet.of(*value)
    if get_origin(value) in {Literal, Annotated, UnionType, Final, tuple, type}:
        return constant.BasicConstantValue.of(value)
    if value in {Literal, Annotated, Union}:
        return constant.TypingSpecialFormConstant.of(value)
    if value_type is sonolus_globals._GlobalPlaceholder:
        return value.get()
    if getattr(value, "_is_comptime_value_", False):
        return constant.BasicConstantValue.of(value)
    raise TypeError(f"Unsupported value: {value!r}")


from sonolus.script import globals as sonolus_globals
from sonolus.script.internal import constant, dict_impl, generic, tuple_impl
from sonolus.script.internal.value import Value
from sonolus.script.num import Num

BUILTIN_IMPLS = {}
