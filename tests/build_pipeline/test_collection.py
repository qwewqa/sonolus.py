"""Tests for sonolus.build.collection.Collection: item names, .scp and source loading, and output writing."""

import json
import re
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from sonolus.build.collection import SINGULAR_CATEGORY_NAMES, Collection, load_asset
from sonolus.build.project import load_resources_files_to_collection


def test_write_main_info_omits_empty_categories(tmp_path):
    c = Collection()
    c.categories["levels"] = {}
    c.categories["skins"] = {"s1": {"item": {}}}

    c._write_main_info(tmp_path)

    info = json.loads((tmp_path / "info").read_text(encoding="utf-8"))
    button_types = {b["type"] for b in info["buttons"]}
    assert SINGULAR_CATEGORY_NAMES["skins"] in button_types
    assert SINGULAR_CATEGORY_NAMES["levels"] not in button_types


def _item(name: str) -> dict:
    return {"name": name, "version": 1, "title": name.upper()}


def _item_details(name: str) -> dict:
    return {
        "item": _item(name),
        "actions": [],
        "hasCommunity": False,
        "leaderboards": [],
        "sections": [],
    }


def _make_scp(entries: dict[str, bytes]) -> bytes:
    """Pack a .scp with the entries in exactly the given order."""
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buffer.getvalue()


def _scp_items(category: str, names: list[str]) -> bytes:
    return _make_scp({f"sonolus/{category}/{name}": json.dumps(_item_details(name)).encode() for name in names})


def _write_source_item(
    root: Path,
    category: str,
    name: str,
    resources: dict[str, bytes] | None = None,
    item: dict | None = None,
) -> None:
    item_dir = root / category / name
    item_dir.mkdir(parents=True)
    (item_dir / "item.json").write_text(json.dumps(item if item is not None else _item(name)), encoding="utf-8")
    for filename, data in (resources or {}).items():
        (item_dir / filename).write_bytes(data)


def _written_tree(base: Path) -> dict[str, bytes]:
    root = base / "sonolus"
    return {str(p.relative_to(root).as_posix()): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("name", ["info", "list", "INFO", "List"])
def test_add_item_rejects_reserved_names(name):
    c = Collection()
    with pytest.raises(ValueError, match="is reserved"):
        c.add_item("skins", name, _item(name))


def test_load_from_source_rejects_reserved_item_directory(tmp_path):
    _write_source_item(tmp_path, "skins", "normal")
    _write_source_item(tmp_path, "skins", "info")

    with pytest.raises(ValueError, match="is reserved"):
        load_resources_files_to_collection(tmp_path)


@pytest.mark.parametrize("item", [None, [], 1, "item"])
def test_load_from_source_skips_non_object_item_json(tmp_path, item):
    item_dir = tmp_path / "skins" / "invalid"
    item_dir.mkdir(parents=True)
    item_json_path = item_dir / "item.json"
    item_json_path.write_text(json.dumps(item), encoding="utf-8")
    collection = Collection()

    with pytest.warns(UserWarning, match="Expected a JSON object"):
        collection.load_from_source(tmp_path)

    assert collection.categories == {}


@pytest.mark.parametrize("name", ["a/b", "a\\b", ".", ".."])
def test_add_item_rejects_unusable_names(name):
    c = Collection()
    with pytest.raises(ValueError, match="is not a usable filename"):
        c.add_item("skins", name, _item(name))


@pytest.mark.parametrize("name", ["trailing.", "trailing ", "nul\x00byte", "star*", "aux.txt"])
def test_add_item_rejects_names_that_are_not_portable_output_filenames(name):
    c = Collection()
    with pytest.raises(ValueError, match="is not a usable filename"):
        c.add_item("skins", name, _item(name))


def test_add_item_allows_same_name_overwrite():
    c = Collection()
    c.add_item("skins", "pixel", _item("pixel"))
    c.add_item("skins", "pixel", {**_item("pixel"), "version": 2})
    assert c.get_item("skins", "pixel")["version"] == 2


def test_scp_reserved_entries_are_skipped_not_rejected():
    """`sonolus/<category>/info` and `list` are category index files, not items, so they are dropped, not rejected."""
    scp = _make_scp(
        {
            "sonolus/skins/info": json.dumps({"sections": []}).encode(),
            "sonolus/skins/list": json.dumps({"pageCount": 1, "items": []}).encode(),
            "sonolus/skins/normal": json.dumps(_item_details("normal")).encode(),
        }
    )
    c = Collection()
    c.load_from_scp(scp)
    assert list(c.categories["skins"]) == ["normal"]


def test_scp_skips_an_empty_entry_name():
    assert Collection()._should_skip_zip_entry(zipfile.ZipInfo(""))


@pytest.mark.parametrize("item", [None, [], 1, "item"])
def test_load_from_scp_skips_non_object_item_json(item):
    scp = _make_scp(
        {
            "sonolus/skins/invalid": json.dumps(item).encode(),
            "sonolus/skins/valid": json.dumps(_item_details("valid")).encode(),
        }
    )
    collection = Collection()

    with pytest.warns(UserWarning, match="Expected a JSON object"):
        collection.load_from_scp(scp)

    assert list(collection.categories["skins"]) == ["valid"]


def test_load_from_scp_skips_item_json_that_is_not_utf8():
    scp = _make_scp(
        {
            "sonolus/skins/invalid": b"\xff",
            "sonolus/skins/valid": json.dumps(_item_details("valid")).encode(),
        }
    )
    collection = Collection()

    with pytest.warns(UserWarning, match="Invalid UTF-8"):
        collection.load_from_scp(scp)

    assert list(collection.categories["skins"]) == ["valid"]


def test_load_from_scp_skips_malformed_item_json():
    scp = _make_scp(
        {
            "sonolus/skins/invalid": b"{",
            "sonolus/skins/valid": json.dumps(_item_details("valid")).encode(),
        }
    )
    collection = Collection()

    with pytest.warns(UserWarning, match="Invalid JSON"):
        collection.load_from_scp(scp)

    assert list(collection.categories["skins"]) == ["valid"]


def test_load_from_scp_warning_points_to_caller():
    scp = _make_scp({"sonolus/skins/invalid": b"\xff"})
    collection = Collection()

    with pytest.warns(UserWarning, match="Invalid UTF-8") as warning_info:
        collection.load_from_scp(scp)

    assert warning_info[0].filename == __file__


def test_project_scp_warning_points_to_caller_and_names_archive(tmp_path):
    archive = tmp_path / "bad.scp"
    archive.write_bytes(_make_scp({"sonolus/skins/invalid": b"\xff"}))

    with pytest.warns(UserWarning, match=re.escape(f"sonolus/skins/invalid from {archive}")) as warning_info:
        load_resources_files_to_collection(tmp_path)

    assert warning_info[0].filename == __file__


def test_scp_warning_uses_pathlike_archive_name(tmp_path):
    class CustomPath:
        def __init__(self, path):
            self.path = path

        def __fspath__(self):
            return str(self.path)

    archive = tmp_path / "bad.scp"
    archive.write_bytes(_make_scp({"sonolus/skins/invalid": b"\xff"}))

    with pytest.warns(UserWarning, match=re.escape(f"sonolus/skins/invalid from {archive}")):
        Collection().load_from_scp(CustomPath(archive))


def test_scp_warning_preserves_url_archive_name():
    class OfflineCollection(Collection):
        def _load_data(self, _):
            return _make_scp({"sonolus/skins/invalid": b"\xff"})

    url = "https://example.com/resources.scp"
    with pytest.warns(UserWarning, match=re.escape(f"sonolus/skins/invalid from {url}")):
        OfflineCollection().load_from_scp(url)


def test_project_source_warning_points_to_caller(tmp_path):
    item_dir = tmp_path / "skins" / "invalid"
    item_dir.mkdir(parents=True)
    (item_dir / "item.json").write_text("null", encoding="utf-8")

    with pytest.warns(UserWarning, match="Expected a JSON object") as warning_info:
        load_resources_files_to_collection(tmp_path)

    assert warning_info[0].filename == __file__


def test_scp_keeps_dotted_item_names():
    c = Collection()
    c.load_from_scp(_scp_items("skins", ["pixel.hd", "pixel.sd", "plain"]))

    assert list(c.categories["skins"]) == ["pixel.hd", "pixel.sd", "plain"]
    assert c.get_item("skins", "pixel.hd")["name"] == "pixel.hd"
    assert c.get_item("skins", "pixel.sd")["name"] == "pixel.sd"


def test_scp_dotted_item_name_resolves_through_link():
    level = {
        "name": "song",
        "engine": _item("engine"),
        "useSkin": {"useDefault": False, "item": "pixel.hd"},
        "useBackground": {"useDefault": True},
        "useEffect": {"useDefault": True},
        "useParticle": {"useDefault": True},
    }
    c = Collection()
    c.load_from_scp(_scp_items("skins", ["pixel.hd"]))
    c.add_item("levels", "song", level)

    c.link()

    assert c.get_item("levels", "song")["useSkin"]["item"] == c.get_item("skins", "pixel.hd")


def test_scp_dotted_reserved_name_does_not_clobber_the_index():
    """`list.json` is a distinct item name; keying it as `list` would overwrite the category index."""
    c = Collection()
    c.load_from_scp(_scp_items("skins", ["list.json"]))
    assert list(c.categories["skins"]) == ["list.json"]


def test_write_scp_round_trip_is_a_fixed_point(tmp_path):
    c = Collection()
    c.load_from_scp(_scp_items("skins", ["pixel.hd", "plain"]))
    first = tmp_path / "first"
    c.write(first)

    repacked = _make_scp(dict(sorted(_prefixed_tree(first).items())))
    reloaded = Collection()
    reloaded.load_from_scp(repacked)
    second = tmp_path / "second"
    reloaded.write(second)

    assert _category_and_repository_files(second) == _category_and_repository_files(first)


def _prefixed_tree(base: Path) -> dict[str, bytes]:
    return {f"sonolus/{name}": data for name, data in _written_tree(base).items()}


def _category_and_repository_files(base: Path) -> dict[str, bytes]:
    # The base info file carries the collection name. That name is not recoverable from a .scp, so it is out of scope.
    return {name: data for name, data in _written_tree(base).items() if "/" in name}


def test_scp_entry_order_does_not_change_output(tmp_path):
    forward = Collection()
    forward.load_from_scp(_scp_items("skins", ["alpha", "zeta"]))
    forward.write(tmp_path / "forward")

    backward = Collection()
    backward.load_from_scp(_scp_items("skins", ["zeta", "alpha"]))
    backward.write(tmp_path / "backward")

    assert _written_tree(tmp_path / "backward") == _written_tree(tmp_path / "forward")


def _load_source_reversed(root: Path) -> Collection:
    real_iterdir = Path.iterdir
    real_rglob = Path.rglob
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "iterdir", lambda self: iter(list(real_iterdir(self))[::-1]))
        mp.setattr(Path, "rglob", lambda self, pattern, **kwargs: iter(list(real_rglob(self, pattern))[::-1]))
        return load_resources_files_to_collection(root)


def test_filesystem_order_does_not_change_output(tmp_path):
    source = tmp_path / "resources"
    source.mkdir()
    _write_source_item(source, "skins", "alpha", {"adata.json": b"{}", "zthumb.png": b"png-a"})
    _write_source_item(source, "skins", "zeta", {"adata.json": b"{}", "zthumb.png": b"png-z"})
    (source / "a.scp").write_bytes(_scp_items("backgrounds", ["a-bg"]))
    (source / "b.scp").write_bytes(_scp_items("backgrounds", ["b-bg"]))

    forward = load_resources_files_to_collection(source)
    forward.write(tmp_path / "forward")

    backward = _load_source_reversed(source)
    backward.write(tmp_path / "backward")

    assert _written_tree(tmp_path / "backward") == _written_tree(tmp_path / "forward")


def test_later_scp_overrides_earlier_for_same_item():
    c = Collection()
    c.load_from_scp(_scp_items("skins", ["pixel"]))
    later = _make_scp({"sonolus/skins/pixel": json.dumps(_item_details("pixel") | {"actions": ["x"]}).encode()})

    c.load_from_scp(later)

    assert list(c.categories["skins"]) == ["pixel"]
    assert c.categories["skins"]["pixel"]["actions"] == ["x"]


def test_source_files_override_scp_items(tmp_path):
    source = tmp_path / "resources"
    source.mkdir()
    (source / "a.scp").write_bytes(_scp_items("skins", ["pixel"]))
    _write_source_item(source, "skins", "pixel", item=_item("pixel") | {"title": "FROM SOURCE"})

    c = load_resources_files_to_collection(source)

    assert c.get_item("skins", "pixel")["title"] == "FROM SOURCE"


def test_load_from_source_rejects_resources_with_the_same_resolved_key(tmp_path):
    _write_source_item(tmp_path, "skins", "pixel", {"data.bin": b"binary", "data.json": b"{}"})
    c = Collection()

    with pytest.raises(ValueError, match="Duplicate resource key 'data'"):
        c.load_from_source(tmp_path)

    assert c.repository == {}


def test_load_from_source_rejects_resource_key_that_overwrites_item_metadata(tmp_path):
    _write_source_item(tmp_path, "skins", "pixel", {"title.png": b"image"})
    c = Collection()

    with pytest.raises(ValueError, match="Resource key 'title' conflicts with item metadata"):
        c.load_from_source(tmp_path)

    assert c.repository == {}


def test_load_asset_reads_a_relative_path_string(tmp_path, monkeypatch):
    (tmp_path / "bgm.mp3").write_bytes(b"audio bytes")
    monkeypatch.chdir(tmp_path)

    assert load_asset("bgm.mp3") == b"audio bytes"


def test_load_asset_reads_an_absolute_path_string(tmp_path):
    asset = tmp_path / "cover.png"
    asset.write_bytes(b"image bytes")

    assert load_asset(str(asset)) == load_asset(asset)


def test_load_asset_recognizes_a_mixed_case_http_scheme(monkeypatch):
    requested = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return b"downloaded"

    def urlopen(request):
        requested.append(request.full_url)
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", urlopen)

    assert load_asset("HtTpS://example.invalid/cover.png") == b"downloaded"
    assert requested == ["HtTpS://example.invalid/cover.png"]


def test_add_asset_accepts_a_path_string(tmp_path, monkeypatch):
    (tmp_path / "thumbnail.png").write_bytes(b"image bytes")
    monkeypatch.chdir(tmp_path)
    c = Collection()

    srl = c.add_asset("thumbnail.png")

    assert c.repository[srl["hash"]] == b"image bytes"


def test_write_preserves_insertion_order(tmp_path):
    """Loading sorts entries; `write` does not, so `add_item` order is what ships in the category list."""
    c = Collection()
    for name in ("zeta", "alpha", "mid"):
        c.add_item("skins", name, _item(name))

    c.write(tmp_path)

    listing = json.loads((tmp_path / "sonolus" / "skins" / "list").read_text(encoding="utf-8"))
    assert [item["name"] for item in listing["items"]] == ["zeta", "alpha", "mid"]
