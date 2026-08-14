# ruff: noqa
def sin(x: float, /) -> float:
    """Compute the sine of x.

    Args:
        x: The angle in radians.
    """
    ...

def cos(x: float, /) -> float:
    """Compute the cosine of x.

    Args:
        x: The angle in radians.
    """
    ...

def tan(x: float, /) -> float:
    """Compute the tangent of x.

    Args:
        x: The angle in radians.
    """
    ...

def asin(x: float, /) -> float:
    """Compute the arcsine of x in radians.

    Args:
        x: A value between -1 and 1.
    """
    ...

def acos(x: float, /) -> float:
    """Compute the arccosine of x in radians.

    Args:
        x: A value between -1 and 1.
    """
    ...

def atan(x: float, /) -> float:
    """Compute the arctangent of x in radians.

    Args:
        x: A numeric value.
    """
    ...

def atan2(y: float, x: float, /) -> float:
    """Compute the arctangent of y / x in radians, considering the quadrant.

    Args:
        y: The y-coordinate.
        x: The x-coordinate.
    """
    ...

def sinh(x: float, /) -> float:
    """Compute the hyperbolic sine of x.

    Args:
        x: A numeric value.
    """
    ...

def cosh(x: float, /) -> float:
    """Compute the hyperbolic cosine of x.

    Args:
        x: A numeric value.
    """
    ...

def tanh(x: float, /) -> float:
    """Compute the hyperbolic tangent of x.

    Args:
        x: A numeric value.
    """
    ...

def floor(x: float, /) -> int:
    """Return the largest integer less than or equal to x.

    Args:
        x: A numeric value.
    """
    ...

def ceil(x: float, /) -> int:
    """Return the smallest integer greater than or equal to x.

    Args:
        x: A numeric value.
    """
    ...

def trunc(x: float, /) -> int:
    """Truncate x to the nearest integer towards zero.

    Args:
        x: A numeric value.
    """
    ...

def log(x: float, base: float = ..., /) -> float:
    """Compute the logarithm of x to the given base.

    Args:
        x: The number for which to compute the logarithm.
        base: The base of the logarithm. If omitted, returns the natural logarithm of x.
    """
    ...

def sqrt(x: float, /) -> float:
    """Compute the square root of x.

    Args:
        x: A non-negative numeric value.
    """
    ...

def degrees(x: float, /) -> float:
    """Convert radians to degrees.

    Args:
        x: An angle in radians.
    """
    ...

def radians(x: float, /) -> float:
    """Convert degrees to radians.

    Args:
        x: An angle in degrees.
    """
    ...

pi: float
"""The mathematical constant pi."""

e: float
"""The mathematical constant e."""

tau: float
"""The mathematical constant tau."""

inf: float
"""Positive infinity."""
