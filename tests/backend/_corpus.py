"""Shared pydori callback corpus enumeration for the backend test suite.

Enumerates every pydori callback CFG (all modes, dev + non-dev) as a
``(label, callback_name, factory)`` stream, where ``factory()`` builds a fresh
CFG on each call.
"""

from __future__ import annotations

from sonolus.backend.mode import Mode
from sonolus.build.compile import _resolve_entity_block_lengths, callback_to_cfg
from sonolus.script.internal.callbacks import (
    navigate_callback,
    preprocess_callback,
    update_callback,
    update_spawn_callback,
)
from sonolus.script.internal.context import ModeContextState, ProjectContextState, RuntimeChecks
from tests.regressions import pydori_project

_ENGINE = pydori_project.engine.data

MODE_SETUP = {
    Mode.PLAY: (lambda: _ENGINE.play, None),
    Mode.WATCH: (lambda: _ENGINE.watch, lambda: [(update_spawn_callback, _ENGINE.watch.update_spawn)]),
    Mode.PREVIEW: (lambda: _ENGINE.preview, None),
    Mode.TUTORIAL: (
        lambda: _ENGINE.tutorial,
        lambda: [
            (preprocess_callback, _ENGINE.tutorial.preprocess),
            (navigate_callback, _ENGINE.tutorial.navigate),
            (update_callback, _ENGINE.tutorial.update),
        ],
    ),
}


def iter_callbacks(mode: Mode, *, dev_modes=(False, True)):
    """Yield (label, callback_name, factory) with a fresh CFG per factory call."""
    definition_fn, globals_fn = MODE_SETUP[mode]
    definition = definition_fn()
    archetypes = getattr(definition, "archetypes", None)
    global_callbacks = globals_fn() if globals_fn is not None else None
    entity_block_lengths = (
        _resolve_entity_block_lengths(
            archetypes or [],
            getattr(definition, "entity_memory_length", None),
            getattr(definition, "entity_data_length", None),
            getattr(definition, "entity_shared_memory_length", None),
        )
        if mode != Mode.TUTORIAL
        else (64, 32, 32)
    )

    for dev in dev_modes:
        checks = RuntimeChecks.NOTIFY_AND_TERMINATE if dev else RuntimeChecks.NONE
        suffix = "_dev" if dev else ""
        arch_index = {a: i for i, a in enumerate(archetypes)} if archetypes is not None else None

        for archetype in archetypes or []:
            archetype._init_fields()
            items = [
                (cn, ci, getattr(archetype, cn))
                for cn, ci in archetype._supported_callbacks_.items()
                if getattr(archetype, cn) not in archetype._default_callbacks_
            ]
            for cn, ci, cb in items:

                def factory(cb=cb, ci=ci, archetype=archetype, checks=checks, arch_index=arch_index):
                    ps = ProjectContextState(runtime_checks=checks)
                    ms = ModeContextState(mode, arch_index, entity_block_lengths)
                    return callback_to_cfg(ps, ms, cb, ci.name, archetype)

                yield f"{mode.name}:{archetype.__name__}.{cn}{suffix}", ci.name, factory

        for ci, cb in global_callbacks or []:

            def factory(cb=cb, ci=ci, checks=checks, arch_index=arch_index):
                ps = ProjectContextState(runtime_checks=checks)
                ms = ModeContextState(mode, arch_index, entity_block_lengths)
                return callback_to_cfg(ps, ms, cb, ci.name, None)

            yield f"{mode.name}:global.{ci.name}{suffix}", ci.name, factory
