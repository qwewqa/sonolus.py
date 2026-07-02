import math
import operator
import random
from collections.abc import Callable

from sonolus.backend.node import EngineNode, FunctionNode
from sonolus.backend.ops import Op


class BreakException(Exception):  # noqa: N818
    n: int
    value: float

    def __init__(self, n: int, value: float):
        self.n = n
        self.value = value


def _rem(a: float, b: float) -> float:
    """Truncated remainder with the sign of the dividend (JS ``%``).

    Matches ``sonolus.script.internal.math_impls._remainder`` exactly, including yielding
    ``-0.0`` for a negative dividend whose remainder is zero (as JS / the real runtime do).
    """
    return math.copysign(abs(a) % abs(b), a)


def _sign(x: float) -> float:
    """JS ``Math.sign``: ``0``/``-0``/``NaN`` map to themselves, otherwise ``+/-1``."""
    if math.isnan(x):
        return x
    if x > 0:
        return 1.0
    if x < 0:
        return -1.0
    # Preserve the sign of zero (+0.0 or -0.0).
    return x


def _judge(
    diff: float,
    perfect_min: float,
    perfect_max: float,
    great_min: float,
    great_max: float,
    good_min: float,
    good_max: float,
) -> float:
    """Mirror of ``sonolus.script.bucket._judge`` (diff is ``source - target``)."""
    if perfect_min <= diff <= perfect_max:
        return 1.0
    if great_min <= diff <= great_max:
        return 2.0
    if good_min <= diff <= good_max:
        return 3.0
    return 0.0


# Registry of easing ops -> literal transcriptions of the bodies in sonolus/script/easing.py.
# Each begins with ``x = max(0, min(1, x))`` which is exactly ``clamp(x, 0, 1)``
# (``clamp(x, a, b) = max(a, min(b, x))``). The transcription is verified bit-for-bit against
# easing.py by tests/backend/test_interpret_oracle.py so the two cannot silently drift.
_EASE_FUNCS: dict[Op, Callable[[float], float]] = {}


def _ease(op: Op) -> Callable[[Callable[[float], float]], Callable[[float], float]]:
    def register(fn: Callable[[float], float]) -> Callable[[float], float]:
        _EASE_FUNCS[op] = fn
        return fn

    return register


@_ease(Op.EaseInBack)
def _ease_in_back(x: float) -> float:
    x = max(0, min(1, x))
    c1 = 1.70158
    c3 = c1 + 1
    return c3 * x**3 - c1 * x**2


@_ease(Op.EaseOutBack)
def _ease_out_back(x: float) -> float:
    x = max(0, min(1, x))
    c1 = 1.70158
    c3 = c1 + 1
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2


@_ease(Op.EaseInOutBack)
def _ease_in_out_back(x: float) -> float:
    x = max(0, min(1, x))
    c1 = 1.70158
    c2 = c1 * 1.525
    if x < 0.5:
        return ((2 * x) ** 2 * ((c2 + 1) * 2 * x - c2)) / 2
    else:
        return ((2 * x - 2) ** 2 * ((c2 + 1) * (2 * x - 2) + c2) + 2) / 2


@_ease(Op.EaseOutInBack)
def _ease_out_in_back(x: float) -> float:
    x = max(0, min(1, x))
    c1 = 1.70158
    c3 = c1 + 1
    if x < 0.5:
        return (1 + c3 * (2 * x - 1) ** 3 + c1 * (2 * x - 1) ** 2) / 2
    else:
        return (c3 * (2 * x - 1) ** 3 - c1 * (2 * x - 1) ** 2) / 2 + 0.5


@_ease(Op.EaseInCirc)
def _ease_in_circ(x: float) -> float:
    x = max(0, min(1, x))
    return 1 - math.sqrt(1 - x**2)


@_ease(Op.EaseOutCirc)
def _ease_out_circ(x: float) -> float:
    x = max(0, min(1, x))
    return math.sqrt(1 - (x - 1) ** 2)


@_ease(Op.EaseInOutCirc)
def _ease_in_out_circ(x: float) -> float:
    x = max(0, min(1, x))
    if x < 0.5:
        return (1 - math.sqrt(1 - (2 * x) ** 2)) / 2
    else:
        return (math.sqrt(1 - (2 * x - 2) ** 2) + 1) / 2


@_ease(Op.EaseOutInCirc)
def _ease_out_in_circ(x: float) -> float:
    x = max(0, min(1, x))
    if x < 0.5:
        return math.sqrt(1 - (2 * x - 1) ** 2) / 2
    else:
        return (1 - math.sqrt(1 - (2 * x - 1) ** 2)) / 2 + 0.5


@_ease(Op.EaseInCubic)
def _ease_in_cubic(x: float) -> float:
    x = max(0, min(1, x))
    return x**3


@_ease(Op.EaseOutCubic)
def _ease_out_cubic(x: float) -> float:
    x = max(0, min(1, x))
    return 1 - (1 - x) ** 3


@_ease(Op.EaseInOutCubic)
def _ease_in_out_cubic(x: float) -> float:
    x = max(0, min(1, x))
    if x < 0.5:
        return 4 * x**3
    else:
        return 1 - (-2 * x + 2) ** 3 / 2


@_ease(Op.EaseOutInCubic)
def _ease_out_in_cubic(x: float) -> float:
    x = max(0, min(1, x))
    if x < 0.5:
        return (1 - (1 - 2 * x) ** 3) / 2
    else:
        return ((2 * x - 1) ** 3) / 2 + 0.5


@_ease(Op.EaseInElastic)
def _ease_in_elastic(x: float) -> float:
    x = max(0, min(1, x))
    c4 = (2 * math.pi) / 3
    if x in {0, 1}:
        return x
    else:
        return -(2 ** (10 * x - 10)) * math.sin((x * 10 - 10.75) * c4)


@_ease(Op.EaseOutElastic)
def _ease_out_elastic(x: float) -> float:
    x = max(0, min(1, x))
    c4 = (2 * math.pi) / 3
    if x in {0, 1}:
        return x
    else:
        return 2 ** (-10 * x) * math.sin((x * 10 - 0.75) * c4) + 1


@_ease(Op.EaseInOutElastic)
def _ease_in_out_elastic(x: float) -> float:
    x = max(0, min(1, x))
    c5 = (2 * math.pi) / 4.5
    if x in {0, 1}:
        return x
    elif x < 0.5:
        return -(2 ** (20 * x - 10) * math.sin((20 * x - 11.125) * c5)) / 2
    else:
        return (2 ** (-20 * x + 10) * math.sin((20 * x - 11.125) * c5)) / 2 + 1


@_ease(Op.EaseOutInElastic)
def _ease_out_in_elastic(x: float) -> float:
    x = max(0, min(1, x))
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


@_ease(Op.EaseInExpo)
def _ease_in_expo(x: float) -> float:
    x = max(0, min(1, x))
    return 0 if x == 0 else 2 ** (10 * x - 10)


@_ease(Op.EaseOutExpo)
def _ease_out_expo(x: float) -> float:
    x = max(0, min(1, x))
    return 1 if x == 1 else 1 - 2 ** (-10 * x)


@_ease(Op.EaseInOutExpo)
def _ease_in_out_expo(x: float) -> float:
    x = max(0, min(1, x))
    if x in {0, 1}:
        return x
    elif x < 0.5:
        return 2 ** (20 * x - 10) / 2
    else:
        return (2 - 2 ** (-20 * x + 10)) / 2


@_ease(Op.EaseOutInExpo)
def _ease_out_in_expo(x: float) -> float:
    x = max(0, min(1, x))
    if x in {0, 1}:
        return x
    elif x < 0.5:
        return (1 - 2 ** (-20 * x)) / 2
    else:
        return (2 ** (20 * x - 20)) / 2 + 0.5


@_ease(Op.EaseInQuad)
def _ease_in_quad(x: float) -> float:
    x = max(0, min(1, x))
    return x**2


@_ease(Op.EaseOutQuad)
def _ease_out_quad(x: float) -> float:
    x = max(0, min(1, x))
    return 1 - (1 - x) ** 2


@_ease(Op.EaseInOutQuad)
def _ease_in_out_quad(x: float) -> float:
    x = max(0, min(1, x))
    if x < 0.5:
        return 2 * x**2
    else:
        return 1 - (-2 * x + 2) ** 2 / 2


@_ease(Op.EaseOutInQuad)
def _ease_out_in_quad(x: float) -> float:
    x = max(0, min(1, x))
    if x < 0.5:
        return (1 - (1 - 2 * x) ** 2) / 2
    else:
        return ((2 * x - 1) ** 2) / 2 + 0.5


@_ease(Op.EaseInQuart)
def _ease_in_quart(x: float) -> float:
    x = max(0, min(1, x))
    return x**4


@_ease(Op.EaseOutQuart)
def _ease_out_quart(x: float) -> float:
    x = max(0, min(1, x))
    return 1 - (1 - x) ** 4


@_ease(Op.EaseInOutQuart)
def _ease_in_out_quart(x: float) -> float:
    x = max(0, min(1, x))
    if x < 0.5:
        return 8 * x**4
    else:
        return 1 - (-2 * x + 2) ** 4 / 2


@_ease(Op.EaseOutInQuart)
def _ease_out_in_quart(x: float) -> float:
    x = max(0, min(1, x))
    if x < 0.5:
        return (1 - (1 - 2 * x) ** 4) / 2
    else:
        return ((2 * x - 1) ** 4) / 2 + 0.5


@_ease(Op.EaseInQuint)
def _ease_in_quint(x: float) -> float:
    x = max(0, min(1, x))
    return x**5


@_ease(Op.EaseOutQuint)
def _ease_out_quint(x: float) -> float:
    x = max(0, min(1, x))
    return 1 - (1 - x) ** 5


@_ease(Op.EaseInOutQuint)
def _ease_in_out_quint(x: float) -> float:
    x = max(0, min(1, x))
    if x < 0.5:
        return 16 * x**5
    else:
        return 1 - (-2 * x + 2) ** 5 / 2


@_ease(Op.EaseOutInQuint)
def _ease_out_in_quint(x: float) -> float:
    x = max(0, min(1, x))
    if x < 0.5:
        return (1 - (1 - 2 * x) ** 5) / 2
    else:
        return ((2 * x - 1) ** 5) / 2 + 0.5


@_ease(Op.EaseInSine)
def _ease_in_sine(x: float) -> float:
    x = max(0, min(1, x))
    return 1 - math.cos((x * math.pi) / 2)


@_ease(Op.EaseOutSine)
def _ease_out_sine(x: float) -> float:
    x = max(0, min(1, x))
    return math.sin((x * math.pi) / 2)


@_ease(Op.EaseInOutSine)
def _ease_in_out_sine(x: float) -> float:
    x = max(0, min(1, x))
    return -(math.cos(math.pi * x) - 1) / 2


@_ease(Op.EaseOutInSine)
def _ease_out_in_sine(x: float) -> float:
    x = max(0, min(1, x))
    if x < 0.5:
        return math.sin(math.pi * x) / 2
    else:
        return (1 - math.cos(math.pi * x)) / 2


class Interpreter:
    blocks: dict[int, list[int]]
    log: list[float]

    def __init__(self):
        self.blocks = {}
        self.log = []

    def run(self, node: EngineNode) -> float:
        if not isinstance(node, FunctionNode):
            return node
        func = node.func
        args = node.args
        match func:
            case Op.Execute:
                result = 0.0
                for arg in args:
                    result = self.run(arg)
                return result
            case Op.Execute0:
                for arg in args:
                    self.run(arg)
                return 0.0
            case Op.If:
                test, t_branch, f_branch = args
                if self.run(test) != 0.0:
                    return self.run(t_branch)
                else:
                    return self.run(f_branch)
            case Op.Switch:
                test, *branches = args
                test_result = self.run(test)
                for i in range(0, len(branches), 2):
                    case, branch = branches[i], branches[i + 1]
                    if test_result == self.run(case):
                        return self.run(branch)
                return 0.0
            case Op.SwitchWithDefault:
                test, *branches, default = args
                test_result = self.run(test)
                for i in range(0, len(branches), 2):
                    case, branch = branches[i], branches[i + 1]
                    if test_result == self.run(case):
                        return self.run(branch)
                return self.run(default)
            case Op.SwitchInteger:
                test, *branches = args
                test_result = self.run(test)
                if 0 <= test_result < len(branches) and int(test_result) == test_result:
                    return self.run(branches[int(test_result)])
                else:
                    return 0.0
            case Op.SwitchIntegerWithDefault:
                test, *branches, default = args
                test_result = self.run(test)
                if 0 <= test_result < len(branches) and int(test_result) == test_result:
                    return self.run(branches[int(test_result)])
                else:
                    return self.run(default)
            case Op.While:
                test, body = args
                while self.run(test) != 0.0:
                    self.run(body)
                return 0.0
            case Op.DoWhile:
                body, test = args
                while True:
                    self.run(body)
                    if self.run(test) == 0.0:
                        break
                return 0.0
            case Op.And:
                result = 0.0
                for arg in args:
                    result = self.run(arg)
                    if result == 0.0:
                        break
                return result
            case Op.Or:
                result = 0.0
                for arg in args:
                    result = self.run(arg)
                    if result != 0.0:
                        break
                return result
            case Op.JumpLoop:
                index = 0
                while 0 <= index < len(args):
                    if index == len(args) - 1:
                        return self.run(args[index])
                    index = int(self.run(args[index]))
                return 0.0
            case Op.Block:
                try:
                    return self.run(args[0])
                except BreakException as e:
                    if e.n > 1:
                        e.n -= 1
                        raise e from None
                    return e.value
            case Op.Break:
                raise BreakException(self.ensure_int(self.run(args[0])), self.run(args[1]))

            case Op.Abs:
                return abs(self.run(args[0]))
            case Op.Add:
                return self.reduce_args(args, operator.add)
            case Op.Arccos:
                return math.acos(self.run(args[0]))
            case Op.Arcsin:
                return math.asin(self.run(args[0]))
            case Op.Arctan:
                return math.atan(self.run(args[0]))
            case Op.Arctan2:
                return math.atan2(self.run(args[0]), self.run(args[1]))
            case Op.Ceil:
                return math.ceil(self.run(args[0]))
            case Op.Clamp:
                x, a, b = (self.run(arg) for arg in args)
                return max(a, min(b, x))
            case Op.Copy:
                src_id, src_index, dst_id, dst_index, count = (self.ensure_int(self.run(arg)) for arg in args)
                assert count >= 0, "Count must be non-negative"
                values = [self.get(src_id, src_index + i) for i in range(count)]
                for i, value in enumerate(values):
                    self.set(dst_id, dst_index + i, value)
                return 0.0
            case Op.Cos:
                return math.cos(self.run(args[0]))
            case Op.Cosh:
                return math.cosh(self.run(args[0]))
            case Op.DebugLog:
                value = self.run(args[0])
                self.log.append(value)
                return 0.0
            case Op.DebugPause:
                return 0.0
            case Op.DecrementPost:
                # Post returns the NEW value (REVERSE of C; wiki-confirmed semantics).
                block, index = (self.ensure_int(self.run(arg)) for arg in args)
                old = self.get(block, index)
                self.set(block, index, old - 1)
                return old - 1
            case Op.DecrementPostPointed:
                block, index, offset = (self.ensure_int(self.run(arg)) for arg in args)
                deref_block = self.get(block, index)
                deref_index = self.get(block, index + 1) + offset
                old = self.get(deref_block, deref_index)
                self.set(deref_block, deref_index, old - 1)
                return old - 1
            case Op.DecrementPostShifted:
                block, offset, index, stride = (self.ensure_int(self.run(arg)) for arg in args)
                addr = offset + index * stride
                old = self.get(block, addr)
                self.set(block, addr, old - 1)
                return old - 1
            case Op.DecrementPre:
                # Pre returns the OLD value (REVERSE of C; wiki-confirmed semantics).
                block, index = (self.ensure_int(self.run(arg)) for arg in args)
                old = self.get(block, index)
                self.set(block, index, old - 1)
                return old
            case Op.DecrementPrePointed:
                block, index, offset = (self.ensure_int(self.run(arg)) for arg in args)
                deref_block = self.get(block, index)
                deref_index = self.get(block, index + 1) + offset
                old = self.get(deref_block, deref_index)
                self.set(deref_block, deref_index, old - 1)
                return old
            case Op.DecrementPreShifted:
                block, offset, index, stride = (self.ensure_int(self.run(arg)) for arg in args)
                addr = offset + index * stride
                old = self.get(block, addr)
                self.set(block, addr, old - 1)
                return old
            case Op.Degree:
                return math.degrees(self.run(args[0]))
            case Op.Divide:
                return self.reduce_args(args, operator.truediv)
            case Op.Equal:
                return 1.0 if self.run(args[0]) == self.run(args[1]) else 0.0
            case Op.Floor:
                return math.floor(self.run(args[0]))
            case Op.Frac:
                result = self.run(args[0]) % 1
                return result if result >= 0 else result + 1
            case Op.Get:
                block, index = (self.ensure_int(self.run(arg)) for arg in args)
                return self.get(block, index)
            case Op.GetPointed:
                block, index, offset = (self.ensure_int(self.run(arg)) for arg in args)
                return self.get(self.get(block, index), self.get(block, index + 1) + offset)
            case Op.GetShifted:
                block, offset, index, stride = (self.ensure_int(self.run(arg)) for arg in args)
                return self.get(block, offset + index * stride)
            case Op.Greater:
                return 1.0 if self.run(args[0]) > self.run(args[1]) else 0.0
            case Op.GreaterOr:
                return 1.0 if self.run(args[0]) >= self.run(args[1]) else 0.0
            case Op.IncrementPost:
                # Post returns the NEW value (REVERSE of C; wiki-confirmed semantics).
                block, index = (self.ensure_int(self.run(arg)) for arg in args)
                old = self.get(block, index)
                self.set(block, index, old + 1)
                return old + 1
            case Op.IncrementPostPointed:
                block, index, offset = (self.ensure_int(self.run(arg)) for arg in args)
                deref_block = self.get(block, index)
                deref_index = self.get(block, index + 1) + offset
                old = self.get(deref_block, deref_index)
                self.set(deref_block, deref_index, old + 1)
                return old + 1
            case Op.IncrementPostShifted:
                block, offset, index, stride = (self.ensure_int(self.run(arg)) for arg in args)
                addr = offset + index * stride
                old = self.get(block, addr)
                self.set(block, addr, old + 1)
                return old + 1
            case Op.IncrementPre:
                # Pre returns the OLD value (REVERSE of C; wiki-confirmed semantics).
                block, index = (self.ensure_int(self.run(arg)) for arg in args)
                old = self.get(block, index)
                self.set(block, index, old + 1)
                return old
            case Op.IncrementPrePointed:
                block, index, offset = (self.ensure_int(self.run(arg)) for arg in args)
                deref_block = self.get(block, index)
                deref_index = self.get(block, index + 1) + offset
                old = self.get(deref_block, deref_index)
                self.set(deref_block, deref_index, old + 1)
                return old
            case Op.IncrementPreShifted:
                block, offset, index, stride = (self.ensure_int(self.run(arg)) for arg in args)
                addr = offset + index * stride
                old = self.get(block, addr)
                self.set(block, addr, old + 1)
                return old
            case Op.Lerp:
                x, y, s = (self.run(arg) for arg in args)
                return x + (y - x) * s
            case Op.LerpClamped:
                x, y, s = (self.run(arg) for arg in args)
                return x + (y - x) * max(0, min(1, s))
            case Op.Less:
                return 1.0 if self.run(args[0]) < self.run(args[1]) else 0.0
            case Op.LessOr:
                return 1.0 if self.run(args[0]) <= self.run(args[1]) else 0.0
            case Op.Log:
                return math.log(self.run(args[0]))
            case Op.Max:
                return max(self.run(args[0]), self.run(args[1]))
            case Op.Min:
                return min(self.run(args[0]), self.run(args[1]))
            case Op.Mod:
                return self.reduce_args(args, operator.mod)
            case Op.Multiply:
                return self.reduce_args(args, operator.mul)
            case Op.Negate:
                return -self.run(args[0])
            case Op.Not:
                return 1.0 if self.run(args[0]) == 0.0 else 0.0
            case Op.NotEqual:
                return 1.0 if self.run(args[0]) != self.run(args[1]) else 0.0
            case Op.Power:
                return self.reduce_args(args, operator.pow)
            case Op.Radian:
                return math.radians(self.run(args[0]))
            case Op.Random:
                lo, hi = (self.run(arg) for arg in args)
                return random.uniform(lo, hi)
            case Op.RandomInteger:
                lo, hi = (self.ensure_int(self.run(arg)) for arg in args)
                return random.randrange(lo, hi)
            case Op.Rem:
                return self.reduce_args(args, _rem)
            case Op.Remap:
                from_min, from_max, to_min, to_max, value = (self.run(arg) for arg in args)
                return to_min + (to_max - to_min) * (value - from_min) / (from_max - from_min)
            case Op.RemapClamped:
                from_min, from_max, to_min, to_max, value = (self.run(arg) for arg in args)
                return to_min + (to_max - to_min) * max(0, min(1, (value - from_min) / (from_max - from_min)))
            case Op.Round:
                return round(self.run(args[0]))
            case Op.Set:
                block, index, value = (self.run(arg) for arg in args)
                block, index = self.ensure_int(block), self.ensure_int(index)
                return self.set(block, index, value)
            # Fused read-modify-write ops. Each is defined as
            #   Set(addr, <binop>(Get(addr), value))
            # with the address computed once and ``value`` evaluated before the read.
            # The binop reuses the exact operator/helper of the plain binary op so the
            # numeric result matches bit-for-bit. Each returns the new (stored) value.
            case Op.SetAdd:
                return self._set_rmw(args, operator.add)
            case Op.SetAddPointed:
                return self._set_rmw_pointed(args, operator.add)
            case Op.SetAddShifted:
                return self._set_rmw_shifted(args, operator.add)
            case Op.SetSubtract:
                return self._set_rmw(args, operator.sub)
            case Op.SetSubtractPointed:
                return self._set_rmw_pointed(args, operator.sub)
            case Op.SetSubtractShifted:
                return self._set_rmw_shifted(args, operator.sub)
            case Op.SetMultiply:
                return self._set_rmw(args, operator.mul)
            case Op.SetMultiplyPointed:
                return self._set_rmw_pointed(args, operator.mul)
            case Op.SetMultiplyShifted:
                return self._set_rmw_shifted(args, operator.mul)
            case Op.SetDivide:
                return self._set_rmw(args, operator.truediv)
            case Op.SetDividePointed:
                return self._set_rmw_pointed(args, operator.truediv)
            case Op.SetDivideShifted:
                return self._set_rmw_shifted(args, operator.truediv)
            case Op.SetMod:
                return self._set_rmw(args, operator.mod)
            case Op.SetModPointed:
                return self._set_rmw_pointed(args, operator.mod)
            case Op.SetModShifted:
                return self._set_rmw_shifted(args, operator.mod)
            case Op.SetRem:
                return self._set_rmw(args, _rem)
            case Op.SetRemPointed:
                return self._set_rmw_pointed(args, _rem)
            case Op.SetRemShifted:
                return self._set_rmw_shifted(args, _rem)
            case Op.SetPower:
                return self._set_rmw(args, operator.pow)
            case Op.SetPowerPointed:
                return self._set_rmw_pointed(args, operator.pow)
            case Op.SetPowerShifted:
                return self._set_rmw_shifted(args, operator.pow)
            case Op.SetPointed:
                block, index, offset, value = (self.run(arg) for arg in args)
                block, index, offset = self.ensure_int(block), self.ensure_int(index), self.ensure_int(offset)
                return self.set(self.get(block, index), self.get(block, index + 1) + offset, value)
            case Op.SetShifted:
                block, offset, index, stride, value = (self.run(arg) for arg in args)
                block, offset, index, stride = (
                    self.ensure_int(block),
                    self.ensure_int(offset),
                    self.ensure_int(index),
                    self.ensure_int(stride),
                )

                return self.set(block, offset + index * stride, value)
            case Op.Sign:
                return _sign(self.run(args[0]))
            case Op.Sin:
                return math.sin(self.run(args[0]))
            case Op.Sinh:
                return math.sinh(self.run(args[0]))
            case Op.Subtract:
                return self.reduce_args(args, operator.sub)
            case Op.Tan:
                return math.tan(self.run(args[0]))
            case Op.Tanh:
                return math.tanh(self.run(args[0]))
            case Op.Trunc:
                return math.trunc(self.run(args[0]))
            case Op.Unlerp:
                lo, hi, value = (self.run(arg) for arg in args)
                return (value - lo) / (hi - lo)
            case Op.UnlerpClamped:
                lo, hi, value = (self.run(arg) for arg in args)
                return max(0, min(1, (value - lo) / (hi - lo)))
            case Op.Judge:
                source, target, perfect_min, perfect_max, great_min, great_max, good_min, good_max = (
                    self.run(arg) for arg in args
                )
                return _judge(
                    source - target, perfect_min, perfect_max, great_min, great_max, good_min, good_max
                )
            case Op.JudgeSimple:
                source, target, max_perfect, max_great, max_good = (self.run(arg) for arg in args)
                return _judge(
                    source - target,
                    -max_perfect,
                    max_perfect,
                    -max_great,
                    max_great,
                    -max_good,
                    max_good,
                )
            case _ if func in _EASE_FUNCS:
                return _EASE_FUNCS[func](self.run(args[0]))
            case _:
                raise NotImplementedError(f"Unsupported operation: {func}")

    def reduce_args(self, args: list[EngineNode], operator) -> float:
        if not args:
            return 0.0
        acc, *rest = (self.run(arg) for arg in args)
        for arg in rest:
            acc = operator(acc, arg)
        return acc

    def _set_rmw(self, args: list[EngineNode], op: Callable[[float, float], float]) -> float:
        """Fused scalar RMW: ``Set(id, index, op(Get(id, index), value))``.

        Address args are evaluated once, left-to-right, and ``value`` is evaluated
        before the memory read; the new (stored) value is returned.
        """
        block, index, value = (self.run(arg) for arg in args)
        block, index = self.ensure_int(block), self.ensure_int(index)
        return self.set(block, index, op(self.get(block, index), value))

    def _set_rmw_pointed(self, args: list[EngineNode], op: Callable[[float, float], float]) -> float:
        """Fused pointer-relative RMW mirroring ``SetPointed``'s double-deref addressing."""
        block, index, offset, value = (self.run(arg) for arg in args)
        block, index, offset = self.ensure_int(block), self.ensure_int(index), self.ensure_int(offset)
        deref_block = self.get(block, index)
        deref_index = self.get(block, index + 1) + offset
        return self.set(deref_block, deref_index, op(self.get(deref_block, deref_index), value))

    def _set_rmw_shifted(self, args: list[EngineNode], op: Callable[[float, float], float]) -> float:
        """Fused strided RMW mirroring ``SetShifted``'s ``offset + index * stride`` addressing."""
        block, offset, index, stride, value = (self.run(arg) for arg in args)
        block, offset, index, stride = (
            self.ensure_int(block),
            self.ensure_int(offset),
            self.ensure_int(index),
            self.ensure_int(stride),
        )
        addr = offset + index * stride
        return self.set(block, addr, op(self.get(block, addr), value))

    def get(self, block: float, index: float) -> float:
        block = self.ensure_int(block)
        index = self.ensure_int(index)
        assert index >= 0, "Index must be non-negative"
        assert index <= 65535, "Index is too large"
        if block not in self.blocks:
            self.blocks[block] = []
        if len(self.blocks[block]) <= index:
            self.blocks[block] += [-1.0] * (index - len(self.blocks[block]) + 1)
        return self.blocks[block][index]

    def set(self, block: float, index: float, value: float):
        block = self.ensure_int(block)
        index = self.ensure_int(index)
        assert index >= 0, "Index must be non-negative"
        assert index <= 65535, "Index is too large"
        if block not in self.blocks:
            self.blocks[block] = []
        if len(self.blocks[block]) <= index:
            self.blocks[block] += [-1.0] * (index - len(self.blocks[block]) + 1)
        self.blocks[block][index] = value
        return value

    def ensure_int(self, value: float) -> int:
        assert value == int(value), "Value must be an integer"
        return int(value)
