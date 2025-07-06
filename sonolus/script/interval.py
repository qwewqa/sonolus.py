from typing import Self

from sonolus.backend.ops import Op
from sonolus.script.array_like import ArrayLike
from sonolus.script.debug import static_error
from sonolus.script.internal.native import native_function
from sonolus.script.internal.range import range_or_tuple
from sonolus.script.num import Num
from sonolus.script.record import Record


class Interval(Record):
    """A closed interval.

    Usage:
        ```python
        Interval(start: float, end: float)
        ```
    """

    start: float
    end: float

    @classmethod
    def zero(cls) -> Self:
        """Get an empty interval."""
        return cls(0, 0)

    @property
    def length(self) -> float:
        """The length of the interval.

        May be negative if the end is less than the start.
        """
        return self.end - self.start

    @property
    def is_empty(self) -> bool:
        """Whether the has a start greater than its end."""
        return self.start > self.end

    @property
    def mid(self) -> float:
        """The midpoint of the interval."""
        return (self.start + self.end) / 2

    @property
    def tuple(self):
        """The interval as a tuple."""
        return self.start, self.end

    def __contains__(self, item: Self | float | int) -> bool:
        """Check if an item is within the interval.

        Args:
            item: The item to check. If it is an interval, it must be fully contained within this interval.

        Returns:
            True if the item is within the interval, False otherwise.
        """
        match item:
            case Interval(start, end):
                return self.start <= start and end <= self.end
            case Num(value):
                return self.start <= value <= self.end
            case _:
                static_error("Invalid type for interval check")

    def __add__(self, other: float | int) -> Self:
        """Add a value to both ends of the interval.

        Args:
            other: The value to add.

        Returns:
            A new interval with the value added to both ends.
        """
        return Interval(self.start + other, self.end + other)

    def __sub__(self, other: float | int) -> Self:
        """Subtract a value from both ends of the interval.

        Args:
            other: The value to subtract.

        Returns:
            A new interval with the value subtracted from both ends.
        """
        return Interval(self.start - other, self.end - other)

    def __mul__(self, other: float | int) -> Self:
        """Multiply both ends of the interval by a value.

        Args:
            other: The value to multiply by.

        Returns:
            A new interval with both ends multiplied by the value.
        """
        return Interval(self.start * other, self.end * other)

    def __truediv__(self, other: float | int) -> Self:
        """Divide both ends of the interval by a value.

        Args:
            other: The value to divide by.

        Returns:
            A new interval with both ends divided by the value.
        """
        return Interval(self.start / other, self.end / other)

    def __floordiv__(self, other: float | int) -> Self:
        """Divide both ends of the interval by a value and floor the result.

        Args:
            other: The value to divide by.

        Returns:
            A new interval with both ends divided by the value and floored.
        """
        return Interval(self.start // other, self.end // other)

    def __and__(self, other: Self) -> Self:
        """Get the intersection of two intervals.

        The resulting interval will be empty and may have a negative length if the two intervals do not overlap.

        Args:
            other: The other interval.

        Returns:
            A new interval representing the intersection of the two intervals.
        """
        return Interval(max(self.start, other.start), min(self.end, other.end))

    def shrink(self, value: float | int) -> Self:
        """Shrink the interval by a value on both ends.

        Args:
            value: The value to shrink by.

        Returns:
            A new interval with the value subtracted from the start and added to the end.
        """
        return Interval(self.start + value, self.end - value)

    def expand(self, value: float | int) -> Self:
        """Expand the interval by a value on both ends.

        Args:
            value: The value to expand by.

        Returns:
            A new interval with the value subtracted from the start and added to the end.
        """
        return Interval(self.start - value, self.end + value)

    def lerp(self, x: float, /) -> float:
        """Linearly interpolate a value within the interval.

        Args:
            x: The interpolation factor.

        Returns:
            The interpolated value.
        """
        return lerp(self.start, self.end, x)

    def lerp_clamped(self, x: float, /) -> float:
        """Linearly interpolate a value within the interval, clamped to the interval.

        Args:
            x: The interpolation factor.

        Returns:
            The interpolated value.
        """
        return lerp_clamped(self.start, self.end, x)

    def unlerp(self, x: float, /) -> float:
        """Inverse linear interpolation of a value within the interval.

        Args:
            x: The value to unlerp.

        Returns:
            The unlerped value.
        """
        return unlerp(self.start, self.end, x)

    def unlerp_clamped(self, x: float, /) -> float:
        """Inverse linear interpolation of a value within the interval, clamped to the interval.

        Args:
            x: The value to unlerp.

        Returns:
            The unlerped value.
        """
        return unlerp_clamped(self.start, self.end, x)

    def clamp(self, x: float, /) -> float:
        """Clamp a value to the interval.

        Args:
            x: The value to clamp.

        Returns:
            The clamped value.
        """
        return clamp(x, self.start, self.end)


@native_function(Op.Lerp)
def _num_lerp(a, b, x, /):
    return a + (b - a) * x


@native_function(Op.LerpClamped)
def _num_lerp_clamped(a, b, x, /):
    return a + (b - a) * max(0, min(1, x))


def _generic_lerp[T](a: T, b: T, x: float, /) -> T:
    return a + (b - a) * x


def _generic_lerp_clamped[T](a: T, b: T, x: float, /) -> T:
    return a + (b - a) * max(0, min(1, x))


def lerp[T](a: T, b: T, x: float, /) -> T:
    """Linearly interpolate between two values.

    Args:
        a: The start value.
        b: The end value.
        x: The interpolation factor.

    Returns:
        The interpolated value.
    """
    match a, b:
        case (Num(a), Num(b)):
            return _num_lerp(a, b, x)
        case _:
            return _generic_lerp(a, b, x)


def lerp_clamped[T](a: T, b: T, x: float, /) -> T:
    """Linearly interpolate between two values, clamped to the interval.

    Args:
        a: The start value.
        b: The end value.
        x: The interpolation factor.

    Returns:
        The interpolated value.
    """
    match a, b:
        case (Num(a), Num(b)):
            return _num_lerp_clamped(a, b, x)
        case _:
            return _generic_lerp_clamped(a, b, x)


@native_function(Op.Unlerp)
def unlerp(a: float, b: float, x: float, /) -> float:
    """Inverse linear interpolation.

    Args:
        a: The start value.
        b: The end value.
        x: The value to unlerp.

    Returns:
        The unlerped value.
    """
    return (x - a) / (b - a)


@native_function(Op.UnlerpClamped)
def unlerp_clamped(a: float, b: float, x: float, /) -> float:
    """Inverse linear interpolation, clamped to the interval.

    Args:
        a: The start value.
        b: The end value.
        x: The value to unlerp.

    Returns:
        The unlerped value.
    """
    return max(0, min(1, (x - a) / (b - a)))


@native_function(Op.Remap)
def remap(a: float, b: float, c: float, d: float, x: float, /) -> float:
    """Linearly remap a value from one interval to another.

    Args:
        a: The start of the input interval.
        b: The end of the input interval.
        c: The start of the output interval.
        d: The end of the output interval.
        x: The value to remap.

    Returns:
        The remapped value.
    """
    return c + (d - c) * (x - a) / (b - a)


@native_function(Op.RemapClamped)
def remap_clamped(a: float, b: float, c: float, d: float, x: float, /) -> float:
    """Linearly remap a value from one interval to another, clamped to the output interval.

    Args:
        a: The start of the input interval.
        b: The end of the input interval.
        c: The start of the output interval.
        d: The end of the output interval.
        x: The value to remap.

    Returns:
        The remapped value.
    """
    return c + (d - c) * max(0, min(1, (x - a) / (b - a)))


@native_function(Op.Clamp)
def clamp(x: float, a: float, b: float, /) -> float:
    """Clamp a value to an interval.

    Args:
        x: The value to clamp.
        a: The start of the interval.
        b: The end of the interval.

    Returns:
        The clamped value.
    """
    return max(a, min(b, x))


def interp(
    xp: ArrayLike[float] | tuple[float, ...],
    fp: ArrayLike[float] | tuple[float, ...],
    x: float,
) -> float:
    """Linearly interpolate a value within a sequence of points.

    The sequence must have at least 2 elements and be sorted in increasing order of x-coordinates.
    For values of x outside the range of xp, the slope of the first or last segment is used to extrapolate.

    Args:
        xp: The x-coordinates of the points in increasing order.
        fp: The y-coordinates of the points.
        x: The x-coordinate to interpolate.

    Returns:
        The interpolated value.
    """
    assert len(xp) == len(fp)
    assert len(xp) >= 2
    for i in range_or_tuple(1, len(xp) - 1):
        # At i == 1, x may be less than x[0], but since we're extrapolating, we use the first segment regardless.
        if x <= xp[i]:
            return remap(xp[i - 1], xp[i], fp[i - 1], fp[i], x)
    # x > xp[-2] so we can just use the last segment regardless of whether x is in it or to the right of it.
    return remap(xp[-2], xp[-1], fp[-2], fp[-1], x)


def interp_clamped(
    xp: ArrayLike[float] | tuple[float, ...],
    fp: ArrayLike[float] | tuple[float, ...],
    x: float,
):
    """Linearly interpolate a value within a sequence of points.

    The sequence must have at least 2 elements and be sorted in increasing order of x-coordinates.
    For x-coordinates outside the range of the sequence, the respective endpoint of fp is returned.

    Args:
        xp: The x-coordinates of the points in increasing order.
        fp: The y-coordinates of the points.
        x: The x-coordinate to interpolate.

    Returns:
        The interpolated value.
    """
    assert len(xp) == len(fp)
    assert len(xp) >= 2
    if x <= xp[0]:
        return fp[0]
    for i in range_or_tuple(1, len(xp)):
        if x <= xp[i]:
            return remap(xp[i - 1], xp[i], fp[i - 1], fp[i], x)
    return fp[-1]
