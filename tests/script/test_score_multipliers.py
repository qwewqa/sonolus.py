"""Tests for `archetype_score_multiplier` and `entity_score_multiplier`, the four score-multiplier descriptors.

Both attributes resolve to a block place and are only reachable inside a compile context, so plain Python has no
reference for them and `run_and_validate` cannot be the oracle. Each round-trip test here compiles an archetype's
`preprocess` and then its `updateParallel` and interprets both against one interpreter, the way the engine runs
them, and reads the values back out of the debug log rather than out of a block, so the expectation depends on
what the callbacks wrote and not on where the implementation put it.
"""

import pytest

from sonolus.backend.blocks import PlayBlock
from sonolus.backend.interpret import Interpreter
from sonolus.backend.mode import Mode
from sonolus.backend.optimize import OptimizerConfig, cfg_to_engine_node, run_passes
from sonolus.build.compile import callback_to_cfg
from sonolus.script.archetype import PlayArchetype
from sonolus.script.debug import debug_log
from sonolus.script.internal.context import ModeContextState, ProjectContextState, RuntimeChecks
from sonolus.script.internal.error import CompilationError
from sonolus.script.internal.visitor import clear_frontend_caches
from tests.script.conftest import optimization_levels

# PlayEntityInfo is (index, archetype_id, state), so the entity's runtime archetype id sits at offset 1.
ENTITY_INFO_ARCHETYPE_ID = 1

# The index of the other entity the reference-data tests write through.
OTHER_ENTITY_INDEX = 1


class ScoreProbe(PlayArchetype):
    name = "ScoreProbe"
    is_scored = True

    def preprocess(self):
        ScoreProbe.archetype_score_multiplier = 2.0
        ScoreOther.archetype_score_multiplier = 3.0
        self.entity_score_multiplier = 4.0
        ScoreProbe.at(OTHER_ENTITY_INDEX).entity_score_multiplier = 5.0

    def update_parallel(self):
        debug_log(ScoreProbe.archetype_score_multiplier)
        debug_log(ScoreOther.archetype_score_multiplier)
        debug_log(self.entity_score_multiplier)
        debug_log(ScoreProbe.at(OTHER_ENTITY_INDEX).entity_score_multiplier)


class ScoreOther(PlayArchetype):
    name = "ScoreOther"


class RuntimeIdProbe(PlayArchetype):
    name = "RuntimeIdProbe"

    def preprocess(self):
        self.archetype_score_multiplier = 6.0

    def update_parallel(self):
        debug_log(ScoreOther.archetype_score_multiplier)
        debug_log(self.archetype_score_multiplier)


ARCHETYPES = [ScoreProbe, ScoreOther, RuntimeIdProbe]

# Ids are assigned in ARCHETYPES order, so these match every ModeContextState run_callbacks builds.
ARCHETYPE_IDS = ModeContextState(Mode.PLAY, ARCHETYPES).archetypes

# An archetype constructed outside a callback holds level data rather than an entity, and level data has no
# score multiplier to reach.
LEVEL_DATA_PROBE = ScoreProbe()


def run_callbacks(archetype: type[PlayArchetype], callbacks, entity_archetype_id: int | None = None) -> list[float]:
    """Compile and interpret each (method name, callback name) of `archetype` in order; return the debug log.

    `entity_archetype_id` seeds the interpreter's EntityInfo block the way the engine would for an entity of that
    archetype, which is what the descriptors reading `self.id` see. Every optimization level must produce the
    same log.
    """
    logs = []
    for passes in optimization_levels:
        clear_frontend_caches()
        project_state = ProjectContextState(runtime_checks=RuntimeChecks.NONE)
        mode_state = ModeContextState(Mode.PLAY, ARCHETYPES)
        interpreter = Interpreter()
        interpreter.blocks[PlayBlock.EngineRom] = project_state.rom.values
        if entity_archetype_id is not None:
            interpreter.set(PlayBlock.EntityInfo, ENTITY_INFO_ARCHETYPE_ID, entity_archetype_id)
        for method_name, callback_name in callbacks:
            cfg = callback_to_cfg(project_state, mode_state, getattr(archetype, method_name), callback_name, archetype)
            cfg = run_passes(cfg, passes, OptimizerConfig(mode=Mode.PLAY, callback=callback_name))
            interpreter.run(cfg_to_engine_node(cfg))
        logs.append(tuple(interpreter.log))
    assert len(set(logs)) == 1, f"Log differs between optimization levels: {logs}"
    return list(logs[0])


def compile_in_mode(fn, mode: Mode):
    clear_frontend_caches()
    project_state = ProjectContextState(runtime_checks=RuntimeChecks.NONE)
    return callback_to_cfg(project_state, ModeContextState(mode, ARCHETYPES), fn, "")


def test_score_multipliers_written_in_preprocess_are_readable_in_a_later_callback():
    # Four writes with four distinct values: any two of them sharing storage would collide here.
    assert run_callbacks(ScoreProbe, [("preprocess", "preprocess"), ("update_parallel", "updateParallel")]) == [
        2.0,
        3.0,
        4.0,
        5.0,
    ]


def test_instance_archetype_score_multiplier_follows_the_entity_runtime_archetype_id():
    # `id` on an entity is documented as its runtime archetype id even when reached through another type, so an
    # entity whose EntityInfo says ScoreOther writes and reads ScoreOther's multiplier.
    log = run_callbacks(
        RuntimeIdProbe,
        [("preprocess", "preprocess"), ("update_parallel", "updateParallel")],
        entity_archetype_id=ARCHETYPE_IDS[ScoreOther],
    )
    assert log == [6.0, 6.0]


def test_entity_score_multiplier_rejects_class_level_access():
    with pytest.raises(CompilationError, match="Entity score multiplier can only be accessed from an instance"):
        compile_in_mode(lambda: ScoreProbe.entity_score_multiplier, Mode.PLAY)


def test_entity_score_multiplier_rejects_class_level_assignment():
    def fn():
        ScoreProbe.entity_score_multiplier = 1.0

    with pytest.raises(
        CompilationError, match="Entity score multiplier can only be set on an instance, not on the class"
    ):
        compile_in_mode(fn, Mode.PLAY)


def test_entity_score_multiplier_rejects_level_data_access():
    with pytest.raises(CompilationError, match="Entity score multiplier is not available in level data"):
        compile_in_mode(lambda: LEVEL_DATA_PROBE.entity_score_multiplier, Mode.PLAY)


def test_entity_score_multiplier_rejects_level_data_assignment():
    def fn():
        LEVEL_DATA_PROBE.entity_score_multiplier = 1.0

    with pytest.raises(CompilationError, match="Entity score multiplier is not available in level data"):
        compile_in_mode(fn, Mode.PLAY)


@pytest.mark.parametrize("mode", [Mode.PREVIEW, Mode.TUTORIAL])
def test_archetype_score_multiplier_rejects_modes_without_scoring(mode):
    with pytest.raises(CompilationError, match=f"Archetype score multiplier is not available in mode '{mode.name}'"):
        compile_in_mode(lambda: ScoreProbe.archetype_score_multiplier, mode)


@pytest.mark.parametrize("mode", [Mode.PREVIEW, Mode.TUTORIAL])
def test_archetype_score_multiplier_assignment_rejects_modes_without_scoring(mode):
    def fn():
        ScoreProbe.archetype_score_multiplier = 1.0

    with pytest.raises(CompilationError, match=f"Archetype score multiplier is not available in mode '{mode.name}'"):
        compile_in_mode(fn, mode)


def test_score_multipliers_are_unavailable_outside_a_compile_context():
    with pytest.raises(RuntimeError, match="Archetype score multiplier is only available during compilation"):
        _ = ScoreProbe.archetype_score_multiplier
    with pytest.raises(RuntimeError, match="Archetype score multiplier is only available during compilation"):
        ScoreProbe.archetype_score_multiplier = 1.0
