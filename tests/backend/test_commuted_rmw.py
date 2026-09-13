"""Constant-first read-modify-write fusion and its allocation tradeoffs."""

import operator

import numpy as np
import pytest
from sonolus.backend._opt.ir import debug_run  # ruff: ignore[import-private-name]
from sonolus.backend._opt.lower import (  # ruff: ignore[import-private-name]
    run_allocate,
    run_fuse_rmw,
    run_store_results,
)

from sonolus.backend.blocks import PlayBlock
from sonolus.backend.interpret import Interpreter
from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet
from sonolus.backend.node import FunctionNode
from sonolus.backend.ops import Op
from sonolus.backend.optimize import (
    FAST_PASSES,
    MINIMAL_PASSES,
    STANDARD_PASSES,
    OptimizerConfig,
    cfg_to_engine_node,
    run_passes,
)
from sonolus.backend.optimize.flow import BasicBlock
from sonolus.backend.place import BlockPlace, TempBlock
from sonolus.script.internal.context import ReadOnlyMemory

A, B = 20, 21


class _Float32Interpreter(Interpreter):
    """An interpreter with f32 leaves, memory, and arithmetic for these scalar fixtures."""

    def run(self, node):
        return np.float32(super().run(node))

    def get(self, block, index):
        return np.float32(super().get(block, index))

    def set(self, block, index, value):
        return super().set(block, index, np.float32(value))


def _run(cfg, initial=None, interpreter_type=Interpreter):
    interpreter = interpreter_type()
    interpreter.blocks[3000] = list(ReadOnlyMemory().values)
    for block, values in (initial or {}).items():
        interpreter.blocks[block] = list(values)
    interpreter.run(cfg_to_engine_node(cfg))
    return interpreter


def _find(node, op):
    result = []
    pending = [node]
    while pending:
        current = pending.pop()
        if isinstance(current, FunctionNode):
            if current.func == op:
                result.append(current)
            pending.extend(current.args)
    return result


def _update(op, constant, place=None):
    place = place or BlockPlace(A, 0)
    return BasicBlock(
        statements=[
            IRSet(place, IRPureInstr(op, [IRConst(constant), IRGet(place)])),
            IRInstr(Op.DebugLog, [IRGet(place)]),
        ]
    )


@pytest.mark.parametrize("strategy", ["bump", "try_bump", "packing"])
@pytest.mark.parametrize(
    ("op", "constant", "fused", "expected"),
    [(Op.Add, 1, Op.IncrementPost, 8), (Op.Add, 3, Op.SetAdd, 10), (Op.Multiply, 3, Op.SetMultiply, 21)],
)
def test_constant_first_scalar_update_fuses(strategy, op, constant, fused, expected):
    cfg = run_fuse_rmw(_update(op, constant), strategy=strategy)
    node = cfg_to_engine_node(cfg)
    assert len(_find(node, fused)) == 1
    assert not _find(node, op)
    result = _run(cfg, {A: [7]})
    assert result.blocks[A] == [expected]
    assert result.log == [expected]


@pytest.mark.parametrize("op", [Op.Add, Op.Multiply])
@pytest.mark.parametrize(
    ("constant", "value"),
    [
        (0, -0.0),
        (0, 0.0),
        (-1, 0.0),
        (-1, -0.0),
        (1, 16777216),
        (3.5, 1e-45),
        (3.5, 3e38),
        (float("inf"), 0),
        (-float("inf"), float("inf")),
        (float("nan"), 7),
        (7, float("nan")),
        (float("nan"), float("nan")),
    ],
)
def test_commuted_update_preserves_f32_result_and_zero_sign(op, constant, value):
    reference = run_allocate(_update(op, constant), strategy="bump")
    optimized = run_fuse_rmw(_update(op, constant), strategy="bump")
    fused = Op.IncrementPost if op == Op.Add and constant == 1 else Op.SetAdd if op == Op.Add else Op.SetMultiply
    assert len(_find(cfg_to_engine_node(optimized), fused)) == 1
    arithmetic = operator.add if op == Op.Add else operator.mul
    with np.errstate(invalid="ignore", over="ignore"):
        expected = arithmetic(np.float32(constant), np.float32(value))
        original = _run(reference, {A: [value]}, _Float32Interpreter)
        result = _run(optimized, {A: [value]}, _Float32Interpreter)
    for actual in [original.get(A, 0), *original.log, result.get(A, 0), *result.log]:
        if np.isnan(expected):
            assert np.isnan(actual)
        else:
            assert np.float32(actual).tobytes() == np.float32(expected).tobytes()

    returned_cfg = run_store_results(optimized)
    assert len(returned_cfg.statements) == 1
    assert _find(cfg_to_engine_node(returned_cfg), Op.DebugLog)[0].args[0].func == fused
    with np.errstate(invalid="ignore", over="ignore"):
        returned = _run(returned_cfg, {A: [value]}, _Float32Interpreter)
    for actual in [returned.get(A, 0), *returned.log]:
        if np.isnan(expected):
            assert np.isnan(actual)
        else:
            assert np.float32(actual).tobytes() == np.float32(expected).tobytes()


@pytest.mark.parametrize("enum_target", [False, True])
@pytest.mark.parametrize("op", [Op.Add, Op.Multiply])
def test_static_enum_and_numeric_places_match_for_commuted_fusion(enum_target, op):
    block = PlayBlock.LevelMemory
    target = BlockPlace(block if enum_target else int(block), 1, 2)
    source = BlockPlace(int(block) if enum_target else block, 3)
    cfg = run_fuse_rmw(BasicBlock(statements=[IRSet(target, IRPureInstr(op, [IRConst(3), IRGet(source)]))]))
    fused = Op.SetAdd if op == Op.Add else Op.SetMultiply
    assert len(_find(cfg_to_engine_node(cfg), fused)) == 1
    result = _run(cfg, {int(block): [11, 22, 33, 7]})
    assert result.blocks[int(block)] == [11, 22, 33, 10 if op == Op.Add else 21]


@pytest.mark.parametrize(
    ("op", "constant", "fused", "expected"),
    [(Op.Add, 1, Op.IncrementPost, 8), (Op.Add, 3, Op.SetAdd, 10), (Op.Multiply, 3, Op.SetMultiply, 21)],
)
def test_commuted_update_can_return_the_stored_value_once(op, constant, fused, expected):
    cfg = run_store_results(run_fuse_rmw(_update(op, constant)))
    node = cfg_to_engine_node(cfg)
    assert len(cfg.statements) == 1
    assert len(_find(node, fused)) == 1
    assert _find(node, Op.DebugLog)[0].args[0].func == fused
    result = _run(cfg, {A: [7]})
    assert result.blocks[A] == [expected]
    assert result.log == [expected]


@pytest.mark.parametrize("level", [FAST_PASSES, STANDARD_PASSES])
@pytest.mark.parametrize("op", [Op.Add, Op.Multiply])
def test_full_pipeline_commuted_updates_match_unfused_reference(level, op):
    reference = _run(run_passes(_update(op, 3), MINIMAL_PASSES, OptimizerConfig()), {A: [7]})
    cfg = run_passes(_update(op, 3), level, OptimizerConfig())
    fused = Op.SetAdd if op == Op.Add else Op.SetMultiply
    assert len(_find(cfg_to_engine_node(cfg), fused)) == 1
    result = _run(cfg, {A: [7]})
    assert result.blocks[A] == reference.blocks[A]
    assert result.log == reference.log


@pytest.mark.parametrize("op", [Op.Add, Op.Multiply])
@pytest.mark.parametrize("component", ["index", "block"])
def test_commuted_update_with_runtime_address_stays_unfused(op, component):
    place = BlockPlace(A, IRGet(BlockPlace(B, 0))) if component == "index" else BlockPlace(IRGet(BlockPlace(B, 0)), 0)
    cfg = run_fuse_rmw(_update(op, 3, place))
    node = cfg_to_engine_node(cfg)
    assert not _find(node, Op.SetAdd)
    assert not _find(node, Op.SetMultiply)
    initial = {A: [7], B: [0 if component == "index" else A]}
    result = _run(cfg, initial)
    assert result.blocks[A] == [10 if op == Op.Add else 21]
    assert result.blocks[B] == initial[B]


@pytest.mark.parametrize("op", [Op.Add, Op.Multiply])
@pytest.mark.parametrize("leading", ["read", "store", "random"])
def test_commuted_update_requires_a_constant_leading_operand(op, leading, monkeypatch):
    draws = []

    def draw(low, high):
        draws.append((low, high))
        return 3

    monkeypatch.setattr("sonolus.backend.interpret.random.uniform", draw)
    value = {
        "read": IRGet(BlockPlace(B, 0)),
        "store": IRInstr(Op.Set, [IRConst(A), IRConst(0), IRConst(3)]),
        "random": IRInstr(Op.Random, [IRConst(0), IRConst(4)]),
    }[leading]
    cfg = run_fuse_rmw(
        BasicBlock(
            statements=[
                IRSet(BlockPlace(A, 0), IRPureInstr(op, [value, IRGet(BlockPlace(A, 0))])),
                IRInstr(Op.DebugLog, [IRGet(BlockPlace(A, 0))]),
            ]
        )
    )
    node = cfg_to_engine_node(cfg)
    assert not _find(node, Op.SetAdd)
    assert not _find(node, Op.SetMultiply)
    result = _run(cfg, {A: [7], B: [3]})
    old = 3 if leading == "store" else 7
    expected = 3 + old if op == Op.Add else 3 * old
    assert result.blocks[A] == [expected]
    assert result.log == [expected]
    assert draws == ([(0, 4)] if leading == "random" else [])


@pytest.mark.parametrize("op", [Op.Add, Op.Multiply])
def test_commuted_update_to_a_different_cell_stays_unfused(op):
    cfg = run_fuse_rmw(
        BasicBlock(statements=[IRSet(BlockPlace(A, 0), IRPureInstr(op, [IRConst(3), IRGet(BlockPlace(A, 1))]))])
    )
    node = cfg_to_engine_node(cfg)
    assert not _find(node, Op.SetAdd)
    assert not _find(node, Op.SetMultiply)
    result = _run(cfg, {A: [7, 11]})
    assert result.blocks[A] == [14 if op == Op.Add else 33, 11]


@pytest.mark.parametrize("op", [Op.Add, Op.Multiply])
@pytest.mark.parametrize("component", ["index", "block"])
def test_commuted_update_preserves_impure_address_evaluations(op, component, monkeypatch):
    draws = []
    results = iter([0, 1] if component == "index" else [A, A])

    def draw(low, high):
        draws.append((low, high))
        return next(results)

    monkeypatch.setattr("sonolus.backend.interpret.random.uniform", draw)

    def place():
        random = IRInstr(Op.Random, [IRConst(0), IRConst(30)])
        return BlockPlace(A, random) if component == "index" else BlockPlace(random, 0)

    cfg = run_fuse_rmw(BasicBlock(statements=[IRSet(place(), IRPureInstr(op, [IRConst(3), IRGet(place())]))]))
    node = cfg_to_engine_node(cfg)
    assert not _find(node, Op.SetAdd)
    assert not _find(node, Op.SetMultiply)
    assert len(_find(node, Op.Random)) == 2
    result = _run(cfg, {A: [7, 11]})
    source = 11 if component == "index" else 7
    assert result.blocks[A] == [3 + source if op == Op.Add else 3 * source, 11]
    assert draws == [(0, 30), (0, 30)]


@pytest.mark.parametrize(
    ("op", "fused", "expected"),
    [
        (Op.Subtract, Op.SetSubtract, 3),
        (Op.Divide, Op.SetDivide, 2),
        (Op.Mod, Op.SetMod, 0),
        (Op.Rem, Op.SetRem, 0),
        (Op.Power, Op.SetPower, 216),
    ],
)
def test_noncommutative_constant_first_operations_stay_unfused(op, fused, expected):
    cfg = run_fuse_rmw(_update(op, 6))
    node = cfg_to_engine_node(cfg)
    assert not _find(node, fused)
    result = _run(cfg, {A: [3]})
    assert result.blocks[A] == [expected]
    assert result.log == [expected]


@pytest.mark.parametrize("op", [Op.Add, Op.Multiply])
def test_nary_constant_first_operations_stay_unfused(op):
    cfg = run_fuse_rmw(
        BasicBlock(
            statements=[IRSet(BlockPlace(A, 0), IRPureInstr(op, [IRConst(3), IRGet(BlockPlace(A, 0)), IRConst(5)]))]
        )
    )
    node = cfg_to_engine_node(cfg)
    assert not _find(node, Op.SetAdd)
    assert not _find(node, Op.SetMultiply)
    assert _run(cfg, {A: [7]}).blocks[A] == [15 if op == Op.Add else 105]


@pytest.mark.parametrize(
    ("op", "constant", "fused", "expected"),
    [(Op.Add, 1, Op.IncrementPost, 13), (Op.Multiply, 3, Op.SetMultiply, 270)],
)
def test_copy_allocation_preserves_larger_commuted_rmw_savings(op, constant, fused, expected):
    a, b, x, y = [TempBlock(name)[0] for name in ("a", "b", "x", "y")]
    cfg = debug_run(
        run_fuse_rmw(
            BasicBlock(
                statements=[
                    IRSet(a, IRConst(10)),
                    IRSet(b, IRConst(20)),
                    IRSet(x, IRPureInstr(op, [IRConst(constant), IRGet(a)])),
                    IRSet(a, IRPureInstr(op, [IRConst(constant), IRGet(x)])),
                    IRSet(x, IRPureInstr(op, [IRConst(constant), IRGet(a)])),
                    IRSet(y, IRConst(30)),
                    IRSet(BlockPlace(B, 0), IRGet(x)),
                    IRSet(BlockPlace(B, 1), IRGet(y)),
                    IRInstr(Op.DebugLog, [IRGet(b)]),
                ]
            )
        ),
        phases=["copy"],
    )
    node = cfg_to_engine_node(cfg)
    assert len(_find(node, fused)) == 3
    assert not _find(node, Op.Copy)
    result = _run(cfg)
    assert result.blocks[B] == [expected, 30]
    assert result.log == [20]
