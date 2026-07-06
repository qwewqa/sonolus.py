"""Tests for Python builtin functions (min, max, ...) as implemented for sonolus scripts."""

from sonolus.script.internal.builtin_impls import _max, _min  # noqa: PLC2701
from sonolus.script.num import Num


def test_max_comptime_honors_key():
    assert _max(Num(1.0), Num(3.0), key=lambda v: -v) == 1


def test_min_comptime_honors_key():
    assert _min(Num(1.0), Num(3.0), key=lambda v: -v) == 3
