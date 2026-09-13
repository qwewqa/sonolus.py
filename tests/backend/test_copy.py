"""Post-allocation memory-copy fusion and its observable memory semantics."""

from itertools import permutations

import pytest
from sonolus.backend._opt.ir import debug_run  # ruff: ignore[import-private-name]

from sonolus.backend.blocks import PlayBlock, PreviewBlock
from sonolus.backend.interpret import Interpreter
from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet
from sonolus.backend.ops import Op
from sonolus.backend.optimize import (
    FAST_PASSES,
    MINIMAL_PASSES,
    STANDARD_PASSES,
    OptimizerConfig,
    cfg_to_engine_node,
    run_passes,
)
from sonolus.backend.optimize.flow import BasicBlock, traverse_cfg_preorder
from sonolus.backend.place import BlockPlace, TempBlock
from sonolus.script.internal.context import ReadOnlyMemory

A, B = 20, 21


def _copies(cfg):
    return [stmt for stmt in cfg.statements if isinstance(stmt, IRInstr) and stmt.op == Op.Copy]


def _counts(cfg):
    return [stmt.args[4].value for stmt in _copies(cfg)]


def _opt(statements, **kwargs):
    return debug_run(BasicBlock(statements=statements), phases=["bump", "copy"], **kwargs)


def _run(cfg, initial=None):
    interpreter = Interpreter()
    interpreter.blocks[3000] = list(ReadOnlyMemory().values)
    for block, values in (initial or {}).items():
        interpreter.blocks[block] = list(values)
    interpreter.run(cfg_to_engine_node(cfg))
    return interpreter


@pytest.mark.parametrize("count", [1, 2, 9])
def test_contiguous_memory_copies(count):
    cfg = _opt([IRSet(BlockPlace(B, 10 + i), IRGet(BlockPlace(A, 3 + i))) for i in range(count)])
    assert _counts(cfg) == ([count] if count > 1 else [])
    for copy in _copies(cfg):
        assert [arg.value for arg in copy.args] == [A, 3, B, 10, count]
    result = _run(cfg, {A: list(range(30)), B: [-1] * 30})
    assert [result.get(B, i) for i in range(9, 11 + count)] == [-1, *range(3, 3 + count), -1]


def test_single_runtime_memory_copy_stays_a_set():
    cfg = _opt([IRSet(BlockPlace(B, 0), IRGet(BlockPlace(A, 1)))])
    assert _counts(cfg) == []
    assert len(cfg.statements) == 1
    assert isinstance(cfg.statements[0], IRSet)
    assert isinstance(cfg.statements[0].value, IRGet)
    result = _run(cfg, {A: [7, 13], B: [-1, -1]})
    assert [result.get(B, i) for i in range(2)] == [13, -1]


@pytest.mark.parametrize("order", [(0, 1, 2, 3), (3, 2, 1, 0), (2, 0, 3, 1), (0, 3, 2, 1)])
def test_contiguous_memory_copies_in_any_order(order):
    cfg = _opt([IRSet(BlockPlace(B, 10 + i), IRGet(BlockPlace(A, 3 + i))) for i in order])
    assert _counts(cfg) == [4]
    assert [arg.value for arg in _copies(cfg)[0].args] == [A, 3, B, 10, 4]
    result = _run(cfg, {A: list(range(20)), B: [-1] * 20})
    assert [result.get(B, i) for i in range(9, 15)] == [-1, 3, 4, 5, 6, -1]


def test_allocation_makes_array_copies_contiguous():
    source = TempBlock("source", 4)
    destination = TempBlock("destination", 4)
    cfg = _opt([IRSet(destination[i], IRGet(source[i])) for i in range(4)])
    assert _counts(cfg) == [4]
    assert [arg.value for arg in _copies(cfg)[0].args] == [10000, 0, 10000, 4, 4]


@pytest.mark.parametrize(("count", "expected"), [(1, []), (2, [2]), (4096, [4096]), (4097, [4096])])
def test_zero_runs_use_reserved_rom(count, expected):
    cfg = _opt([IRSet(BlockPlace(B, 10 + i), IRConst(0)) for i in range(count)])
    assert _counts(cfg) == expected
    if count == 1:
        assert isinstance(cfg.statements[0], IRSet)
    for copy in _copies(cfg):
        assert copy.args[0].value == 3000
        assert copy.args[2].value == B
    result = _run(cfg, {B: [-1] * (count + 12)})
    assert [result.get(B, i) for i in range(9, count + 11)] == [-1, *([0] * count), -1]


@pytest.mark.parametrize("order", [(3, 2, 1, 0), (2, 0, 3, 1)])
def test_zero_runs_in_any_order(order):
    cfg = _opt([IRSet(BlockPlace(B, 10 + i), IRConst(0)) for i in order])
    assert _counts(cfg) == [4]
    assert [arg.value for arg in _copies(cfg)[0].args][2:] == [B, 10, 4]
    result = _run(cfg, {B: [-1] * 16})
    assert [result.get(B, i) for i in range(9, 15)] == [-1, 0, 0, 0, 0, -1]


def test_reverse_zero_run_respects_reserved_rom_limit():
    cfg = _opt([IRSet(BlockPlace(B, 10 + i), IRConst(0)) for i in reversed(range(4097))])
    assert _counts(cfg) == [4096]
    assert [arg.value for arg in _copies(cfg)[0].args][2:] == [B, 11, 4096]
    result = _run(cfg, {B: [-1] * 4108})
    assert [result.get(B, i) for i in range(9, 4108)] == [-1, *([0] * 4097), -1]


@pytest.mark.parametrize(
    ("source", "destination", "counts", "expected"),
    [
        (0, 1, [], [1, 1, 1, 1, 5]),
        (1, 0, [3], [2, 3, 4, 4, 5]),
        (0, 3, [3], [1, 2, 3, 1, 2, 3]),
    ],
)
def test_overlap_preserves_sequential_store_semantics(source, destination, counts, expected):
    cfg = _opt([IRSet(BlockPlace(A, destination + i), IRGet(BlockPlace(A, source + i))) for i in range(3)])
    assert _counts(cfg) == counts
    result = _run(cfg, {A: [1, 2, 3, 4, 5, 6]})
    assert [result.get(A, i) for i in range(len(expected))] == expected


@pytest.mark.parametrize(
    ("source", "destination", "order", "counts", "expected"),
    [
        (0, 1, (3, 2, 1, 0), [4], [1, 1, 2, 3, 4, 6, 7]),
        (1, 0, (3, 2, 1, 0), [], [5, 5, 5, 5, 5, 6, 7]),
        (0, 2, (2, 0, 3, 1), [4], [1, 2, 1, 2, 3, 4, 7]),
        (0, 2, (0, 2, 1, 3), [2], [1, 2, 1, 2, 1, 2, 7]),
    ],
)
def test_unordered_overlap_preserves_sequential_store_semantics(source, destination, order, counts, expected):
    cfg = _opt([IRSet(BlockPlace(A, destination + i), IRGet(BlockPlace(A, source + i))) for i in order])
    assert _counts(cfg) == counts
    result = _run(cfg, {A: list(range(1, 8))})
    assert [result.get(A, i) for i in range(7)] == expected


@pytest.mark.parametrize("shift", [-3, -2, -1, 0, 1, 2, 3])
def test_copy_permutations_match_sequential_memory_updates(shift):
    for order in permutations(range(4)):
        initial = list(range(1, 13))
        expected = initial.copy()
        for i in order:
            expected[4 + shift + i] = expected[4 + i]
        cfg = _opt([IRSet(BlockPlace(A, 4 + shift + i), IRGet(BlockPlace(A, 4 + i))) for i in order])
        result = _run(cfg, {A: initial})
        assert [result.get(A, i) for i in range(12)] == expected, (shift, order)


@pytest.mark.parametrize(("source_offsets", "destination_offsets"), [([0, 2], [0, 1]), ([0, 1], [0, 2])])
def test_gaps_break_copy_runs(source_offsets, destination_offsets):
    cfg = _opt(
        [
            IRSet(BlockPlace(B, dst), IRGet(BlockPlace(A, src)))
            for src, dst in zip(source_offsets, destination_offsets, strict=True)
        ]
    )
    assert _counts(cfg) == []
    assert all(isinstance(stmt, IRSet) for stmt in cfg.statements)


@pytest.mark.parametrize(
    ("order", "counts"),
    [((2, 0, 1, 6, 4, 5), [3, 3]), ((1, 0, 4, 6, 5), [2, 3]), ((0, 1, 4, 5), [2, 2])],
)
def test_gaps_preserve_contiguous_prefix_and_suffix(order, counts):
    cfg = _opt([IRSet(BlockPlace(B, 10 + i), IRGet(BlockPlace(A, i))) for i in order])
    assert _counts(cfg) == counts
    result = _run(cfg, {A: list(range(8)), B: [-1] * 18})
    assert [result.get(B, 10 + i) for i in range(8)] == [i if i in order else -1 for i in range(8)]


def test_copy_source_and_destination_must_have_same_order():
    cfg = _opt([IRSet(BlockPlace(B, dst), IRGet(BlockPlace(A, src))) for src, dst in [(0, 1), (1, 0)]])
    assert _counts(cfg) == []
    assert all(isinstance(stmt, IRSet) for stmt in cfg.statements)
    result = _run(cfg, {A: [7, 8], B: [-1, -1]})
    assert [result.get(B, i) for i in range(2)] == [8, 7]


@pytest.mark.parametrize("zero", [False, True])
def test_duplicate_destination_splits_unordered_run(zero):
    cfg = _opt([IRSet(BlockPlace(B, i), IRConst(0) if zero else IRGet(BlockPlace(A, i))) for i in (1, 0, 1, 2)])
    assert _counts(cfg) == [2, 2]
    result = _run(cfg, {A: [7, 8, 9], B: [-1] * 4})
    assert [result.get(B, i) for i in range(4)] == ([0, 0, 0, -1] if zero else [7, 8, 9, -1])


@pytest.mark.parametrize(
    ("offsets", "counts"),
    [
        ([*range(0, 300, 3), 1000, 1001, 1002], [3]),
        ([0, 3, 6, 9, *range(100, 120)], [20]),
    ],
)
def test_sparse_copy_lookahead_preserves_ascending_tail_and_determinism(offsets, counts):
    def make():
        return _opt([IRSet(BlockPlace(B, i), IRGet(BlockPlace(A, i))) for i in offsets])

    cfg = make()
    assert _counts(cfg) == counts
    assert cfg_to_engine_node(cfg) == cfg_to_engine_node(make())
    result = _run(cfg, {A: list(range(1004)), B: [-1] * 1004})
    assert [result.get(B, i) for i in range(1004)] == [i if i in offsets else -1 for i in range(1004)]


def test_side_effect_between_copies_breaks_run():
    cfg = _opt(
        [
            IRSet(BlockPlace(B, 0), IRGet(BlockPlace(A, 0))),
            IRInstr(Op.DebugLog, [IRGet(BlockPlace(B, 0))]),
            IRSet(BlockPlace(B, 1), IRGet(BlockPlace(A, 1))),
        ]
    )
    assert _counts(cfg) == []
    result = _run(cfg, {A: [7, 8]})
    assert result.log == [7]
    assert [result.get(B, i) for i in range(2)] == [7, 8]


def test_side_effect_between_unordered_copies_breaks_run():
    cfg = _opt(
        [
            IRSet(BlockPlace(B, 1), IRGet(BlockPlace(A, 1))),
            IRSet(BlockPlace(B, 0), IRGet(BlockPlace(A, 0))),
            IRInstr(Op.DebugLog, [IRGet(BlockPlace(B, 2))]),
            IRSet(BlockPlace(B, 3), IRGet(BlockPlace(A, 3))),
            IRSet(BlockPlace(B, 2), IRGet(BlockPlace(A, 2))),
        ]
    )
    assert _counts(cfg) == [2, 2]
    result = _run(cfg, {A: [7, 8, 9, 10], B: [-1] * 4})
    assert result.log == [-1]
    assert [result.get(B, i) for i in range(4)] == [7, 8, 9, 10]


def test_zero_run_stops_at_nonzero_assignment():
    cfg = _opt([IRSet(BlockPlace(B, i), IRConst(value)) for i, value in enumerate([0, 0, 7, 0, 0])])
    assert _counts(cfg) == [2, 2]
    result = _run(cfg)
    assert [result.get(B, i) for i in range(5)] == [0, 0, 7, 0, 0]


def test_dynamic_address_is_not_merged():
    cfg = _opt([IRSet(BlockPlace(B, IRGet(BlockPlace(A, 0)), i), IRConst(0)) for i in range(2)])
    assert _counts(cfg) == []


def test_dynamic_address_observes_preceding_write():
    cfg = _opt([IRSet(BlockPlace(A, IRGet(BlockPlace(A, 0)), i), IRGet(BlockPlace(A, 3 + i))) for i in range(2)])
    assert _counts(cfg) == []
    result = _run(cfg, {A: [0, 5, 6, 1, 9]})
    assert [result.get(A, i) for i in range(5)] == [1, 5, 9, 1, 9]


def test_folded_negative_zero_is_not_replaced_by_positive_rom_zero():
    cfg = BasicBlock(statements=[IRSet(BlockPlace(B, i), IRPureInstr(Op.Negate, [IRConst(0)])) for i in range(2)])
    cfg = debug_run(cfg, phases=["ssa", "sccp", "lower", "bump", "copy"])
    assert _counts(cfg) == []
    assert len(cfg.statements) == 2
    assert all(isinstance(stmt, IRSet) for stmt in cfg.statements)


def test_copy_runs_do_not_cross_basic_blocks():
    first = BasicBlock(statements=[IRSet(BlockPlace(B, 0), IRGet(BlockPlace(A, 0)))])
    second = BasicBlock(statements=[IRSet(BlockPlace(B, 1), IRGet(BlockPlace(A, 1)))])
    first.connect_to(second, None)
    cfg = debug_run(first, phases=["bump", "copy"])
    assert _counts(cfg) == []
    assert isinstance(cfg.statements[0], IRSet)
    successor = next(iter(cfg.outgoing)).dst
    assert _counts(successor) == []
    assert isinstance(successor.statements[0], IRSet)


def test_runtime_constant_scalar_read_stays_a_set():
    cfg = _opt([IRSet(BlockPlace(B, 0), IRGet(BlockPlace(PlayBlock.EngineRom, 0)))])
    assert _counts(cfg) == []
    assert isinstance(cfg.statements[0], IRSet)


def test_runtime_constant_read_run_can_be_copied():
    cfg = _opt([IRSet(BlockPlace(B, i), IRGet(BlockPlace(PlayBlock.EngineRom, i))) for i in range(2)])
    assert _counts(cfg) == [2]


@pytest.mark.parametrize("block_type", [PlayBlock, PreviewBlock])
def test_entity_array_view_alias_does_not_merge(block_type):
    cfg = _opt(
        [
            IRSet(
                BlockPlace(block_type.EntitySharedMemory, i + 1),
                IRGet(BlockPlace(block_type.EntitySharedMemoryArray, i)),
            )
            for i in range(2)
        ]
    )
    assert _counts(cfg) == []
    assert all(isinstance(stmt, IRSet) for stmt in cfg.statements)


def test_indices_beyond_f32_integer_precision_are_not_merged():
    cfg = _opt([IRSet(BlockPlace(B, 2**24 + i), IRGet(BlockPlace(A, i))) for i in range(2)])
    assert _counts(cfg) == []


@pytest.mark.parametrize("level", [MINIMAL_PASSES, FAST_PASSES, STANDARD_PASSES])
def test_full_pipeline_memory_and_logs(level):
    cfg = BasicBlock(
        statements=[
            IRSet(BlockPlace(B, 0), IRGet(BlockPlace(A, 0))),
            IRSet(BlockPlace(B, 1), IRGet(BlockPlace(A, 1))),
            IRInstr(Op.DebugLog, [IRGet(BlockPlace(B, 1))]),
            IRSet(BlockPlace(B, 2), IRConst(0)),
            IRSet(BlockPlace(B, 3), IRConst(0)),
        ]
    )
    cfg = run_passes(cfg, level, OptimizerConfig())
    if level in {FAST_PASSES, STANDARD_PASSES}:
        assert _counts(cfg) == [2, 2]
    result = _run(cfg, {A: [17, 19], B: [-1] * 5})
    assert [result.get(B, i) for i in range(5)] == [17, 19, 0, 0, -1]
    assert result.log == [19]


@pytest.mark.parametrize("level", [FAST_PASSES, STANDARD_PASSES])
@pytest.mark.parametrize(
    ("first_test", "second_test", "expected"),
    [
        (4, 10, [4, 10]),
        (6, 13, [6, 13]),
        (8, 16, [8, 16]),
        (5, 11, [-1, -2]),
    ],
)
def test_copy_preserves_normalized_switch_tests(level, first_test, second_test, expected):
    entry = BasicBlock(
        statements=[IRSet(BlockPlace(B, i), IRGet(BlockPlace(A, i))) for i in range(2)],
        test=IRGet(BlockPlace(A, 2)),
    )
    second_switch = BasicBlock(
        statements=[IRSet(BlockPlace(B, i), IRConst(0)) for i in range(2, 4)],
        test=IRGet(BlockPlace(A, 3)),
    )
    exit_block = BasicBlock()
    for switch, cases, default, successor in [
        (entry, [4, 6, 8], -1, second_switch),
        (second_switch, [10, 13, 16], -2, exit_block),
    ]:
        for case in [*cases, None]:
            arm = BasicBlock(statements=[IRInstr(Op.DebugLog, [IRConst(default if case is None else case)])])
            switch.connect_to(arm, case)
            arm.connect_to(successor, None)
    cfg = run_passes(entry, level, OptimizerConfig())
    blocks = list(traverse_cfg_preorder(cfg))
    assert sorted(count for block in blocks for count in _counts(block)) == [2, 2]
    switches = [block for block in blocks if len(block.outgoing) == 4]
    assert len(switches) == 2
    for switch in switches:
        assert {edge.cond for edge in switch.outgoing} == {0, 1, 2, None}
        assert isinstance(switch.test, IRPureInstr)
        assert switch.test.op == Op.Divide
        assert switch.test.args[0].op == Op.Subtract
    result = _run(cfg, {A: [17, 19, first_test, second_test], B: [-1] * 5})
    assert result.log == expected
    assert [result.get(B, i) for i in range(5)] == [17, 19, 0, 0, -1]
