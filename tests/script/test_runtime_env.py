"""Tests for the runtime.py environment and global accessors.

These members read or write a mode's runtime blocks, which only exist inside a compile context, so plain
Python gives no reference and neither conftest oracle fits: each test compiles a callback in the mode under
test, seeds the block, and interprets the result, following tests/script/test_runtime_predicates.py.

Where field order matters, block slots receive distinct values, so an accessor reading the wrong slot returns a
different result. The expectations follow the published block layouts.
"""

import pytest

from sonolus.backend.blocks import PlayBlock, PreviewBlock, TutorialBlock, WatchBlock
from sonolus.backend.interpret import Interpreter
from sonolus.backend.mode import Mode
from sonolus.backend.optimize import OptimizerConfig, cfg_to_engine_node, run_passes
from sonolus.build.compile import callback_to_cfg
from sonolus.script.debug import debug_log
from sonolus.script.internal.context import ModeContextState, ProjectContextState, RuntimeChecks
from sonolus.script.internal.visitor import clear_frontend_caches
from sonolus.script.quad import Quad
from sonolus.script.runtime import (
    audio_offset,
    background,
    is_debug,
    is_multiplayer,
    is_skip,
    level_life,
    particle_transform,
    runtime_ui,
    safe_area,
    set_background,
    set_particle_transform,
    set_skin_transform,
    skin_transform,
)
from sonolus.script.transform import Transform2d
from sonolus.script.vec import Vec2
from tests.script.conftest import optimization_levels

_BLOCKS = {
    Mode.PLAY: PlayBlock,
    Mode.WATCH: WatchBlock,
    Mode.PREVIEW: PreviewBlock,
    Mode.TUTORIAL: TutorialBlock,
}

_ALL_MODES = [Mode.PLAY, Mode.WATCH, Mode.PREVIEW, Mode.TUTORIAL]

# Field order of each mode's RuntimeEnvironment declaration in runtime.py, safe-area fields last.
_ENV_FIELDS = {
    Mode.PLAY: ["is_debug", "aspect_ratio", "audio_offset", "input_offset", "is_multiplayer"],
    Mode.WATCH: ["is_debug", "aspect_ratio", "audio_offset", "input_offset", "is_replay"],
    Mode.PREVIEW: ["is_debug", "aspect_ratio"],
    Mode.TUTORIAL: ["is_debug", "aspect_ratio", "audio_offset"],
}
_SAFE_AREA_FIELDS = ["safe_area_x_min", "safe_area_x_max", "safe_area_y_min", "safe_area_y_max"]


def _env_seed(mode):
    """Distinct value per environment field, 100 + index, so a transposed read shows in the number itself."""
    names = _ENV_FIELDS[mode] + _SAFE_AREA_FIELDS
    return {name: 100.0 + i for i, name in enumerate(names)}


def _run(fn, mode, *, callback="preprocess", blocks=None, observe=None):
    """Compile `fn` in `mode`, interpret with `blocks` seeded, and return (log, observed block cells).

    `preprocess` is the one callback name every mode has, and it is in RuntimeEnvironment's readable set and
    the transforms' and LevelLife's writable sets. Runs at every optimization level and asserts they agree.
    """
    per_level = []
    for passes in optimization_levels:
        clear_frontend_caches()
        project_state = ProjectContextState(runtime_checks=RuntimeChecks.NONE)
        cfg = callback_to_cfg(project_state, ModeContextState(mode), fn, callback)
        entry = cfg_to_engine_node(run_passes(cfg, passes, OptimizerConfig()))
        interpreter = Interpreter()
        interpreter.blocks[int(_BLOCKS[mode].EngineRom)] = project_state.rom.values
        for block, values in (blocks or {}).items():
            interpreter.blocks[int(block)] = list(values)
        interpreter.run(entry)
        observed = list(interpreter.blocks[int(observe)]) if observe is not None else None
        per_level.append((interpreter.log, observed))
    assert all(entry == per_level[0] for entry in per_level), f"Levels disagree: {per_level}"
    return per_level[0]


def _log_safe_area():
    rect = safe_area()
    debug_log(rect.t)
    debug_log(rect.r)
    debug_log(rect.b)
    debug_log(rect.l)


@pytest.mark.parametrize("mode", _ALL_MODES, ids=lambda mode: mode.name)
def test_safe_area_reads_its_own_modes_fields(mode):
    seed = _env_seed(mode)
    log, _ = _run(_log_safe_area, mode, blocks={_BLOCKS[mode].RuntimeEnvironment: seed.values()})
    assert log == [seed["safe_area_y_max"], seed["safe_area_x_max"], seed["safe_area_y_min"], seed["safe_area_x_min"]]


def _log_env_queries():
    debug_log(is_debug())
    debug_log(audio_offset())
    debug_log(is_multiplayer())


@pytest.mark.parametrize("mode", _ALL_MODES, ids=lambda mode: mode.name)
def test_env_queries_dispatch_on_the_mode(mode):
    seed = _env_seed(mode)
    log, _ = _run(_log_env_queries, mode, blocks={_BLOCKS[mode].RuntimeEnvironment: seed.values()})
    assert log == [
        seed["is_debug"],
        # audio_offset is documented to return 0 in preview mode; is_multiplayer to return False outside play.
        seed.get("audio_offset", 0.0),
        seed["is_multiplayer"] if mode is Mode.PLAY else 0.0,
    ]


def _log_is_skip():
    debug_log(is_skip())


@pytest.mark.parametrize(
    ("mode", "runtime_update", "expected"),
    [
        pytest.param(Mode.PLAY, [100.0, 101.0, 102.0, 103.0, 104.0], 104.0, id="play"),
        pytest.param(Mode.WATCH, [200.0, 201.0, 202.0, 203.0], 203.0, id="watch"),
        pytest.param(Mode.PREVIEW, None, 0.0, id="preview"),
        pytest.param(Mode.TUTORIAL, None, 0.0, id="tutorial"),
    ],
)
def test_is_skip_reads_the_modes_runtime_update_field(mode, runtime_update, expected):
    blocks = {} if runtime_update is None else {_BLOCKS[mode].RuntimeUpdate: runtime_update}
    log, _ = _run(_log_is_skip, mode, blocks=blocks)
    assert log == [expected]


# The transforms project the runtime 4x4 onto a 3x3 using rows and columns 0, 1, and 3, so with cells seeded
# 0..15 the nine components read [0, 1, 3, 4, 5, 7, 12, 13, 15] in row-major order.
_SEEDED_4X4 = [float(i) for i in range(16)]
_PROJECTED_3X3 = [0.0, 1.0, 3.0, 4.0, 5.0, 7.0, 12.0, 13.0, 15.0]


def _log_skin_transform():
    _log_transform(skin_transform())


def _log_particle_transform():
    _log_transform(particle_transform())


def _log_transform(t: Transform2d):
    debug_log(t.a00)
    debug_log(t.a01)
    debug_log(t.a02)
    debug_log(t.a10)
    debug_log(t.a11)
    debug_log(t.a12)
    debug_log(t.a20)
    debug_log(t.a21)
    debug_log(t.a22)


_TRANSFORMS = [
    pytest.param(_log_skin_transform, PlayBlock.RuntimeSkinTransform, id="skin"),
    pytest.param(_log_particle_transform, PlayBlock.RuntimeParticleTransform, id="particle"),
]


@pytest.mark.parametrize(("fn", "block"), _TRANSFORMS)
def test_transform_read_projects_the_4x4_onto_a_3x3(fn, block):
    log, _ = _run(fn, Mode.PLAY, blocks={block: _SEEDED_4X4})
    assert log == _PROJECTED_3X3


def _write_skin_transform():
    set_skin_transform(Transform2d(1, 2, 3, 4, 5, 6, 7, 8, 9))


def _write_particle_transform():
    set_particle_transform(Transform2d(1, 2, 3, 4, 5, 6, 7, 8, 9))


_TRANSFORM_WRITES = [
    pytest.param(_write_skin_transform, PlayBlock.RuntimeSkinTransform, id="skin"),
    pytest.param(_write_particle_transform, PlayBlock.RuntimeParticleTransform, id="particle"),
]


@pytest.mark.parametrize(("fn", "block"), _TRANSFORM_WRITES)
def test_transform_write_targets_the_nine_projected_cells(fn, block):
    # Seeding every cell 100 + i pins the other half of the projection: the seven cells outside rows and
    # columns 0, 1, and 3 must survive the write untouched.
    seeded = [100.0 + i for i in range(16)]
    _, cells = _run(fn, Mode.PLAY, blocks={block: seeded}, observe=block)
    assert cells == [
        1.0,
        2.0,
        102.0,
        3.0,
        4.0,
        5.0,
        106.0,
        6.0,
        108.0,
        109.0,
        110.0,
        111.0,
        7.0,
        8.0,
        114.0,
        9.0,
    ]


def _write_level_life_all():
    level_life().update(
        consecutive_perfect_increment=1,
        consecutive_perfect_step=2,
        consecutive_great_increment=3,
        consecutive_great_step=4,
        consecutive_good_increment=5,
        consecutive_good_step=6,
        initial=7,
        maximum=8,
    )


def test_level_life_update_maps_each_keyword_to_its_slot():
    # The eight keywords land on the eight LevelLife slots in declaration order.
    _, cells = _run(
        _write_level_life_all,
        Mode.PLAY,
        blocks={PlayBlock.LevelLife: [0.0] * 8},
        observe=PlayBlock.LevelLife,
    )
    assert cells == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]


def _write_level_life_one():
    level_life().update(consecutive_great_step=42)


def test_level_life_update_with_one_keyword_writes_only_its_slot():
    _, cells = _run(
        _write_level_life_one,
        Mode.PLAY,
        blocks={PlayBlock.LevelLife: [0.0] * 8},
        observe=PlayBlock.LevelLife,
    )
    assert cells == [0.0, 0.0, 0.0, 42.0, 0.0, 0.0, 0.0, 0.0]


def _log_background():
    quad = background()
    debug_log(quad.bl.x)
    debug_log(quad.bl.y)
    debug_log(quad.tl.x)
    debug_log(quad.tl.y)
    debug_log(quad.tr.x)
    debug_log(quad.tr.y)
    debug_log(quad.br.x)
    debug_log(quad.br.y)


def test_background_reads_the_quad_corners_in_declaration_order():
    log, _ = _run(_log_background, Mode.PLAY, blocks={PlayBlock.RuntimeBackground: [float(i) for i in range(8)]})
    assert log == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]


def _write_background():
    set_background(Quad(bl=Vec2(1, 2), tl=Vec2(3, 4), tr=Vec2(5, 6), br=Vec2(7, 8)))


def test_set_background_writes_the_quad_corners_in_declaration_order():
    _, cells = _run(
        _write_background,
        Mode.PLAY,
        blocks={PlayBlock.RuntimeBackground: [0.0] * 8},
        observe=PlayBlock.RuntimeBackground,
    )
    assert cells == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]


def _log_judgment_config_availability():
    debug_log(runtime_ui().judgment_config.is_available)


@pytest.mark.parametrize("mode", _ALL_MODES, ids=lambda mode: mode.name)
def test_ui_config_is_available_follows_the_mode(mode):
    # UiConfig.is_available, distinct from UiLayout.is_available, which test_mode_guards.py covers:
    # judgment_config exists in play and watch mode only.
    log, _ = _run(_log_judgment_config_availability, mode)
    assert log == [1.0 if mode in {Mode.PLAY, Mode.WATCH} else 0.0]
