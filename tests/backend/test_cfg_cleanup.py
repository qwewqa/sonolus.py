"""Tests for the ``cfg_cleanup`` mid-end pass (milestone M1, section 7.1).

Three layers (the wave-2 block-count-<=-old corpus assertion compared against the
now-deleted old cleanup pipeline and is retired with M1):

1. Hand-built unit tests asserting exact ``cfg_to_text`` output for every
   cleanup rule (constant-test folds in both if-shape polarities incl. NaN,
   multiway matched/default/default-less, empty-block threading incl. chains,
   parallel-edge dedup, single-pred/succ merge, tail-dup enabling threading,
   exit-block sharing, unreachable elimination).
2. Semantic differential on hand-built interpretable CFGs: the ORIGINAL and the
   CLEANED CFG, each run through the NEW ``run_allocate`` (bump) ->
   ``cfg_to_engine_node`` -> ``Interpreter`` path, must agree on result, debug
   log, and observable memory. Covers loops, switches, dead branches, ``Op.Break``.
3. Corpus properties over all pydori callbacks (raw CFGs): verify() stays green,
   cleanup is idempotent, block/edge counts never grow, and the output shape is
   canonical.
"""

from __future__ import annotations

import math

import pytest
from sonolus.backend._opt.midend import cleanup_func, run_cfg_cleanup  # noqa: PLC2701

from sonolus.backend._opt import ir as _ir  # noqa: PLC2701
from sonolus.backend._opt import lower  # noqa: PLC2701
from sonolus.backend.interpret import Interpreter
from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet
from sonolus.backend.mode import Mode
from sonolus.backend.ops import Op
from sonolus.backend.optimize import cfg_to_engine_node
from sonolus.backend.optimize.flow import BasicBlock, cfg_to_text, traverse_cfg_reverse_postorder
from sonolus.backend.place import BlockPlace, TempBlock
from tests.backend.test_corpus_roundtrip import _iter_callbacks

IO = 500  # a real memory block used for interpretable-CFG input/output


def log(n):
    return IRInstr(Op.DebugLog, [IRConst(n)])


def tmp(name="x"):
    return BlockPlace(TempBlock(name, 1))


def rd(name="x"):
    return IRGet(tmp(name))


def io_read(index=0):
    return IRGet(BlockPlace(IO, index))


def io_write(index, value):
    return IRSet(BlockPlace(IO, index), value)


def clean_text(entry, **kw):
    return cfg_to_text(run_cfg_cleanup(entry, **kw))


# ==========================================================================
# 1. Hand-built unit tests -- exact cfg_to_text
# ==========================================================================


def _if_shape(test_value):
    """A {0: false, None: true} block on a constant test; false logs 10, true 20."""
    a = BasicBlock(test=IRConst(test_value))
    f = BasicBlock(statements=[log(10)])
    t = BasicBlock(statements=[log(20)])
    a.connect_to(f, 0)
    a.connect_to(t, None)
    return a


def test_const_fold_if_false():
    # test == 0 -> the cond-0 (false) edge.
    assert clean_text(_if_shape(0)) == "0:\n  DebugLog(10)\n  goto exit\n"


def test_const_fold_if_true():
    # test != 0 -> the None (true) edge.
    assert clean_text(_if_shape(5)) == "0:\n  DebugLog(20)\n  goto exit\n"


def test_const_fold_if_nan_is_true():
    # NaN != 0 -> the None (true) edge (matches runtime If truthiness).
    assert clean_text(_if_shape(math.nan)) == "0:\n  DebugLog(20)\n  goto exit\n"


def test_const_fold_negzero_is_false():
    # -0.0 == 0.0 -> the false edge.
    assert clean_text(_if_shape(-0.0)) == "0:\n  DebugLog(10)\n  goto exit\n"


def _multiway(test_value, *, default):
    a = BasicBlock(test=IRConst(test_value))
    b1 = BasicBlock(statements=[log(1)])
    b2 = BasicBlock(statements=[log(2)])
    a.connect_to(b1, 1)
    a.connect_to(b2, 2)
    if default:
        d = BasicBlock(statements=[log(9)])
        a.connect_to(d, None)
    return a


def test_const_fold_multiway_matched():
    assert clean_text(_multiway(2, default=True)) == "0:\n  DebugLog(2)\n  goto exit\n"


def test_const_fold_multiway_default():
    assert clean_text(_multiway(7, default=True)) == "0:\n  DebugLog(9)\n  goto exit\n"


def test_const_fold_multiway_defaultless_no_match_becomes_exit():
    assert clean_text(_multiway(7, default=False)) == "0:\n  goto exit\n"


def test_const_fold_multiway_defaultless_matched():
    assert clean_text(_multiway(1, default=False)) == "0:\n  DebugLog(1)\n  goto exit\n"


def test_multiway_non_constant_test_is_kept():
    # A non-constant multiway test is not folded; switch shape is preserved.
    a = BasicBlock(test=rd())
    b1 = BasicBlock(statements=[log(1)])
    b2 = BasicBlock(statements=[log(2)])
    d = BasicBlock(statements=[log(9)])
    a.connect_to(b1, 1)
    a.connect_to(b2, 2)
    a.connect_to(d, None)
    assert clean_text(a) == (
        "0:\n  goto when v0\n    1 -> 3\n    2 -> 2\n    default -> 1\n"
        "1:\n  DebugLog(9)\n  goto exit\n"
        "2:\n  DebugLog(2)\n  goto exit\n"
        "3:\n  DebugLog(1)\n  goto exit\n"
    )


def test_empty_block_threading():
    # A cond-> [empty E -> D] / [F], both joining J. E is threaded away.
    a = BasicBlock(test=rd())
    e = BasicBlock()
    d = BasicBlock(statements=[log(1)])
    f = BasicBlock(statements=[log(2)])
    j = BasicBlock(statements=[log(3)])
    a.connect_to(e, None)
    a.connect_to(f, 0)
    e.connect_to(d, None)
    d.connect_to(j, None)
    f.connect_to(j, None)
    assert clean_text(a) == (
        "0:\n  goto 1 if v0 else 2\n"
        "1:\n  DebugLog(1)\n  DebugLog(3)\n  goto exit\n"
        "2:\n  DebugLog(2)\n  DebugLog(3)\n  goto exit\n"
    )


def test_empty_block_chain_threading():
    # Chain of two empty blocks fully threaded.
    a = BasicBlock(statements=[log(1)])
    e1 = BasicBlock()
    e2 = BasicBlock()
    d = BasicBlock(statements=[log(2)])
    a.connect_to(e1, None)
    e1.connect_to(e2, None)
    e2.connect_to(d, None)
    assert clean_text(a) == "0:\n  DebugLog(1)\n  DebugLog(2)\n  goto exit\n"


def test_parallel_edge_dedup_default_covers_value():
    # {0: D, None: D} -> both to D -> unconditional to D.
    a = BasicBlock(test=rd())
    d = BasicBlock(statements=[log(5)])
    a.connect_to(d, 0)
    a.connect_to(d, None)
    assert clean_text(a) == "0:\n  DebugLog(5)\n  goto exit\n"


def test_single_pred_succ_merge_chain():
    a = BasicBlock(statements=[log(1)])
    b = BasicBlock(statements=[log(2)])
    c = BasicBlock(statements=[log(3)])
    a.connect_to(b, None)
    b.connect_to(c, None)
    assert clean_text(a) == "0:\n  DebugLog(1)\n  DebugLog(2)\n  DebugLog(3)\n  goto exit\n"


def test_tail_duplication_exposes_threading():
    # M (1 stmt, conditional) is reached unconditionally from both L and R; it is
    # duplicated into each so the branch is exposed directly in the predecessors.
    a = BasicBlock(test=rd())
    left = BasicBlock(statements=[log(1)])
    right = BasicBlock(statements=[log(2)])
    m = BasicBlock(statements=[log(7)], test=rd())
    p = BasicBlock(statements=[log(8)])
    q = BasicBlock(statements=[log(9)])
    a.connect_to(left, 0)
    a.connect_to(right, None)
    left.connect_to(m, None)
    right.connect_to(m, None)
    m.connect_to(p, 0)
    m.connect_to(q, None)
    assert clean_text(a) == (
        "0:\n  goto 1 if v0 else 2\n"
        "1:\n  DebugLog(2)\n  DebugLog(7)\n  goto 3 if v0 else 4\n"
        "2:\n  DebugLog(1)\n  DebugLog(7)\n  goto 3 if v0 else 4\n"
        "3:\n  DebugLog(9)\n  goto exit\n"
        "4:\n  DebugLog(8)\n  goto exit\n"
    )


def test_exit_block_sharing():
    # Two empty exit blocks collapse into one shared exit.
    a = BasicBlock(test=rd())
    e1 = BasicBlock()
    e2 = BasicBlock()
    a.connect_to(e1, 0)
    a.connect_to(e2, None)
    assert clean_text(a) == "0:\n  goto exit\n"


def test_non_empty_exits_are_kept_separate():
    # Exit blocks WITH statements keep their statements (not merged).
    a = BasicBlock(test=rd())
    e1 = BasicBlock(statements=[log(1)])
    e2 = BasicBlock(statements=[log(2)])
    a.connect_to(e1, 0)
    a.connect_to(e2, None)
    assert clean_text(a) == (
        "0:\n  goto 1 if v0 else 2\n"
        "1:\n  DebugLog(2)\n  goto exit\n"
        "2:\n  DebugLog(1)\n  goto exit\n"
    )


def test_unreachable_elimination():
    a = BasicBlock(statements=[log(1)])
    fin = BasicBlock()
    dead = BasicBlock(statements=[log(99)])
    a.connect_to(fin, None)
    dead.connect_to(fin, None)  # dead is not reachable from a
    assert clean_text(a) == "0:\n  DebugLog(1)\n  goto exit\n"


def test_unreachable_via_const_fold():
    # Folding the const test makes one arm unreachable; it is dropped.
    a = BasicBlock(test=IRConst(1))
    live = BasicBlock(statements=[log(1)], test=rd())
    dead = BasicBlock(statements=[log(99)])
    x = BasicBlock(statements=[log(2)])
    y = BasicBlock(statements=[log(3)])
    a.connect_to(dead, 0)  # test==1 -> not taken
    a.connect_to(live, None)  # taken
    live.connect_to(x, 0)
    live.connect_to(y, None)
    dead.connect_to(x, None)
    assert clean_text(a) == (
        "0:\n  DebugLog(1)\n  goto 1 if v0 else 2\n"
        "1:\n  DebugLog(3)\n  goto exit\n"
        "2:\n  DebugLog(2)\n  goto exit\n"
    )


def test_phi_safe_disables_tail_duplication():
    # With phi_safe=True, the tiny multi-pred block M is NOT duplicated; it stays
    # a shared block (only threading/merge/fold run).
    def build():
        a = BasicBlock(test=rd())
        left = BasicBlock(statements=[log(1)])
        right = BasicBlock(statements=[log(2)])
        m = BasicBlock(statements=[log(7)], test=rd())
        p = BasicBlock(statements=[log(8)])
        q = BasicBlock(statements=[log(9)])
        a.connect_to(left, 0)
        a.connect_to(right, None)
        left.connect_to(m, None)
        right.connect_to(m, None)
        m.connect_to(p, 0)
        m.connect_to(q, None)
        return a

    safe = cfg_to_text(run_cfg_cleanup(build(), phi_safe=True))
    unsafe = cfg_to_text(run_cfg_cleanup(build(), phi_safe=False))
    # phi-safe keeps M as its own block; the tail-dup variant inlines it.
    assert "DebugLog(7)" in safe
    assert safe.count("DebugLog(7)") == 1  # not duplicated
    assert unsafe.count("DebugLog(7)") == 2  # duplicated into both preds


# ==========================================================================
# 2. Semantic differential on interpretable CFGs
# ==========================================================================


def _interp(entry, seed):
    # Allocate temps (bump, == old AllocateBasic behaviour) then emit via the new
    # arena emitter, so the original and cleaned CFGs are compared through the same
    # NEW toolchain (OPTIMIZER_REWRITE.md 5).
    allocated = lower.run_allocate(entry, strategy="bump")
    node = cfg_to_engine_node(allocated)
    it = Interpreter()
    for (blk, idx), val in seed.items():
        it.set(blk, idx, val)
    result = it.run(node)
    # Compare observable memory only: temp scratch (block 10000) allocation may
    # legitimately differ between the original and cleaned CFGs.
    mem = tuple(sorted((k, tuple(v)) for k, v in it.blocks.items() if k != 10000))
    return result, tuple(it.log), mem


def _assert_equiv(build, seeds):
    for seed in seeds:
        original = _interp(build(), dict(seed))
        cleaned = _interp(run_cfg_cleanup(build()), dict(seed))
        assert original == cleaned, f"seed={seed}\n original={original}\n cleaned ={cleaned}"


def _build_countdown():
    # i = IO[0]; while i > 0: log(i); i -= 1; IO[1] = i
    init = BasicBlock(statements=[IRSet(tmp("i"), io_read(0))])
    header = BasicBlock(test=IRPureInstr(Op.Greater, [rd("i"), IRConst(0)]))
    body = BasicBlock(
        statements=[
            IRInstr(Op.DebugLog, [rd("i")]),
            IRSet(tmp("i"), IRPureInstr(Op.Subtract, [rd("i"), IRConst(1)])),
        ]
    )
    done = BasicBlock(statements=[io_write(1, rd("i"))])
    init.connect_to(header, None)
    header.connect_to(body, None)  # true: i > 0
    header.connect_to(done, 0)  # false
    body.connect_to(header, None)
    return init


def test_diff_loop():
    _assert_equiv(_build_countdown, [{(IO, 0): v} for v in (0, 1, 3, 7)])


def _build_switch():
    # switch IO[0] {0: log 10, 1: log 11, 2: log 12, default: log 99}; IO[1] = case
    a = BasicBlock(test=io_read(0))
    branches = []
    for cond, logv in ((0, 10), (1, 11), (2, 12), (None, 99)):
        blk = BasicBlock(statements=[log(logv), io_write(1, IRConst(logv))])
        a.connect_to(blk, cond)
        branches.append(blk)
    return a


def test_diff_switch():
    _assert_equiv(_build_switch, [{(IO, 0): v} for v in (0, 1, 2, 3, 5, -1)])


def _build_dead_branch(const):
    def build():
        a = BasicBlock(test=IRConst(const))
        f = BasicBlock(statements=[log(100), io_write(1, IRConst(100))])
        t = BasicBlock(statements=[log(200), io_write(1, IRConst(200))])
        a.connect_to(f, 0)
        a.connect_to(t, None)
        return a

    return build


def test_diff_dead_branch():
    _assert_equiv(_build_dead_branch(0), [{}])
    _assert_equiv(_build_dead_branch(1), [{}])


def _build_diamond():
    # if IO[0] != 0: r = 111 else r = 222; log(r); IO[1] = r
    a = BasicBlock(test=io_read(0))
    tb = BasicBlock(statements=[IRSet(tmp("r"), IRConst(111))])
    fb = BasicBlock(statements=[IRSet(tmp("r"), IRConst(222))])
    join = BasicBlock(statements=[IRInstr(Op.DebugLog, [rd("r")]), io_write(1, rd("r"))])
    a.connect_to(fb, 0)
    a.connect_to(tb, None)
    tb.connect_to(join, None)
    fb.connect_to(join, None)
    return a


def test_diff_diamond():
    _assert_equiv(_build_diamond, [{(IO, 0): v} for v in (0, 1, 2)])


def _build_nested_loop_switch():
    # for i in IO[0]..0: switch (i % 3): log; then exit
    init = BasicBlock(statements=[IRSet(tmp("i"), io_read(0))])
    header = BasicBlock(test=IRPureInstr(Op.Greater, [rd("i"), IRConst(0)]))
    dispatch = BasicBlock(test=IRPureInstr(Op.Mod, [rd("i"), IRConst(3)]))
    c0 = BasicBlock(statements=[log(1000)])
    c1 = BasicBlock(statements=[log(1001)])
    c2 = BasicBlock(statements=[log(1002)])
    step = BasicBlock(statements=[IRSet(tmp("i"), IRPureInstr(Op.Subtract, [rd("i"), IRConst(1)]))])
    done = BasicBlock(statements=[io_write(1, IRConst(-7))])
    init.connect_to(header, None)
    header.connect_to(dispatch, None)
    header.connect_to(done, 0)
    dispatch.connect_to(c0, 0)
    dispatch.connect_to(c1, 1)
    dispatch.connect_to(c2, None)
    for c in (c0, c1, c2):
        c.connect_to(step, None)
    step.connect_to(header, None)
    return init


def test_diff_nested_loop_switch():
    _assert_equiv(_build_nested_loop_switch, [{(IO, 0): v} for v in (0, 1, 4, 6)])


def _build_break():
    # log(1); if IO[0] != 0: break with IO[0]; log(2); return 0
    a = BasicBlock(statements=[log(1)], test=io_read(0))
    brk = BasicBlock(statements=[IRInstr(Op.Break, [IRConst(1), io_read(0)])])
    cont = BasicBlock(statements=[log(2)])
    a.connect_to(cont, 0)
    a.connect_to(brk, None)
    return a


def test_diff_break():
    _assert_equiv(_build_break, [{(IO, 0): v} for v in (0, 4)])


# ==========================================================================
# 3. Corpus properties (raw CFGs, all pydori callbacks)
# ==========================================================================


def _counts(entry):
    blocks = list(traverse_cfg_reverse_postorder(entry))
    nb = len(blocks)
    ne = sum(len(b.outgoing) for b in blocks)
    ns = sum(len(b.statements) for b in blocks)
    return nb, ne, ns


def _shape_ok(entry):
    for b in traverse_cfg_reverse_postorder(entry):
        conds = [e.cond for e in b.outgoing]
        n_out = len(b.outgoing)
        # no duplicate parallel edges (same cond)
        assert len(conds) == len(set(conds)), "duplicate parallel edges"
        # no constant test on a reachable conditional block
        if n_out >= 2:
            assert not isinstance(b.test, IRConst), "constant test on a conditional block"
        # no empty unconditional-only block (should be threaded away)
        if not b.statements and n_out == 1 and None in conds:
            raise AssertionError("empty unconditional-only block survived")
    # at most one empty exit block (statement-less, no outgoing)
    empty_exits = sum(1 for b in traverse_cfg_reverse_postorder(entry) if not b.statements and not b.outgoing)
    assert empty_exits <= 1, f"{empty_exits} empty exit blocks"


@pytest.mark.parametrize("mode", list(Mode))
def test_corpus_cleanup_properties(mode):
    total_in = [0, 0, 0]
    total_out = [0, 0, 0]
    n = 0
    for label, callback_name, factory in _iter_callbacks(mode):
        cfg = factory()
        ci, ei, si = _counts(cfg)

        cleaned = cleanup_func(factory(), mode, callback_name)
        cleaned.verify()  # keeps verify() green
        out = _ir.to_basic_blocks(cleaned)
        co, eo, so = _counts(out)

        # never increases block or edge counts
        assert co <= ci, f"{label}: blocks grew {ci} -> {co}"
        assert eo <= ei, f"{label}: edges grew {ei} -> {eo}"

        # canonical output shape
        _shape_ok(out)

        # idempotent (a second run changes nothing)
        out2 = run_cfg_cleanup(out, mode, callback_name)
        assert cfg_to_text(out) == cfg_to_text(out2), f"{label}: not idempotent"

        for acc, val in zip((total_in, total_out), ((ci, ei, si), (co, eo, so)), strict=True):
            acc[0] += val[0]
            acc[1] += val[1]
            acc[2] += val[2]
        n += 1

    assert n > 0
    # Blocks and edges shrink substantially; statements may rise slightly because
    # tail-duplication trades a small statement increase for far fewer blocks and
    # dispatches (a deliberate CoalesceSmallConditionalBlocks-style tradeoff).
    assert total_out[0] <= total_in[0]
    assert total_out[1] <= total_in[1]
    print(
        f"\n[{mode.name}] cleanup over {n} callbacks: "
        f"blocks {total_in[0]}->{total_out[0]}, "
        f"edges {total_in[1]}->{total_out[1]}, "
        f"statements {total_in[2]}->{total_out[2]}"
    )


@pytest.mark.parametrize("mode", list(Mode))
def test_corpus_statements_nonincreasing_without_tail_dup(mode):
    # The phi-safe subset (no tail-duplication) never increases statement counts.
    for label, callback_name, factory in _iter_callbacks(mode):
        _, _, si = _counts(factory())
        out = run_cfg_cleanup(factory(), mode, callback_name, phi_safe=True)
        _, _, so = _counts(out)
        assert so <= si, f"{label}: statements grew {si} -> {so} under phi_safe"
