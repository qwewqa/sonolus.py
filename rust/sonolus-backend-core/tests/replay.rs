//! Corpus replay (PORT.md task T1.2 DoD): for every behavioral I/O vector in the
//! checked-in mini-corpus, decode the linked post-pass CFG, emit the engine-node
//! tree (T1.2 emitter), generate output nodes, then run the tree on the T1.1
//! interpreter with the vector's inputs and RNG tape. The observed `result`,
//! `log`, and `writes` must match the values recorded from the legacy Python
//! interpreter.
//!
//! # Equality
//!
//! Values compare by raw `f64` bits, with two deliberate exceptions that match
//! the legacy contract (Python `==`, made NaN-aware):
//!
//! - `NaN == NaN` regardless of payload (the schema's raw-bits encoding preserves
//!   payloads, but NaN payloads are not part of the semantic contract).
//! - `+0.0 == -0.0`: the legacy interpreter propagates Python `int` values from
//!   int constants, where `-0` is `+0` (e.g. `Negate` of an int-const zero); the
//!   f64-only Rust interpreter yields `-0.0` (a documented T1.1 divergence class).
//!   The behavioral suite's Python `==` checks cannot distinguish them either.
//!
//! Writes are compared last-write-wins per `(block, index)`, excluding the
//! vector's `temp_memory_block` (slot allocation is pipeline-specific by
//! contract, even though this replay currently uses the same Python allocation).

use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

use serde::Deserialize;
use serde_json::Value;
use sonolus_backend_core::decode::decode_cfg;
use sonolus_backend_core::emit::cfg_to_engine_nodes;
use sonolus_backend_core::interpret::Interpreter;
use sonolus_backend_core::output::generate_output_nodes;

#[derive(Debug, Deserialize)]
struct Manifest {
    entries: Vec<Entry>,
    vector_total: u64,
}

#[derive(Debug, Deserialize)]
struct Entry {
    hash: String,
    vectors: u64,
}

#[derive(Debug, Deserialize)]
struct VectorFile {
    schema: u32,
    cfg: String,
    vectors: Vec<Vector>,
}

#[derive(Debug, Deserialize)]
struct Vector {
    level: String,
    temp_memory_block: i64,
    post_cfg: Option<String>,
    inputs: Vec<(i64, Vec<Value>)>,
    rng: Vec<(String, Value, Value, Value)>,
    result: Value,
    log: Vec<Value>,
    writes: Vec<(i64, i64, Value)>,
}

fn testdata_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("testdata")
}

/// Decodes a vector-schema value: a JSON number (finite) or a raw-bits string
/// (`"0x%016x"`, non-finite).
fn decode_value(value: &Value) -> f64 {
    match value {
        Value::Number(n) => n.as_f64().expect("vector numbers fit f64"),
        Value::String(s) => {
            let bits = u64::from_str_radix(
                s.strip_prefix("0x").expect("raw-bits values start with 0x"),
                16,
            )
            .expect("raw-bits values are 16 hex digits");
            f64::from_bits(bits)
        }
        other => panic!("unexpected vector value: {other}"),
    }
}

/// See the module docs: bit equality except NaN==NaN and +0.0==-0.0.
fn values_match(actual: f64, expected: f64) -> bool {
    if actual.is_nan() && expected.is_nan() {
        return true;
    }
    if actual == 0.0 && expected == 0.0 {
        return true;
    }
    actual.to_bits() == expected.to_bits()
}

fn replay_vector(label: &str, post_cfg_bytes: &[u8], vector: &Vector) {
    let cfg = decode_cfg(post_cfg_bytes)
        .unwrap_or_else(|e| panic!("{label}: post-pass CFG failed to decode: {e}"));
    let nodes = cfg_to_engine_nodes(&cfg).unwrap_or_else(|e| panic!("{label}: emit failed: {e}"));

    // Output-node generation must succeed on every emitted tree, with arguments
    // strictly before their parents and the root last.
    let output = generate_output_nodes(&nodes.arena, nodes.root)
        .unwrap_or_else(|e| panic!("{label}: output-node generation failed: {e}"));
    assert_eq!(
        output.root as usize,
        output.nodes.len() - 1,
        "{label}: root must be the last output node"
    );

    let tape: Vec<f64> = vector
        .rng
        .iter()
        .map(|(_, _, _, value)| decode_value(value))
        .collect();
    let mut interpreter = Interpreter::with_tape(tape);
    interpreter.record_writes();
    for (block, values) in &vector.inputs {
        interpreter.set_block(*block, values.iter().map(decode_value).collect());
    }

    let result = interpreter
        .run(&nodes)
        .unwrap_or_else(|e| panic!("{label}: interpreter failed: {e}"));
    let expected_result = decode_value(&vector.result);
    assert!(
        values_match(result, expected_result),
        "{label}: result {result:?} != expected {expected_result:?}"
    );

    let expected_log: Vec<f64> = vector.log.iter().map(decode_value).collect();
    let log = interpreter.log();
    assert_eq!(
        log.len(),
        expected_log.len(),
        "{label}: log length mismatch"
    );
    for (i, (&actual, &expected)) in log.iter().zip(&expected_log).enumerate() {
        assert!(
            values_match(actual, expected),
            "{label}: log[{i}] {actual:?} != expected {expected:?}"
        );
    }

    // Writes: last-write-wins per (block, index), excluding the temp memory block.
    let expected_writes: BTreeMap<(i64, i64), f64> = vector
        .writes
        .iter()
        .filter(|(block, _, _)| *block != vector.temp_memory_block)
        .map(|(block, index, value)| ((*block, *index), decode_value(value)))
        .collect();
    let actual_writes: BTreeMap<(i64, i64), f64> = interpreter
        .recorded_writes()
        .expect("write recording was enabled")
        .into_iter()
        .filter(|(block, _, _)| *block != vector.temp_memory_block)
        .map(|(block, index, value)| ((block, index), value))
        .collect();
    let expected_cells: Vec<_> = expected_writes.keys().collect();
    let actual_cells: Vec<_> = actual_writes.keys().collect();
    assert_eq!(
        actual_cells, expected_cells,
        "{label}: written cells differ"
    );
    for ((cell, &actual), &expected) in actual_writes.iter().zip(expected_writes.values()) {
        assert!(
            values_match(actual, expected),
            "{label}: write {cell:?} {actual:?} != expected {expected:?}"
        );
    }
}

#[test]
fn every_corpus_vector_replays_on_the_rust_pipeline() {
    let dir = testdata_dir();
    let manifest: Manifest =
        serde_json::from_slice(&fs::read(dir.join("manifest.json")).expect("manifest readable"))
            .expect("manifest parses");
    let mut replayed = 0u64;
    for entry in manifest.entries.iter().filter(|e| e.vectors > 0) {
        let path = dir.join("vectors").join(format!("{}.json", entry.hash));
        let file: VectorFile = serde_json::from_slice(
            &fs::read(&path).unwrap_or_else(|e| panic!("missing vector file {path:?}: {e}")),
        )
        .unwrap_or_else(|e| panic!("vector file {path:?} invalid: {e}"));
        assert_eq!(file.schema, 2, "unknown vector schema in {path:?}");
        assert_eq!(file.cfg, entry.hash);
        for (i, vector) in file.vectors.iter().enumerate() {
            let label = format!("{}#{i} (level {})", &entry.hash[..12], vector.level);
            let post = vector.post_cfg.as_ref().unwrap_or_else(|| {
                panic!("{label}: vector has no post_cfg link (post-pass encode reject?)")
            });
            let post_bytes = fs::read(dir.join("post_cfgs").join(format!("{post}.scfg")))
                .unwrap_or_else(|e| panic!("{label}: missing post-pass CFG {post}: {e}"));
            replay_vector(&label, &post_bytes, vector);
            replayed += 1;
        }
    }
    assert_eq!(
        replayed, manifest.vector_total,
        "every corpus vector must be replayed"
    );
    assert!(replayed > 0, "the corpus must contain vectors");
}
