from typing import Self

from sonolus.script.math import atan2, cos, sin
from sonolus.script.num import Num
from sonolus.script.record import Record


class Vec2(Record):
    x: float
    y: float

    @classmethod
    def zero(cls) -> Self:
        return cls(x=0, y=0)

    @classmethod
    def one(cls) -> Self:
        return cls(x=1, y=1)

    @classmethod
    def up(cls) -> Self:
        return cls(x=0, y=1)

    @classmethod
    def down(cls) -> Self:
        return cls(x=0, y=-1)

    @classmethod
    def left(cls) -> Self:
        return cls(x=-1, y=0)

    @classmethod
    def right(cls) -> Self:
        return cls(x=1, y=0)

    @property
    def magnitude(self) -> Num:
        return (self.x**2 + self.y**2) ** 0.5

    @property
    def angle(self) -> Num:
        return atan2(self.y, self.x)

    def dot(self, other: Self) -> Num:
        return self.x * other.x + self.y * other.y

    def rotate(self, angle: Num) -> Self:
        return Vec2(
            x=self.x * cos(angle) - self.y * sin(angle),
            y=self.x * sin(angle) + self.y * cos(angle),
        )

    def rotate_about(self, angle: Num, pivot: Self) -> Self:
        return (self - pivot).rotate(angle) + pivot

    @property
    def tuple(self) -> tuple[float, float]:
        return self.x, self.y

    def __add__(self, other: Self) -> Self:
        return Vec2(x=self.x + other.x, y=self.y + other.y)

    def __sub__(self, other: Self) -> Self:
        return Vec2(x=self.x - other.x, y=self.y - other.y)

    def __mul__(self, other: Self | float) -> Self:
        match other:
            case Vec2(x, y):
                return Vec2(x=self.x * x, y=self.y * y)
            case float() | int() as factor:
                return Vec2(x=self.x * factor, y=self.y * factor)

    def __truediv__(self, other: Self | float) -> Self:
        match other:
            case Vec2(x, y):
                return Vec2(x=self.x / x, y=self.y / y)
            case float() | int() as factor:
                return Vec2(x=self.x / factor, y=self.y / factor)

    def __neg__(self) -> Self:
        return Vec2(x=-self.x, y=-self.y)
