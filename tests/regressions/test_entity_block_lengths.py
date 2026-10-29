"""Callback tooling must compile the same storage layout as the packaged engine."""

import sys

import pytest

from sonolus.backend.mode import Mode
from sonolus.backend.node import FunctionNode, format_engine_node
from sonolus.backend.ops import Op
from sonolus.backend.optimize import OptimizerConfig, cfg_to_engine_node, run_passes
from sonolus.build.engine import package_engine, unpackage_data
from sonolus.script.archetype import (
    PlayArchetype,
    PreviewArchetype,
    WatchArchetype,
    entity_data,
    entity_memory,
    imported,
    shared_memory,
)
from sonolus.script.array import Array
from sonolus.script.engine import Engine, EngineData, PlayMode, PreviewMode, WatchMode
from sonolus.script.internal.callbacks import update_spawn_callback
from sonolus.script.project import BuildConfig, Project
from sonolus.script.vec import Vec2
from tests.backend import _corpus
from tests.regressions import test_project
from tests.regressions.test_metrics_gate import _load_metrics_module

MODE_TYPES = {
    Mode.PLAY: (PlayMode, PlayArchetype),
    Mode.WATCH: (WatchMode, WatchArchetype),
    Mode.PREVIEW: (PreviewMode, PreviewArchetype),
}


@pytest.fixture
def metrics():
    old_limit = sys.getrecursionlimit()
    try:
        yield _load_metrics_module()
    finally:
        sys.setrecursionlimit(old_limit)


@pytest.mark.parametrize("mode", MODE_TYPES)
@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("level", ["fast", "standard"])
def test_callback_tools_match_packaged_trees(mode, explicit, level, monkeypatch, metrics):
    definition_type, base = MODE_TYPES[mode]

    class Target(base):
        beat: float = imported()
        cache: float = entity_data()
        shared: Vec2 = shared_memory()

    class Reader(base):
        target_index: int = imported()
        output: Vec2 = shared_memory()
        if mode != Mode.PREVIEW:
            state: Array[float, 3] = entity_memory()

        def preprocess(self):
            other = Target.at(self.target_index, check=False)
            self.output = Vec2(other.beat + other.shared.x, other.cache + other.shared.y)

    def update_spawn():
        return Target.at(2, check=False).cache

    lengths = {"entity_data_length": 43, "entity_shared_memory_length": 37} if explicit else {}
    if explicit and mode != Mode.PREVIEW:
        lengths["entity_memory_length"] = 80
    if mode == Mode.WATCH:
        lengths["update_spawn"] = update_spawn
    definition = definition_type(archetypes=[Reader, Target], **lengths)
    engine = EngineData(**{mode.name.lower(): definition})
    project = Project(engine=Engine(name="probe", data=engine))
    passes = test_project.PASSES[level]
    packaged = package_engine(engine, BuildConfig(passes=passes))
    payload = unpackage_data(getattr(packaged, f"{mode.name.lower()}_data"))
    nodes = []
    for node in payload["nodes"]:
        nodes.append(
            node["value"] if "value" in node else FunctionNode(Op(node["func"]), tuple(nodes[i] for i in node["args"]))
        )
    expected = {"preprocess": nodes[payload["archetypes"][0]["preprocess"]["index"]]}
    if mode == Mode.WATCH:
        expected["updateSpawn"] = nodes[payload["updateSpawn"]]

    measured = []
    emit = metrics.cfg_to_engine_node

    def record_node(cfg, config):
        node = emit(cfg, config)
        measured.append(node)
        return node

    monkeypatch.setattr(metrics, "cfg_to_engine_node", record_node)
    tasks = [task for task in metrics.iter_callback_tasks("probe", project) if task.mode == mode]
    assert {task.callback_name for task in tasks} == expected.keys()
    for task in tasks:
        metrics.measure_callback("probe", task, level, passes, repeat=1)
        assert measured.pop() == expected[task.callback_name]
        assert task.entity_block_lengths == (
            0 if mode == Mode.PREVIEW else 80 if explicit else 3,
            43 if explicit else 2,
            37 if explicit else 2,
        )

    monkeypatch.setattr(_corpus, "_ENGINE", engine)
    callbacks = list(_corpus.iter_callbacks(mode, dev_modes=(False,)))
    assert {callback_name for _, callback_name, _ in callbacks} == expected.keys()
    for _, callback_name, factory in callbacks:
        config = OptimizerConfig(mode=mode, callback=callback_name)
        actual = cfg_to_engine_node(run_passes(factory(), passes, config), config)
        assert actual == expected[callback_name]

    references = {}
    monkeypatch.setattr(test_project, "compare_with_reference", references.__setitem__)
    global_callbacks = [(update_spawn_callback, update_spawn)] if mode == Mode.WATCH else None
    test_project._build_mode_callbacks("probe", mode, definition, global_callbacks, level)
    for task in tasks:
        name = (
            f"reader_{task.cb_name}"
            if task.archetype is not None
            else f"global_{test_project.camel_to_snake(task.callback_name)}"
        )
        key = f"probe_{mode.name.lower()}_{name}_{level}_nodes"
        assert references[key] == format_engine_node(expected[task.callback_name])
