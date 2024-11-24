import math
import operator
import random

from sonolus.backend.node import ConstantNode, EngineNode
from sonolus.backend.ops import Op


class BreakException(Exception):  # noqa: N818
    n: int
    value: float

    def __init__(self, n: int, value: float):
        self.n = n
        self.value = value


class Interpreter:
    blocks: dict[int, list[int]]
    log: list[float]

    def __init__(self):
        self.blocks = {}
        self.log = []

    def run(self, node: EngineNode) -> float:
        if isinstance(node, ConstantNode):
            return node.value
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
                    return self.run(branches[test_result])
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
                block, index = (self.ensure_int(self.run(arg)) for arg in args)
                value = self.get(block, index)
                self.set(block, index, value + 1)
                return value
            case Op.IncrementPostPointed:
                block, index, offset = (self.ensure_int(self.run(arg)) for arg in args)
                deref_block = self.get(block, index)
                deref_index = self.get(block, index + 1) + offset
                value = self.get(deref_block, deref_index)
                self.set(deref_block, deref_index, value + 1)
                return value
            case Op.IncrementPostShifted:
                block, offset, index, stride = (self.ensure_int(self.run(arg)) for arg in args)
                value = self.get(block, offset + index * stride)
                self.set(block, offset + index * stride, value + 1)
                return value
            case Op.IncrementPre:
                block, index = (self.ensure_int(self.run(arg)) for arg in args)
                value = self.get(block, index) + 1
                self.set(block, index, value)
                return value
            case Op.IncrementPrePointed:
                block, index, offset = (self.ensure_int(self.run(arg)) for arg in args)
                deref_block = self.get(block, index)
                deref_index = self.get(block, index + 1) + offset
                value = self.get(deref_block, deref_index) + 1
                self.set(deref_block, deref_index, value)
                return value
            case Op.IncrementPreShifted:
                block, offset, index, stride = (self.ensure_int(self.run(arg)) for arg in args)
                value = self.get(block, offset + index * stride) + 1
                self.set(block, offset + index * stride, value)
                return value
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
                return self.reduce_args(args, math.remainder)
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
                return math.copysign(1, self.run(args[0]))
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
            case _:
                raise NotImplementedError(f"Unsupported operation: {func}")

    def reduce_args(self, args: list[EngineNode], operator) -> float:
        if not args:
            return 0.0
        acc, *rest = (self.run(arg) for arg in args)
        for arg in rest:
            acc = operator(acc, arg)
        return acc

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
