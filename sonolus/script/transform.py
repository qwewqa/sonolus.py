from math import cos, sin
from typing import Self

from sonolus.script.interval import lerp, remap
from sonolus.script.quad import Quad, QuadLike
from sonolus.script.record import Record
from sonolus.script.vec import Vec2


class Transform2d(Record):
    """A transformation matrix for 2D points.

    Usage:
        ```python
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
            angle: The angle of rotation in radians. Positive angles rotate counterclockwise.

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
            angle: The angle of rotation in radians. Positive angles rotate counterclockwise.
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
        """Transform a [`Vec2`][sonolus.script.vec.Vec2] and return a new [`Vec2`][sonolus.script.vec.Vec2].

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
        """Transform a [`Quad`][sonolus.script.quad.Quad] and return a new [`Quad`][sonolus.script.quad.Quad].

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


class InvertibleTransform2d(Record):
    """A transformation matrix for 2D points that can be inverted.

    Usage:
        ```python
        InvertibleTransform2d.new()
        ```
    """

    forward: Transform2d
    inverse: Transform2d

    @classmethod
    def new(cls) -> Self:
        """Create a new identity transform.

        Returns:
            A new identity transform.
        """
        return cls(
            forward=Transform2d.new(),
            inverse=Transform2d.new(),
        )

    def translate(self, translation: Vec2, /) -> Self:
        """Translate along the x and y axes and return a new transform.

        Args:
            translation: The translation vector.

        Returns:
            A new invertible transform after translation.
        """
        return InvertibleTransform2d(
            forward=self.forward.translate(translation),
            inverse=Transform2d.new().translate(-translation).compose(self.inverse),
        )

    def scale(self, factor: Vec2, /) -> Self:
        """Scale about the origin and return a new transform.

        Args:
            factor: The scale factor vector.

        Returns:
            A new invertible transform after scaling.
        """
        return InvertibleTransform2d(
            forward=self.forward.scale(factor),
            inverse=Transform2d.new().scale(Vec2.one() / factor).compose(self.inverse),
        )

    def scale_about(self, factor: Vec2, /, pivot: Vec2) -> Self:
        """Scale about the pivot and return a new transform.

        Args:
            factor: The scale factor vector.
            pivot: The pivot point for scaling.

        Returns:
            A new invertible transform after scaling.
        """
        return InvertibleTransform2d(
            forward=self.forward.scale_about(factor, pivot),
            inverse=Transform2d.new().scale_about(Vec2.one() / factor, pivot).compose(self.inverse),
        )

    def rotate(self, angle: float, /) -> Self:
        """Rotate about the origin and return a new transform.

        Args:
            angle: The angle of rotation in radians. Positive angles rotate counterclockwise.

        Returns:
            A new invertible transform after rotation.
        """
        return InvertibleTransform2d(
            forward=self.forward.rotate(angle),
            inverse=Transform2d.new().rotate(-angle).compose(self.inverse),
        )

    def rotate_about(self, angle: float, /, pivot: Vec2) -> Self:
        """Rotate about the pivot and return a new transform.

        Args:
            angle: The angle of rotation in radians. Positive angles rotate counterclockwise.
            pivot: The pivot point for rotation.

        Returns:
            A new invertible transform after rotation.
        """
        return InvertibleTransform2d(
            forward=self.forward.rotate_about(angle, pivot),
            inverse=Transform2d.new().rotate_about(-angle, pivot).compose(self.inverse),
        )

    def shear_x(self, m: float, /) -> Self:
        """Shear along the x-axis and return a new transform.

        Args:
            m: The shear factor along the x-axis.

        Returns:
            A new invertible transform after shearing.
        """
        return InvertibleTransform2d(
            forward=self.forward.shear_x(m),
            inverse=Transform2d.new().shear_x(-m).compose(self.inverse),
        )

    def shear_y(self, m: float, /) -> Self:
        """Shear along the y-axis and return a new transform.

        Args:
            m: The shear factor along the y-axis.

        Returns:
            A new invertible transform after shearing.
        """
        return InvertibleTransform2d(
            forward=self.forward.shear_y(m),
            inverse=Transform2d.new().shear_y(-m).compose(self.inverse),
        )

    def simple_perspective_x(self, x: float, /) -> Self:
        """Apply perspective along the x-axis with vanishing point at the given x coordinate and return a new transform.

        Args:
            x: The x coordinate of the vanishing point.

        Returns:
            A new invertible transform after applying perspective.
        """
        return InvertibleTransform2d(
            forward=self.forward.simple_perspective_x(x),
            inverse=Transform2d.new().simple_perspective_x(-x).compose(self.inverse),
        )

    def simple_perspective_y(self, y: float, /) -> Self:
        """Apply perspective along the y-axis with vanishing point at the given y coordinate and return a new transform.

        Args:
            y: The y coordinate of the vanishing point.

        Returns:
            A new invertible transform after applying perspective.
        """
        return InvertibleTransform2d(
            forward=self.forward.simple_perspective_y(y),
            inverse=Transform2d.new().simple_perspective_y(-y).compose(self.inverse),
        )

    def perspective_x(self, foreground_x: float, vanishing_point: Vec2, /) -> Self:
        """Apply a perspective transformation along the x-axis and return a new transform.

        Args:
            foreground_x: The foreground x-coordinate.
            vanishing_point: The vanishing point vector.

        Returns:
            A new invertible transform after applying perspective.
        """
        return InvertibleTransform2d(
            forward=self.forward.perspective_x(foreground_x, vanishing_point),
            inverse=Transform2d.new().inverse_perspective_x(foreground_x, vanishing_point).compose(self.inverse),
        )

    def perspective_y(self, foreground_y: float, vanishing_point: Vec2, /) -> Self:
        """Apply a perspective transformation along the y-axis and return a new transform.

        Args:
            foreground_y: The foreground y-coordinate.
            vanishing_point: The vanishing point vector.

        Returns:
            A new invertible transform after applying perspective.
        """
        return InvertibleTransform2d(
            forward=self.forward.perspective_y(foreground_y, vanishing_point),
            inverse=Transform2d.new().inverse_perspective_y(foreground_y, vanishing_point).compose(self.inverse),
        )

    def normalize(self) -> Self:
        """Normalize the transform to have a 1 in the bottom right corner and return a new transform.

        This may fail in some special cases involving perspective transformations where the bottom right corner is 0.

        Returns:
            A new normalized invertible transform.
        """
        return InvertibleTransform2d(
            forward=self.forward.normalize(),
            inverse=self.inverse.normalize(),
        )

    def compose(self, other: Self, /) -> Self:
        """Compose with another invertible transform which is applied after this transform and return a new transform.

        Args:
            other: The other invertible transform to compose with.

        Returns:
            A new invertible transform resulting from the composition.
        """
        return InvertibleTransform2d(
            forward=self.forward.compose(other.forward),
            inverse=other.inverse.compose(self.inverse),
        )

    def compose_before(self, other: Self, /) -> Self:
        """Compose with another invertible transform which is applied before this transform and return a new transform.

        Args:
            other: The other invertible transform to compose with.

        Returns:
            A new invertible transform resulting from the composition.
        """
        return other.compose(self)

    def transform_vec(self, v: Vec2) -> Vec2:
        """Transform a [`Vec2`][sonolus.script.vec.Vec2] and return a new [`Vec2`][sonolus.script.vec.Vec2].

        Args:
            v: The vector to transform.

        Returns:
            A new transformed vector.
        """
        return self.forward.transform_vec(v)

    def inverse_transform_vec(self, v: Vec2) -> Vec2:
        """Inverse transform a [`Vec2`][sonolus.script.vec.Vec2] and return a new [`Vec2`][sonolus.script.vec.Vec2].

        Args:
            v: The vector to inverse transform.

        Returns:
            A new inverse transformed vector.
        """
        return self.inverse.transform_vec(v)

    def transform_quad(self, quad: QuadLike) -> Quad:
        """Transform a [`Quad`][sonolus.script.quad.Quad] and return a new [`Quad`][sonolus.script.quad.Quad].

        Args:
            quad: The quad to transform.

        Returns:
            A new transformed quad.
        """
        return self.forward.transform_quad(quad)

    def inverse_transform_quad(self, quad: QuadLike) -> Quad:
        """Inverse transform a [`Quad`][sonolus.script.quad.Quad] and return a new [`Quad`][sonolus.script.quad.Quad].

        Args:
            quad: The quad to inverse transform.

        Returns:
            A new inverse transformed quad.
        """
        return self.inverse.transform_quad(quad)


def perspective_approach(
    distance_ratio: float,
    progress: float,
) -> float:
    """Calculate the perspective correct approach curve given the initial distance, target distance, and progress.

    For typical engines with stage tilt, distance_ratio is the displayed width of a lane at the judge line divided
    by the displayed width of a lane at note spawn. For flat stages, this will be 1.0, and this function would simply
    return progress unchanged.

    Args:
        distance_ratio: The ratio of the distance at note spawn to the distance at the judge line.
        progress: The progress value, where 0 corresponds to note spawn and 1 corresponds to the judge line.

    Returns:
        The perspective-corrected progress value.
    """
    d_0 = distance_ratio
    d_1 = 1.0
    d = max(lerp(d_0, d_1, progress), 1e-6)  # Avoid a zero or negative distance
    return remap(1 / d_0, 1 / d_1, 0, 1, 1 / d)
