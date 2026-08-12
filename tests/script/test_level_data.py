import gzip
import json

import pytest

from sonolus.backend.mode import Mode
from sonolus.build.compile import callback_to_cfg
from sonolus.build.level import build_level_data
from sonolus.script.archetype import EntityRef, ImportInfo, PlayArchetype, imported
from sonolus.script.array import Array
from sonolus.script.internal.context import ModeContextState, ProjectContextState, RuntimeChecks
from sonolus.script.internal.error import CompilationError
from sonolus.script.internal.visitor import clear_frontend_caches
from sonolus.script.level import ExternalEntityData, ExternalLevelData, LevelData, parse_external_level_data
from sonolus.script.record import Record
from sonolus.script.vec import Vec2


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


class Countdown(Record):
    remaining: int
    total: int

    def __bool__(self) -> bool:
        return self.remaining != 0


class CountdownHolder(PlayArchetype):
    name = "CountdownHolder"

    countdown: Countdown = imported()


class ReversedVec(Record):
    y: float
    x: float


class Positioned(PlayArchetype):
    name = "Positioned"

    pos: Vec2 = imported()


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


def test_level_data_ships_a_field_value_that_is_falsy_as_a_python_object():
    # Countdown(0, 5) is falsy under its own __bool__, so a truthiness test on the argument loses total=5.
    entity = CountdownHolder(countdown=Countdown(0, 5))

    raw = build_level_data(LevelData(bgm_offset=0, entities=[entity]))

    assert raw["entities"][0]["data"] == [
        {"name": "countdown.remaining", "value": 0},
        {"name": "countdown.total", "value": 5},
    ]


def test_level_data_rejects_a_wrong_typed_argument_that_is_falsy():
    with pytest.raises(TypeError, match="Cannot accept value None as Num"):
        RefTarget(value=None)


def test_level_data_rejects_a_wrong_typed_argument_that_is_falsy_for_a_record_field():
    with pytest.raises(TypeError, match=r"Cannot accept value \[\] as Countdown"):
        CountdownHolder(countdown=[])


def test_level_data_ships_zero_for_an_omitted_field():
    entity = RefTarget()

    raw = build_level_data(LevelData(bgm_offset=0, entities=[entity]))

    assert raw["entities"][0]["data"] == [{"name": "value", "value": 0}]


def test_level_data_ships_zero_for_a_field_explicitly_set_to_zero():
    entity = RefTarget(value=0)

    raw = build_level_data(LevelData(bgm_offset=0, entities=[entity]))

    assert raw["entities"][0]["data"] == [{"name": "value", "value": 0}]


def test_level_data_dangling_entity_ref_names_the_referring_field_and_referenced_entity():
    target = RefTarget(value=1)
    holder = RefHolder(target=target.ref())

    # target is deliberately absent from the level, leaving holder's reference dangling.
    with pytest.raises(
        ValueError,
        match=(
            r"Error in level entity 0 \('RefHolder'\): field 'target': "
            r"Reference to a 'RefTarget' entity that is not in the level's entities"
        ),
    ):
        build_level_data(LevelData(bgm_offset=0, entities=[holder]))


def test_level_data_rejects_the_same_entity_instance_twice():
    entity = RefTarget(value=1)

    with pytest.raises(ValueError, match=r"Level entity 1 \('RefTarget'\) is the same instance as entity 0"):
        build_level_data(LevelData(bgm_offset=0, entities=[entity, entity]))


def test_level_data_allows_distinct_entities_with_equal_field_values():
    first = RefTarget(value=1)
    second = RefTarget(value=1)

    raw = build_level_data(LevelData(bgm_offset=0, entities=[first, second]))

    assert [entity["name"] for entity in raw["entities"]] == ["0_RefTarget", "1_RefTarget"]


def test_level_data_rejects_a_wrong_typed_record_argument_of_the_same_size():
    with pytest.raises(TypeError, match=r"Cannot accept value ReversedVec\(y=7, x=8\) as Vec2"):
        Positioned(pos=ReversedVec(7.0, 8.0))


def _init_through_build_path(archetype):
    """Initialize `archetype` the way an engine author reaches it: by compiling a callback that spawns it.

    `callback_to_cfg` traces a failing callback a second time with `no_eval=False` and reports the second trace's
    error, so a build initializes an archetype twice whenever the first attempt fails.
    """

    def fn():
        archetype.spawn()

    clear_frontend_caches()
    project_state = ProjectContextState(runtime_checks=RuntimeChecks.NONE)
    callback_to_cfg(project_state, ModeContextState(Mode.PLAY), fn, "updateSequential")


def _assert_fields_rejected(archetype, message):
    """Assert that a repeated `_init_fields()` and a build both report `message`, not just the first call."""
    for _ in range(2):
        with pytest.raises(TypeError, match=message):
            archetype._init_fields()
    with pytest.raises(CompilationError, match=message):
        _init_through_build_path(archetype)


def test_an_import_default_of_a_wrong_typed_record_of_the_same_size_is_rejected():
    # The other way into the same field, for the same value. The flat keys come from the field's type and the
    # values from the default's declaration order, so accepting this would ship y=7.0 under 'pos.x'.
    class ReversedDefault(PlayArchetype):
        pos: Vec2 = imported(default=ReversedVec(7.0, 8.0))

    _assert_fields_rejected(
        ReversedDefault, "Field 'pos' of ReversedDefault has type Vec2, but its default has type ReversedVec"
    )


def test_an_import_default_of_an_array_for_a_record_field_is_rejected():
    class ArrayDefault(PlayArchetype):
        pos: Vec2 = imported(default=Array[float, 2](1.0, 2.0))

    _assert_fields_rejected(
        ArrayDefault, r"Field 'pos' of ArrayDefault has type Vec2, but its default has type Array\[Num, 2\]"
    )


def test_an_import_default_of_a_record_for_an_array_field_is_rejected():
    class RecordDefault(PlayArchetype):
        offsets: Array[float, 2] = imported(default=Vec2(1.0, 2.0))

    _assert_fields_rejected(
        RecordDefault,
        r"Field 'offsets' of RecordDefault has type Array\[Num, 2\], but its default has type Vec2",
    )


def test_an_import_default_the_fields_type_accepts_is_kept():
    # An int for a float field is the coercion the field type admits, so the check cannot be an isinstance test.
    class AcceptedDefaults(PlayArchetype):
        pos: Vec2 = imported(default=Vec2(1.0, 2.0))
        value: float = imported(default=3)

    AcceptedDefaults._init_fields()

    assert AcceptedDefaults._imported_keys_ == {
        "pos.x": ImportInfo(index=0, default=1.0),
        "pos.y": ImportInfo(index=1, default=2.0),
        "value": ImportInfo(index=2, default=3),
    }
