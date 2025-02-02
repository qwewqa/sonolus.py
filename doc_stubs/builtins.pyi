# ruff: noqa
import builtins
from typing import (
    Any,
    Callable,
    Iterable,
    Iterator,
    Sequence,
    overload,
)

def all(iterable: Iterable[builtins.bool]) -> builtins.bool:
    """Return True if all elements of the iterable are true.

    Args:
        iterable: The iterable to evaluate.

    Returns:
        True if all elements are true, False otherwise.
    """
    ...

def any(iterable: Iterable[builtins.bool]) -> builtins.bool:
    """Return True if any element of the iterable is true.

    Args:
        iterable: The iterable to evaluate.

    Returns:
        True if any element is true, False otherwise.
    """
    ...

def abs(x: builtins.int | builtins.float) -> builtins.int | builtins.float:
    """Return the absolute value of a number.

    Args:
        x: A number.

    Returns:
        The absolute value of x.
    """
    ...

def bool(x: builtins.int | builtins.float | builtins.bool) -> builtins.bool:
    """Convert a value to a Boolean.

    Args:
        x: The value to convert.

    Returns:
        The Boolean value of x.
    """
    ...

def callable(obj: object) -> bool:
    """Check if the object appears callable.

    Args:
        obj: The object to check.

    Returns:
        True if the object appears callable, False otherwise.
    """
    ...

def enumerate[T](iterable: Iterable[T], start: int = 0) -> Iterator[tuple[int, T]]:
    """Return an enumerate object.

    Args:
        iterable: The iterable to enumerate.
        start: The starting index.

    Returns:
        An enumerate object.
    """
    ...

def filter[T](function: Callable[[T], builtins.bool] | None, iterable: Iterable[T]) -> Iterator[T]:
    """Construct an iterator from those elements of iterable for which function returns true.

    Args:
        function: A function that tests if each element should be included. If None, returns the elements that are true.
        iterable: The iterable to filter.

    Returns:
        An iterator yielding the filtered elements.
    """
    ...

def float(x: builtins.int | builtins.float) -> builtins.float:
    """Convert a number to a floating point number.

    Args:
        x: The number to convert.

    Returns:
        The floating point representation of x.
    """
    ...

def int(x: builtins.int | builtins.float) -> builtins.int:
    """Convert a number to an integer.

    Args:
        x: The number to convert.

    Returns:
        The integer representation of x.
    """
    ...

def isinstance(obj: object, classinfo: type | tuple[type, ...]) -> builtins.bool:
    """Check if an object is an instance of a class or of a subclass thereof.

    Args:
        obj: The object to check.
        classinfo: A type or a tuple of types.

    Returns:
        True if the object is an instance of classinfo, False otherwise.
    """
    ...

def issubclass(cls: type, classinfo: type | tuple[type, ...]) -> builtins.bool:
    """Check if a class is a subclass of another class or a tuple of classes.

    Args:
        cls: The class to check.
        classinfo: A class or a tuple of classes.

    Returns:
        True if cls is a subclass of classinfo, False otherwise.
    """
    ...

def len(s: object) -> builtins.int:
    """Return the number of items in a container.

    Args:
        s: The container object.

    Returns:
        The number of items in s.
    """
    ...

def map[T, S](function: Callable[[T], S], iterable: Iterable[T]) -> Iterator[S]:
    """Apply a function to every item of an iterable and return an iterator.

    Unlike the standard Python map function, it is possible that the function may be called more than once on the
    same item.

    Args:
        function: The function to apply.
        iterable: The iterable to process.

    Returns:
        An iterator with the results.
    """
    ...

@overload
def max[T](iterable: Iterable[T], *, key: Callable[[T], Any] | None = ...) -> T:
    """Return the largest item in an iterable or the largest of two or more arguments.

    Args:
        iterable: The iterable to evaluate.
        key: A function of one argument that is used to extract a comparison key from each element.

    Returns:
        The largest item.
    """
    ...

@overload
def max[T](arg1: T, arg2: T, *args: T, key: Callable[[T], Any] | None = ...) -> T:
    """Return the largest item in an iterable or the largest of two or more arguments.

    Args:
        arg1: First argument.
        arg2: Second argument.
        *args: Additional arguments.
        key: A function of one argument that is used to extract a comparison key from each element.

    Returns:
        The largest item.
    """
    ...

@overload
def min[T](iterable: Iterable[T], *, key: Callable[[T], Any] | None = ...) -> T:
    """Return the smallest item in an iterable or the smallest of two or more arguments.

    Args:
        iterable: The iterable to evaluate.
        key: A function of one argument that is used to extract a comparison key from each element.

    Returns:
        The smallest item.
    """
    ...

@overload
def min[T](arg1: T, arg2: T, *args: T, key: Callable[[T], Any] | None = ...) -> T:
    """Return the smallest item in an iterable or the smallest of two or more arguments.

    Args:
        arg1: First argument.
        arg2: Second argument.
        *args: Additional arguments.
        key: A function of one argument that is used to extract a comparison key from each element.

    Returns:
        The smallest item.
    """
    ...

@overload
def range(stop: builtins.int) -> builtins.range:
    """Return an immutable sequence of numbers from 0 to stop.

    Args:
        stop: Stop value.

    Returns:
        The range object.
    """
    ...

@overload
def range(start: builtins.int, stop: builtins.int, step: builtins.int = ...) -> builtins.range:
    """Return an immutable sequence of numbers from start to stop by step.

    Args:
        start: Start value.
        stop: Stop value.
        step: Step value.

    Returns:
        The range object.
    """
    ...

def reversed[T](seq: Sequence[T]) -> Iterator[T]:
    """Return a reverse iterator.

    Args:
        seq: The sequence to reverse.

    Returns:
        An iterator over the reversed sequence.
    """
    ...

def round(number: builtins.int | builtins.float, ndigits: builtins.int = ...) -> builtins.float:
    """Round a number to a given precision in decimal digits.

    Args:
        number: The number to round.
        ndigits: The number of decimal digits to round to.

    Returns:
        The rounded number.
    """
    ...

def zip[T](*iterables: Iterable[T]) -> Iterator[tuple[T, ...]]:
    """Return an iterator of tuples, where the i-th tuple contains the i-th element from each of the argument sequences.

    Args:
        *iterables: Iterables to aggregate.

    Returns:
        An iterator of aggregated tuples.
    """
    ...
