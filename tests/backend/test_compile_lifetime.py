import gc
import weakref

import pytest

from sonolus.backend._opt import driver  # ruff: ignore[import-private-name]
from sonolus.backend.ir import IRConst
from sonolus.backend.mode import Mode
from sonolus.backend.optimize import (
    FAST_PASSES,
    MINIMAL_PASSES,
    STANDARD_PASSES,
    OptimizerConfig,
    optimize_and_finalize,
)
from sonolus.backend.optimize.flow import BasicBlock, traverse_cfg_preorder
from sonolus.build import compile as compile_module
from sonolus.script.archetype import PlayArchetype
from sonolus.script.internal.callbacks import update_callback
from sonolus.script.internal.context import CallbackContextState, Context, ProjectContextState, RuntimeChecks


def _loop_cfg() -> BasicBlock:
    entry = BasicBlock()
    loop = BasicBlock(test=IRConst(0))
    exit_block = BasicBlock()
    entry.connect_to(loop)
    loop.connect_to(exit_block, 0)
    loop.connect_to(loop)
    return entry


def _topology(entry: BasicBlock):
    return tuple(
        (
            block,
            frozenset((edge, edge.src, edge.dst, edge.cond) for edge in block.outgoing),
            None
            if block.incoming is None
            else frozenset((edge, edge.src, edge.dst, edge.cond) for edge in block.incoming),
        )
        for block in traverse_cfg_preorder(entry)
    )


def _compile_with_target(target: str, *, validate_only: bool):
    project_state = ProjectContextState(runtime_checks=RuntimeChecks.NONE)
    if target == "global":

        def callback():
            pass

        return compile_module.compile_mode(
            mode=Mode.TUTORIAL,
            project_state=project_state,
            archetypes=None,
            global_callbacks=[(update_callback, callback)],
            level=MINIMAL_PASSES,
            validate_only=validate_only,
        )

    class Tracked(PlayArchetype):
        name = "Tracked"

        def update_sequential(self):
            pass

    return compile_module.compile_mode(
        mode=Mode.PLAY,
        project_state=project_state,
        archetypes=[Tracked],
        global_callbacks=None,
        level=MINIMAL_PASSES,
        validate_only=validate_only,
    )


@pytest.mark.parametrize("target", ["global", "archetype"])
@pytest.mark.parametrize("validate_only", [False, True], ids=["compile", "validate"])
def test_compile_mode_releases_owned_callback_contexts(monkeypatch, target, validate_only):
    references = []

    def callback_to_context(project_state, mode_state, _callback, name, _archetype):
        callback_state = CallbackContextState(name)
        entry, loop, exit_block = contexts = [Context(project_state, mode_state, callback_state) for _ in range(3)]
        entry.outgoing[None] = loop
        loop.test = IRConst(0)
        loop.outgoing[0] = exit_block
        loop.outgoing[None] = loop
        references.extend(weakref.ref(context) for context in contexts)
        return entry

    monkeypatch.setattr(compile_module, "callback_to_context", callback_to_context)
    if validate_only:
        monkeypatch.setattr(
            driver,
            "optimize_and_finalize_context",
            lambda *_args: pytest.fail("validation-only compilation marshaled its Context"),
        )
    gc_was_enabled = gc.isenabled()
    gc.disable()
    try:
        result = _compile_with_target(target, validate_only=validate_only)
        index = result["update"] if target == "global" else result["archetypes"][0]["updateSequential"]["index"]
        if validate_only:
            assert index == 0
            assert result["nodes"] == []
        else:
            assert isinstance(index, int)
            assert result["nodes"]
        assert len(references) == 3
        assert all(reference() is None for reference in references)
    finally:
        if gc_was_enabled:
            gc.enable()


@pytest.mark.parametrize("level", [MINIMAL_PASSES, FAST_PASSES, STANDARD_PASSES])
def test_public_optimize_and_finalize_preserves_a_reusable_cfg(level):
    cfg = _loop_cfg()
    topology = _topology(cfg)
    config = OptimizerConfig(mode=Mode.PLAY, callback="updateSequential")

    first = optimize_and_finalize(cfg, level, config)

    assert _topology(cfg) == topology
    assert optimize_and_finalize(cfg, level, config) == first
    assert _topology(cfg) == topology
