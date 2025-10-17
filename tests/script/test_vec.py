from math import pi

from hypothesis import assume, given
from hypothesis import strategies as st

from sonolus.script.array import Array
from sonolus.script.vec import Vec2
from tests.script.conftest import is_close, run_and_validate

floats = st.floats(min_value=-999, max_value=999, allow_nan=False, allow_infinity=False)
nonzero_floats = floats.filter(lambda x: abs(x) > 1e-2)
angles = st.floats(min_value=-pi, max_value=pi, allow_nan=False, allow_infinity=False)


def test_magnitude():
    def fn():
        v = Vec2(3, 4)
        return v.magnitude

    assert run_and_validate(fn) == 5


def test_angle():
    def fn():
        v = Vec2(1, 1)
        return v.angle

    assert is_close(run_and_validate(fn), 0.25 * pi)


def test_dot():
    def fn():
        v = Vec2(1, 2)
        u = Vec2(3, 4)
        return v.dot(u)

    assert run_and_validate(fn) == 11


def test_rotate():
    def fn():
        v = Vec2(1, 1)
        return v.rotate(0.25 * pi)

    result = run_and_validate(fn)
    assert is_close(result.x, 0)
    assert is_close(result.y, 2**0.5)


def test_rotate_about():
    def fn():
        v = Vec2(1, 1)
        pivot = Vec2(1, 0)
        return v.rotate_about(0.5 * pi, pivot)

    assert is_close(run_and_validate(fn).magnitude, 0)


def test_add():
    def fn():
        v = Vec2(1, 2)
        u = Vec2(3, 4)
        return v + u

    assert run_and_validate(fn) == Vec2(4, 6)


def test_sub():
    def fn():
        v = Vec2(1, 2)
        u = Vec2(3, 4)
        return v - u

    assert run_and_validate(fn) == Vec2(-2, -2)


def test_mul_vec():
    def fn():
        v = Vec2(1, 2)
        u = Vec2(3, 4)
        return v * u

    assert run_and_validate(fn) == Vec2(3, 8)


def test_mul_float():
    def fn():
        v = Vec2(1, 2)
        return v * 3

    assert run_and_validate(fn) == Vec2(3, 6)


def test_div_vec():
    def fn():
        v = Vec2(1, 2)
        u = Vec2(3, 4)
        return v / u

    assert run_and_validate(fn) == Vec2(1 / 3, 2 / 4)


def test_div_float():
    def fn():
        v = Vec2(1, 2)
        return v / 3

    assert run_and_validate(fn) == Vec2(1 / 3, 2 / 3)


def test_neg():
    def fn():
        v = Vec2(1, 2)
        return -v

    assert run_and_validate(fn) == Vec2(-1, -2)


def test_equal():
    def fn():
        v = Vec2(1, 2)
        u = Vec2(1, 2)
        return v == u

    assert run_and_validate(fn)


def test_not_equal():
    def fn():
        v = Vec2(1, 2)
        u = Vec2(3, 4)
        return v != u

    assert run_and_validate(fn)


@given(angles)
def test_unit_magnitude(angle):
    def fn():
        return Vec2.unit(angle).magnitude

    result = run_and_validate(fn)
    assert is_close(result, 1.0)


@given(angles)
def test_unit_angle(angle):
    def fn():
        return Vec2.unit(angle).angle

    result = run_and_validate(fn)
    assert is_close(result, angle)


@given(floats, floats)
def test_vec2_normalize_maintains_angle(x, y):
    assume(abs(x) > 1e-2 or abs(y) > 1e-2)

    def fn():
        v = Vec2(x, y)
        return Array(v.angle, v.normalize().angle)

    result = run_and_validate(fn)
    assert is_close(result[0], result[1])
