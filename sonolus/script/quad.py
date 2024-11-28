from __future__ import annotations

from typing import Protocol, Self

from sonolus.script.record import Record
from sonolus.script.vec import Vec2, pnpoly


class Quad(Record):
    """A quad defined by its four corners.

    Usage:
        ```
        Quad(bl: Vec2, tl: Vec2, tr: Vec2, br: Vec2)
        ```
    """

    bl: Vec2
    """The bottom-left corner of the quad."""

    tl: Vec2
    """The top-left corner of the quad."""

    tr: Vec2
    """The top-right corner of the quad."""

    br: Vec2
    """The bottom-right corner of the quad."""

    @property
    def center(self) -> Vec2:
        """The center of the quad."""
        return (self.bl + self.tr + self.tl + self.br) / 4

    def translate(self, translation: Vec2, /) -> Self:
        """Translate the quad by the given translation and return a new quad."""
        return Quad(
            bl=self.bl + translation,
            tl=self.tl + translation,
            tr=self.tr + translation,
            br=self.br + translation,
        )

    def scale(self, factor: Vec2, /) -> Self:
        """Scale the quad by the given factor about the origin and return a new quad."""
        return Quad(
            bl=self.bl * factor,
            tl=self.tl * factor,
            tr=self.tr * factor,
            br=self.br * factor,
        )

    def scale_about(self, factor: Vec2, /, pivot: Vec2) -> Self:
        """Scale the quad by the given factor about the given pivot and return a new quad."""
        return Quad(
            bl=(self.bl - pivot) * factor + pivot,
            tl=(self.tl - pivot) * factor + pivot,
            tr=(self.tr - pivot) * factor + pivot,
            br=(self.br - pivot) * factor + pivot,
        )

    def scale_centered(self, factor: Vec2, /) -> Self:
        """Scale the quad by the given factor about its center and return a new quad."""
        return Quad(
            bl=self.bl * factor,
            tl=self.tl * factor,
            tr=self.tr * factor,
            br=self.br * factor,
        ).translate(self.center * (Vec2(1, 1) - factor))

    def rotate(self, angle: float, /) -> Self:
        """Rotate the quad by the given angle about the origin and return a new quad."""
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
        """Rotate the quad by the given angle about the given pivot and return a new quad."""
        return Quad(
            bl=self.bl.rotate_about(angle, pivot),
            tl=self.tl.rotate_about(angle, pivot),
            tr=self.tr.rotate_about(angle, pivot),
            br=self.br.rotate_about(angle, pivot),
        )

    def rotate_centered(self, angle: float, /) -> Self:
        """Rotate the quad by the given angle about its center and return a new quad."""
        return self.rotate_about(angle, self.center)

    def contains_point(self, point: Vec2, /) -> bool:
        """Check if the quad contains the given point.

        Args:
            point: The point to check.

        Returns:
            True if the point is inside the quad, False otherwise.
        """
        return pnpoly((self.bl, self.tl, self.tr, self.br), point)


class Rect(Record):
    """A rectangle defined by its top, right, bottom, and left edges.

    Usage:
        ```
        Rect(t: float, r: float, b: float, l: float)
        ```
    """

    t: float
    """The top edge of the rectangle."""

    r: float
    """The right edge of the rectangle."""

    b: float
    """The bottom edge of the rectangle."""

    l: float  # noqa: E741
    """The left edge of the rectangle."""

    @classmethod
    def from_center(cls, center: Vec2, dimensions: Vec2) -> Rect:
        """Create a rectangle from its center and dimensions."""
        return cls(
            t=center.y + dimensions.y / 2,
            r=center.x + dimensions.x / 2,
            b=center.y - dimensions.y / 2,
            l=center.x - dimensions.x / 2,
        )

    @property
    def w(self) -> float:
        """The width of the rectangle."""
        return self.r - self.l

    @property
    def h(self) -> float:
        """The height of the rectangle."""
        return self.t - self.b

    @property
    def bl(self) -> Vec2:
        """The bottom-left corner of the rectangle."""
        return Vec2(self.l, self.b)

    @property
    def tl(self) -> Vec2:
        """The top-left corner of the rectangle."""
        return Vec2(self.l, self.t)

    @property
    def tr(self) -> Vec2:
        """The top-right corner of the rectangle."""
        return Vec2(self.r, self.t)

    @property
    def br(self) -> Vec2:
        """The bottom-right corner of the rectangle."""
        return Vec2(self.r, self.b)

    @property
    def center(self) -> Vec2:
        """The center of the rectangle."""
        return Vec2((self.l + self.r) / 2, (self.t + self.b) / 2)

    def as_quad(self) -> Quad:
        """Convert the rectangle to a quad."""
        return Quad(
            bl=self.bl,
            tl=self.tl,
            tr=self.tr,
            br=self.br,
        )

    def translate(self, translation: Vec2, /) -> Self:
        """Translate the rectangle by the given translation and return a new rectangle."""
        return Rect(
            t=self.t + translation.y,
            r=self.r + translation.x,
            b=self.b + translation.y,
            l=self.l + translation.x,
        )

    def scale(self, factor: Vec2, /) -> Self:
        """Scale the rectangle by the given factor about the origin and return a new rectangle."""
        return Rect(
            t=self.t * factor.y,
            r=self.r * factor.x,
            b=self.b * factor.y,
            l=self.l * factor.x,
        )

    def scale_about(self, factor: Vec2, /, pivot: Vec2) -> Self:
        """Scale the rectangle by the given factor about the given pivot and return a new rectangle."""
        return Rect(
            t=(self.t - pivot.y) * factor.y + pivot.y,
            r=(self.r - pivot.x) * factor.x + pivot.x,
            b=(self.b - pivot.y) * factor.y + pivot.y,
            l=(self.l - pivot.x) * factor.x + pivot.x,
        )

    def scale_centered(self, factor: Vec2, /) -> Self:
        """Scale the rectangle by the given factor about its center and return a new rectangle."""
        return Rect(
            t=self.t * factor.y,
            r=self.r * factor.x,
            b=self.b * factor.y,
            l=self.l * factor.x,
        ).translate(self.center * (Vec2(1, 1) - factor))

    def expand(self, expansion: Vec2, /) -> Self:
        """Expand the rectangle by the given amount and return a new rectangle."""
        return Rect(
            t=self.t + expansion.y,
            r=self.r + expansion.x,
            b=self.b - expansion.y,
            l=self.l - expansion.x,
        )

    def shrink(self, shrinkage: Vec2, /) -> Self:
        """Shrink the rectangle by the given amount and return a new rectangle."""
        return Rect(
            t=self.t - shrinkage.y,
            r=self.r - shrinkage.x,
            b=self.b + shrinkage.y,
            l=self.l + shrinkage.x,
        )

    def contains_point(self, point: Vec2, /) -> bool:
        """Check if the rectangle contains the given point.

        Args:
            point: The point to check.

        Returns:
            True if the point is inside the rectangle, False otherwise.
        """
        return self.l <= point.x <= self.r and self.b <= point.y <= self.t


class QuadLike(Protocol):
    """A protocol for types that can be used as quads."""

    @property
    def bl(self) -> Vec2:
        """The bottom-left corner of the quad."""

    @property
    def tl(self) -> Vec2:
        """The top-left corner of the quad."""

    @property
    def tr(self) -> Vec2:
        """The top-right corner of the quad."""

    @property
    def br(self) -> Vec2:
        """The bottom-right corner of the quad."""


def flatten_quad(quad: QuadLike) -> tuple[float, float, float, float, float, float, float, float]:
    bl = quad.bl
    tl = quad.tl
    tr = quad.tr
    br = quad.br
    return (
        bl.x,
        bl.y,
        tl.x,
        tl.y,
        tr.x,
        tr.y,
        br.x,
        br.y,
    )
