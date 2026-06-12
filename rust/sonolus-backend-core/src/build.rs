//! Single-call engine build: payload -> six packaged engine blobs (PORT.md task T4.2).
//!
//! Consumes the schema-v1 payload specified in `rust/PAYLOAD.md` (produced by
//! `sonolus/build/payload.py`, task T4.1) and produces the decompressed-content
//! equivalents of the legacy `sonolus/build/engine.py::package_engine` output:
//! `EngineConfiguration`, `EnginePlayData`, `EngineWatchData`, `EnginePreviewData`,
//! `EngineTutorialData`, and `EngineRom`, each gzipped with mtime 0.
//!
//! # Purity and determinism
//!
//! [`build_engine`] is a **pure function** (invariant §3.8, decision D6): no state
//! survives the call and there are no caches with cross-call lifetime. Identical
//! payloads produce identical bytes, including under rayon (invariant §3.5): units
//! are compiled in parallel, but every output-ordering step uses unit list order —
//! per-unit compilation results land in unique-cfg-indexed slots, never in thread
//! completion order.
//!
//! # Dedup (PAYLOAD.md §5 step 2, decision D6)
//!
//! Within one call, units (in any mode) whose encoded `cfg` bytes are identical
//! compile once. The dedup identity is the exact byte string: the map is keyed by
//! the bytes themselves, so equal keys are byte-confirmed by construction. Note
//! the standing consequence: the pipeline currently takes no `(mode, callback)`
//! context, which is what makes byte-identical CFGs share output; if a future pass
//! consumes such context, the dedup key (and the payload contract) must grow with
//! it.
//!
//! # Node arrays (PAYLOAD.md §5 step 3)
//!
//! Each mode gets one [`OutputNodeGenerator`](crate::output::OutputNodeGenerator):
//! unit trees are added in unit list order, sharing sub-trees across the whole
//! mode array exactly like the legacy per-mode generator. `node_index(unit)` is
//! the returned root index; derived archetypes reference the same unit and
//! therefore the same node index.
//!
//! # Metadata rewrite and serialization (PAYLOAD.md §5 steps 4-5)
//!
//! The mode `metadata` JSON is parsed with key order preserved (`serde_json`
//! `preserve_order`), the unit-id callback slots are replaced by node indices, and
//! the document is re-serialized with the `CPython`-exact compact serializer
//! (`collection::pyjson::dumps_compact`). The `"nodes": null` placeholder is
//! spliced as a separately rendered array because node values may be ±inf (raw
//! `SwitchWithDefault` conds), which legacy `json.dumps` writes as
//! `Infinity`/`-Infinity` — tokens `serde_json` values cannot hold. Int-tagged
//! node values render as JSON integers, float-tagged ones with `repr(float)`
//! semantics, matching the legacy Python `int`/`float` objects byte for byte.
//!
//! Known limitation (accepted, PAYLOAD.md §3 round-trip): metadata integers
//! beyond u64/i64 would be re-serialized through f64. Engine metadata contains
//! no such values (indices, ids, finite numeric defaults).
//!
//! # Errors
//!
//! Any failure aborts the whole call ([`BuildEngineError`]); there are no partial
//! results. Unit compilation failures display the inner pipeline message
//! *verbatim* (legacy parity: e.g. `Temporary memory limit exceeded` surfaces
//! exactly as the legacy `ValueError` text); the ROM overflow error mirrors
//! `struct.pack("<f", ...)`'s `OverflowError` message (PAYLOAD.md §5 step 6 —
//! error, never saturate). Payload-shape violations (which the frozen producer
//! cannot emit) carry mode/unit context.
//!
//! # FFI return shape (PAYLOAD.md §5 step 8)
//!
//! The `sonolus_backend.build_engine` binding returns [`PackagedEngine`] as a
//! Python dict with the legacy dataclass's field names:
//! `{"configuration", "play_data", "watch_data", "preview_data",
//! "tutorial_data", "rom"}`, each a gzipped `bytes` value.

use std::collections::HashMap;
use std::fmt;
use std::fmt::Write as _;

use rayon::prelude::*;
use serde_json::Value;

use crate::collection::gzip_compress;
use crate::collection::pyjson;
use crate::decode::{DecodeError, decode_cfg};
use crate::nodes::EngineNodes;
use crate::output::{OutputError, OutputNode, OutputNodeGenerator};
use crate::pipeline::{CompileError, Level, compile_cfg};

/// The payload schema version this assembler consumes (PAYLOAD.md §1).
pub const PAYLOAD_SCHEMA_VERSION: i64 = 1;

/// The required `modes` keys, in payload order (PAYLOAD.md §1).
pub const MODE_NAMES: [&str; 4] = ["play", "watch", "preview", "tutorial"];

/// Archetype-entry keys that are *not* callback slots (PAYLOAD.md §3.2).
const ARCHETYPE_STATIC_KEYS: [&str; 4] = ["name", "hasInput", "imports", "exports"];

/// The converted schema-v1 engine build payload (PAYLOAD.md §1). The FFI layer
/// validates the Python dict shape and produces this; everything in core is
/// pure Rust.
#[derive(Debug, Clone)]
pub struct EnginePayload {
    /// Optimization level for all work units.
    pub level: Level,
    /// The complete `EngineConfiguration` JSON document, pre-serialized.
    pub configuration: String,
    /// ROM values in trace order (never empty; the producer applies the
    /// legacy `values or [0]` guard).
    pub rom: Vec<f64>,
    /// Exactly four modes: play, watch, preview, tutorial, in that order.
    pub modes: Vec<ModePayload>,
}

/// One mode of the payload (PAYLOAD.md §1).
#[derive(Debug, Clone)]
pub struct ModePayload {
    /// The mode name (one of [`MODE_NAMES`]; carried for diagnostics).
    pub name: String,
    /// The mode-data JSON document in its final shape minus per-node data
    /// (PAYLOAD.md §3).
    pub metadata: String,
    /// The mode's work units in canonical order; list position = unit id.
    pub units: Vec<WorkUnit>,
}

/// One work unit (PAYLOAD.md §2).
#[derive(Debug, Clone)]
pub struct WorkUnit {
    /// The runtime callback name (camelCase).
    pub callback: String,
    /// `None` for a mode-global callback, else the index of the first
    /// archetype entry whose base owns this callback.
    pub archetype: Option<i64>,
    /// The callback's order value (informational here; the metadata slot
    /// carries the value that ships).
    pub order: i64,
    /// The frontend CFG in the v1 binary encoding (`rust/ENCODING.md`).
    pub cfg: Vec<u8>,
}

/// The six packaged engine blobs, each gzipped with mtime 0 — the Rust
/// equivalent of the legacy `PackagedEngine` dataclass.
#[derive(Debug, Clone)]
pub struct PackagedEngine {
    pub configuration: Vec<u8>,
    pub play_data: Vec<u8>,
    pub watch_data: Vec<u8>,
    pub preview_data: Vec<u8>,
    pub tutorial_data: Vec<u8>,
    pub rom: Vec<u8>,
}

/// A failed engine build. See the module docs for the display contract.
#[derive(Debug)]
pub enum BuildEngineError {
    /// The `modes` list does not consist of exactly the four known modes in
    /// payload order.
    Modes(String),
    /// The ROM value list is empty (the producer guarantees `[0.0]` minimum).
    EmptyRom,
    /// A finite ROM value overflows f32 — the legacy `struct.pack("<f", ...)`
    /// `OverflowError` (PAYLOAD.md §5 step 6: error, do not saturate).
    RomOverflow { index: usize, value: f64 },
    /// A unit's encoded CFG failed to decode.
    Decode {
        mode: String,
        unit: usize,
        error: DecodeError,
    },
    /// A unit failed to compile. Displays the inner message verbatim
    /// (legacy parity, e.g. "Temporary memory limit exceeded").
    Compile {
        mode: String,
        unit: usize,
        error: CompileError,
    },
    /// Output-node generation failed for a unit (unreachable for
    /// emitter-produced trees).
    Output {
        mode: String,
        unit: usize,
        error: OutputError,
    },
    /// The mode metadata failed to parse or violated the slot/unit-id
    /// contract (PAYLOAD.md §5 step 4).
    Metadata { mode: String, message: String },
}

impl fmt::Display for BuildEngineError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Modes(message) => write!(f, "invalid payload modes: {message}"),
            Self::EmptyRom => write!(f, "the payload ROM value list must not be empty"),
            // The exact CPython `struct.pack("<f", ...)` OverflowError text.
            Self::RomOverflow { .. } => write!(f, "float too large to pack with f format"),
            Self::Decode { mode, unit, error } => {
                write!(f, "mode {mode} unit {unit}: failed to decode CFG: {error}")
            }
            // Verbatim passthrough (legacy parity); the mode/unit context
            // stays available programmatically on the variant.
            Self::Compile { error, .. } => write!(f, "{error}"),
            Self::Output { mode, unit, error } => write!(f, "mode {mode} unit {unit}: {error}"),
            Self::Metadata { mode, message } => write!(f, "mode {mode} metadata: {message}"),
        }
    }
}

impl std::error::Error for BuildEngineError {}

/// Builds the six engine blobs from a converted payload. Pure function; see
/// the module docs for the full contract.
///
/// # Errors
///
/// See [`BuildEngineError`]; any failure aborts the whole call.
pub fn build_engine(payload: &EnginePayload) -> Result<PackagedEngine, BuildEngineError> {
    validate_modes(&payload.modes)?;
    if payload.rom.is_empty() {
        return Err(BuildEngineError::EmptyRom);
    }

    // Dedup (D6): unique encoded byte strings across all modes, in
    // first-encounter order; every unit maps to its unique-cfg index.
    let (unique_cfgs, unit_unique) = dedup_units(&payload.modes);

    // Compile each unique CFG exactly once, in parallel. Results are
    // collected in unique-index order, so assembly below is independent of
    // thread completion order (invariant §3.5).
    let level = payload.level;
    let compiled: Vec<Result<EngineNodes, UnitFailure>> = unique_cfgs
        .par_iter()
        .map(|&cfg| compile_unit(cfg, level))
        .collect();
    let mut trees: Vec<EngineNodes> = Vec::with_capacity(compiled.len());
    for (unique_idx, result) in compiled.into_iter().enumerate() {
        match result {
            Ok(nodes) => trees.push(nodes),
            Err(failure) => {
                // Attribute the failure to the first unit that introduced
                // this unique CFG (deterministic: unique order is
                // first-encounter order).
                let (mode_idx, unit) = first_unit_of_unique(&unit_unique, unique_idx);
                let mode = payload.modes[mode_idx].name.clone();
                return Err(match failure {
                    UnitFailure::Decode(error) => BuildEngineError::Decode { mode, unit, error },
                    UnitFailure::Compile(error) => BuildEngineError::Compile { mode, unit, error },
                });
            }
        }
    }

    let mut mode_blobs: Vec<Vec<u8>> = Vec::with_capacity(payload.modes.len());
    for (mode_idx, mode) in payload.modes.iter().enumerate() {
        mode_blobs.push(assemble_mode(mode, &unit_unique[mode_idx], &trees)?);
    }
    let mut mode_blobs = mode_blobs.into_iter();

    Ok(PackagedEngine {
        configuration: gzip_compress(payload.configuration.as_bytes()),
        play_data: mode_blobs.next().expect("four modes were validated"),
        watch_data: mode_blobs.next().expect("four modes were validated"),
        preview_data: mode_blobs.next().expect("four modes were validated"),
        tutorial_data: mode_blobs.next().expect("four modes were validated"),
        rom: gzip_compress(&pack_rom(&payload.rom)?),
    })
}

fn validate_modes(modes: &[ModePayload]) -> Result<(), BuildEngineError> {
    let names: Vec<&str> = modes.iter().map(|m| m.name.as_str()).collect();
    if names != MODE_NAMES {
        return Err(BuildEngineError::Modes(format!(
            "expected exactly {MODE_NAMES:?} in order, got {names:?}"
        )));
    }
    Ok(())
}

/// One unit's compilation failure (pre-attribution).
enum UnitFailure {
    Decode(DecodeError),
    Compile(CompileError),
}

fn compile_unit(cfg: &[u8], level: Level) -> Result<EngineNodes, UnitFailure> {
    let cfg = decode_cfg(cfg).map_err(UnitFailure::Decode)?;
    compile_cfg(&cfg, level).map_err(UnitFailure::Compile)
}

/// Returns `(unique_cfgs, unit_unique)`: the distinct `cfg` byte strings in
/// first-encounter order across all modes, and for each mode the per-unit
/// index into `unique_cfgs`. The dedup identity is the exact byte string
/// (D6): keys are the bytes themselves, so hash hits are byte-confirmed by
/// the map's equality check.
fn dedup_units(modes: &[ModePayload]) -> (Vec<&[u8]>, Vec<Vec<usize>>) {
    let mut index_of: HashMap<&[u8], usize> = HashMap::new();
    let mut unique_cfgs: Vec<&[u8]> = Vec::new();
    let mut unit_unique: Vec<Vec<usize>> = Vec::with_capacity(modes.len());
    for mode in modes {
        let mut per_unit = Vec::with_capacity(mode.units.len());
        for unit in &mode.units {
            let unique_idx = *index_of.entry(unit.cfg.as_slice()).or_insert_with(|| {
                unique_cfgs.push(unit.cfg.as_slice());
                unique_cfgs.len() - 1
            });
            per_unit.push(unique_idx);
        }
        unit_unique.push(per_unit);
    }
    (unique_cfgs, unit_unique)
}

/// The first `(mode index, unit id)` referencing a unique-cfg index.
fn first_unit_of_unique(unit_unique: &[Vec<usize>], unique_idx: usize) -> (usize, usize) {
    for (mode_idx, per_unit) in unit_unique.iter().enumerate() {
        for (unit, &idx) in per_unit.iter().enumerate() {
            if idx == unique_idx {
                return (mode_idx, unit);
            }
        }
    }
    unreachable!("every unique cfg index originates from a unit")
}

/// Assembles one mode's gzipped data blob: node array in unit order,
/// metadata rewrite, compact serialization (PAYLOAD.md §5 steps 3-5).
fn assemble_mode(
    mode: &ModePayload,
    unit_unique: &[usize],
    trees: &[EngineNodes],
) -> Result<Vec<u8>, BuildEngineError> {
    let mut generator = OutputNodeGenerator::new();
    let mut node_index: Vec<u32> = Vec::with_capacity(mode.units.len());
    for (unit, &unique_idx) in unit_unique.iter().enumerate() {
        let tree = &trees[unique_idx];
        let index =
            generator
                .add(&tree.arena, tree.root)
                .map_err(|error| BuildEngineError::Output {
                    mode: mode.name.clone(),
                    unit,
                    error,
                })?;
        node_index.push(index);
    }

    let metadata_error = |message: String| BuildEngineError::Metadata {
        mode: mode.name.clone(),
        message,
    };
    let mut doc: Value = serde_json::from_str(&mode.metadata)
        .map_err(|e| metadata_error(format!("failed to parse: {e}")))?;
    rewrite_metadata(&mut doc, mode, &node_index).map_err(metadata_error)?;
    let json = render_document(&doc, &render_node_array(generator.nodes()));
    Ok(gzip_compress(json.as_bytes()))
}

/// Replaces unit-id callback slots with node indices and validates the
/// metadata contract (PAYLOAD.md §5 step 4). The `"nodes"` placeholder is
/// validated here and spliced during rendering.
fn rewrite_metadata(doc: &mut Value, mode: &ModePayload, node_index: &[u32]) -> Result<(), String> {
    let Value::Object(map) = doc else {
        return Err("document is not a JSON object".to_owned());
    };
    let mut referenced = vec![false; mode.units.len()];

    // Global callback slots: defined by the units with `archetype: None`.
    for (unit_id, unit) in mode.units.iter().enumerate() {
        if unit.archetype.is_some() {
            continue;
        }
        let slot = map
            .get_mut(&unit.callback)
            .ok_or_else(|| format!("missing global callback key {:?}", unit.callback))?;
        let held = slot
            .as_i64()
            .ok_or_else(|| format!("global callback slot {:?} is not an integer", unit.callback))?;
        if held != i64::try_from(unit_id).expect("unit ids fit i64") {
            return Err(format!(
                "global callback slot {:?} holds {held}, expected unit id {unit_id}",
                unit.callback
            ));
        }
        *slot = Value::from(node_index[unit_id]);
        referenced[unit_id] = true;
    }

    // Archetype entries: every key other than the static four is a slot.
    let entries = map
        .get_mut("archetypes")
        .ok_or_else(|| "missing the archetypes key".to_owned())?;
    let Value::Array(entries) = entries else {
        return Err("archetypes is not an array".to_owned());
    };
    for (entry_idx, entry) in entries.iter_mut().enumerate() {
        let Value::Object(entry) = entry else {
            return Err(format!("archetype entry {entry_idx} is not an object"));
        };
        for (key, value) in entry.iter_mut() {
            if ARCHETYPE_STATIC_KEYS.contains(&key.as_str()) {
                continue;
            }
            let Value::Object(slot) = value else {
                return Err(format!(
                    "archetype entry {entry_idx} callback slot {key:?} is not an object"
                ));
            };
            let index = slot
                .get_mut("index")
                .ok_or_else(|| format!("archetype entry {entry_idx} slot {key:?} has no index"))?;
            let unit_id = index
                .as_u64()
                .and_then(|v| usize::try_from(v).ok())
                .filter(|&v| v < mode.units.len())
                .ok_or_else(|| {
                    format!(
                        "archetype entry {entry_idx} slot {key:?} index {index} \
                         is not a valid unit id"
                    )
                })?;
            let unit = &mode.units[unit_id];
            if unit.callback != *key {
                return Err(format!(
                    "archetype entry {entry_idx} slot {key:?} references unit {unit_id}, \
                     which is callback {:?}",
                    unit.callback
                ));
            }
            if unit.archetype.is_none() {
                return Err(format!(
                    "archetype entry {entry_idx} slot {key:?} references global unit {unit_id}"
                ));
            }
            *index = Value::from(node_index[unit_id]);
            referenced[unit_id] = true;
        }
    }

    // The per-node-data placeholder (PAYLOAD.md §3 substitution 2).
    let nodes = map
        .get("nodes")
        .ok_or_else(|| "missing the nodes placeholder".to_owned())?;
    if !nodes.is_null() {
        return Err("the nodes placeholder must be null".to_owned());
    }

    if let Some(unit_id) = referenced.iter().position(|&r| !r) {
        return Err(format!(
            "unit {unit_id} ({:?}) is not referenced by any metadata slot",
            mode.units[unit_id].callback
        ));
    }
    Ok(())
}

/// Renders the rewritten mode document compactly, splicing the rendered node
/// array in place of the validated `"nodes": null` placeholder. Splicing (vs
/// a `serde_json` value) is required because node values may be ±inf, which
/// `serde_json::Number` cannot represent but legacy `json.dumps` emits as
/// `Infinity`/`-Infinity`.
fn render_document(doc: &Value, nodes_json: &str) -> String {
    let Value::Object(map) = doc else {
        unreachable!("rewrite_metadata validated the document is an object")
    };
    let mut out = String::from("{");
    for (i, (key, value)) in map.iter().enumerate() {
        if i > 0 {
            out.push(',');
        }
        pyjson::write_string(&mut out, key);
        out.push(':');
        if key == "nodes" {
            out.push_str(nodes_json);
        } else {
            out.push_str(&pyjson::dumps_compact(value));
        }
    }
    out.push('}');
    out
}

/// Renders the output-node array exactly like legacy
/// `json.dumps(nodes, separators=(",", ":"))` over
/// `sonolus/build/node.py`-shaped dicts: `{"value": ...}` for constants
/// (int-tagged values as JSON integers, float-tagged via `repr(float)`,
/// ±inf as `Infinity`/`-Infinity`) and `{"func": "Name", "args": [...]}` for
/// function applications.
fn render_node_array(nodes: &[OutputNode]) -> String {
    let mut out = String::from("[");
    for (i, node) in nodes.iter().enumerate() {
        if i > 0 {
            out.push(',');
        }
        match node {
            OutputNode::Value { value, is_int } => {
                out.push_str("{\"value\":");
                write_node_value(&mut out, *value, *is_int);
                out.push('}');
            }
            OutputNode::Func { op, args } => {
                // Op names are plain ASCII identifiers; no escaping needed.
                let _ = write!(out, "{{\"func\":\"{}\",\"args\":[", op.name());
                for (j, arg) in args.iter().enumerate() {
                    if j > 0 {
                        out.push(',');
                    }
                    let _ = write!(out, "{arg}");
                }
                out.push_str("]}");
            }
        }
    }
    out.push(']');
    out
}

/// Writes one node value with the legacy tag semantics. NaN is unreachable
/// (output-node generation rejects it); int-tagged values are always
/// integral (they originate from `i64` constants in the pipeline).
fn write_node_value(out: &mut String, value: f64, is_int: bool) {
    debug_assert!(!value.is_nan(), "NaN constants are rejected upstream");
    if is_int {
        debug_assert!(
            value.is_finite() && value.fract() == 0.0,
            "int-tagged node values are integral"
        );
        if value == 0.0 {
            // A Python int is never -0; normalize defensively.
            out.push('0');
        } else {
            // `{:.0}` prints the exact decimal value of the f64 (no
            // exponent, no decimal point) — identical to Python printing
            // the int the f64 exactly represents.
            let _ = write!(out, "{value:.0}");
        }
    } else if value == f64::INFINITY {
        out.push_str("Infinity");
    } else if value == f64::NEG_INFINITY {
        out.push_str("-Infinity");
    } else {
        out.push_str(&pyjson::py_float_repr(value));
    }
}

/// Packs ROM values as little-endian f32, with the legacy
/// `struct.pack("<f", v)` overflow semantics: a finite value that rounds to
/// an infinity errors instead of saturating (PAYLOAD.md §5 step 6).
/// NaN/±inf pack fine on both sides.
fn pack_rom(values: &[f64]) -> Result<Vec<u8>, BuildEngineError> {
    let mut out = Vec::with_capacity(values.len() * 4);
    for (index, &value) in values.iter().enumerate() {
        // Rust `as f32` and CPython's `_PyFloat_Pack4` both perform the
        // IEEE-754 double->single conversion (round-to-nearest, ties to
        // even); only the overflow handling differs, checked here.
        #[allow(clippy::cast_possible_truncation)]
        let packed = value as f32;
        if packed.is_infinite() && value.is_finite() {
            return Err(BuildEngineError::RomOverflow { index, value });
        }
        out.extend_from_slice(&packed.to_le_bytes());
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    #![allow(clippy::float_cmp)] // exact f64 equality is the assertion contract

    use super::*;
    use crate::collection::gzip_decompress;
    use crate::ops::Op;

    // -------------------------------------------------------------------
    // Minimal test encoder (mirrors decode.rs's TestWriter): just enough of
    // the v1 encoding to express straight-line single-block CFGs.
    // -------------------------------------------------------------------

    const TAG_CONST_INT: u8 = 0;
    const TAG_CONST_FLOAT: u8 = 1;
    const TAG_PURE_INSTR: u8 = 2;
    const TAG_GET: u8 = 4;
    const TAG_SET: u8 = 5;
    const BLOCK_INT: u8 = 0;
    const BLOCK_TEMP: u8 = 1;
    const INDEX_INT: u8 = 0;

    #[derive(Default)]
    struct TestWriter {
        buf: Vec<u8>,
    }

    impl TestWriter {
        fn header(mut self) -> Self {
            self.buf.extend_from_slice(crate::decode::MAGIC);
            self.buf
                .extend_from_slice(&crate::decode::ENCODING_VERSION.to_le_bytes());
            self.buf.extend_from_slice(&Op::COUNT.to_le_bytes());
            self
        }

        fn u8(mut self, v: u8) -> Self {
            self.buf.push(v);
            self
        }

        fn f64(mut self, v: f64) -> Self {
            self.buf.extend_from_slice(&v.to_le_bytes());
            self
        }

        fn varuint(mut self, mut v: u64) -> Self {
            loop {
                let bits = u8::try_from(v & 0x7f).expect("masked to 7 bits");
                v >>= 7;
                if v == 0 {
                    self.buf.push(bits);
                    return self;
                }
                self.buf.push(bits | 0x80);
            }
        }

        #[allow(clippy::cast_sign_loss)]
        fn varint(self, v: i64) -> Self {
            self.varuint(((v << 1) ^ (v >> 63)) as u64)
        }

        fn buf_u16(mut self, v: u16) -> Self {
            self.buf.extend_from_slice(&v.to_le_bytes());
            self
        }
    }

    /// `{ block[index] <- value; test 0; goto exit }` for a runtime block.
    fn set_const_cfg(block: i64, index: i64, value: f64, float: bool) -> Vec<u8> {
        let w = TestWriter::default()
            .header()
            .varuint(0) // strings
            .varuint(0) // temp blocks
            .varuint(1) // blocks
            .varuint(1) // statements
            .u8(TAG_SET)
            .u8(BLOCK_INT)
            .varint(block)
            .u8(INDEX_INT)
            .varint(index)
            .varint(0); // offset
        let w = if float {
            w.u8(TAG_CONST_FLOAT).f64(value)
        } else {
            #[allow(clippy::cast_possible_truncation)]
            let v = value as i64;
            w.u8(TAG_CONST_INT).varint(v)
        };
        w.u8(TAG_CONST_INT)
            .varint(0) // test
            .varuint(0) // edges
            .buf
    }

    /// `{ block[index] <- Add(a, b); ... }` — produces a distinct node tree.
    fn set_add_cfg(block: i64, index: i64, a: i64, b: i64) -> Vec<u8> {
        TestWriter::default()
            .header()
            .varuint(0)
            .varuint(0)
            .varuint(1)
            .varuint(1)
            .u8(TAG_SET)
            .u8(BLOCK_INT)
            .varint(block)
            .u8(INDEX_INT)
            .varint(index)
            .varint(0)
            .u8(TAG_PURE_INSTR)
            .buf_u16(Op::Add.id())
            .varuint(2)
            .u8(TAG_CONST_INT)
            .varint(a)
            .u8(TAG_CONST_INT)
            .varint(b)
            .u8(TAG_CONST_INT)
            .varint(0)
            .varuint(0)
            .buf
    }

    /// Two interfering 3000-slot temps: exceeds the 4096 budget.
    fn temp_limit_cfg() -> Vec<u8> {
        let mut w = TestWriter::default()
            .header()
            .varuint(2) // strings
            .varuint(1); // "a"
        w.buf.push(b'a');
        w = w.varuint(1);
        w.buf.push(b'b');
        w = w
            .varuint(2) // temp blocks
            .varuint(0)
            .varuint(3000)
            .varuint(1)
            .varuint(3000)
            .varuint(1) // blocks
            .varuint(4); // statements
        for t in 0..2u64 {
            w = w
                .u8(TAG_SET)
                .u8(BLOCK_TEMP)
                .varuint(t)
                .u8(INDEX_INT)
                .varint(0)
                .varint(0)
                .u8(TAG_CONST_INT)
                .varint(1);
        }
        for t in 0..2i64 {
            w = w
                .u8(TAG_SET)
                .u8(BLOCK_INT)
                .varint(20)
                .u8(INDEX_INT)
                .varint(t)
                .varint(0)
                .u8(TAG_GET)
                .u8(BLOCK_TEMP)
                .varuint(u64::try_from(t).expect("non-negative"))
                .u8(INDEX_INT)
                .varint(0)
                .varint(0);
        }
        w.u8(TAG_CONST_INT).varint(0).varuint(0).buf
    }

    // -------------------------------------------------------------------
    // Payload helpers
    // -------------------------------------------------------------------

    fn archetype_unit(callback: &str, archetype: i64, cfg: Vec<u8>) -> WorkUnit {
        WorkUnit {
            callback: callback.to_owned(),
            archetype: Some(archetype),
            order: 0,
            cfg,
        }
    }

    fn global_unit(callback: &str, cfg: Vec<u8>) -> WorkUnit {
        WorkUnit {
            callback: callback.to_owned(),
            archetype: None,
            order: 0,
            cfg,
        }
    }

    fn empty_mode(name: &str, metadata: &str) -> ModePayload {
        ModePayload {
            name: name.to_owned(),
            metadata: metadata.to_owned(),
            units: vec![],
        }
    }

    /// A small but fully populated payload: play has one archetype with two
    /// callbacks (one shared cfg with watch's global), watch has one global.
    fn sample_payload() -> EnginePayload {
        let shared_cfg = set_const_cfg(20, 0, 5.0, false);
        let play = ModePayload {
            name: "play".to_owned(),
            metadata: concat!(
                r#"{"archetypes":[{"name":"A","hasInput":false,"imports":[],"exports":[],"#,
                r#""updateSequential":{"index":0,"order":0},"touch":{"index":1,"order":2}}],"#,
                r#""nodes":null,"skin":{"renderMode":"default","sprites":[]},"#,
                r#""effect":{"clips":[]},"particle":{"effects":[]},"buckets":[]}"#
            )
            .to_owned(),
            units: vec![
                archetype_unit("updateSequential", 0, shared_cfg.clone()),
                archetype_unit("touch", 0, set_add_cfg(21, 1, 2, 3)),
            ],
        };
        let watch = ModePayload {
            name: "watch".to_owned(),
            metadata: concat!(
                r#"{"updateSpawn":0,"archetypes":[],"nodes":null,"#,
                r#""skin":{"renderMode":"default","sprites":[]},"effect":{"clips":[]},"#,
                r#""particle":{"effects":[]},"buckets":[]}"#
            )
            .to_owned(),
            units: vec![global_unit("updateSpawn", shared_cfg)],
        };
        EnginePayload {
            level: Level::Standard,
            configuration: r#"{"options":[],"ui":{}}"#.to_owned(),
            rom: vec![0.0],
            modes: vec![
                play,
                watch,
                empty_mode("preview", r#"{"archetypes":[],"nodes":null,"skin":{}}"#),
                empty_mode("tutorial", r#"{"archetypes":[],"nodes":null,"skin":{}}"#),
            ],
        }
    }

    fn parse_mode(blob: &[u8]) -> Value {
        let json = gzip_decompress(blob).expect("mode blob is gzip");
        serde_json::from_str(std::str::from_utf8(&json).expect("utf-8")).expect("valid JSON")
    }

    // -------------------------------------------------------------------
    // Tests
    // -------------------------------------------------------------------

    #[test]
    fn end_to_end_build_fills_slots_and_nodes() {
        let payload = sample_payload();
        let packaged = build_engine(&payload).unwrap();

        // Configuration: utf-8 + gzip, verbatim.
        assert_eq!(
            gzip_decompress(&packaged.configuration).unwrap(),
            payload.configuration.as_bytes()
        );
        // ROM: [0.0] packs to 4 zero bytes.
        assert_eq!(gzip_decompress(&packaged.rom).unwrap(), vec![0, 0, 0, 0]);

        let play = parse_mode(&packaged.play_data);
        let watch = parse_mode(&packaged.watch_data);
        // Key order is preserved through the rewrite.
        let play_keys: Vec<&str> = play
            .as_object()
            .unwrap()
            .keys()
            .map(String::as_str)
            .collect();
        assert_eq!(
            play_keys,
            [
                "archetypes",
                "nodes",
                "skin",
                "effect",
                "particle",
                "buckets"
            ]
        );
        let watch_keys: Vec<&str> = watch
            .as_object()
            .unwrap()
            .keys()
            .map(String::as_str)
            .collect();
        assert_eq!(
            watch_keys,
            [
                "updateSpawn",
                "archetypes",
                "nodes",
                "skin",
                "effect",
                "particle",
                "buckets"
            ]
        );

        // The play archetype slots hold node indices into the play array.
        let entry = &play["archetypes"][0];
        let nodes = play["nodes"].as_array().unwrap();
        let seq_index = entry["updateSequential"]["index"].as_u64().unwrap();
        let touch_index = entry["touch"]["index"].as_u64().unwrap();
        assert_ne!(seq_index, touch_index, "distinct trees get distinct roots");
        assert!(usize::try_from(seq_index).unwrap() < nodes.len());
        assert!(usize::try_from(touch_index).unwrap() < nodes.len());
        // Orders survive untouched.
        assert_eq!(entry["updateSequential"]["order"], 0);
        assert_eq!(entry["touch"]["order"], 2);
        // Roots are function nodes (Block(JumpLoop(...)) trees).
        assert!(nodes[usize::try_from(seq_index).unwrap()]["func"].is_string());

        // Watch's single global slot is its node array's root.
        let spawn_index = watch["updateSpawn"].as_u64().unwrap();
        let watch_nodes = watch["nodes"].as_array().unwrap();
        assert_eq!(usize::try_from(spawn_index).unwrap() + 1, watch_nodes.len());

        // The shared cfg compiles to identical trees: watch's array equals
        // the sub-array the play unit produced (cross-mode dedup compiles
        // once; per-mode arrays are still independent).
        assert!(watch_nodes.len() <= nodes.len());

        // Empty modes keep their metadata with an empty node array.
        let preview = parse_mode(&packaged.preview_data);
        assert_eq!(preview["nodes"], Value::Array(vec![]));
        assert_eq!(preview["archetypes"], Value::Array(vec![]));
    }

    #[test]
    fn dedup_units_keys_on_exact_bytes_across_modes() {
        let payload = sample_payload();
        let (unique, unit_unique) = dedup_units(&payload.modes);
        // Three units, two distinct byte strings (play unit 0 == watch unit 0).
        assert_eq!(unique.len(), 2);
        assert_eq!(unit_unique[0], vec![0, 1]); // play
        assert_eq!(unit_unique[1], vec![0]); // watch shares play's first cfg
        assert_eq!(unit_unique[2], Vec::<usize>::new());
        assert_eq!(unit_unique[3], Vec::<usize>::new());
        assert_eq!(first_unit_of_unique(&unit_unique, 0), (0, 0));
        assert_eq!(first_unit_of_unique(&unit_unique, 1), (0, 1));
    }

    #[test]
    fn shared_cfg_units_in_one_mode_share_node_indices() {
        // Two archetype callbacks with byte-identical CFGs in ONE mode must
        // produce the same node index and the same node array as the
        // non-deduped path (the legacy generator dedups naturally).
        let cfg = set_const_cfg(20, 0, 7.0, false);
        let mut payload = sample_payload();
        payload.modes[0].metadata = concat!(
            r#"{"archetypes":[{"name":"A","hasInput":false,"imports":[],"exports":[],"#,
            r#""updateSequential":{"index":0,"order":0}},"#,
            r#"{"name":"B","hasInput":false,"imports":[],"exports":[],"#,
            r#""updateSequential":{"index":1,"order":0}}],"nodes":null,"skin":{}}"#
        )
        .to_owned();
        payload.modes[0].units = vec![
            archetype_unit("updateSequential", 0, cfg.clone()),
            archetype_unit("updateSequential", 1, cfg.clone()),
        ];
        payload.modes[1].metadata = r#"{"updateSpawn":0,"archetypes":[],"nodes":null}"#.to_owned();
        payload.modes[1].units = vec![global_unit("updateSpawn", cfg.clone())];
        let packaged = build_engine(&payload).unwrap();
        let play = parse_mode(&packaged.play_data);
        let a = play["archetypes"][0]["updateSequential"]["index"].as_u64();
        let b = play["archetypes"][1]["updateSequential"]["index"].as_u64();
        assert_eq!(a, b, "identical units share one node-array segment");

        // Non-deduped reference: compile the cfg twice independently and add
        // both trees to a fresh generator — the node array must be identical.
        let decoded = decode_cfg(&cfg).unwrap();
        let tree_a = compile_cfg(&decoded, Level::Standard).unwrap();
        let tree_b = compile_cfg(&decoded, Level::Standard).unwrap();
        let mut reference = OutputNodeGenerator::new();
        let ref_a = reference.add(&tree_a.arena, tree_a.root).unwrap();
        let ref_b = reference.add(&tree_b.arena, tree_b.root).unwrap();
        assert_eq!(ref_a, ref_b);
        assert_eq!(u64::from(ref_a), a.unwrap());
        // All node values in this cfg are int-tagged, so serde's compact
        // serialization of the parsed array is comparable byte-for-byte.
        assert_eq!(
            render_node_array(reference.nodes()),
            serde_json::to_string(&play["nodes"]).unwrap(),
        );
    }

    #[test]
    fn build_engine_is_deterministic() {
        let payload = sample_payload();
        let a = build_engine(&payload).unwrap();
        let b = build_engine(&payload).unwrap();
        assert_eq!(a.configuration, b.configuration);
        assert_eq!(a.play_data, b.play_data);
        assert_eq!(a.watch_data, b.watch_data);
        assert_eq!(a.preview_data, b.preview_data);
        assert_eq!(a.tutorial_data, b.tutorial_data);
        assert_eq!(a.rom, b.rom);
    }

    #[test]
    fn rom_packs_f32_le_with_legacy_overflow_semantics() {
        // Reference bytes from CPython struct.pack("<f", ...).
        let packed = pack_rom(&[
            1.5,
            f64::NAN,
            f64::INFINITY,
            f64::NEG_INFINITY,
            -0.0,
            0.1,
            65535.0,
        ])
        .unwrap();
        assert_eq!(
            packed,
            [
                [0x00, 0x00, 0xc0, 0x3f],
                [0x00, 0x00, 0xc0, 0x7f],
                [0x00, 0x00, 0x80, 0x7f],
                [0x00, 0x00, 0x80, 0xff],
                [0x00, 0x00, 0x00, 0x80],
                [0xcd, 0xcc, 0xcc, 0x3d],
                [0x00, 0xff, 0x7f, 0x47],
            ]
            .concat()
        );
        // 3.4028235e38 rounds within f32 range; 3.402824e38 overflows
        // (CPython OverflowError boundary, verified against struct.pack).
        assert!(pack_rom(&[3.402_823_5e38]).is_ok());
        let err = pack_rom(&[1.0, 3.402_824e38]).unwrap_err();
        assert!(matches!(
            err,
            BuildEngineError::RomOverflow { index: 1, .. }
        ));
        assert_eq!(err.to_string(), "float too large to pack with f format");
    }

    #[test]
    fn empty_rom_is_rejected() {
        let mut payload = sample_payload();
        payload.rom = vec![];
        assert!(matches!(
            build_engine(&payload),
            Err(BuildEngineError::EmptyRom)
        ));
    }

    #[test]
    fn node_value_rendering_matches_python() {
        let mut out = String::new();
        write_node_value(&mut out, 5.0, true);
        out.push(' ');
        write_node_value(&mut out, -0.0, true);
        out.push(' ');
        write_node_value(&mut out, -7.0, true);
        out.push(' ');
        write_node_value(&mut out, 5.0, false);
        out.push(' ');
        write_node_value(&mut out, -0.0, false);
        out.push(' ');
        write_node_value(&mut out, 2.5, false);
        out.push(' ');
        write_node_value(&mut out, 1e16, false);
        out.push(' ');
        write_node_value(&mut out, f64::INFINITY, false);
        out.push(' ');
        write_node_value(&mut out, f64::NEG_INFINITY, false);
        assert_eq!(out, "5 0 -7 5.0 -0.0 2.5 1e+16 Infinity -Infinity");
    }

    #[test]
    fn render_node_array_matches_legacy_shape() {
        let nodes = [
            OutputNode::Value {
                value: 5.0,
                is_int: true,
            },
            OutputNode::Value {
                value: 2.5,
                is_int: false,
            },
            OutputNode::Func {
                op: Op::Add,
                args: vec![0, 1],
            },
            OutputNode::Func {
                op: Op::Execute,
                args: vec![],
            },
        ];
        assert_eq!(
            render_node_array(&nodes),
            concat!(
                r#"[{"value":5},{"value":2.5},"#,
                r#"{"func":"Add","args":[0,1]},{"func":"Execute","args":[]}]"#
            )
        );
    }

    #[test]
    fn metadata_validation_rejects_contract_violations() {
        let base = sample_payload();

        // Wrong unit id in a global slot.
        let mut p = base.clone();
        p.modes[1].metadata = p.modes[1]
            .metadata
            .replace(r#""updateSpawn":0"#, r#""updateSpawn":7"#);
        let err = build_engine(&p).unwrap_err().to_string();
        assert!(err.contains("expected unit id 0"), "{err}");

        // Non-null nodes placeholder.
        let mut p = base.clone();
        p.modes[1].metadata = p.modes[1]
            .metadata
            .replace(r#""nodes":null"#, r#""nodes":[]"#);
        let err = build_engine(&p).unwrap_err().to_string();
        assert!(err.contains("placeholder must be null"), "{err}");

        // Missing nodes placeholder.
        let mut p = base.clone();
        p.modes[2].metadata = r#"{"archetypes":[],"skin":{}}"#.to_owned();
        let err = build_engine(&p).unwrap_err().to_string();
        assert!(err.contains("missing the nodes placeholder"), "{err}");

        // Out-of-range archetype slot index.
        let mut p = base.clone();
        p.modes[0].metadata = p.modes[0].metadata.replace(
            r#""touch":{"index":1,"order":2}"#,
            r#""touch":{"index":5,"order":2}"#,
        );
        let err = build_engine(&p).unwrap_err().to_string();
        assert!(err.contains("not a valid unit id"), "{err}");

        // Slot referencing a unit of a different callback.
        let mut p = base.clone();
        p.modes[0].metadata = p.modes[0].metadata.replace(
            r#""touch":{"index":1,"order":2}"#,
            r#""touch":{"index":0,"order":2}"#,
        );
        let err = build_engine(&p).unwrap_err().to_string();
        assert!(err.contains("which is callback"), "{err}");

        // Dangling unit (metadata stops referencing unit 1).
        let mut p = base.clone();
        p.modes[0].metadata = p.modes[0]
            .metadata
            .replace(r#","touch":{"index":1,"order":2}"#, "");
        let err = build_engine(&p).unwrap_err().to_string();
        assert!(err.contains("not referenced by any metadata slot"), "{err}");

        // Malformed mode list.
        let mut p = base.clone();
        p.modes.swap(0, 1);
        let err = build_engine(&p).unwrap_err().to_string();
        assert!(err.contains("invalid payload modes"), "{err}");
    }

    #[test]
    fn compile_errors_pass_through_verbatim() {
        let mut p = sample_payload();
        // Minimal does no temp promotion, so the two interfering 3000-slot
        // temps reach the allocator and exceed the 4096 budget.
        p.level = Level::Minimal;
        p.modes[1].units[0].cfg = temp_limit_cfg();
        let err = build_engine(&p).unwrap_err();
        assert_eq!(err.to_string(), "Temporary memory limit exceeded");
        assert!(matches!(err, BuildEngineError::Compile { unit: 0, .. }));
    }

    #[test]
    fn decode_errors_carry_unit_context() {
        let mut p = sample_payload();
        p.modes[0].units[1].cfg = vec![1, 2, 3];
        let err = build_engine(&p).unwrap_err().to_string();
        assert!(
            err.starts_with("mode play unit 1: failed to decode CFG:"),
            "{err}"
        );
    }

    #[test]
    fn document_rendering_preserves_key_order_and_python_format() {
        // Unusual key order + floats/strings that exercise the CPython
        // formatting rules; expected string pinned against
        // json.dumps(..., separators=(",", ":")).
        let payload = EnginePayload {
            level: Level::Minimal,
            configuration: "{}".to_owned(),
            rom: vec![0.0],
            modes: vec![
                empty_mode(
                    "play",
                    r#"{"zeta":[1.5,2.0,true,null],"archetypes":[],"alpha":"café","nodes":null,"e":1e+16,"z":-0.0}"#,
                ),
                empty_mode("watch", r#"{"archetypes":[],"nodes":null}"#),
                empty_mode("preview", r#"{"archetypes":[],"nodes":null}"#),
                empty_mode("tutorial", r#"{"archetypes":[],"nodes":null}"#),
            ],
        };
        let packaged = build_engine(&payload).unwrap();
        let json = gzip_decompress(&packaged.play_data).unwrap();
        // The é is escaped on output (ensure_ascii semantics; the raw string
        // below holds a literal backslash-u sequence). In the real payload it
        // already arrives escaped from Python's json.dumps.
        assert_eq!(
            std::str::from_utf8(&json).unwrap(),
            concat!(
                r#"{"zeta":[1.5,2.0,true,null],"archetypes":[],"#,
                "\"alpha\":\"caf\\u00e9\",\"nodes\":[],\"e\":1e+16,\"z\":-0.0}"
            )
        );
    }
}
