"""Tests for the eleven environment-query ops (``BeatTo*``, ``TimeTo*``, ``Has*``) the oracle models.

Each is a pure single-argument query against a table that lives on the host, not in engine memory: the
level's bpm changes, its timescale changes, and which skin sprites / effect clips / particle effects the
running Sonolus install actually has. ``Interpreter`` answers them from per-instance tables a test may
configure, and an unconfigured instance answers with the identity mapping (60 bpm, timescale 1.0) and
reports everything available.

The reference bodies behind the public wrappers raise ``NotImplementedError`` (``timing.py`` and the
``is_available`` properties are declared with ``native_function``), so plain Python has no reference for
them and ``run_and_validate`` cannot be the oracle here. The first half of this module asserts the
interpreter's semantics on hand-built nodes; the second half compiles the public wrappers and interprets
the result, which is what pins each wrapper to the op it names.
"""

import pytest

from sonolus.backend.blocks import PlayBlock
from sonolus.backend.interpret import Interpreter
from sonolus.backend.mode import Mode
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
from sonolus.backend.place import BlockPlace
from sonolus.build.compile import callback_to_cfg
from sonolus.script.effect import Effect
from sonolus.script.internal.context import ModeContextState, ProjectContextState, RuntimeChecks
from sonolus.script.internal.meta_fn import meta_fn
from sonolus.script.internal.visitor import clear_frontend_caches, compile_and_call
from sonolus.script.num import Num
from sonolus.script.particle import Particle
from sonolus.script.sprite import Sprite
from sonolus.script.timing import (
    beat_to_bpm,
    beat_to_starting_beat,
    beat_to_starting_time,
    beat_to_time,
    time_to_scaled_time,
    time_to_starting_scaled_time,
    time_to_starting_time,
    time_to_timescale,
)

# 120 bpm for the first eight beats (half a second each), then 240 bpm (a quarter each). At beat 10 the
# four BeatTo* queries answer 240, 8, 4.0 and 4.5, which are pairwise distinct and distinct from every
# TimeTo* answer for the same argument, so a wrapper bound to the wrong op cannot pass by coincidence.
BPM_CHANGES = [(0.0, 120.0), (8.0, 240.0)]
PROBE_BEAT = 10.0

# Double speed until t=3 (six units of scaled time), then quarter speed. At t=7 the four TimeTo* queries
# answer 0.25, 3.0, 6.0 and 7.0, again pairwise distinct and distinct from every BeatTo* answer for 7.0.
TIMESCALE_CHANGES = [(0.0, 2.0), (3.0, 0.25)]
PROBE_TIME = 7.0

# One id per query so a wrapper bound to the wrong Has* op reports available where the test wants
# unavailable. Ids not listed stay available, which is the unconfigured default.
UNAVAILABLE = {
    (Op.HasSkinSprite, 1): False,
    (Op.HasEffectClip, 2): False,
    (Op.HasParticleEffect, 3): False,
}


def configured() -> Interpreter:
    """An interpreter carrying the tables above."""
    interpreter = Interpreter()
    interpreter.bpm_changes = list(BPM_CHANGES)
    interpreter.timescale_changes = list(TIMESCALE_CHANGES)
    interpreter.availability = dict(UNAVAILABLE)
    return interpreter


def run_op(op: Op, *args, interpreter: Interpreter | None = None) -> float:
    return (interpreter or Interpreter()).run(FunctionNode(op, tuple(args)))


def compile_and_interpret(fn, make_interpreter=configured) -> float:
    """Compile ``fn`` as a play-mode callback, interpret it, and return what it evaluated to.

    The result is written to place (-2, 0), the sentinel-block trick ``tests/script/conftest.py`` and
    ``tests/script/test_entity_refs.py`` use, and every optimization level must agree on it. Each level gets a
    fresh interpreter, so a level that failed to write the sentinel reads -1.0 rather than the last result.
    """
    results = []
    for passes in (MINIMAL_PASSES, FAST_PASSES, STANDARD_PASSES):
        clear_frontend_caches()
        project_state = ProjectContextState(runtime_checks=RuntimeChecks.NONE)
        mode_state = ModeContextState(Mode.PLAY)

        @meta_fn
        def wrapper(_fn=fn):
            Num._from_place_(BlockPlace(-2, 0))._set_(compile_and_call(_fn))
            return 0

        cfg = callback_to_cfg(project_state, mode_state, wrapper, "")
        entry = cfg_to_engine_node(run_passes(cfg, passes, OptimizerConfig(mode=Mode.PLAY)))
        interpreter = make_interpreter()
        interpreter.blocks[PlayBlock.EngineRom] = project_state.rom.values
        interpreter.run(entry)
        results.append(interpreter.get(-2, 0))
    assert len(set(results)) == 1, f"Result differs between optimization levels: {results}"
    return results[0]


@pytest.mark.parametrize("beat", [-4.0, 0.0, 1.5, 32.0])
def test_default_bpm_table_maps_every_beat_to_the_same_time(beat):
    # 60 bpm is one beat per second anchored at beat 0, so beat and time coincide.
    assert run_op(Op.BeatToTime, beat) == beat
    assert run_op(Op.BeatToBPM, beat) == 60.0
    assert run_op(Op.BeatToStartingBeat, beat) == 0.0
    assert run_op(Op.BeatToStartingTime, beat) == 0.0


@pytest.mark.parametrize("time", [-4.0, 0.0, 1.5, 32.0])
def test_default_timescale_table_leaves_time_unscaled(time):
    assert run_op(Op.TimeToScaledTime, time) == time
    assert run_op(Op.TimeToTimeScale, time) == 1.0
    assert run_op(Op.TimeToStartingTime, time) == 0.0
    assert run_op(Op.TimeToStartingScaledTime, time) == 0.0


@pytest.mark.parametrize("op", [Op.HasSkinSprite, Op.HasEffectClip, Op.HasParticleEffect])
@pytest.mark.parametrize("id_", [0.0, 7.0])
def test_everything_is_available_by_default(op, id_):
    assert run_op(op, id_) == 1.0


@pytest.mark.parametrize(
    ("beat", "expected_bpm", "expected_starting_beat", "expected_starting_time", "expected_time"),
    [
        # Within the first section: two beats at 120 bpm is one second.
        (2.0, 120.0, 0.0, 0.0, 1.0),
        # The change at beat 8 is the start of its own section: eight beats at 120 bpm is four seconds.
        (8.0, 240.0, 8.0, 4.0, 4.0),
        # Two beats past it at 240 bpm is another half second.
        (PROBE_BEAT, 240.0, 8.0, 4.0, 4.5),
        # Before the first marker the opening section keeps running backwards.
        (-2.0, 120.0, 0.0, 0.0, -1.0),
    ],
)
def test_bpm_table_sections(beat, expected_bpm, expected_starting_beat, expected_starting_time, expected_time):
    interpreter = configured()
    assert run_op(Op.BeatToBPM, beat, interpreter=interpreter) == expected_bpm
    assert run_op(Op.BeatToStartingBeat, beat, interpreter=interpreter) == expected_starting_beat
    assert run_op(Op.BeatToStartingTime, beat, interpreter=interpreter) == expected_starting_time
    assert run_op(Op.BeatToTime, beat, interpreter=interpreter) == expected_time


@pytest.mark.parametrize(
    ("time", "expected_scale", "expected_starting_time", "expected_starting_scaled", "expected_scaled"),
    [
        # Within the first section: one second at double speed is two units of scaled time.
        (1.0, 2.0, 0.0, 0.0, 2.0),
        # The change at t=3 starts its own section, three seconds at double speed in.
        (3.0, 0.25, 3.0, 6.0, 6.0),
        # Four seconds past it at quarter speed is one more unit.
        (PROBE_TIME, 0.25, 3.0, 6.0, 7.0),
        # Before the first marker the opening section keeps running backwards.
        (-1.0, 2.0, 0.0, 0.0, -2.0),
    ],
)
def test_timescale_table_sections(
    time, expected_scale, expected_starting_time, expected_starting_scaled, expected_scaled
):
    interpreter = configured()
    assert run_op(Op.TimeToTimeScale, time, interpreter=interpreter) == expected_scale
    assert run_op(Op.TimeToStartingTime, time, interpreter=interpreter) == expected_starting_time
    assert run_op(Op.TimeToStartingScaledTime, time, interpreter=interpreter) == expected_starting_scaled
    assert run_op(Op.TimeToScaledTime, time, interpreter=interpreter) == expected_scaled


@pytest.mark.parametrize("op", [Op.HasSkinSprite, Op.HasEffectClip, Op.HasParticleEffect])
@pytest.mark.parametrize("id_", [1.0, 2.0, 3.0])
def test_availability_table_is_keyed_by_op_and_id(op, id_):
    expected = 1.0 if UNAVAILABLE.get((op, int(id_)), True) else 0.0
    assert run_op(op, id_, interpreter=configured()) == expected


def test_tables_are_per_instance():
    interpreter = configured()
    assert run_op(Op.BeatToTime, PROBE_BEAT, interpreter=interpreter) == 4.5
    assert run_op(Op.HasSkinSprite, 1.0, interpreter=interpreter) == 0.0

    fresh = Interpreter()
    assert run_op(Op.BeatToTime, PROBE_BEAT, interpreter=fresh) == PROBE_BEAT
    assert run_op(Op.HasSkinSprite, 1.0, interpreter=fresh) == 1.0


@pytest.mark.parametrize(
    ("wrapper", "argument", "expected"),
    [
        pytest.param(beat_to_bpm, PROBE_BEAT, 240.0, id="beat_to_bpm"),
        pytest.param(beat_to_time, PROBE_BEAT, 4.5, id="beat_to_time"),
        pytest.param(beat_to_starting_beat, PROBE_BEAT, 8.0, id="beat_to_starting_beat"),
        pytest.param(beat_to_starting_time, PROBE_BEAT, 4.0, id="beat_to_starting_time"),
        pytest.param(time_to_timescale, PROBE_TIME, 0.25, id="time_to_timescale"),
        pytest.param(time_to_scaled_time, PROBE_TIME, 7.0, id="time_to_scaled_time"),
        pytest.param(time_to_starting_time, PROBE_TIME, 3.0, id="time_to_starting_time"),
        pytest.param(time_to_starting_scaled_time, PROBE_TIME, 6.0, id="time_to_starting_scaled_time"),
    ],
)
def test_timing_wrapper_queries_the_op_it_names(wrapper, argument, expected):
    assert compile_and_interpret(lambda: wrapper(argument)) == expected


@pytest.mark.parametrize("id_", [1, 2, 3])
@pytest.mark.parametrize(
    ("resource", "op"),
    [
        pytest.param(Sprite, Op.HasSkinSprite, id="sprite"),
        pytest.param(Effect, Op.HasEffectClip, id="effect"),
        pytest.param(Particle, Op.HasParticleEffect, id="particle"),
    ],
)
def test_is_available_queries_the_op_it_names(resource, op, id_):
    expected = 1.0 if UNAVAILABLE.get((op, id_), True) else 0.0
    assert compile_and_interpret(lambda: resource(id=id_).is_available) == expected
