"""Tests for the arena EngineNode emitter (``sonolus.backend._opt.emit``).

The emitter builds the EngineNode tree from the flat ``Func`` arena, re-flattening
the left spines of n-ary left-fold operations (``Add``/``Multiply``/``Mod``/``Rem``) as it builds.

Two layers of coverage:

1. ``test_*`` unit tests -- every terminator form, NaN/+-Inf/-0.0 constant
   lowering, pointer-deref nested ``Get``s, offset-folding branches, and n-ary
   flatten idempotence (incl. non-flattening of right-nested trees).
2. ``test_semantic_*`` -- hand-built CFGs run through the emitter and the
   ``Interpreter`` oracle, asserting the expected results / logs / memory (incl.
   a shared-subtree case proving hash-consing does not change evaluated
   semantics).
"""

from __future__ import annotations

import math

import pytest

from sonolus.backend._opt import emit  # ruff: ignore[import-private-name]
from sonolus.backend.interpret import Interpreter
from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet
from sonolus.backend.mode import Mode
from sonolus.backend.node import FunctionNode, format_engine_node
from sonolus.backend.ops import Op
from sonolus.backend.optimize import FAST_PASSES, STANDARD_PASSES, OptimizerConfig, optimize_and_finalize
from sonolus.backend.optimize.flow import BasicBlock, traverse_cfg_reverse_postorder
from sonolus.backend.place import BlockPlace, TempBlock

# ---------------------------------------------------------------------------
# Unit-test scaffolding.
# ---------------------------------------------------------------------------


def _emit_program(entry, mode=None, cb=None):
    """emit_cfg(entry) -> (node, [Execute per block in RPO], {block: rpo index})."""
    node = emit.emit_cfg(entry, mode, cb)
    assert node.func == Op.Block
    jl = node.args[0]
    assert jl.func == Op.JumpLoop
    assert jl.args[-1] == 0  # trailing sentinel
    assert type(jl.args[-1]) is int
    idx = {b: i for i, b in enumerate(traverse_cfg_reverse_postorder(entry))}
    return node, list(jl.args[:-1]), idx


def _term(execute):
    """The terminator node (last arg of an Execute)."""
    assert execute.func == Op.Execute
    return execute.args[-1]


# ---------------------------------------------------------------------------
# Terminator forms.
# ---------------------------------------------------------------------------


def test_terminator_empty_exit():
    # {} -> constant exit index (== number of blocks).
    b0 = BasicBlock()
    _node, executes, _idx = _emit_program(b0)
    assert _term(executes[0]) == 1  # single block, exit index 1
    assert type(_term(executes[0])) is int


def test_terminator_unconditional():
    # {None: t} -> constant target index.
    b0, b1 = BasicBlock(), BasicBlock()
    b0.connect_to(b1, None)
    _node, executes, idx = _emit_program(b0)
    assert _term(executes[idx[b0]]) == idx[b1]
    assert _term(executes[idx[b1]]) == 2  # b1 is empty exit -> exit index 2


def test_terminator_if_zero_none():
    # {0: false, None: true} -> If(test, TRUE=none-edge, FALSE=zero-edge).
    b0 = BasicBlock(test=IRGet(BlockPlace(500, 0, 0)))
    bt, bf = BasicBlock(), BasicBlock()
    b0.connect_to(bf, 0)
    b0.connect_to(bt, None)
    _node, executes, idx = _emit_program(b0)
    t = _term(executes[idx[b0]])
    assert t.func == Op.If
    assert t.args[0] == FunctionNode(Op.Get, (500, 0))  # test
    assert t.args[1] == idx[bt]  # TRUE branch = None edge
    assert t.args[2] == idx[bf]  # FALSE branch = 0 edge


def test_terminator_if_equal_const():
    # {None: default, c: branch} with c != 0 -> If(Equal(test, c), branch, default).
    b0 = BasicBlock(test=IRGet(BlockPlace(500, 0, 0)))
    bc, bd = BasicBlock(), BasicBlock()
    b0.connect_to(bc, 5)
    b0.connect_to(bd, None)
    _node, executes, idx = _emit_program(b0)
    t = _term(executes[idx[b0]])
    assert t.func == Op.If
    assert t.args[0] == FunctionNode(Op.Equal, (FunctionNode(Op.Get, (500, 0)), 5))
    assert t.args[1] == idx[bc]
    assert t.args[2] == idx[bd]


def test_terminator_switch_integer_with_default():
    # Contiguous 0..k-1 cases + default -> SwitchIntegerWithDefault.
    b0 = BasicBlock(test=IRGet(BlockPlace(500, 0, 0)))
    b_cases = [BasicBlock() for _ in range(3)]
    bd = BasicBlock()
    for i, bc in enumerate(b_cases):
        b0.connect_to(bc, i)
    b0.connect_to(bd, None)
    _node, executes, idx = _emit_program(b0)
    t = _term(executes[idx[b0]])
    assert t.func == Op.SwitchIntegerWithDefault
    assert t.args[0] == FunctionNode(Op.Get, (500, 0))
    assert list(t.args[1:4]) == [idx[bc] for bc in b_cases]
    assert t.args[4] == idx[bd]


def test_terminator_switch_integer_default_less():
    # Default-less contiguous cases {0, 1} -> SwitchIntegerWithDefault, default = exit.
    b0 = BasicBlock(test=IRGet(BlockPlace(500, 0, 0)))
    b_cases = [BasicBlock(), BasicBlock()]
    for i, bc in enumerate(b_cases):
        b0.connect_to(bc, i)
    _node, executes, idx = _emit_program(b0)
    t = _term(executes[idx[b0]])
    assert t.func == Op.SwitchIntegerWithDefault
    assert list(t.args[1:3]) == [idx[bc] for bc in b_cases]
    assert t.args[3] == 3  # missing default -> exit index (3 blocks)


def test_terminator_switch_with_default_gap():
    # Gapped but near-dense 0-based cases (0, 2) -> SwitchIntegerWithDefault, the
    # hole (slot 1) routed to the default (dense gap-fill).
    b0 = BasicBlock(test=IRGet(BlockPlace(500, 0, 0)))
    b_a, b_c, bd = BasicBlock(), BasicBlock(), BasicBlock()
    b0.connect_to(b_a, 0)
    b0.connect_to(b_c, 2)
    b0.connect_to(bd, None)
    _node, executes, idx = _emit_program(b0)
    t = _term(executes[idx[b0]])
    assert t.func == Op.SwitchIntegerWithDefault
    assert t.args[0] == FunctionNode(Op.Get, (500, 0))
    # slots 0,1,2: the hole at 1 routes to the default (bd); trailing default = bd.
    assert list(t.args[1:4]) == [idx[b_a], idx[bd], idx[b_c]]
    assert t.args[4] == idx[bd]


def test_terminator_switch_with_default_nonzero_min():
    # Non-zero minimum case (1, 2) breaks contiguity -> SwitchWithDefault.
    b0 = BasicBlock(test=IRGet(BlockPlace(500, 0, 0)))
    b1, b2, bd = BasicBlock(), BasicBlock(), BasicBlock()
    b0.connect_to(b1, 1)
    b0.connect_to(b2, 2)
    b0.connect_to(bd, None)
    _node, executes, idx = _emit_program(b0)
    t = _term(executes[idx[b0]])
    assert t.func == Op.SwitchWithDefault
    assert list(t.args[1:5]) == [1, idx[b1], 2, idx[b2]]
    assert t.args[5] == idx[bd]


def test_terminator_switch_with_default_non_integral():
    # A non-integral case (1.5) breaks contiguity -> SwitchWithDefault; the float
    # label keeps its float display form (distinct from an int).
    b0 = BasicBlock(test=IRGet(BlockPlace(500, 0, 0)))
    b_a, b_b, bd = BasicBlock(), BasicBlock(), BasicBlock()
    b0.connect_to(b_a, 0)
    b0.connect_to(b_b, 1.5)
    b0.connect_to(bd, None)
    _node, executes, idx = _emit_program(b0)
    t = _term(executes[idx[b0]])
    assert t.func == Op.SwitchWithDefault
    # Ascending case order; 0 is int, 1.5 is a float.
    assert t.args[1] == 0
    assert type(t.args[1]) is int
    assert t.args[2] == idx[b_a]
    assert t.args[3] == 1.5
    assert type(t.args[3]) is float
    assert t.args[4] == idx[b_b]
    assert t.args[5] == idx[bd]


def test_terminator_switch_with_default_default_less():
    # Default-less near-dense multiway (0, 2) -> SwitchIntegerWithDefault; the hole
    # and out-of-range both route to the exit index (the exit is the value a
    # non-matching test already reaches for a default-less block).
    b0 = BasicBlock(test=IRGet(BlockPlace(500, 0, 0)))
    b_a, b_c = BasicBlock(), BasicBlock()
    b0.connect_to(b_a, 0)
    b0.connect_to(b_c, 2)
    _node, executes, idx = _emit_program(b0)
    t = _term(executes[idx[b0]])
    assert t.func == Op.SwitchIntegerWithDefault
    # 3 blocks -> exit index 3; slot 1 hole -> exit; trailing default = exit.
    assert list(t.args[1:4]) == [idx[b_a], 3, idx[b_c]]
    assert t.args[4] == 3


# --- The dense-switch gate must guard integrality/finiteness/range BEFORE any
# int32 narrowing: conds >= 2^31 / +-inf / NaN / huge integral floats fall back to
# SwitchWithDefault instead of crashing (OverflowError / ValueError). Non-finite
# labels must also lower to EngineRom reads like value-position constants do: a
# bare Infinity/-Infinity/NaN leaf would make the packaged JSON payload invalid. ---


def test_terminator_switch_case_at_2p31_falls_back():
    # A case >= 2^31 overflows the dense gate's int32 span -> SwitchWithDefault.
    b0 = BasicBlock(test=IRGet(BlockPlace(500, 0, 0)))
    b_a, b_b, bd = BasicBlock(), BasicBlock(), BasicBlock()
    b0.connect_to(b_a, 0)
    b0.connect_to(b_b, 2**31)
    b0.connect_to(bd, None)
    _node, executes, idx = _emit_program(b0)
    t = _term(executes[idx[b0]])
    assert t.func == Op.SwitchWithDefault
    assert list(t.args[1:5]) == [0, idx[b_a], 2**31, idx[b_b]]
    assert t.args[5] == idx[bd]


def _assert_rom_read(node, slot):
    """Assert the EngineRom read a non-finite constant lowers to (slot 0=NaN, 1=+Inf, 2=-Inf)."""
    assert isinstance(node, FunctionNode)
    assert node.func == Op.Get
    assert tuple(node.args) == (3000, slot)


def _switch_case_pairs(t):
    """The (label, target) pairs of a SwitchWithDefault terminator."""
    return [(t.args[i], t.args[i + 1]) for i in range(1, len(t.args) - 1, 2)]


def _rom_case_target(t, slot):
    """The target paired with the single ROM-read case label reading the given slot."""
    matches = [target for label, target in _switch_case_pairs(t) if isinstance(label, FunctionNode)]
    assert len(matches) == 1
    for label, target in _switch_case_pairs(t):
        if isinstance(label, FunctionNode):
            _assert_rom_read(label, slot)
            return target
    raise AssertionError


def test_terminator_switch_inf_case_falls_back_and_lowers_to_rom():
    b0 = BasicBlock(test=IRGet(BlockPlace(500, 0, 0)))
    b_a, b_b, bd = BasicBlock(), BasicBlock(), BasicBlock()
    b0.connect_to(b_a, 0)
    b0.connect_to(b_b, math.inf)
    b0.connect_to(bd, None)
    _node, executes, idx = _emit_program(b0)
    t = _term(executes[idx[b0]])
    assert t.func == Op.SwitchWithDefault
    assert (0, idx[b_a]) in _switch_case_pairs(t)
    assert _rom_case_target(t, 1) == idx[b_b]
    assert t.args[-1] == idx[bd]


def test_terminator_switch_negative_inf_case_falls_back_and_lowers_to_rom():
    b0 = BasicBlock(test=IRGet(BlockPlace(500, 0, 0)))
    b_a, b_b, bd = BasicBlock(), BasicBlock(), BasicBlock()
    b0.connect_to(b_a, 0)
    b0.connect_to(b_b, -math.inf)
    b0.connect_to(bd, None)
    _node, executes, idx = _emit_program(b0)
    t = _term(executes[idx[b0]])
    assert t.func == Op.SwitchWithDefault
    assert (0, idx[b_a]) in _switch_case_pairs(t)
    assert _rom_case_target(t, 2) == idx[b_b]


def test_terminator_switch_nan_case_falls_back_and_lowers_to_rom():
    b0 = BasicBlock(test=IRGet(BlockPlace(500, 0, 0)))
    b_a, b_b, bd = BasicBlock(), BasicBlock(), BasicBlock()
    b0.connect_to(b_a, 0)
    b0.connect_to(b_b, math.nan)
    b0.connect_to(bd, None)
    _node, executes, idx = _emit_program(b0)
    t = _term(executes[idx[b0]])
    assert t.func == Op.SwitchWithDefault  # NaN is never a dense case -> no crash
    assert len(t.args) == 6  # test + (cond,target) x2 + default
    assert (0, idx[b_a]) in _switch_case_pairs(t)
    assert _rom_case_target(t, 0) == idx[b_b]


def test_terminator_switch_large_integral_float_falls_back():
    # An integral-valued float far beyond int32 (1e300) must not overflow the dense
    # gate's int32 narrowing.
    b0 = BasicBlock(test=IRGet(BlockPlace(500, 0, 0)))
    b_a, b_b, bd = BasicBlock(), BasicBlock(), BasicBlock()
    b0.connect_to(b_a, 0)
    b0.connect_to(b_b, 1e300)
    b0.connect_to(bd, None)
    _node, executes, idx = _emit_program(b0)
    t = _term(executes[idx[b0]])
    assert t.func == Op.SwitchWithDefault
    assert list(t.args[3:5]) == [1e300, idx[b_b]]


def test_semantic_switch_fallback_dispatch():
    # Non-dense case sets (huge int / +inf / NaN) fall back to SwitchWithDefault and
    # still dispatch every value correctly.
    def build_for(cases, sel):
        def build():
            b0 = BasicBlock(test=IRGet(BlockPlace(500, 0, 0)))
            b0.statements = [IRSet(BlockPlace(500, 0, 0), IRConst(sel))]
            exit_b = BasicBlock()
            for i, c in enumerate(cases):
                bc = BasicBlock(statements=[IRInstr(Op.DebugLog, [IRConst(100 + i)])])
                b0.connect_to(bc, c)
                bc.connect_to(exit_b, None)
            bd = BasicBlock(statements=[IRInstr(Op.DebugLog, [IRConst(199)])])
            b0.connect_to(bd, None)
            bd.connect_to(exit_b, None)
            return b0

        return build

    assert _assert_semantic_parity(build_for([0, 2**31], 0)).log == [100.0]
    assert _assert_semantic_parity(build_for([0, 2**31], 2**31)).log == [101.0]
    assert _assert_semantic_parity(build_for([0, 2**31], 7)).log == [199.0]
    assert _assert_semantic_parity(build_for([0, math.inf], math.inf)).log == [101.0]
    assert _assert_semantic_parity(build_for([0, math.inf], 3)).log == [199.0]
    assert _assert_semantic_parity(build_for([0, math.nan], 0)).log == [100.0]
    assert _assert_semantic_parity(build_for([0, math.nan], 5)).log == [199.0]


# ---------------------------------------------------------------------------
# Constant lowering (int demotion, NaN / +-Inf via ROM, -0.0 -> int 0).
# ---------------------------------------------------------------------------


def _set_value_node(value_ir):
    """Emit ``Set(place, value_ir)`` and return the emitted value node (arg 2)."""
    b0 = BasicBlock(statements=[IRSet(BlockPlace(500, 0, 0), value_ir)])
    _node, executes, _idx = _emit_program(b0)
    set_node = executes[0].args[0]
    assert set_node.func == Op.Set
    return set_node.args[2]


def test_const_integral_float_demotes_to_int():
    v = _set_value_node(IRConst(5.0))
    assert v == 5
    assert type(v) is int


def test_const_finite_non_integral_stays_float():
    v = _set_value_node(IRConst(2.5))
    assert v == 2.5
    assert type(v) is float


def test_const_negative_zero_emits_int_zero():
    # Bit-level: -0.0 is integral, so it demotes to *int* 0.
    v = _set_value_node(IRConst(-0.0))
    assert v == 0
    assert type(v) is int


def test_const_positive_infinity_rom_read():
    v = _set_value_node(IRConst(math.inf))
    assert v == FunctionNode(Op.Get, (3000, 1))


def test_const_negative_infinity_rom_read():
    v = _set_value_node(IRConst(-math.inf))
    assert v == FunctionNode(Op.Get, (3000, 2))


def test_const_nan_rom_read():
    v = _set_value_node(IRConst(math.nan))
    assert v == FunctionNode(Op.Get, (3000, 0))


# ---------------------------------------------------------------------------
# Place emission: offset folding and pointer-deref nested Gets.
# ---------------------------------------------------------------------------


def _set_target_place_node(place):
    """Emit ``Set(place, 0)`` and return ``(block_node, index_node)``."""
    b0 = BasicBlock(statements=[IRSet(place, IRConst(0))])
    _node, executes, _idx = _emit_program(b0)
    set_node = executes[0].args[0]
    assert set_node.func == Op.Set
    return set_node.args[0], set_node.args[1]


def test_place_offset_zero_index_dynamic():
    # offset == 0, dynamic index -> Get(block, emit(index)).
    block, index = _set_target_place_node(BlockPlace(500, IRGet(BlockPlace(501, 0, 0)), 0))
    assert block == 500
    assert index == FunctionNode(Op.Get, (501, 0))


def test_place_offset_nonzero_index_zero():
    # offset != 0, constant index 0 -> raw int offset.
    block, index = _set_target_place_node(BlockPlace(500, 0, 7))
    assert block == 500
    assert index == 7
    assert type(index) is int


def test_place_offset_nonzero_index_dynamic():
    # offset != 0, dynamic (non-Multiply) index -> SetShifted(block, offset, index,
    # 1, value): the address Add(index, offset) is absorbed as stride 1.
    b0 = BasicBlock(statements=[IRSet(BlockPlace(500, IRGet(BlockPlace(501, 0, 0)), 7), IRConst(0))])
    _node, executes, _idx = _emit_program(b0)
    set_node = executes[0].args[0]
    assert set_node.func == Op.SetShifted
    assert list(set_node.args) == [500, 7, FunctionNode(Op.Get, (501, 0)), 1, 0]


def test_place_pointer_deref_nested_gets():
    # A place whose block is itself a place -> nested Get for the block node.
    place = BlockPlace(BlockPlace(600, 3, 0), 5, 0)
    b0 = BasicBlock(statements=[IRSet(BlockPlace(500, 0, 0), IRGet(place))])
    _node, executes, _idx = _emit_program(b0)
    value = executes[0].args[0].args[2]
    assert value == FunctionNode(Op.Get, (FunctionNode(Op.Get, (600, 3)), 5))


# ---------------------------------------------------------------------------
# Strided address -> GetShifted / SetShifted / SetAddShifted.
# GetShifted(block, offset, index, stride) == get(block, offset + index*stride).
# ---------------------------------------------------------------------------


def _strided_index(base_block=501, stride=4):
    # index = Get(base_block, 0) * stride  (a Multiply with a constant stride).
    return IRPureInstr(Op.Multiply, [IRGet(BlockPlace(base_block, 0, 0)), IRConst(stride)])


def test_place_strided_multiply_get_offset():
    # Get(block, Add(Multiply(i, s), offset)) -> GetShifted(block, offset, i, s):
    # both the Multiply and the offset Add are absorbed.
    b0 = BasicBlock(statements=[IRSet(BlockPlace(500, 0, 0), IRGet(BlockPlace(500, _strided_index(), 8)))])
    _node, executes, _idx = _emit_program(b0)
    value = executes[0].args[0].args[2]
    assert value.func == Op.GetShifted
    assert list(value.args) == [500, 8, FunctionNode(Op.Get, (501, 0)), 4]


def test_place_strided_multiply_get_no_offset():
    # Get(block, Multiply(i, s)) -> GetShifted(block, 0, i, s) (offset 0; node-neutral,
    # removes the Multiply fn node).
    b0 = BasicBlock(statements=[IRSet(BlockPlace(500, 0, 0), IRGet(BlockPlace(500, _strided_index(), 0)))])
    _node, executes, _idx = _emit_program(b0)
    value = executes[0].args[0].args[2]
    assert value.func == Op.GetShifted
    assert list(value.args) == [500, 0, FunctionNode(Op.Get, (501, 0)), 4]


def test_place_strided_multiply_set():
    # Set into a strided place -> SetShifted(block, offset, index, stride, value).
    b0 = BasicBlock(statements=[IRSet(BlockPlace(500, _strided_index(), 8), IRConst(9))])
    _node, executes, _idx = _emit_program(b0)
    set_node = executes[0].args[0]
    assert set_node.func == Op.SetShifted
    assert list(set_node.args) == [500, 8, FunctionNode(Op.Get, (501, 0)), 4, 9]


def test_place_nonstrided_no_offset_stays_plain_get():
    # A bare dynamic index with offset 0 and no Multiply stays a plain Get
    # (a stride-1 shift would only ADD nodes).
    b0 = BasicBlock(statements=[IRSet(BlockPlace(500, 0, 0), IRGet(BlockPlace(500, IRGet(BlockPlace(501, 0, 0)), 0)))])
    _node, executes, _idx = _emit_program(b0)
    assert executes[0].args[0].args[2].func == Op.Get


# --- A runtime-constant address subtree declines the Shifted rewrite: the pure
# Multiply/Add form folds to one node on the runtime, while a Shifted op's
# offset/index/stride operand slots are evaluated separately, so rewriting a
# runtime-constant address grows the effective node count. LevelData (2001) is
# runtime-constant in play's updateParallel; LevelMemory (2000) never is. ---


def _rtc_strided_index(stride=4):
    # Get(LevelData, 3) * stride: a fully runtime-constant index subtree.
    return IRPureInstr(Op.Multiply, [IRGet(BlockPlace(2001, 3, 0)), IRConst(stride)])


def test_place_strided_runtime_constant_index_stays_plain_get():
    b0 = BasicBlock(statements=[IRSet(BlockPlace(2000, 0, 0), IRGet(BlockPlace(2000, _rtc_strided_index(), 8)))])
    _node, executes, _idx = _emit_program(b0, mode=Mode.PLAY, cb="updateParallel")
    value = executes[0].args[0].args[2]
    assert value.func == Op.Get


def test_place_strided_runtime_constant_index_stays_plain_set():
    b0 = BasicBlock(statements=[IRSet(BlockPlace(2000, _rtc_strided_index(), 8), IRConst(9))])
    _node, executes, _idx = _emit_program(b0, mode=Mode.PLAY, cb="updateParallel")
    set_node = executes[0].args[0]
    assert set_node.func == Op.Set


def test_place_offset_runtime_constant_index_stays_plain():
    # The stride-1 offset branch declines on a runtime-constant index too.
    b0 = BasicBlock(statements=[IRSet(BlockPlace(2000, IRGet(BlockPlace(2001, 3, 0)), 7), IRConst(9))])
    _node, executes, _idx = _emit_program(b0, mode=Mode.PLAY, cb="updateParallel")
    set_node = executes[0].args[0]
    assert set_node.func == Op.Set


def test_place_strided_mixed_index_keeps_shifted():
    # One Multiply arm reads LevelMemory, so the subtree is not runtime-constant
    # and the rewrite must stay: on a non-runtime-constant address it is a win.
    index = IRPureInstr(Op.Multiply, [IRGet(BlockPlace(2000, 5, 0)), IRConst(4)])
    b0 = BasicBlock(statements=[IRSet(BlockPlace(2000, 0, 0), IRGet(BlockPlace(2000, index, 8)))])
    _node, executes, _idx = _emit_program(b0, mode=Mode.PLAY, cb="updateParallel")
    value = executes[0].args[0].args[2]
    assert value.func == Op.GetShifted


# --- The same decline, for a block id the PASSES resolve rather than marshal-in. A pointer
# whose target folds to a constant becomes a real block inside lowering, which is the only
# place its writability and runtime-constant membership can be derived; the decline above
# reads exactly those two facts, so it has to reach the same answer either way. These run the
# real pipeline because the fold is what they are about. ---

_PIPELINE_LEVELS = {"fast": FAST_PASSES, "standard": STANDARD_PASSES}


def _folded_block_cfg(block_id, inner_index=3, stride=4):
    # b = (block_id - 1) + 1; DebugLog(LevelMemory[Get(b, inner_index) * stride + 8]).
    # Reading through `b` makes the inner address a pointer deref at marshal-in, so the block
    # is unresolved there and only the passes' constant folding turns it into `block_id`.
    pointer = BlockPlace(TempBlock("b", 1), 0, 0)
    index = IRPureInstr(Op.Multiply, [IRGet(BlockPlace(IRGet(pointer), inner_index, 0)), IRConst(stride)])
    b0 = BasicBlock(
        statements=[
            IRSet(pointer, IRPureInstr(Op.Add, [IRConst(block_id - 1), IRConst(1)])),
            IRInstr(Op.DebugLog, [IRGet(BlockPlace(2000, index, 8))]),
        ]
    )
    b0.connect_to(BasicBlock(), None)
    return b0


def _emit_optimized_log_arg(entry, level, config):
    """Optimize and emit `entry`, then return the argument of its single DebugLog."""
    node = optimize_and_finalize(entry, level, config)
    assert node.func == Op.Block
    jump_loop = node.args[0]
    assert jump_loop.func == Op.JumpLoop
    logs = [arg for arg in jump_loop.args[0].args if getattr(arg, "func", None) == Op.DebugLog]
    assert len(logs) == 1
    return logs[0].args[0]


@pytest.mark.parametrize("level_name", ["fast", "standard"])
def test_folded_block_runtime_constant_index_stays_plain_get(level_name):
    # Folds to LevelData (2001), runtime-constant in updateParallel, so the rewrite must decline
    # exactly as it does when the block id is written as a constant. Minimal is excluded because it
    # never lowers out of SSA: nothing folds there, and the address stays a pointer deref.
    config = OptimizerConfig(mode=Mode.PLAY, callback="updateParallel")

    value = _emit_optimized_log_arg(_folded_block_cfg(2001), _PIPELINE_LEVELS[level_name], config)

    assert value.func == Op.Get


@pytest.mark.parametrize("level_name", ["fast", "standard"])
def test_folded_block_non_runtime_constant_index_keeps_shifted(level_name):
    # Guards the test above against passing for the wrong reason: folding to LevelMemory (2000),
    # which is never runtime-constant, must still take the rewrite. If the decline ever became
    # unconditional, this is what would fail instead of both tests staying green.
    config = OptimizerConfig(mode=Mode.PLAY, callback="updateParallel")

    value = _emit_optimized_log_arg(_folded_block_cfg(2000), _PIPELINE_LEVELS[level_name], config)

    assert value.func == Op.GetShifted


@pytest.mark.parametrize("level_name", ["fast", "standard"])
def test_folded_block_read_at_a_runtime_index_keeps_shifted(level_name):
    # Folds to LevelData again, but read at a runtime index: not a constant-index read, so the
    # runtime does not fold it and the rewrite is still a win. Pins that the fold carries the index
    # half of what makes a read runtime-constant, not just the block's identity.
    config = OptimizerConfig(mode=Mode.PLAY, callback="updateParallel")
    runtime_index = IRGet(BlockPlace(2000, 0, 0))

    value = _emit_optimized_log_arg(_folded_block_cfg(2001, runtime_index), _PIPELINE_LEVELS[level_name], config)

    assert value.func == Op.GetShifted


@pytest.mark.parametrize("level_name", ["fast", "standard"])
def test_folded_block_without_a_config_keeps_shifted(level_name):
    # With no mode to resolve 2001 against, the fold cannot know it is LevelData, so it must fall
    # back to what marshal-in gives an unresolved block: writable, not runtime-constant, rewrite kept.
    value = _emit_optimized_log_arg(_folded_block_cfg(2001), _PIPELINE_LEVELS[level_name], None)

    assert value.func == Op.GetShifted


@pytest.mark.parametrize("level_name", ["fast", "standard"])
def test_folded_block_with_a_folded_index_stays_plain_get(level_name):
    # Same as the plain-Get case above, but the inner index is an expression the PASSES fold to a
    # constant rather than a literal marshal-in bakes into the offset, so the place reaches the
    # block-id fold with its index as a const instruction reference. The fold must bake it the way
    # marshal-in would have, or the read misses its runtime-constant classification and the shipped
    # tree keeps a rewrite the goldens/metrics path declines.
    config = OptimizerConfig(mode=Mode.PLAY, callback="updateParallel")
    folded_index = IRPureInstr(Op.Add, [IRConst(2), IRConst(1)])

    value = _emit_optimized_log_arg(_folded_block_cfg(2001, folded_index), _PIPELINE_LEVELS[level_name], config)

    assert value.func == Op.Get


# --- The index half of the same story, for a block id that was static all along. Marshal-in bakes
# a literal index into the place's offset, so an index the PASSES fold has to be baked by a pass to
# reach the address the same source spelled as a literal would. ---


def _static_index_cfg(index, offset, block=2001):
    # DebugLog(block[offset + i]) with i read from a temp, so the index reaches the passes as a
    # value: written as a literal it would be folded into the offset at marshal-in.
    i = BlockPlace(TempBlock("i", 1), 0, 0)
    b0 = BasicBlock(
        statements=[
            IRSet(i, IRPureInstr(Op.Add, [IRConst(index), IRConst(0)])),
            IRInstr(Op.DebugLog, [IRGet(BlockPlace(block, IRGet(i), offset))]),
        ]
    )
    b0.connect_to(BasicBlock(), None)
    return b0


@pytest.mark.parametrize("level_name", ["fast", "standard"])
def test_folded_index_on_a_static_block_emits_the_plain_address(level_name):
    # Cell 8 + 3 of block 2001 is cell 11, and that is the whole address: emitting the sum as a
    # runtime Add costs two nodes per reference for an address known at compile time.
    config = OptimizerConfig(mode=Mode.PLAY, callback="updateParallel")

    value = _emit_optimized_log_arg(_static_index_cfg(3, 8), _PIPELINE_LEVELS[level_name], config)

    assert value == FunctionNode(Op.Get, (2001, 11))


def _rtc_index_cfg(statements, inner_place):
    # DebugLog(LevelMemory[LevelData[...] * 4 + 8]): the read whose index is in question is itself
    # the index of the outer address, which is where its runtime-constant bit gets read.
    index = IRPureInstr(Op.Multiply, [IRGet(inner_place), IRConst(4)])
    b0 = BasicBlock(statements=[*statements, IRInstr(Op.DebugLog, [IRGet(BlockPlace(2000, index, 8))])])
    b0.connect_to(BasicBlock(), None)
    return b0


@pytest.mark.parametrize("level_name", ["fast", "standard"])
def test_folded_index_read_is_classified_runtime_constant(level_name):
    # Baking the index is also what makes the read's runtime-constant bit derivable, since only a
    # constant-index read of LevelData is one the runtime folds. Without the bit the address keeps
    # a Get/SetShifted rewrite that the same read at a literal index declines.
    config = OptimizerConfig(mode=Mode.PLAY, callback="updateParallel")
    i = BlockPlace(TempBlock("i", 1), 0, 0)
    cfg = _rtc_index_cfg([IRSet(i, IRPureInstr(Op.Add, [IRConst(3), IRConst(0)]))], BlockPlace(2001, IRGet(i), 8))

    value = _emit_optimized_log_arg(cfg, _PIPELINE_LEVELS[level_name], config)

    assert value.func == Op.Get


@pytest.mark.parametrize("level_name", ["fast", "standard"])
def test_literal_index_read_is_classified_runtime_constant(level_name):
    # The control for the test above: cell 11 of LevelData again, written as the literal index
    # marshal-in bakes. If this ever stopped declining the rewrite, the test above would be
    # asserting the wrong answer rather than a fixed one.
    config = OptimizerConfig(mode=Mode.PLAY, callback="updateParallel")

    value = _emit_optimized_log_arg(_rtc_index_cfg([], BlockPlace(2001, 11, 0)), _PIPELINE_LEVELS[level_name], config)

    assert value.func == Op.Get


def test_semantic_strided_get_set_match_manual_address():
    # A strided Set then a strided Get land on the same computed address
    # (offset + index*stride), verified against the Interpreter oracle.
    def build():
        b0 = BasicBlock(
            statements=[
                IRSet(BlockPlace(501, 0, 0), IRConst(3)),  # index base i = 3
                IRSet(BlockPlace(500, _strided_index(stride=4), 8), IRConst(42)),  # addr 3*4+8 = 20
                IRSet(BlockPlace(502, 0, 0), IRGet(BlockPlace(500, _strided_index(stride=4), 8))),  # read back
            ]
        )
        b1 = BasicBlock()
        b0.connect_to(b1, None)
        return b0

    it = _assert_semantic_parity(build)
    assert it.blocks[500][20] == 42  # stored at offset + index*stride = 8 + 3*4
    assert it.blocks[502][0] == 42  # GetShifted read it back


def _find_op(node, op, out=None):
    out = [] if out is None else out
    if isinstance(node, FunctionNode):
        if node.func == op:
            out.append(node)
        for arg in node.args:
            _find_op(arg, op, out)
    return out


def test_semantic_strided_fused_rmw_set_add_shifted():
    # A read-modify-write on a strided place fuses to SetAddShifted through the full
    # standard pipeline; the interpreter agrees with the expected stored value.
    from sonolus.backend.optimize import STANDARD_PASSES, OptimizerConfig, optimize_and_finalize
    from sonolus.backend.place import TempBlock

    in_blk, out_blk = 20, 21

    def build():
        arr = TempBlock("arr", 8)
        idx = IRPureInstr(Op.Multiply, [IRGet(BlockPlace(in_blk, 0)), IRConst(2)])
        b0 = BasicBlock(
            statements=[
                IRSet(BlockPlace(arr, idx, 1), IRConst(10)),
                IRSet(
                    BlockPlace(arr, idx, 1),
                    IRPureInstr(Op.Add, [IRGet(BlockPlace(arr, idx, 1)), IRConst(5)]),
                ),
                IRSet(BlockPlace(out_blk, 0), IRGet(BlockPlace(arr, idx, 1))),
            ]
        )
        b1 = BasicBlock()
        b0.connect_to(b1, None)
        return b0

    node = optimize_and_finalize(build(), STANDARD_PASSES, OptimizerConfig())
    assert len(_find_op(node, Op.SetAddShifted)) >= 1, "the strided RMW should fuse to SetAddShifted"
    it = Interpreter()
    it.blocks[3000] = [math.nan, math.inf, -math.inf]
    it.blocks[in_blk] = [3.0]
    it.run(node)
    assert it.get(out_blk, 0) == 15  # (10 + 5) stored at arr[3*2 + 1]


# ---------------------------------------------------------------------------
# n-ary re-flattening: idempotence + right-nesting kept.
# ---------------------------------------------------------------------------


def _reads(*offsets):
    return [IRGet(BlockPlace(500, o, 0)) for o in offsets]


def test_flatten_deep_left_spine():
    a, b, c, d = _reads(0, 1, 2, 3)
    expr = IRPureInstr(Op.Add, [IRPureInstr(Op.Add, [IRPureInstr(Op.Add, [a, b]), c]), d])
    v = _set_value_node(expr)
    assert v.func == Op.Add
    assert list(v.args) == [
        FunctionNode(Op.Get, (500, 0)),
        FunctionNode(Op.Get, (500, 1)),
        FunctionNode(Op.Get, (500, 2)),
        FunctionNode(Op.Get, (500, 3)),
    ]


def test_flatten_already_nary_idempotent():
    # An already-n-ary Add (binarized by marshal-in) re-flattens to the same tree.
    a, b, c = _reads(0, 1, 2)
    v = _set_value_node(IRPureInstr(Op.Add, [a, b, c]))
    assert v.func == Op.Add
    assert list(v.args) == [
        FunctionNode(Op.Get, (500, 0)),
        FunctionNode(Op.Get, (500, 1)),
        FunctionNode(Op.Get, (500, 2)),
    ]


def test_flatten_right_nested_not_flattened():
    # Add(a, Add(b, c)) must NOT flatten (would change FP evaluation order).
    a, b, c = _reads(0, 1, 2)
    v = _set_value_node(IRPureInstr(Op.Add, [a, IRPureInstr(Op.Add, [b, c])]))
    assert v.func == Op.Add
    assert len(v.args) == 2
    assert v.args[0] == FunctionNode(Op.Get, (500, 0))
    assert v.args[1] == FunctionNode(Op.Add, (FunctionNode(Op.Get, (500, 1)), FunctionNode(Op.Get, (500, 2))))


def test_flatten_descends_into_effectful_args():
    # emit descends into impure-instr args: the Add spine inside a DebugLog's args
    # must flatten while DebugLog stays as-is.
    a, b, c = _reads(0, 1, 2)
    add = IRPureInstr(Op.Add, [IRPureInstr(Op.Add, [a, b]), c])
    b0 = BasicBlock(statements=[IRInstr(Op.DebugLog, [add])])
    _node, executes, _idx = _emit_program(b0)
    log = executes[0].args[0]
    assert log.func == Op.DebugLog
    assert log.args[0].func == Op.Add
    assert len(log.args[0].args) == 3


def test_flatten_multiply_left_spine():
    a, b, c = _reads(0, 1, 2)
    v = _set_value_node(IRPureInstr(Op.Multiply, [IRPureInstr(Op.Multiply, [a, b]), c]))
    assert v.func == Op.Multiply
    assert len(v.args) == 3


# ---------------------------------------------------------------------------
# Hash-consing: structurally equal subtrees become the same object.
# ---------------------------------------------------------------------------


def test_hash_consing_shares_equal_subtrees():
    # Two structurally identical reads in one expression collapse to one object.
    r0 = IRGet(BlockPlace(500, 0, 0))
    v = _set_value_node(IRPureInstr(Op.Add, [r0, IRGet(BlockPlace(500, 0, 0))]))
    assert v.func == Op.Add
    assert len(v.args) == 2
    assert v.args[0] is v.args[1]  # same FunctionNode object


def test_hash_consing_distinct_when_different():
    v = _set_value_node(IRPureInstr(Op.Add, _reads(0, 1)))
    assert v.args[0] is not v.args[1]


# ---------------------------------------------------------------------------
# Semantic parity through the Interpreter (both emit paths + hash-consing).
# ---------------------------------------------------------------------------


def _interpret(node):
    it = Interpreter()
    it.blocks[3000] = [math.nan, math.inf, -math.inf]  # ROM: NaN, +Inf, -Inf
    it.run(node)
    return it


def _assert_semantic_parity(build):
    """Emit the CFG and interpret it; the caller's explicit oracle asserts semantics."""
    return _interpret(emit.emit_cfg(build()))


def test_semantic_values_specials_and_shared_subtree():
    def build():
        b0 = BasicBlock()
        b1 = BasicBlock()
        shared = IRGet(BlockPlace(500, 0, 0))
        b0.statements = [
            IRSet(BlockPlace(500, 0, 0), IRConst(7)),
            # 7 + 7 via a shared read subtree (hash-consing collapses it).
            IRSet(BlockPlace(500, 1, 0), IRPureInstr(Op.Add, [shared, IRGet(BlockPlace(500, 0, 0))])),
            IRInstr(Op.DebugLog, [IRGet(BlockPlace(500, 1, 0))]),
            IRSet(BlockPlace(500, 2, 0), IRConst(math.inf)),  # via ROM
            IRSet(BlockPlace(500, 3, 0), IRConst(math.nan)),  # via ROM
        ]
        b0.connect_to(b1, None)
        return b0

    it = _assert_semantic_parity(build)
    assert it.log == [14.0]
    assert it.blocks[500][0] == 7
    assert it.blocks[500][1] == 14
    assert math.isinf(it.blocks[500][2])
    assert it.blocks[500][2] > 0
    assert math.isnan(it.blocks[500][3])

    # The shared subtree really is one object in the emitted tree.
    node = emit.emit_cfg(build())
    add = node.args[0].args[0].args[1].args[2]  # Block>JumpLoop>Execute>Set>value
    assert add.func == Op.Add
    assert add.args[0] is add.args[1]


def test_semantic_if_branch_both_directions():
    def build_for(x):
        def build():
            b0 = BasicBlock(test=IRGet(BlockPlace(500, 0, 0)))
            bt, bf, exit_b = BasicBlock(), BasicBlock(), BasicBlock()
            b0.statements = [IRSet(BlockPlace(500, 0, 0), IRConst(x))]
            bt.statements = [IRInstr(Op.DebugLog, [IRConst(111)])]
            bf.statements = [IRInstr(Op.DebugLog, [IRConst(222)])]
            b0.connect_to(bf, 0)
            b0.connect_to(bt, None)
            bt.connect_to(exit_b, None)
            bf.connect_to(exit_b, None)
            return b0

        return build

    _assert_semantic_parity(build_for(0))  # false branch
    _assert_semantic_parity(build_for(1))  # true branch


def test_semantic_switch_dispatch():
    def build_for(sel):
        def build():
            b0 = BasicBlock(test=IRGet(BlockPlace(500, 0, 0)))
            cases = [BasicBlock() for _ in range(3)]
            bd, exit_b = BasicBlock(), BasicBlock()
            b0.statements = [IRSet(BlockPlace(500, 0, 0), IRConst(sel))]
            for i, bc in enumerate(cases):
                bc.statements = [IRInstr(Op.DebugLog, [IRConst(10 + i)])]
                b0.connect_to(bc, i)
                bc.connect_to(exit_b, None)
            bd.statements = [IRInstr(Op.DebugLog, [IRConst(99)])]
            b0.connect_to(bd, None)
            bd.connect_to(exit_b, None)
            return b0

        return build

    for sel in (0, 1, 2, 5):  # 5 falls through to default
        _assert_semantic_parity(build_for(sel))


def test_semantic_pointer_deref():
    def build():
        b0, b1 = BasicBlock(), BasicBlock()
        b0.statements = [
            IRSet(BlockPlace(600, 3, 0), IRConst(500)),  # mem[600][3] = block id 500
            IRSet(BlockPlace(500, 5, 0), IRConst(42)),  # mem[500][5] = 42
            # Read mem[mem[600][3]][5] via a pointer-deref place -> Get(Get(600,3),5)
            IRInstr(Op.DebugLog, [IRGet(BlockPlace(BlockPlace(600, 3, 0), 5, 0))]),
        ]
        b0.connect_to(b1, None)
        return b0

    it = _assert_semantic_parity(build)
    assert it.log == [42.0]


def test_semantic_nary_flatten_preserves_result():
    def build():
        b0, b1 = BasicBlock(), BasicBlock()
        reads = [IRGet(BlockPlace(500, i, 0)) for i in range(4)]
        spine = reads[0]
        for r in reads[1:]:
            spine = IRPureInstr(Op.Add, [spine, r])
        b0.statements = [
            IRSet(BlockPlace(500, 0, 0), IRConst(1)),
            IRSet(BlockPlace(500, 1, 0), IRConst(2)),
            IRSet(BlockPlace(500, 2, 0), IRConst(4)),
            IRSet(BlockPlace(500, 3, 0), IRConst(8)),
            IRInstr(Op.DebugLog, [spine]),
        ]
        b0.connect_to(b1, None)
        return b0

    it = _assert_semantic_parity(build)
    assert it.log == [15.0]
    # emit flattens the spine; result is unchanged.
    node = emit.emit_cfg(build())
    add = node.args[0].args[0].args[4].args[0]
    assert add.func == Op.Add
    assert len(add.args) == 4


def test_format_engine_node_smoke():
    # A quick end-to-end format check (readability of the emitted tree).
    b0, b1 = BasicBlock(), BasicBlock()
    b0.statements = [IRSet(BlockPlace(500, 0, 0), IRConst(3))]
    b0.connect_to(b1, None)
    text = format_engine_node(emit.emit_cfg(b0))
    assert text.startswith("Block(")
    assert "JumpLoop" in text
    assert "Execute" in text
