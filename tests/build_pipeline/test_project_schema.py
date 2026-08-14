"""Tests that `Project.schema()` tells the names a level supplies apart from the names play mode exports.

A play archetype's `exported()` fields reach watch mode as imports, so an archetype on its own cannot tell an
import a level author writes from one the engine produces. The project schema is the only view that can: it sees
the play, watch, and preview archetypes sharing a name at once, so it reports the exports separately and leaves
them out of the fields a level supplies.
"""

import pytest

from sonolus.build.engine import package_engine, unpackage_data
from sonolus.script.archetype import (
    ArchetypeSchema,
    PlayArchetype,
    PreviewArchetype,
    StandardImport,
    WatchArchetype,
    exported,
    imported,
)
from sonolus.script.engine import Engine, EngineData, PlayMode, PreviewMode, WatchMode
from sonolus.script.project import BuildConfig, Project

# "both" is imported and exported under one name, "judged" is exported and imported back in watch mode, and
# "only_watch" is watch mode's alone.


class PlayThing(PlayArchetype):
    beat: StandardImport.BEAT = imported()
    both: float = imported()
    both_out: float = exported(name="both")
    judged: float = exported()


OtherThing = PlayThing.derive("Other", is_scored=False)


class WatchThing(WatchArchetype):
    beat: StandardImport.BEAT = imported()
    both: float = imported()
    judged: float = imported()
    only_watch: float = imported()
    judgment: StandardImport.JUDGMENT = imported()
    accuracy: StandardImport.ACCURACY = imported()


class PreviewThing(PreviewArchetype):
    beat: StandardImport.BEAT = imported()


class EmptyNamePlay(PlayArchetype):
    name = ""

    play_value: float = exported(name="")


class EmptyNameWatch(WatchArchetype):
    name = ""

    watch_value: float = imported(name="")


def watch_update_spawn() -> float:
    return 0.0


def build_test_project() -> Project:
    return Project(
        engine=Engine(
            name="test",
            data=EngineData(
                play=PlayMode(archetypes=[PlayThing, OtherThing]),
                watch=WatchMode(archetypes=[WatchThing], update_spawn=watch_update_spawn),
                preview=PreviewMode(archetypes=[PreviewThing]),
            ),
        )
    )


def schema_by_name(project: Project) -> dict[str, ArchetypeSchema]:
    """The project's schema entries, keyed by archetype name."""
    return {entry["name"]: entry for entry in project.schema()["archetypes"]}


def pydori_schema_by_name() -> dict[str, ArchetypeSchema]:
    """The pydori project's schema entries, keyed by archetype name."""
    from tests.regressions import pydori_project

    return schema_by_name(pydori_project)


def test_pydori_tap_omits_the_name_play_mode_exports():
    # play/note.py exports end_time and watch/note.py imports it back, so a level supplies everything but it.
    entry = pydori_schema_by_name()["Tap"]

    assert entry["fields"] == ["lane", "#BEAT", "direction", "prev_ref", "next_ref"]
    assert entry["exports"] == ["end_time"]


def test_pydori_hold_connector_keeps_a_name_no_archetype_exports():
    # watch/connector.py imports end_time and play/connector.py never exports it, so a level is its only source.
    entry = pydori_schema_by_name()["HoldConnector"]

    assert entry["fields"] == ["first_ref", "second_ref", "end_time"]
    assert entry["exports"] == []


def test_pydori_stage_has_no_fields_and_no_exports():
    entry = pydori_schema_by_name()["Stage"]

    assert entry["fields"] == []
    assert entry["exports"] == []


def test_a_name_play_mode_imports_and_exports_appears_in_both_lists():
    entry = schema_by_name(build_test_project())["Thing"]

    assert "both" in entry["fields"]
    assert "both" in entry["exports"]


def test_a_name_only_watch_mode_imports_back_is_not_a_field():
    entry = schema_by_name(build_test_project())["Thing"]

    assert entry["fields"] == ["#BEAT", "both", "only_watch"]
    assert entry["exports"] == ["both", "judged"]


def test_the_judgment_and_accuracy_imports_are_not_fields():
    entry = schema_by_name(build_test_project())["Thing"]

    assert "#JUDGMENT" not in entry["fields"]
    assert "#ACCURACY" not in entry["fields"]


def test_a_derived_archetype_reports_the_exports_of_its_base():
    entries = schema_by_name(build_test_project())

    assert entries["Other"]["exports"] == entries["Thing"]["exports"]


def test_exports_agree_with_the_engine_data():
    project = build_test_project()
    packaged = package_engine(
        project.engine.data, BuildConfig(build_watch=False, build_preview=False, build_tutorial=False)
    )
    engine_archetypes = unpackage_data(packaged.play_data)["archetypes"]
    entries = schema_by_name(project)

    assert [entry["name"] for entry in engine_archetypes] == list(entries)
    for engine_archetype in engine_archetypes:
        assert entries[engine_archetype["name"]]["exports"] == engine_archetype["exports"]


def test_an_archetype_reports_its_own_exports():
    assert PlayThing.schema()["exports"] == ["both", "judged"]
    assert WatchThing.schema()["exports"] == []
    assert PreviewThing.schema()["exports"] == []


def test_an_archetype_does_not_narrow_its_own_fields():
    # The narrowing needs every mode at once, so a watch archetype still reports what play mode produces.
    assert "judged" in WatchThing.schema()["fields"]
    assert "judged" not in schema_by_name(build_test_project())["Thing"]["fields"]


def test_schema_rejects_two_archetypes_with_the_same_name_in_one_mode():
    class Chart(PlayArchetype):
        pass

    class PlayChart(PlayArchetype):
        pass

    with pytest.warns(UserWarning, match="both have the name 'Chart'"):
        play = PlayMode(archetypes=[Chart, PlayChart])
    project = Project(Engine(name="test", data=EngineData(play=play)))

    with pytest.raises(ValueError, match="PLAY mode archetypes Chart and PlayChart both have the name 'Chart'"):
        project.schema()


def test_empty_archetype_and_field_names_link_across_modes():
    project = Project(
        Engine(
            name="test",
            data=EngineData(
                play=PlayMode(archetypes=[EmptyNamePlay]),
                watch=WatchMode(archetypes=[EmptyNameWatch], update_spawn=watch_update_spawn),
            ),
        )
    )

    entry = schema_by_name(project)[""]
    assert entry == {"name": "", "fields": [], "exports": [""]}

    packaged = package_engine(project.engine.data, BuildConfig(build_preview=False, build_tutorial=False))
    play_archetype = unpackage_data(packaged.play_data)["archetypes"][0]
    watch_archetype = unpackage_data(packaged.watch_data)["archetypes"][0]
    assert play_archetype["name"] == watch_archetype["name"] == ""  # ruff: ignore[compare-to-empty-string]
    assert play_archetype["exports"] == [""]
    assert watch_archetype["imports"] == [{"name": "", "index": 0}]
