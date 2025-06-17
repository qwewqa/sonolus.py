# ruff: noqa: E741
import itertools
from math import pi

from hypothesis import assume, given
from hypothesis import strategies as st

from sonolus.script.quad import Quad, Rect
from sonolus.script.vec import Vec2
from tests.script.conftest import is_close, run_and_validate

floats = st.floats(min_value=-9, max_value=9, allow_nan=False, allow_infinity=False)
nonzero_floats = floats.filter(lambda x: abs(x) > 1e-2)


@st.composite
def vecs(draw):
    x = draw(floats)
    y = draw(floats)
    return Vec2(x, y)


def _area(points: list[Vec2]) -> float:
    return 0.5 * abs(sum(p.x * q.y - p.y * q.x for p, q in zip(points, points[1:] + points[:1], strict=True)))


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
def test_quad_translate(quad, translation):
    def fn():
        return quad.translate(translation)

    result = run_and_validate(fn)
    assert is_close(result.bl, quad.bl + translation)
    assert is_close(result.tl, quad.tl + translation)
    assert is_close(result.tr, quad.tr + translation)
    assert is_close(result.br, quad.br + translation)


@given(
    quad=quads(),
    factor=vecs(),
)
def test_quad_scale(quad, factor):
    def fn():
        return quad.scale(factor)

    result = run_and_validate(fn)
    assert is_close(result.bl, quad.bl * factor)
    assert is_close(result.tl, quad.tl * factor)
    assert is_close(result.tr, quad.tr * factor)
    assert is_close(result.br, quad.br * factor)


@given(
    quad=quads(),
    factor=vecs(),
    pivot=vecs(),
)
def test_quad_scale_about(quad, factor, pivot):
    def fn():
        return quad.scale_about(factor, pivot)

    result = run_and_validate(fn)
    assert is_close(result.bl, (quad.bl - pivot) * factor + pivot)
    assert is_close(result.tl, (quad.tl - pivot) * factor + pivot)
    assert is_close(result.tr, (quad.tr - pivot) * factor + pivot)
    assert is_close(result.br, (quad.br - pivot) * factor + pivot)


@given(
    quad=quads(),
    angle=floats,
)
def test_quad_rotate(quad, angle):
    def fn():
        return quad.rotate(angle)

    result = run_and_validate(fn)
    assert is_close(result.bl, quad.bl.rotate(angle))
    assert is_close(result.tl, quad.tl.rotate(angle))
    assert is_close(result.tr, quad.tr.rotate(angle))
    assert is_close(result.br, quad.br.rotate(angle))


@given(
    quad=quads(),
    angle=floats,
    pivot=vecs(),
)
def test_quad_rotate_about(quad, angle, pivot):
    def fn():
        return quad.rotate_about(angle, pivot)

    result = run_and_validate(fn)
    assert is_close(result.bl, quad.bl.rotate_about(angle, pivot))
    assert is_close(result.tl, quad.tl.rotate_about(angle, pivot))
    assert is_close(result.tr, quad.tr.rotate_about(angle, pivot))
    assert is_close(result.br, quad.br.rotate_about(angle, pivot))


@given(quad=quads())
def test_quad_center(quad):
    def fn():
        return quad.center

    result = run_and_validate(fn)
    expected = (quad.bl + quad.tr + quad.tl + quad.br) / 4
    assert is_close(result, expected)


def test_quad_permute():
    for rotation in range(-5, 6):
        quad = Quad(
            Vec2(-1, -1),
            Vec2(-1, 1),
            Vec2(1, 1),
            Vec2(1, -1),
        )

        def fn():
            return quad.permute(rotation)  # noqa: B023

        result = run_and_validate(fn)
        expected = quad.rotate(rotation * pi / 2)
        assert is_close(result.bl, expected.bl)
        assert is_close(result.tl, expected.tl)
        assert is_close(result.tr, expected.tr)
        assert is_close(result.br, expected.br)


# Rect Tests
@given(
    rect=rects(),
    translation=vecs(),
)
def test_rect_translate(rect, translation):
    def fn():
        return rect.translate(translation)

    result = run_and_validate(fn)
    assert is_close(result.t, rect.t + translation.y)
    assert is_close(result.r, rect.r + translation.x)
    assert is_close(result.b, rect.b + translation.y)
    assert is_close(result.l, rect.l + translation.x)


@given(
    rect=rects(),
    factor=vecs(),
)
def test_rect_scale(rect, factor):
    def fn():
        return rect.scale(factor)

    result = run_and_validate(fn)
    assert is_close(result.t, rect.t * factor.y)
    assert is_close(result.r, rect.r * factor.x)
    assert is_close(result.b, rect.b * factor.y)
    assert is_close(result.l, rect.l * factor.x)


@given(
    rect=rects(),
    factor=vecs(),
    pivot=vecs(),
)
def test_rect_scale_about(rect, factor, pivot):
    def fn():
        return rect.scale_about(factor, pivot)

    result = run_and_validate(fn)
    assert is_close(result.t, (rect.t - pivot.y) * factor.y + pivot.y)
    assert is_close(result.r, (rect.r - pivot.x) * factor.x + pivot.x)
    assert is_close(result.b, (rect.b - pivot.y) * factor.y + pivot.y)
    assert is_close(result.l, (rect.l - pivot.x) * factor.x + pivot.x)


@given(
    rect=rects(),
    expansion=vecs(),
)
def test_rect_expand(rect, expansion):
    def fn():
        return rect.expand(expansion)

    result = run_and_validate(fn)
    assert is_close(result.t, rect.t + expansion.y)
    assert is_close(result.r, rect.r + expansion.x)
    assert is_close(result.b, rect.b - expansion.y)
    assert is_close(result.l, rect.l - expansion.x)


@given(
    rect=rects(),
    point=vecs(),
)
def test_rect_contains_point(rect, point):
    def fn():
        return rect.contains_point(point)

    result = run_and_validate(fn)
    expected = rect.l <= point.x <= rect.r and rect.b <= point.y <= rect.t
    assert is_close(result, expected)


@given(
    center=vecs(),
    dimensions=vecs(),
)
def test_rect_from_center(center, dimensions):
    def fn():
        return Rect.from_center(center, dimensions)

    result = run_and_validate(fn)
    assert is_close(result.center.x, center.x)
    assert is_close(result.center.y, center.y)
    assert is_close(result.w, dimensions.x)
    assert is_close(result.h, dimensions.y)


@given(rect=rects())
def test_rect_as_quad(rect):
    def fn():
        return rect.as_quad()

    result = run_and_validate(fn)
    assert is_close(result.bl, rect.bl)
    assert is_close(result.tl, rect.tl)
    assert is_close(result.tr, rect.tr)
    assert is_close(result.br, rect.br)


@st.composite
def quad_and_point(draw):
    quad = draw(quads())
    min_x = min(quad.bl.x, quad.tl.x, quad.tr.x, quad.br.x)
    max_x = max(quad.bl.x, quad.tl.x, quad.tr.x, quad.br.x)
    min_y = min(quad.bl.y, quad.tl.y, quad.tr.y, quad.br.y)
    max_y = max(quad.bl.y, quad.tl.y, quad.tr.y, quad.br.y)
    x = draw(st.floats(min_value=min_x, max_value=max_x))
    y = draw(st.floats(min_value=min_y, max_value=max_y))
    point = Vec2(x, y)
    return quad, point


@given(quad_point=quad_and_point())
def test_quad_contains_point(quad_point):
    quad, point = quad_point

    def fn():
        return quad.contains_point(point)

    # Don't have an easy way to validate the correct solution
    # But this still checks that the compiled and Python versions are consistent
    run_and_validate(fn)
