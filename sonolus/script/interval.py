from typing import Self

from sonolus.backend.ops import Op
from sonolus.script.debug import error
from sonolus.script.internal.native import native_function
from sonolus.script.record import Record


class Interval(Record):
    """A closed interval."""

    left: float
    right: float

    @property
    def length(self) -> float:
        return self.right - self.left

    @property
    def is_empty(self) -> bool:
        return self.left > self.right

    @property
    def mid(self) -> float:
        return (self.left + self.right) / 2

    @property
    def tuple(self):
        return self.left, self.right

    def __contains__(self, item: Self | float | int) -> bool:
        match item:
            case Interval(left, right):
                return self.left <= left and right <= self.right
            case float() | int() as value:
                return self.left <= value <= self.right
            case _:
                error("Invalid type for interval check")

    def __add__(self, other: float | int) -> Self:
        return Interval(self.left + other, self.right + other)

    def __sub__(self, other: float | int) -> Self:
        return Interval(self.left - other, self.right - other)

    def __mul__(self, other: float | int) -> Self:
        return Interval(self.left * other, self.right * other)

    def __truediv__(self, other: float | int) -> Self:
        return Interval(self.left / other, self.right / other)

    def __floordiv__(self, other: float | int) -> Self:
        return Interval(self.left // other, self.right // other)

    def __and__(self, other: Self) -> Self:
        return Interval(max(self.left, other.left), min(self.right, other.right))

    def lerp(self, x: float, /) -> float:
        return lerp(self.left, self.right, x)

    def lerp_clamped(self, x: float, /) -> float:
        return lerp_clamped(self.left, self.right, x)

    def unlerp(self, x: float, /) -> float:
        return unlerp(self.left, self.right, x)

    def unlerp_clamped(self, x: float, /) -> float:
        return unlerp_clamped(self.left, self.right, x)


@native_function(Op.Lerp)
def lerp(a, b, x, /):
    return a + (b - a) * x


@native_function(Op.LerpClamped)
def lerp_clamped(a, b, x, /):
    return a + (b - a) * max(0, min(1, x))


@native_function(Op.Unlerp)
def unlerp(a, b, x, /):
    return (x - a) / (b - a)


@native_function(Op.UnlerpClamped)
def unlerp_clamped(a, b, x, /):
    return max(0, min(1, (x - a) / (b - a)))


@native_function(Op.Remap)
def remap(a, b, c, d, x, /):
    return c + (d - c) * (x - a) / (b - a)


@native_function(Op.RemapClamped)
def remap_clamped(a, b, c, d, x, /):
    return c + (d - c) * max(0, min(1, (x - a) / (b - a)))
