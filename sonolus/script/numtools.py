from __future__ import annotations

from collections.abc import Iterable
from math import ceil

from sonolus.script.internal.impl import meta_fn, perf_meta_fn, validate_value
from sonolus.script.interval import clamp
from sonolus.script.num import _is_num
from sonolus.script.record import Record

enable_np = False


@meta_fn
def _validate_num(value: float):
    if enable_np:
        try:
            import numpy as np
        except ImportError:
            np = None
        if np is not None and isinstance(value, np.float32):
            return value
    value = validate_value(value)
    if not _is_num(value):
        raise TypeError("Only numeric arguments to float() are supported")
    return value


class _UInt32(Record):
    hi: int
    lo: int

    @classmethod
    @perf_meta_fn
    def of(cls, value: int):
        lo = value % (2**16)
        hi = (value // (2**16)) % (2**16)
        return _UInt32._(hi, lo)

    @classmethod
    @meta_fn
    def _(cls, hi: int, lo: int) -> _UInt32:
        # This creates read-only instances, which helps with constant folding in the frontend and build times.
        return _UInt32._raw(hi=_validate_num(hi), lo=_validate_num(lo))

    @classmethod
    @perf_meta_fn
    def zero(cls) -> _UInt32:
        return _UInt32._(0, 0)

    @classmethod
    @perf_meta_fn
    def one(cls) -> _UInt32:
        return _UInt32._(0, 1)

    @classmethod
    @perf_meta_fn
    def _carry_add(cls, a: int, b: int) -> tuple[int, int]:
        ab_raw = a + b
        ab = ab_raw % (2**16)
        carry = ab_raw >= (2**16)
        return ab, carry

    @classmethod
    @perf_meta_fn
    def _borrow_sub(cls, a: int, b: int) -> tuple[int, int]:
        ab_raw = a - b
        ab = ab_raw % (2**16)
        borrow = ab_raw < 0
        return ab, borrow

    @classmethod
    @perf_meta_fn
    def _carry_mul(cls, a: int, b: int) -> tuple[int, int]:
        # Have to be careful since 32-bit floats only have 24 bits of precision
        blo = b % (2**8)
        bhi = b // (2**8)
        ablo_raw = a * blo
        abhi_raw = a * bhi * (2**8)

        ablo_lo = ablo_raw % (2**16)
        ablo_hi = ablo_raw // (2**16)
        abhi_lo = abhi_raw % (2**16)
        abhi_hi = abhi_raw // (2**16)
        lo_raw = ablo_lo + abhi_lo
        lo = lo_raw % (2**16)
        carry = ablo_hi + abhi_hi + (lo_raw >= (2**16))
        return lo, carry

    @classmethod
    @perf_meta_fn
    def _wrap_mul(cls, a: int, b: int) -> int:
        # We have this for the same reason as _carry_mul
        blo = b % (2**8)
        bhi = b // (2**8)
        ablo_raw = a * blo
        abhi_raw = a * bhi * (2**8)

        ablo_lo = ablo_raw % (2**16)
        abhi_lo = abhi_raw % (2**16)
        lo_raw = ablo_lo + abhi_lo
        lo = lo_raw % (2**16)
        return lo

    @perf_meta_fn
    def __add__(self, other: _UInt32) -> _UInt32:
        lo, carry = self._carry_add(self.lo, other.lo)
        hi = (self.hi + other.hi + carry) % (2**16)
        return _UInt32._(hi, lo)

    @perf_meta_fn
    def __sub__(self, other: _UInt32) -> _UInt32:
        lo, borrow = self._borrow_sub(self.lo, other.lo)
        hi = (self.hi - other.hi - borrow) % (2**16)
        return _UInt32._(hi, lo)

    @perf_meta_fn
    def __mul__(self, other: _UInt32) -> _UInt32:
        lo_lo, carry_lo_lo = self._carry_mul(self.lo, other.lo)
        hi_lo = self._wrap_mul(self.hi, other.lo)
        lo_hi = self._wrap_mul(self.lo, other.hi)
        # hi_hi is ignored since it would overflow entirely

        lo = lo_lo
        hi = (hi_lo + lo_hi + carry_lo_lo) % (2**16)
        return _UInt32._(hi, lo)

    def __eq__(self, other):
        return (self.hi, self.lo) == (other.hi, other.lo)

    def __ne__(self, other):
        return (self.hi, self.lo) != (other.hi, other.lo)

    def __lt__(self, other):
        return (self.hi, self.lo) < (other.hi, other.lo)

    def __le__(self, other):
        return (self.hi, self.lo) <= (other.hi, other.lo)

    def __gt__(self, other):
        return (self.hi, self.lo) > (other.hi, other.lo)

    def __ge__(self, other):
        return (self.hi, self.lo) >= (other.hi, other.lo)

    __hash__ = None


# Technically we could do a bit more and still fit in the number of, distinct finite 32-bit floats,
# but for simplicity, we limit ourselves to 31 bits.
_MAX_TOTAL_STEPS_UINT32 = _UInt32._(2**15, 0)
_HALF_MAX_TOTAL_STEPS_UINT32 = _UInt32._(2**14, 0)


def quantize_to_step(value: float, start: float, stop: float, step: float) -> tuple[int, int]:
    """Quantize a float value by step size within a range and return the step number and total steps in the range.

    If value is between start and stop, start + step_number * step will be approximately equal to value,
    where step_number is the first element of the returned tuple.

    Args:
        value: The float value to quantize.
        start: The start of the range. The range is inclusive of this value.
        stop: The end of the range. The range is exclusive of this value. Must be strictly greater than start.
        step: The step size. Must be positive.

    Returns:
        A tuple containing the quantized step number and the total number of steps in the range.
    """
    assert stop > start, "stop must be strictly greater than start"
    assert step > 0, "step must be positive"
    total_steps = max(1, ceil((stop - start) / step))
    result_steps = clamp(round((value - start) / step), 0, total_steps - 1)
    return result_steps, total_steps


def make_comparable_float(*values: tuple[int, int]) -> float:
    """Convert a series of non-negative integer values into a float that compares the same way as the original series.

    This is useful for z-indexes, since Sonolus only supports a single float for z-index.

    The product of all maximum values must be less than 2^31.

    Usage:
        ```python
        make_comparable_float(
            quantize_to_step(time, -100, 100, 0.001),
            quantize_to_step(abs(lane), 0, 16, 0.01),
        )
        ```

    Args:
        *values: A series of tuples (value, max_value) where value falls in the range [0, max_value).

    Returns:
        A single float that can be used for comparison.
    """
    _check_max_total_steps(values)
    result = _ints_to_uint32(*values)
    return _uint32_to_comparable_float(result)


@meta_fn
def _check_max_total_steps(values: Iterable[tuple[int, int]]):
    values = validate_value(values).value
    total = 1
    for entry in values:
        _, max_value = validate_value(entry).value
        max_value = validate_value(max_value)
        if not max_value._is_py_():
            return
        total *= max_value._as_py_()
        if total >= 2**31:
            raise ValueError(
                "The product of all maximum values must be less than 2^31. "
                "If using quantize_to_step, increase step sizes or reduce ranges."
            )


def product(values: Iterable[float]) -> float:
    """Calculate the product of an iterable of floats."""
    result = 1.0
    for value in values:
        result *= value
    return result


def _ints_to_uint32(*values: tuple[int, int]) -> _UInt32:
    result = _UInt32.zero()
    for i, (value, steps) in enumerate(values):
        if i == 0:
            result = _UInt32.of(value)
        else:
            result = result * _UInt32.of(steps) + _UInt32.of(value)
    return result


def _uint32_to_comparable_float(value: _UInt32) -> float:
    value = _UInt32._(value.hi, value.lo)
    if value < _HALF_MAX_TOTAL_STEPS_UINT32:
        sign = -1
        value = _HALF_MAX_TOTAL_STEPS_UINT32 - value - _UInt32.one()
        hi = value.hi
        lo = value.lo
    else:
        sign = 1
        value = value - _HALF_MAX_TOTAL_STEPS_UINT32  # noqa: PLR6104
        hi = value.hi
        lo = value.lo
    exponent = hi // (2**7)
    mantissa = (hi % (2**7)) * (2**16) + lo
    return sign * (1 + mantissa / 2**23) * (2**exponent)
