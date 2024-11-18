import random as _random

from sonolus.backend.ops import Op
from sonolus.script.internal.native import native_function


@native_function(Op.Random)
def random_float(lo: float = 0.0, hi: float = 1.0, /) -> float:
    return lo + (hi - lo) * _random.random()


@native_function(Op.RandomInteger)
def random_integer(lo: int, hi: int, /) -> int:
    return _random.randint(lo, hi)
