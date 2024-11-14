# fmt: off
from typing import Self

from sonolus.script.graphics import Quad, QuadLike
from sonolus.script.math import cos, sin
from sonolus.script.record import Record
from sonolus.script.vec import Vec2


class Transform2d(Record):
    a00: float
    a01: float
    a02: float
    a10: float
    a11: float
    a12: float
    a20: float
    a21: float
    a22: float

    @classmethod
    def new(cls) -> Self:
        """Create a new identity transform."""
        return cls(
            1, 0, 0,
            0, 1, 0,
            0, 0, 1,
        )

    def _compose(self,
        b00: float, b01: float, b02: float,
        b10: float, b11: float, b12: float,
        b20: float, b21: float, b22: float,
    ) -> Self:
        """Multiply the matrix with another matrix on the left."""
        a00 = self.a00 * b00 + self.a10 * b01 + self.a20 * b02
        a01 = self.a01 * b00 + self.a11 * b01 + self.a21 * b02
        a02 = self.a02 * b00 + self.a12 * b01 + self.a22 * b02
        a10 = self.a00 * b10 + self.a10 * b11 + self.a20 * b12
        a11 = self.a01 * b10 + self.a11 * b11 + self.a21 * b12
        a12 = self.a02 * b10 + self.a12 * b11 + self.a22 * b12
        a20 = self.a00 * b20 + self.a10 * b21 + self.a20 * b22
        a21 = self.a01 * b20 + self.a11 * b21 + self.a21 * b22
        a22 = self.a02 * b20 + self.a12 * b21 + self.a22 * b22
        return Transform2d(
            a00, a01, a02,
            a10, a11, a12,
            a20, a21, a22,
        )

    def translate(self, translation: Vec2, /) -> Self:
        """Translate along the x and y axes."""
        return self._compose(
            1, 0, translation.x,
            0, 1, translation.y,
            0, 0, 1,
        )

    def scale(self, factor: Vec2, /) -> Self:
        """Scale around the origin."""
        return self._compose(
            factor.x, 0, 0,
            0, factor.y, 0,
            0, 0, 1,
        )

    def scale_about(self, factor: Vec2, /, pivot: Vec2) -> Self:
        """Scale around the pivot."""
        return self.translate(-pivot).scale(factor).translate(pivot)

    def rotate(self, angle: float, /) -> Self:
        """Rotate around the origin."""
        c = cos(angle)
        s = sin(angle)
        return self._compose(
            c, -s, 0,
            s, c, 0,
            0, 0, 1,
        )

    def rotate_about(self, angle: float, /, pivot: Vec2) -> Self:
        """Rotate around the pivot."""
        return self.translate(-pivot).rotate(angle).translate(pivot)

    def shear_x(self, m: float, /) -> Self:
        """Shear along the x-axis."""
        return self._compose(
            1, m, 0,
            0, 1, 0,
            0, 0, 1,
        )

    def shear_y(self, m: float, /) -> Self:
        """Shear along the y-axis."""
        return self._compose(
            1, 0, 0,
            m, 1, 0,
            0, 0, 1,
        )

    def perspective_vanish_y(self, y: float, /) -> Self:
        """Apply perspective vanish along the y-axis with vanish point at the given y coordinate.

        Note: Conveniently, the inverse can be obtained simply by negating the argument.
        """
        return self._compose(
            1, 0, 0,
            0, 1, 0,
            0, 1 / y, 1,
        )

    def perspective(self, foreground_y, vanishing_point: Vec2, /) -> Self:
        """Apply a perspective transformation.

        When the original y is 0, the transformed x coordinate will be unchanged and the y coordinates will
        be equal to foreground_y.
        As the original y coordinate approaches infinity in the direction of the vanishing point,
        the transformed x and y coordinates will approach the vanishing point.
        """
        return (
            self
            .perspective_vanish_y(vanishing_point.y - foreground_y)
            .shear_x(vanishing_point.x / (vanishing_point.y - foreground_y))
            .translate(Vec2(0, foreground_y))
        )

    def inverse_perspective(self, foreground_y, vanishing_point: Vec2, /) -> Self:
        """Apply the inverse of a perspective transformation."""
        return (
            self
            .translate(Vec2(0, -foreground_y))
            .shear_x(-vanishing_point.x / (vanishing_point.y - foreground_y))
            .perspective_vanish_y(-vanishing_point.y + foreground_y)
        )

    def normalize(self):
        """Normalize the transform to have a 1 in the bottom right corner."""
        return Transform2d(
            self.a00 / self.a22, self.a01 / self.a22, self.a02 / self.a22,
            self.a10 / self.a22, self.a11 / self.a22, self.a12 / self.a22,
            self.a20 / self.a22, self.a21 / self.a22, 1,
        )

    def compose(self, other: Self, /) -> Self:
        """Compose with another transform which is applied after this transform."""
        return self._compose(
            other.a00, other.a01, other.a02,
            other.a10, other.a11, other.a12,
            other.a20, other.a21, other.a22,
        )

    def compose_before(self, other: Self, /) -> Self:
        """Compose with another transform which is applied before this transform."""
        return other.compose(self)

    def transform_vec(self, v: Vec2) -> Vec2:
        x = self.a00 * v.x + self.a01 * v.y + self.a02
        y = self.a10 * v.x + self.a11 * v.y + self.a12
        w = self.a20 * v.x + self.a21 * v.y + self.a22
        return Vec2(x / w, y / w)

    def transform_quad(self, quad: QuadLike) -> Quad:
        return Quad(
            bl=self.transform_vec(quad.bl),
            br=self.transform_vec(quad.br),
            tl=self.transform_vec(quad.tl),
            tr=self.transform_vec(quad.tr),
        )
