from collections.abc import Callable
from typing import Literal

import pytest

from sonolus.backend.mode import Mode
from sonolus.backend.node import format_engine_node
from sonolus.backend.optimize import OptimizerConfig, cfg_to_engine_node, run_passes
from sonolus.backend.optimize.flow import cfg_to_text
from sonolus.build.compile import callback_to_cfg
from sonolus.build.engine import _logical_cpu_count, package_engine  # noqa: PLC2701
from sonolus.script.archetype import _BaseArchetype
from sonolus.script.internal.callbacks import (
    CallbackInfo,
    navigate_callback,
    preprocess_callback,
    update_callback,
    update_spawn_callback,
)
from sonolus.script.internal.context import ModeContextState, ProjectContextState, RuntimeChecks
from sonolus.script.project import BuildConfig
from tests.regressions import pydori_project
from tests.regressions.conftest import compare_with_reference

PROJECTS = {
    "pydori": pydori_project,
}

PASSES = {
    # minimal won't work since some callbacks will run out of temporary memory
    "fast": BuildConfig.FAST_PASSES,
    "standard": BuildConfig.STANDARD_PASSES,
}


@pytest.mark.parametrize("project", ["pydori"])
@pytest.mark.parametrize("passes", ["fast", "standard"])
def test_project_full_build_succeeds(
    project: Literal["pydori"],
    passes: Literal["fast", "standard"],
):
    package_engine(
        PROJECTS[project].engine.data,
        BuildConfig(
            passes=PASSES[passes],
        ),
    )


def test_logical_cpu_count_available():
    # Guards the build path's worker-count helper against the 3.12 break where a
    # module-level `from os import process_cpu_count` (3.13+) crashed every build:
    # the helper must import and return a positive count on every supported Python.
    count = _logical_cpu_count()
    assert count is None or (isinstance(count, int) and count >= 1)


def test_project_build_is_deterministic():
    # The always-on thread pool must not make output depend on completion order:
    # two builds of the same project must be byte-for-byte identical (the merge
    # runs single-threaded in submission order). Pins cross-run + threaded
    # determinism, which the golden-based regression test does not exercise.
    engine = PROJECTS["pydori"].engine.data
    config = BuildConfig(passes=BuildConfig.STANDARD_PASSES)
    first = package_engine(engine, config)
    second = package_engine(engine, config)
    assert first == second


@pytest.mark.parametrize("project", ["pydori"])
@pytest.mark.parametrize("passes", ["fast", "standard"])
def test_project_method_build_regressions(
    project: Literal["pydori"],
    passes: Literal["fast", "standard"],
):
    engine = PROJECTS[project].engine.data

    _build_mode_callbacks(
        project_name=project,
        mode=Mode.PLAY,
        archetypes=engine.play.archetypes,
        global_callbacks=None,
        passes=passes,
    )
    _build_mode_callbacks(
        project_name=project,
        mode=Mode.WATCH,
        archetypes=engine.watch.archetypes,
        global_callbacks=[(update_spawn_callback, engine.watch.update_spawn)],
        passes=passes,
    )
    _build_mode_callbacks(
        project_name=project,
        mode=Mode.PREVIEW,
        archetypes=engine.preview.archetypes,
        global_callbacks=None,
        passes=passes,
    )
    _build_mode_callbacks(
        project_name=project,
        mode=Mode.TUTORIAL,
        archetypes=None,
        global_callbacks=[
            (preprocess_callback, engine.tutorial.preprocess),
            (navigate_callback, engine.tutorial.navigate),
            (update_callback, engine.tutorial.update),
        ],
        passes=passes,
    )


def _build_mode_callbacks(
    project_name: str,
    mode: Mode,
    archetypes: list[type[_BaseArchetype]] | None,
    global_callbacks: list[tuple[CallbackInfo, Callable]] | None,
    passes: str,
):
    for dev in (True, False):
        suffixes = [""]
        if dev:
            suffixes.append("dev")
        suffix = "_".join(suffixes)
        for archetype in archetypes or []:
            archetype._init_fields()

            callback_items = [
                (cb_name, cb_info, getattr(archetype, cb_name))
                for cb_name, cb_info in archetype._supported_callbacks_.items()
                if getattr(archetype, cb_name) not in archetype._default_callbacks_
            ]

            for cb_name, cb_info, cb in callback_items:
                project_state = ProjectContextState(
                    runtime_checks=RuntimeChecks.NOTIFY_AND_TERMINATE if dev else RuntimeChecks.NONE
                )
                mode_state = ModeContextState(
                    mode,
                    {a: i for i, a in enumerate(archetypes)} if archetypes is not None else None,
                )
                cfg = callback_to_cfg(project_state, mode_state, cb, cb_info.name, archetype)
                compare_with_reference(
                    f"{project_name}_{mode.name.lower()}_{camel_to_snake(archetype.__name__)}_{cb_name}{suffix}_cfg",
                    cfg_to_text(cfg),
                )
                cfg = run_passes(cfg, PASSES[passes], OptimizerConfig(mode=mode, callback=cb_info.name))
                node = cfg_to_engine_node(cfg)
                compare_with_reference(
                    f"{project_name}_{mode.name.lower()}_{camel_to_snake(archetype.__name__)}_{cb_name}_{passes}{suffix}_optimized_cfg",
                    cfg_to_text(cfg),
                )
                compare_with_reference(
                    f"{project_name}_{mode.name.lower()}_{camel_to_snake(archetype.__name__)}_{cb_name}_{passes}{suffix}_nodes",
                    format_engine_node(node),
                )

        for cb_info, cb in global_callbacks or []:
            project_state = ProjectContextState(
                runtime_checks=RuntimeChecks.NOTIFY_AND_TERMINATE if dev else RuntimeChecks.NONE
            )
            mode_state = ModeContextState(
                mode,
                {a: i for i, a in enumerate(archetypes)} if archetypes is not None else None,
            )
            cfg = callback_to_cfg(project_state, mode_state, cb, cb_info.name, None)
            compare_with_reference(
                f"{project_name}_{mode.name.lower()}_global_{camel_to_snake(cb_info.name)}_{passes}{suffix}_cfg",
                cfg_to_text(cfg),
            )
            cfg = run_passes(cfg, PASSES[passes], OptimizerConfig(mode=mode, callback=cb_info.name))
            node = cfg_to_engine_node(cfg)
            compare_with_reference(
                f"{project_name}_{mode.name.lower()}_global_{camel_to_snake(cb_info.name)}_{passes}{suffix}_optimized_cfg",
                cfg_to_text(cfg),
            )
            compare_with_reference(
                f"{project_name}_{mode.name.lower()}_global_{camel_to_snake(cb_info.name)}_{passes}{suffix}_nodes",
                format_engine_node(node),
            )


def camel_to_snake(name: str) -> str:
    return "".join(f"_{c.lower()}" if c.isupper() else c for c in name).lstrip("_")
