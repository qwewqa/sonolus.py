//! Identity-pipeline corpus check (PORT.md T2.2 DoD).
//!
//! For every corpus entry and every optimization level
//! (`minimal`/`fast`/`standard`) this:
//!
//! 1. decodes the **frontend** CFG and compiles it at the level,
//! 2. replays every behavioral I/O vector against the result (result, log,
//!    writes must match the recorded values, excluding the temp memory block),
//!    and
//! 3. asserts the emitted node tree at `fast` and `standard` is **byte-identical**
//!    to `minimal`'s.
//!
//! Step 3 is the strongest identity statement available today: with the
//! optimization registry empty (`fast`/`standard` are pipeline prefixes that
//! contain no passes yet, decisions D5/D9), every level must produce the exact
//! same output. The structure isolates that assumption in
//! [`IDENTITY_LEVELS`]/[`identical_to_minimal`] so a wave task can relax it for
//! the levels it starts optimizing by editing one place, while keeping the
//! behavioral replay (step 2) at every level forever.

mod common;

use std::fs;

use sonolus_backend_core::decode::decode_cfg;
use sonolus_backend_core::nodes::{EngineNodes, format_engine_node};
use sonolus_backend_core::output::{generate_output_nodes, output_node_dump};
use sonolus_backend_core::pipeline::{Level, compile_cfg};

/// All levels exercised by the identity check.
const ALL_LEVELS: [Level; 3] = [Level::Minimal, Level::Fast, Level::Standard];

/// Levels whose output must currently be byte-identical to `minimal`'s. Today
/// that is every non-minimal level (the optimization registry is empty). A
/// wave task that starts optimizing at `fast` removes `Level::Fast` here.
const IDENTITY_LEVELS: [Level; 2] = [Level::Fast, Level::Standard];

fn identical_to_minimal(level: Level) -> bool {
    IDENTITY_LEVELS.contains(&level)
}

/// A stable byte-identity fingerprint of an engine-node tree: the debug node
/// dump plus the canonical output-node dump (covers arena structure, int/float
/// tags, and dedup/insertion order — exactly the shipped bytes' determinants).
fn node_identity(nodes: &EngineNodes) -> String {
    let tree = format_engine_node(&nodes.arena, nodes.root);
    let out = generate_output_nodes(&nodes.arena, nodes.root).expect("output generation");
    format!("{tree}\n--output--\n{}", output_node_dump(&out))
}

#[test]
fn every_corpus_vector_replays_at_every_level() {
    let dir = common::testdata_dir();
    let manifest = common::load_manifest();
    let mut replayed = 0u64;
    let mut entries_checked = 0usize;
    for entry in &manifest.entries {
        let cfg_bytes = fs::read(dir.join("cfgs").join(format!("{}.scfg", entry.hash)))
            .unwrap_or_else(|e| panic!("missing frontend CFG {}: {e}", entry.hash));
        let cfg = decode_cfg(&cfg_bytes)
            .unwrap_or_else(|e| panic!("{}: frontend CFG failed to decode: {e}", entry.hash));
        entries_checked += 1;

        // Compile at all three levels; capture minimal's identity for the
        // byte-identity assertion.
        let minimal = compile_cfg(&cfg, Level::Minimal)
            .unwrap_or_else(|e| panic!("{}: minimal compile failed: {e}", entry.hash));
        let minimal_identity = node_identity(&minimal);

        let file = (entry.vectors > 0).then(|| common::load_vector_file(&entry.hash));

        for level in ALL_LEVELS {
            let nodes = compile_cfg(&cfg, level)
                .unwrap_or_else(|e| panic!("{}: {} compile failed: {e}", entry.hash, level.name()));

            if identical_to_minimal(level) {
                assert_eq!(
                    node_identity(&nodes),
                    minimal_identity,
                    "{}: {} output must be byte-identical to minimal's today",
                    entry.hash,
                    level.name()
                );
            }

            // Behavioral replay at this level (every level, forever).
            if let Some(file) = &file {
                for (i, vector) in file.vectors.iter().enumerate() {
                    let label = format!(
                        "{}#{i} level={} (captured at {})",
                        &entry.hash[..12],
                        level.name(),
                        vector.level
                    );
                    common::run_and_check(&label, &nodes, vector);
                    if level == Level::Minimal {
                        replayed += 1;
                    }
                }
            }
        }
    }
    assert_eq!(entries_checked, manifest.entries.len());
    assert_eq!(
        replayed, manifest.vector_total,
        "every corpus vector must be replayed (counted once, at minimal)"
    );
    assert!(replayed > 0, "the corpus must contain vectors");
    println!(
        "identity-levels: {} entries x {} levels, {replayed} vectors replayed per level",
        manifest.entries.len(),
        ALL_LEVELS.len(),
    );
}
