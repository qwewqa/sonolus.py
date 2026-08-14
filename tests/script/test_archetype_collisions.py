"""Tests that two archetype declarations which would collide are rejected rather than silently merged.

A field name, an import name, an export name, and an archetype name each address exactly one thing in the
shipped engine data, so two declarations resolving to one of them lose data the level or the callbacks still
refer to. The name a mode ships is the exception: the same name in two different modes is how one archetype is
shared across them, so only a collision within a single mode is reported. Mode construction warns about the
collision, and compilation rejects it before generating ambiguous engine data.

The last test here is about a diagnostic rather than a collision: a callback reading another archetype's field
off the class object is an error, and which error must not depend on where that archetype sits in the mode's
list.
"""

import warnings
from types import FunctionType
from typing import Annotated

import pytest

from sonolus.backend.mode import Mode
from sonolus.build.compile import compile_mode
from sonolus.script.archetype import (
    PlayArchetype,
    PreviewArchetype,
    StandardImport,
    WatchArchetype,
    entity_memory,
    exported,
    imported,
)
from sonolus.script.engine import PlayMode, PreviewMode, WatchMode
from sonolus.script.internal.context import ProjectContextState, RuntimeChecks
from sonolus.script.internal.error import CompilationError
from sonolus.script.vec import Vec2

ARCHETYPE_BASES = [PlayArchetype, WatchArchetype, PreviewArchetype]

# Which members an archetype inherits depends on its mode, so each mode gets its own list and its own
# completeness test below.
PROPERTIES_BY_BASE = {
    PlayArchetype: ["despawn", "index", "is_waiting", "is_active", "is_despawned", "result", "_info"],
    WatchArchetype: ["index", "is_active", "result", "_info"],
    PreviewArchetype: ["index", "_info"],
}

CALLBACKS_BY_BASE = {
    PlayArchetype: [
        "preprocess",
        "spawn_order",
        "should_spawn",
        "initialize",
        "update_sequential",
        "update_parallel",
        "touch",
        "terminate",
    ],
    WatchArchetype: [
        "preprocess",
        "spawn_time",
        "despawn_time",
        "initialize",
        "update_sequential",
        "update_parallel",
        "terminate",
    ],
    PreviewArchetype: ["preprocess", "render"],
}

# The methods every mode inherits from the shared archetype base, which is the class the message names for them.
SHARED_ARCHETYPE_METHODS = ["spawn", "at", "is_at", "ref", "schema", "derive"]


def case_id(value: type | str) -> str:
    return value.__name__ if isinstance(value, type) else value


def base_and_member_cases(members_by_base: dict[type, list[str]]) -> list[tuple[type, str]]:
    return [(base, member) for base, members in members_by_base.items() for member in members]


def declare_shadowing_field(base: type, member: str, name: str, assigned: bool) -> type:
    """Declare a subclass of `base` with a field named `member`, in the assigned or the annotated spelling.

    `field: int = entity_memory()` and `field: Annotated[int, entity_memory()]` declare the same field, but only
    the first leaves anything under that name in the class namespace, so each spelling needs its own rejection.
    """
    if assigned:
        namespace = {"__annotations__": {member: int}, member: entity_memory()}
    else:
        namespace = {"__annotations__": {member: Annotated[int, entity_memory()]}}
    return type(name, (base,), namespace)


def test_two_imported_fields_with_the_same_explicit_name_are_rejected():
    class DupImportName(PlayArchetype):
        beat: float = imported(name="#BEAT")
        other: float = imported(name="#BEAT")

    with pytest.raises(ValueError, match="both use the import name '#BEAT'"):
        DupImportName.schema()


def test_two_standard_imports_of_the_same_kind_are_rejected():
    class DupStandardImport(PlayArchetype):
        beat: StandardImport.BEAT
        end_beat: StandardImport.BEAT

    with pytest.raises(ValueError, match=r"'beat'.*'end_beat'.*both use the import name '#BEAT'"):
        DupStandardImport.schema()


def test_an_import_name_colliding_with_an_inherited_field_is_rejected():
    class ImportBase(PlayArchetype):
        lane: float = imported()

    class ImportSub(ImportBase):
        alt: float = imported(name="lane")

    with pytest.raises(ValueError, match=r"'lane' of ImportBase.*'alt' of ImportSub.*import name 'lane'"):
        ImportSub.schema()


def test_an_import_name_colliding_with_another_flat_key_is_rejected():
    class DupFlatImport(PlayArchetype):
        pos: Vec2 = imported()
        elsewhere: float = imported(name="pos.x")

    with pytest.raises(ValueError, match=r"both use the import name 'pos\.x'"):
        DupFlatImport.schema()


def test_two_exported_fields_with_the_same_explicit_name_are_rejected():
    class DupExportName(PlayArchetype):
        raw: float = exported(name="score")
        adjusted: float = exported(name="score")

    with pytest.raises(ValueError, match="both use the export name 'score'"):
        DupExportName.schema()


def test_an_export_name_colliding_with_an_inherited_field_is_rejected():
    class ExportBase(PlayArchetype):
        raw: float = exported(name="score")

    class ExportSub(ExportBase):
        score: float = exported()

    with pytest.raises(ValueError, match=r"'raw' of ExportBase.*'score' of ExportSub.*export name 'score'"):
        ExportSub.schema()


def test_an_export_name_colliding_with_another_flat_key_is_rejected():
    class DupFlatExport(PlayArchetype):
        pos: Vec2 = exported()
        elsewhere: float = exported(name="pos.x")

    with pytest.raises(ValueError, match=r"both use the export name 'pos\.x'"):
        DupFlatExport.schema()


def test_distinct_export_names_keep_one_index_per_flat_key():
    class Exports(PlayArchetype):
        pos: Vec2 = exported()
        score: float = exported()

    Exports._init_fields()

    assert Exports._exported_keys_ == {"pos.x": 0, "pos.y": 1, "score": 2}


@pytest.mark.parametrize("base", ARCHETYPE_BASES, ids=case_id)
def test_the_property_lists_cover_every_property_an_archetype_inherits(base: type):
    # The rejection tests are parametrized over the written-out lists, so this is what notices a new property.
    inherited = {
        name for entry in base.mro() for name, member in entry.__dict__.items() if isinstance(member, property)
    }

    assert inherited == set(PROPERTIES_BY_BASE[base])


@pytest.mark.parametrize("base", ARCHETYPE_BASES, ids=case_id)
def test_the_method_lists_cover_every_public_method_an_archetype_inherits(base: type):
    # Private members are excluded: they are not names an engine author would reach for, and a new internal
    # helper should not have to be listed here.
    inherited = {
        name
        for entry in base.mro()
        for name, member in entry.__dict__.items()
        if isinstance(member, FunctionType | classmethod | staticmethod) and not name.startswith("_")
    }

    assert inherited == set(SHARED_ARCHETYPE_METHODS) | set(CALLBACKS_BY_BASE[base])


@pytest.mark.parametrize("assigned", [True, False], ids=["assigned", "annotated"])
@pytest.mark.parametrize(("base", "member"), base_and_member_cases(PROPERTIES_BY_BASE), ids=case_id)
def test_a_field_named_after_an_inherited_property_is_rejected(base: type, member: str, assigned: bool):
    archetype = declare_shadowing_field(base, member, "ShadowProperty", assigned)

    message = f"Field '{member}' of ShadowProperty shadows the '{member}' property of {base.__name__}"
    with pytest.raises(TypeError, match=message):
        archetype._init_fields()


@pytest.mark.parametrize("assigned", [True, False], ids=["assigned", "annotated"])
@pytest.mark.parametrize("base", ARCHETYPE_BASES, ids=case_id)
@pytest.mark.parametrize("member", SHARED_ARCHETYPE_METHODS)
def test_a_field_named_after_an_inherited_method_is_rejected(base: type, member: str, assigned: bool):
    archetype = declare_shadowing_field(base, member, "ShadowMethod", assigned)

    message = f"Field '{member}' of ShadowMethod shadows the '{member}' method of _BaseArchetype"
    with pytest.raises(TypeError, match=message):
        archetype._init_fields()


@pytest.mark.parametrize("assigned", [True, False], ids=["assigned", "annotated"])
@pytest.mark.parametrize(("base", "member"), base_and_member_cases(CALLBACKS_BY_BASE), ids=case_id)
def test_a_field_named_after_a_callback_is_rejected(base: type, member: str, assigned: bool):
    archetype = declare_shadowing_field(base, member, "ShadowCallback", assigned)

    message = f"Field '{member}' of ShadowCallback shadows the '{member}' method of {base.__name__}"
    with pytest.raises(TypeError, match=message):
        archetype._init_fields()


def test_a_field_shadowing_an_inherited_member_names_the_declaring_archetype():
    class ShadowIndex(PlayArchetype):
        index: int = entity_memory()

    with pytest.raises(TypeError, match="Field 'index' of ShadowIndex shadows"):
        ShadowIndex._init_fields()


def test_a_member_of_another_mode_is_usable_as_a_field_name():
    class WatchDespawn(WatchArchetype):
        despawn: int = entity_memory()

    WatchDespawn._init_fields()

    assert "despawn" in WatchDespawn._memory_fields_


def test_the_same_play_archetype_listed_twice_is_rejected():
    class RepeatedPlay(PlayArchetype):
        pass

    with pytest.raises(ValueError, match="listed more than once"):
        PlayMode(archetypes=[RepeatedPlay, RepeatedPlay])


def test_the_same_watch_archetype_listed_twice_is_rejected():
    class RepeatedWatch(WatchArchetype):
        pass

    with pytest.raises(ValueError, match="listed more than once"):
        WatchMode(archetypes=[RepeatedWatch, RepeatedWatch], update_spawn=lambda: 0.0)


def test_the_same_preview_archetype_listed_twice_is_rejected():
    class RepeatedPreview(PreviewArchetype):
        pass

    with pytest.raises(ValueError, match="listed more than once"):
        PreviewMode(archetypes=[RepeatedPreview, RepeatedPreview])


def test_two_archetypes_resolving_to_one_name_in_a_mode_warn():
    class Chart(PlayArchetype):
        pass

    class PlayChart(PlayArchetype):
        pass

    with pytest.warns(UserWarning, match="both have the name 'Chart'"):
        PlayMode(archetypes=[Chart, PlayChart])


def test_two_archetypes_resolving_to_one_name_in_a_mode_fail_compilation():
    class Chart(PlayArchetype):
        pass

    class PlayChart(PlayArchetype):
        pass

    with pytest.raises(ValueError, match="PLAY mode archetypes Chart and PlayChart both have the name 'Chart'"):
        compile_mode(
            mode=Mode.PLAY,
            project_state=ProjectContextState(runtime_checks=RuntimeChecks.NONE),
            archetypes=[Chart, PlayChart],
            global_callbacks=None,
            validate_only=True,
        )


def test_an_archetype_name_shared_between_modes_does_not_warn():
    class Beat(PlayArchetype):
        pass

    class WatchBeat(WatchArchetype):
        pass

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        PlayMode(archetypes=[Beat])
        WatchMode(archetypes=[WatchBeat], update_spawn=lambda: 0.0)


def class_level_read_error(mistaken_first: bool) -> str:
    """Build a play mode whose one mistaken callback reads another archetype's field off the class object.

    Every call defines fresh archetypes, because field initialization is sticky and its result is cached.
    """

    class Target(PlayArchetype):
        value: float = imported()

    class Mistaken(PlayArchetype):
        own: float = imported()

        def update_sequential(self):
            if Target.value > 0:
                pass

    archetypes = [Mistaken, Target] if mistaken_first else [Target, Mistaken]
    with pytest.raises(CompilationError) as error:
        compile_mode(
            mode=Mode.PLAY,
            project_state=ProjectContextState(runtime_checks=RuntimeChecks.NONE),
            archetypes=archetypes,
            global_callbacks=None,
            level=None,
            validate_only=True,
        )
    return str(error.value)


def test_a_class_level_field_read_reports_the_same_error_whichever_archetype_compiles_first():
    mistaken_first = class_level_read_error(mistaken_first=True)
    mistaken_second = class_level_read_error(mistaken_first=False)

    assert "Field 'value' must be accessed on an instance of Target" in mistaken_first
    assert "Field 'value' must be accessed on an instance of Target" in mistaken_second
