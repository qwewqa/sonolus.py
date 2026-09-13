"""Coalescing around dead stores and disjoint scalar live ranges."""

import pytest
from sonolus.backend._opt.analysis import liveness_debug  # ruff: ignore[import-private-name]
from sonolus.backend._opt.lower import run_allocate, run_coalesce  # ruff: ignore[import-private-name]

from sonolus.backend.interpret import Interpreter
from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet
from sonolus.backend.ops import Op
from sonolus.backend.optimize import cfg_to_engine_node
from sonolus.backend.optimize.flow import BasicBlock, traverse_cfg_preorder
from sonolus.backend.place import BlockPlace, TempBlock

A = 20


def _run(cfg, initial=None):
    interpreter = Interpreter()
    for block, values in (initial or {}).items():
        interpreter.blocks[block] = list(values)
    interpreter.run(cfg_to_engine_node(run_allocate(cfg, strategy="bump")))
    return interpreter


def _copies(cfg):
    return [
        stmt
        for block in traverse_cfg_preorder(cfg)
        for stmt in block.statements
        if isinstance(stmt, IRSet)
        and isinstance(stmt.place.block, TempBlock)
        and isinstance(stmt.value, IRGet)
        and isinstance(stmt.value.place.block, TempBlock)
    ]


@pytest.mark.parametrize("count", [1, 65])
@pytest.mark.parametrize("effectful", [False, True])
def test_dead_store_does_not_interfere_disjoint_copy_sources_and_live_outs(count, effectful):
    sources = [TempBlock(f"source_{i}")[0] for i in range(count)]
    futures = [TempBlock(f"future_{i}")[0] for i in range(count)]
    dead = TempBlock("dead")[0]
    entry = BasicBlock(
        statements=[
            *(IRSet(place, IRGet(BlockPlace(A, i))) for i, place in enumerate(sources)),
            IRSet(dead, IRInstr(Op.DebugLog, [IRConst(99)]) if effectful else IRConst(99)),
            *(IRSet(dst, IRGet(src)) for src, dst in zip(sources, futures, strict=True)),
        ]
    )
    exit_block = BasicBlock(statements=[IRInstr(Op.DebugLog, [IRGet(place)]) for place in futures])
    entry.connect_to(exit_block, None)
    cfg = run_coalesce(entry)
    assert not _copies(cfg)
    stores = [stmt.place for stmt in cfg.statements if isinstance(stmt, IRSet) and isinstance(stmt.value, IRGet)]
    logs = [
        stmt.args[0].place
        for block in traverse_cfg_preorder(cfg)
        for stmt in block.statements
        if isinstance(stmt, IRInstr)
    ]
    assert stores == logs
    assert len(set(stores)) == count
    values = list(range(1, count + 1))
    assert _run(cfg, {A: values}).log == ([99] if effectful else []) + values


@pytest.mark.parametrize("choice", [0, 1])
def test_actual_overlap_on_one_successor_prevents_copy_coalescing(choice):
    source, future, dead = [TempBlock(name)[0] for name in ("source", "future", "dead")]
    entry = BasicBlock(
        statements=[
            IRSet(source, IRConst(7)),
            IRSet(dead, IRConst(99)),
            IRSet(future, IRGet(source)),
        ],
        test=IRGet(BlockPlace(A, 0)),
    )
    left = BasicBlock(
        statements=[
            IRSet(future, IRConst(11)),
            IRInstr(Op.DebugLog, [IRGet(source)]),
            IRInstr(Op.DebugLog, [IRGet(future)]),
        ]
    )
    right = BasicBlock(statements=[IRInstr(Op.DebugLog, [IRGet(future)])])
    entry.connect_to(left, 0)
    entry.connect_to(right, None)
    cfg = run_coalesce(entry)
    assert len(_copies(cfg)) == 1
    assert _run(cfg, {A: [choice]}).log == ([7, 11] if choice == 0 else [7])


def test_dead_destination_cannot_clobber_a_value_live_across_its_store():
    observed, dead = [TempBlock(name)[0] for name in ("observed", "dead")]
    entry = BasicBlock(
        statements=[
            IRSet(observed, IRConst(7)),
            IRSet(dead, IRGet(observed)),
            IRInstr(Op.DebugLog, [IRGet(dead)]),
            IRSet(dead, IRConst(99)),
            IRInstr(Op.DebugLog, [IRGet(observed)]),
        ]
    )
    cfg = run_coalesce(entry)
    assert len(_copies(cfg)) == 1
    assert _run(cfg).log == [7, 7]


@pytest.mark.parametrize("padding", [0, 64])
def test_dead_target_retains_conservative_edges_to_block_live_out(padding):
    observed, dead = [TempBlock(name)[0] for name in ("observed", "dead")]
    entry = BasicBlock(
        statements=[
            IRSet(observed, IRConst(1)),
            *(IRSet(TempBlock(f"padding_{i}")[0], IRConst(0)) for i in range(padding)),
            IRSet(dead, IRGet(observed)),
            IRInstr(Op.DebugLog, [IRGet(dead)]),
            IRSet(dead, IRConst(2)),
            IRSet(observed, IRConst(3)),
        ]
    )
    entry.connect_to(BasicBlock(statements=[IRInstr(Op.DebugLog, [IRGet(observed)])]), None)
    live = liveness_debug(entry)
    assert list(live["stmt_live"].values())[-3] == set()
    assert live["live_out"][0] == {"observed"}
    cfg = run_coalesce(entry)
    assert len(_copies(cfg)) == 1
    assert _copies(cfg)[0].place != _copies(cfg)[0].value.place
    assert _run(cfg).log == [1, 3]


@pytest.mark.parametrize("rotations", [0, 1, 2, 3, 5])
def test_copy_cycles_keep_simultaneously_live_values_distinct(rotations):
    a, b, c, saved, count, dead = [TempBlock(name)[0] for name in ("a", "b", "c", "saved", "count", "dead")]
    entry = BasicBlock(
        statements=[IRSet(a, IRConst(11)), IRSet(b, IRConst(22)), IRSet(c, IRConst(33)), IRSet(count, IRConst(0))]
    )
    head = BasicBlock(test=IRPureInstr(Op.Less, [IRGet(count), IRGet(BlockPlace(A, 0))]))
    body = BasicBlock(
        statements=[
            IRSet(dead, IRConst(99)),
            IRSet(saved, IRGet(a)),
            IRSet(a, IRGet(b)),
            IRSet(b, IRGet(c)),
            IRSet(c, IRGet(saved)),
            IRSet(count, IRPureInstr(Op.Add, [IRGet(count), IRConst(1)])),
        ]
    )
    exit_block = BasicBlock(statements=[IRInstr(Op.DebugLog, [IRGet(place)]) for place in (a, b, c)])
    entry.connect_to(head, None)
    head.connect_to(exit_block, 0)
    head.connect_to(body, None)
    body.connect_to(head, None)
    cfg = run_coalesce(entry)
    expected = [11, 22, 33]
    for _ in range(rotations):
        expected = [expected[1], expected[2], expected[0]]
    assert _run(cfg, {A: [rotations]}).log == expected
