from typing import Self

from sonolus.script.debug import error
from sonolus.script.record import Record


class Interval(Record):
    """A closed interval."""

    left: float
    right: float

    def size(self) -> float:
        return max(0.0, self.right - self.left)

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
