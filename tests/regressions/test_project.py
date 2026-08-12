from collections.abc import Callable
from typing import Literal

import pytest

from sonolus.backend.mode import Mode
from sonolus.backend.node import format_engine_node
from sonolus.backend.optimize import OptimizerConfig, cfg_to_engine_node, run_passes
from sonolus.backend.optimize.flow import cfg_to_text
from sonolus.build.compile import callback_to_cfg
from sonolus.build.engine import package_engine
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


def test_project_build_is_deterministic():
    # Two builds of the same project must be byte-for-byte identical. Both builds
    # run in one process, so this pins same-process repeat determinism (not true
    # cross-run determinism).
    engine = PROJECTS["pydori"].engine.data
    config = BuildConfig(passes=BuildConfig.STANDARD_PASSES)
    first = package_engine(engine, config)
    second = package_engine(engine, config)
    assert first == second


def test_compile_profiling_records_stages():
    # With profiling enabled, a build records a per-stage timing breakdown whose
    # JSON summary has the documented shape.
    from sonolus.backend.optimize import profiling

    was_enabled = profiling.enabled
    profiling.enable()
    profiling.reset()
    try:
        package_engine(PROJECTS["pydori"].engine.data, BuildConfig(passes=BuildConfig.FAST_PASSES))
        summary = profiling.summary()
    finally:
        profiling.enabled = was_enabled
        profiling.reset()

    assert set(summary) == {"stages", "total_ns"}
    stages = summary["stages"]
    # The fast pipeline exercises the frontend, marshal-in, every fast-level pass, and emit.
    assert {"frontend", "marshal_in", "cfg_cleanup", "build_ssa", "midend", "lower", "allocate", "emit"} <= set(stages)
    for stage in stages.values():
        assert set(stage) == {"total_ns", "count"}
        assert stage["total_ns"] >= 0
        assert stage["count"] >= 1
    assert summary["total_ns"] == sum(stage["total_ns"] for stage in stages.values())


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
        for archetype in base_archetypes(archetypes):
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
                config = OptimizerConfig(mode=mode, callback=cb_info.name)
                cfg = run_passes(cfg, PASSES[passes], config)
                node = cfg_to_engine_node(cfg, config)
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
            config = OptimizerConfig(mode=mode, callback=cb_info.name)
            cfg = run_passes(cfg, PASSES[passes], config)
            node = cfg_to_engine_node(cfg, config)
            compare_with_reference(
                f"{project_name}_{mode.name.lower()}_global_{camel_to_snake(cb_info.name)}_{passes}{suffix}_optimized_cfg",
                cfg_to_text(cfg),
            )
            compare_with_reference(
                f"{project_name}_{mode.name.lower()}_global_{camel_to_snake(cb_info.name)}_{passes}{suffix}_nodes",
                format_engine_node(node),
            )


def base_archetypes(archetypes: list[type[_BaseArchetype]] | None) -> list[type[_BaseArchetype]]:
    """The archetypes a build compiles callbacks for, in order.

    A derived archetype shares its base's callbacks and the build compiles them once, against the base
    (`compile_mode` in `sonolus/backend/_opt/driver.pyx`), then ships the base's node tree under each derived
    name. Comparing the derived binding against a golden pins a compilation the build never performs;
    test_derived_archetype_callbacks_match_their_base is what holds the sharing itself. Archetype ids come from
    the full list and are unaffected.
    """
    result = []
    seen = set()
    for archetype in archetypes or []:
        base = getattr(archetype, "_derived_base_", archetype)
        if base not in seen:
            seen.add(base)
            result.append(base)
    return result


def _emit_node_text(
    mode: Mode,
    archetypes: list[type[_BaseArchetype]],
    archetype: type[_BaseArchetype],
    cb_name: str,
    cb_info: CallbackInfo,
) -> str:
    project_state = ProjectContextState(runtime_checks=RuntimeChecks.NONE)
    mode_state = ModeContextState(mode, {a: i for i, a in enumerate(archetypes)})
    cfg = callback_to_cfg(project_state, mode_state, getattr(archetype, cb_name), cb_info.name, archetype)
    config = OptimizerConfig(mode=mode, callback=cb_info.name)
    return format_engine_node(cfg_to_engine_node(run_passes(cfg, PASSES["standard"], config), config))


def test_derived_archetype_callbacks_match_their_base():
    # The build compiles a shared callback once against the base archetype and lists the resulting node index
    # under every derived archetype's name, so a derived binding that emitted anything else would ship the
    # wrong tree. Nothing else compiles a derived binding: the goldens and the metrics gate measure the base.
    engine = PROJECTS["pydori"].engine.data
    compared = 0
    for mode, archetypes in (
        (Mode.PLAY, engine.play.archetypes),
        (Mode.WATCH, engine.watch.archetypes),
        (Mode.PREVIEW, engine.preview.archetypes),
    ):
        base_texts: dict[tuple[type[_BaseArchetype], str], str] = {}
        for archetype in archetypes:
            base = getattr(archetype, "_derived_base_", archetype)
            if base is archetype:
                continue
            archetype._init_fields()
            base._init_fields()
            for cb_name, cb_info in archetype._supported_callbacks_.items():
                if getattr(archetype, cb_name) in archetype._default_callbacks_:
                    continue
                key = (base, cb_name)
                if key not in base_texts:
                    base_texts[key] = _emit_node_text(mode, archetypes, base, cb_name, cb_info)
                assert _emit_node_text(mode, archetypes, archetype, cb_name, cb_info) == base_texts[key], (
                    f"{mode.name} {archetype.__name__}.{cb_name} emits different nodes than {base.__name__}"
                )
                compared += 1
    assert compared, "no derived archetype was compared, so this test pins nothing"


def camel_to_snake(name: str) -> str:
    return "".join(f"_{c.lower()}" if c.isupper() else c for c in name).lstrip("_")


def test_with_levels_preserves_converters():
    # with_levels() is a copy-with-modified-levels builder; it must not drop the converters
    # mapping (which build/project.py consumes to convert external levels).
    from sonolus.script.project import Project

    def conv(data):
        return None

    converters = {None: conv, "some-engine": conv}
    project = Project(engine=object(), levels=[], resources="res", converters=converters)
    new_project = project.with_levels([1, 2, 3])
    assert new_project.converters == converters  # {} before the fix, preserved after
    assert new_project.engine is project.engine
    assert new_project.resources == project.resources
