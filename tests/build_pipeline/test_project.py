"""Tests for sonolus.build.project build helpers."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from sonolus.build.collection import Collection
from sonolus.build.project import load_resource
from sonolus.script.engine import Engine, EngineData
from sonolus.script.level import Level, LevelData
from sonolus.script.project import Project


def test_build_project_accepts_none_config(monkeypatch):
    from sonolus.build import project as project_mod

    monkeypatch.setattr(project_mod, "add_engine_to_collection", lambda *a, **k: None)
    stub_project = SimpleNamespace(converters={}, engine=SimpleNamespace(name="stub"), levels=[])

    project_mod.build_project_to_existing_collection(stub_project, Collection(), None)


def make_level(name: str) -> Level:
    return Level(name=name, data=LevelData(bgm_offset=0.0, entities=[]))


def make_project(levels: list[Level]) -> Project:
    return Project(engine=Engine(name="test", data=EngineData()), levels=levels)


def test_project_retries_duplicate_level_name_validation():
    project = make_project(iter([make_level("duplicate"), make_level("duplicate")]))

    for _ in range(2):
        with pytest.raises(
            ValueError, match="Project levels must have unique names; duplicate level name: 'duplicate'"
        ):
            assert project.levels


def test_project_rejects_duplicate_level_names_before_packaging(monkeypatch):
    from sonolus.build import project as project_mod

    levels = [make_level("first"), make_level("second")]
    project = make_project(levels)
    assert project.levels[1].name == "second"
    levels[1].name = "first"
    packaged = False

    def add_engine(*args, **kwargs):
        nonlocal packaged
        packaged = True

    monkeypatch.setattr(project_mod, "add_engine_to_collection", add_engine)

    with pytest.raises(ValueError, match="Project levels must have unique names; duplicate level name: 'first'"):
        project_mod.build_project_to_existing_collection(project, Collection(), None)

    assert not packaged


def test_project_to_collection_revalidates_cached_level_names_before_packaging(tmp_path, monkeypatch):
    from sonolus.build import project as project_mod

    levels = [make_level("first"), make_level("second")]
    project = make_project(levels)
    project.resources = tmp_path
    assert project.levels[1].name == "second"
    levels[1].name = "first"
    packaged = False

    def add_engine(*args, **kwargs):
        nonlocal packaged
        packaged = True

    monkeypatch.setattr(project_mod, "add_engine_to_collection", add_engine)

    with pytest.raises(ValueError, match="Project levels must have unique names; duplicate level name: 'first'"):
        project_mod.build_project_to_collection(project, None)

    assert not packaged


def test_project_revalidates_level_names_after_converters(monkeypatch):
    from sonolus.build import project as project_mod

    levels = [make_level("first"), make_level("second")]
    project = make_project(levels)
    project.converters = {"source": lambda _: None}
    packaged = False

    def apply_converter(*args, **kwargs):
        levels[1].name = "first"

    def add_engine(*args, **kwargs):
        nonlocal packaged
        packaged = True

    monkeypatch.setattr(project_mod, "apply_converter_to_collection", apply_converter)
    monkeypatch.setattr(project_mod, "add_engine_to_collection", add_engine)

    with pytest.raises(ValueError, match="Project levels must have unique names; duplicate level name: 'first'"):
        project_mod.build_project_to_existing_collection(project, Collection(), None)

    assert not packaged


def _two_roots(tmp_path: Path, monkeypatch) -> Path:
    """Write the same file name under the project's resources directory and under the current directory."""
    resources = tmp_path / "resources"
    resources.mkdir()
    (resources / "cover.png").write_bytes(b"IN_RESOURCES")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / "cover.png").write_bytes(b"IN_CWD")
    monkeypatch.chdir(cwd)
    return resources


def _loaded(collection: Collection, srl) -> bytes:
    return collection.repository[srl["hash"]]


def test_a_relative_asset_resolves_against_the_resources_directory(tmp_path, monkeypatch):
    # Both spellings of the same name name the same file: the resources directory is the root a project's
    # assets are written against, whichever type they arrive as.
    resources = _two_roots(tmp_path, monkeypatch)
    collection = Collection()

    for asset in ("cover.png", Path("cover.png")):
        assert _loaded(collection, load_resource(collection, asset, resources, b"DEFAULT")) == b"IN_RESOURCES"


def test_an_absolute_asset_is_loaded_from_where_it_points(tmp_path, monkeypatch):
    resources = _two_roots(tmp_path, monkeypatch)
    absolute = (tmp_path / "cwd" / "cover.png").resolve()
    collection = Collection()

    for asset in (str(absolute), absolute):
        assert _loaded(collection, load_resource(collection, asset, resources, b"DEFAULT")) == b"IN_CWD"


def test_an_unset_asset_loads_the_default(tmp_path, monkeypatch):
    resources = _two_roots(tmp_path, monkeypatch)
    collection = Collection()

    assert _loaded(collection, load_resource(collection, None, resources, b"DEFAULT")) == b"DEFAULT"


def test_raw_bytes_are_taken_as_the_asset(tmp_path, monkeypatch):
    resources = _two_roots(tmp_path, monkeypatch)
    collection = Collection()

    assert _loaded(collection, load_resource(collection, b"INLINE", resources, b"DEFAULT")) == b"INLINE"


def test_a_url_asset_is_not_joined_to_the_resources_directory(tmp_path, monkeypatch):
    resources = _two_roots(tmp_path, monkeypatch)
    collection = Collection()
    requested = []
    monkeypatch.setattr(collection, "add_asset", requested.append)

    load_resource(collection, "https://example.invalid/cover.png", resources, b"DEFAULT")

    assert requested == ["https://example.invalid/cover.png"]


def test_a_mixed_case_url_asset_is_not_joined_to_the_resources_directory(tmp_path, monkeypatch):
    resources = _two_roots(tmp_path, monkeypatch)
    collection = Collection()
    requested = []
    monkeypatch.setattr(collection, "add_asset", requested.append)

    load_resource(collection, "HtTpS://example.invalid/cover.png", resources, b"DEFAULT")

    assert requested == ["HtTpS://example.invalid/cover.png"]
