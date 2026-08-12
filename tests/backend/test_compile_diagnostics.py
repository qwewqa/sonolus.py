"""Tests for the diagnostic the compile driver wraps an optimizer or emit failure in.

`compile_mode.optimize_cfg` in `sonolus/backend/_opt/driver.pyx` builds it. Nothing on the optimizer path
carries a source location, and `print_simple_traceback` finds no user frame to filter the traceback down to,
so this string is the only thing telling an engine author which callback of which archetype the build gave up
on.
"""

import pytest

from sonolus.backend.mode import Mode
from sonolus.backend.optimize import FAST_PASSES, MINIMAL_PASSES, STANDARD_PASSES
from sonolus.build.compile import compile_mode
from sonolus.script.archetype import PlayArchetype
from sonolus.script.debug import debug_log
from sonolus.script.internal.callbacks import update_callback
from sonolus.script.internal.context import ProjectContextState, RuntimeChecks
from sonolus.script.internal.error import CompilationError


def _project_state():
    # No runtime checks: a terminate check would add the exit edge the rejection is about.
    return ProjectContextState(runtime_checks=RuntimeChecks.NONE)


@pytest.mark.parametrize("level", [FAST_PASSES, STANDARD_PASSES])
def test_a_failing_archetype_callback_is_located_by_archetype_callback_and_mode(level):
    class Fine(PlayArchetype):
        name = "Fine"

        def update_sequential(self):
            debug_log(2)

    class Looper(PlayArchetype):
        name = "Looper"

        def update_sequential(self):
            while True:
                debug_log(1)

    with pytest.raises(CompilationError) as exc_info:
        compile_mode(
            mode=Mode.PLAY,
            project_state=_project_state(),
            archetypes=[Fine, Looper],
            global_callbacks=None,
            level=level,
        )

    message = str(exc_info.value)
    assert "callback 'updateSequential'" in message
    assert "archetype 'Looper'" in message
    assert "PLAY mode" in message
    # Both archetypes define updateSequential, so naming the callback alone does not identify the offender:
    # the innocent one must not appear.
    assert "archetype 'Fine'" not in message
    assert "Never terminates" in message


def test_a_failing_global_callback_is_located_without_an_archetype_clause():
    def update():
        while True:
            debug_log(1)

    with pytest.raises(CompilationError) as exc_info:
        compile_mode(
            mode=Mode.TUTORIAL,
            project_state=_project_state(),
            archetypes=None,
            global_callbacks=[(update_callback, update)],
            level=STANDARD_PASSES,
        )

    message = str(exc_info.value)
    assert "callback 'update'" in message
    assert "TUTORIAL mode" in message
    # A global callback belongs to no archetype, so an archetype clause here would name the wrong thing.
    assert "archetype" not in message
    assert "Never terminates" in message


def test_a_non_terminating_callback_is_still_packaged_at_the_minimal_level():
    # The rejection above fires inside `compute_liveness`, which the minimal level does not run, so -O0
    # packages the callback that -O1 and -O2 reject. That split is deliberate, and this is the only test of
    # its accepting half: without it, moving liveness earlier would flip -O0 to rejecting unnoticed.
    class Looper(PlayArchetype):
        name = "Looper"

        def update_sequential(self):
            while True:
                debug_log(1)

    mode_data = compile_mode(
        mode=Mode.PLAY,
        project_state=_project_state(),
        archetypes=[Looper],
        global_callbacks=None,
        level=MINIMAL_PASSES,
    )

    assert [archetype["name"] for archetype in mode_data["archetypes"]] == ["Looper"]
    assert "updateSequential" in mode_data["archetypes"][0]
