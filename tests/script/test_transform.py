from datetime import timedelta
from math import cos, sin

from hypothesis import assume, given, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from sonolus.script.transform import Transform2d
from sonolus.script.vec import Vec2
from tests.script.conftest import is_close, validate_dual_run

# Use smaller limits to avoid precision issues.
# In practice, it's rare to see large arguments anyway
floats = st.floats(min_value=-9, max_value=9, allow_nan=False, allow_infinity=False)
nonzero_floats = floats.filter(lambda x: abs(x) > 1e-2)


@st.composite
def vecs(draw):
    x = draw(floats)
    y = draw(floats)
    return Vec2(x, y)


@st.composite
def vecs_nonzero(draw):
    x = draw(nonzero_floats)
    y = draw(nonzero_floats)
    return Vec2(x, y)


@given(
    v=vecs(),
    t=vecs(),
)
@settings(deadline=timedelta(seconds=2))
def test_translate(v, t):
    def fn():
        transform = Transform2d.new().translate(v)
        return transform.transform_vec(t)

    assert validate_dual_run(fn) == v + t


@given(
    v=vecs(),
    factor=vecs(),
)
@settings(deadline=timedelta(seconds=2))
def test_scale(v, factor):
    def fn():
        transform = Transform2d.new().scale(factor)
        return transform.transform_vec(v)

    assert validate_dual_run(fn) == v * factor


@given(
    v=vecs(),
    factor=vecs(),
    pivot=vecs(),
)
@settings(deadline=timedelta(seconds=2))
def test_scale_around(v, factor, pivot):
    def fn():
        transform = Transform2d.new().scale_about(factor, pivot)
        return transform.transform_vec(v)

    result = validate_dual_run(fn)
    assert is_close(result.x, pivot.x + (v.x - pivot.x) * factor.x, rel_tol=1e-6, abs_tol=1e-6)
    assert is_close(result.y, pivot.y + (v.y - pivot.y) * factor.y, rel_tol=1e-6, abs_tol=1e-6)


@given(
    v=vecs(),
    angle=floats,
)
@settings(deadline=timedelta(seconds=2))
def test_rotate(v, angle):
    def fn():
        transform = Transform2d.new().rotate(angle)
        return transform.transform_vec(v)

    assert validate_dual_run(fn) == v.rotate(angle)


@given(
    v=vecs(),
    angle=floats,
    pivot=vecs(),
)
@settings(deadline=timedelta(seconds=2))
def test_rotate_around(v, angle, pivot):
    def fn():
        transform = Transform2d.new().rotate_about(angle, pivot)
        return transform.transform_vec(v)

    result = validate_dual_run(fn)
    assert is_close(
        result.x, pivot.x + (v.x - pivot.x) * cos(angle) - (v.y - pivot.y) * sin(angle), rel_tol=1e-6, abs_tol=1e-6
    )
    assert is_close(
        result.y, pivot.y + (v.x - pivot.x) * sin(angle) + (v.y - pivot.y) * cos(angle), rel_tol=1e-6, abs_tol=1e-6
    )


@given(
    v=vecs(),
    m=floats,
)
@settings(deadline=timedelta(seconds=2))
def test_shear_x(v, m):
    def fn():
        transform = Transform2d.new().shear_x(m)
        return transform.transform_vec(v)

    assert validate_dual_run(fn) == Vec2(v.x + v.y * m, v.y)


@given(
    v=vecs(),
    m=floats,
)
@settings(deadline=timedelta(seconds=2))
def test_shear_y(v, m):
    def fn():
        transform = Transform2d.new().shear_y(m)
        return transform.transform_vec(v)

    assert validate_dual_run(fn) == Vec2(v.x, v.x * m + v.y)


@given(
    v_x=floats,
    foreground_y=floats,
    vanishing_point=vecs(),
)
@settings(deadline=timedelta(seconds=2))
def test_perspective_y_at_foreground(v_x, foreground_y, vanishing_point):
    v = Vec2(v_x, 0)
    assume(abs(foreground_y - vanishing_point.y) > 1e-2)

    def fn():
        transform = Transform2d.new().perspective_y(foreground_y, vanishing_point)
        return transform.transform_vec(v)

    result = validate_dual_run(fn)
    assert is_close(result.x, v_x, rel_tol=1e-4, abs_tol=1e-4)
    assert is_close(result.y, foreground_y, rel_tol=1e-4, abs_tol=1e-4)


@given(
    v_x=floats,
    foreground_y=floats,
    vanishing_point=vecs(),
)
@settings(deadline=timedelta(seconds=2))
def test_perspective_y_at_infinity(v_x, foreground_y, vanishing_point):
    v = Vec2(v_x, (vanishing_point.y - foreground_y) * 1e6)  # Close enough to infinity for testing
    assume(abs(foreground_y - vanishing_point.y) > 1e-2)

    def fn():
        transform = Transform2d.new().perspective_y(foreground_y, vanishing_point)
        return transform.transform_vec(v)

    result = validate_dual_run(fn)
    assert is_close(result.x, vanishing_point.x, rel_tol=1e-3, abs_tol=1e-3)
    assert is_close(result.y, vanishing_point.y, rel_tol=1e-3, abs_tol=1e-3)


@given(
    v_y=floats,
    foreground_x=floats,
    vanishing_point=vecs(),
)
@settings(deadline=timedelta(seconds=2))
def test_perspective_x_at_foreground(v_y, foreground_x, vanishing_point):
    v = Vec2(0, v_y)
    assume(abs(foreground_x - vanishing_point.x) > 1e-2)

    def fn():
        transform = Transform2d.new().perspective_x(foreground_x, vanishing_point)
        return transform.transform_vec(v)

    result = validate_dual_run(fn)
    assert is_close(result.x, foreground_x, rel_tol=1e-4, abs_tol=1e-4)
    assert is_close(result.y, v_y, rel_tol=1e-4, abs_tol=1e-4)


@given(
    v_y=floats,
    foreground_x=floats,
    vanishing_point=vecs(),
)
@settings(deadline=timedelta(seconds=2))
def test_perspective_x_at_infinity(v_y, foreground_x, vanishing_point):
    v = Vec2((vanishing_point.x - foreground_x) * 1e6, v_y)
    assume(abs(foreground_x - vanishing_point.x) > 1e-2)

    def fn():
        transform = Transform2d.new().perspective_x(foreground_x, vanishing_point)
        return transform.transform_vec(v)

    result = validate_dual_run(fn)
    assert is_close(result.x, vanishing_point.x, rel_tol=1e-3, abs_tol=1e-3)
    assert is_close(result.y, vanishing_point.y, rel_tol=1e-3, abs_tol=1e-3)


class TransformInverse(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.transform = Transform2d.new()
        self.inverse = Transform2d.new()

        # Some operations can lead to compounding precision error, so we limit the count
        self.scale_count = 0
        self.shear_count = 0
        self.perspective_count = 0

    @rule(translation=vecs())
    def translate(self, translation):
        self.transform = self.transform.translate(translation)
        self.inverse = Transform2d.new().translate(-translation).compose(self.inverse)

    @precondition(lambda self: self.scale_count < 2)
    @rule(factor=vecs_nonzero())
    def scale(self, factor):
        self.transform = self.transform.scale(factor)
        self.inverse = Transform2d.new().scale(Vec2.one() / factor).compose(self.inverse)
        self.scale_count += 1

    @precondition(lambda self: self.scale_count < 2)
    @rule(factor=vecs_nonzero(), pivot=vecs())
    def scale_about(self, factor, pivot):
        self.transform = self.transform.scale_about(factor, pivot)
        self.inverse = Transform2d.new().scale_about(Vec2.one() / factor, pivot).compose(self.inverse)
        self.scale_count += 1

    @rule(angle=floats)
    def rotate(self, angle):
        self.transform = self.transform.rotate(angle)
        self.inverse = Transform2d.new().rotate(-angle).compose(self.inverse)

    @rule(angle=floats, pivot=vecs())
    def rotate_about(self, angle, pivot):
        self.transform = self.transform.rotate_about(angle, pivot)
        self.inverse = Transform2d.new().rotate_about(-angle, pivot).compose(self.inverse)

    @precondition(lambda self: self.shear_count < 2)
    @rule(m=floats)
    def shear_x(self, m):
        self.transform = self.transform.shear_x(m)
        self.inverse = Transform2d.new().shear_x(-m).compose(self.inverse)
        self.shear_count += 1

    @precondition(lambda self: self.shear_count < 2)
    @rule(m=floats)
    def shear_y(self, m):
        self.transform = self.transform.shear_y(m)
        self.inverse = Transform2d.new().shear_y(-m).compose(self.inverse)
        self.shear_count += 1

    @precondition(lambda self: self.perspective_count < 2)
    @rule(y=nonzero_floats)
    def perspective_vanish_y(self, y):
        self.transform = self.transform.simple_perspective_y(y)
        self.inverse = Transform2d.new().simple_perspective_y(-y).compose(self.inverse)
        self.perspective_count += 1

    @precondition(lambda self: self.perspective_count < 2)
    @rule(foreground_y=floats, vanishing_point=vecs())
    def perspective_y(self, foreground_y, vanishing_point):
        assume(abs(foreground_y - vanishing_point.y) > 1e-2)
        self.transform = self.transform.perspective_y(foreground_y, vanishing_point)
        self.inverse = Transform2d.new().inverse_perspective_y(foreground_y, vanishing_point).compose(self.inverse)
        self.perspective_count += 1

    @precondition(lambda self: self.perspective_count < 2)
    @rule(foreground_x=floats, vanishing_point=vecs())
    def perspective_x(self, foreground_x, vanishing_point):
        assume(abs(foreground_x - vanishing_point.x) > 1e-2)
        self.transform = self.transform.perspective_x(foreground_x, vanishing_point)
        self.inverse = Transform2d.new().inverse_perspective_x(foreground_x, vanishing_point).compose(self.inverse)
        self.perspective_count += 1

    @invariant()
    def inverse_cancels(self):
        combo = self.transform.compose(self.inverse).normalize()
        abs_tol = 1e-2
        assert is_close(combo.a00, 1, abs_tol=abs_tol)
        assert is_close(combo.a01, 0, abs_tol=abs_tol)
        assert is_close(combo.a02, 0, abs_tol=abs_tol)
        assert is_close(combo.a10, 0, abs_tol=abs_tol)
        assert is_close(combo.a11, 1, abs_tol=abs_tol)
        assert is_close(combo.a12, 0, abs_tol=abs_tol)
        assert is_close(combo.a20, 0, abs_tol=abs_tol)
        assert is_close(combo.a21, 0, abs_tol=abs_tol)
        assert is_close(combo.a22, 1, abs_tol=abs_tol)


TestTransformInverse = TransformInverse.TestCase
