"""Tests for the compile-time mode guards on mode-restricted APIs.

The Sonolus engine spec restricts several functions to particular modes: `add_life_scheduled` to the preprocess
callback of play and watch, `print_number` to preview, `InstructionIcon.paint` to tutorial, and effect/particle
playback to the three modes whose payload declares those resources (preview declares neither). Both the mode and
the callback are fixed at build time, so a call in the wrong one is unconditionally wrong and is rejected while
compiling.

Neither oracle helper fits: `tests/script/conftest.py` always compiles at `Mode.PLAY`, so a snippet that must be
compiled in another mode can go through neither `run_and_validate` nor `run_compiled`, and these APIs emit runtime
ops rather than returning anything plain Python could reproduce. These tests build their own compile context with
`callback_to_cfg`, as `tests/script/test_runtime_predicates.py` and `tests/script/test_stream.py` already do.
"""

import pytest

from sonolus.backend.mode import Mode
from sonolus.backend.node import FunctionNode
from sonolus.backend.optimize import STANDARD_PASSES, OptimizerConfig, cfg_to_engine_node, run_passes
from sonolus.build.compile import callback_to_cfg
from sonolus.script.debug import debug_log, simulation_context
from sonolus.script.effect import Effect, LoopedEffectHandle, ScheduledLoopedEffectHandle
from sonolus.script.instruction import Instruction, InstructionIcon, clear_instruction, show_instruction
from sonolus.script.internal.callbacks import PLAY_CALLBACKS, WATCH_ARCHETYPE_CALLBACKS, WATCH_GLOBAL_CALLBACKS
from sonolus.script.internal.context import ModeContextState, ProjectContextState, RuntimeChecks
from sonolus.script.internal.error import CompilationError
from sonolus.script.internal.visitor import clear_frontend_caches
from sonolus.script.particle import Particle, ParticleHandle
from sonolus.script.printing import PrintFormat, print_number
from sonolus.script.quad import Quad
from sonolus.script.runtime import RuntimeUi, add_life_scheduled, is_play, is_preview, is_watch, runtime_ui
from sonolus.script.vec import Vec2

ALL_MODES = [Mode.PLAY, Mode.WATCH, Mode.PREVIEW, Mode.TUTORIAL]

# The callback each mode compiles its per-frame work in.
MODE_CALLBACKS = {
    Mode.PLAY: "updateSequential",
    Mode.WATCH: "updateSequential",
    Mode.PREVIEW: "render",
    Mode.TUTORIAL: "update",
}


def compile_in_mode(fn, mode: Mode, callback: str | None = None):
    """Compile `fn` as a callback of `mode` and return the emitted node tree.

    The frontend cache is keyed on the function, so it has to be cleared between modes: without that, the same
    snippet compiled in a second mode can reuse the first mode's trace and never reach the guard under test.

    An empty `callback` is passed through rather than defaulted, since a context with no callback name is a
    case of its own.
    """
    clear_frontend_caches()
    callback = MODE_CALLBACKS[mode] if callback is None else callback
    project_state = ProjectContextState(runtime_checks=RuntimeChecks.NONE)
    config = OptimizerConfig(mode=mode, callback=callback)
    cfg = callback_to_cfg(project_state, ModeContextState(mode), fn, callback)
    return cfg_to_engine_node(run_passes(cfg, STANDARD_PASSES, config), config)


def op_names(node) -> list[str]:
    """Return the names of every op in the emitted node tree, in traversal order."""
    if not isinstance(node, FunctionNode):
        return []
    return [node.func.name, *(name for arg in node.args for name in op_names(arg))]


def debug_log_args(node) -> list[float]:
    """Return the constant argument of every DebugLog in the emitted node tree, in traversal order."""
    if not isinstance(node, FunctionNode):
        return []
    result = []
    if node.func.name == "DebugLog" and not isinstance(node.args[0], FunctionNode):
        result.append(node.args[0])
    result.extend(arg_value for arg in node.args for arg_value in debug_log_args(arg))
    return result


# add_life_scheduled: play and watch only.


def add_life_between_logs():
    debug_log(111)
    add_life_scheduled(1, 0.0)
    debug_log(222)


def add_life_behind_mode_check():
    debug_log(111)
    if is_play() or is_watch():
        add_life_scheduled(1, 0.0)
    debug_log(222)


# Every callback of play and watch other than preprocess, discovered rather than listed so a new one is covered
# automatically. spawnOrder, spawnTime, and despawnTime belong to the preprocessing stage without being the
# preprocess callback, so the spec's clause excludes them too.
NON_PREPROCESS_CALLBACKS = [
    (mode, info.name)
    for mode, infos in [
        (Mode.PLAY, PLAY_CALLBACKS.values()),
        (Mode.WATCH, [*WATCH_ARCHETYPE_CALLBACKS.values(), *WATCH_GLOBAL_CALLBACKS.values()]),
    ]
    for info in infos
    if info.name != "preprocess"
]


def test_non_preprocess_callbacks_are_discovered():
    # An empty list would parametrize away to nothing, and spawnOrder, spawnTime, and despawnTime are what
    # separate the preprocess callback from the preprocessing stage.
    names = {callback for _, callback in NON_PREPROCESS_CALLBACKS}
    assert {"spawnOrder", "spawnTime", "despawnTime", "updateSequential", "terminate"} <= names
    assert "preprocess" not in names


@pytest.mark.parametrize("mode", [Mode.PREVIEW, Mode.TUTORIAL], ids=lambda mode: mode.name)
def test_add_life_scheduled_rejected_outside_play_and_watch(mode):
    with pytest.raises(CompilationError, match="add_life_scheduled is only available in play and watch mode"):
        compile_in_mode(add_life_between_logs, mode)


@pytest.mark.parametrize(("mode", "callback"), NON_PREPROCESS_CALLBACKS, ids=lambda arg: getattr(arg, "name", arg))
def test_add_life_scheduled_rejected_outside_preprocess(mode, callback):
    with pytest.raises(CompilationError, match="add_life_scheduled is only available in the preprocess callback"):
        compile_in_mode(add_life_between_logs, mode, callback)


@pytest.mark.parametrize("mode", [Mode.PLAY, Mode.WATCH], ids=lambda mode: mode.name)
def test_add_life_scheduled_rejected_in_a_context_with_no_callback(mode):
    # The oracle helpers of tests/script/conftest.py and the default of debug.visualize_cfg compile with no
    # callback name, which is not the preprocess callback either.
    with pytest.raises(CompilationError, match="add_life_scheduled is only available in the preprocess callback"):
        compile_in_mode(add_life_between_logs, mode, "")


@pytest.mark.parametrize("mode", [Mode.PLAY, Mode.WATCH], ids=lambda mode: mode.name)
def test_add_life_scheduled_compiles_in_play_and_watch(mode):
    tree = compile_in_mode(add_life_between_logs, mode, "preprocess")
    assert "AddLifeScheduled" in op_names(tree)
    # The logs straddle the call so that truncation from the call site would show up here.
    assert debug_log_args(tree) == [111, 222]


@pytest.mark.parametrize("mode", ALL_MODES, ids=lambda mode: mode.name)
def test_add_life_scheduled_behind_a_compile_time_mode_check_compiles_in_every_mode(mode):
    # The documented way to share a helper across modes: a compile-time-false branch is never traced, so the
    # guard cannot fire inside one. Every mode has a preprocess callback, so the same snippet compiles as one.
    tree = compile_in_mode(add_life_behind_mode_check, mode, "preprocess")
    assert ("AddLifeScheduled" in op_names(tree)) == (mode in {Mode.PLAY, Mode.WATCH})
    assert debug_log_args(tree) == [111, 222]


# print_number: preview only.


def print_a_number():
    print_number(1, fmt=PrintFormat.NUMBER, anchor=Vec2(0, 0), pivot=Vec2(0, 0), dimensions=Vec2(1, 1))


@pytest.mark.parametrize("mode", [Mode.PLAY, Mode.WATCH, Mode.TUTORIAL], ids=lambda mode: mode.name)
def test_print_number_rejected_outside_preview(mode):
    with pytest.raises(CompilationError, match=f"print_number is not available in '{mode.name}' mode"):
        compile_in_mode(print_a_number, mode)


def test_print_number_compiles_in_preview():
    assert "Print" in op_names(compile_in_mode(print_a_number, Mode.PREVIEW))


# InstructionIcon.paint: tutorial only.


def paint_an_icon():
    InstructionIcon(0).paint(Vec2(0, 0), 1.0, 0.0, 0.0, 1.0)


@pytest.mark.parametrize("mode", [Mode.PLAY, Mode.WATCH, Mode.PREVIEW], ids=lambda mode: mode.name)
def test_paint_rejected_outside_tutorial(mode):
    with pytest.raises(CompilationError, match=f"InstructionIcon.paint is not available in '{mode.name}' mode"):
        compile_in_mode(paint_an_icon, mode)


def test_paint_compiles_in_tutorial():
    assert "Paint" in op_names(compile_in_mode(paint_an_icon, Mode.TUTORIAL))


def show_an_instruction():
    show_instruction(Instruction(0))


def show_an_instruction_through_the_instance_method():
    Instruction(0).show()


def clear_an_instruction():
    clear_instruction()


@pytest.mark.parametrize(
    "fn", [show_an_instruction, show_an_instruction_through_the_instance_method, clear_an_instruction]
)
@pytest.mark.parametrize("mode", [Mode.PLAY, Mode.WATCH, Mode.PREVIEW], ids=lambda mode: mode.name)
def test_instruction_text_apis_are_rejected_outside_tutorial(fn, mode):
    with pytest.raises(CompilationError, match="Instruction text is only available in tutorial mode"):
        compile_in_mode(fn, mode)


@pytest.mark.parametrize(
    "fn", [show_an_instruction, show_an_instruction_through_the_instance_method, clear_an_instruction]
)
def test_instruction_text_apis_compile_in_tutorial(fn):
    assert "Set" in op_names(compile_in_mode(fn, Mode.TUTORIAL))


def test_instruction_text_apis_work_in_a_simulation_context():
    with simulation_context():
        show_instruction(Instruction(0))
        Instruction(0).show()
        clear_instruction()


# Effects and particles: every mode except preview, whose payload declares neither resource.


def play_effect():
    Effect(0).play(0.0)


def schedule_effect():
    Effect(0).schedule(1.0)


def loop_effect():
    Effect(0).loop()


def schedule_loop_effect():
    Effect(0).schedule_loop(1.0)


def stop_looped_effect():
    LoopedEffectHandle(0).stop()


def stop_scheduled_looped_effect():
    ScheduledLoopedEffectHandle(0).stop(1.0)


def spawn_particle():
    Particle(0).spawn(Quad(Vec2(0, 0), Vec2(0, 1), Vec2(1, 1), Vec2(1, 0)), 1.0)


def move_particle():
    ParticleHandle(0).move(Quad(Vec2(0, 0), Vec2(0, 1), Vec2(1, 1), Vec2(1, 0)))


def destroy_particle():
    ParticleHandle(0).destroy()


EFFECT_MESSAGE = "Effect playback is not available in 'PREVIEW' mode"
PARTICLE_MESSAGE = "Particle effects are not available in 'PREVIEW' mode"

# Snippet, the op it emits outside preview, and the message its guard raises in preview.
PLAYBACK_SNIPPETS = {
    "effect_play": (play_effect, "Play", EFFECT_MESSAGE),
    "effect_schedule": (schedule_effect, "PlayScheduled", EFFECT_MESSAGE),
    "effect_loop": (loop_effect, "PlayLooped", EFFECT_MESSAGE),
    "effect_schedule_loop": (schedule_loop_effect, "PlayLoopedScheduled", EFFECT_MESSAGE),
    "effect_stop_looped": (stop_looped_effect, "StopLooped", EFFECT_MESSAGE),
    "effect_stop_looped_scheduled": (stop_scheduled_looped_effect, "StopLoopedScheduled", EFFECT_MESSAGE),
    "particle_spawn": (spawn_particle, "SpawnParticleEffect", PARTICLE_MESSAGE),
    "particle_move": (move_particle, "MoveParticleEffect", PARTICLE_MESSAGE),
    "particle_destroy": (destroy_particle, "DestroyParticleEffect", PARTICLE_MESSAGE),
}


@pytest.mark.parametrize("snippet", list(PLAYBACK_SNIPPETS))
def test_effect_and_particle_playback_rejected_in_preview(snippet):
    fn, _, message = PLAYBACK_SNIPPETS[snippet]
    with pytest.raises(CompilationError, match=message):
        compile_in_mode(fn, Mode.PREVIEW)


@pytest.mark.parametrize("mode", [Mode.PLAY, Mode.WATCH, Mode.TUTORIAL], ids=lambda mode: mode.name)
@pytest.mark.parametrize("snippet", list(PLAYBACK_SNIPPETS))
def test_effect_and_particle_playback_compiles_outside_preview(snippet, mode):
    fn, op, _ = PLAYBACK_SNIPPETS[snippet]
    assert op in op_names(compile_in_mode(fn, mode))


def log_effect_availability():
    debug_log(Effect(0).is_available)


def log_particle_availability():
    debug_log(Particle(0).is_available)


@pytest.mark.parametrize(
    ("fn", "op"),
    [(log_effect_availability, "HasEffectClip"), (log_particle_availability, "HasParticleEffect")],
    ids=["effect", "particle"],
)
@pytest.mark.parametrize("mode", ALL_MODES, ids=lambda mode: mode.name)
def test_availability_checks_compile_in_every_mode(fn, op, mode):
    # The availability checks are deliberately left unguarded: they emit no playback op, so rejecting them would
    # only move the error from the offending call to the check in front of it.
    assert op in op_names(compile_in_mode(fn, mode))


def play_effect_outside_preview():
    if not is_preview():
        Effect(0).play(0.0)
    debug_log(222)


@pytest.mark.parametrize("mode", ALL_MODES, ids=lambda mode: mode.name)
def test_playback_behind_a_compile_time_mode_check_compiles_in_every_mode(mode):
    tree = compile_in_mode(play_effect_outside_preview, mode)
    assert ("Play" in op_names(tree)) == (mode is not Mode.PREVIEW)
    assert debug_log_args(tree) == [222]


# RuntimeUi properties outside a compilation context.

RUNTIME_UI_PROPERTIES = sorted(name for name, member in vars(RuntimeUi).items() if isinstance(member, property))


def test_runtime_ui_properties_are_discovered():
    # The properties are discovered rather than listed so a new one is covered automatically, which is worth a
    # check that the discovery found the documented ones: an empty list would parametrize away to nothing.
    assert {"menu", "menu_config", "judgment", "progress_graph", "instruction"} <= set(RUNTIME_UI_PROPERTIES)


@pytest.mark.parametrize("name", RUNTIME_UI_PROPERTIES)
def test_runtime_ui_property_outside_compilation(name):
    with pytest.raises(RuntimeError, match="Runtime UI access outside of compilation"):
        getattr(runtime_ui(), name)


@pytest.mark.parametrize("name", RUNTIME_UI_PROPERTIES)
def test_runtime_ui_property_inside_simulation_context(name):
    # simulation_context() backs globals with simulated storage but sets up no compilation context, so there is
    # still no mode to dispatch on.
    with simulation_context(), pytest.raises(RuntimeError, match="Runtime UI access outside of compilation"):
        getattr(runtime_ui(), name)


def log_ui_availability():
    debug_log(runtime_ui().menu.is_available)
    debug_log(runtime_ui().judgment.is_available)
    debug_log(runtime_ui().progress_graph.is_available)
    debug_log(runtime_ui().instruction.is_available)


@pytest.mark.parametrize("mode", ALL_MODES, ids=lambda mode: mode.name)
def test_runtime_ui_availability_per_mode_is_unchanged(mode):
    # The per-property mode fallthrough is what feeds is_available, so the ctx() guard must not touch it: menu is
    # available in every mode, judgment in play and watch, progress_graph in watch, instruction in tutorial.
    tree = compile_in_mode(log_ui_availability, mode)
    assert debug_log_args(tree) == [
        1,
        1 if mode in {Mode.PLAY, Mode.WATCH} else 0,
        1 if mode is Mode.WATCH else 0,
        1 if mode is Mode.TUTORIAL else 0,
    ]


def update_ui_menu():
    runtime_ui().menu.update(alpha=0.5)


@pytest.mark.parametrize("mode", ALL_MODES, ids=lambda mode: mode.name)
def test_runtime_ui_update_still_writes_in_every_mode(mode):
    # The menu layout is available in every mode, and the RuntimeUI block is writable in preprocess, which is
    # where pydori lays its UI out.
    assert "Set" in op_names(compile_in_mode(update_ui_menu, mode, "preprocess"))
