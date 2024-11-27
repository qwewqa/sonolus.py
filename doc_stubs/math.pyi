# ruff: noqa
def sin(x: float) -> float:
    """Compute the sine of x.

    Args:
        x: The angle in radians.

    Returns:
        The sine of x.
    """
    ...

def cos(x: float) -> float:
    """Compute the cosine of x.

    Args:
        x: The angle in radians.

    Returns:
        The cosine of x.
    """
    ...

def tan(x: float) -> float:
    """Compute the tangent of x.

    Args:
        x: The angle in radians.

    Returns:
        The tangent of x.
    """
    ...

def asin(x: float) -> float:
    """Compute the arcsine of x.

    Args:
        x: A value between -1 and 1.

    Returns:
        The arcsine of x in radians.
    """
    ...

def acos(x: float) -> float:
    """Compute the arccosine of x.

    Args:
        x: A value between -1 and 1.

    Returns:
        The arccosine of x in radians.
    """
    ...

def atan(x: float) -> float:
    """Compute the arctangent of x.

    Args:
        x: A numeric value.

    Returns:
        The arctangent of x in radians.
    """
    ...

def atan2(y: float, x: float) -> float:
    """Compute the arctangent of y / x considering the quadrant.

    Args:
        y: The y-coordinate.
        x: The x-coordinate.

    Returns:
        The arctangent of y / x in radians.
    """
    ...

def sinh(x: float) -> float:
    """Compute the hyperbolic sine of x.

    Args:
        x: A numeric value.

    Returns:
        The hyperbolic sine of x.
    """
    ...

def cosh(x: float) -> float:
    """Compute the hyperbolic cosine of x.

    Args:
        x: A numeric value.

    Returns:
        The hyperbolic cosine of x.
    """
    ...

def tanh(x: float) -> float:
    """Compute the hyperbolic tangent of x.

    Args:
        x: A numeric value.

    Returns:
        The hyperbolic tangent of x.
    """
    ...

def floor(x: float) -> int:
    """Return the largest integer less than or equal to x.

    Args:
        x: A numeric value.

    Returns:
        The floor of x.
    """
    ...

def ceil(x: float) -> int:
    """Return the smallest integer greater than or equal to x.

    Args:
        x: A numeric value.

    Returns:
        The ceiling of x.
    """
    ...

def trunc(x: float) -> int:
    """Truncate x to the nearest integer towards zero.

    Args:
        x: A numeric value.

    Returns:
        The truncated integer value of x.
    """
    ...

def log(x: float, base: float = ...) -> float:
    """Compute the logarithm of x to the given base.

    Args:
        x: The number for which to compute the logarithm.
        base: The base of the logarithm. If omitted, returns the natural logarithm of x.

    Returns:
        The logarithm of x to the specified base.
    """
    ...
