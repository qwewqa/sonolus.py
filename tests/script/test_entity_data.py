"""Tests that `entity_data()` fields are private per-entity storage rather than part of the archetype schema.

An `entity_data()` field shares the entity data block with `imported()` fields, so it occupies slots of its own
and is readable from other entities. What it must not do is appear anywhere a level author can reach: not in the
archetype schema, not among the parameters an entity accepts when level data is built, and not among the data
entries an entity emits. An `imported()` field must still appear in all three.
"""

from typing import Annotated

import pytest

from sonolus.backend.blocks import PlayBlock
from sonolus.backend.ir import IRGet, IRSet
from sonolus.backend.mode import Mode
from sonolus.backend.optimize.flow import BasicBlock, traverse_cfg_preorder
from sonolus.backend.place import BlockPlace
from sonolus.build.compile import callback_to_cfg
from sonolus.script.archetype import EntityRef, PlayArchetype, entity_data, imported
from sonolus.script.array import Array
from sonolus.script.engine import Engine, EngineData, PlayMode
from sonolus.script.internal.context import ModeContextState, ProjectContextState, RuntimeChecks
from sonolus.script.project import Project
from sonolus.script.vec import Vec2


def imported_indexes(archetype: type[PlayArchetype]) -> dict[str, int]:
    """The archetype's imports keyed by the name they carry in level data."""
    archetype._init_fields()
    return {name: info.index for name, info in archetype._imported_keys_.items()}


def imported_defaults(archetype: type[PlayArchetype]) -> dict[str, float | None]:
    """The default each of the archetype's imports falls back to when the level omits it."""
    archetype._init_fields()
    return {name: info.default for name, info in archetype._imported_keys_.items()}


def level_data_entry_names(entity: PlayArchetype) -> list[str]:
    """The names the entity's data entries carry in level data."""
    return [entry["name"] for entry in entity._level_data_entries()]


def preprocess_cfg(*archetypes: type[PlayArchetype]) -> BasicBlock:
    """Compile the first archetype's preprocess, with every given archetype registered in the mode."""
    for archetype in archetypes:
        archetype._init_fields()
    project_state = ProjectContextState(runtime_checks=RuntimeChecks.NONE)
    mode_state = ModeContextState(Mode.PLAY, list(archetypes))
    return callback_to_cfg(project_state, mode_state, archetypes[0].preprocess, "preprocess", archetypes[0])


def entity_data_slots_written(cfg: BasicBlock) -> set[int]:
    """The slots of this entity's own entity data block that the CFG writes to."""
    slots = set()
    for block in traverse_cfg_preorder(cfg):
        for statement in block.statements:
            match statement:
                case IRSet(place=BlockPlace(block=PlayBlock.EntityData, index=int() as index, offset=offset)):
                    slots.add(index + offset)
    return slots


def blocks_read(cfg: BasicBlock) -> set[PlayBlock]:
    """Every memory block the CFG reads from."""
    blocks = set()

    def walk(node):
        match node:
            case IRGet(place=BlockPlace(block=block)):
                blocks.add(block)
            case IRSet(value=value):
                walk(value)
            case _:
                for arg in getattr(node, "args", ()):
                    walk(arg)

    for block in traverse_cfg_preorder(cfg):
        for statement in block.statements:
            walk(statement)
        walk(block.test)
    return blocks


class Mixed(PlayArchetype):
    beat: float = imported(name="#BEAT")
    computed: float = entity_data()

    def preprocess(self):
        setattr(self, "computed", self.beat * 2)  # noqa: B010


class Interleaved(PlayArchetype):
    first: float = imported()
    middle: Vec2 = entity_data()
    last: float = imported()

    def preprocess(self):
        self.middle = Vec2(1.0, 2.0)
        self.last = 3.0


class WithDefault(PlayArchetype):
    beat: float = imported(default=3)
    computed: float = entity_data()


class Reader(PlayArchetype):
    other: EntityRef[Mixed] = imported()
    seen: float = entity_data()

    def preprocess(self):
        self.seen = self.other.get(check=False).computed


class Base(PlayArchetype):
    beat: float = imported()
    computed: float = entity_data()


class Derived(Base):
    lane: float = imported()


class Full(PlayArchetype):
    packed: Array[float, 30] = imported()
    spare: Vec2 = entity_data()


class BareAnnotation(PlayArchetype):
    beat: float = imported(name="#BEAT")
    computed: Annotated[float, entity_data]

    def preprocess(self):
        self.computed = self.beat * 2


def test_schema_omits_entity_data_fields():
    assert Mixed.schema()["fields"] == ["#BEAT"]


def test_project_schema_omits_entity_data_fields():
    project = Project(engine=Engine(name="test", data=EngineData(play=PlayMode(archetypes=[Mixed]))))

    assert project.schema()["archetypes"] == [{"name": "Mixed", "fields": ["#BEAT"], "exports": []}]


def test_level_data_entries_omit_entity_data_fields():
    entity = Mixed(beat=1.0)

    assert level_data_entry_names(entity) == ["#BEAT"]


def test_constructor_rejects_an_entity_data_field():
    with pytest.raises(TypeError, match="unexpected keyword argument 'computed'"):
        Mixed(beat=1.0, computed=2.0)


def test_reading_an_entity_data_field_of_level_data_raises():
    entity = Mixed(beat=1.0)

    with pytest.raises(RuntimeError, match="Entity data fields are not available in level data"):
        _ = entity.computed


def test_writing_an_entity_data_field_of_level_data_raises():
    entity = Mixed(beat=1.0)

    with pytest.raises(RuntimeError, match="Entity data fields are not available in level data"):
        entity.computed = 2.0


def test_an_entity_data_field_leaves_a_hole_in_the_import_indexes():
    # middle is a Vec2 taking two slots of its own, so last lands at 3, not at 1.
    assert imported_indexes(Interleaved) == {"first": 0, "last": 3}


def test_an_entity_data_field_owns_the_slots_its_hole_covers():
    assert entity_data_slots_written(preprocess_cfg(Interleaved)) == {1, 2, 3}


def test_an_entity_data_field_consumes_the_entity_data_budget():
    # Full fills the entity data block exactly, so one more field of either kind overflows.
    Full._init_fields()

    # Declared inside the test so _init_fields runs on it exactly once: the overflow check fires partway through
    # mutating the class, and a second call fails on the half-built state rather than on the budget.
    class Overflowing(Full):
        overflow: float = imported()

    with pytest.raises(ValueError, match="entity data size"):
        Overflowing._init_fields()


def test_imported_defaults_are_unaffected():
    assert imported_defaults(WithDefault) == {"beat": 3.0}


def test_preprocess_writes_an_entity_data_field_to_its_own_slot():
    assert entity_data_slots_written(preprocess_cfg(Mixed)) == {1}


def test_an_entity_data_field_of_another_entity_is_readable():
    assert PlayBlock.EntityDataArray in blocks_read(preprocess_cfg(Reader, Mixed))


def test_a_subclass_import_lands_after_an_inherited_entity_data_field():
    assert imported_indexes(Derived) == {"beat": 0, "lane": 2}


def test_the_bare_annotation_spelling_declares_the_same_field():
    assert BareAnnotation.schema()["fields"] == ["#BEAT"]
    assert entity_data_slots_written(preprocess_cfg(BareAnnotation)) == {1}
