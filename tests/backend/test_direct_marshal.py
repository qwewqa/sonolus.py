"""Focused equivalence and ownership tests for direct Context marshalling."""

from __future__ import annotations

import weakref
from collections.abc import Callable

import pytest

from sonolus.backend._opt import driver, ir  # ruff: ignore[import-private-name]
from sonolus.backend.blocks import PlayBlock
from sonolus.backend.interpret import Interpreter
from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet
from sonolus.backend.mode import Mode
from sonolus.backend.ops import Op
from sonolus.backend.optimize import (
    MINIMAL_PASSES,
    OptimizerConfig,
    optimize_and_finalize,
)
from sonolus.backend.optimize.flow import cfg_to_text, traverse_cfg_preorder
from sonolus.backend.place import BlockPlace, TempBlock
from sonolus.build import compile as compile_module
from sonolus.script.internal.callbacks import update_callback
from sonolus.script.internal.context import (
    CallbackContextState,
    Context,
    ModeContextState,
    ProjectContextState,
    RuntimeChecks,
    context_to_cfg,
    ctx,
)
from sonolus.script.internal.error import CompilationError

CALLBACK = "updateSequential"
ContextBuilder = Callable[[], tuple[Context, list[Context]]]


def _scalar(name: str) -> BlockPlace:
    return BlockPlace(TempBlock(name, 1), 0, 0)


def _read(name: str) -> IRGet:
    return IRGet(_scalar(name))


def _contexts(count: int, mode: Mode = Mode.PLAY, callback: str = CALLBACK) -> list[Context]:
    project_state = ProjectContextState(runtime_checks=RuntimeChecks.NONE)
    mode_state = ModeContextState(mode)
    callback_state = CallbackContextState(callback)
    return [Context(project_state, mode_state, callback_state) for _ in range(count)]


def build_shared_cycle() -> tuple[Context, list[Context]]:
    """Build a cycle with a shared join and deliberately scrambled branch insertion."""
    root, negative, zero, positive, default, join = contexts = _contexts(6)
    for marker, context in enumerate(contexts, 10):
        context.statements.append(IRSet(_scalar(f"marker_{marker}"), IRConst(marker)))
    root.test = IRGet(BlockPlace(PlayBlock.RuntimeUpdate, 0))
    root.outgoing.update({None: default, 1.5: positive, -2: negative, 0: zero})
    negative.outgoing[None] = join
    zero.outgoing[None] = join
    positive.outgoing[None] = root
    default.outgoing[None] = join
    join.outgoing[None] = root
    return root, contexts


def build_values_and_places() -> tuple[Context, list[Context]]:
    """Build variadic expressions with scalar, dynamic, and readonly memory accesses."""
    (root,) = contexts = _contexts(1)
    pointer = _scalar("pointer")
    index = _scalar("index")
    dynamic_target = BlockPlace(pointer, IRGet(index), 3)
    output = BlockPlace(2000, 7)
    root.statements.extend(
        [
            IRSet(pointer, IRConst(int(PlayBlock.LevelMemory))),
            IRSet(index, IRConst(2)),
            IRSet(dynamic_target, IRPureInstr(Op.Subtract, [IRConst(20), IRConst(3), IRConst(2)])),
            IRSet(
                output,
                IRPureInstr(
                    Op.Add,
                    [
                        IRGet(dynamic_target),
                        IRGet(BlockPlace(PlayBlock.LevelData, 1)),
                        IRGet(BlockPlace(int(PlayBlock.LevelData), 1)),
                        IRGet(BlockPlace(PlayBlock.LevelData, IRGet(index), -1)),
                    ],
                ),
            ),
            IRInstr(Op.DebugLog, [IRGet(output)]),
            IRInstr(Op.Break, [IRConst(1), IRGet(output)]),
        ]
    )
    return root, contexts


def build_deep_cycle(depth: int = 1500) -> tuple[Context, list[Context]]:
    """Build a graph deep enough to reject recursive discovery implementations."""
    contexts = _contexts(depth)
    for index in range(depth - 1):
        contexts[index].outgoing[None] = contexts[index + 1]
    contexts[0].statements.append(IRSet(_scalar("first"), IRConst(1)))
    contexts[depth // 2].statements.append(IRSet(_scalar("middle"), IRConst(2)))
    contexts[-1].statements.append(IRSet(_scalar("last"), IRConst(3)))
    contexts[-1].outgoing[None] = contexts[depth // 2]
    return contexts[0], contexts


def build_large_integer_order() -> tuple[Context, list[Context]]:
    """Insert adjacent integers above 2**53 in the opposite of numeric order."""
    root, lower, higher, exit_ = contexts = _contexts(4)
    lower.statements.append(IRSet(_scalar("lower"), IRConst(101)))
    higher.statements.append(IRSet(_scalar("higher"), IRConst(202)))
    root.test = IRGet(BlockPlace(PlayBlock.RuntimeUpdate, 0))
    root.outgoing[2**53 + 1] = higher
    root.outgoing[2**53] = lower
    root.outgoing[None] = exit_
    lower.outgoing[None] = exit_
    higher.outgoing[None] = exit_
    return root, contexts


def build_terminating_loop(mode: Mode = Mode.TUTORIAL, callback: str = "update") -> tuple[Context, list[Context]]:
    """Build a real cycle that emits a finite loop after optimization."""
    entry, loop, exit_ = contexts = _contexts(3, mode, callback)
    entry.statements.append(IRSet(_scalar("i"), IRConst(0)))
    entry.outgoing[None] = loop
    loop.statements.append(IRSet(_scalar("i"), IRPureInstr(Op.Add, [_read("i"), IRConst(1)])))
    loop.test = IRPureInstr(Op.Less, [_read("i"), IRConst(3)])
    loop.outgoing[None] = loop
    loop.outgoing[0] = exit_
    exit_.statements.append(IRInstr(Op.DebugLog, [_read("i")]))
    return entry, contexts


CASE_BUILDERS: dict[str, ContextBuilder] = {
    "shared_cycle": build_shared_cycle,
    "values_and_places": build_values_and_places,
}


def _marshal_pair(builder: ContextBuilder):
    direct_root, direct_contexts = builder()
    baseline_root, baseline_contexts = builder()
    direct = ir.marshal_context(direct_root, Mode.PLAY, CALLBACK)
    baseline = ir.marshal_in(context_to_cfg(baseline_root), Mode.PLAY, CALLBACK)
    direct.verify()
    baseline.verify()
    return direct, baseline, direct_root, direct_contexts, baseline_contexts


@pytest.mark.parametrize("builder", CASE_BUILDERS.values(), ids=CASE_BUILDERS.keys())
def test_direct_marshal_matches_transient_cfg_exactly(builder: ContextBuilder):
    direct, baseline, root, contexts, baseline_contexts = _marshal_pair(builder)
    try:
        direct_cfg = ir.to_basic_blocks(direct)
        baseline_cfg = ir.to_basic_blocks(baseline)

        assert direct.stats() == baseline.stats()
        assert direct.stats()["blocks"] == len(contexts)
        assert cfg_to_text(direct_cfg) == cfg_to_text(baseline_cfg)
        assert all(hasattr(context, "outgoing") for context in contexts)
        assert all(not hasattr(context, "outgoing") for context in baseline_contexts)

        if builder is build_shared_cycle:
            ordered = sorted(direct_cfg.outgoing, key=lambda edge: (edge.cond is None, edge.cond))
            assert [(type(edge.cond), edge.cond) for edge in ordered] == [
                (int, -2),
                (int, 0),
                (float, 1.5),
                (type(None), None),
            ]
    finally:
        ir.release_context(root)


def test_direct_marshal_deep_cycle_is_iterative_and_exact():
    direct, baseline, root, contexts, baseline_contexts = _marshal_pair(build_deep_cycle)
    try:
        direct_cfg = ir.to_basic_blocks(direct)
        baseline_cfg = ir.to_basic_blocks(baseline)

        assert direct.stats() == baseline.stats()
        assert direct.stats()["blocks"] == 1500
        assert direct.stats()["edges"] == 1500
        assert cfg_to_text(direct_cfg) == cfg_to_text(baseline_cfg)
        assert all(hasattr(context, "outgoing") for context in contexts)
        assert all(not hasattr(context, "outgoing") for context in baseline_contexts)
    finally:
        ir.release_context(root)


def _marker_temp_names(cfg) -> dict[int, str]:
    result = {}
    for block in traverse_cfg_preorder(cfg):
        for statement in block.statements:
            if (
                isinstance(statement, IRSet)
                and isinstance(statement.value, IRConst)
                and statement.value.value in {101, 202}
            ):
                result[statement.value.value] = statement.place.block.name
    return result


def test_context_rpo_orders_large_integer_conditions_without_float_rounding():
    direct_root, _ = build_large_integer_order()
    baseline_root, _ = build_large_integer_order()
    direct = ir.marshal_context(direct_root, Mode.PLAY, CALLBACK)
    baseline = ir.marshal_in(context_to_cfg(baseline_root), Mode.PLAY, CALLBACK)
    try:
        expected_error = "block 0: parallel edges with the same case 9007199254740992 target different blocks"
        with pytest.raises(AssertionError) as direct_error:
            direct.verify()
        with pytest.raises(AssertionError) as baseline_error:
            baseline.verify()

        assert str(direct_error.value) == expected_error
        assert str(baseline_error.value) == expected_error
        direct_names = _marker_temp_names(ir.to_basic_blocks(direct))
        baseline_names = _marker_temp_names(ir.to_basic_blocks(baseline))

        # Correct DFS visits the lower condition first, so reverse postorder marshals
        # the higher target first. Arena export names that target's first temp v0.
        assert baseline_names == {101: "v1", 202: "v0"}
        assert direct_names == baseline_names
    finally:
        ir.release_context(direct_root)


def test_callback_to_context_retries_with_a_fresh_diagnostic_context(monkeypatch):
    attempts = []
    roots = []

    def compile_once(*_args):
        active = ctx()
        attempts.append(active.no_eval)
        roots.append(weakref.ref(active))
        if active.no_eval:
            raise CompilationError("force diagnostic retry")

    monkeypatch.setattr(compile_module, "compile_and_call_at_definition", compile_once)
    result = compile_module.callback_to_context(
        ProjectContextState(runtime_checks=RuntimeChecks.NONE),
        ModeContextState(Mode.TUTORIAL),
        lambda: None,
        "update",
    )
    try:
        assert attempts == [True, False]
        assert roots[0]() is None
        assert roots[1]() is result
        assert result.callback_state.no_eval is False
    finally:
        ir.release_context(result)


def test_callback_to_context_adds_break_for_a_numeric_return():
    def returns_number():
        return 7

    root = compile_module.callback_to_context(
        ProjectContextState(runtime_checks=RuntimeChecks.NONE),
        ModeContextState(Mode.TUTORIAL),
        returns_number,
        "update",
    )
    try:
        arena = ir.marshal_context(root, Mode.TUTORIAL, "update")
        arena.verify()
        exported = ir.to_basic_blocks(arena)
        breaks = [
            statement
            for block in traverse_cfg_preorder(exported)
            for statement in block.statements
            if isinstance(statement, IRInstr) and statement.op is Op.Break
        ]
        assert len(breaks) == 1
        assert len(breaks[0].args) == 2
        assert breaks[0].args[0] == IRConst(1)
        node = driver.optimize_and_finalize_context(root, "minimal", Mode.TUTORIAL, "update")
        assert Interpreter().run(node) == 7
    finally:
        ir.release_context(root)


def _interpret(node) -> tuple[float, list[float], float, float]:
    interpreter = Interpreter()
    interpreter.blocks[int(PlayBlock.LevelData)] = [0, 7, 0]
    result = interpreter.run(node)
    return result, interpreter.log, interpreter.get(2000, 5), interpreter.get(2000, 7)


def test_direct_pipeline_matches_baseline_for_value_and_place_flags():
    direct_root, direct_contexts = build_values_and_places()
    baseline_root, baseline_contexts = build_values_and_places()
    config = OptimizerConfig(mode=Mode.PLAY, callback=CALLBACK)
    try:
        direct_node = driver.optimize_and_finalize_context(direct_root, "minimal", Mode.PLAY, CALLBACK)
        baseline_node = optimize_and_finalize(context_to_cfg(baseline_root), MINIMAL_PASSES, config)

        assert direct_node == baseline_node
        assert _interpret(direct_node) == (36.0, [36.0], 15.0, 36.0)
        assert _interpret(baseline_node) == (36.0, [36.0], 15.0, 36.0)
        assert all(hasattr(context, "outgoing") for context in direct_contexts)
        assert all(not hasattr(context, "outgoing") for context in baseline_contexts)
    finally:
        ir.release_context(direct_root)


def _context_topology(contexts: list[Context]):
    return tuple((context, tuple(context.outgoing.items())) for context in contexts)


def test_marshal_failure_retains_context_graph_for_repair_and_retry():
    root, contexts = build_shared_cycle()
    invalid = contexts[-1]
    invalid.statements.append("not-an-ir-statement")
    topology = _context_topology(contexts)
    try:
        with pytest.raises(ValueError, match=r"^Unsupported statement: str: 'not-an-ir-statement'"):
            ir.marshal_context(root, Mode.PLAY, CALLBACK)

        assert _context_topology(contexts) == topology
        invalid.statements.pop()
        retry = ir.marshal_context(root, Mode.PLAY, CALLBACK)
        retry.verify()
        assert retry.stats()["blocks"] == len(contexts)
    finally:
        ir.release_context(root)


def _compile_direct_context(factory, *, validate_only: bool):
    return driver.compile_mode(
        Mode.TUTORIAL,
        ProjectContextState(runtime_checks=RuntimeChecks.NONE),
        None,
        [(update_callback, lambda: None)],
        factory,
        MINIMAL_PASSES,
        validate_only,
    )


def _compile_actual_global_callback():
    def callback():
        pass

    return compile_module.compile_mode(
        mode=Mode.TUTORIAL,
        project_state=ProjectContextState(runtime_checks=RuntimeChecks.NONE),
        archetypes=None,
        global_callbacks=[(update_callback, callback)],
        level=MINIMAL_PASSES,
    )


def test_public_compile_mode_uses_direct_context_finalizer(monkeypatch):
    calls = []
    original_finalize = driver.optimize_and_finalize_context

    def recording_finalize(entry, level, mode, callback):
        calls.append((level, mode, callback))
        return original_finalize(entry, level, mode, callback)

    monkeypatch.setattr(driver, "optimize_and_finalize_context", recording_finalize)
    result = _compile_actual_global_callback()

    assert calls == [("minimal", Mode.TUTORIAL, "update")]
    assert isinstance(result["update"], int)
    assert result["nodes"]


def test_direct_driver_failure_keeps_topology_and_cause_then_allows_retry(monkeypatch):
    root, contexts = build_terminating_loop()
    topology = _context_topology(contexts)
    original_finalize = driver.optimize_and_finalize_context

    def factory(*_args):
        return root

    def fail_optimization(*_args):
        raise RuntimeError("direct optimizer exploded")

    monkeypatch.setattr(driver, "optimize_and_finalize_context", fail_optimization)
    with pytest.raises(
        CompilationError,
        match=r"^Optimization failed for callback 'update' in TUTORIAL mode: direct optimizer exploded$",
    ) as exc_info:
        _compile_direct_context(factory, validate_only=False)

    assert type(exc_info.value.__cause__) is RuntimeError
    assert str(exc_info.value.__cause__) == "direct optimizer exploded"
    assert _context_topology(contexts) == topology

    try:
        retry_node = original_finalize(root, "minimal", Mode.TUTORIAL, "update")
        baseline_root, _ = build_terminating_loop()
        baseline_node = optimize_and_finalize(
            context_to_cfg(baseline_root),
            MINIMAL_PASSES,
            OptimizerConfig(mode=Mode.TUTORIAL, callback="update"),
        )
        assert retry_node == baseline_node
        assert _context_topology(contexts) == topology
    finally:
        ir.release_context(root)
