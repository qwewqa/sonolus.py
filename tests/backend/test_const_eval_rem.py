"""Tests for the M0 fixes to the SCCP constant-fold kernel in constant_evaluation.py.

Previously ``Op.Rem`` folded via ``smath.remainder`` (a nonexistent attribute -> latent
AttributeError, so this fold never fired), and ``Op.Rem`` / ``Op.Mod`` folds could raise an
uncaught ``ZeroDivisionError`` on a zero divisor. The fix makes ``Rem`` use the sign-of-dividend
semantics of ``math_impls._remainder`` and makes both return not-a-constant when the divisor is 0
(mirroring ``Divide``).
"""

import math

import pytest

import sonolus.script.internal.math_impls as smath
from sonolus.backend.ir import IRConst, IRPureInstr
from sonolus.backend.ops import Op
from sonolus.backend.optimize.constant_evaluation import NAC, SparseConditionalConstantPropagation


def fold(op: Op, *values: float):
    sccp = SparseConditionalConstantPropagation()
    stmt = IRPureInstr(op=op, args=[IRConst(v) for v in values])
    return sccp.evaluate_stmt(stmt, {})


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (5.0, 3.0, 2.0),
        (-5.0, 3.0, -2.0),  # sign of the dividend
        (5.0, -3.0, 2.0),
        (-5.0, -3.0, -2.0),
        (7.5, 2.0, 1.5),
        (-7.5, 2.0, -1.5),
    ],
)
def test_rem_fold_sign_of_dividend(a, b, expected):
    result = fold(Op.Rem, a, b)
    assert result == expected
    # Must agree with the frontend's canonical _remainder implementation.
    assert result == smath._remainder.__wrapped__(a, b)


def test_rem_fold_negative_zero_remainder():
    result = fold(Op.Rem, -6.0, 3.0)
    assert result == 0.0
    assert math.copysign(1.0, result) == -1.0  # -0.0 preserved


def test_rem_fold_zero_divisor_is_nac():
    # Previously would raise (AttributeError / ZeroDivisionError); now returns not-a-constant.
    assert fold(Op.Rem, 5.0, 0.0) is NAC
    assert fold(Op.Rem, -5.0, 0.0) is NAC


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (5.0, 3.0, 2.0),
        (-5.0, 3.0, 1.0),  # Python %: sign of the divisor (Op.Mod semantics)
        (5.0, -3.0, -1.0),
    ],
)
def test_mod_fold(a, b, expected):
    assert fold(Op.Mod, a, b) == expected


def test_mod_fold_zero_divisor_is_nac():
    assert fold(Op.Mod, 5.0, 0.0) is NAC
    assert fold(Op.Mod, 0.0, 0.0) is NAC
