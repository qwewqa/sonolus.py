import random as pyrand
from collections.abc import MutableSequence, Sequence

from sonolus.backend.ops import Op
from sonolus.script.internal.native import native_function
from sonolus.script.values import copy


@native_function(Op.Random)
def _random_float(a: float, b: float) -> float:
    """Returns a random float between a and b, inclusive."""
    return pyrand.uniform(a, b)


@native_function(Op.RandomInteger)
def _random_integer(a: int, b: int) -> int:
    """Returns a random integer between a (inclusive) and b (exclusive)."""
    return pyrand.randrange(a, b)


def _randrange(start: int, stop: int | None = None, step: int = 1) -> int:
    if stop is None:
        stop = start
        start = 0
    range_len = (stop - start + step - 1) // step
    return start + step * _random_integer(0, range_len)


def _randint(a: int, b: int) -> int:
    return _random_integer(a, b + 1)


def _choice[T](seq: Sequence[T]) -> T:
    return seq[_randrange(len(seq))]


def _swap(seq: MutableSequence, i: int, j: int) -> None:
    temp = copy(seq[i])
    seq[i] = seq[j]
    seq[j] = temp


def _shuffle[T: MutableSequence](seq: T) -> None:
    i = len(seq) - 1
    while i > 0:
        j = _randrange(i + 1)
        _swap(seq, i, j)
        i -= 1


def _random() -> float:
    # The end needs to exclude 1, and Sonolus uses 32-bit floats.
    return _random_float(0.0, 0.99999994)


def _uniform(a: float, b: float) -> float:
    return _random_float(a, b)


RANDOM_BUILTIN_IMPLS = {
    id(pyrand.randrange): _randrange,
    id(pyrand.randint): _randint,
    id(pyrand.choice): _choice,
    id(pyrand.shuffle): _shuffle,
    id(pyrand.random): _random,
    id(pyrand.uniform): _uniform,
}
