from abc import ABC
from collections.abc import Iterable
from typing import overload

from sonolus.script.array_like import ArrayLike
from sonolus.script.internal.context import ctx
from sonolus.script.internal.dict_impl import DictImpl
from sonolus.script.internal.impl import meta_fn, validate_value
from sonolus.script.internal.math_impls import MATH_BUILTIN_IMPLS, _trunc
from sonolus.script.internal.random import RANDOM_BUILTIN_IMPLS
from sonolus.script.internal.range import Range
from sonolus.script.internal.tuple_impl import TupleImpl
from sonolus.script.internal.value import Value
from sonolus.script.iterator import (
    SonolusIterator,
    _EmptyIterator,
    _Enumerator,
    _FilteringIterator,
    _MappingIterator,
    _Zipper,
)
from sonolus.script.num import Num, _is_num


@meta_fn
def _isinstance(value, type_):
    value = validate_value(value)
    type_ = validate_value(type_)._as_py_()
    if type_ is dict:
        return isinstance(value, DictImpl)
    if type_ is tuple:
        return isinstance(value, TupleImpl)
    if type_ in {_int, _float, _bool}:
        raise TypeError("Instance check against int, float, or bool is not supported, use Num instead")
    if not (isinstance(type_, type) and issubclass(type_, Value | ABC)):
        raise TypeError(f"Unsupported type: {type_} for isinstance")
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
        return compile_and_call(iterable._enumerate_, start)
    else:
        iterator = compile_and_call(iterable.__iter__)
        if not isinstance(iterator, SonolusIterator):
            raise TypeError("Only subclasses of SonolusIterator are supported as iterators")
        return _Enumerator(0, start, iterator)


@meta_fn
def _reversed(iterable):
    from sonolus.backend.visitor import compile_and_call

    iterable = validate_value(iterable)
    if not isinstance(iterable, ArrayLike):
        raise TypeError(f"Unsupported type: {type(iterable)} for reversed")
    return compile_and_call(iterable.__reversed__)


@meta_fn
def _zip(*iterables):
    from sonolus.backend.visitor import compile_and_call
    from sonolus.script.containers import Pair

    if not iterables:
        return _EmptyIterator()

    iterables = [validate_value(iterable) for iterable in iterables]
    if any(isinstance(iterable, TupleImpl) for iterable in iterables):
        if not all(isinstance(iterable, TupleImpl) for iterable in iterables):
            raise TypeError("Cannot mix tuples with other types in zip")
        return TupleImpl._accept_(tuple(zip(*(iterable._as_py_() for iterable in iterables), strict=False)))
    iterators = [compile_and_call(iterable.__iter__) for iterable in iterables]
    if not all(isinstance(iterator, SonolusIterator) for iterator in iterators):
        raise TypeError("Only subclasses of SonolusIterator are supported as iterators")
    v = iterators.pop()
    while iterators:
        v = Pair(iterators.pop(), v)
    return _Zipper(v)


@meta_fn
def _abs(value):
    from sonolus.backend.visitor import compile_and_call

    value = validate_value(value)
    if not hasattr(value, "__abs__"):
        raise TypeError(f"bad operand type for abs(): '{type(value).__name__}'")
    return compile_and_call(value.__abs__)


def _identity(value):
    return value


@overload
def _max[T](iterable: Iterable[T], *, key: callable = ...) -> T: ...


@overload
def _max[T](a: T, b: T, *args: T, key: callable = ...) -> T: ...


@meta_fn
def _max(*args, key: callable = _identity):
    from sonolus.backend.visitor import compile_and_call

    args = tuple(validate_value(arg) for arg in args)
    if len(args) == 0:
        raise ValueError("Expected at least one argument to max")
    elif len(args) == 1:
        (iterable,) = args
        if isinstance(iterable, ArrayLike):
            return compile_and_call(iterable._max_, key=key)
        else:
            raise TypeError(f"Unsupported type: {type(iterable)} for max")
    else:
        if not all(_is_num(arg) for arg in args):
            raise TypeError("Arguments to max must be numbers")
        if ctx():
            result = compile_and_call(_max2, args[0], args[1], key=key)
            for arg in args[2:]:
                result = compile_and_call(_max2, result, arg, key=key)
            return result
        else:
            return max(arg._as_py_() for arg in args)


def _max2(a, b, key=_identity):
    if key(a) > key(b):
        return a
    else:
        return b


@overload
def _min[T](iterable: Iterable[T], *, key: callable = ...) -> T: ...


@overload
def _min[T](a: T, b: T, *args: T, key: callable = ...) -> T: ...


@meta_fn
def _min(*args, key: callable = _identity):
    from sonolus.backend.visitor import compile_and_call

    args = tuple(validate_value(arg) for arg in args)
    if len(args) == 0:
        raise ValueError("Expected at least one argument to min")
    elif len(args) == 1:
        (iterable,) = args
        if isinstance(iterable, ArrayLike):
            return compile_and_call(iterable._min_, key=key)
        else:
            raise TypeError(f"Unsupported type: {type(iterable)} for min")
    else:
        if not all(_is_num(arg) for arg in args):
            raise TypeError("Arguments to min must be numbers")
        if ctx():
            result = compile_and_call(_min2, args[0], args[1], key=key)
            for arg in args[2:]:
                result = compile_and_call(_min2, result, arg, key=key)
            return result
        else:
            return min(arg._as_py_() for arg in args)


def _min2(a, b, key=_identity):
    if key(a) < key(b):
        return a
    else:
        return b


@meta_fn
def _callable(value):
    return callable(value)


def _map(fn, iterable, *iterables):
    return _MappingIterator(lambda args: fn(*args), zip(iterable, *iterables))  # noqa: B905


def _filter(fn, iterable):
    if fn is None:
        fn = _identity
    return _FilteringIterator(fn, iterable.__iter__())  # noqa: PLC2801


@meta_fn
def _int(value=0):
    value = validate_value(value)
    if not _is_num(value):
        raise TypeError("Only numeric arguments to int() are supported")
    return _trunc(value)


@meta_fn
def _float(value=0.0):
    value = validate_value(value)
    if not _is_num(value):
        raise TypeError("Only numeric arguments to float() are supported")
    return value


@meta_fn
def _bool(value=False):
    value = validate_value(value)
    if not _is_num(value):
        raise TypeError("Only numeric arguments to bool() are supported")
    return value != 0


_int._type_mapping_ = Num
_float._type_mapping_ = Num
_bool._type_mapping_ = Num

# classmethod, property, staticmethod are supported as decorators, but not within functions

BUILTIN_IMPLS = {
    id(abs): _abs,
    id(bool): _bool,
    id(callable): _callable,
    id(enumerate): _enumerate,
    id(filter): _filter,
    id(float): _float,
    id(int): _int,
    id(isinstance): _isinstance,
    id(len): _len,
    id(map): _map,
    id(max): _max,
    id(min): _min,
    id(range): Range,
    id(reversed): _reversed,
    id(zip): _zip,
    **MATH_BUILTIN_IMPLS,  # Includes round
    **RANDOM_BUILTIN_IMPLS,
}
