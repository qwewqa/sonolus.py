"""Tests that `Archetype.schema()` names fields the way level data does.

A schema exists to tell a level author which names an entity's data entries may use, so its field names have to be
the names those entries actually carry. A compound field contributes one name per flat slot, and a field with an
explicit `name=` contributes that name rather than its Python attribute name.
"""

from sonolus.script.archetype import PlayArchetype, imported
from sonolus.script.array import Array
from sonolus.script.vec import Vec2


class Simple(PlayArchetype):
    beat: float = imported()


class Compound(PlayArchetype):
    beat: float = imported()
    pos: Vec2 = imported()
    offsets: Array[float, 2] = imported()


class Renamed(PlayArchetype):
    beat: float = imported(name="#BEAT")
    pos: Vec2 = imported(name="position")


def level_data_entry_names(entity: PlayArchetype) -> list[str]:
    """The names the entity's data entries carry in level data."""
    return [entry["name"] for entry in entity._level_data_entries()]


def test_schema_reports_the_archetype_name():
    assert Simple.schema()["name"] == "Simple"


def test_schema_names_a_scalar_field():
    assert Simple.schema()["fields"] == ["beat"]


def test_schema_names_one_field_per_flat_slot():
    assert Compound.schema()["fields"] == ["beat", "pos.x", "pos.y", "offsets[0]", "offsets[1]"]


def test_schema_follows_an_explicit_field_name():
    assert Renamed.schema()["fields"] == ["#BEAT", "position.x", "position.y"]


def test_schema_fields_match_level_data_entry_names():
    entity = Compound(beat=1.0, pos=Vec2(2.0, 3.0), offsets=Array[float, 2](4.0, 5.0))
    assert Compound.schema()["fields"] == level_data_entry_names(entity)


def test_renamed_schema_fields_match_level_data_entry_names():
    entity = Renamed(beat=1.0, pos=Vec2(2.0, 3.0))
    assert Renamed.schema()["fields"] == level_data_entry_names(entity)
