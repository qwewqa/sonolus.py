from __future__ import annotations

import inspect
from enum import Enum
from types import EllipsisType, FunctionType, MethodType, ModuleType, NoneType, NotImplementedType, UnionType
from typing import TYPE_CHECKING, Annotated, Any, Final, Literal, TypeVar, Union, get_origin

if TYPE_CHECKING:
    from sonolus.script.internal.value import Value

# Hoisted tuple: building `int | float` inline allocates a new types.UnionType per call.
_INT_FLOAT = (int, float)


def validate_value[T](value: T) -> Value | T:
    if isinstance(value, Value):
        return value
    if isinstance(value, _INT_FLOAT):
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
            raise RuntimeError(f"Error initializing value {_describe_unsupported(value)}: {e}") from e

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
        from sonolus.script.internal import dict_impl

        return dict_impl.DictImpl.from_dict(value)
    if value_type in {set, frozenset}:
        from sonolus.script.internal import set_impl

        return set_impl.SetImpl.from_set(value)
    if get_origin(value) in {Literal, Annotated, UnionType, Final, tuple, type}:
        return constant.BasicConstantValue.of(value)
    if value is Literal or value is Annotated or value is Union:
        return constant.TypingSpecialFormConstant.of(value)
    if value_type is sonolus_globals._GlobalPlaceholder:
        return value.get()
    if getattr(value, "_is_comptime_value_", False):
        return constant.BasicConstantValue.of(value)
    raise TypeError(f"Unsupported value: {_describe_unsupported(value)}")


def _describe_unsupported(value) -> str:
    from sonolus.script.internal.builtin_impls import _type_name

    # An object inheriting object.__repr__ prints as `<Cls object at 0xADDRESS>`, which puts an address no
    # reader can act on into user-facing text. Anything with a repr of its own keeps it: it says more.
    if type(value).__repr__ is object.__repr__:
        return f"{_type_name(value)} object"
    return repr(value)


def bind_arguments(
    sig: inspect.Signature, callee_name: str, args: tuple, kwargs: dict[str, Any], *, partial: bool = False
) -> inspect.BoundArguments:
    """Bind `args` and `kwargs` to `sig`, raising a `TypeError` that names `callee_name` on failure.

    A `Signature` has no owner, so `inspect`'s own binding errors name no callee. `inspect` also fills
    parameters in declaration order and gives up on the first one it cannot fill, so a keyword the signature
    does not accept is reported as whichever earlier parameter it left unfilled: the author is told to supply
    a parameter that is not what is wrong. Both are corrected here.

    `partial` binds through `bind_partial`, for a caller that fills the parameters the call omits itself rather
    than from a default. That form accepts any subset, so only the callee naming applies to it.
    """
    try:
        return sig.bind_partial(*args, **kwargs) if partial else sig.bind(*args, **kwargs)
    except TypeError as e:
        message = e.args[0] if e.args else None
        if isinstance(message, str):
            unexpected = _first_unexpected_keyword(sig, kwargs)
            if unexpected is not None:
                message = f"got an unexpected keyword argument '{unexpected}'"
            # Mutated in place rather than replaced by a new exception: the excepthook prints the whole chain,
            # so raising from this one would show the unnamed message again above the named one.
            e.args = (f"{callee_name}() {message}", *e.args[1:])
        raise


def _first_unexpected_keyword(sig: inspect.Signature, kwargs: dict[str, Any]) -> str | None:
    """Return the first keyword `sig` has no parameter for, or None.

    A parameter that exists but cannot be filled by keyword is left out: `inspect` already says exactly what
    is wrong with those, and "unexpected" would be a worse description of a name the signature does list.
    """
    parameters = sig.parameters
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
        return None
    for name in kwargs:
        if name not in parameters:
            return name
    return None


from sonolus.script import globals as sonolus_globals
from sonolus.script.internal import constant, generic, tuple_impl
from sonolus.script.internal.value import Value
from sonolus.script.num import Num

BUILTIN_IMPLS = {}
