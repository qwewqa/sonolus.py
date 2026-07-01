"""Dual-run differential tests for ops that were previously unimplemented in the oracle.

Before the M0 oracle fixes, ``Op.Ease*`` and ``Op.Judge`` raised ``NotImplementedError`` in
``sonolus.backend.interpret`` and ``Op.Rem`` used the wrong (IEEE) remainder. These tests drive
the ops through the full compile + optimize + interpret pipeline via ``run_and_validate``, which
compares the Python reference against the interpreted compiled output at every optimization level.

Note on the closure trick: ``run_and_validate`` also runs a variant that rewrites every closure
cell of the traced function into a runtime ROM read, which forces operands to be non-constant so
that ``const_eval`` / SCCP cannot fold the op away. To keep that path valid, the traced closures
capture only numeric values; native functions are referenced as module globals or passed as a
default argument (defaults are not closure cells).
"""

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from sonolus.script import easing
from sonolus.script.bucket import Judgment, JudgmentWindow
from sonolus.script.internal.math_impls import _remainder  # noqa: PLC2701
from sonolus.script.interval import Interval
from tests.script.conftest import run_and_validate

finite_floats = st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False)
unit_floats = st.floats(min_value=-0.5, max_value=1.5, allow_nan=False, allow_infinity=False)
divisor_floats = finite_floats.filter(lambda v: abs(v) > 1e-6)

# Below-range, endpoints, either side of the 0.5 split, and above-range.
EASE_GRID = [-0.5, 0.0, 0.3, 0.6, 1.0, 1.5]

# A representative easing function per distinct body shape (monomial, mirrored, piecewise
# in-out / out-in, polynomial-with-constants, sqrt, and the elastic/expo ``{0, 1}`` branches).
REPRESENTATIVE_EASE = [
    easing.ease_in_cubic,
    easing.ease_out_quart,
    easing.ease_in_out_cubic,
    easing.ease_out_in_quint,
    easing.ease_in_back,
    easing.ease_out_in_back,
    easing.ease_in_out_circ,
    easing.ease_in_elastic,
    easing.ease_in_out_elastic,
    easing.ease_out_in_elastic,
    easing.ease_out_expo,
    easing.ease_out_in_sine,
]


@pytest.mark.parametrize("ease_func", REPRESENTATIVE_EASE, ids=lambda f: f.__name__)
@pytest.mark.parametrize("x", EASE_GRID)
def test_ease_dual_run(ease_func, x):
    # `_ease` is a default argument (not a closure cell); only `x` is captured.
    def fn(_ease=ease_func):
        return _ease(x)

    run_and_validate(fn)


@settings(max_examples=25)
@given(x=unit_floats)
def test_ease_in_back_hypothesis(x):
    def fn():
        return easing.ease_in_back(x)

    run_and_validate(fn)


@settings(max_examples=25)
@given(x=unit_floats)
def test_ease_out_in_elastic_hypothesis(x):
    def fn():
        return easing.ease_out_in_elastic(x)

    run_and_validate(fn)


# JudgmentWindow with perfect [-0.05, 0.05], great [-0.1, 0.1], good [-0.15, 0.15].
JUDGE_CASES = [
    (0.0, 0.0, Judgment.PERFECT),
    (0.03, 0.0, Judgment.PERFECT),
    (0.05, 0.0, Judgment.PERFECT),  # perfect upper edge (inclusive)
    (-0.05, 0.0, Judgment.PERFECT),  # perfect lower edge (inclusive)
    (0.08, 0.0, Judgment.GREAT),
    (0.1, 0.0, Judgment.GREAT),  # great upper edge (inclusive)
    (0.13, 0.0, Judgment.GOOD),
    (0.15, 0.0, Judgment.GOOD),  # good upper edge (inclusive)
    (0.2, 0.0, Judgment.MISS),
    (-0.2, 0.0, Judgment.MISS),
    (0.5, 0.47, Judgment.PERFECT),  # diff = 0.03 -> perfect (source - target)
]


@pytest.mark.parametrize(("actual", "target", "expected"), JUDGE_CASES)
def test_judgment_window_judge_dual_run(actual, target, expected):
    def fn():
        window = JudgmentWindow(
            perfect=Interval(-0.05, 0.05),
            great=Interval(-0.1, 0.1),
            good=Interval(-0.15, 0.15),
        )
        return window.judge(actual, target)

    result = run_and_validate(fn)
    assert result == expected


REMAINDER_CASES = [
    (5.0, 3.0),
    (-5.0, 3.0),  # negative dividend
    (-6.0, 3.0),  # negative dividend, zero remainder
    (5.0, -3.0),
    (-7.5, 2.0),
    (7.5, 2.0),
]


@pytest.mark.parametrize(("a", "b"), REMAINDER_CASES)
def test_remainder_dual_run(a, b):
    # `_rem` is a default argument; a and b are numeric closure cells (runtime in the ROM path).
    def fn(_rem=_remainder):
        return _rem(a, b)

    result = run_and_validate(fn)
    assert result == math.copysign(abs(a) % abs(b), a)


@settings(max_examples=25)
@given(a=finite_floats, b=divisor_floats)
def test_remainder_hypothesis(a, b):
    def fn(_rem=_remainder):
        return _rem(a, b)

    run_and_validate(fn)
