import math
from collections.abc import Iterable
from typing import overload

from sonolus.backend.ops import Op
from sonolus.script.internal.context import ctx
from sonolus.script.internal.impl import meta_fn, validate_value
from sonolus.script.internal.native import native_function
from sonolus.script.iterator import ArrayLike
from sonolus.script.num import Num


@native_function(Op.Sin)
def sin(x: float) -> float:
    return math.sin(x)


@native_function(Op.Cos)
def cos(x: float) -> float:
    return math.cos(x)


@native_function(Op.Tan)
def tan(x: float) -> float:
    return math.tan(x)


@native_function(Op.Arcsin)
def asin(x: float) -> float:
    return math.asin(x)


@native_function(Op.Arccos)
def acos(x: float) -> float:
    return math.acos(x)


@native_function(Op.Arctan)
def atan(x: float) -> float:
    return math.atan(x)


@native_function(Op.Arctan2)
def atan2(y: float, x: float) -> float:
    return math.atan2(y, x)


@native_function(Op.Sinh)
def sinh(x: float) -> float:
    return math.sinh(x)


@native_function(Op.Cosh)
def cosh(x: float) -> float:
    return math.cosh(x)


@native_function(Op.Tanh)
def tanh(x: float) -> float:
    return math.tanh(x)


@native_function(Op.Abs)
def _abs(x: float) -> float:
    return abs(x)


@overload
def _max[T](iterable: Iterable[T]) -> T: ...


@overload
def _max[T](a: T, b: T, *args: T) -> T: ...


@meta_fn
def _max(*args):
    from sonolus.backend.visitor import compile_and_call

    args = tuple(validate_value(arg) for arg in args)
    if len(args) == 0:
        raise ValueError("Expected at least one argument to max")
    elif len(args) == 1:
        (iterable,) = args
        if isinstance(iterable, ArrayLike):
            return iterable.max()
        else:
            raise TypeError(f"Unsupported type: {type(iterable)} for max")
    else:
        if not all(isinstance(arg, Num) for arg in args):
            raise TypeError("Arguments to max must be numbers")
        if ctx():
            result = compile_and_call(_max2, args[0], args[1])
            for arg in args[2:]:
                result = compile_and_call(_max2, result, arg)
            return result
        else:
            return max(arg._as_py_() for arg in args)


def _max2(a, b):
    if a > b:
        return a
    else:
        return b


@overload
def _min[T](iterable: Iterable[T]) -> T: ...


@overload
def _min[T](a: T, b: T, *args: T) -> T: ...


@meta_fn
def _min(*args):
    from sonolus.backend.visitor import compile_and_call

    args = tuple(validate_value(arg) for arg in args)
    if len(args) == 0:
        raise ValueError("Expected at least one argument to min")
    elif len(args) == 1:
        (iterable,) = args
        if isinstance(iterable, ArrayLike):
            return iterable.min()
        else:
            raise TypeError(f"Unsupported type: {type(iterable)} for min")
    else:
        if not all(isinstance(arg, Num) for arg in args):
            raise TypeError("Arguments to min must be numbers")
        if ctx():
            result = compile_and_call(_min2, args[0], args[1])
            for arg in args[2:]:
                result = compile_and_call(_min2, result, arg)
            return result
        else:
            return min(arg._as_py_() for arg in args)


def _min2(a, b):
    if a < b:
        return a
    else:
        return b


MATH_BUILTIN_IMPLS = {
    id(math.sin): sin,
    id(math.cos): cos,
    id(math.tan): tan,
    id(math.asin): asin,
    id(math.acos): acos,
    id(math.atan): atan,
    id(math.atan2): atan2,
    id(math.sinh): sinh,
    id(math.cosh): cosh,
    id(math.tanh): tanh,
    id(max): _max,
    id(min): _min,
    id(abs): _abs,
}
