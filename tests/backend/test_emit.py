"""Tests for the arena EngineNode emitter (``sonolus.backend._opt.emit``).

The emitter builds the EngineNode tree from the flat ``Func`` arena, re-flattening
associative left spines (``Add``/``Multiply``/``Mod``/``Rem``) as it builds.

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

from sonolus.backend._opt import emit  # noqa: PLC2701
from sonolus.backend.interpret import Interpreter
from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet
from sonolus.backend.node import FunctionNode, format_engine_node
from sonolus.backend.ops import Op
from sonolus.backend.optimize.flow import BasicBlock, traverse_cfg_reverse_postorder
from sonolus.backend.place import BlockPlace

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
# SwitchWithDefault instead of crashing (OverflowError / ValueError). ---


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


def test_terminator_switch_inf_case_falls_back():
    b0 = BasicBlock(test=IRGet(BlockPlace(500, 0, 0)))
    b_a, b_b, bd = BasicBlock(), BasicBlock(), BasicBlock()
    b0.connect_to(b_a, 0)
    b0.connect_to(b_b, math.inf)
    b0.connect_to(bd, None)
    _node, executes, idx = _emit_program(b0)
    t = _term(executes[idx[b0]])
    assert t.func == Op.SwitchWithDefault
    assert list(t.args[1:5]) == [0, idx[b_a], math.inf, idx[b_b]]
    assert t.args[5] == idx[bd]


def test_terminator_switch_nan_case_falls_back():
    b0 = BasicBlock(test=IRGet(BlockPlace(500, 0, 0)))
    b_a, b_b, bd = BasicBlock(), BasicBlock(), BasicBlock()
    b0.connect_to(b_a, 0)
    b0.connect_to(b_b, math.nan)
    b0.connect_to(bd, None)
    _node, executes, idx = _emit_program(b0)
    t = _term(executes[idx[b0]])
    assert t.func == Op.SwitchWithDefault  # NaN is never a dense case -> no crash
    assert len(t.args) == 6  # test + (cond,target) x2 + default


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
