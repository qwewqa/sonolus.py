"""Tests for `get_archetype_by_name`."""

import random

import pytest

from sonolus.backend.blocks import PlayBlock
from sonolus.backend.interpret import Interpreter
from sonolus.backend.mode import Mode
from sonolus.backend.optimize import OptimizerConfig, cfg_to_engine_node, run_passes
from sonolus.build.compile import callback_to_cfg
from sonolus.script.archetype import PlayArchetype, get_archetype_by_name
from sonolus.script.internal.context import ModeContextState, ProjectContextState, RuntimeChecks
from sonolus.script.internal.error import CompilationError
from sonolus.script.internal.visitor import clear_frontend_caches
from tests.script.conftest import optimization_levels, run_compiled


class _Lookup(PlayArchetype):
    name = "Lookup"


class _Other(PlayArchetype):
    name = "Other"


_ARCHETYPES = [_Lookup, _Other]


def _run_in_play(fn):
    """Compile `fn` as a play callback with the archetypes above registered and interpret it.

    The lookup reads the compile context's archetype table, so there is no plain-Python reference to run
    against; this follows the compile-and-interpret idiom of tests/script/test_runtime_predicates.py.
    """
    results = []
    for passes in optimization_levels:
        clear_frontend_caches()
        project_state = ProjectContextState(runtime_checks=RuntimeChecks.NONE)
        cfg = callback_to_cfg(project_state, ModeContextState(Mode.PLAY, _ARCHETYPES), fn, "")
        entry = cfg_to_engine_node(run_passes(cfg, passes, OptimizerConfig()))
        interpreter = Interpreter()
        interpreter.blocks[PlayBlock.EngineRom] = project_state.rom.values
        results.append(interpreter.run(entry))
    assert len(set(results)) == 1, f"Result differs between optimization levels: {results}"
    return results[0]


def test_lookup_returns_the_registered_archetype():
    # Ids are assigned in registration order, so _Other's id is 1; a lookup that ignored the name or the
    # table would return 0, the id of _Lookup.
    def fn():
        return get_archetype_by_name("Other").id

    assert _run_in_play(fn) == 1


def test_non_string_name_is_rejected():
    def fn():
        get_archetype_by_name(5)
        return 0.0

    with pytest.raises(CompilationError, match="Invalid name: '5'"):
        run_compiled(fn)


def test_runtime_valued_name_is_rejected():
    # A name only known at runtime cannot be looked up in the compile-time table. The message interpolates
    # an internal temp name for the value, which is deliberately not pinned here.
    def fn():
        get_archetype_by_name(random.randrange(0, 1) + 5)
        return 0.0

    with pytest.raises(CompilationError, match="Invalid name"):
        run_compiled(fn)


def test_unknown_archetype_name_reads_as_written():
    # The lookup raises a KeyError, whose str() is repr(args[0]) rather than the message, so wrapping it
    # unchanged would report the whole message inside a second pair of quotes.
    def fn():
        get_archetype_by_name("NoSuchArchetype")
        return 0.0

    with pytest.raises(CompilationError) as exc_info:
        run_compiled(fn)
    assert str(exc_info.value) == "Unknown archetype: 'NoSuchArchetype'"


def test_lookup_outside_compilation_raises():
    with pytest.raises(RuntimeError, match=r"Archetypes by name are only available during compilation\."):
        get_archetype_by_name("Lookup")
