"""Out-of-SSA lowering + treeify tests.

Three layers, mirroring test_ssa.py:

* scheduling / treeify units -- inspect ``cfg_to_text`` of ``lower_debug`` to
  assert the fold / duplicate / materialize decision, phi elimination
  (critical-edge splits, cycle temps, UNDEF), n-ary flattening + identity
  dropping, and ``normalize_switch``;
* semantic parity -- the random-CFG property recipes interpreted through
  ``run_lower`` vs the MINIMAL reference (observables equal);
* corpus -- every pydori callback through ``run_lower``: verify() green,
  allocation fits, emission succeeds, with a naive-vs-treeify node-count report.
"""

from __future__ import annotations

import math
import struct

import pytest
from hypothesis import HealthCheck, given, settings

from sonolus.backend._opt import ir, lower  # noqa: PLC2701
from sonolus.backend.blocks import PlayBlock
from sonolus.backend.interpret import Interpreter
from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet
from sonolus.backend.mode import Mode
from sonolus.backend.node import FunctionNode
from sonolus.backend.ops import Op
from sonolus.backend.optimize import MINIMAL_PASSES, OptimizerConfig, cfg_to_engine_node, run_passes
from sonolus.backend.optimize.flow import BasicBlock, cfg_to_text, traverse_cfg_reverse_postorder
from sonolus.backend.place import BlockPlace, TempBlock
from tests.backend._cfg_gen import OBS_BLOCKS, OBS_CAPTURE_LEN, build_cfg, programs
from tests.backend._corpus import MODE_SETUP, iter_callbacks

_ROM = [float("nan"), float("inf"), -float("inf")]


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _sc(name: str) -> BlockPlace:
    return BlockPlace(TempBlock(name, 1), 0, 0)


def _rd(name: str) -> IRGet:
    return IRGet(_sc(name))


def _low_text(cfg: BasicBlock, mode=None, cb=None) -> str:
    return cfg_to_text(lower.lower_debug(cfg, mode, cb))


def _interp(node, rom=None) -> Interpreter:
    it = Interpreter()
    it.blocks[3000] = list(rom) if rom is not None else list(_ROM)
    it.run(node)
    return it


def _run_ref(build, mode=None, cb=None, rom=None) -> Interpreter:
    return _interp(
        cfg_to_engine_node(run_passes(build(), MINIMAL_PASSES, OptimizerConfig(mode=mode, callback=cb))), rom
    )


def _run_low(build, mode=None, cb=None, rom=None) -> Interpreter:
    return _interp(cfg_to_engine_node(lower.run_lower(build(), mode, cb)), rom)


def _observable_memory(it: Interpreter) -> dict:
    return {b: v for b, v in it.blocks.items() if b not in {10000, 3000}}


def _assert_parity(build, mode=None, cb=None, rom=None):
    ref = _run_ref(build, mode, cb, rom)
    low = _run_low(build, mode, cb, rom)
    assert ref.log == low.log, f"log mismatch: {ref.log} vs {low.log}"
    assert _observable_memory(ref) == _observable_memory(low), "observable memory mismatch"
    return ref, low


# ---------------------------------------------------------------------------
# Scheduling decisions (structural).
# ---------------------------------------------------------------------------


_RU = PlayBlock.RuntimeUpdate  # readable, non-writable, NOT a RUNTIME_CONSTANT_BLOCK


def _ru(idx: int) -> IRGet:
    return IRGet(BlockPlace(_RU, idx))


def _diamond_defer(def_expr):
    # b0 defines x = def_expr (a single value), then branches; only the TRUE arm
    # uses x, so x's single use is CROSS-BLOCK (b0 dominates the arm). Two-statement
    # arms keep cfg_cleanup from tail-duplicating them and dissolving the merge.
    b0 = BasicBlock(test=_ru(9), statements=[IRSet(_sc("x"), def_expr)])
    t = BasicBlock(statements=[IRInstr(Op.DebugLog, [_rd("x")]), IRInstr(Op.DebugPause, [IRConst(0)])])
    f = BasicBlock(statements=[IRInstr(Op.DebugLog, [IRConst(0)]), IRInstr(Op.DebugPause, [IRConst(0)])])
    join = BasicBlock(statements=[IRInstr(Op.DebugLog, [IRConst(7)]), IRInstr(Op.DebugPause, [IRConst(7)])])
    b0.connect_to(f, 0)
    b0.connect_to(t, None)
    t.connect_to(join, None)
    f.connect_to(join, None)
    return b0


def test_single_use_pure_folds_cross_block():
    # x = Sign(RuntimeUpdate[0]) defined in b0, used once in the true arm
    # (cross-block, no loop, inlinable non-runtime-const). It folds: the Sign
    # expression appears in the arm, with no dedicated temp store.
    cfg = _diamond_defer(IRPureInstr(Op.Sign, [_ru(0)]))
    text = _low_text(cfg, Mode.PLAY, "updateSequential")
    assert "DebugLog(Sign(RuntimeUpdate[0]))" in text, text
    assert "<- Sign(" not in text, text  # not materialized to a temp


def test_single_use_does_not_sink_into_deeper_loop():
    # x = Sign(RuntimeUpdate[0]) defined before a loop, used once INSIDE the loop
    # body. Folding would sink the computation into the loop; treeify materializes
    # it instead (a single eval hoisted out, read via a temp inside).
    pre = BasicBlock(statements=[IRSet(_sc("x"), IRPureInstr(Op.Sign, [_ru(0)])), IRSet(_sc("k"), IRConst(0))])
    head = BasicBlock(test=IRPureInstr(Op.Less, [_rd("k"), IRConst(3)]))
    body = BasicBlock(statements=[IRInstr(Op.DebugLog, [_rd("x")])])
    step = BasicBlock(statements=[IRSet(_sc("k"), IRPureInstr(Op.Add, [_rd("k"), IRConst(1)]))])
    ex = BasicBlock()
    pre.connect_to(head, None)
    head.connect_to(body, None)
    head.connect_to(ex, 0)
    body.connect_to(step, None)
    step.connect_to(head, None)
    text = _low_text(pre, Mode.PLAY, "updateSequential")
    # Sign is computed ONCE (materialized before the loop), read via a temp inside.
    assert text.count("Sign(") == 1, text
    assert "<- Sign(" in text, text


def test_multi_use_cheap_expr_duplicates():
    # A bare non-writable read (cost 3 < 4) used twice duplicates -- no temp.
    b0 = BasicBlock(
        statements=[
            IRSet(_sc("x"), _ru(0)),
            IRInstr(Op.DebugLog, [_rd("x")]),
            IRInstr(Op.DebugLog, [_rd("x")]),
        ]
    )
    b1 = BasicBlock()
    b0.connect_to(b1, None)
    text = _low_text(b0, Mode.PLAY, "updateSequential")
    assert text.count("RuntimeUpdate[0]") == 2, text  # duplicated at both uses
    assert "<- RuntimeUpdate" not in text, text  # not materialized


def test_multi_use_expensive_expr_materializes():
    # A cost>=4 inlinable expr (Add of two non-writable reads, cost 7) used twice
    # is extracted into ONE temp, read at both uses.
    b0 = BasicBlock(
        statements=[
            IRSet(_sc("e"), IRPureInstr(Op.Add, [_ru(0), _ru(1)])),
            IRInstr(Op.DebugLog, [_rd("e")]),
            IRInstr(Op.DebugLog, [_rd("e")]),
        ]
    )
    b1 = BasicBlock()
    b0.connect_to(b1, None)
    text = _low_text(b0, Mode.PLAY, "updateSequential")
    assert text.count("RuntimeUpdate[0]") == 1, text  # folded once into the temp
    assert "<- " in text, text  # materialized to a temp


def _rom(idx: int) -> IRGet:
    return IRGet(BlockPlace(PlayBlock.EngineRom, idx))


def test_runtime_constant_tree_duplicates_regardless_of_size():
    # A large pure tree over EngineRom reads (runtime-constant under PLAY) is
    # duplicated into every use even though its size >> 4, because a temp would
    # defeat the runtime's own constant folding (effective cost 1).
    def big():
        t = _rom(0)
        for i in range(1, 6):
            t = IRPureInstr(Op.Add, [t, _rom(i)])
        return t

    b0 = BasicBlock(
        statements=[
            IRInstr(Op.DebugLog, [big()]),
            IRInstr(Op.DebugLog, [big()]),
            IRInstr(Op.DebugLog, [big()]),
        ]
    )
    b1 = BasicBlock()
    b0.connect_to(b1, None)
    text = _low_text(b0, Mode.PLAY, "updateSequential")
    # Duplicated: the EngineRom reads appear in all three logs, no temp extraction.
    assert text.count("EngineRom[5]") == 3, text
    assert "<- " not in text, text


def test_writable_block_const_index_read_not_duplicated():
    # Deliberate divergence: a constant-index read of a WRITABLE block used
    # multiple times is materialized, never duplicated (duplication across a write
    # could observe it). Raw int block 20 with mode=None is conservatively writable.
    b0 = BasicBlock(
        statements=[
            IRSet(_sc("x"), IRGet(BlockPlace(20, 0))),  # one read value
            IRInstr(Op.DebugLog, [_rd("x")]),
            IRInstr(Op.DebugLog, [_rd("x")]),
            IRInstr(Op.DebugLog, [_rd("x")]),
        ]
    )
    b1 = BasicBlock()
    b0.connect_to(b1, None)
    text = _low_text(b0)
    # Materialized to one temp, read three times: the "20[0]" place text appears
    # exactly once (the temp store), not at the three uses.
    assert text.count("20[0]") == 1, text
    assert "<- 20[0]" in text, text


def test_pinned_read_fold_blocked_by_intervening_effect():
    # A single-use writable read folds into its consumer only if no effect lies
    # between def and use. With an intervening store it materializes instead.
    def with_effect():
        b0 = BasicBlock(
            statements=[
                IRSet(_sc("x"), IRGet(BlockPlace(20, 0))),  # read
                IRSet(BlockPlace(21, 0), IRConst(9)),  # intervening effect
                IRInstr(Op.DebugLog, [_rd("x")]),  # use
            ]
        )
        b1 = BasicBlock()
        b0.connect_to(b1, None)
        return b0

    def no_effect():
        b0 = BasicBlock(
            statements=[
                IRSet(_sc("x"), IRGet(BlockPlace(20, 0))),
                IRInstr(Op.DebugLog, [_rd("x")]),
            ]
        )
        b1 = BasicBlock()
        b0.connect_to(b1, None)
        return b0

    te = _low_text(with_effect())
    tn = _low_text(no_effect())
    # blocked -> the read is stored to a temp (a "<- 20[0]" materialization).
    assert "<- 20[0]" in te, te
    # allowed -> folded straight into the DebugLog, no temp store of the read.
    assert "DebugLog(20[0])" in tn, tn


def _undef_build():
    b0 = BasicBlock(statements=[IRInstr(Op.DebugLog, [_rd("u")]), IRInstr(Op.DebugLog, [_rd("w")])])
    b1 = BasicBlock()
    b0.connect_to(b1, None)
    return b0


def test_undef_read_lowers_to_shared_temp():
    # Reads of two distinct never-written scalars share ONE never-written temp.
    text = _low_text(_undef_build())
    logged = [line for line in text.splitlines() if "DebugLog" in line]
    # Both logs read the same shared undef temp.
    assert logged[0].split("DebugLog(")[1] == logged[1].split("DebugLog(")[1], logged
    # Undef reads a never-written scalar -> -1.0 in the interpreter.
    it = _run_low(_undef_build)
    assert it.log == [-1.0, -1.0]


# ---------------------------------------------------------------------------
# Coalescing safety around the shared UNDEF slot. Two loop phis both fed
# by the single UNDEF value get a chained copy from the parallel-copy
# sequentializer (``a <- undef; b <- a``); if one phi is a dead loop-carried
# temp, the def-point interference graph must still record the a<->b edge (a
# dead store records no interference for its target), or coalescing merges two
# independent variables into one slot and the observed variable gets clobbered
# by the "dead" sibling's update. All levels must observe the
# UNDEF-seeded value (never-written scalar -> -1.0), same as the MINIMAL bump
# reference. These are the hand-minimized directed cases.
# ---------------------------------------------------------------------------


def _undef_loop_dead_sibling():
    # s_obs and s_dead are BOTH read undefined on iteration 1 (loop-carried from
    # UNDEF via the header phi) and both read-modify-written; s_dead is never
    # observed. s_obs must stay in its own slot: logged before its /2 update, so
    # -1.0, -0.5, -0.25, -0.125 -- NOT -1/14/14/14 (the bug divided the shared
    # slot by both 2 and 7 each iteration).
    init = BasicBlock(statements=[IRSet(_sc("k"), IRConst(0))])
    header = BasicBlock(test=IRPureInstr(Op.Less, [_rd("k"), IRConst(4)]))
    body = BasicBlock(
        statements=[
            IRInstr(Op.DebugLog, [_rd("s_obs")]),
            IRSet(_sc("s_obs"), IRPureInstr(Op.Divide, [_rd("s_obs"), IRConst(2)])),
            IRSet(_sc("s_dead"), IRPureInstr(Op.Divide, [_rd("s_dead"), IRConst(7)])),
        ]
    )
    step = BasicBlock(statements=[IRSet(_sc("k"), IRPureInstr(Op.Add, [_rd("k"), IRConst(1)]))])
    after = BasicBlock()
    init.connect_to(header, None)
    header.connect_to(body, None)
    header.connect_to(after, 0)
    body.connect_to(step, None)
    step.connect_to(header, None)
    return init


def test_undef_loop_dead_sibling_not_coalesced():
    _ref, low = _assert_parity(_undef_loop_dead_sibling)
    assert low.log == [-1.0, -0.5, -0.25, -0.125]


def _undef_loop_two_observed():
    # Both loop-carried-from-UNDEF scalars are observed (both live). They must
    # stay distinct: s_a divides by 2, s_b by 4, independently.
    init = BasicBlock(statements=[IRSet(_sc("k"), IRConst(0))])
    header = BasicBlock(test=IRPureInstr(Op.Less, [_rd("k"), IRConst(3)]))
    body = BasicBlock(
        statements=[
            IRInstr(Op.DebugLog, [_rd("s_a")]),
            IRInstr(Op.DebugLog, [_rd("s_b")]),
            IRSet(_sc("s_a"), IRPureInstr(Op.Divide, [_rd("s_a"), IRConst(2)])),
            IRSet(_sc("s_b"), IRPureInstr(Op.Divide, [_rd("s_b"), IRConst(4)])),
        ]
    )
    step = BasicBlock(statements=[IRSet(_sc("k"), IRPureInstr(Op.Add, [_rd("k"), IRConst(1)]))])
    after = BasicBlock()
    init.connect_to(header, None)
    header.connect_to(body, None)
    header.connect_to(after, 0)
    body.connect_to(step, None)
    step.connect_to(header, None)
    return init


def test_undef_loop_two_observed_stay_distinct():
    _ref, low = _assert_parity(_undef_loop_two_observed)
    # s_a: -1, -0.5, -0.25 ; s_b: -1, -0.25, -0.0625, interleaved.
    assert low.log == [-1.0, -1.0, -0.5, -0.25, -0.25, -0.0625]


def _undef_through_copies():
    # An undef value flows through a chain of coalescible copies before use, and
    # a dead sibling copy chain shares the UNDEF. c is observed; d is dead.
    b0 = BasicBlock(
        statements=[
            IRSet(_sc("c"), _rd("u")),  # c <- undef
            IRSet(_sc("d"), _rd("u")),  # d <- undef (dead)
            IRSet(BlockPlace(20, 0), _rd("c")),  # observe c
            IRSet(_sc("d"), IRPureInstr(Op.Add, [_rd("d"), IRConst(5)])),  # dead update of d
        ]
    )
    b1 = BasicBlock()
    b0.connect_to(b1, None)
    return b0


def test_undef_through_coalescible_copies():
    _ref, low = _assert_parity(_undef_through_copies)
    assert low.get(20, 0) == -1.0


def _many_undef_with_written():
    # Several distinct never-written scalars (may share the ONE undef slot with
    # each other) coexist with a written, observed temp. The undef reads must
    # yield -1.0; the written temp must keep its value (7), never aliased onto
    # the undef slot.
    b0 = BasicBlock(
        statements=[
            IRSet(_sc("w"), IRConst(7)),
            IRInstr(Op.DebugLog, [_rd("u0")]),
            IRInstr(Op.DebugLog, [_rd("u1")]),
            IRInstr(Op.DebugLog, [_rd("u2")]),
            IRInstr(Op.DebugLog, [_rd("w")]),
            IRSet(BlockPlace(20, 0), _rd("u0")),
            IRSet(BlockPlace(20, 1), _rd("w")),
        ]
    )
    b1 = BasicBlock()
    b0.connect_to(b1, None)
    return b0


def test_many_undef_temps_never_alias_written():
    _ref, low = _assert_parity(_many_undef_with_written)
    assert low.log == [-1.0, -1.0, -1.0, 7.0]
    assert low.get(20, 0) == -1.0
    assert low.get(20, 1) == 7.0


# ---------------------------------------------------------------------------
# Phi elimination.
# ---------------------------------------------------------------------------


def _swap_loop():
    b0 = BasicBlock(statements=[IRSet(_sc("a"), IRConst(1)), IRSet(_sc("b"), IRConst(2)), IRSet(_sc("n"), IRConst(0))])
    head = BasicBlock(test=IRPureInstr(Op.Less, [_rd("n"), IRConst(3)]))
    body = BasicBlock(statements=[IRSet(_sc("t"), _rd("a")), IRSet(_sc("a"), _rd("b")), IRSet(_sc("b"), _rd("t"))])
    step = BasicBlock(statements=[IRSet(_sc("n"), IRPureInstr(Op.Add, [_rd("n"), IRConst(1)]))])
    ex = BasicBlock(statements=[IRInstr(Op.DebugLog, [_rd("a")]), IRInstr(Op.DebugLog, [_rd("b")])])
    b0.connect_to(head, None)
    head.connect_to(body, None)
    head.connect_to(ex, 0)
    body.connect_to(step, None)
    step.connect_to(head, None)
    return b0


def test_phi_swap_cycle_sequentialized():
    _ref, low = _assert_parity(_swap_loop)
    assert low.log == [2.0, 1.0]  # three swaps of (1,2)


def test_critical_edge_split_creates_block():
    # A self-loop header has two successors (itself + exit) and two predecessors
    # (preheader + itself), so the back edge head->head is critical: out-of-SSA
    # inserts a split block for its phi copies, adding a block vs the SSA form.
    def build():
        b0, head, ex = BasicBlock(), BasicBlock(), BasicBlock()
        b0.statements = [IRSet(_sc("i"), IRConst(0))]
        b0.connect_to(head, None)
        head.statements = [IRSet(_sc("i"), IRPureInstr(Op.Add, [_rd("i"), IRConst(1)]))]
        head.test = IRPureInstr(Op.Less, [_rd("i"), IRConst(10)])
        head.connect_to(head, None)
        head.connect_to(ex, 0)
        ex.statements = [IRInstr(Op.DebugLog, [_rd("i")]), IRInstr(Op.DebugPause, [_rd("i")])]
        return b0

    lowered = lower.lower_debug(build())
    n_low = len(list(traverse_cfg_reverse_postorder(lowered)))
    n_ssa = len(list(traverse_cfg_reverse_postorder(ir.debug_run(build(), phases=["cfg_cleanup", "ssa"]))))
    assert n_low > n_ssa, (n_low, n_ssa)
    _assert_parity(build)


# ---------------------------------------------------------------------------
# n-ary emission: flatten + identity dropping.
# ---------------------------------------------------------------------------


def test_left_spine_flattened_to_nary():
    # ((a+b)+c)+d emitted as a single n-ary Add(a, b, c, d).
    expr = IRPureInstr(
        Op.Add,
        [IRPureInstr(Op.Add, [IRPureInstr(Op.Add, [_rd("a"), _rd("b")]), _rd("c")]), _rd("d")],
    )
    b0 = BasicBlock(
        statements=[
            IRSet(_sc("a"), IRConst(1)),
            IRSet(_sc("b"), IRConst(2)),
            IRSet(_sc("c"), IRConst(3)),
            IRSet(_sc("d"), IRConst(4)),
            IRInstr(Op.DebugLog, [expr]),
        ]
    )
    b1 = BasicBlock()
    b0.connect_to(b1, None)
    node = cfg_to_engine_node(lower.run_lower(b0))
    add = _find_op(node, Op.Add)
    assert add is not None, node
    assert len(add.args) == 4, add


def test_identity_dropping_inside_impure_instr_args():
    # Add(x, 0) folds to x even when nested inside an impure Set's value tree
    # (identity dropping recurses into impure instrs' args).
    val = IRPureInstr(Op.Add, [_rd("x"), IRConst(0)])
    b0 = BasicBlock(
        statements=[
            IRSet(_sc("x"), IRConst(5)),
            IRSet(BlockPlace(20, 0), val),  # Set (impure) whose value is Add(x, 0)
        ]
    )
    b1 = BasicBlock()
    b0.connect_to(b1, None)
    node = cfg_to_engine_node(lower.run_lower(b0))
    assert _find_op(node, Op.Add) is None, "Add(x, 0) should be dropped to x"
    _assert_parity(_rebuild_identity)


def _rebuild_identity():
    val = IRPureInstr(Op.Add, [_rd("x"), IRConst(0)])
    b0 = BasicBlock(statements=[IRSet(_sc("x"), IRConst(5)), IRSet(BlockPlace(20, 0), val)])
    b1 = BasicBlock()
    b0.connect_to(b1, None)
    return b0


def _find_op(node, op):
    if isinstance(node, FunctionNode):
        if node.func == op:
            return node
        for a in node.args:
            r = _find_op(a, op)
            if r is not None:
                return r
    return None


# ---------------------------------------------------------------------------
# normalize_switch.
# ---------------------------------------------------------------------------


def _switch(conds, default: bool):
    a = BasicBlock(
        statements=[IRSet(_sc("s"), IRConst(0))], test=IRPureInstr(Op.Floor, [IRPureInstr(Op.Abs, [_rd("s")])])
    )
    join = BasicBlock(statements=[IRInstr(Op.DebugLog, [IRConst(1)]), IRInstr(Op.DebugPause, [IRConst(1)])])
    for c in conds:
        blk = BasicBlock(statements=[IRInstr(Op.DebugLog, [IRConst(c)])])
        a.connect_to(blk, c)
        blk.connect_to(join, None)
    if default:
        d = BasicBlock(statements=[IRInstr(Op.DebugLog, [IRConst(-1)])])
        a.connect_to(d, None)
        d.connect_to(join, None)
    return a


def _switch_conds(cfg: BasicBlock) -> set:
    entry = lower.lower_debug(cfg)
    return {e.cond for e in entry.outgoing}


def test_normalize_switch_progression_with_default():
    text = _low_text(_switch([4, 6, 8], default=True))
    assert _switch_conds(_switch([4, 6, 8], default=True)) == {0, 1, 2, None}
    assert "- 4) / 2" in text, text


def test_normalize_switch_default_less_progression():
    conds = _switch_conds(_switch([4, 6, 8], default=False))
    assert conds == {0, 1, 2}, conds
    assert "- 4) / 2" in _low_text(_switch([4, 6, 8], default=False))


def test_normalize_switch_k2_not_normalized():
    # Two numeric cases (default-less) is not a switch -> untouched.
    conds = _switch_conds(_switch([4, 6], default=False))
    assert conds == {4, 6}, conds


def test_normalize_switch_non_progression_untouched():
    conds = _switch_conds(_switch([0, 3, 7], default=True))
    assert conds == {0, 3, 7, None}, conds


def test_normalize_switch_already_contiguous_untouched():
    # Already 0,1,2 -> no rewrite (offset 0, stride 1); test not wrapped in div/sub.
    text = _low_text(_switch([0, 1, 2], default=True))
    assert " / " not in text, text
    assert " - " not in text, text
    assert _switch_conds(_switch([0, 1, 2], default=True)) == {0, 1, 2, None}


# --- normalize_switch correctness. The runtime is 32-bit float (exact integers
# in [-2^24, 2^24]); the synthesized ``(test - off) / stride`` is evaluated in f32,
# so a case magnitude or case-set span beyond 2^24 makes that arithmetic inexact
# and the int64 case rewrite disagrees with the f32 dispatch. The switch scrutinee
# is an OPAQUE EngineRom[0] read (seed ``rom=[testval]``) so the switch survives
# cfg_cleanup/SSA to _normalize_switch -- a written-then-read constant would be
# folded away before lowering and pin nothing. The f64 oracle cannot see an f32
# mis-dispatch, so for out-of-range sets we assert the guard LEFT THE SWITCH PLAIN
# (no synthesized divide), not that the oracle agrees. ---


def _switch_dispatch(conds, testval):
    """Multiway block dispatching on an opaque ``EngineRom[0]`` read == ``testval``.

    Case ``i`` logs ``100+i``, the default logs 199. The scrutinee is opaque so the
    switch reaches _normalize_switch; the reader seeds it with ``rom=[testval]``.
    """
    b0 = BasicBlock(test=_rom(0))
    join = BasicBlock(statements=[IRInstr(Op.DebugLog, [IRConst(-2)])])
    for i, c in enumerate(conds):
        blk = BasicBlock(statements=[IRInstr(Op.DebugLog, [IRConst(100 + i)])])
        b0.connect_to(blk, c)
        blk.connect_to(join, None)
    d = BasicBlock(statements=[IRInstr(Op.DebugLog, [IRConst(199)])])
    b0.connect_to(d, None)
    d.connect_to(join, None)
    return b0


def _lowered_switch_conds(conds, testval) -> set:
    return _switch_conds(_switch_dispatch(conds, testval))


def _assert_switch_dispatch_parity(conds, testval):
    # f64 differential parity: the un-normalizing MINIMAL reference and the
    # normalizing run_lower interpret to identical logs for an actual case value.
    build = lambda: _switch_dispatch(conds, testval)  # noqa: E731
    ref = _run_ref(build, rom=[testval])
    low = _interp(cfg_to_engine_node(lower.run_lower(build())), rom=[testval])
    assert ref.log == low.log, f"conds={conds} test={testval!r}: ref={ref.log} low={low.log}"


@pytest.mark.parametrize("testval", [3, 4, 6, 8, 5, -1])
def test_normalize_switch_in_range_progression_normalizes(testval):
    # Spread 4 (<= 2^24) exact progression: still normalized to 0,1,2, and the
    # normalized dispatch matches the oracle for an actual case value.
    assert _lowered_switch_conds([4, 6, 8], testval) == {0, 1, 2, None}
    _assert_switch_dispatch_parity([4, 6, 8], testval)


@pytest.mark.parametrize("testval", [-2000000000, 3, 2000000003, 42])
def test_normalize_switch_spread_over_2p31_no_miscompile(testval):
    # Spread far exceeds 2^24: the guard declines (also exercises int64 diff/gcd/span
    # -- in 32-bit ``long`` the spread wrapped negative). Left as a plain switch.
    _assert_switch_dispatch_parity([-2000000000, 3, 2000000003], testval)


# Sets the 2^24 guard must leave UN-normalized (spread > 2^24 or magnitude > 2^24):
# the f32 ``(test - off)/stride`` would be inexact, so the switch stays a plain
# SwitchWithDefault (conds == the original case values, no synthesized divide).
_OUT_OF_RANGE_SETS = [
    [-3, 8388612, 16777227],  # magnitude 16777227 = 2^24+11 > 2^24
    [0, 5, 2**31],  # magnitude > 2^24
    [0, 5, 2**53],  # magnitude > 2^24
    [0, 5, 2**62],  # magnitude beyond int64-exact f64 range
    [0, 2**40, 2**41],  # exact progression, magnitude > 2^24
    [-8388609, 4, 8388617],  # exact progression, each |case| < 2^24 but span 2^24+10 > 2^24
    [-8388606, 8, 8388622],  # exact progression, each |case| < 2^24 but span 2^24+12 > 2^24
]


@pytest.mark.parametrize("cases", _OUT_OF_RANGE_SETS)
def test_normalize_switch_out_of_range_left_plain(cases):
    # Structural: the guard declined, so conds are unchanged and no ``(test-off)/
    # stride`` divide was synthesized. (The f64 oracle cannot see the f32
    # mis-dispatch a normalization would cause, so we assert the decline directly.)
    assert _lowered_switch_conds(cases, cases[0]) == set(cases) | {None}
    assert " / " not in _low_text(_switch_dispatch(cases, cases[0]))


@pytest.mark.parametrize("testval", [-2000000000, 3, 2000000003, 0, 5, 42])
def test_normalize_switch_out_of_range_dispatch_parity(testval):
    # And the plain switch still dispatches actual case values correctly (f64
    # oracle parity, since a plain equality dispatch is f32-exact too).
    _assert_switch_dispatch_parity([-2000000000, 3, 2000000003], testval)


def test_normalize_switch_f32_miscompile_prevented():
    # The runtime evaluates (test - off)/stride in f32. A reverted (2^53) guard
    # would take the affine path off=-3, stride=8388615 -> conds 0,1,2. The case
    # value 16777227 is f32(16777227) == 16777228 at runtime, and
    # (16777228 - (-3)) / 8388615 is NOT an exact integer in f32, so case #2 would
    # be sent to the DEFAULT. The f64 oracle cannot see this, so we (a) model the
    # mis-dispatch in numpy.float32 and (b) assert the guard left the switch plain
    # (exact f32 equality dispatch instead).
    np = pytest.importorskip("numpy")
    f32 = np.float32
    cases = [-3, 8388612, 16777227]
    off, stride = -3, 8388615  # what the reverted affine path would synthesize
    # (a) f32(16777227) rounds to 16777228, and its normalized index is non-integral:
    case2 = f32(16777227)
    assert float(case2) == 16777228.0
    idx = f32(f32(case2 - f32(off)) / f32(stride))
    assert float(idx) != float(int(idx)), "reverted normalization mis-dispatches case #2 to default"
    # (b) the guard declined, so the switch is plain and dispatches by exact equality:
    assert _lowered_switch_conds(cases, cases[0]) == set(cases) | {None}
    assert " / " not in _low_text(_switch_dispatch(cases, cases[0]))


@pytest.mark.parametrize("testval", [0.0, 2.0, 4.0, math.inf])
def test_normalize_switch_inf_case_no_crash(testval):
    # A +inf case must not crash int()/the <int64> cast; the switch is left plain
    # and dispatches finite values (and inf itself) correctly.
    assert " / " not in _low_text(_switch_dispatch([0, 2, math.inf], testval))
    _assert_switch_dispatch_parity([0, 2, math.inf], testval)


@pytest.mark.parametrize("testval", [0.0, 2.0, 4.0])
def test_normalize_switch_nan_case_no_crash(testval):
    # A NaN case must not raise ValueError from int(); left plain, dispatch correct.
    assert " / " not in _low_text(_switch_dispatch([0, 2, math.nan], testval))
    _assert_switch_dispatch_parity([0, 2, math.nan], testval)


# --- A constant dynamic-block id is folded to a static REAL_BLOCK only when it is
# a finite int32 integer; an out-of-range / non-integral id must keep its exact
# value (folding it into the int32 block_ref field would truncate/wrap it). ---


def _dynblock_get(block_expr):
    # DebugLog(read of a place whose BLOCK id is a runtime expression that SCCP
    # folds to a constant). run_lower(midend=True) runs SCCP, then lowers.
    def build():
        p = BlockPlace(block_expr, 0, 0)
        return BasicBlock(statements=[IRInstr(Op.DebugLog, [IRGet(p)])])

    return build


def test_dynamic_block_const_id_over_int32_preserved():
    # 2**40 folded into a block_ref must be preserved, not wrapped to int32 (-2147483648).
    text = cfg_to_text(lower.run_lower(_dynblock_get(IRPureInstr(Op.Add, [IRConst(2**40), IRConst(0)]))(), midend=True))
    assert "1099511627776" in text, text
    assert "-2147483648" not in text, text


def test_dynamic_block_const_id_non_integral_preserved():
    # 1000.5 folded into a block_ref must be preserved, not truncated to 1000.
    text = cfg_to_text(
        lower.run_lower(_dynblock_get(IRPureInstr(Op.Add, [IRConst(1000.5), IRConst(0)]))(), midend=True)
    )
    assert "1000.5" in text, text


def test_dynamic_block_const_id_int32_still_folds():
    # A normal in-range integer block id still folds to a static block (unchanged).
    text = cfg_to_text(lower.run_lower(_dynblock_get(IRPureInstr(Op.Add, [IRConst(1000), IRConst(0)]))(), midend=True))
    assert "1000[0]" in text, text


# ---------------------------------------------------------------------------
# Semantic parity: random-CFG recipes through run_lower vs MINIMAL reference.
# ---------------------------------------------------------------------------


def _f(x: float) -> bytes:
    # Compare +-0.0 as equal (NaN bytes pass through unchanged: NaN != 0.0). The
    # lowering path legitimately collapses the sign of zero relative to the
    # MINIMAL reference via two policy exceptions -- Multiply with a
    # constant-0 arg folds to +0.0, and the x+0 / x/1 identity drops an operand
    # (which can turn -0.0 into +0.0). This mirrors test_random_cfg._f and is the
    # ONLY relaxation of the bit-exact observable comparison.
    x = float(x)
    if x == 0.0:
        x = 0.0
    return struct.pack(">d", x)


def _observe(it: Interpreter) -> tuple:
    key = [b"log", *(_f(x) for x in it.log), b"mem"]
    for block in OBS_BLOCKS:
        key.extend(_f(it.get(block, i)) for i in range(OBS_CAPTURE_LEN))
    return tuple(key)


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(program=programs())
def test_random_programs_match_reference(program):
    def build():
        return build_cfg(program)

    ref = _interp(cfg_to_engine_node(run_passes(build(), MINIMAL_PASSES, OptimizerConfig())))
    low = _interp(cfg_to_engine_node(lower.run_lower(build())))
    assert _observe(ref) == _observe(low)


@settings(max_examples=120, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(program=programs(max_depth=4))
def test_random_deeper_programs_match_reference(program):
    def build():
        return build_cfg(program)

    ref = _interp(cfg_to_engine_node(run_passes(build(), MINIMAL_PASSES, OptimizerConfig())))
    low = _interp(cfg_to_engine_node(lower.run_lower(build())))
    assert _observe(ref) == _observe(low)


# ---------------------------------------------------------------------------
# Corpus: every pydori callback through run_lower (verify + alloc + emit),
# with a naive-vs-treeify node-count report.
# ---------------------------------------------------------------------------


def _count_nodes(node) -> int:
    if isinstance(node, FunctionNode):
        return 1 + sum(_count_nodes(a) for a in node.args)
    return 1


def _rc_block_ids(mode: Mode) -> set:
    return {int(m) for m in mode.blocks if m.name in ir.RUNTIME_CONSTANT_BLOCKS}


def _is_rc(node, rcids: set) -> bool:
    if not isinstance(node, FunctionNode):
        return True  # numeric leaf
    if node.func == Op.Get:
        blk, idx = node.args
        return isinstance(blk, int) and blk in rcids and _is_rc(idx, rcids)
    if node.func.pure and not node.func.side_effects:
        return all(_is_rc(a, rcids) for a in node.args)
    return False


def _eff_nodes(node, rcids: set) -> int:
    # Effective cost: a runtime-constant subtree the runtime folds to
    # a single push counts as 1 regardless of size.
    if not isinstance(node, FunctionNode):
        return 1
    if _is_rc(node, rcids):
        return 1
    return 1 + sum(_eff_nodes(a, rcids) for a in node.args)


@pytest.mark.parametrize("mode", list(MODE_SETUP))
def test_corpus_run_lower(mode: Mode):
    rcids = _rc_block_ids(mode)
    raw_mine = raw_naive = eff_mine = eff_naive = 0
    count = 0
    for _label, cbname, factory in iter_callbacks(mode):
        # This isolates the LOWERING strategy holding the mid-end constant: both
        # sides run cfg_cleanup -> ssa -> mid-end, then differ only in out-of-SSA.
        # STANDARD_PASSES can't be the naive baseline: it runs the full mid-end +
        # real treeify, so using it would be circular -- it would re-lower with the
        # very treeify under test. We build a genuine mid-end-then-naive-out_of_ssa
        # baseline via the debug phase registry instead.
        #   mine:  ... -> lower_from_ssa (real treeify) -> packing
        node = cfg_to_engine_node(lower.run_lower(factory(), mode, cbname, midend=True))
        assert isinstance(node, FunctionNode)  # Block(JumpLoop(...))
        #   naive: ... -> out_of_ssa (naive, materialize-everything) -> packing
        lowered = ir.debug_run(factory(), mode, cbname, phases=["cfg_cleanup", "ssa", "midend", "unssa", "packing"])
        naive = cfg_to_engine_node(lowered)
        raw_mine += _count_nodes(node)
        raw_naive += _count_nodes(naive)
        eff_mine += _eff_nodes(node, rcids)
        eff_naive += _eff_nodes(naive, rcids)
        count += 1
    assert count > 0
    print(
        f"\n[{mode.name}] raw naive={raw_naive} mine={raw_mine} "
        f"({100.0 * (raw_mine - raw_naive) / raw_naive:+.1f}%) | "
        f"eff naive={eff_naive} mine={eff_mine} ({100.0 * (eff_mine - eff_naive) / eff_naive:+.1f}%)"
    )
    # The hard gate is EFFECTIVE node count (raw may rise where runtime-constant
    # trees are deliberately duplicated -- the runtime folds each copy to 1).
    assert eff_mine <= eff_naive, (mode, eff_naive, eff_mine)
