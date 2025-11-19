from contextlib import contextmanager

import numpy as np
from hypothesis import example, given
from hypothesis import strategies as st

from sonolus.script.num import _is_num  # noqa: PLC2701
from sonolus.script.numtools import _UInt36, _uint36_to_comparable_float  # noqa: PLC2701
from tests.script.conftest import run_and_validate


class PlainField:
    def __init__(self, name):
        self.name = name

    def __get__(self, instance, owner=None):
        result = instance._value_[self.name]
        if _is_num(result):
            return np.float32(result._as_py_())
        return result

    def __set__(self, instance, value):
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

        cls.__new__ = __new__
    try:
        yield
    finally:
        for cls in classes:
            cls._fields_ = classes_to_orig_fields[cls]
            for field in cls._fields_:
                setattr(cls, field.name, field)
            del cls.__new__


def patch():
    return patch_float32_records(_UInt36)


def uint36_to_int(u: _UInt36) -> int:
    return int(u.hi) << 24 | int(u.mid) << 12 | int(u.lo)


def int_to_uint36(value: int) -> _UInt36:
    hi = (value >> 24) & 0xFFF
    mid = (value >> 12) & 0xFFF
    lo = value & 0xFFF
    return _UInt36(hi, mid, lo)


@given(x=st.integers(min_value=0, max_value=2**31 - 2))
@example(x=0)
@example(x=2**30 - 1)
@example(x=2**30)
@example(x=2**30 + 1)
@example(x=2**31 - 2)
def test_uint36_to_comparable_float_with_float32(x: int):
    with patch():
        ux = int_to_uint36(x)
        uy = int_to_uint36(x + 1)
        fx = _uint36_to_comparable_float(ux)
        fy = _uint36_to_comparable_float(uy)
        assert fx < fy


@given(x=st.integers(min_value=0, max_value=2**31 - 2))
@example(x=0)
@example(x=2**30 - 1)
@example(x=2**30)
@example(x=2**30 + 1)
@example(x=2**31 - 2)
def test_uint36_to_comparable_float(x: int):
    ux = int_to_uint36(x)
    uy = int_to_uint36(x + 1)

    def fn():
        fx = _uint36_to_comparable_float(ux)
        fy = _uint36_to_comparable_float(uy)
        return fx < fy

    assert run_and_validate(fn)
