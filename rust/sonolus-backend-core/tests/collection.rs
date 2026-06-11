//! Collection end-to-end tests (PORT.md task T5.1 DoD): fixture scp archives
//! built in memory, source resource trees built in a temp dir, site-tree
//! writes with skip-if-hash-exists, linking, localization, and determinism
//! (two runs → identical bytes).

use std::fs;
use std::io::{Cursor, Write as _};
use std::path::{Path, PathBuf};

use serde_json::{Value, json};
use sonolus_backend_core::collection::{
    Category, Collection, CollectionError, gzip_compress, gzip_decompress, pyjson, sha1_hex,
};
use zip::ZipWriter;
use zip::write::SimpleFileOptions;

/// Builds a zip archive with the given `(name, bytes)` entries in order.
/// Names ending in `/` become directory entries.
fn build_zip(entries: &[(&str, &[u8])]) -> Vec<u8> {
    let mut writer = ZipWriter::new(Cursor::new(Vec::new()));
    for (name, data) in entries {
        if let Some(dir) = name.strip_suffix('/') {
            writer
                .add_directory(dir, SimpleFileOptions::default())
                .expect("add_directory");
        } else {
            writer
                .start_file(*name, SimpleFileOptions::default())
                .expect("start_file");
            writer.write_all(data).expect("write entry");
        }
    }
    writer.finish().expect("finish zip").into_inner()
}

fn details(item: Value) -> Vec<u8> {
    serde_json::to_vec(&json!({
        "item": item,
        "actions": [],
        "hasCommunity": false,
        "leaderboards": [],
        "sections": [],
    }))
    .expect("serialize details")
}

/// The shared fixture scp: levels (with and without the `sonolus/` prefix),
/// reserved names, a `.json` item stem, repository blobs at both prefixes, an
/// invalid item (category still created), an unknown directory, a too-short
/// path, and a nested item path.
fn fixture_scp() -> Vec<u8> {
    let blob_one = b"blob-one".as_slice();
    let blob_two = b"blob-two".as_slice();
    let level_a = details(json!({
        "name": "level-a",
        "engine": "engine-a",
        "title": "Level A",
        "useSkin": {"useDefault": true},
        "useBackground": {"useDefault": false, "item": "bg-a"},
        "useEffect": {"useDefault": false, "item": {"name": "fx-a"}},
        "useParticle": {"useDefault": false, "item": "missing"},
    }));
    let level_b = details(json!({
        "name": "level-b",
        "engine": {"name": "engine-a", "title": "already linked"},
        "useSkin": {"useDefault": true},
        "useBackground": {"useDefault": true},
        "useEffect": {"useDefault": true},
        "useParticle": {"useDefault": true},
    }));
    let skin_a = details(json!({"name": "skin-a", "title": "Skin A"}));
    let fx_a = details(json!({"name": "fx-a", "title": "FX A"}));
    let p1 = details(json!({"name": "p1", "title": "P1"}));
    let engine_a = details(json!({"name": "engine-a", "title": "Engine A"}));
    let bg_a = details(json!({"name": "bg-a", "title": "BG A"}));
    let hash_one = sha1_hex(blob_one);
    let hash_two = sha1_hex(blob_two);
    let repo_one = format!("repository/{hash_one}");
    let repo_two = format!("sonolus/repository/{hash_two}");
    build_zip(&[
        ("sonolus/levels/", b""),
        ("sonolus/levels/level-a", &level_a),
        ("levels/level-b", &level_b),
        ("sonolus/levels/info", b"{}"),
        ("sonolus/levels/List", b"{}"),
        ("sonolus/skins/skin-a.json", &skin_a),
        (&repo_one, blob_one),
        (&repo_two, blob_two),
        ("sonolus/effects/broken", b"not json"),
        ("sonolus/effects/fx-a", &fx_a),
        ("sonolus/unknown/x", b"{}"),
        ("rootfile", b"ignored"),
        ("sonolus/particles/sub/p1", &p1),
        ("sonolus/engines/engine-a", &engine_a),
        ("sonolus/backgrounds/bg-a", &bg_a),
        ("sonolus/posts/bad", b"also not json"),
    ])
}

fn load_fixture() -> Collection {
    let mut collection = Collection::new();
    collection
        .load_from_scp(&fixture_scp())
        .expect("fixture scp loads");
    collection
}

/// Walks all files under `root` (iteratively) and returns sorted
/// `(relative-path, bytes)` pairs with `/` separators.
fn walk_files(root: &Path) -> Vec<(String, Vec<u8>)> {
    let mut out = Vec::new();
    let mut stack: Vec<PathBuf> = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        for entry in fs::read_dir(&dir).expect("read_dir") {
            let path = entry.expect("dir entry").path();
            if path.is_dir() {
                stack.push(path);
            } else {
                let rel = path
                    .strip_prefix(root)
                    .expect("under root")
                    .to_string_lossy()
                    .replace('\\', "/");
                out.push((rel, fs::read(&path).expect("read file")));
            }
        }
    }
    out.sort();
    out
}

#[test]
fn scp_load_places_items_and_repository_blobs() {
    let collection = load_fixture();

    // Categories in first-encounter order; `unknown` is not a category, and
    // effects/posts exist even though they contained invalid JSON.
    let category_order: Vec<Category> = collection.categories().map(|(c, _)| c).collect();
    assert_eq!(
        category_order,
        [
            Category::Levels,
            Category::Skins,
            Category::Effects,
            Category::Particles,
            Category::Engines,
            Category::Backgrounds,
            Category::Posts,
        ]
    );

    let levels = collection.items(Category::Levels).unwrap();
    assert_eq!(
        levels.keys().collect::<Vec<_>>(),
        ["level-a", "level-b"],
        "reserved names (info/List) skipped; items in archive order"
    );
    assert_eq!(
        collection
            .items(Category::Skins)
            .unwrap()
            .keys()
            .collect::<Vec<_>>(),
        ["skin-a"],
        "the .json suffix is stripped from the item name"
    );
    assert_eq!(
        collection
            .items(Category::Effects)
            .unwrap()
            .keys()
            .collect::<Vec<_>>(),
        ["fx-a"],
        "invalid JSON entries are skipped"
    );
    assert!(
        collection.items(Category::Posts).unwrap().is_empty(),
        "a category whose only entry is invalid still exists, empty"
    );
    assert_eq!(
        collection
            .items(Category::Particles)
            .unwrap()
            .keys()
            .collect::<Vec<_>>(),
        ["p1"],
        "nested entry paths are flattened to the file name"
    );

    // Repository blobs keyed by file name, from both path prefixes.
    assert_eq!(collection.repository().len(), 2);
    assert_eq!(
        collection.repository()[&sha1_hex(b"blob-one")],
        b"blob-one".to_vec()
    );
    assert_eq!(
        collection.repository()[&sha1_hex(b"blob-two")],
        b"blob-two".to_vec()
    );

    // Details survive verbatim (no localization on scp load).
    let item = collection.get_item(Category::Levels, "level-a").unwrap();
    assert_eq!(item["title"], json!("Level A"));
    assert_eq!(item["engine"], json!("engine-a"));
}

#[test]
fn link_resolves_references_like_python() {
    let mut collection = load_fixture();
    collection.link().expect("link succeeds");

    let level_a = collection.get_item(Category::Levels, "level-a").unwrap();
    // String engine -> the engine's item.
    assert_eq!(
        level_a["engine"],
        json!({"name": "engine-a", "title": "Engine A"})
    );
    // No inner "item" -> untouched.
    assert_eq!(level_a["useSkin"], json!({"useDefault": true}));
    // String naming an existing item -> replaced by the item.
    assert_eq!(
        level_a["useBackground"],
        json!({"useDefault": false, "item": {"name": "bg-a", "title": "BG A"}})
    );
    // Object with a "name" naming an existing item -> replaced by the item.
    assert_eq!(
        level_a["useEffect"],
        json!({"useDefault": false, "item": {"name": "fx-a", "title": "FX A"}})
    );
    // String not present in the category -> left as the string, silently.
    assert_eq!(
        level_a["useParticle"],
        json!({"useDefault": false, "item": "missing"})
    );

    // A non-string engine is never resolved.
    let level_b = collection.get_item(Category::Levels, "level-b").unwrap();
    assert_eq!(
        level_b["engine"],
        json!({"name": "engine-a", "title": "already linked"})
    );

    // Linking is idempotent.
    let before = pyjson::dumps(
        collection
            .items(Category::Levels)
            .unwrap()
            .get("level-a")
            .unwrap(),
    );
    collection.link().expect("second link succeeds");
    let after = pyjson::dumps(
        collection
            .items(Category::Levels)
            .unwrap()
            .get("level-a")
            .unwrap(),
    );
    assert_eq!(before, after);
}

#[test]
fn link_missing_engine_is_a_python_keyerror_message() {
    let mut collection = Collection::new();
    collection
        .add_item(
            Category::Levels,
            "l",
            &json!({
                "name": "l",
                "engine": "ghost",
                "useSkin": {"useDefault": true},
                "useBackground": {"useDefault": true},
                "useEffect": {"useDefault": true},
                "useParticle": {"useDefault": true},
            }),
        )
        .unwrap();
    let err = collection.link().unwrap_err();
    assert!(matches!(err, CollectionError::ItemNotFound { .. }));
    assert_eq!(
        err.to_string(),
        "Item 'ghost' not found in category 'engines'"
    );
}

#[test]
fn link_missing_use_key_is_an_error() {
    let mut collection = Collection::new();
    collection
        .add_item(Category::Engines, "e", &json!({"name": "e"}))
        .unwrap();
    collection
        .add_item(Category::Levels, "l", &json!({"name": "l", "engine": "e"}))
        .unwrap();
    // Python raises KeyError('useSkin') here.
    assert!(collection.link().is_err());
}

#[test]
fn write_produces_the_site_tree_with_skip_if_hash_exists() {
    let mut collection = load_fixture();
    let dir = tempfile::tempdir().expect("tempdir");
    let stats = collection.write(dir.path()).expect("write succeeds");

    // 1 main info + (info + list + items) per non-empty category:
    // levels 2+2, skins 2+1, effects 2+1, particles 2+1, engines 2+1,
    // backgrounds 2+1; posts is empty and writes nothing.
    assert_eq!(stats.json_files_written, 20);
    assert_eq!(stats.repository_files_written, 2);
    assert_eq!(stats.repository_files_skipped, 0);

    let base = dir.path().join("sonolus");
    // Main info: byte-exact Python json.dumps output, buttons in
    // CATEGORY_SORT_ORDER (empty `posts` still gets a button).
    let info = fs::read_to_string(base.join("info")).unwrap();
    assert_eq!(
        info,
        concat!(
            r#"{"title": "Unnamed", "buttons": ["#,
            r#"{"type": "level"}, {"type": "engine"}, {"type": "skin"}, "#,
            r#"{"type": "effect"}, {"type": "particle"}, {"type": "background"}, "#,
            r#"{"type": "post"}], "configuration": {"options": []}}"#,
        )
    );
    assert!(
        !base.join("posts").exists(),
        "empty categories are not written"
    );

    // Category structure: info, list, and one file per item.
    let levels_dir = base.join("levels");
    let list: Value = serde_json::from_slice(&fs::read(levels_dir.join("list")).unwrap()).unwrap();
    assert_eq!(list["pageCount"], json!(1));
    assert_eq!(list["items"].as_array().unwrap().len(), 2);
    assert_eq!(list["items"][0]["name"], json!("level-a"));
    assert_eq!(
        list["items"][0]["engine"]["title"],
        json!("Engine A"),
        "the list embeds linked items"
    );
    let cat_info: Value =
        serde_json::from_slice(&fs::read(levels_dir.join("info")).unwrap()).unwrap();
    assert_eq!(cat_info["sections"][0]["itemType"], json!("level"));
    assert_eq!(cat_info["sections"][0]["title"], json!("Items"));
    let item_file: Value =
        serde_json::from_slice(&fs::read(levels_dir.join("level-a")).unwrap()).unwrap();
    assert_eq!(item_file["hasCommunity"], json!(false));
    assert_eq!(item_file["item"]["name"], json!("level-a"));

    // Repository files by hash.
    let repo = base.join("repository");
    let hash_one = sha1_hex(b"blob-one");
    assert_eq!(fs::read(repo.join(&hash_one)).unwrap(), b"blob-one");

    // Second write: every repository blob already exists and is skipped.
    let stats2 = collection.write(dir.path()).expect("second write succeeds");
    assert_eq!(stats2.repository_files_written, 0);
    assert_eq!(stats2.repository_files_skipped, 2);

    // Skip really means "never rewritten": tamper with a blob on disk and
    // write again; the tampered bytes survive.
    fs::write(repo.join(&hash_one), b"tampered").unwrap();
    collection.write(dir.path()).expect("third write succeeds");
    assert_eq!(fs::read(repo.join(&hash_one)).unwrap(), b"tampered");
}

#[test]
fn writes_are_deterministic_across_runs() {
    let scp = fixture_scp();
    let mut first = Collection::new();
    first.load_from_scp(&scp).unwrap();
    let mut second = Collection::new();
    second.load_from_scp(&scp).unwrap();

    let dir_a = tempfile::tempdir().unwrap();
    let dir_b = tempfile::tempdir().unwrap();
    first.write(dir_a.path()).unwrap();
    second.write(dir_b.path()).unwrap();

    let files_a = walk_files(dir_a.path());
    let files_b = walk_files(dir_b.path());
    assert!(!files_a.is_empty());
    assert_eq!(files_a, files_b, "same input must produce identical trees");
}

#[test]
fn written_site_tree_round_trips_through_scp_load() {
    let mut original = load_fixture();
    let dir = tempfile::tempdir().unwrap();
    original.write(dir.path()).unwrap();

    // Re-zip the written tree as an scp (the site layout *is* the scp
    // layout) and load it back. info/list files are regenerated artifacts
    // and are skipped by the loader.
    let files = walk_files(dir.path());
    let entries: Vec<(&str, &[u8])> = files
        .iter()
        .map(|(name, data)| (name.as_str(), data.as_slice()))
        .collect();
    let zip_bytes = build_zip(&entries);
    let mut reloaded = Collection::new();
    reloaded.load_from_scp(&zip_bytes).unwrap();

    for (category, items) in original.categories() {
        if items.is_empty() {
            // Empty categories produce no files, so they do not round-trip.
            continue;
        }
        let reloaded_items = reloaded
            .items(category)
            .unwrap_or_else(|| panic!("category {category} missing after round-trip"));
        assert_eq!(
            items.keys().collect::<Vec<_>>(),
            reloaded_items.keys().collect::<Vec<_>>(),
            "item names and order for {category}"
        );
        for (name, details) in items {
            assert_eq!(
                pyjson::dumps(details),
                pyjson::dumps(&reloaded_items[name]),
                "details for {category}/{name}"
            );
        }
    }
    assert_eq!(original.repository().len(), reloaded.repository().len());
    for (hash, data) in original.repository() {
        assert_eq!(reloaded.repository().get(hash), Some(data), "blob {hash}");
    }
}

#[test]
fn empty_collection_writes_info_and_empty_repository() {
    let mut collection = Collection::new();
    let dir = tempfile::tempdir().unwrap();
    let stats = collection.write(dir.path()).unwrap();
    assert_eq!(stats.json_files_written, 1);
    assert_eq!(stats.repository_files_written, 0);
    let info = fs::read_to_string(dir.path().join("sonolus").join("info")).unwrap();
    assert_eq!(
        info,
        r#"{"title": "Unnamed", "buttons": [], "configuration": {"options": []}}"#
    );
    assert!(
        dir.path().join("sonolus").join("repository").is_dir(),
        "the repository directory is created even when empty"
    );
}

#[test]
fn load_from_source_localizes_gzips_and_warns() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    let pixel = root.join("skins").join("pixel");
    fs::create_dir_all(&pixel).unwrap();
    fs::write(
        pixel.join("item.json"),
        serde_json::to_vec(&json!({
            "title": {"en": "Pixel", "ja": "x"},
            "subtitle": {"fr": "st-fr", "de": "st-de"},
            "meta": {"private": true},
        }))
        .unwrap(),
    )
    .unwrap();
    let png_bytes = b"\x89PNG-not-really".as_slice();
    fs::write(pixel.join("thumbnail.png"), png_bytes).unwrap();
    let data_bytes = br#"{"k": 1}"#.as_slice();
    fs::write(pixel.join("data.json"), data_bytes).unwrap();
    // A nested directory cannot be read as a resource -> warning.
    fs::create_dir_all(pixel.join("subdir")).unwrap();
    // Invalid item JSON -> warning, item skipped.
    let broken = root.join("skins").join("broken");
    fs::create_dir_all(&broken).unwrap();
    fs::write(broken.join("item.json"), b"{invalid").unwrap();
    // No item.json -> silently skipped.
    fs::create_dir_all(root.join("levels").join("noitem")).unwrap();
    // Not a category -> ignored.
    let other = root.join("notacategory").join("foo");
    fs::create_dir_all(&other).unwrap();
    fs::write(other.join("item.json"), b"{}").unwrap();
    // A loose file directly in a category directory -> ignored.
    fs::write(root.join("skins").join("loose.txt"), b"x").unwrap();

    let mut collection = Collection::new();
    let warnings = collection.load_from_source(root).expect("source loads");

    assert_eq!(warnings.len(), 2, "warnings: {warnings:?}");
    assert!(warnings.iter().any(|w| w.starts_with("Invalid JSON in")));
    assert!(
        warnings
            .iter()
            .any(|w| w.starts_with("Error processing resource"))
    );

    let categories: Vec<Category> = collection.categories().map(|(c, _)| c).collect();
    assert_eq!(
        categories,
        [Category::Skins],
        "levels had no loadable items"
    );
    let skins = collection.items(Category::Skins).unwrap();
    assert_eq!(skins.keys().collect::<Vec<_>>(), ["pixel"]);

    let item = collection.get_item(Category::Skins, "pixel").unwrap();
    // Localized: en wins for title, smallest language key for subtitle,
    // meta dropped; name set from the directory; resources recorded as SRLs
    // in name order (data.json before thumbnail.png).
    assert_eq!(
        item.as_object().unwrap().keys().collect::<Vec<_>>(),
        ["title", "subtitle", "name", "data", "thumbnail"]
    );
    assert_eq!(item["title"], json!("Pixel"));
    assert_eq!(item["subtitle"], json!("st-de"));
    assert_eq!(item["name"], json!("pixel"));

    // .json resources are gzipped before hashing; binary ones are not.
    let gzipped = gzip_compress(data_bytes);
    let data_hash = sha1_hex(&gzipped);
    assert_eq!(item["data"]["hash"], json!(data_hash.clone()));
    assert_eq!(
        item["data"]["url"],
        json!(format!("/sonolus/repository/{data_hash}"))
    );
    let stored = &collection.repository()[&data_hash];
    assert_eq!(gzip_decompress(stored).unwrap(), data_bytes);

    let png_hash = sha1_hex(png_bytes);
    assert_eq!(item["thumbnail"]["hash"], json!(png_hash.clone()));
    assert_eq!(collection.repository()[&png_hash], png_bytes.to_vec());
}

#[test]
fn load_from_source_missing_root_is_an_io_error() {
    let dir = tempfile::tempdir().unwrap();
    let missing = dir.path().join("does-not-exist");
    let mut collection = Collection::new();
    let err = collection.load_from_source(&missing).unwrap_err();
    assert!(matches!(err, CollectionError::Io(_)));
}

#[test]
fn load_from_scp_rejects_garbage() {
    let mut collection = Collection::new();
    let err = collection
        .load_from_scp(b"definitely not a zip")
        .unwrap_err();
    assert!(matches!(err, CollectionError::Zip(_)));
}
