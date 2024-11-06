# fmt: off
from typing import Self

from sonolus.script.graphics import Quad
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

    @classmethod
    def new(cls) -> Self:
        return cls(
            1, 0, 0,
            0, 1, 0,
            0, 0,
        )

    def _compose(self,
        b00: float, b01: float, b02: float,
        b10: float, b11: float, b12: float,
        b20: float, b21: float,
    ) -> Self:
        """Multiply the matrix with another matrix on the left."""
        a00 = self.a00 * b00 + self.a10 * b01 + self.a20 * b02
        a01 = self.a01 * b00 + self.a11 * b01 + self.a21 * b02
        a02 = self.a02 * b00 + self.a12 * b01 + b02
        a10 = self.a00 * b10 + self.a10 * b11 + self.a20 * b12
        a11 = self.a01 * b10 + self.a11 * b11 + self.a21 * b12
        a12 = self.a02 * b10 + self.a12 * b11 + b12
        a20 = self.a00 * b20 + self.a10 * b21 + b20
        a21 = self.a01 * b20 + self.a11 * b21 + b21
        a22 = self.a02 * b20 + self.a12 * b21 + 1
        self.a00 = a00 / a22
        self.a01 = a01 / a22
        self.a02 = a02 / a22
        self.a10 = a10 / a22
        self.a11 = a11 / a22
        self.a12 = a12 / a22
        self.a20 = a20 / a22
        self.a21 = a21 / a22
        return self

    def translate(self, x: float = 0, y: float = 0) -> Self:
        return self._compose(
            1, 0, x,
            0, 1, y,
            0, 0,
        )

    def scale(self, x: float = 1, y: float = 1) -> Self:
        return self._compose(
            x, 0, 0,
            0, y, 0,
            0, 0,
        )

    def rotate(self, angle: float) -> Self:
        c = cos(angle)
        s = sin(angle)
        return self._compose(
            c, -s, 0,
            s, c, 0,
            0, 0,
        )

    def shear_x(self, m: float) -> Self:
        """Shear along the x-axis."""
        return self._compose(
            1, m, 0,
            0, 1, 0,
            0, 0,
        )

    def shear_y(self, m: float) -> Self:
        """Shear along the y-axis."""
        return self._compose(
            1, 0, 0,
            m, 1, 0,
            0, 0,
        )

    def perspective_vanish_y(self, y: float) -> Self:
        return self._compose(
            1, 0, 0,
            0, 1, 0,
            0, 1 / y,
        )

    def compose(self, other: Self) -> Self:
        return self._compose(
            other.a00, other.a01, other.a02,
            other.a10, other.a11, other.a12,
            other.a20, other.a21,
        )

    def transform_vec(self, v: Vec2) -> Vec2:
        x = self.a00 * v.x + self.a01 * v.y + self.a02
        y = self.a10 * v.x + self.a11 * v.y + self.a12
        w = self.a20 * v.x + self.a21 * v.y + 1
        return Vec2(x / w, y / w)

    def transform_quad(self, quad: Quad) -> Quad:
        return Quad(
            bl=self.transform_vec(quad.bl),
            br=self.transform_vec(quad.br),
            tl=self.transform_vec(quad.tl),
            tr=self.transform_vec(quad.tr),
        )
