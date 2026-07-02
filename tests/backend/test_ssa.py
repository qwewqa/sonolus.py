"""SSA construction, dominators, and naive out-of-SSA (milestone M2 keystone).

Covers OPTIMIZER_REWRITE.md 7.2.1 (Braun SSA construction), the CHK dominators
in ``analysis.pyx``, and the naive 7.4.2 out-of-SSA (split-all-critical-edges +
parallel-copy sequentialization). Three layers:

* structural unit tests -- inspect ``cfg_to_text`` of the ``["cfg_cleanup","ssa"]``
  and ``["...","unssa"]`` debug exports (phis, per-pred operands, UNDEF, arrays
  staying pinned, critical-edge splits, copy cycles);
* semantic parity -- interpret hand-built CFGs unoptimized (M1 pipeline) vs
  ssa->unssa->(M1 pipeline); identical results / logs / memory;
* corpus round-trip -- every pydori callback through cfg_cleanup->ssa->unssa then
  the standard M1 pipeline + node emission, asserting verify() green at each stage.
"""

from __future__ import annotations

import pytest

from sonolus.backend._opt import analysis, ir  # noqa: PLC2701
from sonolus.backend.interpret import Interpreter
from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet
from sonolus.backend.mode import Mode
from sonolus.backend.node import FunctionNode
from sonolus.backend.ops import Op
from sonolus.backend.optimize import MINIMAL_PASSES, STANDARD_PASSES, OptimizerConfig, cfg_to_engine_node, run_passes
from sonolus.backend.optimize.flow import BasicBlock, cfg_to_text, traverse_cfg_reverse_postorder
from sonolus.backend.place import BlockPlace, TempBlock
from tests.backend.test_corpus_roundtrip import _MODE_SETUP, _iter_callbacks

# ---------------------------------------------------------------------------
# CFG-building helpers.
# ---------------------------------------------------------------------------


def _sc(name: str) -> BlockPlace:
    return BlockPlace(TempBlock(name, 1), 0, 0)


def _ssa_text(cfg: BasicBlock, mode=None, cb=None) -> str:
    return cfg_to_text(ir.debug_run(cfg, mode, cb, phases=["cfg_cleanup", "ssa"]))


def _unssa_cfg(cfg: BasicBlock, mode=None, cb=None) -> BasicBlock:
    return ir.debug_run(cfg, mode, cb, phases=["cfg_cleanup", "ssa", "unssa"])


def _interp(cfg: BasicBlock, level=MINIMAL_PASSES, mode=None, cb=None, rom=None) -> Interpreter:
    """Run the M1 pipeline, emit, and interpret; returns the Interpreter."""
    opt = run_passes(cfg, level, OptimizerConfig(mode=mode, callback=cb))
    node = cfg_to_engine_node(opt)
    it = Interpreter()
    it.blocks[3000] = list(rom) if rom is not None else [float("nan"), float("inf"), -float("inf")]
    it.run(node)
    return it


def _observable_memory(it: Interpreter) -> dict:
    # Block 10000 is temp scratch memory (allocation detail, legitimately differs);
    # block 3000 is the constant ROM input (unchanged, and holds NaN which breaks
    # dict equality). Compare only the real memory the program writes.
    return {b: v for b, v in it.blocks.items() if b not in {10000, 3000}}


def _assert_semantics_preserved(build, mode=None, cb=None, rom=None):
    """Interpret original vs ssa->unssa; identical log + observable memory."""
    orig = _interp(build(), mode=mode, cb=cb, rom=rom)
    rt = _interp(_unssa_cfg(build(), mode, cb), mode=mode, cb=cb, rom=rom)
    assert orig.log == rt.log, f"log mismatch: {orig.log} vs {rt.log}"
    assert _observable_memory(orig) == _observable_memory(rt), "observable memory mismatch"
    return orig, rt


# ---------------------------------------------------------------------------
# Dominators (analysis.pyx).
# ---------------------------------------------------------------------------


def test_dominators_diamond():
    b0 = BasicBlock(test=IRGet(_sc("c")))
    b1, b2, b3 = BasicBlock(), BasicBlock(), BasicBlock()
    b0.statements = [IRSet(_sc("c"), IRConst(1))]
    b0.connect_to(b2, 0)
    b0.connect_to(b1, None)
    b1.connect_to(b3, None)
    b2.connect_to(b3, None)
    d = analysis.dominators_debug(b0)
    # Every block's idom is the entry (the join has two preds).
    assert d["idom"] == {0: 0, 1: 0, 2: 0, 3: 0}
    dom = d["dominates"]
    assert dom(0, 3)
    assert dom(0, 1)
    assert dom(0, 0)
    assert not dom(1, 3)
    assert not dom(3, 0)


def test_dominators_loop_chain():
    # 0 -> 1 -> 2 (2 loops to 1, exits to 3): idom(2)=1, idom(3)=1.
    b0, b1, b2, b3 = BasicBlock(), BasicBlock(), BasicBlock(), BasicBlock()
    b0.connect_to(b1, None)
    b1.statements = [IRSet(_sc("i"), IRConst(0))]
    b1.connect_to(b2, None)
    b2.test = IRGet(_sc("i"))
    b2.connect_to(b1, 0)
    b2.connect_to(b3, None)
    d = analysis.dominators_debug(b0)
    assert d["idom"][1] == 0
    assert d["idom"][2] == 1
    assert d["idom"][3] == 2
    assert d["dominates"](1, 3)
    assert not d["dominates"](2, 1)


# ---------------------------------------------------------------------------
# SSA construction -- structural.
# ---------------------------------------------------------------------------


def test_straight_line_no_phis():
    b0 = BasicBlock()
    b0.statements = [
        IRSet(_sc("a"), IRConst(1)),
        IRSet(_sc("b"), IRPureInstr(Op.Add, [IRGet(_sc("a")), IRConst(2)])),
        IRInstr(Op.DebugLog, [IRGet(_sc("b"))]),
    ]
    text = _ssa_text(b0)
    assert "phi" not in text
    assert "1 + 2" in text  # a=1 dissolves (inlined into the Add); b materialized


def _diamond(v_true=10, v_false=20, sel=1):
    b0 = BasicBlock(test=IRGet(_sc("c")))
    b1, b2, b3 = BasicBlock(), BasicBlock(), BasicBlock()
    b0.statements = [IRSet(_sc("c"), IRConst(sel))]
    b1.statements = [IRSet(_sc("x"), IRConst(v_true))]
    b2.statements = [IRSet(_sc("x"), IRConst(v_false))]
    # two statements so cfg_cleanup won't tail-duplicate the join (which would
    # eliminate the merge / phi).
    b3.statements = [IRInstr(Op.DebugLog, [IRGet(_sc("x"))]), IRInstr(Op.DebugPause, [IRGet(_sc("x"))])]
    b0.connect_to(b2, 0)
    b0.connect_to(b1, None)
    b1.connect_to(b3, None)
    b2.connect_to(b3, None)
    return b0


def test_diamond_one_phi_per_pred_operands():
    text = _ssa_text(_diamond())
    assert text.count("phi(") == 1
    # per-pred operands: true block feeds 10, false block feeds 20 (order-agnostic).
    assert ": 10" in text
    assert ": 20" in text


def test_diamond_semantics():
    _assert_semantics_preserved(lambda: _diamond(sel=1))  # true branch
    _assert_semantics_preserved(lambda: _diamond(sel=0))  # false branch


def _loop():
    b0, head, ex = BasicBlock(), BasicBlock(), BasicBlock()
    b0.statements = [IRSet(_sc("i"), IRConst(0))]
    b0.connect_to(head, None)
    head.statements = [IRSet(_sc("i"), IRPureInstr(Op.Add, [IRGet(_sc("i")), IRConst(1)]))]
    head.test = IRPureInstr(Op.Less, [IRGet(_sc("i")), IRConst(10)])
    head.connect_to(head, None)
    head.connect_to(ex, 0)
    ex.statements = [IRInstr(Op.DebugLog, [IRGet(_sc("i"))]), IRInstr(Op.DebugPause, [IRGet(_sc("i"))])]
    return b0


def test_loop_back_edge_phi():
    text = _ssa_text(_loop())
    # A loop-header phi merges the preheader value and the back-edge value.
    assert "phi(" in text


def test_loop_semantics():
    orig, _ = _assert_semantics_preserved(_loop)
    assert orig.log == [10]  # loop runs until i == 10 (DebugPause does not log)


def _nested_loops():
    # for i in 0..2: for j in 0..2: acc += 1 ; log acc
    entry, outer, inner, after_inner, ex = (BasicBlock() for _ in range(5))
    entry.statements = [IRSet(_sc("i"), IRConst(0)), IRSet(_sc("acc"), IRConst(0))]
    entry.connect_to(outer, None)
    outer.test = IRPureInstr(Op.Less, [IRGet(_sc("i")), IRConst(3)])
    outer.statements = [IRSet(_sc("j"), IRConst(0))]
    outer.connect_to(inner, None)  # cond None -> true edge is default here
    outer.connect_to(ex, 0)
    inner.test = IRPureInstr(Op.Less, [IRGet(_sc("j")), IRConst(3)])
    inner.statements = [
        IRSet(_sc("acc"), IRPureInstr(Op.Add, [IRGet(_sc("acc")), IRConst(1)])),
        IRSet(_sc("j"), IRPureInstr(Op.Add, [IRGet(_sc("j")), IRConst(1)])),
    ]
    inner.connect_to(inner, None)
    inner.connect_to(after_inner, 0)
    after_inner.statements = [IRSet(_sc("i"), IRPureInstr(Op.Add, [IRGet(_sc("i")), IRConst(1)]))]
    after_inner.connect_to(outer, None)
    ex.statements = [IRInstr(Op.DebugLog, [IRGet(_sc("acc"))])]
    return entry


def test_nested_loops_semantics():
    orig, _ = _assert_semantics_preserved(_nested_loops)
    assert orig.log == [9]  # 3 * 3 increments


# ---------------------------------------------------------------------------
# UNDEF handling.
# ---------------------------------------------------------------------------


def test_undef_read_of_never_written_scalar():
    b0 = BasicBlock()
    b0.statements = [IRInstr(Op.DebugLog, [IRGet(_sc("u"))])]
    text = _ssa_text(b0)
    assert "undef" in text


def test_phi_undef_v_collapses_to_v():
    # u written on one path only; the merge phi(UNDEF, 7) collapses to 7 once the
    # dead undef path is pruned. build_ssa deliberately KEEPS phi(UNDEF, v) (the
    # provably-dead collapse is unsound at construction time -- see
    # midend._try_remove_trivial); the collapse is SCCP's job, via edge
    # executability: here c == 1 never takes the cond-0 edge, so that (undef)
    # incoming edge is dead and the phi reduces to 7.
    b0 = BasicBlock(test=IRGet(_sc("c")))
    b1, b3 = BasicBlock(), BasicBlock()
    b0.statements = [IRSet(_sc("c"), IRConst(1))]
    b1.statements = [IRSet(_sc("u"), IRConst(7))]
    b3.statements = [IRInstr(Op.DebugLog, [IRGet(_sc("u"))]), IRInstr(Op.DebugPause, [IRGet(_sc("u"))])]
    b0.connect_to(b3, 0)  # skips b1 -> u undefined on this edge
    b0.connect_to(b1, None)
    b1.connect_to(b3, None)
    # After SSA the phi is present (kept); after SCCP the dead undef edge is
    # pruned and the phi collapses to 7.
    assert "phi(" in _ssa_text(b0)
    text = cfg_to_text(ir.debug_run(b0, phases=["cfg_cleanup", "ssa", "sccp", "dce"]))
    assert "phi" not in text
    assert "7" in text


# ---------------------------------------------------------------------------
# Trivial-phi chain collapse.
# ---------------------------------------------------------------------------


def _loop_invariant_read():
    # k is set before the loop and only READ (never modified) inside it. Braun
    # speculatively creates a header phi for k, then finds it trivial
    # (phi(42, self)) and removes it -- the transitive trivial-phi collapse. n is
    # the genuine loop-carried variable (its phi survives).
    b0, head, body, ex = BasicBlock(), BasicBlock(), BasicBlock(), BasicBlock()
    b0.statements = [IRSet(_sc("k"), IRConst(42)), IRSet(_sc("n"), IRConst(0))]
    b0.connect_to(head, None)
    head.statements = [IRInstr(Op.DebugLog, [IRGet(_sc("k"))])]
    head.test = IRPureInstr(Op.Less, [IRGet(_sc("n")), IRConst(3)])
    head.connect_to(body, None)
    head.connect_to(ex, 0)
    body.statements = [IRSet(_sc("n"), IRPureInstr(Op.Add, [IRGet(_sc("n")), IRConst(1)]))]
    body.connect_to(head, None)
    return b0


def test_trivial_phi_chain_collapses():
    text = _ssa_text(_loop_invariant_read())
    # k's speculative header phi is trivial and removed; only n's phi survives.
    assert text.count("phi(") == 1
    orig, _ = _assert_semantics_preserved(_loop_invariant_read)
    assert orig.log == [42, 42, 42, 42]  # logged each of n = 0,1,2,3


# ---------------------------------------------------------------------------
# Arrays / size-0 stay pinned memory ops (never promoted to values).
# ---------------------------------------------------------------------------


def test_array_not_promoted():
    arr = TempBlock("g", 4)
    b0 = BasicBlock()
    b0.statements = [
        IRSet(BlockPlace(arr, 0, 0), IRConst(5)),
        IRSet(_sc("o"), IRGet(BlockPlace(arr, 1, 0))),
        IRInstr(Op.DebugLog, [IRGet(_sc("o"))]),
    ]
    text = _ssa_text(b0)
    # array reads/writes remain explicit g[..] memory ops.
    assert "g[0]" in text
    assert "g[1]" in text


def test_size0_not_promoted():
    z = TempBlock("z", 0)
    b0 = BasicBlock()
    b0.statements = [
        IRSet(BlockPlace(z, 0, 0), IRConst(3)),
        IRInstr(Op.DebugLog, [IRGet(BlockPlace(z, 0, 0))]),
    ]
    text = _ssa_text(b0)
    # size-0 placeholder access stays as a pinned z[..] memory op.
    assert "z" in text


def test_array_semantics():
    def build():
        arr = TempBlock("g", 4)
        b0, b1 = BasicBlock(), BasicBlock()
        b0.statements = [
            IRSet(BlockPlace(arr, 0, 0), IRConst(11)),
            IRSet(BlockPlace(arr, 1, 0), IRConst(22)),
            IRSet(BlockPlace(500, 0, 0), IRGet(BlockPlace(arr, 0, 0))),
            IRSet(BlockPlace(500, 1, 0), IRGet(BlockPlace(arr, 1, 0))),
        ]
        b0.connect_to(b1, None)
        return b0

    orig, _ = _assert_semantics_preserved(build)
    assert orig.blocks[500][:2] == [11, 22]


# ---------------------------------------------------------------------------
# Parallel edges with equal operands (per-pred normalization on export).
# ---------------------------------------------------------------------------


def test_parallel_edges_equal_operands():
    # b0 reaches the merge via two parallel value edges (cond 0 and 1); b1 reaches
    # it too. The merge phi has 3 per-edge operands but the two from b0 are EQUAL,
    # so the per-pred export normalizes to {b0: .., b1: ..} without error.
    entry = BasicBlock(test=IRGet(_sc("sel")))
    b0, b1, merge = BasicBlock(), BasicBlock(), BasicBlock()
    entry.statements = [IRSet(_sc("sel"), IRConst(0))]
    entry.connect_to(b0, 0)
    entry.connect_to(b1, None)
    b0.statements = [IRSet(_sc("x"), IRConst(10))]
    b0.test = IRGet(_sc("sel"))
    b0.connect_to(merge, 0)
    b0.connect_to(merge, 1)  # parallel edge to the same target
    b1.statements = [IRSet(_sc("x"), IRConst(20))]
    b1.connect_to(merge, None)
    merge.statements = [IRInstr(Op.DebugLog, [IRGet(_sc("x"))]), IRInstr(Op.DebugPause, [IRGet(_sc("x"))])]
    # Must not raise "unequal operands"; a phi merging b0 and b1 survives.
    text = _ssa_text(entry)
    assert "phi(" in text


def test_parallel_edges_nan_operands_export():
    # Regression: the two parallel edges from b0 carry a NaN const. The export
    # compares arena value ids (not exported IRConst objects, since NaN != NaN),
    # so the equal-operand check does not spuriously raise; the two b0 edges
    # collapse to one per-pred entry.
    nan = float("nan")
    entry = BasicBlock(test=IRGet(_sc("sel")))
    b0, b1, merge = BasicBlock(), BasicBlock(), BasicBlock()
    entry.statements = [IRSet(_sc("sel"), IRConst(0))]
    entry.connect_to(b0, 0)
    entry.connect_to(b1, None)
    b0.statements = [IRSet(_sc("x"), IRConst(nan))]
    b0.test = IRGet(_sc("sel"))
    b0.connect_to(merge, 0)
    b0.connect_to(merge, 1)  # parallel edge to the same target, both carrying NaN
    b1.statements = [IRSet(_sc("x"), IRConst(1.0))]
    b1.connect_to(merge, None)
    merge.statements = [IRInstr(Op.DebugLog, [IRGet(_sc("x"))]), IRInstr(Op.DebugPause, [IRGet(_sc("x"))])]
    text = _ssa_text(entry)  # must not raise on the NaN parallel operands
    assert "phi(" in text
    assert "nan" in text.lower()


# ---------------------------------------------------------------------------
# Out-of-SSA: critical-edge splitting + copy-cycle sequentialization.
# ---------------------------------------------------------------------------


def test_critical_edge_split_creates_block():
    # entry has 2 successors and the loop header has 2 preds -> the entry->header
    # edge is critical, so out-of-SSA inserts a split block for its phi copies.
    lowered = _unssa_cfg(_loop())
    orig_blocks = len(list(traverse_cfg_reverse_postorder(ir.debug_run(_loop(), phases=["cfg_cleanup", "ssa"]))))
    new_blocks = len(list(traverse_cfg_reverse_postorder(lowered)))
    assert new_blocks > orig_blocks  # split blocks were inserted


def _swap_loop():
    b0, head, ex = BasicBlock(), BasicBlock(), BasicBlock()
    b0.statements = [IRSet(_sc("a"), IRConst(1)), IRSet(_sc("b"), IRConst(2)), IRSet(_sc("n"), IRConst(0))]
    b0.connect_to(head, None)
    head.statements = [
        IRSet(_sc("t"), IRGet(_sc("a"))),
        IRSet(_sc("a"), IRGet(_sc("b"))),
        IRSet(_sc("b"), IRGet(_sc("t"))),
        IRSet(_sc("n"), IRPureInstr(Op.Add, [IRGet(_sc("n")), IRConst(1)])),
    ]
    head.test = IRPureInstr(Op.Less, [IRGet(_sc("n")), IRConst(3)])
    head.connect_to(head, None)
    head.connect_to(ex, 0)
    ex.statements = [IRInstr(Op.DebugLog, [IRGet(_sc("a"))]), IRInstr(Op.DebugLog, [IRGet(_sc("b"))])]
    return b0


def test_swap_phi_cycle_sequentialized():
    # a<->b swap each iteration produces mutually-referencing phis; out-of-SSA
    # must break the copy cycle with a fresh temp, preserving semantics.
    text = _ssa_text(_swap_loop())
    assert text.count("phi(") >= 2  # a, b (and n) phis
    orig, _ = _assert_semantics_preserved(_swap_loop)
    assert orig.log == [2, 1]  # 3 swaps: a,b end as 2,1


# ---------------------------------------------------------------------------
# SSA-form export inspection + determinism.
# ---------------------------------------------------------------------------


def test_ssa_export_is_inspectable():
    text = _ssa_text(_diamond())
    assert ":= phi(" in text  # BasicBlock.phis rendered by cfg_to_text
    assert "goto" in text


def test_determinism_byte_identical():
    for build in (_loop, _diamond, _swap_loop, _nested_loops):
        t1 = cfg_to_text(_unssa_cfg(build()))
        t2 = cfg_to_text(_unssa_cfg(build()))
        assert t1 == t2, "out-of-SSA export is not deterministic"
        s1 = _ssa_text(build())
        s2 = _ssa_text(build())
        assert s1 == s2, "SSA export is not deterministic"


# ---------------------------------------------------------------------------
# Full pydori corpus round-trip.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", list(_MODE_SETUP))
def test_corpus_ssa_unssa_roundtrip(mode: Mode):
    """Round-trip every pydori callback through cfg_cleanup->ssa->unssa.

    verify() runs after each phase; the standard M1 pipeline + node emission on
    the lowered CFG must then succeed.
    """
    count = 0
    for _label, cbname, factory in _iter_callbacks(mode):
        cfg = factory()
        # verify() runs inside debug_run after each phase (ssa: SSA-form checks;
        # unssa: non-SSA checks). A failure raises here.
        lowered = ir.debug_run(cfg, mode, cbname, phases=["cfg_cleanup", "ssa", "unssa"])
        opt = run_passes(lowered, STANDARD_PASSES, OptimizerConfig(mode=mode, callback=cbname))
        node = cfg_to_engine_node(opt)
        assert isinstance(node, FunctionNode)  # Block(JumpLoop(...))
        count += 1
    assert count > 0
