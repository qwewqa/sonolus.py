from __future__ import annotations

from sonolus.script.interval import clamp
from sonolus.script.record import Record


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
        return UInt36(hi, mid, lo)

    @classmethod
    def zero(cls) -> UInt36:
        return UInt36(0, 0, 0)

    @classmethod
    def one(cls) -> UInt36:
        return UInt36(0, 0, 1)

    def __add__(self, other: UInt36) -> UInt36:
        lo, carry = self._add2(self.lo, other.lo)
        mid, carry = self._add3(self.mid, other.mid, carry)
        hi, _ = self._add3(self.hi, other.hi, carry)
        return UInt36(hi, mid, lo)

    def __sub__(self, other: UInt36) -> UInt36:
        lo_raw = self.lo - other.lo
        lo = lo_raw % self.MOD_BASE
        borrow = lo_raw < 0

        mid_raw = self.mid - other.mid - borrow
        mid = mid_raw % self.MOD_BASE
        borrow = mid_raw < 0

        hi = (self.hi - other.hi - borrow) % self.MOD_BASE
        return UInt36(hi, mid, lo)

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

        return UInt36(result_hi, result_mid, result_lo)

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


class PrecisionRange(Record):
    """A range with a specified number of steps. The range is inclusive of the start and exclusive of the end.

    Usage:
        ```python
        PrecisionRange(start: float, end: float, steps: int)
        ```
    """

    start: float
    end: float
    steps: int

    def to_step_number(self, value: float) -> int:
        result = round((value - self.start) * self.steps / (self.end - self.start))
        return clamp(result, 0, self.steps - 1)

    def from_step_number(self, step: int) -> float:
        step = clamp(step, 0, self.steps - 1)
        return self.start + (self.end - self.start) * step / self.steps

    @classmethod
    def of_subdivision(cls, start: float, end: float, subdivision: int) -> PrecisionRange:
        """Creates a PrecisionRange from start to end with the given subdivision (steps per unit).

        Args:
            start: The start of the range.
            end: The end of the range.
            subdivision: The number of steps per unit.

        Returns:
            A PrecisionRange instance.
        """
        steps = int((end - start) * subdivision)
        return PrecisionRange(start, end, steps)


# Technically we could do a bit more and still fit in the number of, distinct finite 32-bit floats,
# but for simplicity, we limit ourselves to 31 bits.
_MAX_TOTAL_STEPS_UINT36 = UInt36(2**7, 0, 0)
_HALF_MAX_TOTAL_STEPS_UINT36 = UInt36(2**6, 0, 0)


def floats_to_comparable_float(*numbers: tuple[float, PrecisionRange]):
    """Convert a series of floats with associated precision ranges to a single comparable float.

    The resulting float compares the same way as the original series of floats according to their precision ranges.

    This is useful for z-indexes, since Sonolus only supports a single float for z-index.

    Args:
        *numbers: A series of tuples, each containing a number and its associated PrecisionRange.

    Returns:
        A single float that can be used for comparison.
    """
    result = _floats_to_uint36(*numbers)
    return _uint36_to_comparable_float(result)


def _floats_to_uint36(*numbers: tuple[float, PrecisionRange]) -> UInt36:
    result = UInt36.zero()
    multiplier = UInt36.of(1)
    for number, precision_range in reversed(numbers):
        step_number = precision_range.to_step_number(number)
        step_uint36 = UInt36.of(step_number)
        result @= result + (step_uint36 * multiplier)
        multiplier @= multiplier * UInt36.of(precision_range.steps)
    assert multiplier > UInt36.zero(), "Precision ranges must have at least one step"
    assert multiplier < _MAX_TOTAL_STEPS_UINT36, "Maximum precision exceeded"
    return result


def _uint36_to_comparable_float(value: UInt36) -> float:
    value = UInt36(value.hi, value.mid, value.lo)
    if value < _HALF_MAX_TOTAL_STEPS_UINT36:
        sign = -1
        value @= _HALF_MAX_TOTAL_STEPS_UINT36 - value
    else:
        sign = 1
        value @= value - _HALF_MAX_TOTAL_STEPS_UINT36
    hi = value.hi
    midlo = value.midlo
    exponent = hi * 2 + (midlo >= 2**23)
    mantissa = midlo % (2**23)
    return sign * (1 + mantissa / 2**23) * (2 ** (exponent - 126))
