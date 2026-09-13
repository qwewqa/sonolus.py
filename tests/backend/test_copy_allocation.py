"""Copy-aware allocation, interference, and fusion tradeoffs."""

from itertools import starmap

import pytest
from sonolus.backend._opt.ir import debug_run  # ruff: ignore[import-private-name]
from sonolus.backend._opt.lower import run_fuse_rmw  # ruff: ignore[import-private-name]

from sonolus.backend.blocks import PlayBlock
from sonolus.backend.interpret import Interpreter
from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet
from sonolus.backend.ops import Op
from sonolus.backend.optimize import cfg_to_engine_node
from sonolus.backend.optimize.flow import BasicBlock, cfg_to_text, traverse_cfg_preorder
from sonolus.backend.place import BlockPlace, TempBlock
from sonolus.script.internal.context import ReadOnlyMemory

A, B = 20, 21


def _opt(cfg, strategy="packing"):
    return debug_run(run_fuse_rmw(cfg, strategy=strategy), phases=["copy"])


def _copies(cfg):
    return [
        stmt
        for block in traverse_cfg_preorder(cfg)
        for stmt in block.statements
        if isinstance(stmt, IRInstr) and stmt.op == Op.Copy
    ]


def _copy_lengths(cfg):
    return [stmt.args[4].value for stmt in _copies(cfg)]


def _run(cfg, initial=None):
    interpreter = Interpreter()
    interpreter.blocks[3000] = list(ReadOnlyMemory().values)
    for block, values in (initial or {}).items():
        interpreter.blocks[block] = list(values)
    interpreter.run(cfg_to_engine_node(cfg))
    return interpreter


def _sum(places):
    return IRPureInstr(Op.Add, [IRGet(place) for place in places])


def _scalars():
    return [TempBlock(name)[0] for name in ("a", "b", "c", "d")]


def _initialize(places):
    return [IRSet(places[i], IRConst(11 * (i + 1))) for i in (0, 2, 1, 3)]


@pytest.mark.parametrize("order", [(0, 1, 2, 3), (2, 0, 3, 1), (3, 2, 1, 0)])
def test_scalar_sources_follow_copy_order(order):
    places = _scalars()
    cfg = _opt(
        BasicBlock(
            statements=[
                *_initialize(places),
                *(IRSet(BlockPlace(B, i), IRGet(places[i])) for i in order),
            ]
        )
    )
    assert _copy_lengths(cfg) == [4]
    result = _run(cfg)
    assert result.blocks[B] == [11, 22, 33, 44]


@pytest.mark.parametrize("count", [65, 129])
def test_grouped_interference_crosses_bitset_word_boundaries(count):
    places = [TempBlock(f"t{i}")[0] for i in range(count)]
    cfg = _opt(
        BasicBlock(
            statements=[
                *(IRSet(places[i], IRConst(i + 1)) for i in [*range(0, count, 2), *range(1, count, 2)]),
                *(IRSet(BlockPlace(B, i), IRGet(place)) for i, place in enumerate(places)),
            ]
        )
    )
    assert len(_copies(cfg)) < count
    result = _run(cfg)
    assert result.blocks[B] == list(range(1, count + 1))
    assert len(result.blocks[10000]) == count


@pytest.mark.parametrize(("strategy", "lengths"), [("packing", [4]), ("try_bump", [])])
def test_fast_fallback_keeps_original_packing(strategy, lengths):
    array = TempBlock("array", 4096)
    places = _scalars()
    cfg = _opt(
        BasicBlock(
            statements=[
                IRSet(array[4095], IRConst(7)),
                IRInstr(Op.DebugLog, [IRGet(array[4095])]),
                *_initialize(places),
                *(IRSet(BlockPlace(B, i), IRGet(place)) for i, place in enumerate(places)),
            ]
        ),
        strategy,
    )
    assert _copy_lengths(cfg) == lengths
    if strategy == "try_bump":
        stores = [stmt for stmt in cfg.statements if isinstance(stmt, IRSet) and stmt.place.block == B]
        assert len(stores) == 4
        assert all(isinstance(stmt.value, IRGet) for stmt in stores)
    result = _run(cfg)
    assert result.blocks[B] == [11, 22, 33, 44]
    assert result.log == [7]


@pytest.mark.parametrize("order", [(0, 1, 2, 3), (2, 0, 3, 1), (3, 2, 1, 0)])
def test_scalar_destinations_follow_copy_order(order):
    places = _scalars()
    cfg = _opt(
        BasicBlock(
            statements=[
                *_initialize(places),
                IRInstr(Op.DebugLog, [_sum(places)]),
                *(IRSet(places[i], IRGet(BlockPlace(A, i))) for i in order),
                *(IRInstr(Op.DebugLog, [IRGet(place)]) for place in places),
            ]
        )
    )
    assert _copy_lengths(cfg) == [4]
    result = _run(cfg, {A: [7, 13, 19, 23]})
    assert result.log == [110, 7, 13, 19, 23]


def test_scalar_sources_and_destinations_can_both_be_grouped():
    sources = _scalars()
    destinations = [TempBlock(name)[0] for name in ("p", "q", "r", "s")]
    cfg = _opt(
        BasicBlock(
            statements=[
                *_initialize(sources),
                *(IRSet(place, IRConst(11 * (i + 1))) for i, place in enumerate(destinations)),
                IRInstr(Op.DebugLog, [_sum(destinations)]),
                *(IRSet(destination, IRGet(source)) for source, destination in zip(sources, destinations, strict=True)),
                IRInstr(Op.DebugLog, [_sum([*sources, *destinations])]),
            ]
        )
    )
    assert _copy_lengths(cfg) == [4]
    assert _run(cfg).log == [110, 220]


def test_scalar_zero_stores_become_a_rom_copy():
    places = _scalars()
    cfg = _opt(
        BasicBlock(
            statements=[
                *_initialize(places),
                IRInstr(Op.DebugLog, [_sum(places)]),
                *(IRSet(place, IRConst(0)) for place in places),
                IRInstr(Op.DebugLog, [_sum(places)]),
            ]
        )
    )
    assert _copy_lengths(cfg) == [4]
    assert _copies(cfg)[0].args[0].value == 3000
    assert _run(cfg).log == [110, 0]


@pytest.mark.parametrize("choice", [0, 1])
def test_copy_groups_preserve_values_at_cfg_joins(choice):
    places = _scalars()
    entry = BasicBlock(statements=_initialize(places), test=IRGet(BlockPlace(A, 0)))
    left = BasicBlock(statements=[IRSet(places[0], IRPureInstr(Op.Add, [IRGet(places[0]), IRConst(10)]))])
    right = BasicBlock(statements=[IRSet(places[1], IRPureInstr(Op.Add, [IRGet(places[1]), IRConst(20)]))])
    join = BasicBlock(
        statements=[
            *(IRSet(BlockPlace(B, i), IRGet(place)) for i, place in enumerate(places)),
            IRInstr(Op.DebugLog, [_sum(places)]),
        ]
    )
    entry.connect_to(left, 0)
    entry.connect_to(right, None)
    left.connect_to(join, None)
    right.connect_to(join, None)
    cfg = _opt(entry)
    assert _copy_lengths(cfg) == [4]
    result = _run(cfg, {A: [choice]})
    expected = [21, 22, 33, 44] if choice == 0 else [11, 42, 33, 44]
    assert result.blocks[B] == expected
    assert result.log == [sum(expected)]


@pytest.mark.parametrize(("count", "index"), [(0, 0), (1, 2), (3, 0), (3, 3)])
def test_copy_groups_preserve_arrays_and_dynamic_indices_in_loops(count, index):
    array = TempBlock("array", 4)
    places = _scalars()
    counter = TempBlock("counter")[0]
    dynamic_place = BlockPlace(array, IRGet(BlockPlace(A, 1)))
    entry = BasicBlock(
        statements=[
            *(IRSet(array[i], IRConst(0)) for i in range(4)),
            *_initialize(places),
            IRSet(counter, IRConst(0)),
        ]
    )
    head = BasicBlock(test=IRPureInstr(Op.Less, [IRGet(counter), IRGet(BlockPlace(A, 0))]))
    body = BasicBlock(
        statements=[
            *(IRSet(array[i], IRGet(place)) for i, place in enumerate(places)),
            IRSet(dynamic_place, IRPureInstr(Op.Add, [IRGet(dynamic_place), IRGet(counter)])),
            *(IRSet(place, IRPureInstr(Op.Add, [IRGet(place), IRConst(i + 1)])) for i, place in enumerate(places)),
            IRSet(counter, IRPureInstr(Op.Add, [IRGet(counter), IRConst(1)])),
        ]
    )
    exit_block = BasicBlock(
        statements=[
            *(IRSet(BlockPlace(B, i), IRGet(array[i])) for i in range(4)),
            IRInstr(Op.DebugLog, [_sum(places)]),
        ]
    )
    entry.connect_to(head, None)
    head.connect_to(exit_block, 0)
    head.connect_to(body, None)
    body.connect_to(head, None)
    cfg = _opt(entry)
    result = _run(cfg, {A: [count, index]})
    expected = [0] * 4 if count == 0 else [(11 + count - 1) * (i + 1) for i in range(4)]
    if count:
        expected[index] += count - 1
    assert result.blocks[B] == expected
    assert result.log == [110 + 10 * count]


def test_conflicting_copy_orders_are_deterministic_and_preserve_values():
    def make():
        a, b, c = [TempBlock(name)[0] for name in ("a", "b", "c")]
        return BasicBlock(
            statements=[
                IRSet(a, IRConst(11)),
                IRSet(b, IRConst(22)),
                IRSet(c, IRConst(33)),
                IRSet(BlockPlace(B, 0), IRGet(a)),
                IRSet(BlockPlace(B, 1), IRGet(b)),
                IRInstr(Op.DebugLog, [IRGet(b)]),
                IRSet(BlockPlace(B, 2), IRGet(b)),
                IRSet(BlockPlace(B, 3), IRGet(c)),
                IRInstr(Op.DebugLog, [IRGet(c)]),
                IRSet(BlockPlace(B, 4), IRGet(c)),
                IRSet(BlockPlace(B, 5), IRGet(a)),
            ]
        )

    cfg = _opt(make())
    assert cfg_to_text(cfg) == cfg_to_text(_opt(make()))
    result = _run(cfg)
    assert result.blocks[B] == [11, 22, 22, 33, 33, 11]
    assert result.log == [22, 33]


def test_copy_group_preserves_source_dependencies():
    a, b, c, d = places = _scalars()
    cfg = _opt(
        BasicBlock(
            statements=[
                *_initialize(places),
                IRInstr(Op.DebugLog, [_sum(places)]),
                IRSet(b, IRGet(a)),
                IRSet(c, IRGet(b)),
                IRSet(d, IRGet(c)),
                *(IRSet(BlockPlace(B, i), IRGet(place)) for i, place in enumerate(places)),
            ]
        )
    )
    result = _run(cfg)
    assert result.blocks[B] == [11, 11, 11, 11]
    assert result.log == [110]


def test_copy_group_falls_back_when_only_baseline_fits():
    array = TempBlock("array", 4094)
    a, b, c = [TempBlock(name)[0] for name in ("a", "b", "c")]
    cfg = _opt(
        BasicBlock(
            statements=[
                IRSet(array[0], IRConst(7)),
                IRSet(array[4093], IRConst(9)),
                IRSet(a, IRConst(11)),
                IRSet(b, IRConst(22)),
                IRSet(BlockPlace(B, 0), IRGet(a)),
                IRSet(BlockPlace(B, 1), IRGet(b)),
                IRSet(c, IRConst(33)),
                IRSet(BlockPlace(B, 2), IRGet(b)),
                IRSet(BlockPlace(B, 3), IRGet(c)),
                IRInstr(Op.DebugLog, [_sum([array[0], array[4093]])]),
            ]
        )
    )
    assert _copy_lengths(cfg) == [2]
    stores = [stmt for stmt in cfg.statements if isinstance(stmt, IRSet) and stmt.place.block == B]
    assert len(stores) == 2
    assert all(isinstance(stmt.value, IRGet) for stmt in stores)
    result = _run(cfg)
    assert result.blocks[B] == [11, 22, 22, 33]
    assert result.log == [16]
    assert len(result.blocks[10000]) == 4096


def test_copy_gain_does_not_discard_larger_rmw_savings():
    a, b, x, y = [TempBlock(name)[0] for name in ("a", "b", "x", "y")]
    cfg = _opt(
        BasicBlock(
            statements=[
                IRSet(a, IRConst(10)),
                IRSet(b, IRConst(20)),
                IRSet(x, IRPureInstr(Op.Add, [IRGet(a), IRConst(1)])),
                IRSet(a, IRPureInstr(Op.Add, [IRGet(x), IRConst(1)])),
                IRSet(x, IRPureInstr(Op.Add, [IRGet(a), IRConst(1)])),
                IRSet(y, IRConst(30)),
                IRSet(BlockPlace(B, 0), IRGet(x)),
                IRSet(BlockPlace(B, 1), IRGet(y)),
                IRInstr(Op.DebugLog, [IRGet(b)]),
            ]
        )
    )
    increments = [stmt for stmt in cfg.statements if isinstance(stmt, IRInstr) and stmt.op == Op.IncrementPost]
    assert len(increments) == 3
    assert _copy_lengths(cfg) == []
    stores = [stmt for stmt in cfg.statements if isinstance(stmt, IRSet) and stmt.place.block == B]
    assert len(stores) == 2
    assert all(isinstance(stmt.value, IRGet) for stmt in stores)
    result = _run(cfg)
    assert result.blocks[B] == [13, 30]
    assert result.log == [20]


@pytest.mark.parametrize("source_block", [None, PlayBlock.EngineRom, A], ids=["zero", "rom", "memory"])
@pytest.mark.parametrize("count", [2, 3])
def test_zero_and_runtime_constant_copy_gains_use_folded_cost(source_block, count):
    a, b, x, y, z = [TempBlock(name)[0] for name in ("a", "b", "x", "y", "z")]
    targets = [x, y, z][:count]
    values = [IRConst(0) if source_block is None else IRGet(BlockPlace(source_block, i)) for i in range(count)]
    cfg = _opt(
        BasicBlock(
            statements=[
                IRSet(a, IRConst(10)),
                IRSet(b, IRConst(20)),
                IRSet(x, IRPureInstr(Op.Add, [IRGet(a), IRConst(2)])),
                IRInstr(Op.DebugLog, [IRGet(x)]),
                *starmap(IRSet, zip(targets, values, strict=True)),
                IRInstr(Op.DebugLog, [_sum([*targets, b])]),
            ]
        )
    )
    fused = [stmt for stmt in cfg.statements if isinstance(stmt, IRInstr) and stmt.op == Op.SetAdd]
    profitable = source_block == A and count == 3
    assert len(fused) == (0 if profitable else 1)
    assert _copy_lengths(cfg) == ([3] if profitable else [2] if count == 3 else [])
    initial = {A: [7, 9, 11]}
    if source_block == PlayBlock.EngineRom:
        initial[PlayBlock.EngineRom] = [7, 9, 11]
    result = _run(cfg, initial)
    assert result.log == [12, 20 if source_block is None else 20 + sum([7, 9, 11][:count])]


def test_equal_copy_and_rmw_savings_keep_baseline_allocation():
    a, b, c, d, x, y, z, w = [TempBlock(name)[0] for name in ("a", "b", "c", "d", "x", "y", "z", "w")]
    cfg = _opt(
        BasicBlock(
            statements=[
                IRSet(a, IRConst(10)),
                IRSet(b, IRConst(20)),
                IRSet(c, IRConst(30)),
                IRSet(d, IRConst(40)),
                IRSet(x, IRPureInstr(Op.Add, [IRGet(a), IRConst(2)])),
                IRInstr(Op.DebugLog, [IRGet(x)]),
                *(IRSet(place, IRConst(0)) for place in [x, y, z, w]),
                IRInstr(Op.DebugLog, [_sum([b, c, d, x, y, z, w])]),
            ]
        )
    )
    fused = [stmt for stmt in cfg.statements if isinstance(stmt, IRInstr) and stmt.op == Op.SetAdd]
    assert len(fused) == 1
    assert _copy_lengths(cfg) == [3]
    result = _run(cfg)
    assert result.log == [12, 90]


def test_moving_scalar_index_preserves_dynamic_rmw_fusion():
    a, b, x, y, index = [TempBlock(name)[0] for name in ("a", "b", "x", "y", "index")]
    destination = BlockPlace(B, IRGet(index))
    cfg = _opt(
        BasicBlock(
            statements=[
                IRSet(a, IRConst(10)),
                IRSet(b, IRConst(20)),
                IRSet(x, IRPureInstr(Op.Add, [IRGet(a), IRConst(2)])),
                IRInstr(Op.DebugLog, [IRGet(x)]),
                IRSet(x, IRGet(BlockPlace(A, 0))),
                IRSet(y, IRGet(BlockPlace(A, 1))),
                IRSet(index, IRGet(BlockPlace(A, 2))),
                IRSet(destination, IRPureInstr(Op.Add, [IRGet(destination), IRConst(1)])),
                IRInstr(Op.DebugLog, [_sum([x, y, index, b])]),
                IRInstr(Op.DebugLog, [IRGet(destination)]),
            ]
        )
    )
    assert _copy_lengths(cfg) == [3]
    increments = [stmt for stmt in cfg.statements if isinstance(stmt, IRInstr) and stmt.op == Op.IncrementPost]
    assert len(increments) == 1
    assert not any(isinstance(stmt, IRInstr) and stmt.op == Op.SetAdd for stmt in cfg.statements)
    result = _run(cfg, {A: [7, 6, 9], B: [100] * 10})
    assert result.blocks[B] == [100] * 9 + [101]
    assert result.log == [12, 42, 101]
