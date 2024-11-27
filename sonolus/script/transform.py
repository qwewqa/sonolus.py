from math import cos, sin
from typing import Self

from sonolus.script.quad import Quad, QuadLike
from sonolus.script.record import Record
from sonolus.script.vec import Vec2


class Transform2d(Record):
    """A transformation matrix for 2D points.

    Usage:
        ```
        Transform2d.new()
        ```
    """

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
        """Create a new identity transform.

        Returns:
            A new identity transform.
        """
        return cls(
            1,
            0,
            0,
            0,
            1,
            0,
            0,
            0,
            1,
        )

    def _compose(
        self,
        b00: float,
        b01: float,
        b02: float,
        b10: float,
        b11: float,
        b12: float,
        b20: float,
        b21: float,
        b22: float,
    ) -> Self:
        # Multiply by b on the left (b @ a)
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
            a00,
            a01,
            a02,
            a10,
            a11,
            a12,
            a20,
            a21,
            a22,
        )

    def translate(self, translation: Vec2, /) -> Self:
        """Translate along the x and y axes and return a new transform.

        Args:
            translation: The translation vector.

        Returns:
            A new transform after translation.
        """
        return self._compose(
            1,
            0,
            translation.x,
            0,
            1,
            translation.y,
            0,
            0,
            1,
        )

    def scale(self, factor: Vec2, /) -> Self:
        """Scale about the origin and return a new transform.

        Args:
            factor: The scale factor vector.

        Returns:
            A new transform after scaling.
        """
        return self._compose(
            factor.x,
            0,
            0,
            0,
            factor.y,
            0,
            0,
            0,
            1,
        )

    def scale_about(self, factor: Vec2, /, pivot: Vec2) -> Self:
        """Scale about the pivot and return a new transform.

        Args:
            factor: The scale factor vector.
            pivot: The pivot point for scaling.

        Returns:
            A new transform after scaling.
        """
        return self.translate(-pivot).scale(factor).translate(pivot)

    def rotate(self, angle: float, /) -> Self:
        """Rotate about the origin and return a new transform.

        Args:
            angle: The angle of rotation in radians.

        Returns:
            A new transform after rotation.
        """
        c = cos(angle)
        s = sin(angle)
        return self._compose(
            c,
            -s,
            0,
            s,
            c,
            0,
            0,
            0,
            1,
        )

    def rotate_about(self, angle: float, /, pivot: Vec2) -> Self:
        """Rotate about the pivot and return a new transform.

        Args:
            angle: The angle of rotation in radians.
            pivot: The pivot point for rotation.

        Returns:
            A new transform after rotation.
        """
        return self.translate(-pivot).rotate(angle).translate(pivot)

    def shear_x(self, m: float, /) -> Self:
        """Shear along the x-axis and return a new transform.

        Args:
            m: The shear factor along the x-axis.

        Returns:
            A new transform after shearing.
        """
        return self._compose(
            1,
            m,
            0,
            0,
            1,
            0,
            0,
            0,
            1,
        )

    def shear_y(self, m: float, /) -> Self:
        """Shear along the y-axis and return a new transform.

        Args:
            m: The shear factor along the y-axis.

        Returns:
            A new transform after shearing.
        """
        return self._compose(
            1,
            0,
            0,
            m,
            1,
            0,
            0,
            0,
            1,
        )

    def simple_perspective_x(self, x: float, /) -> Self:
        """Apply perspective along the x-axis with vanishing point at the given x coordinate and return a new transform.

        Args:
            x: The x coordinate of the vanishing point.

        Returns:
            A new transform after applying perspective.
        """
        return self._compose(
            1,
            0,
            0,
            0,
            1,
            0,
            1 / x,
            0,
            1,
        )

    def simple_perspective_y(self, y: float, /) -> Self:
        """Apply perspective along the y-axis with vanishing point at the given y coordinate and return a new transform.

        Args:
            y: The y coordinate of the vanishing point.

        Returns:
            A new transform after applying perspective.
        """
        return self._compose(
            1,
            0,
            0,
            0,
            1,
            0,
            0,
            1 / y,
            1,
        )

    def perspective_x(self, foreground_x: float, vanishing_point: Vec2, /) -> Self:
        """Apply a perspective transformation along the x-axis and return a new transform.

        Args:
            foreground_x: The foreground x-coordinate.
            vanishing_point: The vanishing point vector.

        Returns:
            A new transform after applying perspective.
        """
        return (
            self.simple_perspective_x(vanishing_point.x - foreground_x)
            .shear_y(vanishing_point.y / (vanishing_point.x - foreground_x))
            .translate(Vec2(foreground_x, 0))
        )

    def perspective_y(self, foreground_y: float, vanishing_point: Vec2, /) -> Self:
        """Apply a perspective transformation along the y-axis and return a new transform.

        Args:
            foreground_y: The foreground y-coordinate.
            vanishing_point: The vanishing point vector.

        Returns:
            A new transform after applying perspective.
        """
        return (
            self.simple_perspective_y(vanishing_point.y - foreground_y)
            .shear_x(vanishing_point.x / (vanishing_point.y - foreground_y))
            .translate(Vec2(0, foreground_y))
        )

    def inverse_perspective_x(self, foreground_x: float, vanishing_point: Vec2, /) -> Self:
        """Apply the inverse of a perspective transformation along the x-axis and return a new transform.

        Args:
            foreground_x: The foreground x-coordinate.
            vanishing_point: The vanishing point vector.

        Returns:
            A new transform after applying the inverse perspective.
        """
        return (
            self.translate(Vec2(-foreground_x, 0))
            .shear_y(-vanishing_point.y / (vanishing_point.x - foreground_x))
            .simple_perspective_x(-vanishing_point.x + foreground_x)
        )

    def inverse_perspective_y(self, foreground_y: float, vanishing_point: Vec2, /) -> Self:
        """Apply the inverse of a perspective transformation along the y-axis and return a new transform.

        Args:
            foreground_y: The foreground y-coordinate.
            vanishing_point: The vanishing point vector.

        Returns:
            A new transform after applying the inverse perspective.
        """
        return (
            self.translate(Vec2(0, -foreground_y))
            .shear_x(-vanishing_point.x / (vanishing_point.y - foreground_y))
            .simple_perspective_y(-vanishing_point.y + foreground_y)
        )

    def normalize(self) -> Self:
        """Normalize the transform to have a 1 in the bottom right corner and return a new transform.

        This may fail in some special cases involving perspective transformations where the bottom right corner is 0.

        Returns:
            A new normalized transform.
        """
        return Transform2d(
            self.a00 / self.a22,
            self.a01 / self.a22,
            self.a02 / self.a22,
            self.a10 / self.a22,
            self.a11 / self.a22,
            self.a12 / self.a22,
            self.a20 / self.a22,
            self.a21 / self.a22,
            1,
        )

    def compose(self, other: Self, /) -> Self:
        """Compose with another transform which is applied after this transform and return a new transform.

        Args:
            other: The other transform to compose with.

        Returns:
            A new transform resulting from the composition.
        """
        return self._compose(
            other.a00,
            other.a01,
            other.a02,
            other.a10,
            other.a11,
            other.a12,
            other.a20,
            other.a21,
            other.a22,
        )

    def compose_before(self, other: Self, /) -> Self:
        """Compose with another transform which is applied before this transform and return a new transform.

        Args:
            other: The other transform to compose with.

        Returns:
            A new transform resulting from the composition.
        """
        return other.compose(self)

    def transform_vec(self, v: Vec2) -> Vec2:
        """Transform a Vec2 and return a new Vec2.

        Args:
            v: The vector to transform.

        Returns:
            A new transformed vector.
        """
        x = self.a00 * v.x + self.a01 * v.y + self.a02
        y = self.a10 * v.x + self.a11 * v.y + self.a12
        w = self.a20 * v.x + self.a21 * v.y + self.a22
        return Vec2(x / w, y / w)

    def transform_quad(self, quad: QuadLike) -> Quad:
        """Transform a Quad and return a new Quad.

        Args:
            quad: The quad to transform.

        Returns:
            A new transformed quad.
        """
        return Quad(
            bl=self.transform_vec(quad.bl),
            br=self.transform_vec(quad.br),
            tl=self.transform_vec(quad.tl),
            tr=self.transform_vec(quad.tr),
        )
