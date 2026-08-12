"""Tests for JudgmentWindow's scalar operators and start/end properties.

Both legs of run_and_validate execute the same property and operator bodies, so a defect symmetric in both,
such as `start` reading the perfect interval instead of the good one, survives the differential check. Every
assertion here is therefore against a hand-computed constant, and the interval endpoints are dyadic so the
expectations are exact in both f32 and f64.
"""

from sonolus.script.array import Array
from sonolus.script.bucket import JudgmentWindow
from sonolus.script.interval import Interval
from tests.script.conftest import run_and_validate


def _window() -> JudgmentWindow:
    return JudgmentWindow(
        perfect=Interval(-0.25, 0.25),
        great=Interval(-0.5, 0.5),
        good=Interval(-0.75, 0.75),
    )


def test_start_and_end_read_the_good_interval():
    def fn():
        w = _window()
        return Array(w.start, w.end)

    assert run_and_validate(fn) == Array(-0.75, 0.75)


def test_add_shifts_all_three_intervals():
    def fn():
        w = _window() + 0.5
        return Array(
            w.perfect.start,
            w.perfect.end,
            w.great.start,
            w.great.end,
            w.good.start,
            w.good.end,
        )

    assert run_and_validate(fn) == Array(0.25, 0.75, 0.0, 1.0, -0.25, 1.25)


def test_mul_scales_start_and_end():
    def fn():
        w = _window() * 4
        return Array(w.start, w.end)

    assert run_and_validate(fn) == Array(-3.0, 3.0)
