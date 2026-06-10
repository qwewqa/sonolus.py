//! Corpus pipeline replay (PORT.md task T1.3 DoD): for every behavioral I/O
//! vector in the checked-in mini-corpus, decode the **frontend** CFG, run the
//! Rust `minimal` pipeline (`compile_cfg`), generate output nodes, then run
//! the tree on the T1.1 interpreter with the vector's inputs and RNG tape.
//! The observed `result`, `log`, and `writes` (excluding the temp memory
//! block, whose layout is pipeline-specific) must match the values recorded
//! from the legacy Python interpreter running the legacy minimal artifact.
//!
//! This is the strongest minimal-pipeline check available offline: the Rust
//! pipeline starts from the *pre-optimization* CFG, so cleanups, binarization,
//! flattening, allocation, lowering, and emission are all on the line.
//!
//! Comparison semantics are shared with `replay.rs` via `common/mod.rs`.

mod common;

use std::fs;

use sonolus_backend_core::decode::decode_cfg;
use sonolus_backend_core::pipeline::{Level, compile_cfg_stats};

#[test]
fn every_corpus_vector_replays_through_the_minimal_pipeline() {
    let dir = common::testdata_dir();
    let manifest = common::load_manifest();
    let mut replayed = 0u64;
    let mut max_slots = 0u32;
    for entry in manifest.entries.iter().filter(|e| e.vectors > 0) {
        let cfg_bytes = fs::read(dir.join("cfgs").join(format!("{}.scfg", entry.hash)))
            .unwrap_or_else(|e| panic!("missing frontend CFG {}: {e}", entry.hash));
        let cfg = decode_cfg(&cfg_bytes)
            .unwrap_or_else(|e| panic!("{}: frontend CFG failed to decode: {e}", entry.hash));
        let (nodes, stats) = compile_cfg_stats(&cfg, Level::Minimal)
            .unwrap_or_else(|e| panic!("{}: minimal pipeline failed: {e}", entry.hash));
        max_slots = max_slots.max(stats.temp_slots_used);
        let file = common::load_vector_file(&entry.hash);
        for (i, vector) in file.vectors.iter().enumerate() {
            let label = format!(
                "{}#{i} (captured at level {})",
                &entry.hash[..12],
                vector.level
            );
            common::run_and_check(&label, &nodes, vector);
            replayed += 1;
        }
    }
    assert_eq!(
        replayed, manifest.vector_total,
        "every corpus vector must be replayed"
    );
    assert!(replayed > 0, "the corpus must contain vectors");
    assert!(max_slots <= 4096);
    println!(
        "pipeline replay: {replayed}/{} vectors, max temp slots used {max_slots}",
        manifest.vector_total
    );
}

#[test]
fn every_corpus_cfg_compiles_at_minimal() {
    // Vector-less corpus CFGs still must compile (budget + domain coverage).
    let dir = common::testdata_dir();
    let manifest = common::load_manifest();
    let mut compiled = 0usize;
    for entry in &manifest.entries {
        let cfg_bytes = fs::read(dir.join("cfgs").join(format!("{}.scfg", entry.hash)))
            .unwrap_or_else(|e| panic!("missing frontend CFG {}: {e}", entry.hash));
        let cfg = decode_cfg(&cfg_bytes)
            .unwrap_or_else(|e| panic!("{}: frontend CFG failed to decode: {e}", entry.hash));
        compile_cfg_stats(&cfg, Level::Minimal)
            .unwrap_or_else(|e| panic!("{}: minimal pipeline failed: {e}", entry.hash));
        compiled += 1;
    }
    assert_eq!(compiled, manifest.entries.len());
}
