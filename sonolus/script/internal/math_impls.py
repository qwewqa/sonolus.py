import math

from sonolus.backend.ops import Op
from sonolus.script.internal.native import native_function


@native_function(Op.Sin)
def _sin(x: float) -> float:
    return math.sin(x)


@native_function(Op.Cos)
def _cos(x: float) -> float:
    return math.cos(x)


@native_function(Op.Tan)
def _tan(x: float) -> float:
    return math.tan(x)


@native_function(Op.Arcsin)
def _asin(x: float) -> float:
    return math.asin(x)


@native_function(Op.Arccos)
def _acos(x: float) -> float:
    return math.acos(x)


@native_function(Op.Arctan)
def _atan(x: float) -> float:
    return math.atan(x)


@native_function(Op.Arctan2)
def _atan2(y: float, x: float) -> float:
    return math.atan2(y, x)


@native_function(Op.Sinh)
def _sinh(x: float) -> float:
    return math.sinh(x)


@native_function(Op.Cosh)
def _cosh(x: float) -> float:
    return math.cosh(x)


@native_function(Op.Tanh)
def _tanh(x: float) -> float:
    return math.tanh(x)


@native_function(Op.Floor)
def _floor(x: float) -> float:
    return math.floor(x)


@native_function(Op.Ceil)
def _ceil(x: float) -> float:
    return math.ceil(x)


@native_function(Op.Trunc)
def _trunc(x: float) -> float:
    return math.trunc(x)


@native_function(Op.Round)
def __round(x: float) -> float:
    return round(x)


def _round(x: float, n: int = 0) -> float:
    if n == 0:
        return __round(x)
    return __round(x * 10**n) / 10**n


@native_function(Op.Frac)
def frac(x: float) -> float:
    return x % 1


@native_function(Op.Log)
def _ln(x: float) -> float:
    return math.log(x)


def _log(x: float, base: float | None = None) -> float:
    if base is None:
        return _ln(x)
    else:
        return _ln(x) / _ln(base)


@native_function(Op.Rem)
def _remainder(x: float, y: float) -> float:
    # This is different from math.remainder in Python's math package, which could be confusing
    return math.copysign(abs(x) % abs(y), x)


MATH_BUILTIN_IMPLS = {
    id(math.sin): _sin,
    id(math.cos): _cos,
    id(math.tan): _tan,
    id(math.asin): _asin,
    id(math.acos): _acos,
    id(math.atan): _atan,
    id(math.atan2): _atan2,
    id(math.sinh): _sinh,
    id(math.cosh): _cosh,
    id(math.tanh): _tanh,
    id(math.floor): _floor,
    id(math.ceil): _ceil,
    id(math.trunc): _trunc,
    id(round): _round,
    id(math.log): _log,
}
