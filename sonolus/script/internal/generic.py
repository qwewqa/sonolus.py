from __future__ import annotations

import typing
from enum import Enum
from types import UnionType
from typing import Annotated, Any, ClassVar, Literal, Self, TypeVar, get_origin

from sonolus.script.internal.impl import meta_fn, validate_value
from sonolus.script.internal.value import Value

type AnyType = type[Value] | PartialGeneric | TypeVar


def validate_type_arg(arg: Any) -> Any:
    arg = validate_value(arg)
    if not arg._is_py_():
        raise TypeError(f"Expected a compile-time constant type argument, got {arg}")
    result = arg._as_py_()
    if hasattr(result, "_type_mapping_"):
        return result._type_mapping_
    if get_origin(result) is Annotated:
        return result.__args__[0]
    if get_origin(result) is Literal:
        return result.__args__[0]
    return result


def validate_type_spec(spec: Any) -> PartialGeneric | TypeVar | type[Value]:
    spec = validate_type_arg(spec)
    if isinstance(spec, type) and issubclass(spec, Enum):
        # For values like IntEnum subclasses, this will call validate_type_spec(IntEnum),
        # which in turn will call it on int, so this works.
        spec = validate_type_spec(spec.__mro__[1])
    if isinstance(spec, PartialGeneric | TypeVar) or (isinstance(spec, type) and issubclass(spec, Value)):
        return spec
    if typing.get_origin(spec) is UnionType:
        args = typing.get_args(spec)
        validated_args = {validate_type_arg(arg) for arg in args}
        if len(validated_args) == 1:
            return validated_args.pop()
    raise TypeError(f"Invalid type spec: {spec}")


def validate_concrete_type(spec: Any) -> type[Value]:
    spec = validate_type_spec(spec)
    if isinstance(spec, type) and issubclass(spec, Value) and spec._is_concrete_():
        return spec
    raise TypeError(f"Expected a concrete type, got {spec}")


def validate_type_args(args) -> tuple[Any, ...]:
    if not isinstance(args, tuple):
        args = (args,)
    return tuple(validate_type_arg(arg) for arg in args)


def contains_incomplete_type(args) -> bool:
    if not isinstance(args, tuple):
        args = (args,)
    return any(isinstance(arg, TypeVar | PartialGeneric) for arg in args)


def format_type_arg(arg: Any) -> str:
    if isinstance(arg, type):
        return arg.__name__
    return f"{arg}"


class GenericValue(Value):
    _parameterized_: ClassVar[dict[tuple[Any, ...], type[Self]]] = {}
    _type_args_: ClassVar[tuple[Any, ...] | None] = None
    _type_vars_to_args_: ClassVar[dict[TypeVar, Any] | None] = None

    def __init__(self):
        if self._type_args_ is None:
            raise TypeError(f"Missing type arguments for {self.__class__.__name__}")

    @classmethod
    def _validate_type_args_(cls, args: tuple[Any, ...]) -> tuple[Any, ...]:
        """Validate the type arguments and return them as a tuple.

        This may be called with PartialGeneric or TypeVar instances inside args.
        """
        if len(args) != len(cls.__type_params__):
            raise TypeError(f"Expected {len(cls.__type_params__)} type arguments, got {len(args)}")
        return args

    @classmethod
    def _is_concrete_(cls) -> bool:
        return cls._type_args_ is not None

    @classmethod
    @meta_fn
    def type_var_value(cls, var: TypeVar) -> Any:
        if isinstance(var, Value):
            var = var._as_py_()
        if cls._type_args_ is None:
            raise TypeError(f"Type {cls.__name__} is not parameterized")
        if var in cls._type_vars_to_args_:
            return cls._type_vars_to_args_[var]
        raise TypeError(f"Missing type argument for {var}")

    def __class_getitem__(cls, args: Any) -> type[Self]:
        if cls._type_args_ is not None:
            raise TypeError(f"Type {cls.__name__} is already parameterized")
        args = validate_type_args(args)
        args = cls._validate_type_args_(args)
        if contains_incomplete_type(args):
            return PartialGeneric(cls, args)
        if args not in cls._parameterized_:
            cls._parameterized_[args] = cls._get_parameterized(args)
        return cls._parameterized_[args]

    @classmethod
    def _get_parameterized(cls, args: tuple[Any, ...]) -> type[Self]:
        class Parameterized(cls):
            _type_args_ = args
            _type_vars_to_args_ = dict(zip(cls.__type_params__, args, strict=True))  # noqa: RUF012

        if args:
            Parameterized.__name__ = f"{cls.__name__}[{', '.join(format_type_arg(arg) for arg in args)}]"
            Parameterized.__qualname__ = f"{cls.__qualname__}[{', '.join(format_type_arg(arg) for arg in args)}]"
        else:
            Parameterized.__name__ = cls.__name__
            Parameterized.__qualname__ = cls.__qualname__
        Parameterized.__module__ = cls.__module__
        return Parameterized


class PartialGeneric[T: GenericValue]:
    def __init__(self, base: type[T], args: tuple[Any, ...]):
        self.base = base
        self.args = args

    def __repr__(self):
        return f"PartialGeneric({self.base!r}, {self.args!r})"

    def __str__(self):
        params = ", ".join(format_type_arg(arg) for arg in self.args)
        return f"{self.base.__name__}?[{params}]"

    def __call__(self, *args, **kwargs):
        instance = self.base(*args, **kwargs)
        # Throw an error if it fails
        accept_and_infer_types(self, instance, {})
        return instance


def infer_and_validate_types(dst: Any, src: Any, results: dict[TypeVar, Any] | None = None) -> dict[TypeVar, Any]:
    results = results if results is not None else {}
    match dst:
        case TypeVar():
            if dst in results:
                if results[dst] != src:
                    raise TypeError(
                        f"Conflicting types for {dst}: {format_type_arg(results[dst])} and {format_type_arg(src)}"
                    )
            else:
                results[dst] = validate_type_arg(src)
        case PartialGeneric():
            if not isinstance(src, type) or not issubclass(src, dst.base):
                raise TypeError(f"Expected type {dst.base.__name__}, got {format_type_arg(src)}")
            assert issubclass(src, GenericValue)
            for d, s in zip(dst.args, src._type_args_, strict=True):
                infer_and_validate_types(d, s, results)
        case _:
            if (
                src != dst  # noqa: PLR1714
                and dst != Any
                and not (isinstance(dst, type) and isinstance(src, type) and issubclass(src, dst))
            ):
                raise TypeError(f"Expected {format_type_arg(dst)}, got {format_type_arg(src)}")
    return results


def accept_and_infer_types(dst: Any, val: Any, results: dict[TypeVar, Any]) -> Value:
    val = validate_value(val)
    match dst:
        case TypeVar():
            infer_and_validate_types(dst, type(val), results)
            return val
        case PartialGeneric():
            val = dst.base._accept_(val)
            infer_and_validate_types(dst, type(val), results)
            return val
        case type():
            return dst._accept_(val)
        case _:
            raise TypeError(f"Expected a type, got {format_type_arg(dst)}")


def validate_and_resolve_type(dst: Any, results: dict[TypeVar, Any]) -> Any:
    match dst:
        case TypeVar():
            if dst in results:
                return results[dst]
            raise TypeError(f"Missing type for {dst}")
        case PartialGeneric():
            return dst.base[tuple(validate_and_resolve_type(arg, results) for arg in dst.args)]
        case _:
            return dst
