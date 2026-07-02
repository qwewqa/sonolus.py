"""Temp-memory allocation tests for the arena optimizer core (M1, §7.5).

Covers the three strategies (``bump`` / ``packing`` / ``try_bump``), the
true-first-fit gap packer, array contiguity, size-0 sentinels, the 4096-slot
cap, determinism, dead-store elimination, a semantic differential across the
three strategies (emit + ``Interpreter``) on hand-built interpretable CFGs, and
a pydori corpus check that packing ``verify()``s green and stays within the cap.
The wave-2 A/B comparisons against the now-deleted old ``Allocate`` /
``LivenessAnalysis`` served their purpose and are retired with M1. See
OPTIMIZER_REWRITE.md §4 and §7.5.
"""

from __future__ import annotations

import pytest

from sonolus.backend._opt import ir, lower  # noqa: PLC2701
from sonolus.backend.interpret import Interpreter
from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet
from sonolus.backend.mode import Mode
from sonolus.backend.ops import Op
from sonolus.backend.optimize import cfg_to_engine_node
from sonolus.backend.optimize.flow import BasicBlock, cfg_to_text, traverse_cfg_reverse_postorder
from sonolus.backend.place import BlockPlace, TempBlock

TEMP_BLOCK = 10000


def _sc(name):
    return BlockPlace(TempBlock(name, 1), 0, 0)


# --------------------------------------------------------------------------
# Helpers for reading offsets / slot usage out of an allocated CFG.
# --------------------------------------------------------------------------

def _effective_offset(place):
    if isinstance(place.index, int):
        return place.index + place.offset
    return place.offset


def slot_count(cfg):
    """max(effective offset)+1 over all emitted block-10000 places (spec metric)."""
    mx = 0

    def scan_place(p):
        nonlocal mx
        if isinstance(p, BlockPlace):
            if p.block == TEMP_BLOCK:
                mx = max(mx, _effective_offset(p) + 1)
            scan_val(p.index)
            if isinstance(p.block, BlockPlace):
                scan_place(p.block)

    def scan_val(v):
        if isinstance(v, (IRInstr, IRPureInstr)):
            for a in v.args:
                scan_val(a)
        elif isinstance(v, IRGet):
            scan_place(v.place)
        elif isinstance(v, IRSet):
            scan_place(v.place)
            scan_val(v.value)
        elif isinstance(v, BlockPlace):
            scan_place(v)

    for b in traverse_cfg_reverse_postorder(cfg):
        for st in b.statements:
            scan_val(st)
        scan_val(b.test)
    return mx


def stmt_count(cfg):
    return sum(len(b.statements) for b in traverse_cfg_reverse_postorder(cfg))


def store_offsets(cfg):
    """Effective offsets of each IRSet-to-block-10000 target, in statement order."""
    offs = []
    for b in traverse_cfg_reverse_postorder(cfg):
        offs.extend(
            _effective_offset(st.place)
            for st in b.statements
            if isinstance(st, IRSet) and isinstance(st.place, BlockPlace) and st.place.block == TEMP_BLOCK
        )
    return offs


# --------------------------------------------------------------------------
# Interference honored: co-live temps get distinct slots.
# --------------------------------------------------------------------------

def test_interference_honored_distinct_slots():
    b0 = BasicBlock()
    b0.statements = [
        IRSet(_sc("a"), IRConst(1)),
        IRSet(_sc("b"), IRConst(2)),
        IRSet(BlockPlace(500, 0, 0), IRPureInstr(Op.Add, [IRGet(_sc("a")), IRGet(_sc("b"))])),
    ]
    cfg = lower.run_allocate(b0, strategy="packing")
    offs = store_offsets(cfg)  # a-store, b-store
    assert offs[0] != offs[1], f"co-live a,b share a slot: {offs}"


def test_conditional_infinite_loop_store_survives():
    # `if c: while True: use(x)`: x is written before the branch and read only
    # inside the exit-unreachable spin loop. Liveness must keep x live at its store,
    # so packing's dead-store elimination must NOT drop it (pre-fix the spin block
    # was never visited, x looked dead, and its store was clobbered).
    def make():
        x = _sc("x")
        b0 = BasicBlock()
        spin = BasicBlock()
        after = BasicBlock()
        b0.statements = [IRSet(x, IRConst(5))]
        b0.test = IRGet(BlockPlace(501, 0, 0))  # runtime branch condition
        b0.connect_to(after, 0)  # false -> exit path
        b0.connect_to(spin, None)  # true -> infinite loop
        spin.statements = [IRSet(BlockPlace(500, 0, 0), IRGet(x))]  # reads x each iter
        spin.connect_to(spin, None)  # self-loop, never exits
        return b0

    cfg = lower.run_allocate(make(), strategy="packing")
    # x is the only temp, so its surviving store is the sole block-10000 write.
    assert len(store_offsets(cfg)) == 1, cfg_to_text(cfg)


def test_non_interfering_temps_share_slot():
    # a dies before b is defined: packing reuses the slot; bump does not.
    def make():
        b0 = BasicBlock()
        b0.statements = [
            IRSet(_sc("a"), IRConst(1)),
            IRSet(BlockPlace(500, 0, 0), IRGet(_sc("a"))),
            IRSet(_sc("b"), IRConst(2)),
            IRSet(BlockPlace(501, 0, 0), IRGet(_sc("b"))),
        ]
        return b0

    packed = lower.run_allocate(make(), strategy="packing")
    bumped = lower.run_allocate(make(), strategy="bump")
    assert slot_count(packed) == 1
    assert slot_count(bumped) == 2


# --------------------------------------------------------------------------
# Gap reuse: true first-fit reuses a dead array's slots, unlike bump.
# --------------------------------------------------------------------------

def test_gap_reuse_packs_below_bump():
    def make():
        arr = TempBlock("p", 4)
        b0 = BasicBlock()
        b0.statements = [
            IRSet(BlockPlace(arr, 0, 0), IRConst(1)),
            IRSet(_sc("n"), IRGet(BlockPlace(arr, 1, 0))),
            IRSet(BlockPlace(400, 0, 0), IRGet(BlockPlace(arr, 2, 0))),  # array dies here
            IRSet(_sc("t"), IRConst(7)),
            IRSet(BlockPlace(401, 0, 0), IRPureInstr(Op.Add, [IRGet(_sc("n")), IRGet(_sc("t"))])),
        ]
        return b0

    packed = lower.run_allocate(make(), strategy="packing")
    bumped = lower.run_allocate(make(), strategy="bump")
    # First-fit reuses the dead 4-slot array's range for n/t; bump never does.
    assert slot_count(packed) < slot_count(bumped)


# --------------------------------------------------------------------------
# Arrays: contiguous and non-overlapping.
# --------------------------------------------------------------------------

def test_arrays_contiguous_and_non_overlapping():
    a = TempBlock("a", 3)
    b = TempBlock("b", 2)
    b0 = BasicBlock()
    b0.statements = [
        IRSet(BlockPlace(a, 0, 0), IRConst(1)),
        IRSet(BlockPlace(a, 1, 0), IRConst(2)),
        IRSet(BlockPlace(a, 2, 0), IRConst(3)),
        IRSet(BlockPlace(b, 0, 0), IRConst(4)),
        IRSet(BlockPlace(b, 1, 0), IRConst(5)),
        IRSet(
            BlockPlace(500, 0, 0),
            IRPureInstr(
                Op.Add,
                [
                    IRGet(BlockPlace(a, 0, 0)),
                    IRGet(BlockPlace(a, 1, 0)),
                    IRGet(BlockPlace(a, 2, 0)),
                    IRGet(BlockPlace(b, 0, 0)),
                    IRGet(BlockPlace(b, 1, 0)),
                ],
            ),
        ),
    ]
    cfg = lower.run_allocate(b0, strategy="packing")
    offs = store_offsets(cfg)
    a_offs, b_offs = offs[:3], offs[3:5]
    # each array occupies a contiguous run.
    assert sorted(a_offs) == list(range(min(a_offs), min(a_offs) + 3))
    assert sorted(b_offs) == list(range(min(b_offs), min(b_offs) + 2))
    # the two arrays do not overlap.
    assert set(a_offs).isdisjoint(b_offs)


# --------------------------------------------------------------------------
# Size-0 temp -> sentinel offset -1.
# --------------------------------------------------------------------------

def test_size0_temp_sentinel_offset():
    e = TempBlock("e", 0)
    b0 = BasicBlock()
    b0.statements = [
        IRSet(BlockPlace(e, 0, 0), IRConst(0)),
        IRSet(BlockPlace(600, 0, 0), IRGet(BlockPlace(e, 0, 0))),
    ]
    for strat in ("bump", "packing", "try_bump"):
        cfg = lower.run_allocate(b0, strategy=strat)
        text = cfg_to_text(cfg)
        assert "10000[-1]" in text, f"{strat}: {text}"


# --------------------------------------------------------------------------
# 4096-slot cap.
# --------------------------------------------------------------------------

def test_overflow_raises():
    def make():
        arr = TempBlock("big", 5000)
        b0 = BasicBlock()
        b0.statements = [
            IRSet(BlockPlace(arr, 0, 0), IRConst(1)),
            IRSet(BlockPlace(700, 0, 0), IRGet(BlockPlace(arr, 0, 0))),
        ]
        return b0

    for strat in ("bump", "packing", "try_bump"):
        with pytest.raises(ValueError, match="Temporary memory limit exceeded"):
            lower.run_allocate(make(), strategy=strat)


def test_bump_rejects_near_int32_temp_without_overflow():
    # A size-1 temp then a near-INT32_MAX array: the bump accumulator's `index +
    # size` must not overflow int32 and slip past the cap; reject cleanly.
    def make():
        small = TempBlock("s", 1)
        big = TempBlock("g", 2**31 - 1)
        b0 = BasicBlock()
        b0.statements = [
            IRSet(BlockPlace(small, 0, 0), IRConst(1)),
            IRSet(BlockPlace(big, 0, 0), IRConst(2)),
        ]
        return b0

    with pytest.raises(ValueError, match="Temporary memory limit exceeded"):
        lower.run_allocate(make(), strategy="bump")


def test_try_bump_rejects_near_int32_temp_sum_without_overflow():
    # Two temps whose sizes sum past INT32_MAX: `_bump_fits` must not wrap its int32
    # sum below the cap and wrongly report "fits"; it declines bump and packing rejects.
    def make():
        a = TempBlock("a", 2)
        big = TempBlock("g", 2**31 - 1)
        b0 = BasicBlock()
        b0.statements = [
            IRSet(BlockPlace(a, 0, 0), IRConst(1)),
            IRSet(BlockPlace(big, 0, 0), IRConst(2)),
        ]
        return b0

    with pytest.raises(ValueError, match="Temporary memory limit exceeded"):
        lower.run_allocate(make(), strategy="try_bump")


def test_rewrite_places_rejects_out_of_range_constant_offset():
    # A constant index near INT32_MAX folded into a temp place: `temp_offset[t] +
    # offset` must not overflow int32 into a negative real-block offset; the sum
    # lands outside the 4096-slot temp block and is rejected.
    def make():
        pre = TempBlock("pre", 1)  # forces x's temp base >= 1
        x = TempBlock("x", 1)
        b0 = BasicBlock()
        b0.statements = [
            IRSet(BlockPlace(pre, 0, 0), IRConst(1)),
            IRSet(BlockPlace(x, 0, 2**31 - 1), IRConst(2)),
        ]
        return b0

    with pytest.raises(ValueError, match="Temporary memory limit exceeded"):
        lower.run_allocate(make(), strategy="bump")


def test_marshal_rejects_out_of_range_constant_index():
    # A constant real-block index+offset summing past INT32_MAX: the fold `offset +
    # index` must not silently overflow int32; marshal rejects the out-of-range place.
    def make():
        b0 = BasicBlock()
        b0.statements = [IRSet(BlockPlace(500, 2**31 - 1, 1), IRConst(0))]
        return b0

    with pytest.raises(ValueError, match="int32 range"):
        lower.run_allocate(make(), strategy="bump")


# --------------------------------------------------------------------------
# Determinism.
# --------------------------------------------------------------------------

def test_determinism_byte_identical():
    def make():
        arr = TempBlock("arr", 3)
        b0 = BasicBlock()
        head = BasicBlock()
        body = BasicBlock()
        ex = BasicBlock()
        b0.statements = [IRSet(_sc("i"), IRConst(0)), IRSet(BlockPlace(arr, 0, 0), IRConst(9))]
        b0.connect_to(head, None)
        head.test = IRPureInstr(Op.Less, [IRGet(_sc("i")), IRConst(3)])
        head.connect_to(ex, 0)
        head.connect_to(body, None)
        body.statements = [
            IRSet(BlockPlace(arr, IRGet(_sc("i")), 0), IRGet(_sc("i"))),
            IRSet(_sc("i"), IRPureInstr(Op.Add, [IRGet(_sc("i")), IRConst(1)])),
        ]
        body.connect_to(head, None)
        ex.statements = [IRSet(BlockPlace(800, 0, 0), IRGet(BlockPlace(arr, 0, 0)))]
        return b0

    for strat in ("bump", "packing", "try_bump"):
        t1 = cfg_to_text(lower.run_allocate(make(), strategy=strat))
        t2 = cfg_to_text(lower.run_allocate(make(), strategy=strat))
        assert t1 == t2


# --------------------------------------------------------------------------
# Dead-store elimination (packing only).
# --------------------------------------------------------------------------

def test_dead_store_dropped():
    b0 = BasicBlock()
    b0.statements = [
        IRSet(_sc("dead"), IRConst(1)),  # never read -> dropped
        IRSet(BlockPlace(900, 0, 0), IRConst(2)),
    ]
    cfg = lower.run_allocate(b0, strategy="packing")
    assert stmt_count(cfg) == 1
    assert "10000" not in cfg_to_text(cfg)  # the only temp store was dropped


def test_dead_self_copy_dropped():
    # mem[0] = mem[0] is a self-copy: dropped even for a real block.
    b0 = BasicBlock()
    b0.statements = [
        IRSet(BlockPlace(950, 0, 0), IRGet(BlockPlace(950, 0, 0))),
        IRSet(BlockPlace(950, 1, 0), IRConst(3)),
    ]
    cfg = lower.run_allocate(b0, strategy="packing")
    assert stmt_count(cfg) == 1


def test_dead_store_side_effecting_value_kept_as_bare():
    b0 = BasicBlock()
    b0.statements = [
        IRSet(_sc("unused"), IRInstr(Op.DebugLog, [IRConst(42)])),  # dead but side-effecting
        IRSet(BlockPlace(960, 0, 0), IRConst(1)),
    ]
    cfg = lower.run_allocate(b0, strategy="packing")
    # The store is gone but the DebugLog survives as a bare statement.
    assert stmt_count(cfg) == 2
    text = cfg_to_text(cfg)
    assert "DebugLog(42)" in text
    assert "<- DebugLog" not in text  # no longer a store target


def test_bump_keeps_dead_stores():
    # bump has no liveness, so it does not eliminate dead stores.
    b0 = BasicBlock()
    b0.statements = [
        IRSet(_sc("dead"), IRConst(1)),
        IRSet(BlockPlace(970, 0, 0), IRConst(2)),
    ]
    cfg = lower.run_allocate(b0, strategy="bump")
    assert stmt_count(cfg) == 2


# --------------------------------------------------------------------------
# verify() green after allocation.
# --------------------------------------------------------------------------

def test_verify_green_after_allocation():
    arr = TempBlock("arr", 4)
    b0 = BasicBlock()
    b0.statements = [
        IRSet(BlockPlace(arr, IRGet(_sc("k")), 0), IRConst(1)),
        IRSet(_sc("s"), IRGet(BlockPlace(arr, IRGet(_sc("k")), 1))),
        IRSet(BlockPlace(500, 0, 0), IRGet(_sc("s"))),
    ]
    for strat in ("bump", "packing", "try_bump"):
        func = lower.allocate_arena(b0, strategy=strat)
        assert func.verify()


# --------------------------------------------------------------------------
# Semantic differential across the three strategies on interpretable CFGs.
# --------------------------------------------------------------------------

def _interpret(cfg, seed=None):
    node = cfg_to_engine_node(cfg)  # non-destructive (marshals a fresh arena)
    interp = Interpreter()
    for block, values in (seed or {}).items():
        interp.blocks[block] = list(values)
    result = interp.run(node)
    memory = {k: v for k, v in interp.blocks.items() if k != TEMP_BLOCK}
    return result, list(interp.log), memory


def _assert_semantic_match(make_cfg, seed=None, mode=None, callback=None):
    # All three allocation strategies must be observably equivalent (result, log,
    # and non-scratch memory); packing is the reference.
    results = {
        strat: _interpret(lower.run_allocate(make_cfg(), mode, callback, strat), seed)
        for strat in ("packing", "bump", "try_bump")
    }
    ref = results["packing"]
    for strat, res in results.items():
        assert res == ref, f"strategy {strat}: {res} != packing {ref}"


def test_semantic_loop_sum():
    def make():
        b0 = BasicBlock()
        head = BasicBlock()
        body = BasicBlock()
        ex = BasicBlock()
        b0.statements = [IRSet(_sc("acc"), IRConst(0)), IRSet(_sc("i"), IRConst(0))]
        b0.connect_to(head, None)
        head.test = IRPureInstr(Op.Less, [IRGet(_sc("i")), IRConst(5)])
        head.connect_to(ex, 0)
        head.connect_to(body, None)
        body.statements = [
            IRSet(_sc("acc"), IRPureInstr(Op.Add, [IRGet(_sc("acc")), IRGet(_sc("i"))])),
            IRSet(_sc("i"), IRPureInstr(Op.Add, [IRGet(_sc("i")), IRConst(1)])),
        ]
        body.connect_to(head, None)
        ex.statements = [
            IRSet(BlockPlace(300, 0, 0), IRGet(_sc("acc"))),
            IRInstr(Op.DebugLog, [IRGet(_sc("acc"))]),
        ]
        return b0

    _assert_semantic_match(make)


def test_semantic_array_dynamic_index():
    def make():
        arr = TempBlock("arr", 5)
        b0 = BasicBlock()
        head = BasicBlock()
        body = BasicBlock()
        ex = BasicBlock()
        b0.statements = [IRSet(_sc("i"), IRConst(0))]
        b0.connect_to(head, None)
        head.test = IRPureInstr(Op.Less, [IRGet(_sc("i")), IRConst(5)])
        head.connect_to(ex, 0)
        head.connect_to(body, None)
        body.statements = [
            # arr[i] = i * 2 (dynamic index)
            IRSet(BlockPlace(arr, IRGet(_sc("i")), 0), IRPureInstr(Op.Multiply, [IRGet(_sc("i")), IRConst(2)])),
            IRSet(_sc("i"), IRPureInstr(Op.Add, [IRGet(_sc("i")), IRConst(1)])),
        ]
        body.connect_to(head, None)
        # read arr[3] back out and export the whole array to real memory
        ex.statements = [
            IRSet(BlockPlace(310, 0, 0), IRGet(BlockPlace(arr, 3, 0))),
            IRSet(BlockPlace(310, 1, 0), IRGet(BlockPlace(arr, IRConst(4), 0))),
        ]
        return b0

    _assert_semantic_match(make)


def test_semantic_dead_and_side_effecting_dead_store():
    def make():
        b0 = BasicBlock()
        b0.statements = [
            IRSet(_sc("dead"), IRConst(999)),  # pure dead store -> dropped
            IRSet(_sc("logged"), IRInstr(Op.DebugLog, [IRConst(7)])),  # side-effecting dead -> bare log
            IRSet(_sc("keep"), IRConst(3)),
            IRSet(BlockPlace(320, 0, 0), IRPureInstr(Op.Add, [IRGet(_sc("keep")), IRConst(1)])),
        ]
        return b0

    _assert_semantic_match(make)


def test_semantic_branch_and_memory():
    def make():
        b0 = BasicBlock()
        t = BasicBlock()
        f = BasicBlock()
        j = BasicBlock()
        b0.statements = [IRSet(_sc("x"), IRGet(BlockPlace(330, 0, 0)))]
        b0.test = IRPureInstr(Op.Less, [IRGet(_sc("x")), IRConst(10)])
        b0.connect_to(f, 0)
        b0.connect_to(t, None)
        t.statements = [IRSet(_sc("y"), IRPureInstr(Op.Multiply, [IRGet(_sc("x")), IRConst(2)]))]
        t.connect_to(j, None)
        f.statements = [IRSet(_sc("y"), IRPureInstr(Op.Add, [IRGet(_sc("x")), IRConst(100)]))]
        f.connect_to(j, None)
        j.statements = [IRSet(BlockPlace(331, 0, 0), IRGet(_sc("y")))]
        return b0

    _assert_semantic_match(make, seed={330: [7]})
    _assert_semantic_match(make, seed={330: [50]})


# --------------------------------------------------------------------------
# pydori corpus: packing verifies green and stays within the slot cap.
# --------------------------------------------------------------------------

def _enumerate_pydori_callbacks():
    from sonolus.build.compile import callback_to_cfg
    from sonolus.script.internal.callbacks import (
        navigate_callback,
        preprocess_callback,
        update_callback,
        update_spawn_callback,
    )
    from sonolus.script.internal.context import ModeContextState, ProjectContextState, RuntimeChecks
    from tests.regressions import pydori_project

    engine = pydori_project.engine.data

    def build(mode, archetypes, global_callbacks):
        for archetype in archetypes or []:
            archetype._init_fields()
            items = [
                (n, i, getattr(archetype, n))
                for n, i in archetype._supported_callbacks_.items()
                if getattr(archetype, n) not in archetype._default_callbacks_
            ]
            for cb_name, cb_info, cb in items:
                ps = ProjectContextState(runtime_checks=RuntimeChecks.NONE)
                ms = ModeContextState(
                    mode, {a: i for i, a in enumerate(archetypes)} if archetypes is not None else None
                )
                cfg = callback_to_cfg(ps, ms, cb, cb_info.name, archetype)
                yield (f"{mode.name}:{archetype.__name__}:{cb_name}", cfg, mode, cb_info.name)
        for cb_info, cb in global_callbacks or []:
            ps = ProjectContextState(runtime_checks=RuntimeChecks.NONE)
            ms = ModeContextState(mode, {a: i for i, a in enumerate(archetypes)} if archetypes is not None else None)
            cfg = callback_to_cfg(ps, ms, cb, cb_info.name, None)
            yield (f"{mode.name}:global:{cb_info.name}", cfg, mode, cb_info.name)

    yield from build(Mode.PLAY, engine.play.archetypes, None)
    yield from build(Mode.WATCH, engine.watch.archetypes, [(update_spawn_callback, engine.watch.update_spawn)])
    yield from build(Mode.PREVIEW, engine.preview.archetypes, None)
    yield from build(
        Mode.TUTORIAL,
        None,
        [
            (preprocess_callback, engine.tutorial.preprocess),
            (navigate_callback, engine.tutorial.navigate),
            (update_callback, engine.tutorial.update),
        ],
    )


def test_pydori_corpus_packing(capsys):
    new_total = 0
    n = 0
    for name, cfg, mode, callback in _enumerate_pydori_callbacks():
        n += 1
        func = lower.allocate_arena(cfg, mode, callback, "packing")
        assert func.verify(), f"{name}: verify failed"
        new_cfg = ir.to_basic_blocks(func)
        new_slots = slot_count(new_cfg)
        # Packing must stay within the 4096-slot temp-memory cap (block 10000).
        assert new_slots <= 4096, f"{name}: {new_slots} slots exceeds cap"
        new_total += new_slots

    assert n > 0
    with capsys.disabled():
        print(f"\n[pydori corpus] {n} callbacks: packing slots total={new_total}")
