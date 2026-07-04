"""Differential tests for the C constant-fold kernels (kernels.pyx).

For every foldable op, Hypothesis-generated
operands (finite floats + special values: NaN, +/-Inf, +/-0.0, integers,
denormals) assert that ``kernels.fold(op, args)`` matches the oracle
``Interpreter().run(FunctionNode(op, tuple(args)))`` **bit-for-bit** -- and that
when the oracle would raise (ZeroDivisionError / ValueError / OverflowError /
AssertionError) or return a *complex* (``Power(-2, 0.5)``), ``fold`` returns
``None`` (== FOLD_NOT_CONSTANT).

NaN caveat (documented, matches the const table's NaN canonicalization): when
the oracle returns any NaN, any NaN bit-pattern from ``fold`` is accepted; every
other value is compared by exact ``struct.pack`` bits (so -0.0 != +0.0).

Plus directed edge-case tables per op family (round half-even, Rem/Mod sign and
+/-0.0, Sign(+/-0/NaN), ease boundaries/clamping, Judge windows, Min/Max NaN and
tie order, And/Or value semantics, and the NOT_CONSTANT cases).
"""

from __future__ import annotations

import math
import struct

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from sonolus.backend._opt import kernels  # noqa: PLC2701
from sonolus.backend.interpret import Interpreter
from sonolus.backend.node import FunctionNode
from sonolus.backend.ops import Op

# --------------------------------------------------------------------------
# Arity table for every foldable op (used to draw the right operand count).
# --------------------------------------------------------------------------

_UNARY = {
    "Not", "Negate", "Abs", "Sign", "Ceil", "Floor", "Round", "Trunc", "Frac",
    "Sin", "Cos", "Tan", "Sinh", "Cosh", "Tanh", "Arcsin", "Arccos", "Arctan",
    "Log", "Degree", "Radian",
}  # fmt: skip
_BINARY = {
    "Equal", "NotEqual", "Greater", "GreaterOr", "Less", "LessOr", "And", "Or",
    "Add", "Subtract", "Multiply", "Divide", "Power", "Mod", "Rem", "Max", "Min", "Arctan2",
}  # fmt: skip
_TERNARY = {"Clamp", "Lerp", "LerpClamped", "Unlerp", "UnlerpClamped"}
_FIVE = {"Remap", "RemapClamped", "JudgeSimple"}
_EIGHT = {"Judge"}
_EASE = {op for op in Op if op.value.startswith("Ease")}
# The strict-select ops bypass the generic harness: their f32-exactness guard
# declines tests/keys the oracle evaluates fine, breaking check()'s
# fold-iff-oracle biconditional (and the four Switch* are variadic besides);
# they get guard-aware differential tests below.
_SELECT = {"If", "Switch", "SwitchWithDefault", "SwitchInteger", "SwitchIntegerWithDefault"}

ARITY: dict[Op, int] = {}
for _op in Op:
    if _op.value in _SELECT:
        continue
    if _op in _EASE or _op.value in _UNARY:
        ARITY[_op] = 1
    elif _op.value in _BINARY:
        ARITY[_op] = 2
    elif _op.value in _TERNARY:
        ARITY[_op] = 3
    elif _op.value in _FIVE:
        ARITY[_op] = 5
    elif _op.value in _EIGHT:
        ARITY[_op] = 8

FOLDABLE_OPS = sorted(ARITY, key=lambda o: o.value)

_ORACLE_RAISES = (ZeroDivisionError, ValueError, OverflowError, AssertionError)


def test_arity_table_covers_exactly_the_89_foldable_ops():
    # The fixed-arity table plus the variadic select ops partition the kernels'
    # compiled foldable table (89 ops total).
    ops = list(Op)
    foldable = {ops[i].value for i in kernels.foldable_op_ids()}
    assert len(foldable) == 89
    assert {op.value for op in ARITY}.isdisjoint(_SELECT)
    assert {op.value for op in ARITY} | _SELECT == foldable
    assert len(ARITY) == 84


# --------------------------------------------------------------------------
# Bit-exact comparison helpers.
# --------------------------------------------------------------------------


def _bits(x: float) -> bytes:
    return struct.pack(">d", float(x))


def same_bits(a, b) -> bool:
    a = float(a)
    b = float(b)
    if math.isnan(a) and math.isnan(b):
        return True
    return _bits(a) == _bits(b)


def run_oracle(op: Op, args):
    """Return ``("ok", value)`` or ``("raise", exc)``; value may be complex/int."""
    try:
        return ("ok", Interpreter().run(FunctionNode(op, tuple(args))))
    except _ORACLE_RAISES as e:
        return ("raise", e)


def check(op: Op, args) -> None:
    kind, value = run_oracle(op, args)
    got = kernels.fold(op, list(args))
    if kind == "raise":
        assert got is None, f"{op.value}{tuple(args)}: oracle raised {value!r} but fold={got!r}"
    elif isinstance(value, complex):
        assert got is None, f"{op.value}{tuple(args)}: oracle complex {value!r} but fold={got!r}"
    else:
        assert got is not None, f"{op.value}{tuple(args)}: oracle={value!r} but fold=None"
        assert same_bits(got, value), (
            f"{op.value}{tuple(args)}: fold={got!r} ({_bits(got).hex()}) != oracle={value!r} ({_bits(value).hex()})"
        )


# --------------------------------------------------------------------------
# Mixed operand strategy: finite floats, specials, integers, denormals.
# --------------------------------------------------------------------------

_SPECIALS = [
    0.0, -0.0, 1.0, -1.0, 2.0, -2.0, 0.5, -0.5, 2.5, -2.5, 3.5, -3.5,
    100.0, -100.0, math.inf, -math.inf, math.nan,
    1e308, -1e308, 1e-300, -1e-300,
    5e-324, -5e-324,          # smallest positive/negative denormals
    2.2250738585072014e-308,  # smallest normal
    math.pi, math.e, 0.1, -0.1,
]  # fmt: skip

_operand = st.one_of(
    st.sampled_from(_SPECIALS),
    st.floats(allow_nan=False, allow_infinity=False),
    st.floats(allow_nan=True, allow_infinity=True),
    st.integers(min_value=-1000, max_value=1000).map(float),
    st.floats(min_value=-3.0, max_value=3.0),  # dense over the ease/clamp/judge range
)


@pytest.mark.parametrize("op", FOLDABLE_OPS, ids=lambda o: o.value)
@given(data=st.data())
@settings(max_examples=200, deadline=None)
def test_fold_matches_oracle_bit_for_bit(op, data):
    args = [data.draw(_operand) for _ in range(ARITY[op])]
    check(op, args)


# --------------------------------------------------------------------------
# Directed edge-case tables.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("x", "expected"),
    [
        (0.5, 0.0),
        (1.5, 2.0),
        (2.5, 2.0),
        (3.5, 4.0),
        (4.5, 4.0),
        (-0.5, 0.0),
        (-1.5, -2.0),
        (-2.5, -2.0),
        (-3.5, -4.0),
        (0.49999999999999994, 0.0),
        (2.0, 2.0),
        (2.6, 3.0),
        (-2.6, -3.0),
        (0.0, 0.0),
        (-0.0, 0.0),
    ],
)
def test_round_half_even(x, expected):
    got = kernels.fold(Op.Round, [x])
    assert same_bits(got, expected), (x, got, expected)
    # And it agrees with the oracle bit-for-bit.
    check(Op.Round, [x])


def test_round_negative_half_is_positive_zero():
    got = kernels.fold(Op.Round, [-0.5])
    assert _bits(got) == _bits(0.0)  # +0.0, not -0.0 (oracle returns Python int 0)


@pytest.mark.parametrize("bad", [math.inf, -math.inf, math.nan])
@pytest.mark.parametrize("op", [Op.Round, Op.Ceil, Op.Floor, Op.Trunc])
def test_round_family_inf_nan_not_constant(op, bad):
    assert kernels.fold(op, [bad]) is None  # math.ceil/round(inf/nan) raises
    check(op, [bad])


@pytest.mark.parametrize(
    ("op", "x"),
    [
        (Op.Ceil, -0.3),
        (Op.Floor, -0.0),
        (Op.Trunc, -0.3),
    ],
)
def test_round_family_returns_positive_zero(op, x):
    # Oracle returns a Python int, so -0.0 never survives.
    got = kernels.fold(op, [x])
    assert got == 0.0
    assert _bits(got) == _bits(0.0)


@pytest.mark.parametrize(
    ("a", "b", "expected", "neg_zero"),
    [
        (5.0, 3.0, 2.0, False),
        (-5.0, 3.0, -2.0, False),
        (5.0, -3.0, 2.0, False),
        (-5.0, -3.0, -2.0, False),
        (6.0, 3.0, 0.0, False),  # +0.0
        (-6.0, 3.0, 0.0, True),  # -0.0 (sign of dividend)
        (6.0, -3.0, 0.0, False),  # +0.0 (sign of dividend)
        (-6.0, -3.0, 0.0, True),  # -0.0
    ],
)
def test_rem_sign_table(a, b, expected, neg_zero):
    got = kernels.fold(Op.Rem, [a, b])
    assert got == expected
    if expected == 0.0:  # sign bit only meaningful for the +-0.0 results
        assert math.copysign(1.0, got) == (-1.0 if neg_zero else 1.0)
    check(Op.Rem, [a, b])


@pytest.mark.parametrize(
    ("a", "b", "expected", "neg_zero"),
    [
        (6.0, 3.0, 0.0, False),  # +0.0 (sign of divisor)
        (-6.0, 3.0, 0.0, False),  # +0.0
        (6.0, -3.0, 0.0, True),  # -0.0 (sign of divisor)
        (-6.0, -3.0, 0.0, True),  # -0.0
        (7.0, 3.0, 1.0, False),
        (-7.0, 3.0, 2.0, False),  # Python %: sign of divisor
        (7.0, -3.0, -2.0, False),
    ],
)
def test_mod_sign_table(a, b, expected, neg_zero):
    got = kernels.fold(Op.Mod, [a, b])
    assert got == expected
    if expected == 0.0:
        assert math.copysign(1.0, got) == (-1.0 if neg_zero else 1.0)
    check(Op.Mod, [a, b])


def test_sign_bit_exact():
    assert kernels.fold(Op.Sign, [5.0]) == 1.0
    assert kernels.fold(Op.Sign, [-5.0]) == -1.0
    assert kernels.fold(Op.Sign, [1e-320]) == 1.0  # tiny denormal still positive
    pz = kernels.fold(Op.Sign, [0.0])
    nz = kernels.fold(Op.Sign, [-0.0])
    assert _bits(pz) == _bits(0.0)
    assert _bits(nz) == _bits(-0.0)
    assert math.isnan(kernels.fold(Op.Sign, [math.nan]))
    for x in (0.0, -0.0, math.nan, 5.0, -5.0):
        check(Op.Sign, [x])


def test_negate_and_multiply_preserve_negative_zero():
    assert _bits(kernels.fold(Op.Negate, [0.0])) == _bits(-0.0)
    assert _bits(kernels.fold(Op.Negate, [-0.0])) == _bits(0.0)
    assert _bits(kernels.fold(Op.Multiply, [-1.0, 0.0])) == _bits(-0.0)
    assert _bits(kernels.fold(Op.Add, [-0.0, -0.0])) == _bits(-0.0)
    assert _bits(kernels.fold(Op.Add, [-0.0, 0.0])) == _bits(0.0)


@pytest.mark.parametrize("op", sorted(_EASE, key=lambda o: o.value), ids=lambda o: o.value)
def test_ease_boundaries_and_clamping(op):
    # Out-of-range inputs clamp: ease(-5) == ease(0), ease(5) == ease(1).
    assert same_bits(kernels.fold(op, [-5.0]), kernels.fold(op, [0.0]))
    assert same_bits(kernels.fold(op, [5.0]), kernels.fold(op, [1.0]))
    # Endpoints and NaN (which clamps to 1.0) match the oracle bit-for-bit.
    for x in (-5.0, -0.0, 0.0, 0.25, 0.5, 0.75, 1.0, 5.0, math.inf, -math.inf, math.nan):
        check(op, [x])


# perfect [-1, 1], great [-2, 2], good [-3, 3]
_JUDGE_WINDOW = (-1.0, 1.0, -2.0, 2.0, -3.0, 3.0)


@pytest.mark.parametrize(
    ("diff", "expected"),
    [
        (0.0, 1.0),
        (1.0, 1.0),
        (-1.0, 1.0),
        (1.0000001, 2.0),
        (2.0, 2.0),
        (-2.0, 2.0),
        (2.0000001, 3.0),
        (3.0, 3.0),
        (-3.0, 3.0),
        (3.0000001, 0.0),
        (-3.0000001, 0.0),
    ],
)
def test_judge_window_edges(diff, expected):
    got = kernels.fold(Op.Judge, [diff, 0.0, *_JUDGE_WINDOW])
    assert got == expected
    check(Op.Judge, [diff, 0.0, *_JUDGE_WINDOW])


@pytest.mark.parametrize(
    ("diff", "expected"),
    [(0.0, 1.0), (1.0, 1.0), (-1.0, 1.0), (1.5, 2.0), (2.0, 2.0), (2.5, 3.0), (3.0, 3.0), (3.5, 0.0)],
)
def test_judge_simple_window_edges(diff, expected):
    got = kernels.fold(Op.JudgeSimple, [diff, 0.0, 1.0, 2.0, 3.0])
    assert got == expected
    check(Op.JudgeSimple, [diff, 0.0, 1.0, 2.0, 3.0])


def test_judge_simple_matches_judge_expansion():
    for diff in (-4.0, -2.5, -1.0, 0.0, 0.5, 1.0, 2.0, 2.5, 3.0, 4.0):
        simple = kernels.fold(Op.JudgeSimple, [diff, 0.0, 1.0, 2.0, 3.0])
        expanded = kernels.fold(Op.Judge, [diff, 0.0, -1.0, 1.0, -2.0, 2.0, -3.0, 3.0])
        assert simple == expanded


def test_min_max_nan_and_tie_order():
    # max(a, b) == b only if b > a; NaN comparisons are all false.
    assert math.isnan(kernels.fold(Op.Max, [math.nan, 5.0]))  # keep first (nan)
    assert kernels.fold(Op.Max, [5.0, math.nan]) == 5.0  # nan not > 5 -> keep 5
    assert math.isnan(kernels.fold(Op.Min, [math.nan, 5.0]))
    assert kernels.fold(Op.Min, [5.0, math.nan]) == 5.0
    # Signed-zero tie: keep the first argument.
    assert _bits(kernels.fold(Op.Max, [0.0, -0.0])) == _bits(0.0)
    assert _bits(kernels.fold(Op.Max, [-0.0, 0.0])) == _bits(-0.0)
    assert _bits(kernels.fold(Op.Min, [-0.0, 0.0])) == _bits(-0.0)
    for a, b in [(math.nan, 5.0), (5.0, math.nan), (0.0, -0.0), (-0.0, 0.0), (3.0, 3.0)]:
        check(Op.Max, [a, b])
        check(Op.Min, [a, b])


def test_clamp_is_max_a_min_b_x():
    # Clamp(x, a, b) = max(a, min(b, x)); note a=lo, b=hi. Even if a > b it follows the formula.
    assert kernels.fold(Op.Clamp, [5.0, 0.0, 1.0]) == 1.0
    assert kernels.fold(Op.Clamp, [-5.0, 0.0, 1.0]) == 0.0
    assert kernels.fold(Op.Clamp, [0.3, 0.0, 1.0]) == 0.3
    # a > b: max(2, min(1, x)) == 2 for any x <= 1.
    assert kernels.fold(Op.Clamp, [0.5, 2.0, 1.0]) == 2.0
    for x in (-5.0, 0.3, 5.0, math.nan):
        check(Op.Clamp, [x, 0.0, 1.0])


def test_and_or_value_semantics():
    # And returns the first zero arg, else the last; Or the first nonzero, else the last.
    assert kernels.fold(Op.And, [2.0, 3.0]) == 3.0
    assert kernels.fold(Op.And, [0.0, 3.0]) == 0.0
    assert kernels.fold(Op.And, [2.0, 0.0]) == 0.0
    assert kernels.fold(Op.Or, [0.0, 5.0]) == 5.0
    assert kernels.fold(Op.Or, [4.0, 9.0]) == 4.0
    assert kernels.fold(Op.Or, [0.0, 0.0]) == 0.0
    # NaN is truthy (NaN != 0).
    assert kernels.fold(Op.And, [math.nan, 5.0]) == 5.0
    assert math.isnan(kernels.fold(Op.Or, [math.nan, 5.0]))
    # n-ary value semantics (matches the oracle reduce loop).
    assert kernels.fold(Op.And, [1.0, 2.0, 3.0]) == 3.0
    assert kernels.fold(Op.And, [1.0, 0.0, 3.0]) == 0.0
    assert kernels.fold(Op.Or, [0.0, 0.0, 7.0]) == 7.0
    assert kernels.fold(Op.Or, [0.0, 6.0, 7.0]) == 6.0
    for args in ([2.0, 3.0], [0.0, 3.0], [math.nan, 5.0], [1.0, 2.0, 3.0], [0.0, 0.0, 7.0]):
        check(Op.And, args)
        check(Op.Or, args)


@pytest.mark.parametrize(
    ("op", "args"),
    [
        (Op.Divide, [1.0, 0.0]),
        (Op.Divide, [0.0, 0.0]),
        (Op.Divide, [1.0, -0.0]),
        (Op.Mod, [5.0, 0.0]),
        (Op.Rem, [5.0, 0.0]),
        (Op.Rem, [5.0, -0.0]),
        (Op.Power, [-2.0, 0.5]),  # complex
        (Op.Power, [0.0, -1.0]),  # ZeroDivisionError
        (Op.Power, [1e308, 2.0]),  # OverflowError
        (Op.Log, [-1.0]),
        (Op.Log, [0.0]),  # -inf singularity -> ValueError
        (Op.Arcsin, [2.0]),
        (Op.Arccos, [-2.0]),
        (Op.Sin, [math.inf]),
        (Op.Sinh, [1000.0]),  # overflow
        (Op.Unlerp, [1.0, 1.0, 0.5]),  # hi == lo
        (Op.UnlerpClamped, [1.0, 1.0, 0.5]),
        (Op.Remap, [1.0, 1.0, 0.0, 1.0, 0.5]),
        (Op.RemapClamped, [1.0, 1.0, 0.0, 1.0, 0.5]),
    ],
)
def test_not_constant_cases(op, args):
    assert kernels.fold(op, args) is None
    check(op, args)


@pytest.mark.parametrize(
    ("op", "args", "expected"),
    [
        (Op.Power, [-2.0, 3.0], -8.0),
        (Op.Power, [-2.0, 2.0], 4.0),
        (Op.Power, [2.0, 0.5], math.sqrt(2.0)),
        (Op.Power, [0.0, 0.0], 1.0),
        (Op.Power, [math.nan, 0.0], 1.0),  # anything ** 0 == 1
        (Op.Power, [1.0, math.nan], 1.0),  # 1 ** anything == 1
        (Op.Power, [4.0, 0.5], 2.0),
    ],
)
def test_power_special_cases(op, args, expected):
    got = kernels.fold(op, args)
    assert same_bits(got, expected), (args, got, expected)
    check(op, args)


def test_wrong_arity_is_not_constant():
    # The associative ops are binary in the mid-end; other arities do not fold here.
    assert kernels.fold(Op.Add, [1.0]) is None
    assert kernels.fold(Op.Add, [1.0, 2.0, 3.0]) is None
    assert kernels.fold(Op.Clamp, [1.0, 2.0]) is None
    assert kernels.fold(Op.Judge, [1.0]) is None
    assert kernels.fold(Op.EaseInSine, [1.0, 2.0]) is None
    # But binary Add does fold.
    assert kernels.fold(Op.Add, [1.0, 2.0]) == 3.0


# ==========================================================================
# Strict-select value ops: If / Switch / SwitchWithDefault / SwitchInteger /
# SwitchIntegerWithDefault. Differential vs the oracle, plus directed tables.
#
# The test and every examined key pass the kernel's f32 fold guard, so fold agrees
# with the f64 oracle bit-for-bit. Arm/key VALUES need no guard, so they are drawn
# from the full mixed operand pool (NaN/inf/-0.0 arms are returned verbatim).
# ==========================================================================

# f32-roundtrip-exact values that pass the fold guard on the test/keys (small ints
# and simple binary fractions inject into f32; +/-inf and +/-0.0 are exact; NaN
# folds THROUGH the guard). Small ints also make SwitchInteger select real branches
# and make Switch keys match the test.
_F32_EXACT = [
    0.0,
    -0.0,
    1.0,
    -1.0,
    2.0,
    -2.0,
    3.0,
    -3.0,
    4.0,
    5.0,
    6.0,
    8.0,
    16.0,
    0.5,
    -0.5,
    0.25,
    -0.25,
    1.5,
    2.5,
    100.0,
    -100.0,
    math.inf,
    -math.inf,
    math.nan,
]
_f32 = st.sampled_from(_F32_EXACT)


@given(test=_f32, t=_operand, f=_operand)
@settings(max_examples=400, deadline=None)
def test_if_matches_oracle(test, t, f):
    check(Op.If, [test, t, f])


@given(test=_f32, pairs=st.lists(st.tuples(_f32, _operand), min_size=0, max_size=12))
@settings(max_examples=400, deadline=None)
def test_switch_matches_oracle(test, pairs):
    # Odd arity: test + (key, value) pairs. max 12 pairs -> up to 25 args (>16).
    args = [test]
    for k, v in pairs:
        args += [k, v]
    check(Op.Switch, args)


@given(test=_f32, pairs=st.lists(st.tuples(_f32, _operand), min_size=1, max_size=12), default=_operand)
@settings(max_examples=400, deadline=None)
def test_switch_with_default_matches_oracle(test, pairs, default):
    # Even arity >= 4: test + (key, value) pairs + default. Exercises >16 args.
    args = [test]
    for k, v in pairs:
        args += [k, v]
    args.append(default)
    check(Op.SwitchWithDefault, args)


@given(test=_f32, branches=st.lists(_operand, min_size=1, max_size=20))
@settings(max_examples=400, deadline=None)
def test_switch_integer_matches_oracle(test, branches):
    # test + >=1 branches (up to 21 args, >16). test drawn from the exact pool so
    # integral in-range selectors hit a real branch and drift never rejects a fold.
    check(Op.SwitchInteger, [test, *branches])


@given(test=_f32, branches=st.lists(_operand, min_size=1, max_size=20), default=_operand)
@settings(max_examples=400, deadline=None)
def test_switch_integer_with_default_matches_oracle(test, branches, default):
    check(Op.SwitchIntegerWithDefault, [test, *branches, default])


def test_if_nan_and_negative_zero_truthiness():
    # NaN is truthy (NaN != 0.0 -> consequent); -0.0 is falsy (-0.0 == 0.0 -> alt).
    assert kernels.fold(Op.If, [math.nan, 10.0, 20.0]) == 10.0
    assert kernels.fold(Op.If, [-0.0, 10.0, 20.0]) == 20.0
    assert kernels.fold(Op.If, [0.0, 10.0, 20.0]) == 20.0
    assert kernels.fold(Op.If, [1.0, 10.0, 20.0]) == 10.0
    # -0.0 arm is returned verbatim (bit-exact, not collapsed to +0.0 here).
    assert _bits(kernels.fold(Op.If, [1.0, -0.0, 5.0])) == _bits(-0.0)
    for test in (math.nan, -0.0, 0.0, 1.0, math.inf, -math.inf):
        check(Op.If, [test, 10.0, 20.0])


def test_switch_first_match_wins_on_duplicate_keys():
    # Duplicate keys: the first match wins (ordered scan).
    assert kernels.fold(Op.Switch, [1.0, 1.0, 111.0, 1.0, 222.0]) == 111.0
    assert kernels.fold(Op.SwitchWithDefault, [1.0, 1.0, 111.0, 1.0, 222.0, 999.0]) == 111.0
    check(Op.Switch, [1.0, 1.0, 111.0, 1.0, 222.0])
    check(Op.SwitchWithDefault, [1.0, 1.0, 111.0, 1.0, 222.0, 999.0])


def test_switch_nan_key_never_matches():
    # A NaN key matches nothing (folds through the guard, then NaN == x is False).
    assert kernels.fold(Op.Switch, [5.0, math.nan, 111.0, 5.0, 222.0]) == 222.0
    assert kernels.fold(Op.Switch, [math.nan, math.nan, 111.0]) == 0.0  # NaN test vs NaN key: no match
    assert kernels.fold(Op.SwitchWithDefault, [math.nan, math.nan, 111.0, 7.0]) == 7.0
    check(Op.Switch, [5.0, math.nan, 111.0, 5.0, 222.0])
    check(Op.Switch, [math.nan, math.nan, 111.0])


def test_switch_negative_zero_key_matches_zero_test():
    # -0.0 == 0.0 in IEEE, so a -0.0 key matches a +0.0 test (and vice versa).
    assert kernels.fold(Op.Switch, [0.0, -0.0, 42.0]) == 42.0
    assert kernels.fold(Op.Switch, [-0.0, 0.0, 42.0]) == 42.0
    check(Op.Switch, [0.0, -0.0, 42.0])
    check(Op.Switch, [-0.0, 0.0, 42.0])


def test_switch_empty_cases_is_zero():
    # Switch with no (key, value) pairs is always 0.0.
    assert kernels.fold(Op.Switch, [5.0]) == 0.0
    assert kernels.fold(Op.Switch, [math.nan]) == 0.0
    check(Op.Switch, [5.0])


@pytest.mark.parametrize(
    ("test", "expected"),
    [
        (0.0, 10.0),  # branch 0
        (-0.0, 10.0),  # -0.0 selects branch 0
        (1.0, 20.0),  # branch 1
        (2.0, 30.0),  # branch 2 (last)
        (-1.0, 0.0),  # t < 0 -> default 0.0
        (3.0, 0.0),  # t == nbranches -> out of range -> 0.0
        (1.5, 0.0),  # non-integral -> 0.0
        (16777216.0, 0.0),  # 2^24 (f32-exact) out of range -> 0.0
        (16777215.0, 0.0),  # 2^24 - 1 (f32-exact) out of range -> 0.0
        (math.inf, 0.0),  # +inf out of range -> 0.0
        (-math.inf, 0.0),  # -inf < 0 -> 0.0
        (math.nan, 0.0),  # NaN -> 0.0
    ],
)
def test_switch_integer_boundaries(test, expected):
    # 3 branches: [10, 20, 30]; selector picks branch int(t) iff 0 <= t < 3, integral.
    got = kernels.fold(Op.SwitchInteger, [test, 10.0, 20.0, 30.0])
    assert got == expected, (test, got, expected)
    check(Op.SwitchInteger, [test, 10.0, 20.0, 30.0])


def test_switch_integer_with_default_boundaries():
    # Same, but out-of-range/non-integral selects the trailing default (777).
    args = lambda t: [t, 10.0, 20.0, 30.0, 777.0]  # noqa: E731
    assert kernels.fold(Op.SwitchIntegerWithDefault, args(1.0)) == 20.0
    assert kernels.fold(Op.SwitchIntegerWithDefault, args(-0.0)) == 10.0
    assert kernels.fold(Op.SwitchIntegerWithDefault, args(3.0)) == 777.0  # t == nbranches
    assert kernels.fold(Op.SwitchIntegerWithDefault, args(2.5)) == 777.0  # non-integral
    assert kernels.fold(Op.SwitchIntegerWithDefault, args(math.nan)) == 777.0
    for t in (1.0, -0.0, 3.0, 2.5, math.nan, math.inf):
        check(Op.SwitchIntegerWithDefault, args(t))


def test_select_f32_guard_rejects_non_roundtrip_exact():
    # A test (or examined key) that is not f32-roundtrip-exact declines to fold;
    # NaN is admitted (folds through), never rejected.
    assert kernels.fold(Op.If, [1e-46, 10.0, 20.0]) is None  # subnormal-in-f64, not f32-exact
    assert kernels.fold(Op.If, [3.0000000001, 10.0, 20.0]) is None
    assert kernels.fold(Op.SwitchInteger, [16777217.0, 10.0, 20.0]) is None  # 2^24 + 1 not f32-exact
    # A non-exact key that is examined (scanned before any match) rejects the fold.
    assert kernels.fold(Op.Switch, [1.0, 3.0000000001, 5.0]) is None
    assert kernels.fold(Op.SwitchWithDefault, [1.0, 3.0000000001, 5.0, 99.0]) is None
    # NaN is NOT rejected: it folds through as truthy / non-matching.
    assert kernels.fold(Op.If, [math.nan, 10.0, 20.0]) == 10.0
    assert kernels.fold(Op.Switch, [math.nan, 5.0, 111.0]) == 0.0
    # A non-exact ARM/default value is fine (no guard on values) and folds.
    assert kernels.fold(Op.If, [1.0, 3.0000000001, 20.0]) == 3.0000000001
    assert kernels.fold(Op.SwitchWithDefault, [7.0, 1.0, 2.0, 3.0000000001]) == 3.0000000001


def test_select_wrong_arity_is_not_constant():
    assert kernels.fold(Op.If, [1.0, 2.0]) is None
    assert kernels.fold(Op.If, [1.0, 2.0, 3.0, 4.0]) is None
    assert kernels.fold(Op.Switch, []) is None
    assert kernels.fold(Op.Switch, [1.0, 2.0]) is None  # even arity: no trailing value
    assert kernels.fold(Op.SwitchWithDefault, [1.0]) is None  # odd arity
    assert kernels.fold(Op.SwitchWithDefault, [1.0, 2.0, 3.0]) is None  # odd arity
    assert kernels.fold(Op.SwitchInteger, []) is None
    assert kernels.fold(Op.SwitchIntegerWithDefault, [1.0]) is None  # needs test + default
