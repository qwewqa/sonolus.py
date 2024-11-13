from datetime import timedelta

from hypothesis import assume, given, settings
from hypothesis import strategies as st
from script.conftest import is_close

from sonolus.script.transform import Transform2d
from sonolus.script.vec import Vec2
from tests.script.conftest import validate_dual_run

ints = st.integers(min_value=-999, max_value=999)
floats = st.floats(min_value=-999, max_value=999, allow_nan=False, allow_infinity=False)


@st.composite
def vecs(draw):
    x = draw(floats)
    y = draw(floats)
    return Vec2(x, y)


@given(
    v=vecs(),
    t=vecs(),
)
@settings(deadline=timedelta(milliseconds=1000))
def test_translate(v, t):
    def fn():
        transform = Transform2d.new().translate(v)
        return transform.transform_vec(t)

    assert validate_dual_run(fn) == v + t


@given(
    v_x=floats,
    foreground_y=floats,
    vanishing_point=vecs(),
)
@settings(deadline=timedelta(milliseconds=1000))
def test_perspective_at_foreground(v_x, foreground_y, vanishing_point):
    v = Vec2(v_x, foreground_y)
    assume(abs(foreground_y - vanishing_point.y) > 1e-2)

    def fn():
        transform = Transform2d.new().perspective(foreground_y, vanishing_point)
        return transform.transform_vec(v)

    result = validate_dual_run(fn)
    assert is_close(result.x, v_x, rel_tol=1e-4, abs_tol=1e-4)
    assert is_close(result.y, foreground_y, rel_tol=1e-4, abs_tol=1e-4)


@given(
    v_x=floats,
    foreground_y=floats,
    vanishing_point=vecs(),
)
@settings(deadline=timedelta(milliseconds=1000))
def test_perspective_at_infinity(v_x, foreground_y, vanishing_point):
    v = Vec2(v_x, (vanishing_point.y - foreground_y) * 1e6)  # Close enough to infinity for testing
    assume(abs(foreground_y - vanishing_point.y) > 1e-2)

    def fn():
        transform = Transform2d.new().perspective(foreground_y, vanishing_point)
        return transform.transform_vec(v)

    result = validate_dual_run(fn)
    assert is_close(result.x, vanishing_point.x, rel_tol=1e-3, abs_tol=1e-3)
    assert is_close(result.y, vanishing_point.y, rel_tol=1e-3, abs_tol=1e-3)
