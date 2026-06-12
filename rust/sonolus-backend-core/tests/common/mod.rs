//! Shared corpus-replay helpers used by `replay.rs` (post-pass CFG → emitter)
//! and `pipeline_replay.rs` (frontend CFG → minimal pipeline).
//!
//! # Equality
//!
//! Values compare by raw `f64` bits, with two deliberate exceptions matching
//! the legacy contract (Python `==`, made NaN-aware):
//!
//! - `NaN == NaN` regardless of payload (the schema's raw-bits encoding
//!   preserves payloads, but NaN payloads are not part of the semantic
//!   contract).
//! - `+0.0 == -0.0`: the legacy interpreter propagates Python `int` values
//!   from int constants, where `-0` is `+0` (e.g. `Negate` of an int-const
//!   zero); the f64-only Rust interpreter yields `-0.0` (a documented T1.1
//!   divergence class). The behavioral suite's Python `==` checks cannot
//!   distinguish them either.
//!
//! Writes are compared last-write-wins per `(block, index)`, excluding the
//! vector's `temp_memory_block` (slot allocation is pipeline-specific by
//! contract).

// Each test binary compiles its own copy of this module and uses a different
// subset of it (corpus replay vs. differential testing), so per-binary
// dead-code analysis needs a module-wide allow.
#![allow(dead_code)]

use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

use serde::Deserialize;
use serde_json::Value;
use sonolus_backend_core::interpret::Interpreter;
use sonolus_backend_core::nodes::EngineNodes;
use sonolus_backend_core::output::generate_output_nodes;

#[derive(Debug, Deserialize)]
pub struct Manifest {
    pub entries: Vec<Entry>,
    pub vector_total: u64,
}

#[derive(Debug, Deserialize)]
pub struct Entry {
    pub hash: String,
    pub vectors: u64,
}

#[derive(Debug, Deserialize)]
pub struct VectorFile {
    pub schema: u32,
    pub cfg: String,
    pub vectors: Vec<Vector>,
}

#[derive(Debug, Deserialize)]
pub struct Vector {
    pub level: String,
    pub temp_memory_block: i64,
    /// Used by `replay.rs` only (each test binary compiles its own copy of
    /// this module, so per-binary dead-code analysis needs the allow).
    #[allow(dead_code)]
    pub post_cfg: Option<String>,
    pub inputs: Vec<(i64, Vec<Value>)>,
    pub rng: Vec<(String, Value, Value, Value)>,
    pub result: Value,
    pub log: Vec<Value>,
    pub writes: Vec<(i64, i64, Value)>,
}

pub fn testdata_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("testdata")
}

pub fn load_manifest() -> Manifest {
    let dir = testdata_dir();
    serde_json::from_slice(&fs::read(dir.join("manifest.json")).expect("manifest readable"))
        .expect("manifest parses")
}

pub fn load_vector_file(hash: &str) -> VectorFile {
    let path = testdata_dir().join("vectors").join(format!("{hash}.json"));
    let file: VectorFile = serde_json::from_slice(
        &fs::read(&path).unwrap_or_else(|e| panic!("missing vector file {}: {e}", path.display())),
    )
    .unwrap_or_else(|e| panic!("vector file {} invalid: {e}", path.display()));
    assert_eq!(
        file.schema,
        2,
        "unknown vector schema in {}",
        path.display()
    );
    assert_eq!(file.cfg, hash);
    file
}

/// Decodes a vector-schema value: a JSON number (finite) or a raw-bits string
/// (`"0x%016x"`, non-finite).
pub fn decode_value(value: &Value) -> f64 {
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
pub fn values_match(actual: f64, expected: f64) -> bool {
    if actual.is_nan() && expected.is_nan() {
        return true;
    }
    if actual == 0.0 && expected == 0.0 {
        return true;
    }
    actual.to_bits() == expected.to_bits()
}

/// Runs an engine-node tree against one vector's inputs and RNG tape, then
/// asserts the observed result/log/writes match the recorded ones (excluding
/// the temp memory block). Also checks output-node generation succeeds with
/// the root last.
pub fn run_and_check(label: &str, nodes: &EngineNodes, vector: &Vector) {
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
        .run(nodes)
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

    // Writes: last-write-wins per (block, index), excluding temp memory.
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
