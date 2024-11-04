from __future__ import annotations

from typing import Protocol

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
    def from_xywh(cls, x: float, y: float, w: float, h: float) -> Rect:
        return cls(
            t=y,
            r=x + w,
            b=y + h,
            l=x,
        )

    @classmethod
    def from_center(cls, x: float, y: float, w: float, h: float) -> Rect:
        return cls(
            t=y - h / 2,
            r=x + w / 2,
            b=y + h / 2,
            l=x - w / 2,
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
        self.b = self.t + value

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

    def scale(self, x: float, y: float | None, /):
        if y is None:
            y = x
        self.l *= x
        self.r *= x
        self.t *= y
        self.b *= y

    def translate(self, x: float, y: float, /):
        self.l += x
        self.r += x
        self.t += y
        self.b += y


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
