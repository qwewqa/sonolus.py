"""Mid-end tests: SCCP, simplify/GVN, DCE, the round driver, and end-to-end.

Covers SCCP (+ set lattice + policy exceptions), dominator-scoped GVN (+ algebraic
identities), DCE, and the change-driven round. Three layers, mirroring test_ssa.py:

* structural units -- inspect ``cfg_to_text`` of the ``["cfg_cleanup","ssa",...]``
  debug exports (const folding, edge pruning, GVN unification, dead-code removal);
* semantic parity -- interpret hand-built CFGs unoptimized vs through ``run_midend``;
* corpus + differential -- the full pydori corpus and the random-CFG property suite
  run through ``run_midend``, verify()-green and observably identical.

The Multiply x0 / x+0 rewrites legally collapse -0.0 -> +0.0 (a documented policy
tolerance); the differential comparison relaxes to treat +0.0 == -0.0 while
keeping full NaN bit-checks (kernel-level tests stay bit-exact).
"""

from __future__ import annotations

import math
import struct

import pytest
from hypothesis import HealthCheck, given, settings

from sonolus.backend._opt import ir, midend  # noqa: PLC2701
from sonolus.backend.blocks import PlayBlock
from sonolus.backend.interpret import Interpreter
from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet
from sonolus.backend.mode import Mode
from sonolus.backend.node import FunctionNode
from sonolus.backend.ops import Op
from sonolus.backend.optimize import MINIMAL_PASSES, STANDARD_PASSES, OptimizerConfig, cfg_to_engine_node, run_passes
from sonolus.backend.optimize.flow import BasicBlock, cfg_to_text, traverse_cfg_reverse_postorder
from sonolus.backend.place import BlockPlace, TempBlock
from tests.backend._cfg_gen import OBS_BLOCKS, OBS_CAPTURE_LEN, build_cfg, programs
from tests.backend.test_corpus_roundtrip import _MODE_SETUP, _iter_callbacks

# A resolvable read-only block (BlockData member, read-only when callback is None):
# GVN-eligible. Raw int blocks are conservatively writable: never GVN'd.
RO = PlayBlock.RuntimeEnvironment  # read-only
W = 20  # raw int -> writable


def _sc(name: str) -> BlockPlace:
    return BlockPlace(TempBlock(name, 1), 0, 0)


def _rd(name: str) -> IRGet:
    return IRGet(_sc(name))


def _ro(index: int) -> IRGet:
    return IRGet(BlockPlace(RO, index))


def _w(index: int) -> IRGet:
    return IRGet(BlockPlace(W, index))


def _log(v) -> IRInstr:
    return IRInstr(Op.DebugLog, [v if not isinstance(v, (int, float)) else IRConst(v)])


def _text(cfg: BasicBlock, phases, mode=None, cb=None) -> str:
    return cfg_to_text(ir.debug_run(cfg, mode, cb, phases=phases))


def _count_stmts(cfg: BasicBlock) -> int:
    return sum(len(b.statements) for b in traverse_cfg_reverse_postorder(cfg))


# --------------------------------------------------------------------------
# Interpretation helpers (semantic parity).
# --------------------------------------------------------------------------

_ROM = [float("nan"), float("inf"), float("-inf")]


def _run(node) -> Interpreter:
    it = Interpreter()
    it.blocks[3000] = list(_ROM)
    it.run(node)
    return it


def _interp_plain(build, mode=None, cb=None) -> Interpreter:
    cfg = run_passes(build(), MINIMAL_PASSES, OptimizerConfig(mode=mode, callback=cb))
    return _run(cfg_to_engine_node(cfg))


def _interp_midend(build, mode=None, cb=None, repeat=True) -> Interpreter:
    # Allocate run_midend's output with MINIMAL (-O0: cfg_cleanup + bump alloc, no
    # SSA / SCCP / GVN / DCE / LICM), NOT STANDARD. Re-running the full -O2 pipeline
    # here would re-optimize the output and could mask a defect in run_midend, so
    # the parity layer would no longer isolate the mid-end pass under test.
    mid = midend.run_midend(build(), mode, cb, allow_repeat=repeat)
    allocated = run_passes(mid, MINIMAL_PASSES, OptimizerConfig(mode=mode, callback=cb))
    return _run(cfg_to_engine_node(allocated))


def _obs_memory(a: Interpreter, b: Interpreter, window=32) -> tuple[dict, dict]:
    # Compare via get() over a fixed window so a dead read that merely extends a
    # block with -1.0 padding in one run is not counted as a difference. Block
    # 10000 (temp scratch) and 3000 (NaN ROM) are excluded.
    blocks = sorted((set(a.blocks) | set(b.blocks)) - {10000, 3000})
    va = {blk: [a.get(blk, i) for i in range(window)] for blk in blocks}
    vb = {blk: [b.get(blk, i) for i in range(window)] for blk in blocks}
    return va, vb


def _assert_semantics(build, mode=None, cb=None):
    """Original (MINIMAL) vs run_midend: identical log + observable memory."""
    ref = _interp_plain(build, mode, cb)
    mid = _interp_midend(build, mode, cb)
    assert ref.log == mid.log, f"log mismatch: {ref.log} vs {mid.log}"
    va, vb = _obs_memory(ref, mid)
    assert va == vb, "observable memory mismatch"
    return mid


# ==========================================================================
# SCCP
# ==========================================================================


def _const_diamond(sel):
    a = BasicBlock(test=IRConst(sel))
    tb = BasicBlock(statements=[IRSet(_sc("r"), IRConst(111))])
    fb = BasicBlock(statements=[IRSet(_sc("r"), IRConst(222))])
    join = BasicBlock(statements=[_log(_rd("r")), IRInstr(Op.DebugPause, [_rd("r")])])
    a.connect_to(fb, 0)
    a.connect_to(tb, None)
    tb.connect_to(join, None)
    fb.connect_to(join, None)
    return a


def test_sccp_constant_diamond_folds_to_one_arm():
    text = _text(_const_diamond(1), ["cfg_cleanup", "ssa", "sccp", "dce"])
    assert "111" in text
    assert "222" not in text  # the false arm is unreachable and pruned
    text0 = _text(_const_diamond(0), ["cfg_cleanup", "ssa", "sccp", "dce"])
    assert "222" in text0
    assert "111" not in text0
    it = _assert_semantics(lambda: _const_diamond(1))
    assert it.log == [111.0]


def test_sccp_nan_loop_phi_terminates():
    # A constant NaN flowing through a loop-carried phi: bitwise lattice equality
    # keeps it a single lattice point instead of re-enqueuing forever. This must
    # simply complete (and verify() green after SCCP).
    def build():
        b0 = BasicBlock(statements=[IRSet(_sc("x"), IRConst(float("nan"))), IRSet(_sc("i"), IRConst(0))])
        head = BasicBlock(test=IRPureInstr(Op.Less, [_rd("i"), IRConst(3)]))
        body = BasicBlock(statements=[IRSet(_sc("i"), IRPureInstr(Op.Add, [_rd("i"), IRConst(1)]))])
        ex = BasicBlock(statements=[_log(_rd("x"))])
        b0.connect_to(head, None)
        head.connect_to(body, None)
        head.connect_to(ex, 0)
        body.connect_to(head, None)
        return b0

    # Does not hang; verify() runs inside debug_run after every phase.
    text = _text(build(), ["cfg_cleanup", "ssa", "sccp", "gvn", "dce"])
    assert "DebugLog" in text
    it = _interp_midend(build)
    assert len(it.log) == 1
    assert math.isnan(it.log[0])


def test_sccp_neg_zero_distinct_but_equal_folds():
    # Equal(-0.0, 0.0) == 1 (== semantics); Arctan2(0, -0) == pi but Arctan2(0, 0)
    # == 0 -- proving -0.0 stays distinct from +0.0 in the lattice (not collapsed).
    def build():
        b0 = BasicBlock(
            statements=[
                _log(IRPureInstr(Op.Equal, [IRPureInstr(Op.Negate, [IRConst(0.0)]), IRConst(0.0)])),
                _log(IRPureInstr(Op.Arctan2, [IRConst(0.0), IRPureInstr(Op.Negate, [IRConst(0.0)])])),
                _log(IRPureInstr(Op.Arctan2, [IRConst(0.0), IRConst(0.0)])),
            ]
        )
        b0.connect_to(BasicBlock(), None)
        return b0

    text = _text(build(), ["cfg_cleanup", "ssa", "sccp"])
    assert "Arctan2" not in text  # both folded (proving each reached the kernel)
    it = _interp_midend(build)
    assert it.log[0] == 1.0
    assert abs(it.log[1] - math.pi) < 1e-9  # atan2(0, -0) folded distinctly
    assert it.log[2] == 0.0


def _set_switch(n_arms, downstream_cases, sel_default_const=99):
    # entry switches a non-const selector into ``n_arms`` arms each setting x to a
    # distinct const (0..n_arms-1); the merge phi feeds a downstream switch whose
    # cases are ``downstream_cases`` (each logs 100+cond), plus a default (logs 199).
    # The merge carries two statements so cfg_cleanup does not tail-duplicate the
    # switch into the arms (which would const-fold it per-arm and bypass the SET
    # lattice we are exercising here).
    entry = BasicBlock(test=_w(0))
    merge = BasicBlock(statements=[_log(-2), IRInstr(Op.DebugPause, [IRConst(0)])], test=_rd("x"))
    for i in range(n_arms):
        arm = BasicBlock(statements=[IRSet(_sc("x"), IRConst(i))])
        cond = None if i == n_arms - 1 else i
        entry.connect_to(arm, cond)
        arm.connect_to(merge, None)
    join = BasicBlock(statements=[_log(-1)])
    for c in downstream_cases:
        blk = BasicBlock(statements=[_log(100 + c), IRInstr(Op.DebugPause, [IRConst(c)])])
        merge.connect_to(blk, c)
        blk.connect_to(join, None)
    dflt = BasicBlock(statements=[_log(199), IRInstr(Op.DebugPause, [IRConst(0)])])
    merge.connect_to(dflt, None)
    dflt.connect_to(join, None)
    return entry


def test_sccp_set_lattice_switch_pruning():
    # phi of {0, 1, 2}; downstream switch has cases 0,1,2,5 + default. The set is
    # {0,1,2}, so the case-5 edge and the (unreachable) default are pruned.
    text = _text(_set_switch(3, [0, 1, 2, 5]), ["cfg_cleanup", "ssa", "sccp", "dce"])
    assert "105" not in text  # case-5 block pruned (5 not in the set)
    assert "199" not in text  # default block pruned (all set values have cases)
    for c in (0, 1, 2):
        assert str(100 + c) in text


def test_sccp_set_lattice_over_100_goes_bottom():
    # phi of {0..100} (101 distinct) exceeds the cap -> BOTTOM -> NO pruning, so a
    # downstream case for 500 (not a member of any real set) survives.
    text = _text(_set_switch(101, [0, 500]), ["cfg_cleanup", "ssa", "sccp", "dce"])
    assert "600" in text  # log(100 + 500): the case-500 block survives (BOTTOM, no prune)
    assert "199" in text  # default also survives under BOTTOM


def _set_switch_defaultless(cases):
    # Like _set_switch but the merge switch is DEFAULT-LESS (only ``cases`` get an
    # edge, no default). The scrutinee's set lattice is {0,1,2}; an element with no
    # matching case must fall through to exit -- SCCP must NOT promote a surviving
    # case edge to the default here, or it would misroute that missed element.
    entry = BasicBlock(test=_w(0))
    merge = BasicBlock(statements=[_log(-2), IRInstr(Op.DebugPause, [IRConst(0)])], test=_rd("x"))
    for i in range(3):
        arm = BasicBlock(statements=[IRSet(_sc("x"), IRConst(i))])
        entry.connect_to(arm, None if i == 2 else i)
        arm.connect_to(merge, None)
    join = BasicBlock(statements=[_log(-1)])
    for c in cases:
        blk = BasicBlock(statements=[_log(100 + c)])
        merge.connect_to(blk, c)
        blk.connect_to(join, None)
    return entry


def _run_seeded(node, wsel):
    it = Interpreter()
    it.blocks[3000] = list(_ROM)
    it.blocks[W] = [wsel]
    it.run(node)
    return it


def test_sccp_defaultless_nonexhaustive_switch_missed_element_exits():
    # Set lattice {0,1,2}; the DEFAULT-LESS downstream switch has cases 0,1 only.
    # For selector 7 the phi is 2, which no case matches: with no default it must
    # exit ([-2] only). SCCP's max-cond->default promotion is guarded to fire only
    # when every set element matches a case; neutralizing that guard would promote
    # case 1 to the default and route the missed element 2 to case 1 ([-2, 101, -1]).
    build = lambda: _set_switch_defaultless([0, 1])  # noqa: E731
    for wsel, expect in [(0, [-2, 100, -1]), (1, [-2, 101, -1]), (7, [-2])]:
        ref = _run_seeded(cfg_to_engine_node(run_passes(build(), MINIMAL_PASSES, OptimizerConfig())), wsel)
        mid = _run_seeded(
            cfg_to_engine_node(run_passes(midend.run_midend(build(), None, None, allow_repeat=True),
                                          MINIMAL_PASSES, OptimizerConfig())),
            wsel,
        )
        assert ref.log == mid.log == expect, f"wsel={wsel}: ref={ref.log} mid={mid.log} expect={expect}"


def test_out_of_ssa_pure_self_loop_splits_self_edge():
    # A pure self-loop (both test outcomes re-enter b) whose test reads the loop
    # phi: out_of_ssa must SPLIT the self-edge so the phi copies land on a fresh
    # block past b's test, never as a direct b->b edge. Without the split the copies
    # would be emitted at b's end and b would keep a direct self-edge; verify() runs
    # inside run_unssa, and the CFG must carry no direct self-edge.
    def build():
        entry = BasicBlock(statements=[IRSet(_sc("x"), IRConst(0))])
        b = BasicBlock(
            statements=[
                IRInstr(Op.DebugPause, [_rd("x")]),
                IRSet(_sc("x"), IRPureInstr(Op.Add, [_rd("x"), IRConst(1)])),
            ],
            test=_rd("x"),
        )
        entry.connect_to(b, None)
        b.connect_to(b, 0)     # x == 0 -> loop
        b.connect_to(b, None)  # else -> loop (pure self-loop)
        return entry

    cfg = midend.run_unssa(build())  # verify() runs inside
    blocks = list(traverse_cfg_reverse_postorder(cfg))
    self_edges = [blk for blk in blocks for e in blk.outgoing if e.dst is blk]
    assert not self_edges, "the self-edge must be split (no block targets itself directly)"
    # the split added a block beyond entry + the loop body.
    assert len(blocks) >= 3


def test_sccp_dead_loop_entry_self_phi_collapses_cleanly():
    # s == 0 is provably false (s := 1 in SSA), so SCCP prunes the loop-entry edge;
    # the loop becomes reachable only via its own back-edge and its loop-carried phi
    # realigns to a single self-referential operand. _collapse_trivial_phis must
    # leave that degenerate phi for DCE -- substituting it would build a subst[p]=p
    # cycle (hanging _resolve) or a def-before-use. This must simply complete,
    # verify() green after every phase, and interpret to the loop-skipped result.
    def build():
        entry = BasicBlock(
            statements=[IRSet(_sc("s"), IRConst(1)), IRSet(_sc("acc"), IRConst(0))],
            test=IRPureInstr(Op.Equal, [_rd("s"), IRConst(0)]),
        )
        loop = BasicBlock(
            statements=[
                IRSet(_sc("acc"), IRPureInstr(Op.Add, [_rd("acc"), IRConst(1)])),
                IRInstr(Op.DebugPause, [_rd("acc")]),
            ],
            test=IRPureInstr(Op.Less, [_rd("acc"), IRConst(3)]),
        )
        after = BasicBlock(statements=[_log(42)])
        entry.connect_to(after, 0)     # s == 0 -> after (taken)
        entry.connect_to(loop, None)   # default -> loop (SCCP proves dead)
        loop.connect_to(loop, 0)       # self back-edge
        loop.connect_to(after, None)
        return entry

    text = _text(build(), ["cfg_cleanup", "ssa", "sccp", "dce"])  # verify() runs inside
    assert "DebugPause" not in text  # the dead loop is gone
    assert _assert_semantics(build).log == [42.0]


def test_sccp_multiply_by_zero_exception():
    # Multiply(unknown, 0) -> 0 even though the other arg is non-constant.
    def build():
        b0 = BasicBlock(statements=[_log(IRPureInstr(Op.Multiply, [_w(0), IRConst(0)]))])
        b0.connect_to(BasicBlock(), None)
        return b0

    text = _text(build(), ["cfg_cleanup", "ssa", "sccp", "dce"])
    assert "*" not in text  # the Multiply folded away
    it = _interp_midend(build)
    assert it.log == [0.0]


def test_sccp_and_or_boolean_exception_only_when_boolean():
    # And(bool, 0) -> 0 and Or(bool, 1) -> 1 (bool = comparison result); And/Or with
    # a non-boolean unknown arg does NOT short-circuit; And(2, 3) -> 3 by value.
    def build():
        b0 = BasicBlock(
            statements=[
                IRSet(_sc("bt"), IRPureInstr(Op.Less, [_w(1), IRConst(5)])),
                _log(IRPureInstr(Op.And, [_rd("bt"), IRConst(0)])),  # boolean -> 0
                _log(IRPureInstr(Op.Or, [_rd("bt"), IRConst(1)])),  # boolean -> 1
                _log(IRPureInstr(Op.And, [_w(2), IRConst(0)])),  # non-boolean -> not folded
                _log(IRPureInstr(Op.And, [IRConst(2), IRConst(3)])),  # both const -> 3
            ]
        )
        b0.connect_to(BasicBlock(), None)
        return b0

    text = _text(build(), ["cfg_cleanup", "ssa", "sccp", "dce"])
    # exactly one surviving And (the non-boolean one) and no surviving Or.
    assert text.count("&&") == 1
    assert "||" not in text
    it = _interp_midend(build)
    # And(bool,0)=0, Or(bool,1)=1, And(read,0)=0 (runtime short-circuit), And(2,3)=3.
    assert it.log == [0.0, 1.0, 0.0, 3.0]


def test_sccp_division_by_zero_does_not_fold():
    b0 = BasicBlock(statements=[IRSet(BlockPlace(W, 0), IRPureInstr(Op.Divide, [IRConst(6), IRConst(0)]))])
    b0.connect_to(BasicBlock(), None)
    text = _text(b0, ["cfg_cleanup", "ssa", "sccp", "dce"])
    assert "6 / 0" in text  # left as a runtime division, not folded to a constant


def test_sccp_degenerate_constant_ops_never_raise():
    # Compile-time evaluation must NEVER raise on JS-like degenerate arithmetic: the
    # fold either produces the correct IEEE value or declines. Compile-only text
    # checks (the interpreter oracle raises on mod/div by zero -- see
    # test_sccp_division_by_zero_does_not_fold, which is why this stays textual).
    def _blk(rhs):
        b0 = BasicBlock(statements=[IRSet(BlockPlace(W, 0), rhs)])
        b0.connect_to(BasicBlock(), None)
        return b0

    phases = ["cfg_cleanup", "ssa", "sccp", "gvn", "dce"]
    big = IRPureInstr(Op.Multiply, [IRConst(1e308), IRConst(10.0)])  # overflow -> +inf
    # mod-by-zero declines to fold (left as a runtime op), like divide-by-zero.
    assert "5 % 0" in _text(_blk(IRPureInstr(Op.Mod, [IRConst(5), IRConst(0)])), phases)
    # overflow folds to the IEEE inf; inf - inf folds to nan (interned as a float const,
    # exercising the isinf/isnan-guarded is_int path in the SCCP rebuild).
    assert "<- inf" in _text(_blk(big), phases)
    assert "<- nan" in _text(_blk(IRPureInstr(Op.Subtract, [big, big])), phases)


def test_sccp_switch_on_degenerate_constant_takes_default():
    # A multi-way switch whose test folds to a non-matching constant (inf) selects
    # the default/exit edge -- inf equals no integer case (C ``==`` on NaN/inf never
    # matches). Must fold without raising, mirroring the runtime's switch semantics.
    def build(val):
        head = BasicBlock(statements=[IRSet(_sc("t"), val)], test=_rd("t"))
        a = BasicBlock(statements=[_log(1)])
        b = BasicBlock(statements=[_log(2)])
        ex = BasicBlock(statements=[_log(99)])
        head.connect_to(a, 0)
        head.connect_to(b, 1)
        head.connect_to(ex, None)  # default: missed -> exit
        a.connect_to(ex, None)
        b.connect_to(ex, None)
        return head

    inf = IRPureInstr(Op.Multiply, [IRConst(1e308), IRConst(10.0)])
    text = _text(build(inf), ["cfg_cleanup", "ssa", "sccp", "gvn", "dce"])
    assert "DebugLog(99)" in text  # default arm reached
    assert "DebugLog(1)" not in text and "DebugLog(2)" not in text  # cases pruned


def test_sccp_undef_statement_removal():
    # An arm made unreachable by a constant test takes its store with it; the phi
    # collapses (phi(UNDEF, v) = v) and only the live value survives.
    def build():
        a = BasicBlock(test=IRConst(0))  # always false -> the true arm dies
        tb = BasicBlock(statements=[IRSet(_sc("u"), IRConst(7)), IRInstr(Op.DebugPause, [IRConst(0)])])
        fb = BasicBlock(statements=[IRSet(_sc("u"), IRConst(9)), IRInstr(Op.DebugPause, [IRConst(1)])])
        join = BasicBlock(statements=[_log(_rd("u")), IRInstr(Op.DebugPause, [_rd("u")])])
        a.connect_to(fb, 0)
        a.connect_to(tb, None)
        tb.connect_to(join, None)
        fb.connect_to(join, None)
        return a

    text = _text(build(), ["cfg_cleanup", "ssa", "sccp", "dce"])
    assert "7" not in text  # dead true-arm store removed
    assert "phi" not in text  # phi collapsed
    it = _assert_semantics(build)
    assert it.log == [9.0]


# ==========================================================================
# GVN
# ==========================================================================


def _gvn_text(cfg):
    return _text(cfg, ["cfg_cleanup", "ssa", "gvn", "dce"])


def test_gvn_unifies_across_dominating_not_siblings():
    # Dominating: entry computes E and both children reuse it -> 1 Max.
    def dom():
        entry = BasicBlock(statements=[_log(IRPureInstr(Op.Max, [_ro(0), _ro(1)]))], test=_w(0))
        ba = BasicBlock(statements=[_log(IRPureInstr(Op.Max, [_ro(0), _ro(1)])), IRInstr(Op.DebugPause, [IRConst(0)])])
        bb = BasicBlock(statements=[_log(IRPureInstr(Op.Max, [_ro(0), _ro(1)])), IRInstr(Op.DebugPause, [IRConst(1)])])
        entry.connect_to(ba, 0)
        entry.connect_to(bb, None)
        exit_ = BasicBlock()
        ba.connect_to(exit_, None)
        bb.connect_to(exit_, None)
        return entry

    assert _gvn_text(dom()).count("Max(") == 1

    # Siblings: only the two arms compute E, neither dominates the other -> 2 Max.
    def sib():
        entry = BasicBlock(test=_w(0))
        ba = BasicBlock(statements=[_log(IRPureInstr(Op.Max, [_ro(0), _ro(1)])), IRInstr(Op.DebugPause, [IRConst(0)])])
        bb = BasicBlock(statements=[_log(IRPureInstr(Op.Max, [_ro(0), _ro(1)])), IRInstr(Op.DebugPause, [IRConst(1)])])
        entry.connect_to(ba, 0)
        entry.connect_to(bb, None)
        exit_ = BasicBlock()
        ba.connect_to(exit_, None)
        bb.connect_to(exit_, None)
        return entry

    assert _gvn_text(sib()).count("Max(") == 2


def test_gvn_commutative_canon_max_not_add():
    # Max(a,b) and Max(b,a) unify (canonicalized); Add(a,b) and Add(b,a) do not.
    def build(op):
        entry = BasicBlock(statements=[_log(IRPureInstr(op, [_ro(0), _ro(1)]))])
        child = BasicBlock(statements=[_log(IRPureInstr(op, [_ro(1), _ro(0)])), IRInstr(Op.DebugPause, [IRConst(0)])])
        entry.connect_to(child, None)
        child.connect_to(BasicBlock(), None)
        return entry

    # cfg_cleanup merges the single-pred/single-succ chain, but GVN still unifies
    # only the commutative op.
    assert _gvn_text(build(Op.Max)).count("Max(") == 1
    assert _gvn_text(build(Op.Add)).count(" + ") == 2


def test_gvn_algebraic_identities():
    def build():
        b0 = BasicBlock(
            statements=[
                IRSet(_sc("m"), IRPureInstr(Op.Max, [_ro(0), _ro(1)])),
                _log(IRPureInstr(Op.Add, [_rd("m"), IRConst(0)])),  # x + 0 -> x
                _log(IRPureInstr(Op.Subtract, [_rd("m"), IRConst(0)])),  # x - 0 -> x
                _log(IRPureInstr(Op.Multiply, [_rd("m"), IRConst(1)])),  # x * 1 -> x
                _log(IRPureInstr(Op.Divide, [_rd("m"), IRConst(1)])),  # x / 1 -> x
                _log(IRPureInstr(Op.Subtract, [IRConst(0), _rd("m")])),  # 0 - x -> -x
                _log(IRPureInstr(Op.Min, [_rd("m"), _rd("m")])),  # min(x, x) -> x
            ]
        )
        b0.connect_to(BasicBlock(), None)
        return b0

    text = _gvn_text(build())
    assert " + " not in text  # x + 0 removed
    assert " / " not in text  # x / 1 removed
    assert " * " not in text  # x * 1 removed
    assert "Min(" not in text  # min(x, x) removed
    assert "-v" in text  # 0 - x became Negate
    # Subtract(x, 0) removed too: the only "-" is the unary negate line.
    assert _assert_semantics(build)  # all identities preserve value


def test_gvn_readonly_get_unified_writable_never():
    # Two reads of the same read-only block[index] across a store to a DIFFERENT
    # (writable) block unify (no aliasing). Reads of a writable block never unify.
    def build_ro():
        b0 = BasicBlock(
            statements=[
                _log(_ro(0)),
                IRSet(BlockPlace(W, 3), IRConst(1)),  # store to a different (writable) block
                _log(_ro(0)),  # same read-only read -> unified despite the store
                IRInstr(Op.DebugPause, [IRConst(0)]),
            ]
        )
        b0.connect_to(BasicBlock(), None)
        return b0

    assert _gvn_text(build_ro()).count("RuntimeEnvironment[0]") == 1

    def build_w():
        b0 = BasicBlock(
            statements=[_log(_w(0)), _log(_w(0)), IRInstr(Op.DebugPause, [IRConst(0)])]
        )
        b0.connect_to(BasicBlock(), None)
        return b0

    assert _gvn_text(build_w()).count("20[0]") == 2  # writable reads never unified


def test_gvn_random_never_unified():
    def build():
        b0 = BasicBlock(
            statements=[
                IRSet(_sc("a"), IRInstr(Op.Random, [IRConst(0), IRConst(1)])),
                IRSet(_sc("b"), IRInstr(Op.Random, [IRConst(0), IRConst(1)])),
                _log(_rd("a")),
                _log(_rd("b")),
            ]
        )
        b0.connect_to(BasicBlock(), None)
        return b0

    assert _gvn_text(build()).count("Random(") == 2  # each draw kept distinct


# ==========================================================================
# DCE
# ==========================================================================


def test_dce_unused_pure_chain_deleted():
    def build():
        b0 = BasicBlock(
            statements=[
                IRSet(_sc("a"), IRPureInstr(Op.Add, [_w(0), IRConst(1)])),
                IRSet(_sc("b"), IRPureInstr(Op.Multiply, [_rd("a"), IRConst(3)])),  # unused chain
                _log(42),
            ]
        )
        b0.connect_to(BasicBlock(), None)
        return b0

    text = _gvn_text(build())
    assert " + " not in text  # whole dead chain removed
    assert " * " not in text
    assert "DebugLog(42)" in text


def test_dce_unused_random_deleted():
    def build():
        b0 = BasicBlock(statements=[IRSet(_sc("a"), IRInstr(Op.Random, [IRConst(0), IRConst(1)])), _log(7)])
        b0.connect_to(BasicBlock(), None)
        return b0

    assert "Random" not in _gvn_text(build())  # legal to delete an unused draw


def test_dce_used_by_dead_phi_only_deleted():
    # Values feed a phi whose result is never used -> phi dead -> arm values dead.
    def build():
        a = BasicBlock(test=_w(0))
        tb = BasicBlock(statements=[IRSet(_sc("x"), IRPureInstr(Op.Cosh, [_w(1)])), IRInstr(Op.DebugPause, [IRConst(0)])])
        fb = BasicBlock(statements=[IRSet(_sc("x"), IRPureInstr(Op.Sinh, [_w(1)])), IRInstr(Op.DebugPause, [IRConst(1)])])
        join = BasicBlock(statements=[_log(5)])  # x never read
        a.connect_to(fb, 0)
        a.connect_to(tb, None)
        tb.connect_to(join, None)
        fb.connect_to(join, None)
        return a

    text = _gvn_text(build())
    assert "Cosh" not in text
    assert "Sinh" not in text
    assert "phi" not in text


def test_dce_store_to_real_block_kept_unused_read_deleted():
    def build():
        b0 = BasicBlock(
            statements=[
                IRSet(BlockPlace(W, 0), IRConst(5)),  # store to a real block, never read -> kept
                IRSet(_sc("t"), _w(1)),  # read into a temp, never used -> deleted
                _log(9),
            ]
        )
        b0.connect_to(BasicBlock(), None)
        return b0

    text = _gvn_text(build())
    assert "20[0] <- 5" in text  # the pinned store survives
    assert "20[1]" not in text  # the unused read is gone
    it = _assert_semantics(build)
    assert it.get(W, 0) == 5.0


# ==========================================================================
# Round driver: allow_repeat exposes second-round opportunities.
# ==========================================================================


def test_round_repeat_folds_cascade():
    # SCCP folds the test; cleanup exposes a merge; a second SCCP/GVN/DCE round
    # then folds the now-constant downstream expression.
    def build():
        a = BasicBlock(statements=[IRSet(_sc("c"), IRConst(1))], test=_rd("c"))
        tb = BasicBlock(statements=[IRSet(_sc("x"), IRConst(10))])
        fb = BasicBlock(statements=[IRSet(_sc("x"), IRConst(20))])
        join = BasicBlock(statements=[_log(IRPureInstr(Op.Add, [_rd("x"), IRConst(5)]))])
        a.connect_to(fb, 0)
        a.connect_to(tb, None)
        tb.connect_to(join, None)
        fb.connect_to(join, None)
        return a

    it = _interp_midend(build, repeat=True)
    assert it.log == [15.0]  # x == 10 (true arm), 10 + 5 == 15, fully folded


# ==========================================================================
# End-to-end: full pydori corpus through run_midend.
# ==========================================================================


@pytest.mark.parametrize("mode", list(_MODE_SETUP))
def test_corpus_run_midend(mode: Mode):
    """Verify()-green through each mid-end phase, emittable, and shrinking.

    Every pydori callback runs through the full mid-end; the aggregate statement
    count must be no worse than plain ssa->unssa (and strictly better on most).
    """
    plain_total = 0
    mid_total = 0
    improved = 0
    regressed = 0
    count = 0
    for _label, cbname, factory in _iter_callbacks(mode):
        # verify() runs inside debug_run after every phase.
        plain = ir.debug_run(factory(), mode, cbname, phases=["cfg_cleanup", "ssa", "unssa"])
        mid = ir.debug_run(factory(), mode, cbname, phases=["cfg_cleanup", "ssa", "midend", "unssa"])
        node = cfg_to_engine_node(run_passes(mid, STANDARD_PASSES, OptimizerConfig(mode=mode, callback=cbname)))
        assert isinstance(node, FunctionNode)
        ps = _count_stmts(plain)
        ms = _count_stmts(mid)
        plain_total += ps
        mid_total += ms
        if ms < ps:
            improved += 1
        elif ms > ps:
            regressed += 1
        count += 1
    assert count > 0
    # No-regression, per callback and in aggregate: the mid-end never makes any
    # callback's statement count worse than plain ssa->unssa.
    assert regressed == 0, f"{mode.name}: {regressed}/{count} callbacks regressed"
    assert mid_total <= plain_total, f"{mode.name}: mid-end regressed ({mid_total} > {plain_total})"
    # The mid-end must still be doing real work on the corpus (not a no-op).
    assert improved > 0, f"{mode.name}: no callback improved ({improved}/{count})"


# ==========================================================================
# Differential: random-CFG property suite through run_midend vs MINIMAL.
# ==========================================================================


def _f(x: float) -> bytes:
    # +-0.0 folded to a single key (a documented policy tolerance); NaN keeps its
    # bit pattern.
    if x == 0.0:
        return struct.pack(">d", 0.0)
    return struct.pack(">d", float(x))


def _observe(it: Interpreter, blocks=OBS_BLOCKS, length=OBS_CAPTURE_LEN) -> tuple:
    key = [b"log", *(_f(x) for x in it.log), b"mem"]
    for block in blocks:
        key.extend(_f(it.get(block, i)) for i in range(length))
    return tuple(key)


@settings(max_examples=250, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(program=programs())
def test_random_programs_midend_matches_reference(program):
    config = OptimizerConfig()

    def build():
        return build_cfg(program)

    ref_node = cfg_to_engine_node(run_passes(build(), MINIMAL_PASSES, config))
    ref = Interpreter()
    ref.blocks[3000] = list(_ROM)
    ref.run(ref_node)

    mid = midend.run_midend(build(), None, None, allow_repeat=True)
    mid_node = cfg_to_engine_node(run_passes(mid, MINIMAL_PASSES, config))
    got = Interpreter()
    got.blocks[3000] = list(_ROM)
    got.run(mid_node)

    assert _observe(got) == _observe(ref), "run_midend diverged from the MINIMAL reference"
