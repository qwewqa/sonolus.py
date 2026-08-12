"""Tests for the compile-time mode predicates in sonolus.script.runtime.

is_play/is_watch/is_preview/is_tutorial/is_preprocessing read the compile context (ctx().mode_state.mode /
ctx().callback) while tracing and resolve to constants before any optimizer pass runs; outside a compile context
they all return False, so plain Python gives no reference to check against. These tests build their own compile
context with callback_to_cfg and interpret the result, as tests/script/test_global_memory.py and
tests/backend/test_block_access.py already do.
"""

import pytest

from sonolus.backend.blocks import PlayBlock
from sonolus.backend.interpret import Interpreter
from sonolus.backend.mode import Mode
from sonolus.backend.optimize import OptimizerConfig, cfg_to_engine_node, run_passes
from sonolus.build.compile import callback_to_cfg
from sonolus.script.internal.callbacks import (
    despawn_time_callback,
    preprocess_callback,
    spawn_order_callback,
    spawn_time_callback,
    update_callback,
    update_sequential_callback,
)
from sonolus.script.internal.context import ModeContextState, ProjectContextState, RuntimeChecks
from sonolus.script.internal.visitor import clear_frontend_caches
from sonolus.script.runtime import is_play, is_preprocessing, is_preview, is_tutorial, is_watch
from tests.script.conftest import optimization_levels


def _compile_predicate(mode, callback_name, predicate):
    """Compile a callback that returns `predicate()` under the given mode/callback-name context.

    Runs at every optimization level tests/script/conftest.py exercises and asserts the results agree
    before returning the shared one. The predicate resolves to a constant before any pass runs, so a
    level that disagrees means a pass changed that constant.
    """
    results = []
    for passes in optimization_levels:
        clear_frontend_caches()
        project_state = ProjectContextState(runtime_checks=RuntimeChecks.NONE)
        mode_state = ModeContextState(mode)

        def cb(_predicate=predicate):
            return _predicate()

        cfg = callback_to_cfg(project_state, mode_state, cb, callback_name)
        cfg = run_passes(cfg, passes, OptimizerConfig())
        entry = cfg_to_engine_node(cfg)
        interpreter = Interpreter()
        interpreter.blocks[PlayBlock.EngineRom] = project_state.rom.values
        results.append(interpreter.run(entry))
    assert len(set(results)) == 1, f"Result differs between optimization levels: {results}"
    return results[0]


MODES = [Mode.PLAY, Mode.WATCH, Mode.PREVIEW, Mode.TUTORIAL]
MODE_PREDICATES = {
    Mode.PLAY: is_play,
    Mode.WATCH: is_watch,
    Mode.PREVIEW: is_preview,
    Mode.TUTORIAL: is_tutorial,
}


@pytest.mark.parametrize("compiled_mode", MODES, ids=lambda m: m.name)
@pytest.mark.parametrize("predicate_mode", MODES, ids=lambda m: m.name)
def test_mode_predicate_true_only_for_its_own_mode(predicate_mode, compiled_mode):
    predicate = MODE_PREDICATES[predicate_mode]
    result = _compile_predicate(compiled_mode, "", predicate)
    assert result == (1.0 if compiled_mode == predicate_mode else 0.0)


# Imported from callbacks.py rather than hardcoded: is_preprocessing keeps its own literal copy of the
# names, so a rename in callbacks.py has to fail here rather than drift out of sync unnoticed.
CANONICAL_PREPROCESSING_CALLBACKS = [
    preprocess_callback,
    spawn_order_callback,
    spawn_time_callback,
    despawn_time_callback,
]


@pytest.mark.parametrize("callback_info", CANONICAL_PREPROCESSING_CALLBACKS, ids=lambda c: c.py_name)
def test_is_preprocessing_true_for_each_canonical_preprocessing_callback(callback_info):
    result = _compile_predicate(Mode.PLAY, callback_info.name, is_preprocessing)
    assert result == 1.0


@pytest.mark.parametrize("callback_info", [update_callback, update_sequential_callback], ids=lambda c: c.py_name)
def test_is_preprocessing_false_for_non_preprocessing_callback(callback_info):
    result = _compile_predicate(Mode.PLAY, callback_info.name, is_preprocessing)
    assert result == 0.0


def test_is_preprocessing_false_with_no_callback_name():
    result = _compile_predicate(Mode.PLAY, "", is_preprocessing)
    assert result == 0.0
