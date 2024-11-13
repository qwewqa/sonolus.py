from __future__ import annotations

from typing import Protocol, Self

from sonolus.script.record import Record
from sonolus.script.vec import Vec2


class Quad(Record):
    bl: Vec2
    tl: Vec2
    tr: Vec2
    br: Vec2

    @property
    def center(self) -> Vec2:
        return (self.bl + self.tr + self.tl + self.br) / 4

    def translate(self, translation: Vec2, /) -> Self:
        return Quad(
            bl=self.bl + translation,
            tl=self.tl + translation,
            tr=self.tr + translation,
            br=self.br + translation,
        )

    def scale(self, factor: Vec2, /) -> Self:
        return Quad(
            bl=self.bl * factor,
            tl=self.tl * factor,
            tr=self.tr * factor,
            br=self.br * factor,
        )

    def scale_about(self, factor: Vec2, /, pivot: Vec2) -> Self:
        return Quad(
            bl=(self.bl - pivot) * factor + pivot,
            tl=(self.tl - pivot) * factor + pivot,
            tr=(self.tr - pivot) * factor + pivot,
            br=(self.br - pivot) * factor + pivot,
        )

    def scale_centered(self, factor: Vec2, /) -> Self:
        return Quad(
            bl=self.bl * factor,
            tl=self.tl * factor,
            tr=self.tr * factor,
            br=self.br * factor,
        ).translate(self.center * (Vec2(1, 1) - factor))

    def rotate(self, angle: float, /) -> Self:
        return Quad(
            bl=self.bl.rotate(angle),
            tl=self.tl.rotate(angle),
            tr=self.tr.rotate(angle),
            br=self.br.rotate(angle),
        )

    def rotate_about(
        self,
        angle: float,
        /,
        pivot: Vec2,
    ) -> Self:
        return Quad(
            bl=self.bl.rotate_about(angle, pivot),
            tl=self.tl.rotate_about(angle, pivot),
            tr=self.tr.rotate_about(angle, pivot),
            br=self.br.rotate_about(angle, pivot),
        )

    def rotate_centered(self, angle: float, /) -> Self:
        return self.rotate_about(angle, self.center)


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
    def w(self) -> float:
        return self.r - self.l

    @property
    def h(self) -> float:
        return self.t - self.b

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

    def translate(self, translation: Vec2, /) -> Self:
        return Rect(
            t=self.t + translation.y,
            r=self.r + translation.x,
            b=self.b + translation.y,
            l=self.l + translation.x,
        )

    def scale(self, factor: Vec2, /) -> Self:
        return Rect(
            t=self.t * factor.y,
            r=self.r * factor.x,
            b=self.b * factor.y,
            l=self.l * factor.x,
        )

    def scale_about(self, factor: Vec2, /, pivot: Vec2) -> Self:
        return Rect(
            t=(self.t - pivot.y) * factor.y + pivot.y,
            r=(self.r - pivot.x) * factor.x + pivot.x,
            b=(self.b - pivot.y) * factor.y + pivot.y,
            l=(self.l - pivot.x) * factor.x + pivot.x,
        )

    def scale_centered(self, factor: Vec2, /) -> Self:
        return Rect(
            t=self.t * factor.y,
            r=self.r * factor.x,
            b=self.b * factor.y,
            l=self.l * factor.x,
        ).translate(self.center * (Vec2(1, 1) - factor))

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
