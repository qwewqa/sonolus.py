# ruff: noqa: E741
import itertools
import math
from datetime import timedelta

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from sonolus.script.quad import Quad, Rect
from sonolus.script.vec import Vec2
from tests.script.conftest import is_close, validate_dual_run

floats = st.floats(min_value=-9, max_value=9, allow_nan=False, allow_infinity=False)
nonzero_floats = floats.filter(lambda x: abs(x) > 1e-2)


@st.composite
def vecs(draw):
    x = draw(floats)
    y = draw(floats)
    return Vec2(x, y)


def _area(points: list[Vec2]) -> float:
    return 0.5 * abs(sum(p.x * q.y - p.y * q.x for p, q in zip(points, points[1:] + points[:1], strict=False)))


@st.composite
def quads(draw):
    points = [draw(vecs()) for _ in range(4)]
    for p1, p2 in itertools.combinations(points, 2):
        assume((p1 - p2).magnitude > 1e-2)
    centroid = sum(points, Vec2(0, 0)) / 4
    points = sorted(points, key=lambda p: (p - centroid).angle)
    assume(_area(points) > 1e-2)
    return Quad(*points)


@st.composite
def rects(draw):
    l = draw(floats)
    r = draw(floats)
    if l > r:
        l, r = r, l

    b = draw(floats)
    t = draw(floats)
    if b > t:
        b, t = t, b

    return Rect(t=t, r=r, b=b, l=l)


@given(
    quad=quads(),
    translation=vecs(),
)
@settings(deadline=timedelta(seconds=2))
def test_quad_translate(quad, translation):
    def fn():
        return quad.translate(translation)

    result = validate_dual_run(fn)
    assert is_close(result.bl, quad.bl + translation)
    assert is_close(result.tl, quad.tl + translation)
    assert is_close(result.tr, quad.tr + translation)
    assert is_close(result.br, quad.br + translation)


@given(
    quad=quads(),
    factor=vecs(),
)
@settings(deadline=timedelta(seconds=2))
def test_quad_scale(quad, factor):
    def fn():
        return quad.scale(factor)

    result = validate_dual_run(fn)
    assert is_close(result.bl, quad.bl * factor)
    assert is_close(result.tl, quad.tl * factor)
    assert is_close(result.tr, quad.tr * factor)
    assert is_close(result.br, quad.br * factor)


@given(
    quad=quads(),
    factor=vecs(),
    pivot=vecs(),
)
@settings(deadline=timedelta(seconds=2))
def test_quad_scale_about(quad, factor, pivot):
    def fn():
        return quad.scale_about(factor, pivot)

    result = validate_dual_run(fn)
    assert is_close(result.bl, (quad.bl - pivot) * factor + pivot)
    assert is_close(result.tl, (quad.tl - pivot) * factor + pivot)
    assert is_close(result.tr, (quad.tr - pivot) * factor + pivot)
    assert is_close(result.br, (quad.br - pivot) * factor + pivot)


@given(
    quad=quads(),
    angle=floats,
)
@settings(deadline=timedelta(seconds=2))
def test_quad_rotate(quad, angle):
    def fn():
        return quad.rotate(angle)

    result = validate_dual_run(fn)
    assert is_close(result.bl, quad.bl.rotate(angle))
    assert is_close(result.tl, quad.tl.rotate(angle))
    assert is_close(result.tr, quad.tr.rotate(angle))
    assert is_close(result.br, quad.br.rotate(angle))


@given(
    quad=quads(),
    angle=floats,
    pivot=vecs(),
)
@settings(deadline=timedelta(seconds=2))
def test_quad_rotate_about(quad, angle, pivot):
    def fn():
        return quad.rotate_about(angle, pivot)

    result = validate_dual_run(fn)
    assert is_close(result.bl, quad.bl.rotate_about(angle, pivot))
    assert is_close(result.tl, quad.tl.rotate_about(angle, pivot))
    assert is_close(result.tr, quad.tr.rotate_about(angle, pivot))
    assert is_close(result.br, quad.br.rotate_about(angle, pivot))


@given(quad=quads())
@settings(deadline=timedelta(seconds=2))
def test_quad_center(quad):
    def fn():
        return quad.center

    result = validate_dual_run(fn)
    expected = (quad.bl + quad.tr + quad.tl + quad.br) / 4
    assert is_close(result, expected)


# Rect Tests
@given(
    rect=rects(),
    translation=vecs(),
)
@settings(deadline=timedelta(seconds=2))
def test_rect_translate(rect, translation):
    def fn():
        return rect.translate(translation)

    result = validate_dual_run(fn)
    assert is_close(result.t, rect.t + translation.y)
    assert is_close(result.r, rect.r + translation.x)
    assert is_close(result.b, rect.b + translation.y)
    assert is_close(result.l, rect.l + translation.x)


@given(
    rect=rects(),
    factor=vecs(),
)
@settings(deadline=timedelta(seconds=2))
def test_rect_scale(rect, factor):
    def fn():
        return rect.scale(factor)

    result = validate_dual_run(fn)
    assert is_close(result.t, rect.t * factor.y)
    assert is_close(result.r, rect.r * factor.x)
    assert is_close(result.b, rect.b * factor.y)
    assert is_close(result.l, rect.l * factor.x)


@given(
    rect=rects(),
    factor=vecs(),
    pivot=vecs(),
)
@settings(deadline=timedelta(seconds=2))
def test_rect_scale_about(rect, factor, pivot):
    def fn():
        return rect.scale_about(factor, pivot)

    result = validate_dual_run(fn)
    assert is_close(result.t, (rect.t - pivot.y) * factor.y + pivot.y)
    assert is_close(result.r, (rect.r - pivot.x) * factor.x + pivot.x)
    assert is_close(result.b, (rect.b - pivot.y) * factor.y + pivot.y)
    assert is_close(result.l, (rect.l - pivot.x) * factor.x + pivot.x)


@given(
    rect=rects(),
    expansion=vecs(),
)
@settings(deadline=timedelta(seconds=2))
def test_rect_expand(rect, expansion):
    def fn():
        return rect.expand(expansion)

    result = validate_dual_run(fn)
    assert is_close(result.t, rect.t + expansion.y)
    assert is_close(result.r, rect.r + expansion.x)
    assert is_close(result.b, rect.b - expansion.y)
    assert is_close(result.l, rect.l - expansion.x)


@given(
    rect=rects(),
    point=vecs(),
)
@settings(deadline=timedelta(seconds=2))
def test_rect_contains_point(rect, point):
    def fn():
        return rect.contains_point(point)

    result = validate_dual_run(fn)
    expected = rect.l <= point.x <= rect.r and rect.b <= point.y <= rect.t
    assert is_close(result, expected)


@given(
    center=vecs(),
    dimensions=vecs(),
)
@settings(deadline=timedelta(seconds=2))
def test_rect_from_center(center, dimensions):
    def fn():
        return Rect.from_center(center, dimensions)

    result = validate_dual_run(fn)
    assert is_close(result.center.x, center.x)
    assert is_close(result.center.y, center.y)
    assert is_close(result.w, dimensions.x)
    assert is_close(result.h, dimensions.y)


@given(rect=rects())
@settings(deadline=timedelta(seconds=2))
def test_rect_as_quad(rect):
    def fn():
        return rect.as_quad()

    result = validate_dual_run(fn)
    assert is_close(result.bl, rect.bl)
    assert is_close(result.tl, rect.tl)
    assert is_close(result.tr, rect.tr)
    assert is_close(result.br, rect.br)


@st.composite
def points_with_expected_containment(draw):
    """Generate a point and whether it should be inside a given quad."""
    quad = draw(quads())

    is_inside = draw(st.booleans())

    if is_inside:
        margin = 1.001
        a = draw(st.floats(min_value=0, max_value=1)) + margin
        b = draw(st.floats(min_value=0, max_value=1)) + margin
        c = draw(st.floats(min_value=0, max_value=1)) + margin
        d = draw(st.floats(min_value=0, max_value=1)) + margin
        total = a + b + c + d
        a /= total
        b /= total
        c /= total
        d /= total
        # Not totally uniform, but good enough for testing
        point = quad.bl * a + quad.tl * b + quad.tr * c + quad.br * d
    else:
        diam = max((quad.tl - quad.br).magnitude, (quad.tr - quad.bl).magnitude)
        angle = draw(st.floats(min_value=0, max_value=2 * math.pi))
        scale = draw(st.floats(min_value=1, max_value=2))
        point = quad.center + Vec2(math.cos(angle), math.sin(angle)) * scale * (diam + 0.001)

    return quad, point, is_inside


@given(quad_point_expected=points_with_expected_containment())
@settings(deadline=timedelta(seconds=2))
def test_quad_contains_point(quad_point_expected):
    quad, point, expected = quad_point_expected

    def fn():
        return quad.contains_point(point)

    result = validate_dual_run(fn)
    assert result == expected, f"Expected point {point} to be {'inside' if expected else 'outside'} quad {quad}"
