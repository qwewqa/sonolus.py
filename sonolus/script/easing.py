import math

from sonolus.backend.ops import Op
from sonolus.script.internal.native import native_function
from sonolus.script.interval import clamp


@native_function(Op.EaseInBack)
def ease_in_back(x: float) -> float:
    x = clamp(x, 0, 1)
    c1 = 1.70158
    c3 = c1 + 1
    return c3 * x**3 - c1 * x**2


@native_function(Op.EaseOutBack)
def ease_out_back(x: float) -> float:
    x = clamp(x, 0, 1)
    c1 = 1.70158
    c3 = c1 + 1
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2


@native_function(Op.EaseInOutBack)
def ease_in_out_back(x: float) -> float:
    x = clamp(x, 0, 1)
    c1 = 1.70158
    c2 = c1 * 1.525
    if x < 0.5:
        return ((2 * x) ** 2 * ((c2 + 1) * 2 * x - c2)) / 2
    else:
        return ((2 * x - 2) ** 2 * ((c2 + 1) * (2 * x - 2) + c2) + 2) / 2


@native_function(Op.EaseInCirc)
def ease_in_circ(x: float) -> float:
    x = clamp(x, 0, 1)
    return 1 - math.sqrt(1 - x**2)


@native_function(Op.EaseOutCirc)
def ease_out_circ(x: float) -> float:
    x = clamp(x, 0, 1)
    return math.sqrt(1 - (x - 1) ** 2)


@native_function(Op.EaseInOutCirc)
def ease_in_out_circ(x: float) -> float:
    x = clamp(x, 0, 1)
    if x < 0.5:
        return (1 - math.sqrt(1 - (2 * x) ** 2)) / 2
    else:
        return (math.sqrt(1 - (2 * x - 2) ** 2) + 1) / 2


@native_function(Op.EaseInCubic)
def ease_in_cubic(x: float) -> float:
    x = clamp(x, 0, 1)
    return x**3


@native_function(Op.EaseOutCubic)
def ease_out_cubic(x: float) -> float:
    x = clamp(x, 0, 1)
    return 1 - (1 - x) ** 3


@native_function(Op.EaseInOutCubic)
def ease_in_out_cubic(x: float) -> float:
    x = clamp(x, 0, 1)
    if x < 0.5:
        return 4 * x**3
    else:
        return 1 - (-2 * x + 2) ** 3 / 2


@native_function(Op.EaseInElastic)
def ease_in_elastic(x: float) -> float:
    x = clamp(x, 0, 1)
    c4 = (2 * math.pi) / 3
    if x in {0, 1}:
        return x
    else:
        return -(2 ** (10 * x - 10)) * math.sin((x * 10 - 10.75) * c4)


@native_function(Op.EaseOutElastic)
def ease_out_elastic(x: float) -> float:
    x = clamp(x, 0, 1)
    c4 = (2 * math.pi) / 3
    if x in {0, 1}:
        return x
    else:
        return 2 ** (-10 * x) * math.sin((x * 10 - 0.75) * c4) + 1


@native_function(Op.EaseInOutElastic)
def ease_in_out_elastic(x: float) -> float:
    x = clamp(x, 0, 1)
    c5 = (2 * math.pi) / 4.5
    if x in {0, 1}:
        return x
    elif x < 0.5:
        return -(2 ** (20 * x - 10) * math.sin((20 * x - 11.125) * c5)) / 2
    else:
        return (2 ** (-20 * x + 10) * math.sin((20 * x - 11.125) * c5)) / 2 + 1


@native_function(Op.EaseInExpo)
def ease_in_expo(x: float) -> float:
    x = clamp(x, 0, 1)
    return 0 if x == 0 else 2 ** (10 * x - 10)


@native_function(Op.EaseOutExpo)
def ease_out_expo(x: float) -> float:
    x = clamp(x, 0, 1)
    return 1 if x == 1 else 1 - 2 ** (-10 * x)


@native_function(Op.EaseInOutExpo)
def ease_in_out_expo(x: float) -> float:
    x = clamp(x, 0, 1)
    if x in {0, 1}:
        return x
    elif x < 0.5:
        return 2 ** (20 * x - 10) / 2
    else:
        return (2 - 2 ** (-20 * x + 10)) / 2


@native_function(Op.EaseInOutQuad)
def ease_in_out_quad(x: float) -> float:
    x = clamp(x, 0, 1)
    if x < 0.5:
        return 2 * x**2
    else:
        return 1 - (-2 * x + 2) ** 2 / 2


@native_function(Op.EaseInOutQuart)
def ease_in_out_quart(x: float) -> float:
    x = clamp(x, 0, 1)
    if x < 0.5:
        return 8 * x**4
    else:
        return 1 - (-2 * x + 2) ** 4 / 2


@native_function(Op.EaseInOutQuint)
def ease_in_out_quint(x: float) -> float:
    x = clamp(x, 0, 1)
    if x < 0.5:
        return 16 * x**5
    else:
        return 1 - (-2 * x + 2) ** 5 / 2


@native_function(Op.EaseInOutSine)
def ease_in_out_sine(x: float) -> float:
    x = clamp(x, 0, 1)
    return -(math.cos(math.pi * x) - 1) / 2


@native_function(Op.EaseInQuad)
def ease_in_quad(x: float) -> float:
    x = clamp(x, 0, 1)
    return x**2


@native_function(Op.EaseOutQuad)
def ease_out_quad(x: float) -> float:
    x = clamp(x, 0, 1)
    return 1 - (1 - x) ** 2


@native_function(Op.EaseInQuart)
def ease_in_quart(x: float) -> float:
    x = clamp(x, 0, 1)
    return x**4


@native_function(Op.EaseOutQuart)
def ease_out_quart(x: float) -> float:
    x = clamp(x, 0, 1)
    return 1 - (1 - x) ** 4


@native_function(Op.EaseInQuint)
def ease_in_quint(x: float) -> float:
    x = clamp(x, 0, 1)
    return x**5


@native_function(Op.EaseOutQuint)
def ease_out_quint(x: float) -> float:
    x = clamp(x, 0, 1)
    return 1 - (1 - x) ** 5


@native_function(Op.EaseInSine)
def ease_in_sine(x: float) -> float:
    x = clamp(x, 0, 1)
    return 1 - math.cos((x * math.pi) / 2)


@native_function(Op.EaseOutSine)
def ease_out_sine(x: float) -> float:
    x = clamp(x, 0, 1)
    return math.sin((x * math.pi) / 2)


@native_function(Op.EaseOutInBack)
def ease_out_in_back(x: float) -> float:
    x = clamp(x, 0, 1)
    c1 = 1.70158
    c3 = c1 + 1
    if x < 0.5:
        return (1 + c3 * (2 * x - 1) ** 3 + c1 * (2 * x - 1) ** 2) / 2
    else:
        return (c3 * (2 * x - 1) ** 3 - c1 * (2 * x - 1) ** 2) / 2 + 0.5


@native_function(Op.EaseOutInCirc)
def ease_out_in_circ(x: float) -> float:
    x = clamp(x, 0, 1)
    if x < 0.5:
        return (math.sqrt(1 - (2 * x - 1) ** 2)) / 2
    else:
        return (1 - math.sqrt(1 - (2 * x - 1) ** 2)) / 2 + 0.5


@native_function(Op.EaseOutInCubic)
def ease_out_in_cubic(x: float) -> float:
    x = clamp(x, 0, 1)
    if x < 0.5:
        return (1 - (1 - 2 * x) ** 3) / 2
    else:
        return ((2 * x - 1) ** 3) / 2 + 0.5


@native_function(Op.EaseOutInElastic)
def ease_out_in_elastic(x: float) -> float:
    x = clamp(x, 0, 1)
    c4 = (2 * math.pi) / 3
    if x < 0.5:
        if x == 0:
            return 0
        else:
            return (2 ** (-20 * x + 10) * math.sin((20 * x - 0.75) * c4)) / 2 + 0.5
    elif x == 1:
        return 1
    else:
        return (-(2 ** (10 * (2 * x - 1) - 10)) * math.sin((20 * x - 10.75) * c4)) / 2 + 0.5


@native_function(Op.EaseOutInExpo)
def ease_out_in_expo(x: float) -> float:
    x = clamp(x, 0, 1)
    if x in {0, 1}:
        return x
    elif x < 0.5:
        return (1 - 2 ** (-20 * x)) / 2
    else:
        return (2 ** (20 * x - 20)) / 2 + 0.5


@native_function(Op.EaseOutInQuad)
def ease_out_in_quad(x: float) -> float:
    x = clamp(x, 0, 1)
    if x < 0.5:
        return (1 - (1 - 2 * x) ** 2) / 2
    else:
        return ((2 * x - 1) ** 2) / 2 + 0.5


@native_function(Op.EaseOutInQuart)
def ease_out_in_quart(x: float) -> float:
    x = clamp(x, 0, 1)
    if x < 0.5:
        return (1 - (1 - 2 * x) ** 4) / 2
    else:
        return ((2 * x - 1) ** 4) / 2 + 0.5


@native_function(Op.EaseOutInQuint)
def ease_out_in_quint(x: float) -> float:
    x = clamp(x, 0, 1)
    if x < 0.5:
        return (1 - (1 - 2 * x) ** 5) / 2
    else:
        return ((2 * x - 1) ** 5) / 2 + 0.5


@native_function(Op.EaseOutInSine)
def ease_out_in_sine(x: float) -> float:
    x = clamp(x, 0, 1)
    if x < 0.5:
        return (math.sin(math.pi * x)) / 2
    else:
        return (1 - math.cos(math.pi * x)) / 2


def linstep(x: float) -> float:
    return clamp(x, 0.0, 1.0)


def smoothstep(x: float) -> float:
    x = clamp(x, 0.0, 1.0)
    return x * x * (3 - 2 * x)


def smootherstep(x: float) -> float:
    x = clamp(x, 0.0, 1.0)
    return x * x * x * (x * (x * 6 - 15) + 10)


def step_start(x: float) -> float:
    return 1.0 if x >= 0 else 0.0


def step_end(x: float) -> float:
    return 1.0 if x >= 1 else 0.0
