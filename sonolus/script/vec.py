from __future__ import annotations

from sonolus.script.array import Array
from sonolus.script.array_like import ArrayLike
from sonolus.script.internal.impl import perf_meta_fn
from sonolus.script.internal.math_impls import _atan2, _cos, _sin
from sonolus.script.num import Num
from sonolus.script.record import Record

atan2 = _atan2
sin = _sin
cos = _cos


class Vec2(Record):
    """A 2D vector.

    Usage:
        ```python
        Vec2(x: float, y: float)
        ```
    """

    x: float
    y: float

    @classmethod
    def zero(cls) -> Vec2:
        """Return a vector with x and y set to 0.

        Returns:
            A new vector with x=0 and y=0.
        """
        return cls(x=0, y=0)

    @classmethod
    def one(cls) -> Vec2:
        """Return a vector with x and y set to 1.

        Returns:
            A new vector with x=1 and y=1.
        """
        return cls(x=1, y=1)

    @classmethod
    def up(cls) -> Vec2:
        """Return a vector pointing upwards (x=0, y=1).

        Returns:
            A new vector pointing upwards.
        """
        return cls(x=0, y=1)

    @classmethod
    def down(cls) -> Vec2:
        """Return a vector pointing downwards (x=0, y=-1).

        Returns:
            A new vector pointing downwards.
        """
        return cls(x=0, y=-1)

    @classmethod
    def left(cls) -> Vec2:
        """Return a vector pointing to the left (x=-1, y=0).

        Returns:
            A new vector pointing to the left.
        """
        return cls(x=-1, y=0)

    @classmethod
    def right(cls) -> Vec2:
        """Return a vector pointing to the right (x=1, y=0).

        Returns:
            A new vector pointing to the right.
        """
        return cls(x=1, y=0)

    @classmethod
    def unit(cls, angle: float) -> Vec2:
        """Return a unit vector (magnitude 1) at a given angle in radians.

        Args:
            angle: The angle in radians.

        Returns:
            A new unit vector at the specified angle.
        """
        return Vec2(x=cos(angle), y=sin(angle))

    @property
    @perf_meta_fn
    def magnitude(self) -> float:
        """Calculate the magnitude (length) of the vector.

        Returns:
            The magnitude of the vector.
        """
        return (self.x**2 + self.y**2) ** 0.5

    @property
    @perf_meta_fn
    def angle(self) -> float:
        """Calculate the angle of the vector in radians from the positive x-axis.

        Returns:
            The angle of the vector in radians.
        """
        return atan2(self.y, self.x)

    @perf_meta_fn
    def dot(self, other: Vec2) -> float:
        """Calculate the dot product of this vector with another vector.

        Args:
            other: The other vector to calculate the dot product with.

        Returns:
            The dot product of the two vectors.
        """
        return self.x * other.x + self.y * other.y

    @perf_meta_fn
    def rotate(self, angle: float) -> Vec2:
        """Rotate the vector by a given angle in radians and return a new vector.

        Args:
            angle: The angle to rotate the vector by, in radians. Positive angles rotate counterclockwise.

        Returns:
            A new vector rotated by the given angle.
        """
        return Vec2._quick_construct(
            x=self.x * cos(angle) - self.y * sin(angle),
            y=self.x * sin(angle) + self.y * cos(angle),
        )

    @perf_meta_fn
    def rotate_about(self, angle: float, pivot: Vec2) -> Vec2:
        """Rotate the vector about a pivot by a given angle in radians and return a new vector.

        Args:
            angle: The angle to rotate the vector by, in radians. Positive angles rotate counterclockwise.
            pivot: The pivot point to rotate about.

        Returns:
            A new vector rotated about the pivot by the given angle.
        """
        return (self - pivot).rotate(angle) + pivot

    @perf_meta_fn
    def normalize(self) -> Vec2:
        """Normalize the vector (set the magnitude to 1) and return a new vector.

        Returns:
            A new vector with magnitude 1.
        """
        magnitude = (self.x**2 + self.y**2) ** 0.5
        assert magnitude != 0, "Cannot normalize a zero vector"
        return Vec2._quick_construct(x=self.x / magnitude, y=self.y / magnitude)

    @perf_meta_fn
    def orthogonal(self) -> Vec2:
        """Return a vector orthogonal to this vector.

        The orthogonal vector is rotated 90 degrees counter-clockwise from this vector.

        Returns:
            A new vector orthogonal to this vector.
        """
        return Vec2._quick_construct(x=-self.y, y=self.x)

    @property
    def tuple(self) -> tuple[float, float]:
        """Return the vector as a tuple (x, y).

        Returns:
            A tuple representation of the vector.
        """
        return self.x, self.y

    @perf_meta_fn
    def __add__(self, other: Vec2) -> Vec2:
        """Add this vector to another vector and return a new vector.

        Args:
            other: The vector to add.

        Returns:
            A new vector resulting from the addition.
        """
        return Vec2._quick_construct(x=self.x + other.x, y=self.y + other.y)

    @perf_meta_fn
    def __sub__(self, other: Vec2) -> Vec2:
        """Subtract another vector from this vector and return a new vector.

        Args:
            other: The vector to subtract.

        Returns:
            A new vector resulting from the subtraction.
        """
        return Vec2._quick_construct(x=self.x - other.x, y=self.y - other.y)

    @perf_meta_fn
    def __mul__(self, other: Vec2 | float) -> Vec2:
        """Multiply this vector by another vector or a scalar and return a new vector.

        Args:
            other: The vector or scalar to multiply by.

        Returns:
            A new vector resulting from the multiplication.
        """
        match other:
            case Vec2(x, y):
                return Vec2._quick_construct(x=self.x * x, y=self.y * y)
            case Num(factor):
                return Vec2._quick_construct(x=self.x * factor, y=self.y * factor)
            case _:
                return NotImplemented

    @perf_meta_fn
    def __rmul__(self, other):
        match other:
            case Num(factor):
                return Vec2._quick_construct(x=self.x * factor, y=self.y * factor)
            case _:
                return NotImplemented

    @perf_meta_fn
    def __truediv__(self, other: Vec2 | float) -> Vec2:
        """Divide this vector by another vector or a scalar and return a new vector.

        Args:
            other: The vector or scalar to divide by.

        Returns:
            A new vector resulting from the division.
        """
        match other:
            case Vec2(x, y):
                return Vec2._quick_construct(x=self.x / x, y=self.y / y)
            case Num(factor):
                return Vec2._quick_construct(x=self.x / factor, y=self.y / factor)
            case _:
                return NotImplemented

    @perf_meta_fn
    def __neg__(self) -> Vec2:
        """Negate the vector (invert the direction) and return a new vector.

        Returns:
            A new vector with inverted direction.
        """
        return Vec2._quick_construct(x=-self.x, y=-self.y)


def pnpoly(vertices: ArrayLike[Vec2] | tuple[Vec2, ...], test: Vec2) -> bool:
    """Check if a point is inside a polygon.

    No guaranteed behavior for points on the edges or very close to the edges.

    Args:
        vertices: The vertices of the polygon.
        test: The point to test.

    Returns:
        Whether the point is inside the polygon.
    """
    if isinstance(vertices, tuple):
        vertices = Array(*vertices)
    i = 0
    j = len(vertices) - 1
    c = False
    while i < len(vertices):
        if (vertices[i].y > test.y) != (vertices[j].y > test.y) and test.x < (vertices[j].x - vertices[i].x) * (
            test.y - vertices[i].y
        ) / (vertices[j].y - vertices[i].y) + vertices[i].x:
            c = not c
        j = i
        i += 1
    return c
