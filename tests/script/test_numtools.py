import operator
from contextlib import contextmanager
from functools import reduce

import numpy as np
from hypothesis import example, given
from hypothesis import strategies as st

from sonolus.script import numtools
from sonolus.script.containers import Pair
from sonolus.script.num import _is_num  # noqa: PLC2701
from sonolus.script.numtools import (
    _ints_to_uint32,  # noqa: PLC2701
    _UInt32,  # noqa: PLC2701
    _uint32_to_comparable_float,  # noqa: PLC2701
    make_comparable_float,
    product,
    quantize_to_step,
)
from tests.script.conftest import run_and_validate

numtools.enable_np = True


class PlainField:
    def __init__(self, name):
        self.name = name

    def __get__(self, instance, owner=None):
        result = instance._value_[self.name]
        if _is_num(result):
            return np.float32(result._as_py_())
        return result

    def __set__(self, instance, value):
        if _is_num(value):
            value = np.float32(value._as_py_())
        instance._value_[self.name] = value


@contextmanager
def patch_float32_records(*classes):
    classes_to_orig_fields = {}
    for cls in classes:
        classes_to_orig_fields[cls] = cls._fields_
        new_fields = [PlainField(field.name) for field in cls._fields_]
        cls._fields_ = new_fields
        for field in new_fields:
            setattr(cls, field.name, field)

        def __new__(cls, *args, **kwargs):  # noqa: N807
            bound = cls._constructor_signature_.bind(*args, **kwargs)
            result = object.__new__(cls)
            result._value_ = {name: np.float32(value) for name, value in bound.arguments.items()}
            return result

        def _copy_(self):
            new_instance = object.__new__(self.__class__)
            new_instance._value_ = self._value_
            return new_instance

        cls.__new__ = __new__
        cls._copy_ = _copy_
    try:
        yield
    finally:
        for cls in classes:
            cls._fields_ = classes_to_orig_fields[cls]
            for field in cls._fields_:
                setattr(cls, field.name, field)
            del cls.__new__
            del cls._copy_


def patch():
    return patch_float32_records(_UInt32)


def uint32_to_int(u: _UInt32) -> int:
    return int(u.hi) << 16 | int(u.lo)


def int_to_uint32(value: int) -> _UInt32:
    hi = (value >> 16) & 0xFFFF
    lo = value & 0xFFFF
    return _UInt32(hi=hi, lo=lo)


@given(x=st.integers(min_value=0, max_value=2**31 - 2))
@example(x=0)
@example(x=2**30 - 1)
@example(x=2**30)
@example(x=2**30 + 1)
@example(x=2**31 - 2)
def test_uint32_to_comparable_float_with_float32(x: int):
    with patch():
        ux = int_to_uint32(x)
        uy = int_to_uint32(x + 1)
        fx = _uint32_to_comparable_float(ux)
        fy = _uint32_to_comparable_float(uy)
        assert fx < fy


@given(x=st.integers(min_value=0, max_value=2**31 - 2))
@example(x=0)
@example(x=2**30 - 1)
@example(x=2**30)
@example(x=2**30 + 1)
@example(x=2**31 - 2)
def test_uint32_to_comparable_float(x: int):
    ux = int_to_uint32(x)
    uy = int_to_uint32(x + 1)

    def fn():
        fx = _uint32_to_comparable_float(ux)
        fy = _uint32_to_comparable_float(uy)
        return fx < fy

    assert run_and_validate(fn)


@st.composite
def make_ints_to_uint32_arg(draw, max_max_value) -> tuple[int, int]:
    max_value = draw(st.integers(min_value=1, max_value=max_max_value))
    value = draw(st.integers(min_value=0, max_value=max_value - 1))
    return value, max_value


@st.composite
def make_ints_to_uint32_args(draw, min_args: int = 0, max_args: int = 5) -> tuple[tuple[int, int], ...]:
    headroom = (1 << 31) - 1
    args = []
    count = draw(st.integers(min_value=min_args, max_value=max_args))
    for _ in range(count):
        value, max_value = draw(make_ints_to_uint32_arg(max_max_value=headroom))
        args.append((value, max_value))
        headroom //= max_value
    return tuple(args)


@st.composite
def make_paired_ints_to_uint32_args(
    draw, min_args: int = 1, max_args: int = 5
) -> tuple[tuple[tuple[int, int], ...], tuple[tuple[int, int], ...]]:
    num_args = draw(st.integers(min_value=min_args, max_value=max_args))
    return (
        draw(make_ints_to_uint32_args(min_args=num_args, max_args=num_args)),
        draw(make_ints_to_uint32_args(min_args=num_args, max_args=num_args)),
    )


@given(args=make_ints_to_uint32_args())
def test_ints_to_uint32(args):
    def fn():
        return _ints_to_uint32(*args)

    expected = 0
    multiplier = 1
    for value, max_value in reversed(args):
        expected += value * multiplier
        multiplier *= max_value
    assert uint32_to_int(run_and_validate(fn)) == expected


@given(args=make_ints_to_uint32_args())
def test_ints_to_uint32_with_float32(args):
    with patch():
        expected = 0
        multiplier = 1
        for value, max_value in reversed(args):
            expected += value * multiplier
            multiplier *= max_value

        assert uint32_to_int(_ints_to_uint32(*args)) == expected


@given(args=make_paired_ints_to_uint32_args())
def test_ints_to_uint32_comparison(args: tuple[tuple[tuple[int, int], ...], tuple[tuple[int, int], ...]]):
    args1, args2 = args

    def fn():
        return Pair(_ints_to_uint32(*args1), _ints_to_uint32(*args2))

    u1, u2 = run_and_validate(fn).tuple
    r1 = uint32_to_int(u1)
    r2 = uint32_to_int(u2)
    assert (r1 < r2) == (u1 < u2)
    assert (r1 <= r2) == (u1 <= u2)
    assert (r1 > r2) == (u1 > u2)
    assert (r1 >= r2) == (u1 >= u2)
    assert (r1 == r2) == (u1 == u2)
    assert (r1 != r2) == (u1 != u2)


@given(args=make_paired_ints_to_uint32_args())
def test_ints_to_uint32_comparison_with_float32(args: tuple[tuple[tuple[int, int], ...], tuple[tuple[int, int], ...]]):
    args1, args2 = args
    with patch():
        u1 = _ints_to_uint32(*args1)
        u2 = _ints_to_uint32(*args2)
        r1 = uint32_to_int(u1)
        r2 = uint32_to_int(u2)
        assert (r1 < r2) == (u1 < u2)
        assert (r1 <= r2) == (u1 <= u2)
        assert (r1 > r2) == (u1 > u2)
        assert (r1 >= r2) == (u1 >= u2)
        assert (r1 == r2) == (u1 == u2)
        assert (r1 != r2) == (u1 != u2)


@given(args=make_paired_ints_to_uint32_args())
def test_make_comparable_float_comparison(args: tuple[tuple[tuple[int, int], ...], tuple[tuple[int, int], ...]]):
    args1, args2 = args

    def fn():
        return Pair(make_comparable_float(*args1), make_comparable_float(*args2))

    f1, f2 = run_and_validate(fn).tuple
    u1 = _ints_to_uint32(*args1)
    u2 = _ints_to_uint32(*args2)
    assert (f1 < f2) == (u1 < u2)
    assert (f1 <= f2) == (u1 <= u2)
    assert (f1 > f2) == (u1 > u2)
    assert (f1 >= f2) == (u1 >= u2)
    assert (f1 == f2) == (u1 == u2)
    assert (f1 != f2) == (u1 != u2)


@given(args=make_paired_ints_to_uint32_args())
def test_make_comparable_float_comparison_with_float32(
    args: tuple[tuple[tuple[int, int], ...], tuple[tuple[int, int], ...]],
):
    args1, args2 = args
    with patch():
        f1 = make_comparable_float(*args1)
        f2 = make_comparable_float(*args2)
        u1 = _ints_to_uint32(*args1)
        u2 = _ints_to_uint32(*args2)
        assert (f1 < f2) == (u1 < u2)
        assert (f1 <= f2) == (u1 <= u2)
        assert (f1 > f2) == (u1 > u2)
        assert (f1 >= f2) == (u1 >= u2)
        assert (f1 == f2) == (u1 == u2)
        assert (f1 != f2) == (u1 != u2)


@st.composite
def make_quantize_to_step_bounds(draw) -> tuple[float, float, float]:
    start = draw(st.floats(allow_infinity=False, allow_nan=False, min_value=-1e5, max_value=1e5))
    delta = draw(st.floats(allow_infinity=False, allow_nan=False, min_value=1e-5, max_value=1e5))
    step = draw(st.floats(allow_infinity=False, allow_nan=False, min_value=1e-2, max_value=1e5))
    stop = start + delta
    return start, stop, step


@st.composite
def make_in_range_quantize_to_step_args(draw) -> tuple[float, float, float, float]:
    start, stop, step = draw(make_quantize_to_step_bounds())
    value = draw(
        st.floats(allow_infinity=False, allow_nan=False, min_value=start, max_value=max(start, stop - step / 2))
    )
    return value, start, stop, step


@st.composite
def make_out_of_range_quantize_to_step_args(draw) -> tuple[float, float, float, float]:
    start, stop, step = draw(make_quantize_to_step_bounds())
    if draw(st.booleans()):
        value = draw(st.floats(allow_infinity=False, allow_nan=False, max_value=start - 1, min_value=-1e10))
    else:
        value = draw(st.floats(allow_infinity=False, allow_nan=False, min_value=stop + 1, max_value=1e10))
    return value, start, stop, step


@given(args=make_in_range_quantize_to_step_args())
def test_quantize_to_step_in_range(args: tuple[float, float, float, float]):
    value, start, stop, step = args

    def fn():
        return Pair(*quantize_to_step(value, start, stop, step))

    result_step, result_max_steps = run_and_validate(fn).tuple
    assert abs((start + result_step * step) - value) < step / 2 + 1e-4
    assert result_max_steps >= 1
    assert start + (result_max_steps - 1) * step < stop + 1e-4
    assert start + result_max_steps * step >= stop - 1e-4


@given(args=make_out_of_range_quantize_to_step_args())
def test_quantize_to_step_out_of_range(args: tuple[float, float, float, float]):
    value, start, stop, step = args

    def fn():
        return Pair(*quantize_to_step(value, start, stop, step))

    result_step, result_max_steps = run_and_validate(fn).tuple
    if value < start:
        assert result_step == 0
    else:
        assert result_step == result_max_steps - 1
    assert result_max_steps >= 1
    assert start + (result_max_steps - 1) * step < stop + 1e-4
    assert start + result_max_steps * step >= stop - 1e-4


@given(
    values=st.lists(
        st.floats(allow_infinity=False, allow_nan=False, min_value=1e-5, max_value=1e5), min_size=0, max_size=10
    )
)
def test_product(values: list[float]):
    values = tuple(values)

    def fn():
        return product(values)

    expected = reduce(operator.mul, values, 1.0)
    assert run_and_validate(fn) == expected
