"""Per-mode entity storage sizes and cross-entity addressing.

Entity fields require a compile context, so addressing tests interpret the emitted callbacks with runtime blocks
populated explicitly instead of using a plain-Python oracle.
"""

import pytest

from sonolus.backend.blocks import PlayBlock, PreviewBlock, WatchBlock
from sonolus.backend.interpret import Interpreter
from sonolus.backend.node import FunctionNode
from sonolus.backend.ops import Op
from sonolus.backend.optimize import FAST_PASSES, MINIMAL_PASSES, STANDARD_PASSES
from sonolus.build.engine import package_engine, unpackage_data, validate_engine
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
from sonolus.script.engine import EngineData, PlayMode, PreviewMode, WatchMode
from sonolus.script.project import BuildConfig
from sonolus.script.vec import Vec2

MODE_TYPES = {
    "play": (PlayMode, PlayArchetype, PlayBlock),
    "watch": (WatchMode, WatchArchetype, WatchBlock),
    "preview": (PreviewMode, PreviewArchetype, PreviewBlock),
}
LENGTH_NAMES = ("entity_memory_length", "entity_data_length", "entity_shared_memory_length")
JSON_KEYS = ("entityMemoryLength", "entityDataLength", "entitySharedMemoryLength")
MODE_LENGTH_NAMES = [
    (mode, name) for mode in MODE_TYPES for name in LENGTH_NAMES if mode != "preview" or name != "entity_memory_length"
]


def _length_names(mode):
    return LENGTH_NAMES[1:] if mode == "preview" else LENGTH_NAMES


def _engine(mode, archetypes=(), **lengths):
    mode_type, _, _ = MODE_TYPES[mode]
    if mode == "watch":
        lengths["update_spawn"] = lambda: 0.0
    return EngineData(**{mode: mode_type(archetypes=archetypes, **lengths)})


def _payload(mode, engine, config=None):
    packaged = package_engine(engine, config)
    return unpackage_data(getattr(packaged, f"{mode}_data"))


def _lengths(payload):
    return (payload.get("entityMemoryLength", 0), payload["entityDataLength"], payload["entitySharedMemoryLength"])


@pytest.mark.parametrize(
    ("mode", "resource_keys", "expected_lengths"),
    [
        (
            "play",
            {"skin", "effect", "particle", "buckets", "archetypes", "nodes"},
            {"entityMemoryLength": 47, "entityDataLength": 47, "entitySharedMemoryLength": 47},
        ),
        (
            "watch",
            {"skin", "effect", "particle", "buckets", "archetypes", "nodes", "updateSpawn"},
            {"entityMemoryLength": 47, "entityDataLength": 47, "entitySharedMemoryLength": 47},
        ),
        (
            "preview",
            {"skin", "archetypes", "nodes"},
            {"entityDataLength": 47, "entitySharedMemoryLength": 47},
        ),
    ],
)
def test_packaged_json_uses_sonolus_field_names(mode, resource_keys, expected_lengths):
    payload = _payload(mode, _engine(mode, **dict.fromkeys(_length_names(mode), 47)))
    assert payload.keys() == resource_keys | expected_lengths.keys()
    assert {key: payload[key] for key in expected_lengths} == expected_lengths


@pytest.mark.parametrize("mode", MODE_TYPES)
def test_automatic_lengths_use_maximum_flat_extent_including_inherited_fields(mode):
    _, base, _ = MODE_TYPES[mode]

    class Small(base):
        beat: float = imported()
        cache: Vec2 = entity_data()
        if mode != "preview":
            state: Vec2 = entity_memory()
        shared: Array[float, 3] = shared_memory()

    class Larger(Small):
        extra: Vec2 = imported()
        if mode != "preview":
            more_state: float = entity_memory()

    class WideShared(base):
        shared: Array[float, 7] = shared_memory()

    engine = _engine(mode, [Small, Larger, WideShared])
    assert _lengths(_payload(mode, engine)) == (0 if mode == "preview" else 3, 5, 7)
    assert _lengths(_payload(mode, _engine(mode, [Small]))) == (0 if mode == "preview" else 2, 3, 3)
    assert _lengths(_payload(mode, engine)) == (0 if mode == "preview" else 3, 5, 7)


@pytest.mark.parametrize("mode", MODE_TYPES)
def test_unused_storage_has_zero_automatic_length(mode):
    assert _lengths(_payload(mode, _engine(mode))) == (0, 0, 0)
    assert _lengths(_payload(mode, _engine(mode, **dict.fromkeys(_length_names(mode))))) == (0, 0, 0)


@pytest.mark.parametrize("mode", MODE_TYPES)
def test_explicit_lengths_are_preserved_and_none_is_resolved_independently(mode):
    _, base, _ = MODE_TYPES[mode]

    class Note(base):
        beat: Vec2 = imported()

    lengths = {"entity_shared_memory_length": 45}
    if mode != "preview":
        lengths["entity_memory_length"] = 80
    engine = _engine(mode, [Note], **lengths)
    assert _lengths(_payload(mode, engine)) == (0 if mode == "preview" else 80, 2, 45)
    assert _lengths(_payload(mode, _engine(mode, **dict.fromkeys(_length_names(mode), 0)))) == (0, 0, 0)


@pytest.mark.parametrize("mode", MODE_TYPES)
def test_storage_can_exceed_the_legacy_limits(mode):
    _, base, _ = MODE_TYPES[mode]

    class Large(base):
        imports: Array[float, 40] = imported()
        data: Vec2 = entity_data()
        if mode != "preview":
            state: Array[float, 65] = entity_memory()
        shared: Array[float, 33] = shared_memory()

    assert _lengths(_payload(mode, _engine(mode, [Large]))) == (0 if mode == "preview" else 65, 42, 33)


@pytest.mark.parametrize(("mode", "name"), MODE_LENGTH_NAMES)
@pytest.mark.parametrize("validate_only", [False, True])
def test_explicit_length_below_the_declared_extent_is_rejected(mode, name, validate_only):
    _, base, _ = MODE_TYPES[mode]

    class Note(base):
        data: Vec2 = entity_data()
        if mode != "preview":
            state: Vec2 = entity_memory()
        shared: Vec2 = shared_memory()

    engine = _engine(mode, [Note], **{name: 1})
    build = validate_engine if validate_only else package_engine
    with pytest.raises(ValueError, match=f"{name} must be at least 2, got 1"):
        build(engine)


@pytest.mark.parametrize(("mode", "name"), MODE_LENGTH_NAMES)
@pytest.mark.parametrize("bad", [-1, 1.5, "3", True])
def test_invalid_lengths_are_rejected(mode, name, bad):
    error = ValueError if bad == -1 else TypeError
    with pytest.raises(error, match=name):
        package_engine(_engine(mode, **{name: bad}))


@pytest.mark.parametrize("mode", MODE_TYPES)
def test_disabled_modes_use_empty_automatic_lengths(mode):
    engine = _engine(mode, **dict.fromkeys(_length_names(mode), 100))
    assert _lengths(_payload(mode, engine, BuildConfig(**{f"build_{mode}": False}))) == (0, 0, 0)


def test_preview_has_no_entity_memory_length_field():
    assert "entityMemoryLength" not in _payload("preview", _engine("preview"))


def test_tutorial_has_no_entity_length_fields():
    packaged = package_engine(EngineData())
    assert set(JSON_KEYS).isdisjoint(unpackage_data(packaged.tutorial_data))


@pytest.mark.parametrize("mode", MODE_TYPES)
@pytest.mark.parametrize("passes", [MINIMAL_PASSES, FAST_PASSES, STANDARD_PASSES])
@pytest.mark.parametrize("explicit", [False, True])
def test_cross_entity_reads_and_writes_use_the_emitted_mode_lengths(mode, passes, explicit):
    _, base, blocks = MODE_TYPES[mode]

    class Target(base):
        beat: float = imported()
        cached: float = entity_data()
        shared: Vec2 = shared_memory()

    class Reader(base):
        target_index: int = imported()
        output: Vec2 = shared_memory()

        def preprocess(self):
            other = Target.at(self.target_index, check=False)
            self.output.x = other.beat + other.shared.x
            self.output.y = other.cached + other.shared.y
            other.cached = 17
            other.shared = Vec2(23, 29)

    lengths = {"entity_data_length": 43, "entity_shared_memory_length": 37} if explicit else {}
    engine = _engine(mode, [Reader, Target], **lengths)
    payload = _payload(mode, engine, BuildConfig(passes=passes))
    nodes = []
    for node in payload["nodes"]:
        nodes.append(
            node["value"] if "value" in node else FunctionNode(Op(node["func"]), tuple(nodes[i] for i in node["args"]))
        )
    interpreter = Interpreter()
    interpreter.set(blocks.EntityData, 0, 2)
    data_start = 2 * (43 if explicit else 2)
    shared_start = 2 * (37 if explicit else 2)
    interpreter.set(blocks.EntityDataArray, data_start, 5)
    interpreter.set(blocks.EntityDataArray, data_start + 1, 7)
    interpreter.set(blocks.EntitySharedMemoryArray, shared_start, 11)
    interpreter.set(blocks.EntitySharedMemoryArray, shared_start + 1, 13)
    callback = payload["archetypes"][0]["preprocess"]["index"]
    interpreter.run(nodes[callback])
    assert interpreter.get(blocks.EntitySharedMemory, 0) == 16
    assert interpreter.get(blocks.EntitySharedMemory, 1) == 20
    assert interpreter.get(blocks.EntityDataArray, data_start + 1) == 17
    assert interpreter.get(blocks.EntitySharedMemoryArray, shared_start) == 23
    assert interpreter.get(blocks.EntitySharedMemoryArray, shared_start + 1) == 29
