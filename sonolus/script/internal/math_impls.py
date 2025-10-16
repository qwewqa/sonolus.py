import math

from sonolus.backend.ops import Op
from sonolus.script.internal.native import native_function


@native_function(Op.Sin, const_eval=True)
def _sin(x: float) -> float:
    return math.sin(x)


@native_function(Op.Cos, const_eval=True)
def _cos(x: float) -> float:
    return math.cos(x)


@native_function(Op.Tan, const_eval=True)
def _tan(x: float) -> float:
    return math.tan(x)


@native_function(Op.Arcsin, const_eval=True)
def _asin(x: float) -> float:
    return math.asin(x)


@native_function(Op.Arccos, const_eval=True)
def _acos(x: float) -> float:
    return math.acos(x)


@native_function(Op.Arctan, const_eval=True)
def _atan(x: float) -> float:
    return math.atan(x)


@native_function(Op.Arctan2, const_eval=True)
def _atan2(y: float, x: float) -> float:
    return math.atan2(y, x)


@native_function(Op.Sinh, const_eval=True)
def _sinh(x: float) -> float:
    return math.sinh(x)


@native_function(Op.Cosh, const_eval=True)
def _cosh(x: float) -> float:
    return math.cosh(x)


@native_function(Op.Tanh, const_eval=True)
def _tanh(x: float) -> float:
    return math.tanh(x)


@native_function(Op.Floor, const_eval=True)
def _floor(x: float) -> float:
    return math.floor(x)


@native_function(Op.Ceil, const_eval=True)
def _ceil(x: float) -> float:
    return math.ceil(x)


@native_function(Op.Trunc, const_eval=True)
def _trunc(x: float) -> float:
    return math.trunc(x)


@native_function(Op.Round, const_eval=True)
def __round(x: float) -> float:
    return round(x)


def _round(x: float, n: int = 0) -> float:
    if n == 0:
        return __round(x)
    return __round(x * 10**n) / 10**n


@native_function(Op.Frac, const_eval=True)
def frac(x: float) -> float:
    return x % 1


@native_function(Op.Log, const_eval=True)
def _ln(x: float) -> float:
    return math.log(x)


def _log(x: float, base: float | None = None) -> float:
    if base is None:
        return _ln(x)
    else:
        return _ln(x) / _ln(base)


def _sqrt(x: float) -> float:
    return x**0.5


@native_function(Op.Degree, const_eval=True)
def _degrees(x: float) -> float:
    """Convert radians to degrees."""
    return math.degrees(x)


@native_function(Op.Radian, const_eval=True)
def _radians(x: float) -> float:
    """Convert degrees to radians."""
    return math.radians(x)


@native_function(Op.Rem, const_eval=True)
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
    id(math.sqrt): _sqrt,
}
