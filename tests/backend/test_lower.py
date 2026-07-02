"""Out-of-SSA lowering + treeify tests (milestone M2, OPTIMIZER_REWRITE.md 7.4).

Three layers, mirroring test_ssa.py:

* scheduling / treeify units -- inspect ``cfg_to_text`` of ``lower_debug`` to
  assert the fold / duplicate / materialize decision (7.4.1), phi elimination
  (7.4.2 -- critical-edge splits, cycle temps, UNDEF), n-ary flattening +
  identity dropping (7.4.3), and ``normalize_switch`` (7.4.5);
* semantic parity -- the random-CFG property recipes interpreted through
  ``run_lower`` vs the MINIMAL reference (observables equal);
* corpus -- every pydori callback through ``run_lower``: verify() green,
  allocation fits, emission succeeds, with a naive-vs-treeify node-count report.
"""

from __future__ import annotations

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
from sonolus.backend.optimize import MINIMAL_PASSES, STANDARD_PASSES, OptimizerConfig, cfg_to_engine_node, run_passes
from sonolus.backend.optimize.flow import BasicBlock, cfg_to_text, traverse_cfg_reverse_postorder
from sonolus.backend.place import BlockPlace, TempBlock
from tests.backend._cfg_gen import OBS_BLOCKS, OBS_CAPTURE_LEN, build_cfg, programs
from tests.backend.test_corpus_roundtrip import _MODE_SETUP, _iter_callbacks

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
    return _interp(cfg_to_engine_node(run_passes(build(), MINIMAL_PASSES, OptimizerConfig(mode=mode, callback=cb))), rom)


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
# 7.4.1 scheduling decisions (structural).
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
    assert "<- Sign(" not in text, text  # not materialised to a temp


def test_single_use_does_not_sink_into_deeper_loop():
    # x = Sign(RuntimeUpdate[0]) defined before a loop, used once INSIDE the loop
    # body. Folding would sink the computation into the loop; treeify materialises
    # it instead (a single eval hoisted out, read via a temp inside).
    pre = BasicBlock(
        statements=[IRSet(_sc("x"), IRPureInstr(Op.Sign, [_ru(0)])), IRSet(_sc("k"), IRConst(0))]
    )
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
    # Sign is computed ONCE (materialised before the loop), read via a temp inside.
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
    assert "<- RuntimeUpdate" not in text, text  # not materialised


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
    assert "<- " in text, text  # materialised to a temp


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
    # Deliberate 7.4.1 divergence: a constant-index read of a WRITABLE block used
    # multiple times is materialised, never duplicated (duplication across a write
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
    # Materialised to one temp, read three times: the "20[0]" place text appears
    # exactly once (the temp store), not at the three uses.
    assert text.count("20[0]") == 1, text
    assert "<- 20[0]" in text, text


def test_pinned_read_fold_blocked_by_intervening_effect():
    # A single-use writable read folds into its consumer only if no effect lies
    # between def and use. With an intervening store it materialises instead.
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
    # blocked -> the read is stored to a temp (a "<- 20[0]" materialisation).
    assert "<- 20[0]" in te, te
    # allowed -> folded straight into the DebugLog, no temp store of the read.
    assert "DebugLog(20[0])" in tn, tn


def test_undef_read_lowers_to_shared_temp():
    # Reads of two distinct never-written scalars share ONE never-written temp.
    b0 = BasicBlock(
        statements=[
            IRInstr(Op.DebugLog, [_rd("u")]),
            IRInstr(Op.DebugLog, [_rd("w")]),
        ]
    )
    b1 = BasicBlock()
    b0.connect_to(b1, None)
    text = _low_text(b0)
    logged = [line for line in text.splitlines() if "DebugLog" in line]
    # Both logs read the same shared undef temp.
    assert logged[0].split("DebugLog(")[1] == logged[1].split("DebugLog(")[1], logged
    # Undef reads a never-written scalar -> -1.0 in the interpreter.
    it = _run_low(_undef_build)
    assert it.log == [-1.0, -1.0]


def _undef_build():
    b0 = BasicBlock(statements=[IRInstr(Op.DebugLog, [_rd("u")]), IRInstr(Op.DebugLog, [_rd("w")])])
    b1 = BasicBlock()
    b0.connect_to(b1, None)
    return b0


# ---------------------------------------------------------------------------
# 7.4.2 phi elimination.
# ---------------------------------------------------------------------------


def _swap_loop():
    b0 = BasicBlock(statements=[IRSet(_sc("a"), IRConst(1)), IRSet(_sc("b"), IRConst(2)), IRSet(_sc("n"), IRConst(0))])
    head = BasicBlock(test=IRPureInstr(Op.Less, [_rd("n"), IRConst(3)]))
    body = BasicBlock(
        statements=[IRSet(_sc("t"), _rd("a")), IRSet(_sc("a"), _rd("b")), IRSet(_sc("b"), _rd("t"))]
    )
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
# 7.4.3 n-ary emission: flatten + identity dropping.
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
    # (fixing the old RemoveRedundantArguments impure-recursion bug).
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
# 7.4.5 normalize_switch.
# ---------------------------------------------------------------------------


def _switch(conds, default: bool):
    a = BasicBlock(statements=[IRSet(_sc("s"), IRConst(0))], test=IRPureInstr(Op.Floor, [IRPureInstr(Op.Abs, [_rd("s")])]))
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


# ---------------------------------------------------------------------------
# Semantic parity: random-CFG recipes through run_lower vs MINIMAL reference.
# ---------------------------------------------------------------------------


def _f(x: float) -> bytes:
    return struct.pack(">d", float(x))


def _observe(it: Interpreter, ret=0.0) -> tuple:
    key = [b"log", *(_f(x) for x in it.log), b"mem"]
    for block in OBS_BLOCKS:
        key.extend(_f(it.get(block, i)) for i in range(OBS_CAPTURE_LEN))
    return tuple(key)


@settings(max_examples=300, deadline=None, suppress_health_check=list(HealthCheck))
@given(program=programs())
def test_random_programs_match_reference(program):
    def build():
        return build_cfg(program)

    ref = _interp(cfg_to_engine_node(run_passes(build(), MINIMAL_PASSES, OptimizerConfig())))
    low = _interp(cfg_to_engine_node(lower.run_lower(build())))
    assert _observe(ref) == _observe(low)


@settings(max_examples=120, deadline=None, suppress_health_check=list(HealthCheck))
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
    # Effective cost (section 2): a runtime-constant subtree the runtime folds to
    # a single push counts as 1 regardless of size.
    if not isinstance(node, FunctionNode):
        return 1
    if _is_rc(node, rcids):
        return 1
    return 1 + sum(_eff_nodes(a, rcids) for a in node.args)


@pytest.mark.parametrize("mode", list(_MODE_SETUP))
def test_corpus_run_lower(mode: Mode):
    rcids = _rc_block_ids(mode)
    raw_mine = raw_naive = eff_mine = eff_naive = 0
    count = 0
    for _label, cbname, factory in _iter_callbacks(mode):
        # mine: run_lower -> emit (verify + allocation-fits + emit are exercised).
        node = cfg_to_engine_node(lower.run_lower(factory(), mode, cbname))
        assert isinstance(node, FunctionNode)  # Block(JumpLoop(...))
        # naive: build_ssa -> naive out_of_ssa -> M1 pipeline -> emit.
        lowered = ir.debug_run(factory(), mode, cbname, phases=["cfg_cleanup", "ssa", "unssa"])
        naive = cfg_to_engine_node(run_passes(lowered, STANDARD_PASSES, OptimizerConfig(mode=mode, callback=cbname)))
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
