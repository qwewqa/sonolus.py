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


def test_project_preserves_an_explicit_empty_resource_path():
    project = Project(engine=Engine(name="test", data=EngineData()), resources="")

    assert project.resources == Path()


def test_project_defaults_resources_only_when_unset():
    project = Project(engine=Engine(name="test", data=EngineData()))

    assert project.resources == Path("resources")


@pytest.mark.parametrize("resource_path", ["downloaded", Path("downloaded"), ""])
def test_project_loads_resources_on_first_access(resource_path):
    calls = []

    def load_resources():
        calls.append(True)
        return resource_path

    project = Project(engine=Engine(name="test", data=EngineData()), resources=load_resources)

    assert calls == []
    assert project.schema() == {"archetypes": []}
    assert calls == []
    assert project.resources == Path(resource_path)
    assert project.resources == Path(resource_path)
    assert calls == [True]


def test_project_build_does_not_load_resources(tmp_path):
    def load_resources():
        pytest.fail("An engine build must not load project resources")

    project = Project(engine=Engine(name="test", data=EngineData()), resources=load_resources)

    project.build(tmp_path)

    assert (tmp_path / "dist" / "engine" / "EngineConfiguration").is_file()


def test_project_retries_a_failed_resource_loader(tmp_path):
    calls = []

    def load_resources():
        calls.append(True)
        if len(calls) == 1:
            raise OSError("Download failed")
        return tmp_path

    project = Project(engine=Engine(name="test", data=EngineData()), resources=load_resources)

    with pytest.raises(OSError, match="Download failed"):
        _ = project.resources

    assert project.resources == tmp_path
    assert project.resources == tmp_path
    assert calls == [True, True]


def test_project_rejects_an_invalid_resource_loader_result():
    calls = []

    def load_resources():
        calls.append(True)

    project = Project(engine=Engine(name="test", data=EngineData()), resources=load_resources)

    for _ in range(2):
        with pytest.raises(TypeError, match=r"str|PathLike"):
            _ = project.resources
    assert calls == [True, True]


def test_project_resource_assignment_replaces_the_cached_path(tmp_path):
    project = make_project([])
    assert project.resources == Path("resources")
    calls = []

    def load_resources():
        calls.append(True)
        return tmp_path

    project.resources = load_resources
    assert calls == []
    assert project.resources == tmp_path
    assert calls == [True]
    project.resources = "replacement"
    assert project.resources == Path("replacement")
    project.resources = None
    assert project.resources == Path("resources")


@pytest.mark.parametrize("load_before_copy", [False, True])
def test_project_with_levels_preserves_lazy_resources(tmp_path, load_before_copy):
    calls = []

    def load_resources():
        calls.append(True)
        return tmp_path

    project = Project(engine=Engine(name="test", data=EngineData()), resources=load_resources)
    if load_before_copy:
        assert project.resources == tmp_path
    calls_before_copy = list(calls)

    copied = project.with_levels([make_level("new")])

    assert calls == calls_before_copy
    assert [level.name for level in copied.levels] == ["new"]
    assert copied.resources == tmp_path
    assert copied.resources == tmp_path
    assert calls == [True]


def test_collection_loads_resources_after_the_loader_creates_them(tmp_path, monkeypatch):
    from sonolus.build import project as project_mod

    resources = tmp_path / "downloaded"
    calls = []

    def load_resources():
        calls.append(True)
        item_dir = resources / "skins" / "downloaded-skin"
        item_dir.mkdir(parents=True)
        (item_dir / "item.json").write_text('{"title": "Downloaded skin"}', encoding="utf-8")
        (resources / "cover.png").write_bytes(b"DOWNLOADED")
        return str(resources)

    engine = Engine(name="test", data=EngineData(), thumbnail="cover.png")
    project = Project(engine=engine, resources=load_resources)
    thumbnails = []

    def add_engine(collection, project, engine, *args, **kwargs):
        thumbnails.append(load_resource(collection, engine.thumbnail, project.resources, b"DEFAULT"))

    monkeypatch.setattr(project_mod, "add_engine_to_collection", add_engine)

    for _ in range(2):
        collection = project_mod.build_project_to_collection(project, None)
        assert collection.get_item("skins", "downloaded-skin")["title"] == "Downloaded skin"
        assert collection.repository[thumbnails[-1]["hash"]] == b"DOWNLOADED"
    assert calls == [True]


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
