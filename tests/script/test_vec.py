import random
from math import pi

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from sonolus.script.array import Array
from sonolus.script.containers import VarArray
from sonolus.script.internal.context import RuntimeChecks
from sonolus.script.vec import Vec2, angle_diff, pnpoly, signed_angle_diff
from tests.script.conftest import is_close, run_and_validate, run_compiled

floats = st.floats(min_value=-999, max_value=999, allow_nan=False, allow_infinity=False)
nonzero_floats = floats.filter(lambda x: abs(x) > 1e-2)
angles = st.floats(min_value=-pi, max_value=pi, allow_nan=False, allow_infinity=False)


def test_pnpoly_rejects_empty_polygon():
    def fn():
        return pnpoly((), Vec2.zero())

    with pytest.raises(AssertionError, match="Polygon must contain at least one vertex"):
        run_and_validate(fn)


def test_empty_polygon_in_runtime_dead_branch_compiles():
    def fn():
        if random.randrange(0, 1):
            pnpoly((), Vec2.zero())
        return 23

    # Unlike run_and_validate, the default run_compiled matrix covers every runtime-check setting.
    assert run_compiled(fn) == 23


@pytest.mark.parametrize("runtime_checks", [RuntimeChecks.TERMINATE, RuntimeChecks.NOTIFY_AND_TERMINATE])
def test_pnpoly_rejects_runtime_empty_polygon(runtime_checks):
    def fn():
        vertices = VarArray[Vec2, 1].new()
        pnpoly(vertices, Vec2.zero())
        return 23

    assert run_compiled(fn, runtime_checks=runtime_checks) == 0


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


def test_orthogonal():
    # Right maps to up. An asymmetric case is what pins which way the quarter turn goes.
    def fn():
        return Vec2(1, 0).orthogonal()

    assert run_and_validate(fn) == Vec2(0, 1)


def test_orthogonal_off_axis():
    def fn():
        return Vec2(3, 4).orthogonal()

    assert run_and_validate(fn) == Vec2(-4, 3)


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


def test_named_vectors():
    def fn():
        return Array(Vec2.zero(), Vec2.one(), Vec2.up(), Vec2.down(), Vec2.left(), Vec2.right())

    result = run_and_validate(fn)
    assert list(result) == [Vec2(0, 0), Vec2(1, 1), Vec2(0, 1), Vec2(0, -1), Vec2(-1, 0), Vec2(1, 0)]


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
        return angle_diff(v.angle, v.normalize().angle)

    diff = run_and_validate(fn)
    assert is_close(diff, 0)


@given(floats, floats)
def test_vec2_normalize_or_zero_maintains_angle(x, y):
    assume(abs(x) > 1e-2 or abs(y) > 1e-2)

    def fn():
        v = Vec2(x, y)
        return angle_diff(v.angle, v.normalize_or_zero().angle)

    diff = run_and_validate(fn)
    assert is_close(diff, 0)


def test_vec2_zero_normalize_or_zero():
    def fn():
        v = Vec2(0, 0)
        return v.normalize_or_zero()

    assert run_and_validate(fn) == Vec2(0, 0)


def test_angle_diff_examples():
    assert is_close(angle_diff(0, 0), 0)
    assert is_close(angle_diff(0, pi), pi)
    assert is_close(angle_diff(0, 2 * pi), 0)
    assert is_close(angle_diff(0, pi / 4), pi / 4)
    assert is_close(angle_diff(pi / 2, -pi / 2), pi)
    assert is_close(angle_diff(0, 3 * pi / 2), pi / 2)
    assert is_close(angle_diff(-pi / 4, pi / 4), pi / 2)


@given(floats, floats)
def test_angle_diff_in_range(a, b):
    result = angle_diff(a, b)
    assert 0 <= result <= pi


@given(floats)
def test_angle_diff_identity(angle):
    result = angle_diff(angle, angle)
    assert result == 0


@given(floats, floats)
def test_angle_diff_commutative(a, b):
    result1 = angle_diff(a, b)
    result2 = angle_diff(b, a)
    assert is_close(result1, result2)


@given(floats, st.floats(min_value=-pi, max_value=pi, allow_nan=False, allow_infinity=False))
def test_angle_diff_delta(angle, delta):
    result = angle_diff(angle, angle + delta)
    assert is_close(result, abs(delta))


def test_signed_angle_diff_examples():
    assert is_close(signed_angle_diff(0, 0), 0)
    assert is_close(signed_angle_diff(pi / 4, 0), pi / 4)
    assert is_close(signed_angle_diff(0, pi / 4), -pi / 4)
    assert is_close(signed_angle_diff(0, 2 * pi), 0)
    assert is_close(signed_angle_diff(pi / 2, 0), pi / 2)
    assert is_close(signed_angle_diff(0, 3 * pi / 4), -3 * pi / 4)
    assert is_close(signed_angle_diff(3 * pi / 4, -3 * pi / 4), -pi / 2)
    assert is_close(signed_angle_diff(pi, 0), -pi)


@given(floats, floats)
def test_signed_angle_diff_in_range(a, b):
    result = signed_angle_diff(a, b)
    assert -pi <= result <= pi


@given(floats)
def test_signed_angle_diff_identity(angle):
    result = signed_angle_diff(angle, angle)
    assert result == 0


@given(floats, floats)
def test_signed_angle_diff_anticommutative(a, b):
    result1 = signed_angle_diff(a, b)
    result2 = signed_angle_diff(b, a)
    assert is_close(result1, -result2)


@given(floats, st.floats(min_value=-pi, max_value=pi, exclude_max=True, allow_nan=False, allow_infinity=False))
def test_signed_angle_diff_delta(angle, delta):
    result = signed_angle_diff(angle + delta, angle)
    assert is_close(result, delta) or (is_close(abs(result), pi) and is_close(abs(delta), pi))
