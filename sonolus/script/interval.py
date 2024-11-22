from typing import Self, overload

from sonolus.backend.ops import Op
from sonolus.script.debug import error
from sonolus.script.internal.native import native_function
from sonolus.script.num import Num
from sonolus.script.record import Record


class Interval(Record):
    """A closed interval."""

    start: float
    end: float

    @property
    def length(self) -> float:
        return self.end - self.start

    @property
    def is_empty(self) -> bool:
        return self.start > self.end

    @property
    def mid(self) -> float:
        return (self.start + self.end) / 2

    @property
    def tuple(self):
        return self.start, self.end

    def __contains__(self, item: Self | float | int) -> bool:
        match item:
            case Interval(start, end):
                return self.start <= start and end <= self.end
            case float() | int() as value:
                return self.start <= value <= self.end
            case _:
                error("Invalid type for interval check")

    def __add__(self, other: float | int) -> Self:
        return Interval(self.start + other, self.end + other)

    def __sub__(self, other: float | int) -> Self:
        return Interval(self.start - other, self.end - other)

    def __mul__(self, other: float | int) -> Self:
        return Interval(self.start * other, self.end * other)

    def __truediv__(self, other: float | int) -> Self:
        return Interval(self.start / other, self.end / other)

    def __floordiv__(self, other: float | int) -> Self:
        return Interval(self.start // other, self.end // other)

    def __and__(self, other: Self) -> Self:
        return Interval(max(self.start, other.start), min(self.end, other.end))

    def shrink(self, value: float | int) -> Self:
        return Interval(self.start + value, self.end - value)

    def expand(self, value: float | int) -> Self:
        return Interval(self.start - value, self.end + value)

    def lerp(self, x: float, /) -> float:
        return lerp(self.start, self.end, x)

    def lerp_clamped(self, x: float, /) -> float:
        return lerp_clamped(self.start, self.end, x)

    def unlerp(self, x: float, /) -> float:
        return unlerp(self.start, self.end, x)

    def unlerp_clamped(self, x: float, /) -> float:
        return unlerp_clamped(self.start, self.end, x)

    def clamp(self, x: float, /) -> float:
        return clamp(x, self.start, self.end)


@native_function(Op.Lerp)
def _num_lerp(a, b, x, /):
    return a + (b - a) * x


@native_function(Op.LerpClamped)
def _num_lerp_clamped(a, b, x, /):
    return a + (b - a) * max(0, min(1, x))


def _generic_lerp[T](a: T, b: T, x: float, /) -> T:
    return a + (b - a) * x


def _generic_lerp_clamped[T](a: T, b: T, x: float, /) -> T:
    return a + (b - a) * max(0, min(1, x))


@overload
def lerp(a: float, b: float, x: float, /) -> float: ...


@overload
def lerp[T](a: T, b: T, x: float, /) -> T: ...


def lerp(a, b, x, /):
    match a, b:
        case (Num(a), Num(b)):
            return _num_lerp(a, b, x)
        case _:
            return _generic_lerp(a, b, x)


@overload
def lerp_clamped(a: float, b: float, x: float, /) -> float: ...


@overload
def lerp_clamped[T](a: T, b: T, x: float, /) -> T: ...


def lerp_clamped(a, b, x, /):
    match a, b:
        case (Num(a), Num(b)):
            return _num_lerp_clamped(a, b, x)
        case _:
            return _generic_lerp_clamped(a, b, x)


@native_function(Op.Unlerp)
def unlerp(a: float, b: float, x: float, /):
    return (x - a) / (b - a)


@native_function(Op.UnlerpClamped)
def unlerp_clamped(a: float, b: float, x: float, /):
    return max(0, min(1, (x - a) / (b - a)))


@native_function(Op.Remap)
def remap(a: float, b: float, c: float, d: float, x: float, /):
    return c + (d - c) * (x - a) / (b - a)


@native_function(Op.RemapClamped)
def remap_clamped(a: float, b: float, c: float, d: float, x: float, /):
    return c + (d - c) * max(0, min(1, (x - a) / (b - a)))


@native_function(Op.Clamp)
def clamp(x: float, a: float, b: float, /):
    return max(a, min(b, x))
