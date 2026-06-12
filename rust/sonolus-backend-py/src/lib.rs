//! Thin `PyO3` bindings exposing `sonolus-backend-core` to Python as the
//! `sonolus_backend` extension module.

mod collection;

use pyo3::exceptions::{
    PyAssertionError, PyIndexError, PyNotImplementedError, PyOverflowError, PyRuntimeError,
    PyTypeError, PyValueError, PyZeroDivisionError,
};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyFloat, PyInt, PyList, PyString, PyTuple};
use sonolus_backend_core::build::{
    self, BuildEngineError, EnginePayload, ModePayload, PAYLOAD_SCHEMA_VERSION, WorkUnit,
};
use sonolus_backend_core::cfg::{canonical_dump, cfg_to_text};
use sonolus_backend_core::decode::decode_cfg;
use sonolus_backend_core::diff::build_memory;
use sonolus_backend_core::emit;
use sonolus_backend_core::interpret::{
    Interpreter as CoreInterpreter, InterpreterError, InterpreterErrorKind,
};
use sonolus_backend_core::nodes::{
    EngineNodes as CoreEngineNodes, NodeArena, NodeId, format_engine_node, tree_node_count,
};
use sonolus_backend_core::ops::Op;
use sonolus_backend_core::output;
use sonolus_backend_core::pipeline::{self, CompileError, CompileStats, Level};

/// Returns the version of the Rust backend.
#[pyfunction]
fn backend_version() -> &'static str {
    sonolus_backend_core::version()
}

/// Decodes an encoded CFG (see `rust/ENCODING.md`) and returns its canonical
/// structural dump, byte-identical to the Python side's
/// `sonolus.backend.encode.cfg_canonical_dump` for the same CFG.
///
/// Test handle for round-trip validation. Raises `ValueError` on malformed input.
#[pyfunction]
fn decode_cfg_canonical_dump(data: &[u8]) -> PyResult<String> {
    let cfg = decode_cfg(data).map_err(|e| PyValueError::new_err(e.to_string()))?;
    Ok(canonical_dump(&cfg))
}

/// Decodes an encoded CFG and returns a human-readable debug dump (Rust-native
/// formatting; decision D7 — not a compatibility surface).
///
/// Raises `ValueError` on malformed input.
#[pyfunction]
fn decode_cfg_debug_dump(data: &[u8]) -> PyResult<String> {
    let cfg = decode_cfg(data).map_err(|e| PyValueError::new_err(e.to_string()))?;
    Ok(cfg_to_text(&cfg))
}

/// Decodes an encoded post-pass CFG and emits its `Block(JumpLoop(...))`
/// engine-node tree, exactly like the legacy `cfg_to_engine_node` (T1.2; no
/// optimization happens here).
///
/// Raises `ValueError` on malformed input or out-of-domain CFGs (`TempBlock`
/// places, NaN edge conds).
#[pyfunction]
fn cfg_to_engine_nodes(data: &[u8]) -> PyResult<EngineNodes> {
    let cfg = decode_cfg(data).map_err(|e| PyValueError::new_err(e.to_string()))?;
    let inner =
        emit::cfg_to_engine_nodes(&cfg).map_err(|e| PyValueError::new_err(e.to_string()))?;
    Ok(EngineNodes { inner })
}

/// Renders the canonical output-node dump of an engine-node tree: output nodes
/// are generated with the legacy `OutputNodeGenerator` dedup semantics and
/// insertion order, one line per node — `v i 0x...`/`v f 0x...` for value nodes
/// (raw IEEE-754 bits + int/float tag) and `f <OpName> <arg indices...>` for
/// function nodes. The root is the last node.
///
/// Raises `ValueError` if a NaN constant is reachable (impossible for
/// emitter-produced trees).
#[pyfunction]
#[allow(clippy::needless_pass_by_value)] // PyO3 argument convention
fn engine_nodes_to_output_dump(nodes: PyRef<'_, EngineNodes>) -> PyResult<String> {
    let out = output::generate_output_nodes(&nodes.inner.arena, nodes.inner.root)
        .map_err(|e| PyValueError::new_err(e.to_string()))?;
    Ok(output::output_node_dump(&out))
}

/// Parses a level name (`"minimal"`/`"fast"`/`"standard"`).
fn parse_level(level: &str) -> PyResult<Level> {
    level
        .parse()
        .map_err(|e: pipeline::UnknownLevel| PyValueError::new_err(e.to_string()))
}

/// Maps a pipeline failure onto a Python exception. The temp-memory budget
/// error matches the legacy `ValueError("Temporary memory limit exceeded")`
/// exactly; every pipeline failure is a `ValueError`.
fn compile_error_to_py(e: &CompileError) -> PyErr {
    PyValueError::new_err(e.to_string())
}

/// Runs the Rust compilation pipeline on an encoded frontend CFG
/// (`rust/ENCODING.md`) at the given optimization level (`"minimal"`,
/// `"fast"`, or `"standard"`) and returns the engine-node tree. The level
/// selects a prefix of the optimization pass pipeline (T2.2); all three levels
/// are callable (the optimization registry is empty until W1, so `fast` and
/// `standard` are identity-equal to `minimal` today).
///
/// Raises `ValueError` on malformed input, out-of-domain CFGs, or when the
/// 4096-slot temporary-memory budget is exceeded (message matches the legacy
/// backend: "Temporary memory limit exceeded").
#[pyfunction]
fn run_pipeline(data: &[u8], level: &str) -> PyResult<EngineNodes> {
    let level = parse_level(level)?;
    let cfg = decode_cfg(data).map_err(|e| PyValueError::new_err(e.to_string()))?;
    let inner = pipeline::compile_cfg(&cfg, level).map_err(|e| compile_error_to_py(&e))?;
    Ok(EngineNodes { inner })
}

fn stats_to_dict(py: Python<'_>, stats: CompileStats) -> PyResult<Py<PyDict>> {
    let dict = PyDict::new(py);
    dict.set_item("temp_slots_used", stats.temp_slots_used)?;
    dict.set_item("temps_allocated", stats.temps_allocated)?;
    dict.set_item("mir_blocks", stats.mir_blocks)?;
    dict.set_item("mir_insts", stats.mir_insts)?;
    dict.set_item("node_count", stats.node_count)?;
    Ok(dict.unbind())
}

/// Like [`run_pipeline`], but also returns a stats dict with
/// `temp_slots_used`, `temps_allocated`, `mir_blocks`, `mir_insts`,
/// `node_count`, plus the T2.4 quality metrics `static_nodes` (tree node
/// count, pre-dedup) and `dag_size` (output-node count after DAG dedup).
#[pyfunction]
fn run_pipeline_stats(
    py: Python<'_>,
    data: &[u8],
    level: &str,
) -> PyResult<(EngineNodes, Py<PyDict>)> {
    let level = parse_level(level)?;
    let cfg = decode_cfg(data).map_err(|e| PyValueError::new_err(e.to_string()))?;
    let (inner, stats) =
        pipeline::compile_cfg_stats(&cfg, level).map_err(|e| compile_error_to_py(&e))?;
    let dict = stats_to_dict(py, stats)?;
    dict.bind(py)
        .set_item("static_nodes", tree_node_count(&inner.arena, inner.root))?;
    let out = output::generate_output_nodes(&inner.arena, inner.root)
        .map_err(|e| PyValueError::new_err(e.to_string()))?;
    dict.bind(py).set_item("dag_size", out.nodes.len())?;
    Ok((EngineNodes { inner }, dict))
}

/// Validates that a payload dict has exactly the expected keys (any order).
fn expect_exact_keys(dict: &Bound<'_, PyDict>, expected: &[&str], what: &str) -> PyResult<()> {
    let mut keys: Vec<String> = Vec::with_capacity(dict.len());
    for key in dict.keys() {
        keys.push(
            key.extract::<String>()
                .map_err(|_| PyTypeError::new_err(format!("{what} keys must be strings")))?,
        );
    }
    let mut sorted = keys.clone();
    sorted.sort_unstable();
    let mut expected_sorted: Vec<&str> = expected.to_vec();
    expected_sorted.sort_unstable();
    if sorted != expected_sorted {
        return Err(PyValueError::new_err(format!(
            "{what} must have exactly the keys {expected:?}, got {keys:?}"
        )));
    }
    Ok(())
}

fn get_required<'py>(
    dict: &Bound<'py, PyDict>,
    key: &str,
    what: &str,
) -> PyResult<Bound<'py, PyAny>> {
    dict.get_item(key)?
        .ok_or_else(|| PyValueError::new_err(format!("{what} is missing the {key:?} key")))
}

/// Converts the schema-v1 payload dict (`rust/PAYLOAD.md`) into the core
/// representation, validating its shape. Runs with the GIL held; everything
/// it produces is GIL-independent so the build itself can detach.
fn convert_payload(payload: &Bound<'_, PyDict>) -> PyResult<EnginePayload> {
    expect_exact_keys(
        payload,
        &["schema", "level", "configuration", "rom", "modes"],
        "the payload",
    )?;
    let schema: i64 = get_required(payload, "schema", "the payload")?.extract()?;
    if schema != PAYLOAD_SCHEMA_VERSION {
        return Err(PyValueError::new_err(format!(
            "unsupported payload schema {schema} (this backend consumes schema \
             {PAYLOAD_SCHEMA_VERSION})"
        )));
    }
    let level = parse_level(&get_required(payload, "level", "the payload")?.extract::<String>()?)?;
    let configuration: String = get_required(payload, "configuration", "the payload")?.extract()?;
    let rom: Vec<f64> = get_required(payload, "rom", "the payload")?.extract()?;

    let modes_obj = get_required(payload, "modes", "the payload")?;
    let all_modes = modes_obj
        .cast::<PyDict>()
        .map_err(|_| PyTypeError::new_err("the payload modes value must be a dict"))?;
    // PAYLOAD.md §1: exactly the four mode keys, in that insertion order
    // (validated in order by core's `validate_modes` via the names below).
    let mut modes: Vec<ModePayload> = Vec::with_capacity(all_modes.len());
    for (name_obj, mode_obj) in all_modes.iter() {
        let name: String = name_obj
            .extract()
            .map_err(|_| PyTypeError::new_err("mode keys must be strings"))?;
        let mode_dict = mode_obj
            .cast::<PyDict>()
            .map_err(|_| PyTypeError::new_err(format!("mode {name:?} must be a dict")))?;
        expect_exact_keys(mode_dict, &["metadata", "units"], &format!("mode {name:?}"))?;
        let metadata: String = get_required(mode_dict, "metadata", &name)?.extract()?;
        let units_obj = get_required(mode_dict, "units", &name)?;
        let units_list = units_obj
            .cast::<PyList>()
            .map_err(|_| PyTypeError::new_err(format!("mode {name:?} units must be a list")))?;
        let mut units: Vec<WorkUnit> = Vec::with_capacity(units_list.len());
        for (i, unit_obj) in units_list.iter().enumerate() {
            let what = format!("mode {name:?} unit {i}");
            let unit_dict = unit_obj
                .cast::<PyDict>()
                .map_err(|_| PyTypeError::new_err(format!("{what} must be a dict")))?;
            expect_exact_keys(unit_dict, &["callback", "archetype", "order", "cfg"], &what)?;
            let callback: String = get_required(unit_dict, "callback", &what)?.extract()?;
            let archetype: Option<i64> = get_required(unit_dict, "archetype", &what)?.extract()?;
            let order: i64 = get_required(unit_dict, "order", &what)?.extract()?;
            let cfg_obj = get_required(unit_dict, "cfg", &what)?;
            let cfg: Vec<u8> = cfg_obj
                .cast::<PyBytes>()
                .map_err(|_| PyTypeError::new_err(format!("{what} cfg must be bytes")))?
                .as_bytes()
                .to_vec();
            units.push(WorkUnit {
                callback,
                archetype,
                order,
                cfg,
            });
        }
        modes.push(ModePayload {
            name,
            metadata,
            units,
        });
    }

    Ok(EnginePayload {
        level,
        configuration,
        rom,
        modes,
    })
}

/// Maps a failed engine build onto the legacy exception surface: the ROM
/// overflow mirrors `struct.pack("<f", ...)`'s `OverflowError` (message
/// included); everything else is a `ValueError` (unit compilation failures
/// carry the pipeline message verbatim, e.g. "Temporary memory limit
/// exceeded").
fn build_engine_error_to_py(e: &BuildEngineError) -> PyErr {
    match e {
        BuildEngineError::RomOverflow { .. } => PyOverflowError::new_err(e.to_string()),
        _ => PyValueError::new_err(e.to_string()),
    }
}

/// Builds the six packaged engine blobs from a schema-v1 build payload
/// (`rust/PAYLOAD.md`; produced by `sonolus.build.payload.assemble_engine_payload`)
/// in one call: per-unit compilation at the payload's level (parallel, GIL
/// released), stateless intra-call dedup of byte-identical CFGs, per-mode
/// node arrays in canonical unit order, metadata rewrite, and gzip (mtime 0).
///
/// Pure function: no state survives the call (PORT.md invariant §3.8, D6).
///
/// Returns a dict shaped like the legacy `PackagedEngine` dataclass:
/// `{"configuration", "play_data", "watch_data", "preview_data",
/// "tutorial_data", "rom"}`, each a gzipped `bytes` value.
///
/// Raises `ValueError` for malformed payloads and unit compilation failures
/// (legacy message parity, e.g. "Temporary memory limit exceeded") and
/// `OverflowError` for finite ROM values that overflow f32 (legacy
/// `struct.pack("<f", ...)` parity).
#[pyfunction]
fn build_engine(py: Python<'_>, payload: &Bound<'_, PyDict>) -> PyResult<Py<PyDict>> {
    let payload = convert_payload(payload)?;
    // The compile phase is pure Rust; release the GIL so rayon workers (and
    // other Python threads) can run.
    let packaged = py
        .detach(|| build::build_engine(&payload))
        .map_err(|e| build_engine_error_to_py(&e))?;
    let dict = PyDict::new(py);
    dict.set_item("configuration", PyBytes::new(py, &packaged.configuration))?;
    dict.set_item("play_data", PyBytes::new(py, &packaged.play_data))?;
    dict.set_item("watch_data", PyBytes::new(py, &packaged.watch_data))?;
    dict.set_item("preview_data", PyBytes::new(py, &packaged.preview_data))?;
    dict.set_item("tutorial_data", PyBytes::new(py, &packaged.tutorial_data))?;
    dict.set_item("rom", PyBytes::new(py, &packaged.rom))?;
    Ok(dict.unbind())
}

/// The deterministic seeded memory fill of the differential harness
/// (`sonolus-backend-core::diff::build_memory`), for an encoded frontend CFG:
/// `[(block_id, [value, ...]), ...]` sorted by block id. ROM (3000) starts
/// with NaN/+inf/-inf; temp block 10000 is never filled. `tools/metrics.py`
/// re-implements this fill in pure Python; this handle exists so a test can
/// pin both implementations bit-identical.
///
/// Raises `ValueError` on malformed input.
#[pyfunction]
fn seeded_memory(data: &[u8], seed: u64) -> PyResult<Vec<(i64, Vec<f64>)>> {
    let cfg = decode_cfg(data).map_err(|e| PyValueError::new_err(e.to_string()))?;
    Ok(build_memory(&cfg, seed))
}

/// Maps a core interpreter error onto the Python exception type the legacy
/// interpreter raises for the same condition (exact message preserved).
fn interpreter_error_to_py(e: InterpreterError) -> PyErr {
    match e.kind {
        InterpreterErrorKind::Assertion => PyAssertionError::new_err(e.message),
        InterpreterErrorKind::ZeroDivision => PyZeroDivisionError::new_err(e.message),
        InterpreterErrorKind::Value => PyValueError::new_err(e.message),
        InterpreterErrorKind::Overflow => PyOverflowError::new_err(e.message),
        InterpreterErrorKind::Index => PyIndexError::new_err(e.message),
        InterpreterErrorKind::NotImplemented => PyNotImplementedError::new_err(e.message),
        // EvalBudgetExceeded (settable via `Interpreter.set_eval_budget`) maps
        // to RuntimeError with the distinct "eval budget exceeded ..." message
        // (tools/metrics.py matches on that prefix for runaway handling).
        InterpreterErrorKind::Runtime | InterpreterErrorKind::EvalBudgetExceeded => {
            PyRuntimeError::new_err(e.message)
        }
    }
}

/// An immutable engine-node tree (arena form; see `sonolus-backend-core::nodes`).
///
/// Built from nested Python data: a node is a number (`int` -> int-tagged constant,
/// `float` -> float-tagged constant) or an `(op, args)` pair (tuple or list) where
/// `op` is an op name (`str`) or stable op id (`int`) and `args` is a tuple/list of
/// nodes. Construction is iterative — arbitrarily deep trees are fine. T1.2's emitter
/// will produce these directly from the compilation pipeline.
#[pyclass(frozen)]
struct EngineNodes {
    inner: CoreEngineNodes,
}

#[pymethods]
impl EngineNodes {
    #[new]
    fn new(data: &Bound<'_, PyAny>) -> PyResult<Self> {
        Ok(Self {
            inner: build_engine_nodes(data)?,
        })
    }

    /// Renders the tree like `sonolus.backend.node.format_engine_node` (decision D7:
    /// Rust float formatting).
    fn format(&self) -> String {
        format_engine_node(&self.inner.arena, self.inner.root)
    }

    /// Total number of nodes in the arena.
    fn node_count(&self) -> usize {
        self.inner.arena.len()
    }

    /// Total node count of the tree rooted at the root, counting shared arena
    /// nodes once per occurrence (the `static_nodes` metric — pre-DAG-dedup;
    /// saturating).
    fn tree_node_count(&self) -> u64 {
        tree_node_count(&self.inner.arena, self.inner.root)
    }

    /// Output-node count after DAG dedup (the `dag_size` metric; legacy
    /// `OutputNodeGenerator` semantics).
    ///
    /// Raises `ValueError` if a NaN constant is reachable (impossible for
    /// emitter-produced trees).
    fn output_node_count(&self) -> PyResult<usize> {
        let out = output::generate_output_nodes(&self.inner.arena, self.inner.root)
            .map_err(|e| PyValueError::new_err(e.to_string()))?;
        Ok(out.nodes.len())
    }
}

/// Intermediate item while converting Python data (children hold item indices).
enum Item {
    Const { value: f64, is_int: bool },
    Func { op: Op, children: Vec<usize> },
}

/// Hard cap on constructed nodes: nested Python *lists* can be cyclic, and a cycle
/// would otherwise expand forever.
const NODE_LIMIT: usize = 10_000_000;

fn parse_op(obj: &Bound<'_, PyAny>) -> PyResult<Op> {
    if let Ok(name) = obj.cast::<PyString>() {
        let name = name.to_cow()?;
        Op::from_name(&name)
            .ok_or_else(|| PyValueError::new_err(format!("unknown op name: {name}")))
    } else if obj.is_instance_of::<PyInt>() {
        let id: u16 = obj.extract()?;
        Op::from_id(id).ok_or_else(|| PyValueError::new_err(format!("unknown op id: {id}")))
    } else {
        Err(PyTypeError::new_err(
            "op must be an op name (str) or op id (int)",
        ))
    }
}

fn sequence_items<'py>(obj: &Bound<'py, PyAny>) -> PyResult<Vec<Bound<'py, PyAny>>> {
    if let Ok(tuple) = obj.cast::<PyTuple>() {
        Ok(tuple.iter().collect())
    } else if let Ok(list) = obj.cast::<PyList>() {
        Ok(list.iter().collect())
    } else {
        Err(PyTypeError::new_err(
            "engine node arguments must be a tuple or list",
        ))
    }
}

/// Work-stack entry while walking the Python structure: the object plus its slot
/// (parent item index, argument position) when it is not the root.
type WalkEntry<'py> = (Bound<'py, PyAny>, Option<(usize, usize)>);

/// Iteratively converts nested Python data into arena form (no recursion: node trees
/// are user-sized).
fn build_engine_nodes(data: &Bound<'_, PyAny>) -> PyResult<CoreEngineNodes> {
    // Phase 1: explicit-stack walk of the Python structure. Children are created
    // strictly after their parent, so child item indices are always greater.
    let mut items: Vec<Item> = Vec::new();
    let mut stack: Vec<WalkEntry<'_>> = vec![(data.clone(), None)];
    while let Some((obj, parent)) = stack.pop() {
        if items.len() >= NODE_LIMIT {
            return Err(PyValueError::new_err(
                "engine node structure is too large (possible cycle through a list)",
            ));
        }
        let index = items.len();
        if let Some((parent_index, slot)) = parent {
            let Item::Func { children, .. } = &mut items[parent_index] else {
                unreachable!("parent is always a function item");
            };
            children[slot] = index;
        }
        if obj.is_instance_of::<PyFloat>() {
            items.push(Item::Const {
                value: obj.extract::<f64>()?,
                is_int: false,
            });
        } else if obj.is_instance_of::<PyInt>() {
            items.push(Item::Const {
                value: obj.extract::<f64>()?,
                is_int: true,
            });
        } else if obj.is_instance_of::<PyTuple>() || obj.is_instance_of::<PyList>() {
            let pair = sequence_items(&obj)?;
            if pair.len() != 2 {
                return Err(PyTypeError::new_err(
                    "an engine node pair must be (op, args)",
                ));
            }
            let op = parse_op(&pair[0])?;
            let children = sequence_items(&pair[1])?;
            items.push(Item::Func {
                op,
                children: vec![usize::MAX; children.len()],
            });
            for (slot, child) in children.into_iter().enumerate() {
                stack.push((child, Some((index, slot))));
            }
        } else {
            return Err(PyTypeError::new_err(format!(
                "expected a number or an (op, args) pair, got {}",
                obj.get_type().name()?
            )));
        }
    }
    // Phase 2: build the arena bottom-up (reverse item order puts every child before
    // its parent).
    let mut arena = NodeArena::new();
    let mut ids: Vec<Option<NodeId>> = vec![None; items.len()];
    let mut scratch: Vec<NodeId> = Vec::new();
    for i in (0..items.len()).rev() {
        let id = match &items[i] {
            Item::Const { value, is_int } => arena.push_const(*value, *is_int),
            Item::Func { op, children } => {
                scratch.clear();
                scratch.extend(
                    children
                        .iter()
                        .map(|&child| ids[child].expect("children are built before parents")),
                );
                arena.push_func(*op, &scratch)
            }
        };
        ids[i] = Some(id);
    }
    Ok(CoreEngineNodes {
        arena,
        root: ids[0].expect("at least the root item exists"),
    })
}

/// The engine-node interpreter (see `sonolus-backend-core::interpret` for the full
/// semantic contract, counter definitions, and divergence notes).
#[pyclass]
struct Interpreter {
    inner: CoreInterpreter,
}

#[pymethods]
#[allow(clippy::needless_pass_by_value)]
impl Interpreter {
    /// `Interpreter(seed=None, tape=None)`: `seed` (default 0) seeds the
    /// deterministic RNG; passing `tape` switches to RNG tape mode instead.
    #[new]
    #[pyo3(signature = (seed=None, tape=None))]
    fn new(seed: Option<i64>, tape: Option<Vec<f64>>) -> Self {
        #[allow(clippy::cast_sign_loss)]
        let mut inner = CoreInterpreter::new(seed.unwrap_or(0) as u64);
        if let Some(tape) = tape {
            inner.set_rng_tape(tape);
        }
        Self { inner }
    }

    /// Switches the RNG to tape mode (values returned in order; exhaustion raises).
    fn set_rng_tape(&mut self, values: Vec<f64>) {
        self.inner.set_rng_tape(values);
    }

    /// Sets (or clears, with `None`) the eval budget: once the cumulative
    /// `eval_count` exceeds it, evaluation stops with a `RuntimeError`
    /// whose message starts with `"eval budget exceeded"` (the T2.3/T2.4
    /// termination backstop — a budget cutoff is not a semantic fact).
    #[pyo3(signature = (budget))]
    fn set_eval_budget(&mut self, budget: Option<u64>) {
        self.inner.set_eval_budget(budget);
    }

    /// Enables (or disables) the METRICS-only runtime-op stub mode (default
    /// off): runtime-only ops the interpreter does not implement (`Draw`,
    /// `BeatToTime`, `ExportValue`, ...) evaluate their arguments in order
    /// and produce `0.0` instead of raising `NotImplementedError`. Used only
    /// by `tools/metrics.py --stub-runtime-ops`; see
    /// `Interpreter::set_stub_runtime_ops` in `sonolus-backend-core` for the
    /// exact rule.
    fn set_stub_runtime_ops(&mut self, enabled: bool) {
        self.inner.set_stub_runtime_ops(enabled);
    }

    /// Replaces a block's contents (legacy `interpreter.blocks[id] = values`).
    fn set_block(&mut self, id: i64, values: Vec<f64>) {
        self.inner.set_block(id, values);
    }

    /// The current contents of a block, or None if it was never touched.
    fn get_block(&self, id: i64) -> Option<Vec<f64>> {
        self.inner.block(id).map(<[f64]>::to_vec)
    }

    /// Sorted ids of all existing blocks.
    fn block_ids(&self) -> Vec<i64> {
        self.inner.block_ids()
    }

    /// The legacy mutating `get`: extends the block with -1.0 fill and returns the
    /// value. Raises `AssertionError` with the exact legacy messages.
    fn get(&mut self, block: f64, index: f64) -> PyResult<f64> {
        self.inner
            .get(block, index)
            .map_err(interpreter_error_to_py)
    }

    /// The legacy `set`: writes and returns the value.
    fn set(&mut self, block: f64, index: f64, value: f64) -> PyResult<f64> {
        self.inner
            .set(block, index, value)
            .map_err(interpreter_error_to_py)
    }

    /// Runs an engine-node tree and returns the result.
    fn run(&mut self, nodes: PyRef<'_, EngineNodes>) -> PyResult<f64> {
        self.inner
            .run(&nodes.inner)
            .map_err(interpreter_error_to_py)
    }

    /// `Op::DebugLog` values, in order.
    #[getter]
    fn log(&self) -> Vec<f64> {
        self.inner.log().to_vec()
    }

    /// Node-evaluation counter (one increment per node evaluation, equivalent to one
    /// legacy `run()` call, including constants; accumulates across runs).
    #[getter]
    fn eval_count(&self) -> u64 {
        self.inner.eval_count()
    }

    /// `JumpLoop` dispatch counter (one increment per non-tail index-walk step;
    /// accumulates across runs).
    #[getter]
    fn dispatch_count(&self) -> u64 {
        self.inner.dispatch_count()
    }
}

/// The `sonolus_backend` Python extension module.
#[pymodule(gil_used = false)]
fn sonolus_backend(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(backend_version, m)?)?;
    m.add_function(wrap_pyfunction!(decode_cfg_canonical_dump, m)?)?;
    m.add_function(wrap_pyfunction!(decode_cfg_debug_dump, m)?)?;
    m.add_function(wrap_pyfunction!(cfg_to_engine_nodes, m)?)?;
    m.add_function(wrap_pyfunction!(engine_nodes_to_output_dump, m)?)?;
    m.add_function(wrap_pyfunction!(run_pipeline, m)?)?;
    m.add_function(wrap_pyfunction!(run_pipeline_stats, m)?)?;
    m.add_function(wrap_pyfunction!(seeded_memory, m)?)?;
    m.add_function(wrap_pyfunction!(build_engine, m)?)?;
    m.add_class::<EngineNodes>()?;
    m.add_class::<Interpreter>()?;
    m.add_class::<collection::Collection>()?;
    Ok(())
}
