//! Mini-corpus validation (PORT.md task T0.5).
//!
//! Loads the curated corpus checked into `rust/testdata/` (produced by
//! `tools/gen_corpus.py` from a `SONOLUS_CAPTURE_CORPUS` pytest run), decodes every
//! CFG, checks the canonical dump against the Python-side dump stored at capture
//! time (bit-exact, per `rust/ENCODING.md` §5), and asserts the manifest matches
//! the directory contents. A negative test checks that perturbed CFG byte streams
//! are rejected or detected by the round-trip comparison.

use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};

use serde::Deserialize;
use sonolus_backend_core::cfg::canonical_dump;
use sonolus_backend_core::decode::decode_cfg;

const CORPUS_BUDGET_BYTES: u64 = 5_000_000;

#[derive(Debug, Deserialize)]
struct Manifest {
    schema: u32,
    encoding_version: u16,
    count: usize,
    vector_total: u64,
    cfg_bytes: u64,
    dump_bytes: u64,
    vector_bytes: u64,
    post_cfg_count: usize,
    post_cfg_bytes: u64,
    total_bytes: u64,
    entries: Vec<Entry>,
}

#[derive(Debug, Deserialize)]
struct Entry {
    hash: String,
    cfg_size: u64,
    dump_size: u64,
    vector_size: u64,
    vectors: u64,
    post_cfgs: Vec<String>,
}

fn testdata_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("testdata")
}

fn load_manifest() -> Manifest {
    let path = testdata_dir().join("manifest.json");
    let data = fs::read(&path).unwrap_or_else(|e| panic!("failed to read {}: {e}", path.display()));
    let manifest: Manifest = serde_json::from_slice(&data).expect("manifest.json must parse");
    assert_eq!(manifest.schema, 2, "unknown manifest schema");
    assert_eq!(
        manifest.encoding_version,
        sonolus_backend_core::decode::ENCODING_VERSION,
        "corpus encoding version must match the decoder"
    );
    assert!(!manifest.entries.is_empty(), "corpus must not be empty");
    manifest
}

fn read_entry_files(entry: &Entry) -> (Vec<u8>, String) {
    let dir = testdata_dir();
    let cfg = fs::read(dir.join("cfgs").join(format!("{}.scfg", entry.hash)))
        .unwrap_or_else(|e| panic!("missing cfg file for {}: {e}", entry.hash));
    let dump = fs::read(dir.join("dumps").join(format!("{}.txt", entry.hash)))
        .unwrap_or_else(|e| panic!("missing dump file for {}: {e}", entry.hash));
    let dump = String::from_utf8(dump).expect("stored dump must be UTF-8");
    (cfg, dump)
}

fn dir_file_names(dir: &Path) -> BTreeSet<String> {
    fs::read_dir(dir)
        .unwrap_or_else(|e| panic!("failed to list {}: {e}", dir.display()))
        .map(|entry| {
            entry
                .expect("readable dir entry")
                .file_name()
                .to_string_lossy()
                .into_owned()
        })
        .collect()
}

#[test]
fn every_corpus_cfg_decodes_and_round_trips() {
    let manifest = load_manifest();
    let mut cfg_bytes = 0u64;
    let mut dump_bytes = 0u64;
    for entry in &manifest.entries {
        let (data, expected_dump) = read_entry_files(entry);
        assert_eq!(
            data.len() as u64,
            entry.cfg_size,
            "cfg size mismatch for {}",
            entry.hash
        );
        assert_eq!(
            expected_dump.len() as u64,
            entry.dump_size,
            "dump size mismatch for {} (check .gitattributes eol settings)",
            entry.hash
        );
        let cfg =
            decode_cfg(&data).unwrap_or_else(|e| panic!("decode failed for {}: {e}", entry.hash));
        let actual_dump = canonical_dump(&cfg);
        assert_eq!(
            actual_dump, expected_dump,
            "canonical dump mismatch for {} (Rust != stored Python dump)",
            entry.hash
        );
        cfg_bytes += entry.cfg_size;
        dump_bytes += entry.dump_size;
    }
    assert_eq!(
        manifest.count,
        manifest.entries.len(),
        "manifest count mismatch"
    );
    assert_eq!(manifest.cfg_bytes, cfg_bytes, "manifest cfg_bytes mismatch");
    assert_eq!(
        manifest.dump_bytes, dump_bytes,
        "manifest dump_bytes mismatch"
    );
    let vector_bytes: u64 = manifest.entries.iter().map(|e| e.vector_size).sum();
    assert_eq!(
        manifest.vector_bytes, vector_bytes,
        "manifest vector_bytes mismatch"
    );
    // Post-pass CFGs: the union of all entry references must decode cleanly and
    // match the manifest's count/size accounting.
    let post_hashes: BTreeSet<&String> =
        manifest.entries.iter().flat_map(|e| &e.post_cfgs).collect();
    assert_eq!(
        manifest.post_cfg_count,
        post_hashes.len(),
        "manifest post_cfg_count mismatch"
    );
    let mut post_cfg_bytes = 0u64;
    for hash in &post_hashes {
        let path = testdata_dir()
            .join("post_cfgs")
            .join(format!("{hash}.scfg"));
        let data = fs::read(&path).unwrap_or_else(|e| panic!("missing post-pass CFG {hash}: {e}"));
        decode_cfg(&data).unwrap_or_else(|e| panic!("post-pass CFG {hash} failed to decode: {e}"));
        post_cfg_bytes += data.len() as u64;
    }
    assert_eq!(
        manifest.post_cfg_bytes, post_cfg_bytes,
        "manifest post_cfg_bytes mismatch"
    );
    assert_eq!(
        manifest.total_bytes,
        cfg_bytes + dump_bytes + vector_bytes + post_cfg_bytes,
        "manifest total_bytes mismatch"
    );
    assert!(
        manifest.total_bytes <= CORPUS_BUDGET_BYTES,
        "corpus exceeds its byte budget: {} > {CORPUS_BUDGET_BYTES}",
        manifest.total_bytes
    );
}

#[test]
fn manifest_matches_directory_contents_exactly() {
    let manifest = load_manifest();
    let dir = testdata_dir();
    let expected_cfgs: BTreeSet<String> = manifest
        .entries
        .iter()
        .map(|e| format!("{}.scfg", e.hash))
        .collect();
    let expected_dumps: BTreeSet<String> = manifest
        .entries
        .iter()
        .map(|e| format!("{}.txt", e.hash))
        .collect();
    let expected_vectors: BTreeSet<String> = manifest
        .entries
        .iter()
        .filter(|e| e.vectors > 0)
        .map(|e| format!("{}.json", e.hash))
        .collect();
    let expected_post_cfgs: BTreeSet<String> = manifest
        .entries
        .iter()
        .flat_map(|e| &e.post_cfgs)
        .map(|h| format!("{h}.scfg"))
        .collect();
    assert_eq!(
        expected_cfgs.len(),
        manifest.count,
        "duplicate hashes in manifest"
    );
    assert_eq!(
        dir_file_names(&dir.join("cfgs")),
        expected_cfgs,
        "cfgs/ does not match the manifest"
    );
    assert_eq!(
        dir_file_names(&dir.join("dumps")),
        expected_dumps,
        "dumps/ does not match the manifest"
    );
    assert_eq!(
        dir_file_names(&dir.join("vectors")),
        expected_vectors,
        "vectors/ does not match the manifest"
    );
    assert_eq!(
        dir_file_names(&dir.join("post_cfgs")),
        expected_post_cfgs,
        "post_cfgs/ does not match the manifest"
    );
}

#[test]
fn vector_files_match_manifest() {
    let manifest = load_manifest();
    let dir = testdata_dir();
    let mut vector_total = 0u64;
    for entry in manifest.entries.iter().filter(|e| e.vectors > 0) {
        let path = dir.join("vectors").join(format!("{}.json", entry.hash));
        let data = fs::read(&path)
            .unwrap_or_else(|e| panic!("missing vector file for {}: {e}", entry.hash));
        assert_eq!(
            data.len() as u64,
            entry.vector_size,
            "vector size mismatch for {}",
            entry.hash
        );
        let value: serde_json::Value = serde_json::from_slice(&data)
            .unwrap_or_else(|e| panic!("vector JSON invalid for {}: {e}", entry.hash));
        assert_eq!(
            value["schema"],
            serde_json::json!(2),
            "vector schema version for {}",
            entry.hash
        );
        assert_eq!(
            value["cfg"].as_str(),
            Some(entry.hash.as_str()),
            "vector file cfg field mismatch for {}",
            entry.hash
        );
        let vectors = value["vectors"]
            .as_array()
            .expect("vectors must be an array");
        assert_eq!(
            vectors.len() as u64,
            entry.vectors,
            "vector count mismatch for {}",
            entry.hash
        );
        // Every vector's post_cfg link must be listed in the entry's post_cfgs.
        for vector in vectors {
            if let Some(post) = vector["post_cfg"].as_str() {
                assert!(
                    entry.post_cfgs.iter().any(|h| h == post),
                    "vector post_cfg {post} not in entry post_cfgs for {}",
                    entry.hash
                );
            }
        }
        vector_total += entry.vectors;
    }
    assert_eq!(
        manifest.vector_total, vector_total,
        "manifest vector_total mismatch"
    );
}

#[test]
fn perturbed_cfg_byte_streams_are_rejected_or_detected() {
    let manifest = load_manifest();
    // Smallest CFG (deterministic tie-break by hash) keeps the full byte sweep fast.
    let entry = manifest
        .entries
        .iter()
        .min_by_key(|e| (e.cfg_size, e.hash.clone()))
        .expect("corpus must not be empty");
    let (data, expected_dump) = read_entry_files(entry);
    let mut undetected = Vec::new();
    for i in 0..data.len() {
        let mut corrupted = data.clone();
        corrupted[i] ^= 0xFF;
        match decode_cfg(&corrupted) {
            Err(_) => {} // Rejected outright: detected.
            Ok(cfg) => {
                // Decoded to something: the round-trip dump comparison must differ.
                if canonical_dump(&cfg) == expected_dump {
                    undetected.push(i);
                }
            }
        }
    }
    assert!(
        undetected.is_empty(),
        "byte flips at offsets {undetected:?} of {} were not detected by the round-trip check",
        entry.hash
    );
}
