"""Direct unit tests for the tree-walking test oracle (``sonolus.backend.interpret``).

These construct ``FunctionNode`` trees directly and assert the oracle's numeric semantics,
with particular attention to the M0 fixes: ``Op.Rem`` (sign-of-dividend), ``Op.Sign`` (JS
``Math.sign``), ``Op.Judge``/``Op.JudgeSimple``, and the 36 ``Op.Ease*`` ops.

The ``Ease*`` implementations in the oracle are literal transcriptions of the bodies in
``sonolus/script/easing.py``; the tests here compare the oracle against those bodies
(recovered via ``__wrapped__``) bit-for-bit so the two implementations cannot drift.
"""

import math
import operator
import re
import struct

import pytest
from hypothesis import given
from hypothesis import strategies as st

import sonolus.script.internal.math_impls as smath
from sonolus.backend.interpret import _EASE_FUNCS, Interpreter, _rem  # noqa: PLC2701
from sonolus.backend.node import FunctionNode
from sonolus.backend.ops import Op
from sonolus.script import easing
from sonolus.script.bucket import Judgment
from sonolus.script.bucket import _judge as bucket_judge  # noqa: PLC2701

EASE_OPS = sorted(_EASE_FUNCS, key=lambda op: op.name)

# Grid covering below-range (clamped to 0), the [0, 1] interval, and above-range (clamped to 1).
EASE_GRID = [-0.5, -0.0, 0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0, 1.5]

finite_floats = st.floats(allow_nan=False, allow_infinity=False, width=64)


def run_node(op: Op, *args) -> float:
    return Interpreter().run(FunctionNode(op, tuple(args)))


def bits(value) -> bytes:
    return struct.pack(">d", float(value))


def same_bits(a, b) -> bool:
    """Bit-for-bit float equality, treating any two NaNs as equal."""
    a = float(a)
    b = float(b)
    if math.isnan(a) and math.isnan(b):
        return True
    return bits(a) == bits(b)


def snake(name: str) -> str:
    """``EaseInOutBack`` -> ``ease_in_out_back`` (matches the easing.py function names)."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def easing_body(op: Op):
    """The raw Python easing body (undecorated) for an ``Ease*`` op."""
    return getattr(easing, snake(op.name)).__wrapped__


# --------------------------------------------------------------------------------------------- #
# Op.SwitchInteger: index the branch list with the integer test result
# --------------------------------------------------------------------------------------------- #


def test_switch_integer_functionnode_test_selects_branch():
    # A FunctionNode test evaluates to a float; the branch list must be indexed
    # with int(test_result) for an integral in-range index...
    assert run_node(Op.SwitchInteger, FunctionNode(Op.Add, (0, 0)), 10, 20) == 10.0
    assert run_node(Op.SwitchInteger, FunctionNode(Op.Add, (0, 1)), 10, 20) == 20.0
    # ...and a FunctionNode that evaluates to a NON-integral (0.5) or OUT-OF-RANGE
    # (5) index must fall through to 0.0, not index the list with a float / OOB int.
    assert run_node(Op.SwitchInteger, FunctionNode(Op.Divide, (1, 2)), 10, 20) == 0.0
    assert run_node(Op.SwitchInteger, FunctionNode(Op.Add, (0, 5)), 10, 20) == 0.0


def test_switch_integer_out_of_range_and_non_integer_default_to_zero():
    assert run_node(Op.SwitchInteger, FunctionNode(Op.Add, (0, 5)), 10, 20) == 0.0
    assert run_node(Op.SwitchInteger, FunctionNode(Op.Divide, (1, 2)), 10, 20) == 0.0


def test_switch_integer_with_default_functionnode_float_or_oob_takes_default():
    # SwitchIntegerWithDefault(test, b0, b1, default): the same int(test)==test /
    # in-range guard applies -- a non-integral or out-of-range FunctionNode index
    # takes the default (the last arg), not branch int(test).
    assert run_node(Op.SwitchIntegerWithDefault, FunctionNode(Op.Add, (0, 0)), 10, 20, 99) == 10.0
    assert run_node(Op.SwitchIntegerWithDefault, FunctionNode(Op.Add, (0, 1)), 10, 20, 99) == 20.0
    assert run_node(Op.SwitchIntegerWithDefault, FunctionNode(Op.Divide, (1, 2)), 10, 20, 99) == 99.0
    assert run_node(Op.SwitchIntegerWithDefault, FunctionNode(Op.Add, (0, 5)), 10, 20, 99) == 99.0


# --------------------------------------------------------------------------------------------- #
# Op.Rem: truncated remainder with the sign of the dividend (JS `%`), n-ary left fold, empty = 0
# --------------------------------------------------------------------------------------------- #


def test_rem_basic_sign():
    assert run_node(Op.Rem, 5.0, 3.0) == 2.0
    assert run_node(Op.Rem, -5.0, 3.0) == -2.0
    assert run_node(Op.Rem, 5.0, -3.0) == 2.0
    assert run_node(Op.Rem, -5.0, -3.0) == -2.0


def test_rem_negative_zero_remainder_is_negative_zero():
    # Negative dividend, zero remainder -> -0.0 (matches JS / the real runtime).
    result = run_node(Op.Rem, -6.0, 3.0)
    assert result == 0.0
    assert math.copysign(1.0, result) == -1.0  # bit-exact: sign bit is set


def test_rem_positive_zero_remainder_is_positive_zero():
    result = run_node(Op.Rem, 6.0, 3.0)
    assert result == 0.0
    assert math.copysign(1.0, result) == 1.0


def test_rem_nary_left_fold():
    # Left fold: Rem(17, 5, 3) = rem(rem(17, 5), 3) = rem(2, 3) = 2.
    # (A right fold would give rem(17, rem(5, 3)) = rem(17, 2) = 1, so this distinguishes them.)
    assert run_node(Op.Rem, 17.0, 5.0, 3.0) == 2.0


def test_rem_empty_is_zero():
    assert run_node(Op.Rem) == 0.0


def test_rem_single_arg_is_identity():
    assert run_node(Op.Rem, 5.0) == 5.0


@given(a=finite_floats, b=finite_floats.filter(lambda v: abs(v) > 1e-6))
def test_rem_matches_math_impls(a, b):
    assert same_bits(run_node(Op.Rem, a, b), smath._remainder.__wrapped__(a, b))


# --------------------------------------------------------------------------------------------- #
# Op.Sign: JS Math.sign (0/-0/NaN map to themselves, otherwise +/-1)
# --------------------------------------------------------------------------------------------- #


def test_sign_nonzero():
    assert run_node(Op.Sign, 5.0) == 1.0
    assert run_node(Op.Sign, -5.0) == -1.0
    assert run_node(Op.Sign, 1e-300) == 1.0
    assert run_node(Op.Sign, -1e-300) == -1.0


def test_sign_positive_zero():
    result = run_node(Op.Sign, 0.0)
    assert result == 0.0
    assert math.copysign(1.0, result) == 1.0  # positive sign bit


def test_sign_negative_zero():
    result = run_node(Op.Sign, -0.0)
    assert result == 0.0
    assert math.copysign(1.0, result) == -1.0  # sign bit preserved


def test_sign_nan():
    assert math.isnan(run_node(Op.Sign, float("nan")))


@given(x=finite_floats)
def test_sign_matches_reference(x):
    def reference(v):
        if v > 0:
            return 1.0
        if v < 0:
            return -1.0
        return v

    assert same_bits(run_node(Op.Sign, x), reference(x))


# --------------------------------------------------------------------------------------------- #
# Op.Judge / Op.JudgeSimple: window inclusivity and first-match (elif) ordering
# --------------------------------------------------------------------------------------------- #

# perfect [-1, 1], great [-2, 2], good [-3, 3]
JUDGE_WINDOW = (-1.0, 1.0, -2.0, 2.0, -3.0, 3.0)


@pytest.mark.parametrize(
    ("diff", "expected"),
    [
        (0.0, 1.0),  # centre -> perfect
        (1.0, 1.0),  # perfect upper edge (inclusive)
        (-1.0, 1.0),  # perfect lower edge (inclusive)
        (1.0000001, 2.0),  # just past perfect -> great
        (2.0, 2.0),  # great upper edge (inclusive)
        (-2.0, 2.0),  # great lower edge (inclusive)
        (2.0000001, 3.0),  # just past great -> good
        (3.0, 3.0),  # good upper edge (inclusive)
        (-3.0, 3.0),  # good lower edge (inclusive)
        (3.0000001, 0.0),  # past good -> miss
        (-3.0000001, 0.0),  # past good (negative) -> miss
    ],
)
def test_judge_boundaries(diff, expected):
    # Use source = diff, target = 0 so that source - target = diff.
    result = run_node(Op.Judge, diff, 0.0, *JUDGE_WINDOW)
    assert result == expected


def test_judge_matches_bucket_reference():
    for diff in (-4.0, -3.0, -2.5, -1.0, 0.0, 0.5, 1.0, 2.0, 2.5, 3.0, 4.0):
        oracle = run_node(Op.Judge, diff, 0.0, *JUDGE_WINDOW)
        reference = bucket_judge.__wrapped__(diff, 0.0, *JUDGE_WINDOW)
        assert oracle == float(reference)


def test_judge_uses_source_minus_target():
    # diff = 5 - 4.5 = 0.5 -> perfect
    assert run_node(Op.Judge, 5.0, 4.5, *JUDGE_WINDOW) == 1.0
    # diff = 4.5 - 5 = -0.5 -> perfect
    assert run_node(Op.Judge, 4.5, 5.0, *JUDGE_WINDOW) == 1.0


def test_judge_overlapping_windows_first_match_wins():
    # perfect [0, 2] overlaps great [1, 3]; diff in the overlap must return perfect (checked first).
    window = (0.0, 2.0, 1.0, 3.0, 0.0, 0.0)
    assert run_node(Op.Judge, 1.5, 0.0, *window) == 1.0  # in perfect AND great -> perfect
    assert run_node(Op.Judge, 2.5, 0.0, *window) == 2.0  # in great only -> great


def test_judge_all_returns_map_to_judgment_enum():
    # Sanity: the oracle's numeric returns line up with the bucket Judgment enum values.
    assert run_node(Op.Judge, 0.0, 0.0, *JUDGE_WINDOW) == Judgment.PERFECT
    assert run_node(Op.Judge, 1.5, 0.0, *JUDGE_WINDOW) == Judgment.GREAT
    assert run_node(Op.Judge, 2.5, 0.0, *JUDGE_WINDOW) == Judgment.GOOD
    assert run_node(Op.Judge, 10.0, 0.0, *JUDGE_WINDOW) == Judgment.MISS


@pytest.mark.parametrize(
    ("diff", "expected"),
    [
        (0.0, 1.0),
        (1.0, 1.0),  # perfect edge
        (-1.0, 1.0),
        (1.5, 2.0),  # great
        (2.0, 2.0),  # great edge
        (-2.0, 2.0),
        (2.5, 3.0),  # good
        (3.0, 3.0),  # good edge
        (-3.0, 3.0),
        (3.5, 0.0),  # miss
    ],
)
def test_judge_simple_boundaries(diff, expected):
    # JudgeSimple(source, target, maxPerfect, maxGreat, maxGood) with symmetric windows.
    assert run_node(Op.JudgeSimple, diff, 0.0, 1.0, 2.0, 3.0) == expected


def test_judge_simple_equals_judge_expansion():
    # JudgeSimple(s, t, mp, mg, mgd) == Judge(s, t, -mp, mp, -mg, mg, -mgd, mgd)
    for diff in (-4.0, -2.5, -1.0, 0.0, 0.5, 1.0, 2.0, 2.5, 3.0, 4.0):
        simple = run_node(Op.JudgeSimple, diff, 0.0, 1.0, 2.0, 3.0)
        expanded = run_node(Op.Judge, diff, 0.0, -1.0, 1.0, -2.0, 2.0, -3.0, 3.0)
        assert simple == expanded


# --------------------------------------------------------------------------------------------- #
# Op.Ease*: literal transcription must match easing.py bodies bit-for-bit
# --------------------------------------------------------------------------------------------- #


def test_all_36_ease_ops_registered():
    assert len(EASE_OPS) == 36


@pytest.mark.parametrize("op", EASE_OPS, ids=lambda op: op.name)
def test_ease_matches_easing_body_on_grid(op):
    body = easing_body(op)
    for x in EASE_GRID:
        assert same_bits(run_node(op, x), body(x)), (op.name, x)


@pytest.mark.parametrize("op", EASE_OPS, ids=lambda op: op.name)
@given(x=finite_floats)
def test_ease_matches_easing_body_hypothesis(op, x):
    assert same_bits(run_node(op, x), easing_body(op)(x))


@pytest.mark.parametrize("op", EASE_OPS, ids=lambda op: op.name)
def test_ease_clamps_input(op):
    # Out-of-range inputs are clamped, so ease(-5) == ease(0) and ease(5) == ease(1).
    assert same_bits(run_node(op, -5.0), run_node(op, 0.0))
    assert same_bits(run_node(op, 5.0), run_node(op, 1.0))


# --------------------------------------------------------------------------------------------- #
# Fused read-modify-write ops (M3.5): Set<BinOp>[|Pointed|Shifted], Increment*/Decrement*.
#
# Each fused op is checked against its definitional expansion executed on a fresh interpreter
# (same seeded memory), asserting BOTH the final memory and the return value match (the
# expansion's return being the wrapping ``Set``'s value). Constant addresses are used so the
# expansion's double index-evaluation is harmless. Pre/Post return conventions (REVERSE of C:
# Pre=old, Post=new), pointer-pair addressing, x+y*s striding, and Mod/Rem sign edges are
# covered through the fused forms.
# --------------------------------------------------------------------------------------------- #

# (scalar, pointed, shifted) fused op per binary operator, plus the plain binop + Python impl.
_FUSED = {
    Op.Add: (Op.SetAdd, Op.SetAddPointed, Op.SetAddShifted, operator.add),
    Op.Subtract: (Op.SetSubtract, Op.SetSubtractPointed, Op.SetSubtractShifted, operator.sub),
    Op.Multiply: (Op.SetMultiply, Op.SetMultiplyPointed, Op.SetMultiplyShifted, operator.mul),
    Op.Divide: (Op.SetDivide, Op.SetDividePointed, Op.SetDivideShifted, operator.truediv),
    Op.Mod: (Op.SetMod, Op.SetModPointed, Op.SetModShifted, operator.mod),
    Op.Rem: (Op.SetRem, Op.SetRemPointed, Op.SetRemShifted, _rem),
    Op.Power: (Op.SetPower, Op.SetPowerPointed, Op.SetPowerShifted, operator.pow),
}

BINOPS = list(_FUSED)


def _run_seeded(node: FunctionNode, seed: dict) -> tuple:
    """Run ``node`` on a fresh interpreter seeded with ``seed`` (block -> {index: value}).

    Returns ``(return_value, memory_snapshot)`` where the snapshot is a dict of the touched
    blocks as tuples.
    """
    it = Interpreter()
    for block, slots in seed.items():
        for index, value in slots.items():
            it.set(block, index, value)
    ret = it.run(node)
    mem = {block: tuple(cells) for block, cells in it.blocks.items()}
    return ret, mem


def _c(x) -> float:
    return float(x)


# ---- scalar Set<BinOp> vs Set(id, index, BinOp(Get(id, index), value)) --------------------- #


@pytest.mark.parametrize("binop", BINOPS, ids=lambda op: op.name)
@pytest.mark.parametrize("old", [8.0, -8.0, 6.0, -6.0, 2.5])
@pytest.mark.parametrize("value", [3.0, -3.0, 2.0])
def test_scalar_fused_matches_expansion(binop, old, value):
    scalar_op = _FUSED[binop][0]
    block, index = 100, 5
    seed = {block: {index: old}}
    fused = FunctionNode(scalar_op, (_c(block), _c(index), _c(value)))
    get = FunctionNode(Op.Get, (_c(block), _c(index)))
    expansion = FunctionNode(Op.Set, (_c(block), _c(index), FunctionNode(binop, (get, _c(value)))))
    assert _run_seeded(fused, seed) == _run_seeded(expansion, seed)


# ---- Pointed: double-deref addressing (ptr block at index, ptr index at index+1) ----------- #


@pytest.mark.parametrize("binop", BINOPS, ids=lambda op: op.name)
@pytest.mark.parametrize("value", [3.0, -2.0])
def test_pointed_fused_matches_expansion(binop, value):
    pointed_op = _FUSED[binop][1]
    # mem[100][0] = target block 200; mem[100][1] = base index 3; offset 2 -> mem[200][5].
    ptr_block, ptr_index, offset = 100, 0, 2
    seed = {ptr_block: {ptr_index: 200.0, ptr_index + 1: 3.0}, 200: {5: 40.0}}
    args = (_c(ptr_block), _c(ptr_index), _c(offset), _c(value))
    fused = FunctionNode(pointed_op, args)
    get = FunctionNode(Op.GetPointed, (_c(ptr_block), _c(ptr_index), _c(offset)))
    deref_block = FunctionNode(Op.Get, (_c(ptr_block), _c(ptr_index)))
    deref_index = FunctionNode(Op.Add, (FunctionNode(Op.Get, (_c(ptr_block), _c(ptr_index) + 1)), _c(offset)))
    expansion = FunctionNode(Op.Set, (deref_block, deref_index, FunctionNode(binop, (get, _c(value)))))
    assert _run_seeded(fused, seed) == _run_seeded(expansion, seed)


# ---- Shifted: addr = x + y * s ------------------------------------------------------------- #


@pytest.mark.parametrize("binop", BINOPS, ids=lambda op: op.name)
@pytest.mark.parametrize("value", [3.0, -2.0])
def test_shifted_fused_matches_expansion(binop, value):
    shifted_op = _FUSED[binop][2]
    block, x, y, s = 100, 2, 3, 4  # addr = 2 + 3*4 = 14
    seed = {block: {x + y * s: 40.0}}
    args = (_c(block), _c(x), _c(y), _c(s), _c(value))
    fused = FunctionNode(shifted_op, args)
    get = FunctionNode(Op.GetShifted, (_c(block), _c(x), _c(y), _c(s)))
    addr = FunctionNode(Op.Add, (_c(x), FunctionNode(Op.Multiply, (_c(y), _c(s)))))
    expansion = FunctionNode(Op.Set, (_c(block), addr, FunctionNode(binop, (get, _c(value)))))
    assert _run_seeded(fused, seed) == _run_seeded(expansion, seed)


def test_shifted_addressing_x_plus_y_times_s():
    # Directly assert the x + y*s address is where the store lands.
    it = Interpreter()
    it.set(7, 14, 100.0)  # 2 + 3*4 = 14
    ret = it.run(FunctionNode(Op.SetAddShifted, (7.0, 2.0, 3.0, 4.0, 5.0)))
    assert ret == 105.0
    assert it.get(7, 14) == 105.0


# ---- Increment/Decrement Pre/Post return + memory (REVERSE of C: Pre=old, Post=new) -------- #


@pytest.mark.parametrize(
    ("op", "ret_is_new", "delta"),
    [
        (Op.IncrementPost, True, 1),
        (Op.IncrementPre, False, 1),
        (Op.DecrementPost, True, -1),
        (Op.DecrementPre, False, -1),
    ],
)
def test_increment_decrement_scalar_return_and_memory(op, ret_is_new, delta):
    block, index, old = 100, 5, 8.0
    it = Interpreter()
    it.set(block, index, old)
    ret = it.run(FunctionNode(op, (float(block), float(index))))
    assert it.get(block, index) == old + delta  # memory always changes by +/-1
    assert ret == (old + delta if ret_is_new else old)  # Post=new, Pre=old


@pytest.mark.parametrize(
    ("op", "ret_is_new", "delta"),
    [
        (Op.IncrementPostPointed, True, 1),
        (Op.IncrementPrePointed, False, 1),
        (Op.DecrementPostPointed, True, -1),
        (Op.DecrementPrePointed, False, -1),
    ],
)
def test_increment_decrement_pointed_return_and_addressing(op, ret_is_new, delta):
    # ptr pair mem[100][0]=200, mem[100][1]=3; offset 2 -> target mem[200][5].
    seed_old = 40.0
    it = Interpreter()
    it.set(100, 0, 200.0)
    it.set(100, 1, 3.0)
    it.set(200, 5, seed_old)
    ret = it.run(FunctionNode(op, (100.0, 0.0, 2.0)))
    assert it.get(200, 5) == seed_old + delta
    assert ret == (seed_old + delta if ret_is_new else seed_old)


@pytest.mark.parametrize(
    ("op", "ret_is_new", "delta"),
    [
        (Op.IncrementPostShifted, True, 1),
        (Op.IncrementPreShifted, False, 1),
        (Op.DecrementPostShifted, True, -1),
        (Op.DecrementPreShifted, False, -1),
    ],
)
def test_increment_decrement_shifted_return_and_addressing(op, ret_is_new, delta):
    seed_old = 40.0
    it = Interpreter()
    it.set(100, 14, seed_old)  # 2 + 3*4
    ret = it.run(FunctionNode(op, (100.0, 2.0, 3.0, 4.0)))
    assert it.get(100, 14) == seed_old + delta
    assert ret == (seed_old + delta if ret_is_new else seed_old)


# ---- Mod / Rem sign edge cases through the fused forms ------------------------------------- #


@pytest.mark.parametrize(
    ("old", "value"),
    [(-7.0, 3.0), (7.0, -3.0), (-7.0, -3.0), (7.0, 3.0), (-6.0, 3.0), (6.0, -3.0)],
)
def test_set_mod_matches_python_floored(old, value):
    it = Interpreter()
    it.set(1, 0, old)
    ret = it.run(FunctionNode(Op.SetMod, (1.0, 0.0, value)))
    assert same_bits(ret, operator.mod(old, value))
    assert same_bits(it.get(1, 0), operator.mod(old, value))


@pytest.mark.parametrize(
    ("old", "value"),
    [(-7.0, 3.0), (7.0, -3.0), (-7.0, -3.0), (7.0, 3.0), (-6.0, 3.0), (6.0, -3.0)],
)
def test_set_rem_matches_sign_of_dividend(old, value):
    it = Interpreter()
    it.set(1, 0, old)
    ret = it.run(FunctionNode(Op.SetRem, (1.0, 0.0, value)))
    assert same_bits(ret, _rem(old, value))
    assert same_bits(it.get(1, 0), _rem(old, value))


def test_set_rem_negative_zero_remainder_is_negative_zero():
    # Negative dividend, zero remainder -> -0.0 stored (sign-of-dividend), through the fused form.
    it = Interpreter()
    it.set(1, 0, -6.0)
    ret = it.run(FunctionNode(Op.SetRem, (1.0, 0.0, 3.0)))
    assert ret == 0.0
    assert math.copysign(1.0, ret) == -1.0
    assert math.copysign(1.0, it.get(1, 0)) == -1.0


@given(old=finite_floats, value=finite_floats.filter(lambda v: abs(v) > 1e-6))
def test_set_rem_hypothesis_matches_expansion(old, value):
    it = Interpreter()
    it.set(1, 0, old)
    ret = it.run(FunctionNode(Op.SetRem, (1.0, 0.0, value)))
    assert same_bits(ret, _rem(old, value))
