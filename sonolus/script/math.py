import math

from sonolus.backend.ops import Op
from sonolus.script.internal.native import native_function


@native_function(Op.Sin)
def sin(x: float) -> float:
    return math.sin(x)


@native_function(Op.Cos)
def cos(x: float) -> float:
    return math.cos(x)


@native_function(Op.Tan)
def tan(x: float) -> float:
    return math.tan(x)


@native_function(Op.Arcsin)
def asin(x: float) -> float:
    return math.asin(x)


@native_function(Op.Arccos)
def acos(x: float) -> float:
    return math.acos(x)


@native_function(Op.Arctan)
def atan(x: float) -> float:
    return math.atan(x)


@native_function(Op.Arctan2)
def atan2(y: float, x: float) -> float:
    return math.atan2(y, x)


@native_function(Op.Sinh)
def sinh(x: float) -> float:
    return math.sinh(x)


@native_function(Op.Cosh)
def cosh(x: float) -> float:
    return math.cosh(x)


@native_function(Op.Tanh)
def tanh(x: float) -> float:
    return math.tanh(x)


@native_function(Op.Floor)
def floor(x: float) -> float:
    return math.floor(x)


@native_function(Op.Ceil)
def ceil(x: float) -> float:
    return math.ceil(x)


@native_function(Op.Trunc)
def trunc(x: float) -> float:
    return math.trunc(x)


@native_function(Op.Round)
def _round(x: float) -> float:
    return round(x)


MATH_BUILTIN_IMPLS = {
    id(math.sin): sin,
    id(math.cos): cos,
    id(math.tan): tan,
    id(math.asin): asin,
    id(math.acos): acos,
    id(math.atan): atan,
    id(math.atan2): atan2,
    id(math.sinh): sinh,
    id(math.cosh): cosh,
    id(math.tanh): tanh,
    id(math.floor): floor,
    id(math.ceil): ceil,
    id(math.trunc): trunc,
    id(round): _round,
}
