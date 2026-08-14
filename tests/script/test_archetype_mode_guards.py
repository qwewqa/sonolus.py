"""Tests that the archetype mode guards name the mode that rejected the access.

Life, score multipliers, and entity info each exist in some modes and not others, so reaching one from a mode
that lacks it is a mistake the compiler has to report. The mode is the whole content of that report: an author
who wrote the access already knows which attribute they asked for, and only the message tells them which mode
they are compiling.
"""

import pytest

from sonolus.backend.mode import Mode
from sonolus.build.compile import callback_to_cfg
from sonolus.script.archetype import PlayArchetype, entity_info_at
from sonolus.script.internal.context import ModeContextState, ProjectContextState, RuntimeChecks
from sonolus.script.internal.error import CompilationError
from sonolus.script.internal.visitor import clear_frontend_caches


class GuardProbe(PlayArchetype):
    name = "GuardProbe"


ARCHETYPES = [GuardProbe]

# An archetype constructed outside a callback holds level data, which is all the instance accessors need: every
# guard here fires before the entity behind the instance is looked at.
PROBE = GuardProbe()


def read_archetype_life():
    return GuardProbe.life


def read_entity_life():
    return PROBE.entity_life


def read_class_archetype_score_multiplier():
    return GuardProbe.archetype_score_multiplier


def write_class_archetype_score_multiplier():
    GuardProbe.archetype_score_multiplier = 1.0


def read_instance_archetype_score_multiplier():
    return PROBE.archetype_score_multiplier


def write_instance_archetype_score_multiplier():
    PROBE.archetype_score_multiplier = 1.0


def write_archetype_life_with_builtin_setattr():
    setattr(PROBE, "archetype_life", 1)  # noqa: B010


def read_entity_score_multiplier():
    return PROBE.entity_score_multiplier


def write_entity_score_multiplier():
    PROBE.entity_score_multiplier = 1.0


MODE_GUARDED_ACCESSES = [
    (read_archetype_life, "Archetype life"),
    (read_entity_life, "Entity life"),
    (read_class_archetype_score_multiplier, "Archetype score multiplier"),
    (write_class_archetype_score_multiplier, "Archetype score multiplier"),
    (read_instance_archetype_score_multiplier, "Archetype score multiplier"),
    (write_instance_archetype_score_multiplier, "Archetype score multiplier"),
    (read_entity_score_multiplier, "Entity score multiplier"),
    (write_entity_score_multiplier, "Entity score multiplier"),
]


def compile_in_mode(fn, mode: Mode):
    clear_frontend_caches()
    project_state = ProjectContextState(runtime_checks=RuntimeChecks.NONE)
    return callback_to_cfg(project_state, ModeContextState(mode, ARCHETYPES), fn, "")


@pytest.mark.parametrize("mode", [Mode.PREVIEW, Mode.TUTORIAL], ids=lambda mode: mode.name)
@pytest.mark.parametrize(("access", "subject"), MODE_GUARDED_ACCESSES, ids=lambda value: getattr(value, "__name__", ""))
def test_a_mode_guard_names_the_rejecting_mode(access, subject: str, mode: Mode):
    with pytest.raises(CompilationError, match=f"{subject} is not available in mode '{mode.name}'"):
        compile_in_mode(access, mode)


def test_builtin_setattr_preserves_the_archetype_life_read_only_error():
    with pytest.raises(CompilationError, match="Archetype life is read-only and cannot be set"):
        compile_in_mode(write_archetype_life_with_builtin_setattr, Mode.PLAY)


def read_entity_info():
    return entity_info_at(0).index


# Entity info exists in play, watch, and preview mode, so tutorial is the one mode this guard rejects and it does
# not fit the table above.
def test_the_entity_info_guard_names_the_rejecting_mode():
    with pytest.raises(CompilationError, match="Entity info is not available in mode 'TUTORIAL'"):
        compile_in_mode(read_entity_info, Mode.TUTORIAL)


def spawn_probe():
    GuardProbe.spawn()


# Only play and watch have a spawning system, and only they have an entity memory block for the injected values
# to land in. The guard is on the call rather than on the block, since spawn packs its values into the op's
# arguments instead of dereferencing entity memory.
@pytest.mark.parametrize("mode", [Mode.PREVIEW, Mode.TUTORIAL], ids=lambda mode: mode.name)
def test_the_spawn_guard_names_the_rejecting_mode(mode: Mode):
    with pytest.raises(CompilationError, match=f"GuardProbe.spawn is not available in '{mode.name}' mode"):
        compile_in_mode(spawn_probe, mode)


@pytest.mark.parametrize("mode", [Mode.PLAY, Mode.WATCH], ids=lambda mode: mode.name)
def test_spawn_compiles_in_play_and_watch(mode: Mode):
    compile_in_mode(spawn_probe, mode)
