from __future__ import annotations

from typing import Protocol, Self, assert_never, overload

from sonolus.script.internal.impl import perf_meta_fn
from sonolus.script.record import Record
from sonolus.script.values import zeros
from sonolus.script.vec import Vec2, pnpoly


class Quad(Record):
    """A quad defined by its four corners.

    Usage:
        ```python
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

    @classmethod
    def zero(cls) -> Quad:
        """Return a quad with all corners set to (0, 0).

        Returns:
            A new quad with all corners at the origin.
        """
        return cls(
            bl=Vec2.zero(),
            tl=Vec2.zero(),
            tr=Vec2.zero(),
            br=Vec2.zero(),
        )

    @classmethod
    def from_quad(cls, value: QuadLike, /) -> Quad:
        """Create a quad from a quad-like value."""
        return cls(
            bl=value.bl,
            tl=value.tl,
            tr=value.tr,
            br=value.br,
        )

    @property
    def center(self) -> Vec2:
        """The center of the quad."""
        return (self.bl + self.tr + self.tl + self.br) / 4

    @property
    def mt(self) -> Vec2:
        """The midpoint of the top edge of the quad."""
        return (self.tl + self.tr) / 2

    @property
    def mr(self) -> Vec2:
        """The midpoint of the right edge of the quad."""
        return (self.tr + self.br) / 2

    @property
    def mb(self) -> Vec2:
        """The midpoint of the bottom edge of the quad."""
        return (self.bl + self.br) / 2

    @property
    def ml(self) -> Vec2:
        """The midpoint of the left edge of the quad."""
        return (self.bl + self.tl) / 2

    def translate(self, translation: Vec2, /) -> Quad:
        """Translate the quad by the given translation and return a new quad."""
        return Quad(
            bl=self.bl + translation,
            tl=self.tl + translation,
            tr=self.tr + translation,
            br=self.br + translation,
        )

    def scale(self, factor: Vec2, /) -> Quad:
        """Scale the quad by the given factor about the origin and return a new quad."""
        return Quad(
            bl=self.bl * factor,
            tl=self.tl * factor,
            tr=self.tr * factor,
            br=self.br * factor,
        )

    def scale_about(self, factor: Vec2, /, pivot: Vec2) -> Quad:
        """Scale the quad by the given factor about the given pivot and return a new quad."""
        return Quad(
            bl=(self.bl - pivot) * factor + pivot,
            tl=(self.tl - pivot) * factor + pivot,
            tr=(self.tr - pivot) * factor + pivot,
            br=(self.br - pivot) * factor + pivot,
        )

    def scale_centered(self, factor: Vec2, /) -> Quad:
        """Scale the quad by the given factor about its center and return a new quad."""
        return Quad(
            bl=self.bl * factor,
            tl=self.tl * factor,
            tr=self.tr * factor,
            br=self.br * factor,
        ).translate(self.center * (Vec2(1, 1) - factor))

    def rotate(self, angle: float, /) -> Quad:
        """Rotate the quad by the given angle about the origin and return a new quad.

        Args:
            angle: The angle of rotation in radians. Positive angles rotate counterclockwise.

        Returns:
            A new quad rotated by the given angle.
        """
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
    ) -> Quad:
        """Rotate the quad by the given angle about the given pivot and return a new quad.

        Args:
            angle: The angle of rotation in radians. Positive angles rotate counterclockwise.
            pivot: The pivot point for rotation.

        Returns:
            A new quad rotated about the pivot by the given angle.
        """
        return Quad(
            bl=self.bl.rotate_about(angle, pivot),
            tl=self.tl.rotate_about(angle, pivot),
            tr=self.tr.rotate_about(angle, pivot),
            br=self.br.rotate_about(angle, pivot),
        )

    def rotate_centered(self, angle: float, /) -> Quad:
        """Rotate the quad by the given angle about its center and return a new quad.

        Args:
            angle: The angle of rotation in radians. Positive angles rotate counterclockwise.

        Returns:
            A new quad rotated about its center by the given angle.
        """
        return self.rotate_about(angle, self.center)

    def permute(self, count: int = 1, /) -> Quad:
        """Perform a cyclic permutation of the quad's vertices and return a new quad.

        On a square, this operation is equivalent to rotating the square counterclockwise 90 degrees `count` times.

        Negative values of `count` are allowed and will rotate the quad clockwise.

        Args:
            count: The number of vertices to shift. Defaults to 1.

        Returns:
            The permuted quad.
        """
        count = int(count % 4)
        result = zeros(Quad)
        match count:
            case 0:
                result.bl @= self.bl
                result.tl @= self.tl
                result.tr @= self.tr
                result.br @= self.br
            case 1:
                result.bl @= self.br
                result.tl @= self.bl
                result.tr @= self.tl
                result.br @= self.tr
            case 2:
                result.bl @= self.tr
                result.tl @= self.br
                result.tr @= self.bl
                result.br @= self.tl
            case 3:
                result.bl @= self.tl
                result.tl @= self.tr
                result.tr @= self.br
                result.br @= self.bl
        return result

    def contains_point(self, point: Vec2, /) -> bool:
        """Check if the quad contains the given point.

        It is not guaranteed whether points on the edges of the quad are considered inside or outside.

        Args:
            point: The point to check.

        Returns:
            True if the point is inside the quad, False otherwise.
        """
        return pnpoly((self.bl, self.tl, self.tr, self.br), point)


class Rect(Record):
    """A rectangle defined by its top, right, bottom, and left edges.

    Usage:
        ```python
        Rect(t: float, r: float, b: float, l: float)
        ```
    """

    t: float
    """The top edge of the rectangle."""

    r: float
    """The right edge of the rectangle."""

    b: float
    """The bottom edge of the rectangle."""

    l: float
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

    @overload
    @classmethod
    def from_margin(cls, trbl: float, /) -> Rect: ...

    @overload
    @classmethod
    def from_margin(cls, tb: float, lr: float, /) -> Rect: ...

    @overload
    @classmethod
    def from_margin(cls, t: float, lr: float, b: float, /) -> Rect: ...

    @overload
    @classmethod
    def from_margin(cls, t: float, r: float, b: float, l: float, /) -> Rect: ...

    @classmethod
    def from_margin(cls, a: float, b: float | None = None, c: float | None = None, d: float | None = None, /) -> Self:
        """Create a rectangle based on margins (edge distances) from the origin.

        Compared to the regular [`Rect`][sonolus.script.quad.Rect] constructor, this method negates the bottom and
        left values, and supports shorthands when fewer than four arguments are provided.

        The following signatures are supported:

        - `from_margin(trbl)`: All margins set to `trbl`.
        - `from_margin(tb, lr)`: Top and bottom margins set to `tb`, left and right margins set to `lr`.
        - `from_margin(t, lr, b)`: Top margin set to `t`, left and right margins set to `lr`, bottom margin set to `b`.
        - `from_margin(t, r, b, l)`: Top, right, bottom, and left margins set to `t`, `r`, `b`, and `l` respectively.

        Usage:
            ```python
            Rect.from_margin(1)  # Rect(t=1, r=1, b=-1, l=-1)
            Rect.from_margin(1, 2)  # Rect(t=1, r=2, b=-1, l=-2)
            Rect.from_margin(1, 2, 3)  # Rect(t=1, r=2, b=-3, l=-2)
            Rect.from_margin(1, 2, 3, 4)  # Rect(t=1, r=2, b=-3, l=-4)
            ```
        """
        args = (a, b, c, d)
        match args:
            case (a, None, None, None):
                return cls(t=a, r=a, b=-a, l=-a)
            case (a, b, None, None):
                return cls(t=a, r=b, b=-a, l=-b)
            case (a, b, c, None):
                return cls(t=a, r=b, b=-c, l=-b)
            case (a, b, c, d):
                return cls(t=a, r=b, b=-c, l=-d)
            case _:
                assert_never(args)

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

    @property
    def mt(self) -> Vec2:
        """The middle-top point of the rectangle."""
        return Vec2((self.l + self.r) / 2, self.t)

    @property
    def mr(self) -> Vec2:
        """The middle-right point of the rectangle."""
        return Vec2(self.r, (self.t + self.b) / 2)

    @property
    def mb(self) -> Vec2:
        """The middle-bottom point of the rectangle."""
        return Vec2((self.l + self.r) / 2, self.b)

    @property
    def ml(self) -> Vec2:
        """The middle-left point of the rectangle."""
        return Vec2(self.l, (self.t + self.b) / 2)

    def as_quad(self) -> Quad:
        """Convert the rectangle to a [`Quad`][sonolus.script.quad.Quad]."""
        return Quad(
            bl=self.bl,
            tl=self.tl,
            tr=self.tr,
            br=self.br,
        )

    def translate(self, translation: Vec2, /) -> Rect:
        """Translate the rectangle by the given translation and return a new rectangle."""
        return Rect(
            t=self.t + translation.y,
            r=self.r + translation.x,
            b=self.b + translation.y,
            l=self.l + translation.x,
        )

    def scale(self, factor: Vec2, /) -> Rect:
        """Scale the rectangle by the given factor about the origin and return a new rectangle."""
        return Rect(
            t=self.t * factor.y,
            r=self.r * factor.x,
            b=self.b * factor.y,
            l=self.l * factor.x,
        )

    def scale_about(self, factor: Vec2, /, pivot: Vec2) -> Rect:
        """Scale the rectangle by the given factor about the given pivot and return a new rectangle."""
        return Rect(
            t=(self.t - pivot.y) * factor.y + pivot.y,
            r=(self.r - pivot.x) * factor.x + pivot.x,
            b=(self.b - pivot.y) * factor.y + pivot.y,
            l=(self.l - pivot.x) * factor.x + pivot.x,
        )

    def scale_centered(self, factor: Vec2, /) -> Rect:
        """Scale the rectangle by the given factor about its center and return a new rectangle."""
        return Rect(
            t=self.t * factor.y,
            r=self.r * factor.x,
            b=self.b * factor.y,
            l=self.l * factor.x,
        ).translate(self.center * (Vec2(1, 1) - factor))

    def expand(self, expansion: Vec2, /) -> Rect:
        """Expand the rectangle by the given amount and return a new rectangle."""
        return Rect(
            t=self.t + expansion.y,
            r=self.r + expansion.x,
            b=self.b - expansion.y,
            l=self.l - expansion.x,
        )

    def shrink(self, shrinkage: Vec2, /) -> Rect:
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


class _QuadLike(Protocol):
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


# PyCharm doesn't recognize attributes as satisfying the protocol.
type QuadLike = _QuadLike | Quad
"""A type that can be used as a quad."""


@perf_meta_fn
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
