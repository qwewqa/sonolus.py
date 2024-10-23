import math

from sonolus.backend.ops import Op
from sonolus.script.internal.native import native_function


@native_function(Op.Arctan2)
def atan2(y: float, x: float) -> float:
    return math.atan2(y, x)


MATH_BUILTIN_IMPLS = {
    id(math.atan2): atan2,
}
