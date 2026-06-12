"""Parity tests for the Rust-backed Collection (PORT.md T5.2).

The legacy pure-Python :class:`~sonolus.build.collection.Collection` and the
Rust-backed :class:`~sonolus.build.rust_collection.RustCollection` are driven
through identical operation sequences and must agree on the in-memory state
(``categories`` dicts, repository contents) and on the written site tree,
byte for byte. The full pydori A/B lives in T5.3 (``tools/ab_collection.py``);
these tests cover the API contract on small fixtures.

Skipped when the ``sonolus_backend`` extension is not installed.
"""

import gzip
import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

sonolus_backend = pytest.importorskip("sonolus_backend")

from sonolus.build.collection import Collection, make_collection
from sonolus.build.rust_collection import RustCollection


def details(item: dict) -> bytes:
    return json.dumps(
        {
            "item": item,
            "actions": [],
            "hasCommunity": False,
            "leaderboards": [],
            "sections": [],
        }
    ).encode()


def build_zip(entries: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return buffer.getvalue()


def fixture_scp() -> bytes:
    """The T5.1 fixture scp (mirrors ``rust/sonolus-backend-core/tests/collection.rs``).

    Levels with and without the ``sonolus/`` prefix, reserved names, a
    ``.json`` item stem, repository blobs at both prefixes, an invalid item
    (category still created), an unknown directory, a too-short path, a nested
    item path, non-ASCII text, and int/float item values.
    """
    blob_one = b"blob-one"
    blob_two = b"blob-two"
    level_a = details(
        {
            "name": "level-a",
            "engine": "engine-a",
            "title": "Lévêl Ä 日本 \U0001f3b5",
            "rating": 7,
            "speed": 1.5,
            "engine_version": 13,
            "useSkin": {"useDefault": True},
            "useBackground": {"useDefault": False, "item": "bg-a"},
            "useEffect": {"useDefault": False, "item": {"name": "fx-a"}},
            "useParticle": {"useDefault": False, "item": "missing"},
        }
    )
    level_b = details(
        {
            "name": "level-b",
            "engine": {"name": "engine-a", "title": "already linked"},
            "useSkin": {"useDefault": True},
            "useBackground": {"useDefault": True},
            "useEffect": {"useDefault": True},
            "useParticle": {"useDefault": True},
        }
    )
    skin_a = details({"name": "skin-a", "title": "Skin A"})
    fx_a = details({"name": "fx-a", "title": "FX A"})
    p1 = details({"name": "p1", "title": "P1"})
    engine_a = details({"name": "engine-a", "title": "Engine A"})
    bg_a = details({"name": "bg-a", "title": "BG A"})
    hash_one = hashlib.sha1(blob_one).hexdigest()
    hash_two = hashlib.sha1(blob_two).hexdigest()
    return build_zip(
        [
            ("sonolus/levels/", b""),
            ("sonolus/levels/level-a", level_a),
            ("levels/level-b", level_b),
            ("sonolus/levels/info", b"{}"),
            ("sonolus/levels/List", b"{}"),
            ("sonolus/skins/skin-a.json", skin_a),
            (f"repository/{hash_one}", blob_one),
            (f"sonolus/repository/{hash_two}", blob_two),
            ("sonolus/effects/broken", b"not json"),
            ("sonolus/effects/fx-a", fx_a),
            ("sonolus/unknown/x", b"{}"),
            ("rootfile", b"ignored"),
            ("sonolus/particles/sub/p1", p1),
            ("sonolus/engines/engine-a", engine_a),
            ("sonolus/backgrounds/bg-a", bg_a),
            ("sonolus/posts/bad", b"also not json"),
        ]
    )


def walk_files(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def assert_same_state(legacy: Collection, rust: RustCollection) -> None:
    assert list(rust.categories) == list(legacy.categories), "category insertion order"
    for category, legacy_items in legacy.categories.items():
        assert list(rust.categories[category]) == list(legacy_items), f"item order in {category}"
    assert rust.categories == legacy.categories
    assert list(rust.repository) == list(legacy.repository), "repository insertion order"
    assert dict(rust.repository) == legacy.repository


def write_both(legacy: Collection, rust: RustCollection, tmp_path: Path) -> tuple[Path, Path]:
    legacy_dir = tmp_path / "legacy_site"
    rust_dir = tmp_path / "rust_site"
    legacy.write(legacy_dir)
    rust.write(rust_dir)
    return legacy_dir, rust_dir


def test_make_collection_selects_backend(monkeypatch):
    monkeypatch.delenv("SONOLUS_BACKEND", raising=False)
    default = make_collection()
    assert type(default) is Collection

    monkeypatch.setenv("SONOLUS_BACKEND", "python")
    assert type(make_collection()) is Collection

    monkeypatch.setenv("SONOLUS_BACKEND", "rust")
    rust = make_collection()
    assert type(rust) is RustCollection
    assert isinstance(rust, Collection), "the Rust-backed collection is a drop-in Collection"

    monkeypatch.setenv("SONOLUS_BACKEND", "bogus")
    with pytest.raises(RuntimeError, match="Unsupported SONOLUS_BACKEND"):
        make_collection()


def test_scp_load_parity():
    scp = fixture_scp()
    legacy = Collection()
    legacy.load_from_scp(scp)
    rust = RustCollection()
    rust.load_from_scp(scp)

    assert_same_state(legacy, rust)
    # Spot-check the documented loader quirks on the Rust side too.
    assert list(rust.categories["levels"]) == ["level-a", "level-b"], "reserved names skipped, archive order kept"
    assert list(rust.categories["skins"]) == ["skin-a"], ".json suffix stripped from the item name"
    assert rust.categories["posts"] == {}, "a category whose only entry is invalid still exists, empty"
    assert "unknown" not in rust.categories


def test_scp_write_parity_and_post_link_state(tmp_path):
    scp = fixture_scp()
    legacy = Collection()
    legacy.load_from_scp(scp)
    rust = RustCollection()
    rust.load_from_scp(scp)
    legacy.name = rust.name = "Parity"

    legacy_dir, rust_dir = write_both(legacy, rust, tmp_path)
    legacy_files = walk_files(legacy_dir)
    rust_files = walk_files(rust_dir)
    assert list(legacy_files) == list(rust_files)
    for name, data in legacy_files.items():
        assert rust_files[name] == data, f"site-tree file {name} differs"

    # write() links: the Rust-backed collection adopts the post-link snapshot,
    # so the in-memory state keeps evolving exactly like the legacy in-place
    # link (the dev server rebuilds against this state).
    assert_same_state(legacy, rust)
    assert isinstance(rust.categories["levels"]["level-a"]["item"]["engine"], dict)
    assert rust.categories["levels"]["level-a"]["item"]["engine"]["title"] == "Engine A"

    # Writing again (the dev-server rebuild pattern, clear=False) stays equal.
    legacy_dir2, rust_dir2 = write_both(legacy, rust, tmp_path / "again")
    assert walk_files(legacy_dir2) == walk_files(rust_dir2)


def test_in_place_item_mutation_reaches_write(tmp_path):
    """The converter pattern: items fetched from `categories` are mutated in place."""
    scp = fixture_scp()
    legacy = Collection()
    legacy.load_from_scp(scp)
    rust = RustCollection()
    rust.load_from_scp(scp)

    for collection in (legacy, rust):
        for level_details in collection.categories.get("levels", {}).values():
            level = level_details["item"]
            level["data"] = {"hash": "0" * 40, "url": f"/sonolus/repository/{'0' * 40}"}
        collection.categories["levels"]["level-a"]["item"]["engine"] = "engine-a"

    legacy_dir, rust_dir = write_both(legacy, rust, tmp_path)
    legacy_files = walk_files(legacy_dir)
    rust_files = walk_files(rust_dir)
    assert legacy_files == rust_files
    written = json.loads(rust_files["sonolus/levels/level-a"])
    assert written["item"]["data"]["hash"] == "0" * 40


def source_tree(root: Path) -> None:
    """Builds a source resource tree with name-sorted entries.

    Sorted names keep the legacy OS-order iteration and the Rust name-sorted
    iteration in agreement (the iteration order is a documented divergence).
    """
    pixel = root / "skins" / "pixel"
    pixel.mkdir(parents=True)
    pixel.joinpath("item.json").write_text(
        json.dumps(
            {
                "title": {"en": "Pixel", "ja": "x"},
                "subtitle": {"fr": "st-fr", "de": "st-de"},
                "author": "作者",
                "tags": [{"title": {"en": "t1"}, "extra": 2}],
                "meta": {"private": True},
            }
        ),
        encoding="utf-8",
    )
    pixel.joinpath("data.json").write_bytes(b'{"k": 1}')
    pixel.joinpath("thumbnail.png").write_bytes(b"\x89PNG-not-really")
    # No item.json -> silently skipped.
    (root / "levels" / "noitem").mkdir(parents=True)
    # Not a category -> ignored.
    other = root / "notacategory" / "foo"
    other.mkdir(parents=True)
    other.joinpath("item.json").write_bytes(b"{}")
    # A loose file directly in a category directory -> ignored.
    (root / "skins" / "zloose.txt").write_bytes(b"x")


def test_source_tree_parity(tmp_path):
    source_root = tmp_path / "resources"
    source_root.mkdir()
    source_tree(source_root)

    legacy = Collection()
    legacy.load_from_source(source_root)
    rust = RustCollection()
    rust.load_from_source(source_root)

    assert_same_state(legacy, rust)
    item = rust.categories["skins"]["pixel"]["item"]
    assert item["title"] == "Pixel"
    assert item["subtitle"] == "st-de", "smallest language key wins when 'en' is absent"
    assert item["tags"] == [{"title": "t1", "extra": 2}]
    assert "meta" not in item
    # .json resources are gzipped (mtime=0) before hashing; gzip bytes match
    # the Python gzip output (zlib-rs level 9, verified by the shared hash).
    gzipped = gzip.compress(b'{"k": 1}', mtime=0)
    data_hash = hashlib.sha1(gzipped).hexdigest()
    assert item["data"] == {"hash": data_hash, "url": f"/sonolus/repository/{data_hash}"}
    assert rust.repository[data_hash] == gzipped

    legacy_dir, rust_dir = write_both(legacy, rust, tmp_path)
    assert walk_files(legacy_dir) == walk_files(rust_dir)


def test_source_tree_invalid_item_warns(tmp_path):
    # Separate from the parity fixture: warning emission timing differs
    # between the implementations (documented), but both warn and both skip.
    for index, collection in enumerate((Collection(), RustCollection())):
        root = tmp_path / f"root{index}"
        broken = root / "skins" / "broken"
        broken.mkdir(parents=True)
        broken.joinpath("item.json").write_bytes(b"{invalid")
        with pytest.warns(UserWarning, match="Invalid JSON in"):
            collection.load_from_source(root)
        assert collection.categories.get("skins", {}) == {}


def test_add_item_add_asset_and_get_item_parity():
    legacy = Collection()
    rust = RustCollection()
    item = {
        "name": "s1",
        "title": {"en": "english", "ja": "x"},
        "artists": {"fr": "bonjour", "de": "hallo"},
        "tags": [{"title": "plain", "n": 1}],
        "meta": {"dropped": True},
    }
    for collection in (legacy, rust):
        collection.add_item("skins", "s1", dict(item))
        collection.add_item("skins", "s2", {"name": "s2", "title": "Second"})
    assert rust.categories == legacy.categories
    assert rust.get_item("skins", "s1") == legacy.get_item("skins", "s1")
    assert rust.get_default_item("skins") == legacy.get_default_item("skins")
    with pytest.raises(KeyError, match="Item 'nope' not found in category 'skins'"):
        rust.get_item("skins", "nope")
    with pytest.raises(KeyError, match="No items found in category 'levels'"):
        rust.get_default_item("levels")

    legacy_srl = legacy.add_asset(b"asset-bytes")
    rust_srl = rust.add_asset(b"asset-bytes")
    assert rust_srl == legacy_srl
    assert rust.repository[rust_srl["hash"]] == b"asset-bytes"
    assert dict(rust.repository) == legacy.repository


def test_repository_mapping_behavior():
    rust = RustCollection()
    srl = rust.add_asset(b"one")
    assert srl["hash"] in rust.repository
    assert rust.repository.get(srl["hash"]) == b"one"
    assert rust.repository.get("missing") is None
    with pytest.raises(KeyError):
        rust.repository["missing"]
    rust.repository["manual"] = b"two"
    assert list(rust.repository) == [srl["hash"], "manual"]
    assert len(rust.repository) == 2
    assert dict(rust.repository.items()) == {srl["hash"]: b"one", "manual": b"two"}
    del rust.repository["manual"]
    assert len(rust.repository) == 1
    with pytest.raises(KeyError):
        del rust.repository["manual"]


def test_repository_write_skips_existing_hashes(tmp_path):
    rust = RustCollection()
    srl = rust.add_asset(b"blob-content")
    site = tmp_path / "site"
    rust.write(site)
    stats = rust._last_write_stats
    assert stats["repository_files_written"] == 1
    assert stats["repository_files_skipped"] == 0

    # Skip-if-hash-exists means "never rewritten": tamper with the blob on
    # disk and write again; the tampered bytes survive.
    blob_path = site / "sonolus" / "repository" / srl["hash"]
    blob_path.write_bytes(b"tampered")
    rust.write(site)
    stats = rust._last_write_stats
    assert stats["repository_files_written"] == 0
    assert stats["repository_files_skipped"] == 1
    assert blob_path.read_bytes() == b"tampered"


def test_update_parity():
    def populated(cls):
        collection = cls()
        collection.add_item("skins", "s1", {"name": "s1", "v": 1})
        collection.add_asset(b"base")
        return collection

    def other_of(cls):
        other = cls()
        other.add_item("skins", "s1", {"name": "s1", "v": 10})
        other.add_item("levels", "l1", {"name": "l1"})
        other.add_asset(b"extra")
        return other

    legacy = populated(Collection)
    legacy.update(other_of(Collection))

    rust_from_rust = populated(RustCollection)
    rust_from_rust.update(other_of(RustCollection))
    assert_same_state(legacy, rust_from_rust)

    rust_from_legacy = populated(RustCollection)
    rust_from_legacy.update(other_of(Collection))
    assert_same_state(legacy, rust_from_legacy)

    # Self-update is a no-op on both (the legacy class tolerates it too).
    rust_from_rust.update(rust_from_rust)
    assert_same_state(legacy, rust_from_rust)


def test_load_resources_files_to_collection_lane_parity(tmp_path, monkeypatch):
    """The project.py entry point routes through make_collection and both lanes agree."""
    from sonolus.build.project import load_resources_files_to_collection

    resources = tmp_path / "resources"
    resources.mkdir()
    resources.joinpath("fixture.scp").write_bytes(fixture_scp())
    source_tree(resources)

    monkeypatch.delenv("SONOLUS_BACKEND", raising=False)
    legacy = load_resources_files_to_collection(resources)
    assert type(legacy) is Collection

    monkeypatch.setenv("SONOLUS_BACKEND", "rust")
    rust = load_resources_files_to_collection(resources)
    assert type(rust) is RustCollection

    assert_same_state(legacy, rust)
    legacy_dir, rust_dir = write_both(legacy, rust, tmp_path)
    assert walk_files(legacy_dir) == walk_files(rust_dir)


def test_link_missing_engine_raises_keyerror(tmp_path):
    rust = RustCollection()
    rust.add_item(
        "levels",
        "l",
        {
            "name": "l",
            "engine": "ghost",
            "useSkin": {"useDefault": True},
            "useBackground": {"useDefault": True},
            "useEffect": {"useDefault": True},
            "useParticle": {"useDefault": True},
        },
    )
    with pytest.raises(KeyError, match="Item 'ghost' not found in category 'engines'"):
        rust.write(tmp_path / "site")
