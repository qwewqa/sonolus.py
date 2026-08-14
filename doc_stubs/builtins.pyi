# ruff: noqa
import builtins
from enum import Enum
from typing import (
    Any,
    Callable,
    Iterable,
    Iterator,
    Literal,
    overload,
)

from sonolus.script.array_like import ArrayLike

def all(iterable: Iterable[builtins.object]) -> builtins.bool:
    """Return True if all elements of the iterable are true.

    Args:
        iterable: The iterable to evaluate.

    Returns:
        True if all elements are true, False otherwise.
    """
    ...

def any(iterable: Iterable[builtins.object]) -> builtins.bool:
    """Return True if any element of the iterable is true.

    Args:
        iterable: The iterable to evaluate.

    Returns:
        True if any element is true, False otherwise.
    """
    ...

def sum(
    iterable: Iterable[builtins.int | builtins.float], /, start: builtins.int | builtins.float = 0
) -> builtins.int | builtins.float:
    """Return the sum of a 'start' value (default: 0) and an iterable of numbers.

    Args:
        iterable: The iterable of numbers to sum.
        start: The starting value to add to the sum.

    Returns:
        The total sum.
    """
    ...

def abs(x: builtins.int | builtins.float, /) -> builtins.int | builtins.float:
    """Return the absolute value of a number.

    Args:
        x: A number.

    Returns:
        The absolute value of x.
    """
    ...

def bool(x: object = False, /) -> builtins.bool:
    """Convert a value to a Boolean.

    Args:
        x: The value to convert. Defaults to False if omitted.

    Returns:
        The Boolean value of x.
    """
    ...

def callable(obj: object, /) -> builtins.bool:
    """Check if the object appears callable.

    Args:
        obj: The object to check.

    Returns:
        True if the object appears callable, False otherwise.
    """
    ...

@overload
def dict() -> builtins.dict: ...
@overload
def dict[K, V](mapping_or_iterable: builtins.dict[K, V], **kwargs: V) -> builtins.dict[K, V]: ...
@overload
def dict[K, V](mapping_or_iterable: tuple[tuple[K, V], ...], **kwargs: V) -> builtins.dict[K, V]: ...
@overload
def dict[V](**kwargs: V) -> builtins.dict[builtins.str, V]: ...
def dict(*args, **kwargs) -> builtins.dict:
    """Construct a dict from another dict, a tuple of key-value pairs, or keyword arguments.

    All dict keys must be compile-time constants. Dynamic access using a key that is not
    a compile-time constant is only supported when all values are compile-time constants of a
    single type, and that type is numeric, `Array`, or `Record`.

    Dynamic access requires key comparisons to be consistent, including a total ordering when ordering is used.

    Accepts an optional dict to copy from or a tuple of `(key, value)` pairs, plus
    optional keyword arguments to include in the dict.

    Returns:
        A new dict.
    """
    ...

def enumerate[T](iterable: Iterable[T], start: builtins.int = 0) -> Iterator[tuple[builtins.int, T]]:
    """Return an enumerate object.

    Args:
        iterable: The iterable to enumerate.
        start: The starting index.

    Returns:
        An enumerate object.
    """
    ...

def filter[T](function: Callable[[T], builtins.object] | None, iterable: Iterable[T], /) -> Iterator[T]:
    """Construct an iterator from those elements of iterable for which function returns true.

    Args:
        function: A function that tests if each element should be included. If None, returns the elements that are true.
        iterable: The iterable to filter.

    Returns:
        An iterator yielding the filtered elements.
    """
    ...

def float(x: builtins.int | builtins.float = 0.0, /) -> builtins.float:
    """Convert a number to a floating point number.

    Args:
        x: The number to convert. Defaults to 0.0 if omitted.

    Returns:
        The floating point representation of x.
    """
    ...

@overload
def getattr(obj: object, name: builtins.str) -> Any: ...
@overload
def getattr[T](obj: object, name: builtins.str, default: T) -> Any | T: ...
def getattr(obj: object, name: builtins.str, default: Any = ...) -> Any:
    """Get a named attribute from an object.

    Args:
        obj: The object to get the attribute from.
        name: The name of the attribute.
        default: The value to return if the attribute does not exist.

    Returns:
        The value of the attribute, or default if it does not exist.
    """
    ...

def hasattr(obj: object, name: builtins.str) -> builtins.bool:
    """Check if an object has a named attribute.

    Args:
        obj: The object to check.
        name: The name of the attribute.

    Returns:
        True if the object has the attribute, False otherwise.
    """
    ...

def int(x: builtins.int | builtins.float = 0, /) -> builtins.int:
    """Convert a number to an integer.

    Args:
        x: The number to convert. Defaults to 0 if omitted.

    Returns:
        The integer representation of x.
    """
    ...

def isinstance(obj: object, classinfo: type | tuple[type, ...], /) -> builtins.bool:
    """Check if an object is an instance of a class or of a subclass thereof.

    `classinfo` may be a single type or a tuple of types. Checking against `int`, `float`, or `bool` directly is not
    supported; use `Num` instead.

    Args:
        obj: The object to check.
        classinfo: A type or a tuple of types.

    Returns:
        True if the object is an instance of classinfo, False otherwise.
    """
    ...

def issubclass(cls: type, classinfo: type | tuple[type, ...]) -> builtins.bool:
    """Check if a class is a subclass of another class or a tuple of classes.

    Both arguments must be types known at compile time. `classinfo` may be a single type or a tuple of types.
    Checking against `int`, `float`, or `bool` directly is not supported; use `Num` instead.

    Args:
        cls: The class to check.
        classinfo: A class or a tuple of classes.

    Returns:
        True if cls is a subclass of classinfo, False otherwise.
    """
    ...

def iter[T](iterable: Iterable[T]) -> Iterator[T]:
    """Return an iterator for the given iterable.

    Not supported for a `tuple`, `dict`, `set`, or enum class.

    Args:
        iterable: The iterable to convert to an iterator.

    Returns:
        An iterator over the elements of the iterable.
    """
    ...

def len(s: object, /) -> builtins.int:
    """Return the number of items in a container.

    Args:
        s: The container object.

    Returns:
        The number of items in s.
    """
    ...

def map[T, S](function: Callable[..., S], iterable: Iterable[T], /, *iterables: Iterable[Any]) -> Iterator[S]:
    """Apply a function to every item of an iterable and return an iterator.

    A `tuple`, `dict`, `set`, or enum class may be used when every iterable argument is one of those. Other
    supported iterable types may be mixed with each other, but not with those compile-time collections.

    Args:
        function: The function to apply.
        iterable: The iterable to process.
        *iterables: Additional iterables to process in parallel with iterable.

    Returns:
        An iterator with the results.
    """
    ...

@overload
def max[T](iterable: Iterable[T], /, *, key: Callable[[T], Any] | None = ...) -> T: ...
@overload
def max[T](
    iterable: Iterable[T],
    /,
    *,
    default: T = ...,
    key: Callable[[T], Any] | None = ...,
) -> T: ...
@overload
def max(
    arg1: builtins.int | builtins.float,
    arg2: builtins.int | builtins.float,
    /,
    *args: builtins.int | builtins.float,
    key: Callable[[builtins.int | builtins.float], Any] | None = ...,
) -> builtins.int | builtins.float: ...
def max(*args, **kwargs):
    """Return the largest item in an iterable or the largest of multiple arguments.

    When called with a single iterable, returns the largest item from that iterable. When called with two or
    more arguments, all arguments must be numbers, and the largest one is returned.

    Use the `key` parameter to specify a function that transforms each element before comparison.

    The `key` function must be side-effect free because it may be called more than once per element.

    A `tuple`, `dict`, `set`, or enum class argument is only supported when every element is numeric; use an
    `Array` or `VarArray` for a collection of other types.

    The `default` parameter is supported only with a single iterable and must be usable in place of an element.
    When it is known only at runtime whether the iterable is empty, only numeric elements support `default`.
    """
    ...

@overload
def min[T](iterable: Iterable[T], /, *, key: Callable[[T], Any] | None = ...) -> T: ...
@overload
def min[T](
    iterable: Iterable[T],
    /,
    *,
    default: T = ...,
    key: Callable[[T], Any] | None = ...,
) -> T: ...
@overload
def min(
    arg1: builtins.int | builtins.float,
    arg2: builtins.int | builtins.float,
    /,
    *args: builtins.int | builtins.float,
    key: Callable[[builtins.int | builtins.float], Any] | None = ...,
) -> builtins.int | builtins.float: ...
def min(*args, **kwargs):
    """Return the smallest item in an iterable or the smallest of multiple arguments.

    When called with a single iterable, returns the smallest item from that iterable. When called with two or
    more arguments, all arguments must be numbers, and the smallest one is returned.

    Use the `key` parameter to specify a function that transforms each element before comparison.

    The `key` function must be side-effect free because it may be called more than once per element.

    A `tuple`, `dict`, `set`, or enum class argument is only supported when every element is numeric; use an
    `Array` or `VarArray` for a collection of other types.

    The `default` parameter is supported only with a single iterable and must be usable in place of an element.
    When it is known only at runtime whether the iterable is empty, only numeric elements support `default`.
    """
    ...

def next[T](iterator: Iterator[T]) -> T:
    """Retrieve the next item from an iterator.

    Errors if the iterator is exhausted.

    Args:
        iterator: The iterator to retrieve the next item from.

    Returns:
        The next item from the iterator.
    """
    ...

@overload
def range(stop: builtins.int, /) -> builtins.range: ...
@overload
def range(start: builtins.int, stop: builtins.int, step: builtins.int = ..., /) -> builtins.range: ...
def range(*args) -> builtins.range:
    """Return an immutable sequence of numbers.

    When called with one argument, creates a sequence from 0 to that number (exclusive).
    When called with two arguments, creates a sequence from the first to the second (exclusive).
    When called with three arguments, the third argument specifies the step size.
    """
    ...

@overload
def reversed[T](seq: ArrayLike[T], /) -> ArrayLike[T]: ...
@overload
def reversed[T](seq: tuple[T, ...] | builtins.dict[T, Any], /) -> tuple[T, ...]: ...
@overload
def reversed[T: Enum](seq: type[T], /) -> tuple[T, ...]: ...
def reversed(seq: Any, /) -> Any:
    """Return a reversed view of a supported sequence.

    Accepts an `ArrayLike`, tuple, dict, or enum class.

    Args:
        seq: The sequence to reverse.
    """
    ...

def round(number: builtins.int | builtins.float, ndigits: builtins.int = ...) -> builtins.float:
    """Round a number to a given precision in decimal digits.

    With `ndigits`, a value near the midpoint between two rounded values may round differently than in Python.

    Extremely large `ndigits` values are not supported. Precision is limited when either argument has an extreme
    magnitude.

    Args:
        number: The number to round.
        ndigits: The number of decimal digits to round to.

    Returns:
        The rounded number.
    """
    ...

@overload
def set() -> builtins.set: ...
@overload
def set[T](iterable: tuple[T, ...] | builtins.dict[T, Any] | builtins.set[T]) -> builtins.set[T]: ...
@overload
def set[T: Enum](iterable: type[T]) -> builtins.set[T]: ...
def set(*args) -> builtins.set:
    """Construct a set from a supported compile-time collection.

    All set members must be compile-time constants. Accepts an optional `tuple`, `dict`, enum class, or `set`.
    An `Array`, `VarArray`, or `range` is not accepted.

    Returns:
        A new set.
    """
    ...

def setattr(obj: object, name: builtins.str, value: Any) -> None:
    """Set a named attribute on an object.

    The attribute must already exist as a supported field or property.

    Args:
        obj: The object to set the attribute on.
        name: The name of the attribute.
        value: The value to set.
    """
    ...

def super(cls: type = ..., instance: Any = ..., /) -> Any:
    """Return a proxy object that delegates method calls to a parent or sibling class.

    Args:
        cls: The class to delegate.
        instance: The instance to delegate to.

    Returns:
        A proxy object that can be used to call methods from the parent or sibling class.
    """
    ...

def type(obj: object, /) -> builtins.type:
    """Return the type of an object.

    Args:
        obj: The object to get the type of.

    Returns:
        The type of the object.
    """
    ...

@overload
def zip(*, strict: Literal[False] = False) -> Iterator[tuple[()]]: ...
@overload
def zip[T1](iterable1: Iterable[T1], /, *, strict: Literal[False] = False) -> Iterator[tuple[T1]]: ...
@overload
def zip[T1, T2](
    iterable1: Iterable[T1], iterable2: Iterable[T2], /, *, strict: Literal[False] = False
) -> Iterator[tuple[T1, T2]]: ...
@overload
def zip[T1, T2, T3](
    iterable1: Iterable[T1], iterable2: Iterable[T2], iterable3: Iterable[T3], /, *, strict: Literal[False] = False
) -> Iterator[tuple[T1, T2, T3]]: ...
@overload
def zip[T1, T2, T3, T4](
    iterable1: Iterable[T1],
    iterable2: Iterable[T2],
    iterable3: Iterable[T3],
    iterable4: Iterable[T4],
    /,
    *,
    strict: Literal[False] = False,
) -> Iterator[tuple[T1, T2, T3, T4]]: ...
@overload
def zip[T1, T2, T3, T4, T5](
    iterable1: Iterable[T1],
    iterable2: Iterable[T2],
    iterable3: Iterable[T3],
    iterable4: Iterable[T4],
    iterable5: Iterable[T5],
    /,
    *,
    strict: Literal[False] = False,
) -> Iterator[tuple[T1, T2, T3, T4, T5]]: ...
@overload
def zip(*iterables: Iterable[Any], strict: Literal[False] = False) -> Iterator[tuple[Any, ...]]: ...
def zip(*iterables: Iterable[Any], strict: Literal[False] = False) -> Iterator[tuple[Any, ...]]:
    """Return an iterator of tuples, where the i-th tuple contains the i-th element from each of the argument sequences.

    Args:
        *iterables: Iterables to aggregate.
        strict: Must be False.

    Returns:
        An iterator of aggregated tuples.
    """
    ...
