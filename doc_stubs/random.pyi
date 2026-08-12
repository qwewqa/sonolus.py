# ruff: noqa
from typing import overload

from sonolus.script.array_like import ArrayLike

@overload
def randrange(stop: int, /) -> int:
    """Return a randomly selected element from range(stop).

    Args:
        stop: The end of the range.

    Returns:
        A randomly selected integer from the range.
    """
    ...

@overload
def randrange(start: int, stop: int, step: int = ...) -> int:
    """Return a randomly selected element from range(start, stop, step).

    Args:
        start: The start of the range.
        stop: The end of the range.
        step: The step size.

    Returns:
        A randomly selected integer from the range.
    """
    ...

def randrange(start: int, stop: int = ..., step: int = ...) -> int:
    """Return a randomly selected element from range(start, stop, step).

    Args:
        start: The start of the range.
        stop: The end of the range.
        step: The step size.

    Returns:
        A randomly selected integer from the range.
    """
    ...

def randint(a: int, b: int) -> int:
    """Return a random integer N such that a <= N <= b.

    Args:
        a: The lower bound.
        b: The upper bound.

    Returns:
        A randomly selected integer between a and b, inclusive.
    """
    ...

def choice[T](seq: ArrayLike[T]) -> T:
    """Return a randomly selected element from a non-empty sequence.

    The sequence must be array-like, such as an `Array` or `VarArray`. Tuples are not supported.

    Args:
        seq: The sequence to choose from.

    Returns:
        A randomly selected element from the sequence.
    """
    ...

def shuffle[T](seq: ArrayLike[T]) -> None:
    """Shuffle the sequence in place.

    The sequence must be a mutable array-like, such as an `Array` or `VarArray`.

    Args:
        seq: The sequence to shuffle.
    """
    ...

def random() -> float:
    """Return a random floating point number in the range [0.0, 1.0).

    Returns:
        A random float between 0.0 (inclusive) and 1.0 (exclusive).
    """
    ...

def uniform(a: float, b: float) -> float:
    """Return a random floating point number between a and b, inclusive.

    Args:
        a: One endpoint of the range.
        b: The other endpoint of the range.

    Returns:
        A random float between the endpoints, inclusive.
    """
    ...
