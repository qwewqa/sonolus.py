from __future__ import annotations

try:
    import numpy as np
except ImportError:
    np = None

from math import ceil

from sonolus.script.internal.impl import meta_fn, validate_value
from sonolus.script.interval import clamp
from sonolus.script.num import _is_num
from sonolus.script.record import Record


@meta_fn
def _validate_num(value: float):
    if np is not None and isinstance(value, np.float32):
        return value
    value = validate_value(value)
    if not _is_num(value):
        raise TypeError("Only numeric arguments to float() are supported")
    return value


class UInt36(Record):
    hi: int
    mid: int
    lo: int

    MOD_BASE = 2**12

    @classmethod
    def of(cls, value: int):
        lo = value % cls.MOD_BASE
        mid = (value // cls.MOD_BASE) % cls.MOD_BASE
        hi = (value // cls.MOD_BASE // cls.MOD_BASE) % cls.MOD_BASE
        return UInt36._(hi, mid, lo)

    @classmethod
    @meta_fn
    def _(cls, hi: int, mid: int, lo: int) -> UInt36:
        # This creates read-only instances, which helps with constant folding in the frontend and build times.
        return UInt36._raw(hi=_validate_num(hi), mid=_validate_num(mid), lo=_validate_num(lo))

    @classmethod
    def zero(cls) -> UInt36:
        return UInt36._(0, 0, 0)

    @classmethod
    def one(cls) -> UInt36:
        return UInt36._(0, 0, 1)

    def __add__(self, other: UInt36) -> UInt36:
        lo, carry = self._add2(self.lo, other.lo)
        mid, carry = self._add3(self.mid, other.mid, carry)
        hi, _ = self._add3(self.hi, other.hi, carry)
        return UInt36._(hi, mid, lo)

    def __sub__(self, other: UInt36) -> UInt36:
        lo_raw = self.lo - other.lo
        lo = lo_raw % self.MOD_BASE
        borrow = lo_raw < 0

        mid_raw = self.mid - other.mid - borrow
        mid = mid_raw % self.MOD_BASE
        borrow = mid_raw < 0

        hi = (self.hi - other.hi - borrow) % self.MOD_BASE
        return UInt36._(hi, mid, lo)

    def __mul__(self, other: UInt36) -> UInt36:
        lo_lo = self.lo * other.lo

        lo_mid = self.lo * other.mid
        mid_lo = self.mid * other.lo

        lo_hi = self.lo * other.hi
        hi_lo = self.hi * other.lo
        mid_mid = self.mid * other.mid

        result_lo = lo_lo % self.MOD_BASE
        carry = lo_lo // self.MOD_BASE

        result_mid, carry = self._add3(lo_mid, mid_lo, carry)

        temp, carry2 = self._add3(lo_hi, hi_lo, mid_mid)
        result_hi, _ = self._add2(temp, carry + carry2)

        return UInt36._(result_hi, result_mid, result_lo)

    def __eq__(self, other: UInt36) -> bool:
        return (self.hi, self.mid, self.lo) == (other.hi, other.mid, other.lo)

    def __ne__(self, other: UInt36) -> bool:
        return (self.hi, self.mid, self.lo) != (other.hi, other.mid, other.lo)

    def __lt__(self, other: UInt36) -> bool:
        return (self.hi, self.mid, self.lo) < (other.hi, other.mid, other.lo)

    def __le__(self, other: UInt36) -> bool:
        return (self.hi, self.mid, self.lo) <= (other.hi, other.mid, other.lo)

    def __gt__(self, other: UInt36) -> bool:
        return (self.hi, self.mid, self.lo) > (other.hi, other.mid, other.lo)

    def __ge__(self, other: UInt36) -> bool:
        return (self.hi, self.mid, self.lo) >= (other.hi, other.mid, other.lo)

    @property
    def midlo(self) -> int:
        return self.mid * self.MOD_BASE + self.lo

    @classmethod
    def _add2(cls, a: int, b: int) -> tuple[int, int]:
        ab_raw = a + b
        ab = ab_raw % cls.MOD_BASE
        carry = ab_raw // cls.MOD_BASE
        return ab, carry

    @classmethod
    def _add3(cls, a: int, b: int, c: int) -> tuple[int, int]:
        ab, carry1 = cls._add2(a, b)
        abc, carry2 = cls._add2(ab, c)
        carry = carry1 + carry2
        return abc, carry

    def __str__(self):
        return str(self._int())

    def _int(self):
        return self.hi * self.MOD_BASE * self.MOD_BASE + self.mid * self.MOD_BASE + self.lo

    __hash__ = None


# Technically we could do a bit more and still fit in the number of, distinct finite 32-bit floats,
# but for simplicity, we limit ourselves to 31 bits.
_MAX_TOTAL_STEPS_UINT36 = UInt36._(2**7, 0, 0)
_HALF_MAX_TOTAL_STEPS_UINT36 = UInt36._(2**6, 0, 0)


def quantize_to_step(value: float, start: float, stop: float, step: float) -> tuple[float, float]:
    """Quantize a float value by step size within a range and return the step number and total steps in the range.

    Args:
        value: The float value to quantize.
        start: The start of the range. The range is inclusive of this value.
        stop: The end of the range. The range is exclusive of this value.
        step: The step size.

    Returns:
        A tuple containing the quantized step number and the total number of steps in the range.
    """
    total_steps = max(1, ceil((stop - start) / step))
    result_steps = clamp((value - start) / step, 0, total_steps - 1)
    return result_steps, total_steps


def make_comparable_float(*values: tuple[int, int]) -> float:
    """Convert a series of integer values with associated step counts to a single comparable float.

    The resulting float compares the same way as the original series of integer values.

    This is useful for z-indexes, since Sonolus only supports a single float for z-index.

    Usage:
        ```python
        make_comparable_float(
            quantize_to_step(time, -100, 100, 0.001),
            quantize_to_step(lane, 0, 16, 0.01),
        )
        ```

    Args:
        *values: A series of tuples, each containing an integer value and its associated step count. The integer value
                 must be in the inclusive range [0, step_count - 1].

    Returns:
        A single float that can be used for comparison.
    """
    result = _ints_to_uint36(*values)
    return _uint36_to_comparable_float(result)


def _ints_to_uint36(*values: tuple[int, int]) -> UInt36:
    result = UInt36.zero()
    multiplier = UInt36.one()
    for value, steps in reversed(values):
        step_uint36 = UInt36.of(value)
        result = result + (step_uint36 * multiplier)  # noqa: PLR6104
        multiplier = multiplier * UInt36.of(steps)  # noqa: PLR6104
    # These don't catch everything if multiplier overflows all 36 bits, but they'll catch most
    # reasonable mistakes.
    assert multiplier < _MAX_TOTAL_STEPS_UINT36, "Maximum precision exceeded"
    assert multiplier > UInt36.zero(), "Precision ranges must have at least one step"
    return result


def _uint36_to_comparable_float(value: UInt36) -> float:
    value = UInt36._(value.hi, value.mid, value.lo)
    if value < _HALF_MAX_TOTAL_STEPS_UINT36:
        sign = -1
        value = _HALF_MAX_TOTAL_STEPS_UINT36 - value - UInt36.one()
        hi = value.hi
        midlo = value.midlo
    else:
        sign = 1
        value = value - _HALF_MAX_TOTAL_STEPS_UINT36  # noqa: PLR6104
        hi = value.hi
        midlo = value.midlo
    exponent = hi * 2 + (midlo >= 2**23)
    mantissa = midlo % (2**23)
    return sign * (1 + mantissa / 2**23) * (2**exponent)
