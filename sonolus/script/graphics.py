from __future__ import annotations

from typing import Protocol, Self

from sonolus.script.record import Record
from sonolus.script.vec import Vec2


class Quad(Record):
    bl: Vec2
    tl: Vec2
    tr: Vec2
    br: Vec2


class Rect(Record):
    t: float
    r: float
    b: float
    l: float  # noqa: E741

    @classmethod
    def from_center(cls, center: Vec2, dimensions: Vec2) -> Rect:
        return cls(
            t=center.y + dimensions.y / 2,
            r=center.x + dimensions.x / 2,
            b=center.y - dimensions.y / 2,
            l=center.x - dimensions.x / 2,
        )

    @property
    def x(self) -> float:
        return self.l

    @x.setter
    def x(self, value: float):
        self.r += value - self.l
        self.l = value

    @property
    def y(self) -> float:
        return self.t

    @y.setter
    def y(self, value: float):
        self.b += value - self.t
        self.t = value

    @property
    def w(self) -> float:
        return self.r - self.l

    @w.setter
    def w(self, value: float):
        self.r = self.l + value

    @property
    def h(self) -> float:
        return self.t - self.b

    @h.setter
    def h(self, value: float):
        self.t = self.b + value

    @property
    def bl(self) -> Vec2:
        return Vec2(self.l, self.b)

    @property
    def tl(self) -> Vec2:
        return Vec2(self.l, self.t)

    @property
    def tr(self) -> Vec2:
        return Vec2(self.r, self.t)

    @property
    def br(self) -> Vec2:
        return Vec2(self.r, self.b)

    @property
    def center(self) -> Vec2:
        return Vec2((self.l + self.r) / 2, (self.t + self.b) / 2)

    def as_quad(self) -> Quad:
        return Quad(
            bl=self.bl,
            tl=self.tl,
            tr=self.tr,
            br=self.br,
        )

    def scale(self, factor: Vec2, /) -> Self:
        return Rect(
            t=self.t * factor.y,
            r=self.r * factor.x,
            b=self.b * factor.y,
            l=self.l * factor.x,
        )

    def expand(self, expansion: Vec2, /) -> Self:
        return Rect(
            t=self.t + expansion.y,
            r=self.r + expansion.x,
            b=self.b - expansion.y,
            l=self.l - expansion.x,
        )

    def shrink(self, shrinkage: Vec2, /) -> Self:
        return Rect(
            t=self.t - shrinkage.y,
            r=self.r - shrinkage.x,
            b=self.b + shrinkage.y,
            l=self.l + shrinkage.x,
        )

    def translate(self, translation: Vec2, /) -> Self:
        return Rect(
            t=self.t + translation.y,
            r=self.r + translation.x,
            b=self.b + translation.y,
            l=self.l + translation.x,
        )


class QuadLike(Protocol):
    @property
    def bl(self) -> Vec2: ...

    @property
    def tl(self) -> Vec2: ...

    @property
    def tr(self) -> Vec2: ...

    @property
    def br(self) -> Vec2: ...


def flatten_quad(quad: QuadLike) -> tuple[float, float, float, float, float, float, float, float]:
    return (
        quad.bl.x,
        quad.bl.y,
        quad.tl.x,
        quad.tl.y,
        quad.tr.x,
        quad.tr.y,
        quad.br.x,
        quad.br.y,
    )
