import builtins
import inspect
import math
import random as pyrandom
from enum import Enum
from types import FunctionType
from typing import Any, Never, assert_never

from sonolus.backend.ops import Op
from sonolus.script.array import Array
from sonolus.script.array_like import ArrayLike
from sonolus.script.debug import assert_true, error, require
from sonolus.script.internal import impl
from sonolus.script.internal.context import ctx
from sonolus.script.internal.dict_impl import DictImpl
from sonolus.script.internal.impl import validate_value
from sonolus.script.internal.math_impls import MATH_BUILTIN_IMPLS, _trunc
from sonolus.script.internal.meta_fn import meta_fn
from sonolus.script.internal.native import native_function
from sonolus.script.internal.random import RANDOM_BUILTIN_IMPLS
from sonolus.script.internal.range import Range
from sonolus.script.internal.set_impl import SetImpl
from sonolus.script.internal.tuple_impl import TupleImpl, has_tuple_iter, tuple_iter
from sonolus.script.internal.value import Value
from sonolus.script.iterator import (
    SonolusIterator,
    _EmptyIterator,
    _Enumerator,
    _FilteringIterator,
    _MappingIterator,
    _validate_next_result,
    _Zipper,
)
from sonolus.script.num import Num, _is_num
from sonolus.script.record import Record

_empty = object()


def _special_method(value: Any, name: str) -> Any | None:
    from sonolus.script.internal.visitor import _SPECIAL_METHOD_MISSING, _bind_special_method

    method = _bind_special_method(value, name)
    return None if method is _SPECIAL_METHOD_MISSING or method is None else method


def _type_name(value) -> str:
    """A readable type name for a value, for use in error messages.

    `type(value).__name__` is not usable directly: a compile-time constant is wrapped in a per-value ConstantValue
    subclass named after the wrapped value rather than after its type, and DictImpl and SetImpl are generics whose
    type arguments spell out the same wrappers.
    """
    from sonolus.script.internal.constant import ConstantValue

    if isinstance(value, ConstantValue):
        py_value = value._as_py_()
        # A class's own type is its metaclass (RecordMeta, EnumType, ...), which is an internal detail; report
        # classes the way Python does instead.
        if isinstance(py_value, (type, *_BUILTIN_TYPE_SHIMS)):
            return "type"
        return type(py_value).__name__
    if isinstance(value, _BUILTIN_TYPE_SHIMS):
        return "type"
    if isinstance(value, SetImpl):
        return "set"
    if isinstance(value, DictImpl):
        return "dict"
    return type(value).__name__


def _property_has_no_setter(target, name):
    return AttributeError(f"property '{name}' of '{_type_name(target)}' object has no setter")


def _unwrap_set(value):
    """Return the value that carries a set's elements, leaving anything else alone."""
    return value._dict if isinstance(value, SetImpl) else value


def _class_arg_name(value) -> str:
    """A readable description of the class argument to isinstance() or issubclass()."""
    if id(value) in _BUILTIN_ALIAS_NAMES:
        return _BUILTIN_ALIAS_NAMES[id(value)]
    if isinstance(value, SetImpl | DictImpl):
        return _type_name(value)
    if isinstance(value, tuple):
        # A tuple of classes is described element by element, since a set or dict nested in one would otherwise
        # print its own repr.
        inner = ", ".join(_class_arg_name(item) for item in value)
        return f"({inner},)" if len(value) == 1 else f"({inner})"
    return str(value)


def _comptime_iter_result(items) -> TupleImpl:
    """Wrap the result of a compile-time `zip`, `enumerate`, or `reversed` as a tuple."""
    return TupleImpl._accept_(tuple(items))


def _compile_time_iterable_kind(value) -> str | None:
    if isinstance(value, TupleImpl):
        return "a tuple"
    if isinstance(value, DictImpl):
        return "a dict"
    if isinstance(value, SetImpl):
        return "a set"
    if value._is_py_():
        py_value = value._as_py_()
        if isinstance(py_value, type) and issubclass(py_value, Enum):
            return "an enum class"
    return None


def _resolve_class_arg(value, check: str):
    """Map a builtin type to the internal type representing it, mirroring the aliases isinstance() accepts.

    `check` names the kind of check for the error messages, and is "Instance" or "Subclass".
    """
    if value is dict or value is _dict:
        return DictImpl
    if value is set or value is _set:
        return SetImpl
    if value is frozenset:
        raise TypeError(f"{check} check against frozenset is not supported")
    if value is tuple:
        return TupleImpl
    if value is range or value is _range:
        return Range
    if value is _int or value is _float or value is _bool:
        raise TypeError(f"{check} check against int, float, or bool is not supported, use Num instead")
    return value


# Builtin aliases resolve to internal types that subclass Record or TransientValue, so a plain issubclass would
# expose an implementation-only hierarchy. Anything outside the aliases must fail instead.
_ALIAS_REPRS = (DictImpl, SetImpl, TupleImpl, Range)


def _alias_repr_isolated(cls) -> bool:
    """Whether cls is an internal representation of a builtin type alias."""
    return isinstance(cls, type) and issubclass(cls, _ALIAS_REPRS)


def _matches_outside_alias_repr(candidate, base) -> bool:
    """Whether candidate is allowed to match base, given the alias representations sit outside the public tree."""
    return _alias_repr_isolated(base) or not _alias_repr_isolated(candidate)


def _comptime_class_arg(value, name: str, position: str):
    """Unwrap a class argument to isinstance()/issubclass(), which both fold entirely at compile time."""
    value = validate_value(value)
    if not value._is_py_():
        raise TypeError(f"{name}() arg {position} must be a class known at compile time")
    return value._as_py_()


def _check_classinfo(classinfo, matches, *, check: str, name: str, original) -> bool:
    """Evaluate an isinstance()/issubclass() check against classinfo, which may be a tuple of classes."""
    if isinstance(classinfo, tuple):
        return any(_check_classinfo(member, matches, check=check, name=name, original=original) for member in classinfo)
    classinfo = _resolve_class_arg(classinfo, check)
    if not (
        isinstance(classinfo, type)
        and (issubclass(classinfo, Value) or getattr(classinfo, "_allow_instance_check_", False))
    ):
        raise TypeError(f"Unsupported type: {_class_arg_name(original)} for {name}")
    return matches(classinfo)


@meta_fn
def _isinstance(value, type_):
    value = validate_value(value)
    type_ = _comptime_class_arg(type_, "isinstance", "2")
    result = _check_classinfo(
        type_,
        lambda cls: isinstance(value, cls) and _matches_outside_alias_repr(type(value), cls),
        check="Instance",
        name="isinstance",
        original=type_,
    )
    return validate_value(result)


@meta_fn
def _issubclass(cls, classinfo):
    cls = _comptime_class_arg(cls, "issubclass", "1")
    classinfo = _comptime_class_arg(classinfo, "issubclass", "2")
    # Only classinfo may be a tuple; a tuple as arg 1 falls through to the "must be a class" check, as in Python.
    cls = _resolve_class_arg(cls, "Subclass")
    if not isinstance(cls, type):
        raise TypeError("issubclass() arg 1 must be a class")
    result = _check_classinfo(
        classinfo,
        lambda base: issubclass(cls, base) and _matches_outside_alias_repr(cls, base),
        check="Subclass",
        name="issubclass",
        original=classinfo,
    )
    return validate_value(result)


@meta_fn
def _len(value):
    from sonolus.script.internal.visitor import compile_and_call

    value = validate_value(value)
    if has_tuple_iter(value):
        return len(tuple_iter(value))
    len_method = _special_method(value, "__len__")
    if len_method is None:
        raise TypeError(f"object of type '{_type_name(value)}' has no len()")
    return _validate_len_result(compile_and_call(len_method))


def _validate_len_result(length):
    if ctx() and not ctx().live:
        return Num._accept_(0)
    length = validate_value(length)
    if not _is_num(length):
        raise TypeError(f"Invalid type for __len__: {_type_name(length)}")
    assert_true(Num.and_(length >= 0, length % 1 == 0), "__len__() must return a non-negative integer")
    return length


@meta_fn
def _enumerate(iterable, start=0):
    from sonolus.script.internal.visitor import (
        _bind_special_method,
        compile_and_call,
        reject_custom_record_getattribute,
    )

    iterable = _unwrap_set(validate_value(iterable))
    start = Num._accept_(start)
    assert_true(start % 1 == 0, "enumerate() start must be an integer")
    if has_tuple_iter(iterable):
        return _comptime_iter_result((start + i, value) for i, value in enumerate(tuple_iter(iterable)))
    iter_method = _special_method(iterable, "__iter__")
    if iter_method is None:
        raise TypeError(f"'{_type_name(iterable)}' object is not iterable")
    if isinstance(iterable, ArrayLike):
        reject_custom_record_getattribute(iterable)
        return compile_and_call(_bind_special_method(iterable, "_enumerate_"), start)
    iterator = compile_and_call(iter_method)
    if not ctx().live:
        return validate_value(())
    if not isinstance(iterator, SonolusIterator):
        raise TypeError("Only subclasses of SonolusIterator are supported as iterators")
    return _Enumerator(0, start, iterator)


@meta_fn
def _reversed(iterable):
    from sonolus.script.internal.visitor import (
        _bind_special_method,
        compile_and_call,
        reject_custom_record_getattribute,
    )

    iterable = validate_value(iterable)
    if has_tuple_iter(iterable):
        return _comptime_iter_result(reversed(tuple_iter(iterable)))
    if not isinstance(iterable, ArrayLike):
        raise TypeError(f"'{_type_name(iterable)}' object is not reversible")
    reject_custom_record_getattribute(iterable)
    return compile_and_call(_bind_special_method(iterable, "__reversed__"))


@meta_fn
def _zip(*iterables, strict: bool = False):
    from sonolus.script.containers import Pair
    from sonolus.script.internal.visitor import compile_and_call

    if validate_value(strict)._as_py_():  # type: ignore
        raise NotImplementedError("Strict zipping is not supported")

    if not iterables:
        return _EmptyIterator()

    iterables = [_unwrap_set(validate_value(iterable)) for iterable in iterables]
    if any(has_tuple_iter(iterable) for iterable in iterables):
        if not all(has_tuple_iter(iterable) for iterable in iterables):
            raise TypeError("Cannot mix tuples with other types in zip")
        return _comptime_iter_result(zip(*(tuple_iter(iterable) for iterable in iterables), strict=False))
    for iterable in iterables:
        # Checked explicitly so a non-iterable argument gets the same message as it would from iter(), rather than
        # an internal AttributeError naming the wrapper class.
        if _special_method(iterable, "__iter__") is None:
            raise TypeError(f"'{_type_name(iterable)}' object is not iterable")
    iterators = [compile_and_call(_special_method(iterable, "__iter__")) for iterable in iterables]
    if not ctx().live:
        return validate_value(())
    if not all(isinstance(iterator, SonolusIterator) for iterator in iterators):
        raise TypeError("Only subclasses of SonolusIterator are supported as iterators")
    v = iterators.pop()
    while iterators:
        v = Pair(iterators.pop(), v)
    return _Zipper(v)


@meta_fn
def _abs(value):
    from sonolus.script.internal.visitor import compile_and_call

    value = validate_value(value)
    abs_method = _special_method(value, "__abs__")
    if abs_method is None:
        raise TypeError(f"bad operand type for abs(): '{_type_name(value)}'")
    return compile_and_call(abs_method)


def _identity(value):
    return value


def _array_like_extremum(iterable, default, key, *, is_max: bool):
    from sonolus.script.array_like import _validate_extremum_default
    from sonolus.script.internal.visitor import (
        _bind_special_method,
        compile_and_call,
        reject_custom_record_getattribute,
    )

    reject_custom_record_getattribute(iterable)
    name = "max" if is_max else "min"
    plain = _bind_special_method(iterable, "_max_" if is_max else "_min_")
    if default is _empty:
        return compile_and_call(plain, key=key)
    default = validate_value(default)
    if not (_is_num(default) or isinstance(default, Record | Array)):
        raise TypeError(f"default argument to {name}() must be a number, record, or array, got '{_type_name(default)}'")
    length = _validate_len_result(compile_and_call(_bind_special_method(iterable, "__len__")))
    if not ctx().live:
        return default
    if length._is_py_():
        if length._as_py_() == 0:
            return default
        # Validate the unreachable default against an element so acceptance does not depend on whether length folds.
        result = compile_and_call(plain, key=key)
        _validate_extremum_default(result, default)
        return result
    with_default = _bind_special_method(iterable, "_max_with_default_" if is_max else "_min_with_default_")
    return compile_and_call(with_default, default, key=key)


@meta_fn
def _max(*args, default=_empty, key=None):
    from sonolus.script.internal.context import force_shared_runtime_owner_id
    from sonolus.script.internal.visitor import compile_and_call

    if _is_none_arg(key):
        key = _identity

    args = tuple(validate_value(arg) for arg in args)
    if len(args) == 0:
        raise ValueError("Expected at least one argument to max")
    elif len(args) == 1:
        iterable = _unwrap_set(args[0])
        if isinstance(iterable, ArrayLike):
            return _array_like_extremum(iterable, default, key, is_max=True)
        elif has_tuple_iter(iterable) and all(_is_num(v) for v in tuple_iter(iterable)):
            t = tuple_iter(iterable)
            if len(t) == 0:
                if default is not _empty:
                    return default
                raise ValueError("max() arg is an empty sequence")
            return compile_and_call(Array(*t)._max_, key=key)
        elif isinstance(iterable, SonolusIterator):
            if not (default is _empty or Num._accepts_(default)):
                raise TypeError("default argument must be a number")
            with force_shared_runtime_owner_id():
                return compile_and_call(
                    _max_num_iterator,
                    iterable,
                    Num._accept_(default) if default is not _empty else None,
                    key=key if key is not _identity else None,
                )
        else:
            raise TypeError(f"Unsupported type: '{_type_name(iterable)}' for max")
    else:
        if default is not _empty:
            raise TypeError("default argument is not supported for max with multiple arguments")
        if not all(_is_num(arg) for arg in args):
            raise TypeError("Arguments to max must be numbers")
        if ctx():
            result = _max2(args[0], args[1], key=key)
            for arg in args[2:]:
                result = _max2(result, arg, key=key)
            return result
        else:
            return max((arg._as_py_() for arg in args), key=key)


def _max2(a, b, key=_identity):
    from sonolus.script.internal.visitor import compile_and_call

    a = validate_value(a)
    b = validate_value(b)
    if _is_num(a) and _is_num(b) and key == _identity:
        return compile_and_call(_max2_num, a, b)
    return compile_and_call(_max2_generic, a, b, key=key)


@native_function(Op.Max, const_eval=True)
def _max2_num(a, b):
    if a > b:
        return a
    else:
        return b


def _max2_generic(a, b, key=_identity):
    # Return the FIRST argument on a key tie, matching Python's max() and the library's
    # single-iterable path (index_of_max uses a strict comparison).
    if key(b) > key(a):
        return b
    else:
        return a


def _max_num_iterator(iterable, default, key):
    iterator = iterable.__iter__()  # ruff: ignore[unnecessary-dunder-call]
    initial = _validate_next_result(iterator.next())
    if initial.is_nothing:
        require(default is not None, "default must be provided if the iterator is empty")
        return default
    if key is not None:
        result = initial.get_unsafe()
        best_key = key(result)
        for value in iterator:
            new_key = key(value)
            if new_key > best_key:
                result = value
                best_key = new_key
        return result
    else:
        result = initial.get_unsafe()
        for value in iterator:
            if value > result:  # ruff: ignore[if-stmt-min-max]
                result = value
        return result


@meta_fn
def _min(*args, default=_empty, key=None):
    from sonolus.script.internal.context import force_shared_runtime_owner_id
    from sonolus.script.internal.visitor import compile_and_call

    if _is_none_arg(key):
        key = _identity

    args = tuple(validate_value(arg) for arg in args)
    if len(args) == 0:
        raise ValueError("Expected at least one argument to min")
    elif len(args) == 1:
        iterable = _unwrap_set(args[0])
        if isinstance(iterable, ArrayLike):
            return _array_like_extremum(iterable, default, key, is_max=False)
        elif has_tuple_iter(iterable) and all(_is_num(v) for v in tuple_iter(iterable)):
            t = tuple_iter(iterable)
            if len(t) == 0:
                if default is not _empty:
                    return default
                raise ValueError("min() arg is an empty sequence")
            return compile_and_call(Array(*t)._min_, key=key)
        elif isinstance(iterable, SonolusIterator):
            if not (default is _empty or Num._accepts_(default)):
                raise TypeError("default argument must be a number")
            with force_shared_runtime_owner_id():
                return compile_and_call(
                    _min_num_iterator,
                    iterable,
                    Num._accept_(default) if default is not _empty else None,
                    key=key if key is not _identity else None,
                )
        else:
            raise TypeError(f"Unsupported type: '{_type_name(iterable)}' for min")
    else:
        if default is not _empty:
            raise TypeError("default argument is not supported for min with multiple arguments")
        if not all(_is_num(arg) for arg in args):
            raise TypeError("Arguments to min must be numbers")
        if ctx():
            result = _min2(args[0], args[1], key=key)
            for arg in args[2:]:
                result = _min2(result, arg, key=key)
            return result
        else:
            return min((arg._as_py_() for arg in args), key=key)


def _min2(a, b, key=_identity):
    from sonolus.script.internal.visitor import compile_and_call

    a = validate_value(a)
    b = validate_value(b)
    if _is_num(a) and _is_num(b) and key == _identity:
        return compile_and_call(_min2_num, a, b)
    return compile_and_call(_min2_generic, a, b, key=key)


@native_function(Op.Min, const_eval=True)
def _min2_num(a, b):
    if a < b:
        return a
    else:
        return b


def _min2_generic(a, b, key=_identity):
    # Return the FIRST argument on a key tie, matching Python's min() and the library's
    # single-iterable path (index_of_min uses a strict comparison).
    if key(b) < key(a):
        return b
    else:
        return a


def _min_num_iterator(iterable, default, key):
    iterator = iterable.__iter__()  # ruff: ignore[unnecessary-dunder-call]
    initial = _validate_next_result(iterator.next())
    if initial.is_nothing:
        require(default is not None, "default must be provided if the iterator is empty")
        return default
    if key is not None:
        result = initial.get_unsafe()
        best_key = key(result)
        for value in iterator:
            new_key = key(value)
            if new_key < best_key:
                result = value
                best_key = new_key
        return result
    else:
        result = initial.get_unsafe()
        for value in iterator:
            if value < result:  # ruff: ignore[if-stmt-min-max]
                result = value
        return result


@meta_fn
def _callable(value):
    value = validate_value(value)
    if value._is_py_():
        value = value._as_py_()
    return validate_value(callable(value))


def _map_over_compile_time_iterables(fn, *iterables):
    """map() over compile-time iterables, written as an ordinary generator function.

    zip() stops at the shortest iterable, matching Python's map() and the runtime path.
    """
    for args in zip(*iterables):  # ruff: ignore[zip-without-explicit-strict]
        yield fn(*args)


@meta_fn
def _map(fn, iterable, *iterables):
    """map(), dispatching between the compile-time iterable path and the runtime iterator path.

    Tuples, dicts, sets, and enum classes are unrolled at compile time and have no runtime iterator, so they get a
    compiled generator function instead of going through _MappingIterator. Either way the result is a lazy
    iterator, as in Python.
    """
    from sonolus.script.containers import Pair
    from sonolus.script.internal.visitor import compile_and_call

    all_iterables = [_unwrap_set(validate_value(it)) for it in (iterable, *iterables)]
    if any(has_tuple_iter(it) for it in all_iterables):
        # Checked here rather than being left to the zip() inside the helper so that the message names map(),
        # which is what the user wrote.
        if not all(has_tuple_iter(it) for it in all_iterables):
            raise TypeError("Cannot mix compile-time iterables (tuple, dict, set, enum class) with other types in map")
        return compile_and_call(_map_over_compile_time_iterables, fn, *all_iterables)
    for it in all_iterables:
        if _special_method(it, "__iter__") is None:
            raise TypeError(f"'{_type_name(it)}' object is not iterable")
    iterators = []
    for it in all_iterables:
        iterator = compile_and_call(_special_method(it, "__iter__"))
        if not ctx().live:
            return _EmptyIterator()
        iterators.append(_validate_iterator_result(iterator))
    if len(iterators) == 1:
        return _MappingIterator(fn, iterators[0])
    chain = iterators.pop()
    while iterators:
        chain = Pair(iterators.pop(), chain)
    return compile_and_call(_map_zipped_runtime, fn, _Zipper(chain))


def _map_zipped_runtime(fn, iterator):
    return _MappingIterator(lambda args: fn(*args), iterator)


def _is_none_arg(fn) -> bool:
    """Whether an argument is None, whether it arrived raw or wrapped as a compile-time constant."""
    if fn is None:
        return True
    fn = validate_value(fn)
    return fn._is_py_() and fn._as_py_() is None


def _filter_over_compile_time_iterable(fn, iterable):
    for value in iterable:
        if fn(value):
            yield value


@meta_fn
def _filter(fn, iterable):
    from sonolus.script.internal.visitor import compile_and_call

    if _is_none_arg(fn):
        fn = _identity
    iterable = _unwrap_set(validate_value(iterable))
    if has_tuple_iter(iterable):
        return compile_and_call(_filter_over_compile_time_iterable, fn, iterable)
    iter_method = _special_method(iterable, "__iter__")
    if iter_method is None:
        raise TypeError(f"'{_type_name(iterable)}' object is not iterable")
    iterator = compile_and_call(iter_method)
    if not ctx().live:
        return _EmptyIterator()
    return compile_and_call(_filter_runtime, fn, _validate_iterator_result(iterator))


def _filter_runtime(fn, iterator):
    return _FilteringIterator(fn, iterator)


class _Int:
    _is_comptime_value_ = True
    _type_mapping_ = Num

    @meta_fn
    def __call__(self, value=0):
        value = validate_value(value)
        if not _is_num(value):
            raise TypeError("Only numeric arguments to int() are supported")
        return _trunc(value)

    def __or__(self, other):
        other = validate_value(other)
        if other._is_py_():
            other = other._as_py_()
        other = getattr(other, "_type_mapping_", other)
        return Num | other

    __ror__ = __or__


_int = _Int()


class _Float:
    _is_comptime_value_ = True
    _type_mapping_ = Num

    @meta_fn
    def __call__(self, value=0.0):
        value = validate_value(value)
        if not _is_num(value):
            raise TypeError("Only numeric arguments to float() are supported")
        return value

    def __or__(self, other):
        other = validate_value(other)
        if other._is_py_():
            other = other._as_py_()
        other = getattr(other, "_type_mapping_", other)
        return Num | other

    __ror__ = __or__


_float = _Float()


def _bool_by_compiling(value):
    """Convert a value to a boolean by putting it in a boolean context."""
    if value:  # ruff: ignore[needless-bool]
        return True
    else:
        return False


class _Bool:
    _is_comptime_value_ = True
    _type_mapping_ = Num

    @meta_fn
    def __call__(self, value=False):
        from sonolus.script.internal.visitor import compile_and_call

        value = validate_value(value)
        if _is_num(value):
            # Unlike a boolean context, which accepts any Num, bool() must normalize to 0/1. Compile-time
            # values still fold to a compile-time result.
            return Num._accept_(bool(value._as_py_())) if value._is_py_() else value != 0
        if value._is_py_() and _special_method(value, "__bool__") is None and _special_method(value, "__len__") is None:
            # Compile-time constants with no truthiness protocol of their own (strings, None, types, functions,
            # and Records defining neither __bool__ nor __len__) follow ordinary Python truthiness. Unwrapping
            # first matters: the wrapper itself is always truthy, so bool("") would return True otherwise.
            return Num._accept_(bool(value._as_py_()))
        # Everything else is converted by the compiler, since __bool__ and __len__ may return a runtime Num.
        return compile_and_call(_bool_by_compiling, value)

    def __or__(self, other):
        other = validate_value(other)
        if other._is_py_():
            other = other._as_py_()
        other = getattr(other, "_type_mapping_", other)
        return Num | other

    __ror__ = __or__


_bool = _Bool()


class _Set:
    _is_comptime_value_ = True
    _type_mapping_ = SetImpl

    @meta_fn
    def __call__(self, iterable=_empty):
        if iterable is _empty:
            return SetImpl.from_set(set())
        iterable = validate_value(iterable)
        if isinstance(iterable, SetImpl):
            return iterable
        if has_tuple_iter(iterable):
            return SetImpl.from_set(tuple_iter(iterable))
        raise TypeError(f"'{_type_name(iterable)}' object is not iterable")

    @meta_fn
    def __getitem__(self, item):
        return self

    def __or__(self, other):
        other = validate_value(other)
        if other._is_py_():
            other = other._as_py_()
        other = getattr(other, "_type_mapping_", other)
        return SetImpl | other

    __ror__ = __or__


_set = _Set()


class _Dict:
    _is_comptime_value_ = True
    _type_mapping_ = DictImpl

    @meta_fn
    def __call__(self, mapping_or_iterable=_empty, **kwargs):
        if mapping_or_iterable is _empty:
            if not kwargs:
                return DictImpl.from_dict({})
            return DictImpl.from_dict(kwargs)
        arg = validate_value(mapping_or_iterable)
        if isinstance(arg, DictImpl):
            if not kwargs:
                return arg
            return DictImpl.from_items((*arg.items(), *kwargs.items()))
        if has_tuple_iter(arg):
            items = tuple_iter(arg)
            result_items = []
            for item in items:
                item = validate_value(item)
                if not has_tuple_iter(item):
                    raise TypeError(f"cannot convert '{_type_name(item)}' object to dict items")
                kv = tuple_iter(item)
                if len(kv) != 2:
                    raise ValueError(f"dictionary update sequence element has length {len(kv)}; 2 is required")
                k, v = kv
                result_items.append((k, v))
            if kwargs:
                result_items.extend(kwargs.items())
            return DictImpl.from_items(result_items)
        raise TypeError(f"'{_type_name(arg)}' object is not a mapping")

    @meta_fn
    def __getitem__(self, item):
        return self

    def __or__(self, other):
        other = validate_value(other)
        if other._is_py_():
            other = other._as_py_()
        other = getattr(other, "_type_mapping_", other)
        return DictImpl | other

    __ror__ = __or__


_dict = _Dict()


class _Range:
    _is_comptime_value_ = True
    _type_mapping_ = Range

    @meta_fn
    def __call__(self, start, stop=None, step=1, /):
        from sonolus.script.internal.visitor import compile_and_call

        return compile_and_call(Range.frozen, start, stop, step)

    def __or__(self, other):
        other = validate_value(other)
        if other._is_py_():
            other = other._as_py_()
        other = getattr(other, "_type_mapping_", other)
        return Range | other

    __ror__ = __or__


_range = _Range()


def _any(iterable):
    for value in iterable:  # ruff: ignore[reimplemented-builtin]
        if value:
            return True
    return False


def _all(iterable):
    for value in iterable:  # ruff: ignore[reimplemented-builtin]
        if not value:
            return False
    return True


def contains_by_iteration(item, iterable):
    """Scan `iterable` for `item` when the iterable has no `__contains__` method."""
    for value in iterable:  # ruff: ignore[reimplemented-builtin]
        if value == item:
            return True
    return False


@meta_fn
def _require_sum_num(value, what):
    value = validate_value(value)
    if not _is_num(value):
        raise TypeError(
            f"sum() only supports numeric values, but the {validate_value(what)._as_py_()} has type "
            f"'{_type_name(value)}'. Accumulate non-numeric values with an explicit loop instead."
        )
    return value


def _sum(iterable, /, start=0):
    total = _require_sum_num(start, "start value")
    for value in iterable:
        total = total + _require_sum_num(value, "iterable element")  # ruff: ignore[non-augmented-assignment]
    return total


@meta_fn
def _detach_next_result(value):
    if not ctx():
        return value
    return validate_value(value)._get_readonly_()


@meta_fn
def _advance_iterator_once(iterator):
    from sonolus.script.internal.visitor import (
        _bind_special_method,
        compile_and_call,
        reject_custom_record_getattribute,
    )

    if not ctx():
        return iterator.next()
    reject_custom_record_getattribute(iterator)
    next_method = _bind_special_method(iterator, "next")
    return compile_and_call(next_method)


def _next(iterator):
    require(isinstance(iterator, SonolusIterator), "next() requires an instance of SonolusIterator")
    value = _validate_next_result(_advance_iterator_once(iterator))
    if value.is_some:
        return _detach_next_result(value.get_unsafe())
    error("Iterator has been exhausted")


@meta_fn
def _iter(iterable):
    from sonolus.script.internal.visitor import compile_and_call

    iterable = validate_value(iterable)
    kind = _compile_time_iterable_kind(iterable)
    if kind is not None:
        raise TypeError(
            f"Cannot call iter() on {kind}: {kind} is a compile-time construct and has no iterator; "
            "iterate over it directly in a for loop (or via map/filter/zip/enumerate/reversed), "
            "or use an Array if you need a runtime iterator"
        )
    iter_method = _special_method(iterable, "__iter__")
    if iter_method is None:
        raise TypeError(f"'{_type_name(iterable)}' object is not iterable")
    iterator = compile_and_call(iter_method)
    if not ctx().live:
        return _EmptyIterator()
    return _validate_iterator_result(iterator)


def _validate_iterator_result(iterator):
    if not isinstance(iterator, SonolusIterator):
        raise TypeError(f"iter() returned non-iterator of type '{_type_name(iterator)}'")
    return iterator


@meta_fn
def _super(*args):
    """Get the super class of a class or instance."""
    return super(*(arg._as_py_() if arg._is_py_() else arg for arg in args))


@meta_fn
def _hasattr(obj: Any, name: str) -> bool:
    from sonolus.script.internal.error import caused_by_attribute_error
    from sonolus.script.internal.visitor import compile_and_call

    try:
        compile_and_call(_getattr, obj, name)
    except Exception as e:
        if caused_by_attribute_error(e):
            return False
        raise
    context = ctx()
    return not context or context.live


@meta_fn
def _getattr(obj: Any, name: str, default=_empty) -> Any:
    from sonolus.script.internal.constant import ConstantValue
    from sonolus.script.internal.descriptor import SonolusDescriptor
    from sonolus.script.internal.error import caused_by_attribute_error
    from sonolus.script.internal.visitor import (
        _SPECIAL_METHOD_MISSING,
        _attribute_owner_name,
        _bind_special_method,
        _raise_getattr_attribute_error,
        _raise_property_getter_attribute_error,
        _raw_special_method,
        _resolve_super_descriptor,
        _super_proxy_parts,
        compile_and_call,
        reject_custom_record_getattribute,
        reject_instance_only_attribute,
    )

    name_value = validate_value(name)
    if not name_value._is_py_():
        raise TypeError(f"attribute name must be a compile-time string, not '{_type_name(name_value)}'")
    name = name_value._as_py_()
    if not isinstance(name, str):
        raise TypeError(f"attribute name must be string, not '{type(name).__name__}'")
    was_constant = isinstance(obj, ConstantValue)
    if was_constant:
        obj = obj._as_py_()
    reject_custom_record_getattribute(obj)
    if isinstance(obj, super):
        descriptor = _resolve_super_descriptor(obj, name)
        _, descriptor_target, descriptor_target_type = _super_proxy_parts(obj)
        if descriptor_target is descriptor_target_type:
            descriptor = None
        descriptor_found = descriptor is not None
    else:
        descriptor = None
        descriptor_target = obj
        descriptor_target_type = type(obj)
        descriptor_found = False
        for cls in type.mro(type(obj)):
            if name in cls.__dict__:
                descriptor = cls.__dict__[name]
                descriptor_found = True
                break
    match descriptor:
        case property(fget=getter):
            if getter is None:
                error = AttributeError(f"property '{name}' of '{descriptor_target_type.__name__}' object has no getter")
            else:
                try:
                    return compile_and_call(getter, descriptor_target)
                except Exception as e:
                    if not caused_by_attribute_error(e):
                        raise
                    _raise_property_getter_attribute_error(descriptor_target_type, name, e)
            fallback = _bind_special_method(obj, "__getattr__")
            if fallback is not _SPECIAL_METHOD_MISSING:
                try:
                    return compile_and_call(fallback, name)
                except Exception as e:
                    if not caused_by_attribute_error(e):
                        raise
                    _raise_getattr_attribute_error(type(obj), name, e)
            if default is not _empty:
                return default
            raise error
        case None if not descriptor_found and not was_constant and name not in getattr(obj, "__dict__", {}):
            fallback = _bind_special_method(obj, "__getattr__")
            if fallback is not _SPECIAL_METHOD_MISSING:
                try:
                    return compile_and_call(fallback, name)
                except Exception as e:
                    if not caused_by_attribute_error(e):
                        raise
                    _raise_getattr_attribute_error(type(obj), name, e)
            if default is not _empty:
                return default
            raise AttributeError(f"'{type(obj).__name__}' object has no attribute '{name}'")
        case SonolusDescriptor() | FunctionType() | classmethod() | staticmethod() | None:
            attribute = getattr(obj, name) if default is _empty else getattr(obj, name, default)
            if isinstance(obj, type):
                reject_instance_only_attribute(obj, name, attribute)
            return validate_value(attribute)
        case non_descriptor if _raw_special_method(type(non_descriptor), "__get__") is _SPECIAL_METHOD_MISSING:
            return validate_value(getattr(obj, name) if default is _empty else getattr(obj, name, default))
        case _:
            raise TypeError(f"Accessing attribute {name!r} on {_attribute_owner_name(obj)} is not supported")


@meta_fn
def _setattr(obj: Any, name: str, value: Any):
    from sonolus.script.internal.descriptor import SonolusDescriptor
    from sonolus.script.internal.visitor import (
        _attribute_owner_name,
        _resolve_descriptor,
        compile_and_call,
        reject_instance_only_attribute,
    )

    name_value = validate_value(name)
    if not name_value._is_py_():
        raise TypeError(f"attribute name must be a compile-time string, not '{_type_name(name_value)}'")
    name = name_value._as_py_()
    if not isinstance(name, str):
        raise TypeError(f"attribute name must be string, not '{type(name).__name__}'")
    if obj._is_py_():
        obj = obj._as_py_()
    descriptor = _resolve_descriptor(type(obj), name)
    match descriptor:
        case property(fset=setter):
            if setter is None:
                raise _property_has_no_setter(obj, name)
            compile_and_call(setter, obj, value)
        case SonolusDescriptor():
            setattr(obj, name, value)
        case _:
            if isinstance(obj, type):
                # The lookup above ran against the metaclass, which is what answers the class-level writes that
                # do work (archetype_score_multiplier). Everything else lands here, where the class's own
                # descriptors are what the author meant, so resolve those instead.
                reject_instance_only_attribute(obj, name, _resolve_descriptor(obj, name))
            raise TypeError(f"Assigning to attribute {name!r} on {_attribute_owner_name(obj)} is not supported")


class _Type(Record):
    @meta_fn
    def __call__(self, value, /):
        value = validate_value(value)
        if value._is_py_():
            value = value._as_py_()
        if isinstance(value, _BUILTIN_TYPE_SHIMS):
            return validate_value(_type)
        return validate_value(type(value))

    def __getitem__(self, item):
        return self


_type = _Type()

# The singleton stand-ins for the builtin types, which _type_name reports as `type` rather than by their own
# class names.
_BUILTIN_TYPE_SHIMS = (_Int, _Float, _Bool, _Set, _Dict, _Range, _Type)

# Keyed by id, matching _resolve_class_arg: these are singletons, and _Type is a Record whose __eq__ compares
# fields rather than identity.
_BUILTIN_ALIAS_NAMES = {
    id(_int): "int",
    id(_float): "float",
    id(_bool): "bool",
    id(_set): "set",
    id(_dict): "dict",
    id(_range): "range",
    id(_type): "type",
}


@meta_fn
def _assert_never(arg: Never, /):
    error("Expected code to be unreachable")


# classmethod, property, staticmethod are supported as decorators, but not within functions

BUILTIN_IMPLS = {
    id(abs): _abs,
    id(all): _all,
    id(any): _any,
    id(bool): _bool,
    id(callable): _callable,
    id(dict): _dict,
    id(enumerate): _enumerate,
    id(filter): _filter,
    id(float): _float,
    id(getattr): _getattr,
    id(hasattr): _hasattr,
    id(int): _int,
    id(isinstance): _isinstance,
    id(issubclass): _issubclass,
    id(iter): _iter,
    id(len): _len,
    id(map): _map,
    id(max): _max,
    id(min): _min,
    id(next): _next,
    id(range): _range,
    id(reversed): _reversed,
    id(set): _set,
    id(setattr): _setattr,
    id(sum): _sum,
    id(super): _super,
    id(type): _type,
    id(zip): _zip,
    id(assert_never): _assert_never,
    **MATH_BUILTIN_IMPLS,  # Includes round
    **RANDOM_BUILTIN_IMPLS,
}


def _build_impl_names() -> dict[int, str]:
    """Map the id of each impl in BUILTIN_IMPLS to the name an author writes for it."""
    names = {}
    for namespace in (vars(builtins), vars(math), vars(pyrandom), {"assert_never": assert_never}):
        for name, value in namespace.items():
            target = BUILTIN_IMPLS.get(id(value))
            if target is not None:
                names.setdefault(id(target), name)
    return names


BUILTIN_IMPL_NAMES = _build_impl_names()


def name_builtin_in_binding_error(fn: Any, exc: TypeError, args: tuple) -> None:
    """Rewrite an argument-binding error raised by calling a builtin impl so it names the builtin."""
    if isinstance(fn, Value):
        if not fn._is_py_():
            return
        fn = fn._as_py_()
    public_name = BUILTIN_IMPL_NAMES.get(id(fn))
    if public_name is None:
        return
    message = exc.args[0] if exc.args else None
    if not isinstance(message, str):
        return
    for impl_name in (getattr(fn, "__qualname__", None), getattr(type(fn).__call__, "__qualname__", None)):
        if impl_name is not None and message.startswith(f"{impl_name}("):
            remainder = message[len(impl_name) :]
            # CPython's other counted shape, "N positional arguments (and M keyword-only arguments) were
            # given", carries more than a rebuild from the signature could say, so it is left alone.
            if remainder.startswith("() takes ") and "keyword" not in remainder:
                arity = _describe_positional_arity(fn, len(args))
                if arity is not None:
                    remainder = arity
            # Mutated in place rather than replaced by a new exception: the excepthook prints the whole
            # chain, so a `raise ... from exc` would show the private name again above the rewritten one.
            exc.args = (public_name + remainder, *exc.args[1:])
            return


def _describe_positional_arity(fn: Any, n_given: int) -> str | None:
    """Word CPython's "takes ... but ... given" for `fn` called with `n_given` positional arguments, or None."""
    try:
        parameters = list(inspect.signature(fn).parameters.values())
    except (TypeError, ValueError):
        return None
    if any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in parameters):
        return None
    positional = [
        p for p in parameters if p.kind in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
    ]
    maximum = len(positional)
    minimum = sum(1 for p in positional if p.default is inspect.Parameter.empty)
    if minimum == maximum:
        accepted = f"{maximum} positional argument{'' if maximum == 1 else 's'}"
    else:
        accepted = f"from {minimum} to {maximum} positional arguments"
    return f"() takes {accepted} but {n_given} {'was' if n_given == 1 else 'were'} given"


# builtin_impls imports validate_value from impl, so impl cannot import this registry without a cycle.
impl.BUILTIN_IMPLS = BUILTIN_IMPLS
