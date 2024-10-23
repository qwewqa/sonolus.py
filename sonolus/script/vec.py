from typing import Self

from sonolus.script.num import Num
from sonolus.script.record import Record


class Vec2(Record):
    x: float
    y: float

    def __add__(self, other: Self) -> Self:
        return Vec2(x=self.x + other.x, y=self.y + other.y)

    def __sub__(self, other: Self) -> Self:
        return Vec2(x=self.x - other.x, y=self.y - other.y)

    def __mul__(self, other: Self | float) -> Self:
        if isinstance(other, Vec2):
            return Vec2(x=self.x * other.x, y=self.y * other.y)
        else:
            return Vec2(x=self.x * other, y=self.y * other)

    def __truediv__(self, other: Self | float) -> Self:
        if isinstance(other, Vec2):
            return Vec2(x=self.x / other.x, y=self.y / other.y)
        else:
            return Vec2(x=self.x / other, y=self.y / other)

    def __neg__(self) -> Self:
        return Vec2(x=-self.x, y=-self.y)

    def magnitude(self) -> Num:
        return (self.x**2 + self.y**2) ** 0.5

    def dot(self, other: Self) -> Num:
        return self.x * other.x + self.y * other.y
