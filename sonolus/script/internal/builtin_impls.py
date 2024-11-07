from collections.abc import Iterable
from typing import overload

from sonolus.script.internal.context import ctx
from sonolus.script.internal.impl import meta_fn, validate_value
from sonolus.script.iterator import ArrayLike, Enumerator, SonolusIterator
from sonolus.script.math import MATH_BUILTIN_IMPLS
from sonolus.script.num import is_num
from sonolus.script.range import Range


@meta_fn
def _isinstance(value, type_):
    value = validate_value(value)
    type_ = validate_value(type_)._as_py_()
    return validate_value(isinstance(value, type_))


@meta_fn
def _len(value):
    from sonolus.backend.visitor import compile_and_call

    value = validate_value(value)
    if not hasattr(value, "__len__"):
        raise TypeError(f"object of type '{type(value).__name__}' has no len()")
    return compile_and_call(value.__len__)


@meta_fn
def _enumerate(iterable, start=0):
    from sonolus.backend.visitor import compile_and_call

    iterable = validate_value(iterable)
    if not hasattr(iterable, "__iter__"):
        raise TypeError(f"'{type(iterable).__name__}' object is not iterable")
    if isinstance(iterable, ArrayLike):
        return compile_and_call(iterable.enumerate, start)
    else:
        iterator = compile_and_call(iterable.__iter__)
        if not isinstance(iterator, SonolusIterator):
            raise TypeError("Only subclasses of SonolusIterator are supported as iterators")
        return Enumerator(0, start, iterator)


@meta_fn
def _abs(value):
    from sonolus.backend.visitor import compile_and_call

    value = validate_value(value)
    if not hasattr(value, "__abs__"):
        raise TypeError(f"bad operand type for abs(): '{type(value).__name__}'")
    return compile_and_call(value.__abs__)


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
        if not all(is_num(arg) for arg in args):
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
        if not all(is_num(arg) for arg in args):
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


BUILTIN_IMPLS = {
    id(isinstance): _isinstance,
    id(len): _len,
    id(enumerate): _enumerate,
    id(abs): _abs,
    id(max): _max,
    id(min): _min,
    id(range): Range,
    **MATH_BUILTIN_IMPLS,
}
