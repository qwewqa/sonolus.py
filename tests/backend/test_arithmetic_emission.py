"""Left-fold arithmetic emission with runtime-constant and effect boundaries."""

import operator
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
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

_LEVELS = [MINIMAL_PASSES, FAST_PASSES, STANDARD_PASSES]
_LEFT_FOLDS = [Op.Add, Op.Subtract, Op.Multiply, Op.Divide, Op.Power, Op.Mod, Op.Rem]
_VARIABLE_TAIL_FOLDS = [Op.Add, Op.Subtract, Op.Multiply, Op.Mod, Op.Rem]
_NEW_LEFT_FOLDS = [Op.Subtract, Op.Divide, Op.Power]
_BINARY = {Op.Subtract: operator.sub, Op.Divide: operator.truediv, Op.Power: operator.pow}
_INPUT, _OUTPUT = 500, 501


@pytest.fixture(scope="module")
def analyze_node():
    old_limit = sys.getrecursionlimit()
    try:
        from tools.metrics import analyze_node as analyze
    finally:
        sys.setrecursionlimit(old_limit)
    return analyze


def _read(offset, block=_INPUT):
    return IRGet(BlockPlace(block, offset))


def _store(offset, value):
    return IRInstr(Op.Set, [IRConst(_OUTPUT), IRConst(offset), value])


def _log_cfg(value):
    return BasicBlock(statements=[IRInstr(Op.DebugLog, [value])])


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


def _both_paths(cfg, level, config=None):
    direct = optimize_and_finalize(cfg, level, config)
    exported = cfg_to_engine_node(run_passes(cfg, level, config), config)
    assert direct == exported
    return direct, exported


class _F32Interpreter(Interpreter):
    def run(self, node):
        if isinstance(node, FunctionNode) and node.func in _BINARY:
            values = [np.float32(self.run(arg)) for arg in node.args]
            value = values[0]
            for other in values[1:]:
                value = np.float32(_BINARY[node.func](value, other))
            return value
        return super().run(node)


@pytest.mark.parametrize("level", _LEVELS)
@pytest.mark.parametrize(
    ("op", "values", "expected"),
    [
        (Op.Subtract, [2**24, -1, 2**24], np.float32(0)),
        (Op.Divide, [1, 3, 7], np.float32(np.float32(1) / np.float32(3)) / np.float32(7)),
        (
            Op.Power,
            [1 + 2**-23, 3, 5],
            np.float32(np.float32(1 + 2**-23) ** np.float32(3)) ** np.float32(5),
        ),
    ],
)
def test_left_chain_preserves_each_f32_rounding_step(level, op, values, expected):
    expression = IRPureInstr(op, [IRPureInstr(op, [_read(0), IRConst(values[1])]), IRConst(values[2])])
    for node in _both_paths(_log_cfg(expression), level):
        arithmetic = _find(node, Op.DebugLog)[0].args[0]
        assert arithmetic == FunctionNode(op, (FunctionNode(Op.Get, (_INPUT, 0)), *values[1:]))
        interpreter = _F32Interpreter()
        interpreter.blocks[_INPUT] = values
        interpreter.run(node)
        assert np.float32(interpreter.log[0]).view(np.uint32) == expected.view(np.uint32)


@pytest.mark.parametrize("level", _LEVELS)
@pytest.mark.parametrize(
    ("op", "values", "expected"),
    [(Op.Subtract, [20, 8, 2], 14), (Op.Divide, [24, 6, 2], 8), (Op.Power, [2, 3, 2], 512)],
)
def test_right_nested_arithmetic_keeps_its_grouping(level, op, values, expected):
    expression = IRPureInstr(op, [_read(0), IRPureInstr(op, [_read(1), _read(2)])])
    for node in _both_paths(_log_cfg(expression), level):
        arithmetic = _find(node, Op.DebugLog)[0].args[0]
        assert arithmetic.func == op
        assert len(arithmetic.args) == 2
        assert arithmetic.args[1].func == op
        interpreter = Interpreter()
        interpreter.blocks[_INPUT] = values
        interpreter.run(node)
        assert interpreter.log == [expected]


@pytest.mark.parametrize("level", _LEVELS)
@pytest.mark.parametrize("op", _NEW_LEFT_FOLDS)
def test_nary_input_and_left_nested_input_emit_identically(level, op):
    nested = IRPureInstr(op, [IRPureInstr(op, [_read(0), IRConst(2)]), IRConst(3)])
    nary = IRPureInstr(op, [_read(0), IRConst(2), IRConst(3)])
    assert _both_paths(_log_cfg(nested), level)[0] == _both_paths(_log_cfg(nary), level)[0]


@pytest.mark.parametrize(
    ("op", "expected"),
    [(Op.Add, 3), (Op.Subtract, -3), (Op.Multiply, 0), (Op.Divide, 0), (Op.Power, 0), (Op.Mod, 0), (Op.Rem, 0)],
)
def test_empty_left_fold_is_not_absorbed_into_its_parent(op, expected):
    expression = IRPureInstr(op, [IRPureInstr(op, []), IRConst(3)])
    node = cfg_to_engine_node(_log_cfg(expression))
    assert _find(node, Op.DebugLog)[0].args[0] == FunctionNode(op, (FunctionNode(op, ()), 3))
    interpreter = Interpreter()
    interpreter.run(node)
    assert interpreter.log == [expected]


@pytest.mark.parametrize("level", _LEVELS)
@pytest.mark.parametrize("op", _VARIABLE_TAIL_FOLDS)
@pytest.mark.parametrize("nary_input", [False, True])
def test_runtime_constant_prefix_stays_a_single_foldable_group(level, op, nary_input):
    inputs = [_read(index, PlayBlock.LevelData) for index in range(3)]
    if nary_input:
        expression = IRPureInstr(op, [*inputs, _read(0), _read(1)])
    else:
        expression = IRPureInstr(op, [IRPureInstr(op, inputs[:2]), inputs[2]])
        expression = IRPureInstr(op, [IRPureInstr(op, [expression, _read(0)]), _read(1)])
    config = OptimizerConfig(Mode.PLAY, "updateParallel")
    for node in _both_paths(_log_cfg(expression), level, config):
        arithmetic = _find(node, Op.DebugLog)[0].args[0]
        assert arithmetic.func == op
        assert len(arithmetic.args) == 3
        assert arithmetic.args[0] == FunctionNode(
            op, tuple(FunctionNode(Op.Get, (PlayBlock.LevelData, index)) for index in range(3))
        )
        assert arithmetic.args[1:] == (FunctionNode(Op.Get, (_INPUT, 0)), FunctionNode(Op.Get, (_INPUT, 1)))


@pytest.mark.parametrize("level", _LEVELS)
@pytest.mark.parametrize("op", _VARIABLE_TAIL_FOLDS)
def test_writable_callback_does_not_preserve_a_runtime_constant_group(level, op):
    prefix = IRPureInstr(op, [_read(0, PlayBlock.LevelData), _read(1, PlayBlock.LevelData)])
    config = OptimizerConfig(Mode.PLAY, "preprocess")
    for node in _both_paths(_log_cfg(IRPureInstr(op, [prefix, _read(0)])), level, config):
        arithmetic = _find(node, Op.DebugLog)[0].args[0]
        assert arithmetic.func == op
        assert len(arithmetic.args) == 3
        assert all(arg.func == Op.Get for arg in arithmetic.args)


@pytest.mark.parametrize("level", _LEVELS)
@pytest.mark.parametrize("op", _VARIABLE_TAIL_FOLDS)
def test_runtime_constant_tail_does_not_hide_a_variable_prefix(level, op):
    prefix = IRPureInstr(op, [_read(0, PlayBlock.LevelData), _read(0)])
    config = OptimizerConfig(Mode.PLAY, "updateParallel")
    expression = IRPureInstr(op, [prefix, _read(1, PlayBlock.LevelData)])
    for node in _both_paths(_log_cfg(expression), level, config):
        arithmetic = _find(node, Op.DebugLog)[0].args[0]
        assert arithmetic.func == op
        assert len(arithmetic.args) == 3
        assert all(arg.func == Op.Get for arg in arithmetic.args)


@pytest.mark.parametrize("level", _LEVELS)
@pytest.mark.parametrize("op", _LEFT_FOLDS)
def test_runtime_constant_child_is_preserved_below_a_variable_parent(level, op):
    prefix = IRPureInstr(op, [_read(0, PlayBlock.LevelData), IRConst(2)])
    config = OptimizerConfig(Mode.PLAY, "updateParallel")
    for node in _both_paths(_log_cfg(IRPureInstr(op, [prefix, _read(0)])), level, config):
        arithmetic = _find(node, Op.DebugLog)[0].args[0]
        assert arithmetic == FunctionNode(
            op,
            (FunctionNode(op, (FunctionNode(Op.Get, (PlayBlock.LevelData, 0)), 2)), FunctionNode(Op.Get, (_INPUT, 0))),
        )


def test_deep_runtime_constant_prefix_keeps_its_folded_cost(analyze_node):
    def check():
        old_limit = sys.getrecursionlimit()
        sys.setrecursionlimit(200000)
        try:
            prefix = _read(0, PlayBlock.LevelData)
            for _ in range(1200):
                prefix = IRPureInstr(Op.Negate, [prefix])
            expression = IRPureInstr(Op.Subtract, [IRPureInstr(Op.Subtract, [prefix, IRConst(2)]), _read(0)])
            config = OptimizerConfig(Mode.PLAY, "updateParallel")
            node = cfg_to_engine_node(_log_cfg(expression), config)
            arithmetic = _find(node, Op.DebugLog)[0].args[0]
            assert arithmetic.func == Op.Subtract
            assert len(arithmetic.args) == 2
            assert arithmetic.args[0].func == Op.Subtract
            assert len(arithmetic.args[0].args) == 2
            cost = analyze_node(arithmetic, config.mode, config.callback)["effective_node_count"]
            assert cost == 5
            ungrouped = FunctionNode(Op.Subtract, (*arithmetic.args[0].args, arithmetic.args[1]))
            ungrouped_cost = analyze_node(ungrouped, config.mode, config.callback)["effective_node_count"]
            assert ungrouped_cost - cost == 1
            interpreter = Interpreter()
            interpreter.blocks[PlayBlock.LevelData] = [10]
            interpreter.blocks[_INPUT] = [3]
            assert interpreter.run(arithmetic) == 5
        finally:
            sys.setrecursionlimit(old_limit)

    # Marshaling and emission recurse beyond the default Windows thread stack.
    old_size = threading.stack_size(128 * 1024 * 1024)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(check).result()
    finally:
        threading.stack_size(old_size)


@pytest.mark.parametrize("op", _NEW_LEFT_FOLDS)
@pytest.mark.parametrize("runtime_constant_left", [False, True])
@pytest.mark.parametrize("literal_tail", [False, True])
def test_store_result_savings_match_emission_for_arithmetic_addresses(
    analyze_node, op, runtime_constant_left, literal_tail
):
    source = PlayBlock.LevelData if runtime_constant_left else _INPUT
    left = IRPureInstr(op, [_read(0, source), IRConst(2)])
    index = IRPureInstr(op, [left, IRConst(3) if literal_tail else _read(1)])
    place = BlockPlace(PlayBlock.LevelMemory, index)
    original = BasicBlock(statements=[IRSet(place, IRConst(7)), IRInstr(Op.DebugLog, [IRGet(place)])])
    config = OptimizerConfig(Mode.PLAY, "updateParallel")
    fused, saved = run_store_results(original, config.mode, config.callback, counted=True)
    before = analyze_node(cfg_to_engine_node(original, config), config.mode, config.callback)
    after = analyze_node(cfg_to_engine_node(fused, config), config.mode, config.callback)
    assert saved > 0
    assert saved == before["effective_node_count"] - after["effective_node_count"]


@pytest.mark.parametrize("level", _LEVELS)
@pytest.mark.parametrize(("op", "expected"), [(Op.Subtract, -3), (Op.Divide, 1 / 3), (Op.Power, 64)])
def test_left_chain_preserves_store_order_and_used_store_results(level, op, expected):
    left = IRPureInstr(op, [_store(0, IRConst(2)), _store(1, _read(0, _OUTPUT))])
    expression = IRPureInstr(op, [left, _store(0, IRConst(3))])
    for node in _both_paths(_log_cfg(expression), level):
        interpreter = Interpreter()
        interpreter.run(node)
        assert interpreter.blocks[_OUTPUT] == [3, 2]
        assert interpreter.log == [expected]
        assert len([store for store in _find(node, Op.Set) if store.args[0] == _OUTPUT]) == 3


@pytest.mark.parametrize("condition", [False, True])
@pytest.mark.parametrize(
    ("op", "false_result", "true_result"), [(Op.Subtract, -6, -2), (Op.Divide, 2 / 15, 0.5), (Op.Power, 32768, 16)]
)
def test_emission_keeps_stores_inside_the_selected_conditional_arm(condition, op, false_result, true_result):
    conditional = IRInstr(Op.If, [_read(1), _store(0, IRConst(2)), _store(1, IRConst(3))])
    expression = IRPureInstr(
        op,
        [IRPureInstr(op, [_read(0), conditional]), _store(2, _read(0, _OUTPUT))],
    )
    node = cfg_to_engine_node(_log_cfg(expression))
    interpreter = Interpreter()
    interpreter.blocks[_INPUT] = [2, int(condition)]
    interpreter.blocks[_OUTPUT] = [5, 7, 0]
    interpreter.run(node)
    assert interpreter.blocks[_OUTPUT] == ([2, 7, 2] if condition else [5, 3, 5])
    assert interpreter.log == [true_result if condition else false_result]


@pytest.mark.parametrize("level", _LEVELS)
@pytest.mark.parametrize("condition", [False, True])
@pytest.mark.parametrize(
    ("op", "false_result", "true_result"), [(Op.Subtract, -6, -2), (Op.Divide, 2 / 15, 0.5), (Op.Power, 32768, 16)]
)
def test_pipeline_keeps_stores_in_the_selected_cfg_branch(level, condition, op, false_result, true_result):
    selected = BlockPlace(TempBlock("selected", 1), 0)
    entry = BasicBlock(test=_read(1))
    true = BasicBlock(statements=[_store(0, IRConst(2)), IRSet(selected, IRConst(2))])
    false = BasicBlock(statements=[_store(1, IRConst(3)), IRSet(selected, IRConst(3))])
    expression = IRPureInstr(op, [IRPureInstr(op, [_read(0), IRGet(selected)]), _store(2, _read(0, _OUTPUT))])
    join = _log_cfg(expression)
    entry.connect_to(true, None)
    entry.connect_to(false, 0)
    true.connect_to(join, None)
    false.connect_to(join, None)
    for node in _both_paths(entry, level):
        interpreter = Interpreter()
        interpreter.blocks[_INPUT] = [2, int(condition)]
        interpreter.blocks[_OUTPUT] = [5, 7, 0]
        interpreter.run(node)
        assert interpreter.blocks[_OUTPUT] == ([2, 7, 2] if condition else [5, 3, 5])
        assert interpreter.log == [true_result if condition else false_result]


@pytest.mark.parametrize(
    ("op", "values", "error"),
    [(Op.Divide, [1, 0], ZeroDivisionError), (Op.Power, [1e308, 2], OverflowError)],
)
def test_failing_inner_arithmetic_does_not_execute_a_later_store(op, values, error):
    expression = IRPureInstr(op, [IRPureInstr(op, [_read(0), _read(1)]), _store(0, IRConst(3))])
    node = cfg_to_engine_node(_log_cfg(expression))
    arithmetic = _find(node, Op.DebugLog)[0].args[0]
    assert arithmetic.args[0].func == op
    assert len(arithmetic.args) == 2
    interpreter = Interpreter()
    interpreter.blocks[_INPUT] = values
    interpreter.blocks[_OUTPUT] = [7]
    with pytest.raises(error):
        interpreter.run(node)
    assert interpreter.blocks[_OUTPUT] == [7]
    assert interpreter.log == []


@pytest.mark.parametrize(
    ("op", "values", "tail", "error"),
    [
        (Op.Divide, [1, 0], IRPureInstr(Op.Log, [IRConst(-1)]), ZeroDivisionError),
        (Op.Power, [1e308, 2], IRPureInstr(Op.Divide, [IRConst(1), IRConst(0)]), OverflowError),
    ],
)
def test_failing_inner_arithmetic_keeps_precedence_over_a_later_pure_error(op, values, tail, error):
    expression = IRPureInstr(op, [IRPureInstr(op, [_read(0), _read(1)]), tail])
    node = cfg_to_engine_node(_log_cfg(expression))
    arithmetic = _find(node, Op.DebugLog)[0].args[0]
    assert arithmetic.args[0].func == op
    assert len(arithmetic.args) == 2
    interpreter = Interpreter()
    interpreter.blocks[_INPUT] = values
    with pytest.raises(error):
        interpreter.run(node)
    assert interpreter.log == []
