"""Stored-value reuse with observable evaluation order and memory effects."""

import sys

import pytest
from sonolus.backend._opt.lower import run_store_results  # ruff: ignore[import-private-name]

from sonolus.backend.blocks import PlayBlock
from sonolus.backend.interpret import Interpreter
from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet
from sonolus.backend.mode import Mode
from sonolus.backend.node import FunctionNode
from sonolus.backend.ops import Op
from sonolus.backend.optimize import (
    FAST_PASSES,
    MINIMAL_PASSES,
    STANDARD_PASSES,
    OptimizerConfig,
    cfg_to_engine_node,
    optimize_and_finalize,
    run_passes,
)
from sonolus.backend.optimize.flow import BasicBlock
from sonolus.backend.place import BlockPlace, TempBlock
from sonolus.script.internal.context import ReadOnlyMemory

A, B, C = 20, 21, 22


@pytest.fixture(scope="module")
def analyze_node():
    old_limit = sys.getrecursionlimit()
    try:
        from tools.metrics import analyze_node as analyze
    finally:
        sys.setrecursionlimit(old_limit)
    return analyze


def _run_node(node, initial=None):
    interpreter = Interpreter()
    interpreter.blocks[3000] = list(ReadOnlyMemory().values)
    for block, values in (initial or {}).items():
        interpreter.blocks[block] = list(values)
    interpreter.run(node)
    return interpreter


def _run(cfg, initial=None):
    return _run_node(cfg_to_engine_node(cfg), initial)


def _find(node, op):
    result = []
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, FunctionNode):
            if current.func == op:
                result.append(current)
            stack.extend(current.args)
    return result


def _memory_access(form):
    if form == "scalar":
        return "", [IRConst(A), IRConst(0)], 0
    if form == "shifted":
        return "Shifted", [IRConst(A), IRConst(2), IRConst(3), IRConst(4)], 14
    return "Pointed", [IRConst(B), IRConst(0), IRConst(1)], 3


@pytest.mark.parametrize("form", ["scalar", "shifted", "pointed"])
@pytest.mark.parametrize(
    ("family", "operand", "expected"),
    [
        ("Set", 5, 5),
        ("SetAdd", 3, 15),
        ("SetSubtract", 3, 9),
        ("SetMultiply", 3, 36),
        ("SetDivide", 3, 4),
        ("SetMod", 5, 2),
        ("SetRem", 5, 2),
        ("SetPower", 2, 144),
        ("IncrementPost", None, 13),
        ("DecrementPost", None, 11),
    ],
)
def test_store_variants_return_the_new_value_once(form, family, operand, expected):
    suffix, address, target = _memory_access(form)
    store_op = Op[family + suffix]
    store_args = [*address, *([] if operand is None else [IRConst(operand)])]
    cfg = run_store_results(
        BasicBlock(
            statements=[
                IRInstr(store_op, store_args),
                IRInstr(Op.DebugLog, [IRInstr(Op["Get" + suffix], address)]),
            ]
        )
    )
    node = cfg_to_engine_node(cfg)
    assert len(_find(node, store_op)) == 1
    if form != "pointed":
        assert len(cfg.statements) == 1
        assert _find(node, Op.DebugLog)[0].args[0].func == store_op
    initial = [-1] * 15
    initial[target] = 12
    result = _run_node(node, {A: initial, B: [A, 2]})
    expected_memory = [-1] * 15
    expected_memory[target] = expected
    assert result.blocks[A] == expected_memory
    assert result.blocks[B] == [A, 2]
    assert result.log == [expected]


@pytest.mark.parametrize("form", ["scalar", "shifted", "pointed"])
@pytest.mark.parametrize(("family", "expected"), [("IncrementPre", 13), ("DecrementPre", 11)])
def test_pre_variants_do_not_substitute_the_old_returned_value(form, family, expected):
    suffix, address, target = _memory_access(form)
    store_op = Op[family + suffix]
    cfg = run_store_results(
        BasicBlock(
            statements=[
                IRInstr(store_op, address),
                IRInstr(Op.DebugLog, [IRInstr(Op["Get" + suffix], address)]),
            ]
        )
    )
    assert len(_find(cfg_to_engine_node(cfg), store_op)) == 1
    initial = [-1] * 15
    initial[target] = 12
    result = _run(cfg, {A: initial, B: [A, 2]})
    assert result.get(A, target) == expected
    assert result.log == [expected]


def test_place_store_becomes_a_nested_set_value():
    cfg = run_store_results(
        BasicBlock(
            statements=[
                IRSet(BlockPlace(A, 0), IRGet(BlockPlace(B, 0))),
                IRInstr(Op.DebugLog, [IRPureInstr(Op.Add, [IRConst(2), IRGet(BlockPlace(A, 0))])]),
            ]
        )
    )
    assert len(cfg.statements) == 1
    node = cfg_to_engine_node(cfg)
    logged = _find(node, Op.DebugLog)[0].args[0]
    assert logged.func == Op.Add
    assert logged.args[1].func == Op.Set
    result = _run_node(node, {B: [7]})
    assert result.blocks[A] == [7]
    assert result.log == [9]


def test_bare_read_replaced_by_a_store_keeps_the_statement_root():
    cfg = run_store_results(
        BasicBlock(
            statements=[
                IRSet(BlockPlace(A, 0), IRConst(7)),
                IRInstr(Op.Get, [IRConst(A), IRConst(0)]),
            ]
        )
    )
    assert len(cfg.statements) == 1
    node = cfg_to_engine_node(cfg)
    assert len(_find(node, Op.Set)) == 1
    result = _run_node(node)
    assert result.blocks[A] == [7]
    assert result.log == []


@pytest.mark.parametrize("component", ["index", "block"])
def test_store_result_can_feed_the_next_store_address(component):
    if component == "index":
        value = 1
        destination = BlockPlace(B, IRGet(BlockPlace(A, 0)))
        expected = [-1, 7]
    else:
        value = B
        destination = BlockPlace(IRGet(BlockPlace(A, 0)), 0)
        expected = [7, -1]
    cfg = run_store_results(
        BasicBlock(statements=[IRSet(BlockPlace(A, 0), IRConst(value)), IRSet(destination, IRConst(7))])
    )
    assert len(cfg.statements) == 1
    node = cfg_to_engine_node(cfg)
    assert len(_find(node, Op.Set)) == 2
    result = _run_node(node, {B: [-1, -1]})
    assert result.blocks[A] == [value]
    assert result.blocks[B] == expected


@pytest.mark.parametrize("component", ["index", "block"])
@pytest.mark.parametrize("level", [MINIMAL_PASSES, FAST_PASSES, STANDARD_PASSES])
def test_nested_address_store_precedes_value_effects_after_reoptimization(component, level):
    if component == "index":
        initial_value = 1
        destination = BlockPlace(B, IRGet(BlockPlace(A, 0)))
        expected = [-1, 7]
    else:
        initial_value = B
        destination = BlockPlace(IRGet(BlockPlace(A, 0)), 0)
        expected = [7, -1]
    cfg = run_store_results(
        BasicBlock(
            statements=[
                IRSet(BlockPlace(A, 0), IRGet(BlockPlace(C, 0))),
                IRSet(destination, IRInstr(Op.Set, [IRConst(C), IRConst(0), IRConst(7)])),
            ]
        )
    )
    assert len(cfg.statements) == 1
    direct = optimize_and_finalize(cfg, level)
    exported = cfg_to_engine_node(run_passes(cfg, level))
    assert direct == exported
    for node in (cfg_to_engine_node(cfg), direct, exported):
        result = _run_node(node, {B: [-1, -1], C: [initial_value]})
        assert result.blocks[A] == [initial_value]
        assert result.blocks[B] == expected
        assert result.blocks[C] == [7]


@pytest.mark.parametrize("shifted", [False, True])
def test_dynamic_index_in_a_distinct_block_can_be_reused(shifted):
    index = IRGet(BlockPlace(B, 0))
    place = BlockPlace(A, IRPureInstr(Op.Multiply, [index, IRConst(3)]), 2) if shifted else BlockPlace(A, index)
    cfg = run_store_results(BasicBlock(statements=[IRSet(place, IRConst(7)), IRInstr(Op.DebugLog, [IRGet(place)])]))
    assert len(cfg.statements) == 1
    result = _run(cfg, {A: [-1] * 9, B: [2]})
    expected = [-1] * 9
    expected[8 if shifted else 2] = 7
    assert result.blocks[A] == expected
    assert result.blocks[B] == [2]
    assert result.log == [7]


def test_multiple_reads_do_not_duplicate_an_increment():
    cfg = run_store_results(
        BasicBlock(
            statements=[
                IRInstr(Op.IncrementPost, [IRConst(A), IRConst(0)]),
                IRInstr(Op.DebugLog, [IRPureInstr(Op.Add, [IRGet(BlockPlace(A, 0)), IRGet(BlockPlace(A, 0))])]),
            ]
        )
    )
    node = cfg_to_engine_node(cfg)
    assert len(_find(node, Op.IncrementPost)) == 1
    assert len(cfg.statements) == 1
    result = _run_node(node, {A: [10]})
    assert result.blocks[A] == [11]
    assert result.log == [22]


def test_store_chain_nests_in_evaluation_order():
    cfg = run_store_results(
        BasicBlock(
            statements=[
                IRSet(BlockPlace(A, 0), IRInstr(Op.IncrementPost, [IRConst(B), IRConst(0)])),
                IRSet(BlockPlace(C, 0), IRPureInstr(Op.Add, [IRGet(BlockPlace(A, 0)), IRConst(2)])),
                IRInstr(Op.DebugLog, [IRGet(BlockPlace(C, 0))]),
            ]
        )
    )
    assert len(cfg.statements) == 1
    node = cfg_to_engine_node(cfg)
    assert len(_find(node, Op.Set)) == 2
    assert len(_find(node, Op.IncrementPost)) == 1
    result = _run_node(node, {B: [10]})
    assert result.blocks[A] == [11]
    assert result.blocks[B] == [11]
    assert result.blocks[C] == [13]
    assert result.log == [13]


def test_long_store_chain_keeps_emission_depth_bounded():
    count = 1200
    cfg = run_store_results(
        BasicBlock(
            statements=[
                IRSet(BlockPlace(A, 0), IRGet(BlockPlace(B, 0))),
                *(
                    IRSet(BlockPlace(A, i), IRPureInstr(Op.Add, [IRGet(BlockPlace(A, i - 1)), IRConst(1)]))
                    for i in range(1, count)
                ),
                IRInstr(Op.DebugLog, [IRGet(BlockPlace(A, count - 1))]),
            ]
        )
    )
    assert len(cfg.statements) > 1
    node = cfg_to_engine_node(cfg)
    stack = [(node, 1)]
    while stack:
        current, depth = stack.pop()
        assert depth < count
        if isinstance(current, FunctionNode):
            stack.extend((arg, depth + 1) for arg in current.args)
    # The Python oracle uses several stack frames per engine node.
    old_limit = sys.getrecursionlimit()
    try:
        sys.setrecursionlimit(max(old_limit, 10000))
        result = _run_node(node, {B: [2]})
    finally:
        sys.setrecursionlimit(old_limit)
    assert result.blocks[A] == list(range(2, count + 2))
    assert result.log == [count + 1]


@pytest.mark.parametrize("nested_barrier", [False, True])
def test_side_effects_before_the_read_remain_in_order(nested_barrier):
    store = IRSet(BlockPlace(A, 0), IRInstr(Op.DebugLog, [IRConst(7)]))
    barrier = IRInstr(Op.DebugLog, [IRConst(99)])
    if nested_barrier:
        statements = [store, IRInstr(Op.DebugLog, [IRPureInstr(Op.Add, [barrier, IRGet(BlockPlace(A, 0))])])]
    else:
        statements = [store, barrier, IRInstr(Op.DebugLog, [IRGet(BlockPlace(A, 0))])]
    cfg = run_store_results(BasicBlock(statements=statements))
    result = _run(cfg)
    assert result.blocks[A] == [0]
    assert result.log == [7, 99, 0]


def test_store_value_effect_precedes_an_earlier_consumer_read():
    cfg = run_store_results(
        BasicBlock(
            statements=[
                IRSet(BlockPlace(A, 0), IRInstr(Op.IncrementPost, [IRConst(B), IRConst(0)])),
                IRInstr(Op.DebugLog, [IRPureInstr(Op.Add, [IRGet(BlockPlace(B, 0)), IRGet(BlockPlace(A, 0))])]),
            ]
        )
    )
    result = _run(cfg, {B: [7]})
    assert result.blocks[A] == [8]
    assert result.blocks[B] == [8]
    assert result.log == [16]


@pytest.mark.parametrize("form", ["index", "block", "shifted", "pointed"])
def test_store_that_changes_its_address_is_not_substituted(form):
    if form == "index":
        place = BlockPlace(A, IRGet(BlockPlace(A, 0)))
        store, read = IRSet(place, IRConst(1)), IRGet(place)
        initial = {A: [0, 99]}
        expected = {A: [1, 99]}
    elif form == "block":
        place = BlockPlace(IRGet(BlockPlace(A, 0)), 0)
        store, read = IRSet(place, IRConst(B)), IRGet(place)
        initial = {A: [A], B: [99]}
        expected = {A: [B], B: [99]}
    elif form == "shifted":
        address = [IRConst(A), IRConst(0), IRGet(BlockPlace(A, 0)), IRConst(1)]
        store, read = IRInstr(Op.SetShifted, [*address, IRConst(1)]), IRInstr(Op.GetShifted, address)
        initial = {A: [0, 99]}
        expected = {A: [1, 99]}
    else:
        address = [IRConst(B), IRConst(0), IRConst(0)]
        store, read = IRInstr(Op.SetPointed, [*address, IRConst(A)]), IRInstr(Op.GetPointed, address)
        initial = {A: [99], B: [B, 0]}
        expected = {A: [99], B: [A, 0]}
    cfg = run_store_results(BasicBlock(statements=[store, IRInstr(Op.DebugLog, [read])]))
    assert len(cfg.statements) == 2
    result = _run(cfg, initial)
    for block, values in expected.items():
        assert result.blocks[block] == values
    assert result.log == [99]


def test_store_value_that_changes_an_index_keeps_the_following_read():
    place = BlockPlace(A, IRGet(BlockPlace(B, 0)))
    cfg = run_store_results(
        BasicBlock(
            statements=[
                IRSet(place, IRInstr(Op.IncrementPost, [IRConst(B), IRConst(0)])),
                IRInstr(Op.DebugLog, [IRGet(place)]),
            ]
        )
    )
    assert len(cfg.statements) == 2
    result = _run(cfg, {A: [0, 99], B: [0]})
    assert result.blocks[A] == [1, 99]
    assert result.blocks[B] == [1]
    assert result.log == [99]


def test_copy_result_is_not_the_value_of_its_first_destination():
    cfg = run_store_results(
        BasicBlock(
            statements=[
                IRInstr(Op.Copy, [IRConst(A), IRConst(0), IRConst(B), IRConst(0), IRConst(2)]),
                IRInstr(Op.DebugLog, [IRGet(BlockPlace(B, 0))]),
            ]
        )
    )
    assert len(cfg.statements) == 2
    result = _run(cfg, {A: [7, 9]})
    assert result.blocks[B] == [7, 9]
    assert result.log == [7]


@pytest.mark.parametrize("op", [Op.If, Op.And, Op.Or])
@pytest.mark.parametrize("choice", [0, 1])
def test_conditionally_evaluated_read_does_not_control_the_store(op, choice):
    args = [IRGet(BlockPlace(B, 0)), IRGet(BlockPlace(A, 0))]
    if op == Op.If:
        args.append(IRConst(-1))
    cfg = run_store_results(
        BasicBlock(
            statements=[
                IRSet(BlockPlace(A, 0), IRConst(7)),
                IRInstr(Op.DebugLog, [IRPureInstr(op, args)]),
            ]
        )
    )
    assert len(cfg.statements) == 2
    result = _run(cfg, {B: [choice]})
    assert result.blocks[A] == [7]
    if op == Op.If:
        expected = 7 if choice else -1
    elif op == Op.And:
        expected = 7 if choice else 0
    else:
        expected = 1 if choice else 7
    assert result.log == [expected]


@pytest.mark.parametrize("value", [0, 4])
def test_store_can_feed_an_always_evaluated_branch_test(value):
    entry = BasicBlock(statements=[IRSet(BlockPlace(A, 0), IRGet(BlockPlace(B, 0)))], test=IRGet(BlockPlace(A, 0)))
    entry.connect_to(BasicBlock(statements=[IRInstr(Op.DebugLog, [IRConst(-1)])]), 0)
    entry.connect_to(BasicBlock(statements=[IRInstr(Op.DebugLog, [IRConst(1)])]), None)
    cfg = run_store_results(entry)
    assert cfg.statements == []
    result = _run(cfg, {B: [value]})
    assert result.blocks[A] == [value]
    assert result.log == [-1 if value == 0 else 1]


@pytest.mark.parametrize("has_successor", [False, True])
def test_unconditional_block_does_not_move_store_into_unused_test(has_successor):
    entry = BasicBlock(statements=[IRSet(BlockPlace(A, 0), IRConst(9))], test=IRGet(BlockPlace(A, 0)))
    if has_successor:
        entry.connect_to(BasicBlock(statements=[IRInstr(Op.DebugLog, [IRGet(BlockPlace(A, 0))])]), None)
    cfg = run_store_results(entry)
    assert len(cfg.statements) == 1
    result = _run(cfg)
    assert result.blocks[A] == [9]
    assert result.log == ([9] if has_successor else [])


@pytest.mark.parametrize("choice", [0, 1])
def test_store_before_a_branch_runs_on_both_paths(choice):
    entry = BasicBlock(statements=[IRInstr(Op.IncrementPost, [IRConst(A), IRConst(0)])], test=IRGet(BlockPlace(B, 0)))
    left = BasicBlock(statements=[IRInstr(Op.DebugLog, [IRGet(BlockPlace(A, 0))])])
    right = BasicBlock(statements=[IRInstr(Op.DebugLog, [IRConst(-1)])])
    join = BasicBlock(statements=[IRInstr(Op.DebugLog, [IRGet(BlockPlace(A, 0))])])
    entry.connect_to(left, 0)
    entry.connect_to(right, None)
    left.connect_to(join, None)
    right.connect_to(join, None)
    cfg = run_store_results(entry)
    result = _run(cfg, {A: [10], B: [choice]})
    assert result.blocks[A] == [11]
    assert result.log == ([11, 11] if choice == 0 else [-1, 11])


@pytest.mark.parametrize("level", [MINIMAL_PASSES, FAST_PASSES, STANDARD_PASSES])
@pytest.mark.parametrize(
    ("op", "operand", "stored", "returned"),
    [(Op.SetAdd, 3, 13, 13), (Op.IncrementPost, None, 11, 11), (Op.IncrementPre, None, 11, 10)],
)
def test_already_used_store_result_is_not_executed_again(level, op, operand, stored, returned):
    saved = TempBlock("saved")[0]
    args = [IRConst(A), IRConst(0), *([] if operand is None else [IRConst(operand)])]
    cfg = run_passes(
        BasicBlock(
            statements=[
                IRSet(saved, IRInstr(op, args)),
                IRInstr(Op.DebugLog, [IRGet(BlockPlace(A, 0))]),
                IRInstr(Op.DebugLog, [IRGet(saved)]),
            ]
        ),
        level,
    )
    node = cfg_to_engine_node(cfg)
    assert len(_find(node, op)) == 1
    result = _run_node(node, {A: [10]})
    assert result.blocks[A] == [stored]
    assert result.log == [stored, returned]


@pytest.mark.parametrize("level", [MINIMAL_PASSES, FAST_PASSES, STANDARD_PASSES])
def test_full_pipeline_emission_agrees_for_a_nested_rmw_result(level):
    cfg = BasicBlock(
        statements=[
            IRSet(BlockPlace(A, 0), IRGet(BlockPlace(B, 0))),
            IRSet(BlockPlace(A, 0), IRPureInstr(Op.Add, [IRGet(BlockPlace(A, 0)), IRConst(2)])),
            IRInstr(Op.DebugLog, [IRGet(BlockPlace(A, 0))]),
        ]
    )
    direct = optimize_and_finalize(cfg, level)
    exported = cfg_to_engine_node(run_passes(cfg, level))
    assert direct == exported
    if level in {FAST_PASSES, STANDARD_PASSES}:
        assert _find(direct, Op.DebugLog)[0].args[0].func == Op.SetAdd
    for node in (direct, exported):
        result = _run_node(node, {B: [7]})
        assert result.blocks[A] == [9]
        assert result.log == [9]


@pytest.mark.parametrize("op", [Op.Add, Op.Multiply, Op.Mod, Op.Rem])
@pytest.mark.parametrize("runtime_constant_left", [False, True])
def test_reported_savings_match_emitted_cost_for_flattened_indices(analyze_node, op, runtime_constant_left):
    if runtime_constant_left:
        left = IRPureInstr(op, [IRGet(BlockPlace(PlayBlock.LevelData, 0)), IRConst(2)])
        index = IRPureInstr(op, [left, IRGet(BlockPlace(B, 0))])
    else:
        left = IRPureInstr(op, [IRGet(BlockPlace(B, 0)), IRConst(2)])
        index = IRPureInstr(op, [left, IRConst(3)])
    place = BlockPlace(PlayBlock.LevelMemory, index)
    original = BasicBlock(statements=[IRSet(place, IRConst(7)), IRInstr(Op.DebugLog, [IRGet(place)])])
    config = OptimizerConfig(Mode.PLAY, "updateParallel")
    cfg, saved = run_store_results(original, config.mode, config.callback, counted=True)
    before = analyze_node(cfg_to_engine_node(original, config), config.mode, config.callback)
    after = analyze_node(cfg_to_engine_node(cfg, config), config.mode, config.callback)
    assert saved > 0
    assert saved == before["effective_node_count"] - after["effective_node_count"]


@pytest.mark.parametrize("raw_shifted", [False, True])
@pytest.mark.parametrize("op", [Op.Add, Op.Multiply, Op.Subtract])
@pytest.mark.parametrize("runtime_constant_index", [False, True])
def test_reported_savings_match_emitted_cost_for_offset_indices(analyze_node, raw_shifted, op, runtime_constant_index):
    source = PlayBlock.LevelData if runtime_constant_index else B
    index = IRPureInstr(op, [IRGet(BlockPlace(source, 0)), IRConst(2)])
    if raw_shifted:
        address = [IRConst(PlayBlock.LevelMemory), IRConst(3), index, IRConst(2)]
        store = IRInstr(Op.SetShifted, [*address, IRConst(7)])
        read = IRInstr(Op.GetShifted, address)
    else:
        place = BlockPlace(PlayBlock.LevelMemory, index, 3)
        store, read = IRSet(place, IRConst(7)), IRGet(place)
    original = BasicBlock(statements=[store, IRInstr(Op.DebugLog, [read])])
    config = OptimizerConfig(Mode.PLAY, "updateParallel")
    cfg, saved = run_store_results(original, config.mode, config.callback, counted=True)
    before = analyze_node(cfg_to_engine_node(original, config), config.mode, config.callback)
    after = analyze_node(cfg_to_engine_node(cfg, config), config.mode, config.callback)
    assert saved > 0
    assert saved == before["effective_node_count"] - after["effective_node_count"]


def test_store_and_consumer_random_draws_keep_their_order(monkeypatch):
    draws = iter([0.25, 0.75])
    monkeypatch.setattr("random.uniform", lambda *_: next(draws))
    cfg = run_store_results(
        BasicBlock(
            statements=[
                IRSet(BlockPlace(A, 0), IRInstr(Op.Random, [IRConst(0), IRConst(1)])),
                IRInstr(
                    Op.DebugLog,
                    [
                        IRPureInstr(
                            Op.Subtract,
                            [IRInstr(Op.Random, [IRConst(0), IRConst(1)]), IRGet(BlockPlace(A, 0))],
                        )
                    ],
                ),
            ]
        )
    )
    result = _run(cfg)
    assert result.blocks[A] == [0.25]
    assert result.log == [0.5]
    assert next(draws, None) is None


@pytest.mark.parametrize(("op", "function"), [(Op.Random, "uniform"), (Op.RandomInteger, "randrange")])
def test_random_address_is_evaluated_again_for_the_read(monkeypatch, op, function):
    draws = iter([0, 1])
    monkeypatch.setattr(f"sonolus.backend.interpret.random.{function}", lambda *_: next(draws))
    place = BlockPlace(A, IRInstr(op, [IRConst(0), IRConst(2)]))
    cfg = run_store_results(BasicBlock(statements=[IRSet(place, IRConst(7)), IRInstr(Op.DebugLog, [IRGet(place)])]))
    assert len(cfg.statements) == 2
    result = _run(cfg, {A: [0, 99]})
    assert result.blocks[A] == [7, 99]
    assert result.log == [99]
    assert next(draws, None) is None


def test_entity_array_alias_read_observes_the_preceding_store():
    target = PlayBlock.EntitySharedMemory
    alias = PlayBlock.EntitySharedMemoryArray
    cfg = run_store_results(
        BasicBlock(
            statements=[
                IRSet(BlockPlace(target, 0), IRConst(7)),
                IRInstr(
                    Op.DebugLog, [IRPureInstr(Op.Add, [IRGet(BlockPlace(alias, 0)), IRGet(BlockPlace(target, 0))])]
                ),
            ]
        )
    )
    assert len(cfg.statements) == 2
    shared = [3]
    result = Interpreter()
    result.blocks[target] = shared
    result.blocks[alias] = shared
    result.run(cfg_to_engine_node(cfg))
    assert shared == [7]
    assert result.log == [14]


def test_addresses_beyond_f32_precision_are_not_proven_disjoint():
    cfg = run_store_results(
        BasicBlock(
            statements=[
                IRSet(BlockPlace(A, 2**24), IRConst(7)),
                IRInstr(
                    Op.DebugLog,
                    [IRPureInstr(Op.Add, [IRGet(BlockPlace(A, 2**24 + 1)), IRGet(BlockPlace(A, 2**24))])],
                ),
            ]
        )
    )
    assert len(cfg.statements) == 2
    assert len(_find(cfg_to_engine_node(cfg), Op.Set)) == 1


@pytest.mark.parametrize(
    ("offset", "index", "stride", "aliased_index"),
    [(-(2**24), 4097, 4097, 8192), (1, 4096, 4096, 2**24)],
)
def test_shifted_addresses_with_rounded_products_or_sums_are_not_proven_disjoint(offset, index, stride, aliased_index):
    f32 = pytest.importorskip("numpy").float32
    assert offset + index * stride != aliased_index
    assert f32(f32(offset) + f32(f32(index) * f32(stride))) == aliased_index
    address = [IRConst(A), IRConst(offset), IRConst(index), IRConst(stride)]
    original = BasicBlock(
        statements=[
            IRInstr(Op.SetShifted, [*address, IRConst(7)]),
            IRInstr(
                Op.DebugLog,
                [IRPureInstr(Op.Add, [IRGet(BlockPlace(A, aliased_index)), IRInstr(Op.GetShifted, address)])],
            ),
        ]
    )
    cfg, saved = run_store_results(original, counted=True)
    assert saved == 0
    assert cfg_to_engine_node(cfg) == cfg_to_engine_node(original)
