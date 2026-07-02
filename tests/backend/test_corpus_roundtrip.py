"""Marshal round-trip over the whole pydori callback corpus.

For every callback CFG (all modes, dev + non-dev), both raw (frontend output)
and optimized (old STANDARD_PASSES output) forms must:

* round-trip faithfully modulo the exporter's temp renumbering
  (``canon_text``; the optimized form is compared against an unflattened copy,
  since marshal-in binarizes n-ary associative ops), and
* be export/import idempotent byte-for-byte.

Mirrors tests/regressions/test_project.py's enumeration. See
OPTIMIZER_REWRITE.md sections 6.2 and 10.
"""

from __future__ import annotations

import pytest

from sonolus.backend.mode import Mode
from sonolus.backend.optimize.flow import cfg_to_text
from sonolus.backend.optimize.optimize import STANDARD_PASSES
from sonolus.backend.optimize.passes import OptimizerConfig, run_passes
from sonolus.backend.optimize.simplify import UnflattenAssociativeOps
from sonolus.build.compile import callback_to_cfg
from sonolus.script.internal.callbacks import (
    navigate_callback,
    preprocess_callback,
    update_callback,
    update_spawn_callback,
)
from sonolus.script.internal.context import ModeContextState, ProjectContextState, RuntimeChecks
from tests.backend._roundtrip_helpers import canon_text, roundtrip
from tests.regressions import pydori_project

_ENGINE = pydori_project.engine.data

_MODE_SETUP = {
    Mode.PLAY: (lambda: _ENGINE.play.archetypes, None),
    Mode.WATCH: (lambda: _ENGINE.watch.archetypes, lambda: [(update_spawn_callback, _ENGINE.watch.update_spawn)]),
    Mode.PREVIEW: (lambda: _ENGINE.preview.archetypes, None),
    Mode.TUTORIAL: (
        lambda: None,
        lambda: [
            (preprocess_callback, _ENGINE.tutorial.preprocess),
            (navigate_callback, _ENGINE.tutorial.navigate),
            (update_callback, _ENGINE.tutorial.update),
        ],
    ),
}


def _iter_callbacks(mode: Mode):
    """Yield (label, factory) where factory() builds a fresh CFG for the callback."""
    archetypes_fn, globals_fn = _MODE_SETUP[mode]
    archetypes = archetypes_fn()
    global_callbacks = globals_fn() if globals_fn is not None else None

    for dev in (False, True):
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
                    ms = ModeContextState(mode, arch_index)
                    return callback_to_cfg(ps, ms, cb, ci.name, archetype)

                yield f"{mode.name}:{archetype.__name__}.{cn}{suffix}", ci.name, factory

        for ci, cb in global_callbacks or []:

            def factory(cb=cb, ci=ci, checks=checks, arch_index=arch_index):
                ps = ProjectContextState(runtime_checks=checks)
                ms = ModeContextState(mode, arch_index)
                return callback_to_cfg(ps, ms, cb, ci.name, None)

            yield f"{mode.name}:global.{ci.name}{suffix}", ci.name, factory


def _check_callback(label, callback_name, factory, mode):
    cfg = factory()

    # Raw form: faithful (binary already) + idempotent.
    raw_rt = roundtrip(cfg, mode, callback_name)
    assert canon_text(raw_rt) == canon_text(cfg), f"{label}: raw not faithful"
    raw_rt2 = roundtrip(raw_rt, mode, callback_name)
    assert cfg_to_text(raw_rt) == cfg_to_text(raw_rt2), f"{label}: raw not idempotent"

    # Optimized form (mutates cfg): idempotent + faithful vs an unflattened copy.
    opt = run_passes(cfg, STANDARD_PASSES, OptimizerConfig(mode=mode, callback=callback_name))
    opt_rt = roundtrip(opt, mode, callback_name)
    opt_rt2 = roundtrip(opt_rt, mode, callback_name)
    assert cfg_to_text(opt_rt) == cfg_to_text(opt_rt2), f"{label}: optimized not idempotent"
    UnflattenAssociativeOps().run(opt, OptimizerConfig())
    assert canon_text(opt) == canon_text(opt_rt), f"{label}: optimized not faithful"


@pytest.mark.parametrize("mode", list(_MODE_SETUP))
def test_corpus_roundtrip(mode):
    count = 0
    for label, callback_name, factory in _iter_callbacks(mode):
        _check_callback(label, callback_name, factory, mode)
        count += 1
    assert count > 0, f"no callbacks enumerated for {mode}"
