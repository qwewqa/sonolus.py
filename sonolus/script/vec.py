from math import atan2, cos, sin
from typing import Self

from sonolus.script.array import Array
from sonolus.script.array_like import ArrayLike
from sonolus.script.num import Num
from sonolus.script.record import Record


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
    def zero(cls) -> Self:
        """Return a vector with x and y set to 0.

        Returns:
            A new vector with x=0 and y=0.
        """
        return cls(x=0, y=0)

    @classmethod
    def one(cls) -> Self:
        """Return a vector with x and y set to 1.

        Returns:
            A new vector with x=1 and y=1.
        """
        return cls(x=1, y=1)

    @classmethod
    def up(cls) -> Self:
        """Return a vector pointing upwards (x=0, y=1).

        Returns:
            A new vector pointing upwards.
        """
        return cls(x=0, y=1)

    @classmethod
    def down(cls) -> Self:
        """Return a vector pointing downwards (x=0, y=-1).

        Returns:
            A new vector pointing downwards.
        """
        return cls(x=0, y=-1)

    @classmethod
    def left(cls) -> Self:
        """Return a vector pointing to the left (x=-1, y=0).

        Returns:
            A new vector pointing to the left.
        """
        return cls(x=-1, y=0)

    @classmethod
    def right(cls) -> Self:
        """Return a vector pointing to the right (x=1, y=0).

        Returns:
            A new vector pointing to the right.
        """
        return cls(x=1, y=0)

    @classmethod
    def unit(cls, angle: Num) -> Self:
        """Return a unit vector (magnitude 1) at a given angle in radians.

        Args:
            angle: The angle in radians.

        Returns:
            A new unit vector at the specified angle.
        """
        return Vec2(x=cos(angle), y=sin(angle))

    @property
    def magnitude(self) -> Num:
        """Calculate the magnitude (length) of the vector.

        Returns:
            The magnitude of the vector.
        """
        return (self.x**2 + self.y**2) ** 0.5

    @property
    def angle(self) -> Num:
        """Calculate the angle of the vector in radians from the positive x-axis.

        Returns:
            The angle of the vector in radians.
        """
        return atan2(self.y, self.x)

    def dot(self, other: Self) -> Num:
        """Calculate the dot product of this vector with another vector.

        Args:
            other: The other vector to calculate the dot product with.

        Returns:
            The dot product of the two vectors.
        """
        return self.x * other.x + self.y * other.y

    def rotate(self, angle: Num) -> Self:
        """Rotate the vector by a given angle in radians and return a new vector.

        Args:
            angle: The angle to rotate the vector by, in radians. Positive angles rotate counterclockwise.

        Returns:
            A new vector rotated by the given angle.
        """
        return Vec2(
            x=self.x * cos(angle) - self.y * sin(angle),
            y=self.x * sin(angle) + self.y * cos(angle),
        )

    def rotate_about(self, angle: Num, pivot: Self) -> Self:
        """Rotate the vector about a pivot by a given angle in radians and return a new vector.

        Args:
            angle: The angle to rotate the vector by, in radians. Positive angles rotate counterclockwise.
            pivot: The pivot point to rotate about.

        Returns:
            A new vector rotated about the pivot by the given angle.
        """
        return (self - pivot).rotate(angle) + pivot

    def normalize(self) -> Self:
        """Normalize the vector (set the magnitude to 1) and return a new vector.

        Returns:
            A new vector with magnitude 1.
        """
        magnitude = self.magnitude
        return Vec2(x=self.x / magnitude, y=self.y / magnitude)

    def orthogonal(self) -> Self:
        """Return a vector orthogonal to this vector.

        The orthogonal vector is rotated 90 degrees counter-clockwise from this vector.

        Returns:
            A new vector orthogonal to this vector.
        """
        return Vec2(x=-self.y, y=self.x)

    @property
    def tuple(self) -> tuple[float, float]:
        """Return the vector as a tuple (x, y).

        Returns:
            A tuple representation of the vector.
        """
        return self.x, self.y

    def __add__(self, other: Self) -> Self:
        """Add this vector to another vector and return a new vector.

        Args:
            other: The vector to add.

        Returns:
            A new vector resulting from the addition.
        """
        return Vec2(x=self.x + other.x, y=self.y + other.y)

    def __sub__(self, other: Self) -> Self:
        """Subtract another vector from this vector and return a new vector.

        Args:
            other: The vector to subtract.

        Returns:
            A new vector resulting from the subtraction.
        """
        return Vec2(x=self.x - other.x, y=self.y - other.y)

    def __mul__(self, other: Self | float) -> Self:
        """Multiply this vector by another vector or a scalar and return a new vector.

        Args:
            other: The vector or scalar to multiply by.

        Returns:
            A new vector resulting from the multiplication.
        """
        match other:
            case Vec2(x, y):
                return Vec2(x=self.x * x, y=self.y * y)
            case Num(factor):
                return Vec2(x=self.x * factor, y=self.y * factor)
            case _:
                return NotImplemented

    def __rmul__(self, other):
        match other:
            case Num(factor):
                return Vec2(x=self.x * factor, y=self.y * factor)
            case _:
                return NotImplemented

    def __truediv__(self, other: Self | float) -> Self:
        """Divide this vector by another vector or a scalar and return a new vector.

        Args:
            other: The vector or scalar to divide by.

        Returns:
            A new vector resulting from the division.
        """
        match other:
            case Vec2(x, y):
                return Vec2(x=self.x / x, y=self.y / y)
            case Num(factor):
                return Vec2(x=self.x / factor, y=self.y / factor)

    def __neg__(self) -> Self:
        """Negate the vector (invert the direction) and return a new vector.

        Returns:
            A new vector with inverted direction.
        """
        return Vec2(x=-self.x, y=-self.y)


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
