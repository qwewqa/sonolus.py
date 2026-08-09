import gzip
import json

import pytest

from sonolus.build.level import build_level_data
from sonolus.script.archetype import EntityRef, PlayArchetype, imported
from sonolus.script.level import ExternalEntityData, ExternalLevelData, LevelData, parse_external_level_data


class Stage(PlayArchetype):
    name = "Stage"


class Note(PlayArchetype):
    name = "Note"


class RefTarget(PlayArchetype):
    name = "RefTarget"

    value: int = imported()


class RefHolder(PlayArchetype):
    name = "RefHolder"

    target: EntityRef[RefTarget] = imported()


def test_level_data_flattens_a_tuple_of_entities():
    stage = Stage()
    a = Note()
    b = Note()

    level_data = LevelData(bgm_offset=0, entities=(stage, a, b))

    assert level_data.entities == [stage, a, b]


def test_level_data_flattens_arbitrarily_nested_mixed_sequences():
    stage = Stage()
    a = Note()
    b = Note()
    c = Note()

    level_data = LevelData(bgm_offset=0, entities=[stage, (a, [b, (c,)])])

    assert level_data.entities == [stage, a, b, c]


def test_level_data_accepts_a_single_entity():
    stage = Stage()

    level_data = LevelData(bgm_offset=0, entities=stage)

    assert level_data.entities == [stage]


def test_level_data_rejects_a_string_instead_of_recursing_into_it():
    with pytest.raises(TypeError, match="sequence of entities"):
        LevelData(bgm_offset=0, entities="abc")


def test_parse_external_level_data_round_trips_build_level_data_dict():
    target = RefTarget(value=42)
    other = RefTarget(value=7)
    # The holder references other, not target: index 0 is also the documented fallback for an unresolved ref,
    # so only a ref to a non-zero index proves resolution through the entity's name key.
    holder = RefHolder(target=other.ref().with_archetype(RefTarget))

    level_data = LevelData(bgm_offset=1.5, entities=[target, other, holder])
    raw = build_level_data(level_data)

    parsed = parse_external_level_data(raw)

    assert parsed.bgm_offset == 1.5
    assert len(parsed.entities) == 3
    assert parsed.entities[0] == ExternalEntityData(archetype="RefTarget", data={"value": 42})
    assert parsed.entities[1] == ExternalEntityData(archetype="RefTarget", data={"value": 7})
    assert parsed.entities[2] == ExternalEntityData(archetype="RefHolder", data={"target": 1})


def test_parse_external_level_data_round_trips_through_json_string():
    target = RefTarget(value=3)
    other = RefTarget(value=4)
    holder = RefHolder(target=other.ref().with_archetype(RefTarget))
    level_data = LevelData(bgm_offset=0, entities=[target, other, holder])
    raw = build_level_data(level_data)

    parsed = parse_external_level_data(json.dumps(raw))

    assert parsed == ExternalLevelData(
        bgm_offset=0,
        entities=[
            ExternalEntityData(archetype="RefTarget", data={"value": 3}),
            ExternalEntityData(archetype="RefTarget", data={"value": 4}),
            ExternalEntityData(archetype="RefHolder", data={"target": 1}),
        ],
    )


def test_parse_external_level_data_round_trips_through_gzipped_bytes():
    target = RefTarget(value=9)
    level_data = LevelData(bgm_offset=0, entities=[target])
    raw = build_level_data(level_data)
    compressed = gzip.compress(json.dumps(raw).encode("utf-8"))

    parsed = parse_external_level_data(compressed)

    assert parsed == ExternalLevelData(
        bgm_offset=0,
        entities=[ExternalEntityData(archetype="RefTarget", data={"value": 9})],
    )


def test_parse_external_level_data_resolves_ref_by_entity_name_key():
    # The raw dict pins the name-key rule against the format itself, not against what build_level_data emits.
    raw = {
        "bgmOffset": 0,
        "entities": [
            {"name": "some_target", "archetype": "RefTarget", "data": [{"name": "value", "value": 5}]},
            {"name": "some_other", "archetype": "RefTarget", "data": [{"name": "value", "value": 6}]},
            {"name": "holder", "archetype": "RefHolder", "data": [{"name": "target", "ref": "some_other"}]},
        ],
    }

    parsed = parse_external_level_data(raw)

    assert parsed.entities[2].data == {"target": 1}


def test_parse_external_level_data_ref_index_counts_unnamed_entities_too():
    raw = {
        "bgmOffset": 0,
        "entities": [
            {"archetype": "Stage"},
            {"name": "some_target", "archetype": "RefTarget", "data": [{"name": "value", "value": 5}]},
            {"name": "holder", "archetype": "RefHolder", "data": [{"name": "target", "ref": "some_target"}]},
        ],
    }

    parsed = parse_external_level_data(raw)

    assert parsed.entities[2].data == {"target": 1}


def test_parse_external_level_data_dangling_ref_falls_back_to_zero():
    raw = {
        "bgmOffset": 0,
        "entities": [
            {"archetype": "RefHolder", "data": [{"name": "target", "ref": "does_not_exist"}]},
        ],
    }

    parsed = parse_external_level_data(raw)

    assert parsed.entities[0].data == {"target": 0}


def test_parse_external_level_data_entity_without_data_key_has_empty_data():
    raw = {"bgmOffset": 0, "entities": [{"archetype": "Stage"}]}

    parsed = parse_external_level_data(raw)

    assert parsed.entities == [ExternalEntityData(archetype="Stage", data={})]
