//! Differential-interpretation harness (PORT.md T2.3, invariant §3.7).
//!
//! Compares two compilations of the same frontend [`Cfg`] — by default the
//! `minimal` baseline against an optimized level — by running both on fresh
//! [`Interpreter`]s with **identical** RNG seed and **identical** seeded-random
//! initial block memory, and comparing every observable:
//!
//! - the final run result (or the error outcome: kind **and** message),
//! - the debug log,
//! - written memory, **excluding** temp block 10000
//!   ([`TEMP_RUNTIME_BLOCK`] — slot allocation is pipeline-specific by contract),
//! - the RNG draw count (draw-order/count preservation is part of the optimizer
//!   contract: the same seed must yield identical draws at every level; a
//!   draw-count mismatch is surfaced distinctly as [`MismatchKind::RngDraws`]).
//!
//! An [`InterpreterErrorKind::EvalBudgetExceeded`] outcome on **either** side
//! makes the case [`DiffOutcome::Inconclusive`] (discarded, counted, never a
//! failure): the budget is the termination backstop, not a semantic fact.
//!
//! # How T3.x tasks use this
//!
//! - Level-vs-level (corpus runs, wave gates):
//!   [`diff_levels`]`(cfg, Level::Minimal, Level::Standard, &config)`.
//! - Per-transform (a single pass or bespoke pass list under test):
//!   [`diff_with`] with closures, typically
//!   `|cfg| compile_cfg_with_pipeline(cfg, &my_pipeline)` for the test side
//!   (see [`crate::pipeline::compile_cfg_with_pipeline`]) and the `minimal`
//!   level for the base side.
//! - Aggregate over a corpus or fuzz run with [`DiffSummary::record`] and
//!   assert with [`DiffSummary::assert_no_mismatches`].
//!
//! # Memory randomization
//!
//! [`build_memory`] discovers which runtime blocks the CFG can read — every
//! concrete `BlockPlace` block id plus constant block-id first arguments of
//! memory ops — and fills each with values deterministic in the seed: a mix of
//! small non-negative ints (index-shaped, the majority so dynamic indexing does
//! not instantly trap), `0`/`-1`/`65535`, small negative ints, integral floats,
//! floats in `[-1, 1)`, and full-range floats. Block 3000 (`EngineRom`) always
//! gets `NaN`/`+inf`/`-inf` at `[0..2]`, exactly like the runtime provides
//! (NaN/inf constants compile to ROM reads). Block 10000 is **never** filled:
//! temp-slot layout is pipeline-specific, so pre-seeding it would make
//! uninitialized-temp reads allocation-dependent; unfilled cells read the
//! interpreter default `-1.0` on every side instead.
//!
//! Everything here is deterministic: the same `(cfg, config)` always produces
//! the same outcome.

use std::collections::BTreeSet;
use std::fmt;
use std::fmt::Write as _;

use crate::alloc::TEMP_RUNTIME_BLOCK;
use crate::cfg::{BlockValue, Cfg, Node};
use crate::effects::op_effects;
use crate::interpret::{Interpreter, InterpreterError, InterpreterErrorKind};
use crate::nodes::EngineNodes;
use crate::pipeline::{CompileError, Level, compile_cfg};

/// The `EngineRom` block id (NaN/±inf constants are emitted as reads of it).
pub const ROM_BLOCK: i64 = 3000;

/// Configuration of one differential case.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct DiffConfig {
    /// Seed for the randomized initial block memory (see [`build_memory`]).
    pub memory_seed: u64,
    /// Seed for both interpreters' RNGs (identical on both sides by
    /// construction — that *is* the draw-preservation contract under test).
    pub rng_seed: u64,
    /// Eval budget per side (termination backstop; see the module docs).
    pub eval_budget: u64,
}

impl Default for DiffConfig {
    fn default() -> Self {
        Self {
            memory_seed: 0,
            rng_seed: 0,
            eval_budget: 1_000_000,
        }
    }
}

/// What differed, when a [`DiffOutcome::Mismatch`] is reported.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MismatchKind {
    /// The two sides did not agree on whether/how compilation fails.
    Compile,
    /// Both ran to completion with different results.
    Result,
    /// Error outcomes differ (error vs success, or different kind/message).
    Error,
    /// Debug logs differ.
    Log,
    /// Written memory differs (excluding [`TEMP_RUNTIME_BLOCK`]).
    Writes,
    /// RNG draw counts differ (the draw-preservation contract; surfaced
    /// distinctly because it is an optimizer-contract violation even when no
    /// downstream value happens to diverge).
    RngDraws,
}

/// A reported behavioral difference.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Mismatch {
    pub kind: MismatchKind,
    /// Human-readable description of the first observed divergence.
    pub detail: String,
}

impl fmt::Display for Mismatch {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{:?}: {}", self.kind, self.detail)
    }
}

/// The outcome of one differential case.
#[derive(Debug, Clone, PartialEq)]
pub enum DiffOutcome {
    /// Every observable agreed (including "both failed to compile with the
    /// identical error" and "both trapped with the identical error").
    Match,
    /// The eval budget was exceeded on at least one side: the case is
    /// discarded (counted, never a failure).
    Inconclusive {
        base_budget: bool,
        test_budget: bool,
    },
    /// A behavioral difference — the thing this harness exists to catch.
    Mismatch(Mismatch),
}

/// Everything observable about one side's run.
#[derive(Debug, Clone, PartialEq)]
pub struct RunObservation {
    /// The run result, or the error outcome (kind + message).
    pub result: Result<f64, InterpreterError>,
    /// `DebugLog` values, in order.
    pub log: Vec<f64>,
    /// Last-write-wins writes, sorted by `(block, index)`, excluding
    /// [`TEMP_RUNTIME_BLOCK`].
    pub writes: Vec<(i64, i64, f64)>,
    /// Successful RNG draws.
    pub rng_draws: u64,
    /// Node evaluations (diagnostics; not compared — levels legitimately
    /// differ here, that is the point of optimizing).
    pub eval_count: u64,
    /// Whether the run was cut off by the eval budget.
    pub budget_exceeded: bool,
}

// ----------------------------------------------------------------------------------
// Seeded memory randomization
// ----------------------------------------------------------------------------------

/// `SplitMix64` (Steele, Lea, Flood 2014) — private copy for memory filling,
/// independent of the interpreter's RNG stream.
struct SplitMix64 {
    state: u64,
}

impl SplitMix64 {
    fn new(seed: u64) -> Self {
        Self { state: seed }
    }

    fn next_u64(&mut self) -> u64 {
        self.state = self.state.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = self.state;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    }

    #[allow(clippy::cast_precision_loss)]
    fn next_f64(&mut self) -> f64 {
        (self.next_u64() >> 11) as f64 * (1.0 / (1u64 << 53) as f64)
    }
}

/// Cells per randomized block.
const FILL_LEN: usize = 32;

/// One value of the documented mix (module docs). Small non-negative ints are
/// the majority so index-shaped slots are plausible.
#[allow(clippy::cast_precision_loss)]
fn mixed_value(rng: &mut SplitMix64) -> f64 {
    match rng.next_u64() % 100 {
        0..=34 => (rng.next_u64() % 16) as f64, // small ints
        35..=44 => 0.0,
        45..=52 => -1.0,
        53..=57 => 65535.0,
        58..=64 => -((rng.next_u64() % 16) as f64 + 1.0), // small negatives
        65..=76 => (rng.next_u64() % 20_001) as f64 - 10_000.0, // integral floats
        77..=89 => rng.next_f64() * 2.0 - 1.0,            // [-1, 1)
        _ => {
            // Full-range floats: random sign and binary exponent in [-60, 60].
            #[allow(clippy::cast_possible_truncation, clippy::cast_possible_wrap)]
            let exp = (rng.next_u64() % 121) as i32 - 60;
            let sign = if rng.next_u64() & 1 == 0 { 1.0 } else { -1.0 };
            sign * rng.next_f64() * 2.0f64.powi(exp)
        }
    }
}

/// Discovers the runtime blocks a CFG can read: every concrete (`Int`)
/// `BlockPlace` block id, plus constant first arguments of memory-touching ops
/// (`Get`/`Set`/`GetShifted`/..., whose first argument is the block id).
/// Always includes [`ROM_BLOCK`]; never includes [`TEMP_RUNTIME_BLOCK`]
/// (module docs). Sorted (deterministic).
pub fn discover_read_blocks(cfg: &Cfg) -> Vec<i64> {
    let mut ids: BTreeSet<i64> = BTreeSet::new();
    ids.insert(ROM_BLOCK);
    for place in &cfg.places {
        if let BlockValue::Int(id) = place.block {
            ids.insert(id);
        }
    }
    for node in &cfg.nodes {
        if let Node::PureInstr { op, args } | Node::Instr { op, args } = node {
            let effects = op_effects(*op);
            if (effects.reads_memory || effects.writes_memory)
                && let Some(&first) = args.first()
                && let Node::ConstInt(id) = cfg.nodes[first]
            {
                ids.insert(id);
            }
        }
    }
    ids.remove(&TEMP_RUNTIME_BLOCK);
    ids.into_iter().collect()
}

/// Builds the randomized initial memory for a CFG: one [`FILL_LEN`]-cell block
/// per discovered read block, values deterministic in `memory_seed` (and the
/// block id, so blocks differ from each other). [`ROM_BLOCK`] starts with
/// `NaN`/`+inf`/`-inf` exactly like the runtime. Sorted by block id.
pub fn build_memory(cfg: &Cfg, memory_seed: u64) -> Vec<(i64, Vec<f64>)> {
    discover_read_blocks(cfg)
        .into_iter()
        .map(|block| {
            #[allow(clippy::cast_sign_loss)]
            let mut rng =
                SplitMix64::new(memory_seed ^ (block as u64).wrapping_mul(0x9E37_79B9_7F4A_7C15));
            let mut values: Vec<f64> = Vec::with_capacity(FILL_LEN);
            if block == ROM_BLOCK {
                values.extend([f64::NAN, f64::INFINITY, f64::NEG_INFINITY]);
            }
            while values.len() < FILL_LEN {
                values.push(mixed_value(&mut rng));
            }
            (block, values)
        })
        .collect()
}

// ----------------------------------------------------------------------------------
// Running and comparing
// ----------------------------------------------------------------------------------

/// Runs one engine-node tree against the given initial memory with a fresh
/// seeded interpreter and an eval budget, and collects every observable.
pub fn run_with_memory(
    nodes: &EngineNodes,
    memory: &[(i64, Vec<f64>)],
    rng_seed: u64,
    eval_budget: u64,
) -> RunObservation {
    let mut interp = Interpreter::new(rng_seed);
    interp.set_eval_budget(Some(eval_budget));
    interp.record_writes();
    for (block, values) in memory {
        interp.set_block(*block, values.clone());
    }
    let result = interp.run(nodes);
    let budget_exceeded = matches!(
        &result,
        Err(e) if e.kind == InterpreterErrorKind::EvalBudgetExceeded
    );
    let writes: Vec<(i64, i64, f64)> = interp
        .recorded_writes()
        .expect("write recording was enabled")
        .into_iter()
        .filter(|&(block, _, _)| block != TEMP_RUNTIME_BLOCK)
        .collect();
    RunObservation {
        result,
        log: interp.log().to_vec(),
        writes,
        rng_draws: interp.rng_draw_count(),
        eval_count: interp.eval_count(),
        budget_exceeded,
    }
}

/// Value equality for differential comparison: raw bit equality, except any
/// NaN equals any NaN and `+0.0 == -0.0` (the documented legacy contract — the
/// behavioral suite's Python `==` cannot distinguish them either; see
/// `rust/testdata/README.md`).
#[allow(clippy::float_cmp)] // the zero comparison is the exact ±0 rule
pub fn values_match(a: f64, b: f64) -> bool {
    if a.is_nan() && b.is_nan() {
        return true;
    }
    if a == 0.0 && b == 0.0 {
        return true;
    }
    a.to_bits() == b.to_bits()
}

fn mismatch(kind: MismatchKind, detail: String) -> DiffOutcome {
    DiffOutcome::Mismatch(Mismatch { kind, detail })
}

/// Compares two observations (module-doc rules). Public so single-side runs
/// captured with [`run_with_memory`] can be compared by other drivers.
pub fn compare_observations(base: &RunObservation, test: &RunObservation) -> DiffOutcome {
    if base.budget_exceeded || test.budget_exceeded {
        return DiffOutcome::Inconclusive {
            base_budget: base.budget_exceeded,
            test_budget: test.budget_exceeded,
        };
    }
    match (&base.result, &test.result) {
        (Ok(a), Ok(b)) => {
            if !values_match(*a, *b) {
                return mismatch(MismatchKind::Result, format!("base {a:?} != test {b:?}"));
            }
        }
        (Err(a), Err(b)) => {
            if a.kind != b.kind || a.message != b.message {
                return mismatch(
                    MismatchKind::Error,
                    format!(
                        "base error {:?} ({}) != test error {:?} ({})",
                        a.kind, a.message, b.kind, b.message
                    ),
                );
            }
        }
        (Ok(a), Err(b)) => {
            return mismatch(
                MismatchKind::Error,
                format!(
                    "base succeeded with {a:?}, test errored: {:?} ({})",
                    b.kind, b.message
                ),
            );
        }
        (Err(a), Ok(b)) => {
            return mismatch(
                MismatchKind::Error,
                format!(
                    "base errored: {:?} ({}), test succeeded with {b:?}",
                    a.kind, a.message
                ),
            );
        }
    }
    if base.log.len() != test.log.len() {
        return mismatch(
            MismatchKind::Log,
            format!("log length {} != {}", base.log.len(), test.log.len()),
        );
    }
    for (i, (a, b)) in base.log.iter().zip(&test.log).enumerate() {
        if !values_match(*a, *b) {
            return mismatch(
                MismatchKind::Log,
                format!("log[{i}] base {a:?} != test {b:?}"),
            );
        }
    }
    {
        let base_cells: Vec<(i64, i64)> = base.writes.iter().map(|&(b, i, _)| (b, i)).collect();
        let test_cells: Vec<(i64, i64)> = test.writes.iter().map(|&(b, i, _)| (b, i)).collect();
        if base_cells != test_cells {
            return mismatch(
                MismatchKind::Writes,
                format!("written cells differ: base {base_cells:?} != test {test_cells:?}"),
            );
        }
        for (&(block, index, a), &(_, _, b)) in base.writes.iter().zip(&test.writes) {
            if !values_match(a, b) {
                return mismatch(
                    MismatchKind::Writes,
                    format!("write ({block}, {index}) base {a:?} != test {b:?}"),
                );
            }
        }
    }
    if base.rng_draws != test.rng_draws {
        return mismatch(
            MismatchKind::RngDraws,
            format!(
                "RNG draw count {} != {} (draw-order preservation violated)",
                base.rng_draws, test.rng_draws
            ),
        );
    }
    DiffOutcome::Match
}

/// Differentially interprets one CFG compiled by two arbitrary compile
/// functions (the per-transform entry point; see the module docs).
pub fn diff_with<B, T>(
    cfg: &Cfg,
    compile_base: B,
    compile_test: T,
    config: &DiffConfig,
) -> DiffOutcome
where
    B: Fn(&Cfg) -> Result<EngineNodes, CompileError>,
    T: Fn(&Cfg) -> Result<EngineNodes, CompileError>,
{
    let base_nodes = compile_base(cfg);
    let test_nodes = compile_test(cfg);
    let (base_nodes, test_nodes) = match (base_nodes, test_nodes) {
        (Ok(b), Ok(t)) => (b, t),
        (Err(b), Err(t)) => {
            if b == t {
                // Both pipelines rejected the CFG identically: vacuous agreement.
                return DiffOutcome::Match;
            }
            return mismatch(
                MismatchKind::Compile,
                format!("base compile error {b} != test compile error {t}"),
            );
        }
        (Ok(_), Err(t)) => {
            return mismatch(
                MismatchKind::Compile,
                format!("base compiled, test failed to compile: {t}"),
            );
        }
        (Err(b), Ok(_)) => {
            return mismatch(
                MismatchKind::Compile,
                format!("base failed to compile ({b}), test compiled"),
            );
        }
    };
    let memory = build_memory(cfg, config.memory_seed);
    let base = run_with_memory(&base_nodes, &memory, config.rng_seed, config.eval_budget);
    let test = run_with_memory(&test_nodes, &memory, config.rng_seed, config.eval_budget);
    compare_observations(&base, &test)
}

/// Differentially interprets one CFG compiled at two optimization levels
/// (the corpus / wave-gate entry point).
pub fn diff_levels(cfg: &Cfg, base: Level, test: Level, config: &DiffConfig) -> DiffOutcome {
    diff_with(
        cfg,
        |c| compile_cfg(c, base),
        |c| compile_cfg(c, test),
        config,
    )
}

// ----------------------------------------------------------------------------------
// Aggregation
// ----------------------------------------------------------------------------------

/// Tallies outcomes over a corpus or fuzz run.
#[derive(Debug, Clone, Default)]
pub struct DiffSummary {
    pub cases: usize,
    pub matched: usize,
    pub inconclusive: usize,
    /// `(label, mismatch)` pairs, in encounter order.
    pub mismatches: Vec<(String, Mismatch)>,
}

impl DiffSummary {
    /// Records one case's outcome under a label (used in failure reports).
    pub fn record(&mut self, label: impl Into<String>, outcome: &DiffOutcome) {
        self.cases += 1;
        match outcome {
            DiffOutcome::Match => self.matched += 1,
            DiffOutcome::Inconclusive { .. } => self.inconclusive += 1,
            DiffOutcome::Mismatch(m) => self.mismatches.push((label.into(), m.clone())),
        }
    }

    /// One-line counts plus every mismatch (for test output).
    pub fn report(&self) -> String {
        let mut out = format!(
            "{} cases: {} matched, {} inconclusive (budget), {} mismatches",
            self.cases,
            self.matched,
            self.inconclusive,
            self.mismatches.len()
        );
        for (label, m) in &self.mismatches {
            let _ = write!(out, "\n  {label}: {m}");
        }
        out
    }

    /// Panics with the full report if any mismatch was recorded.
    pub fn assert_no_mismatches(&self) {
        assert!(
            self.mismatches.is_empty(),
            "differential mismatches found:\n{}",
            self.report()
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::cfg::{BasicBlock, Edge, EdgeCond, IndexValue, Place};
    use crate::ops::Op;
    use crate::passes::Pipeline;
    use crate::pipeline::compile_cfg_with_pipeline;

    /// Tiny CFG builder for hand-built test programs.
    #[derive(Default)]
    struct B {
        cfg: Cfg,
    }

    impl B {
        fn node(&mut self, n: Node) -> usize {
            self.cfg.nodes.push(n);
            self.cfg.nodes.len() - 1
        }
        fn place_int(&mut self, block: i64, index: i64) -> usize {
            self.cfg.places.push(Place {
                block: BlockValue::Int(block),
                index: IndexValue::Int(index),
                offset: 0,
            });
            self.cfg.places.len() - 1
        }
        fn int(&mut self, v: i64) -> usize {
            self.node(Node::ConstInt(v))
        }
        fn set(&mut self, place: usize, value: usize) -> usize {
            self.node(Node::Set { place, value })
        }
        fn instr(&mut self, op: Op, args: Vec<usize>) -> usize {
            self.node(Node::Instr { op, args })
        }
        fn pure(&mut self, op: Op, args: Vec<usize>) -> usize {
            self.node(Node::PureInstr { op, args })
        }
        /// One exit block holding `stmts`.
        fn single_block(mut self, stmts: Vec<usize>) -> Cfg {
            let test = self.int(0);
            self.cfg.blocks.push(BasicBlock {
                statements: stmts,
                test,
                outgoing: vec![],
            });
            self.cfg
        }
    }

    /// `20[0] <- Add(1, 2)`.
    fn add_cfg() -> Cfg {
        let mut b = B::default();
        let p = b.place_int(20, 0);
        let one = b.int(1);
        let two = b.int(2);
        let add = b.pure(Op::Add, vec![one, two]);
        let s = b.set(p, add);
        b.single_block(vec![s])
    }

    fn minimal(c: &Cfg) -> Result<EngineNodes, CompileError> {
        compile_cfg(c, Level::Minimal)
    }

    #[test]
    fn identical_levels_match_on_all_level_pairs() {
        let cfg = add_cfg();
        for test in [Level::Minimal, Level::Fast, Level::Standard] {
            assert_eq!(
                diff_levels(&cfg, Level::Minimal, test, &DiffConfig::default()),
                DiffOutcome::Match
            );
        }
    }

    #[test]
    fn write_value_mismatch_is_caught() {
        // Test side compiled from a different CFG: 20[0] <- 4.
        let cfg = add_cfg();
        let mut b = B::default();
        let p = b.place_int(20, 0);
        let four = b.int(4);
        let s = b.set(p, four);
        let other = b.single_block(vec![s]);
        let outcome = diff_with(&cfg, minimal, |_| minimal(&other), &DiffConfig::default());
        let DiffOutcome::Mismatch(m) = outcome else {
            panic!("expected a mismatch, got {outcome:?}");
        };
        assert_eq!(m.kind, MismatchKind::Writes, "{m}");
    }

    #[test]
    fn log_mismatch_is_caught() {
        let log_cfg = |v: i64| {
            let mut b = B::default();
            let c = b.int(v);
            let log = b.instr(Op::DebugLog, vec![c]);
            b.single_block(vec![log])
        };
        let base = log_cfg(1);
        let other = log_cfg(2);
        let outcome = diff_with(&base, minimal, |_| minimal(&other), &DiffConfig::default());
        let DiffOutcome::Mismatch(m) = outcome else {
            panic!("expected a mismatch, got {outcome:?}");
        };
        assert_eq!(m.kind, MismatchKind::Log, "{m}");
    }

    #[test]
    fn result_mismatch_is_caught() {
        // Break(1, v) is the frontend return: result v.
        let ret_cfg = |v: i64| {
            let mut b = B::default();
            let one = b.int(1);
            let c = b.int(v);
            let brk = b.instr(Op::Break, vec![one, c]);
            b.single_block(vec![brk])
        };
        let base = ret_cfg(1);
        let other = ret_cfg(2);
        let outcome = diff_with(&base, minimal, |_| minimal(&other), &DiffConfig::default());
        let DiffOutcome::Mismatch(m) = outcome else {
            panic!("expected a mismatch, got {outcome:?}");
        };
        assert_eq!(m.kind, MismatchKind::Result, "{m}");
    }

    #[test]
    fn error_vs_success_is_caught() {
        // Base traps: Get(20, NaN-from-ROM) -> "cannot convert float NaN ...".
        let mut b = B::default();
        let rom_nan = b.place_int(ROM_BLOCK, 0);
        let nan = b.node(Node::Get(rom_nan));
        let twenty = b.int(20);
        let get = b.instr(Op::Get, vec![twenty, nan]);
        let log = b.instr(Op::DebugLog, vec![get]);
        let base = b.single_block(vec![log]);
        let ok = add_cfg();
        let outcome = diff_with(&base, minimal, |_| minimal(&ok), &DiffConfig::default());
        let DiffOutcome::Mismatch(m) = outcome else {
            panic!("expected a mismatch, got {outcome:?}");
        };
        assert_eq!(m.kind, MismatchKind::Error, "{m}");
        // Identical traps on both sides agree.
        assert_eq!(
            diff_levels(
                &base,
                Level::Minimal,
                Level::Standard,
                &DiffConfig::default()
            ),
            DiffOutcome::Match
        );
    }

    #[test]
    fn rng_draw_count_mismatch_is_surfaced_distinctly() {
        // Both write the constant 0 to 20[0]; the base additionally *draws*
        // (Equal(Random(0,1), 2) is always 0). Same writes, different draws.
        let mut b = B::default();
        let p = b.place_int(20, 0);
        let lo = b.int(0);
        let hi = b.int(1);
        let draw = b.instr(Op::Random, vec![lo, hi]);
        let two = b.int(2);
        let eq = b.pure(Op::Equal, vec![draw, two]);
        let s = b.set(p, eq);
        let base = b.single_block(vec![s]);

        let mut b = B::default();
        let p = b.place_int(20, 0);
        let zero = b.int(0);
        let s = b.set(p, zero);
        let other = b.single_block(vec![s]);

        let outcome = diff_with(&base, minimal, |_| minimal(&other), &DiffConfig::default());
        let DiffOutcome::Mismatch(m) = outcome else {
            panic!("expected a mismatch, got {outcome:?}");
        };
        assert_eq!(m.kind, MismatchKind::RngDraws, "{m}");
    }

    #[test]
    fn budget_exceeded_is_inconclusive_not_a_failure() {
        // Entry loops on itself forever: 0 -> 0 unconditionally.
        let mut b = B::default();
        let test = b.int(0);
        b.cfg.blocks.push(BasicBlock {
            statements: vec![],
            test,
            outgoing: vec![Edge {
                cond: EdgeCond::None,
                target: 0,
            }],
        });
        let cfg = b.cfg;
        let config = DiffConfig {
            eval_budget: 10_000,
            ..DiffConfig::default()
        };
        let outcome = diff_levels(&cfg, Level::Minimal, Level::Standard, &config);
        assert_eq!(
            outcome,
            DiffOutcome::Inconclusive {
                base_budget: true,
                test_budget: true,
            }
        );
        let mut summary = DiffSummary::default();
        summary.record("loop", &outcome);
        assert_eq!(summary.inconclusive, 1);
        summary.assert_no_mismatches(); // must not panic
    }

    #[test]
    fn explicit_pipeline_side_works() {
        // diff_with against compile_cfg_with_pipeline (the T3.x shape).
        let cfg = add_cfg();
        let outcome = diff_with(
            &cfg,
            minimal,
            |c| compile_cfg_with_pipeline(c, &Pipeline::new(vec![])),
            &DiffConfig::default(),
        );
        assert_eq!(outcome, DiffOutcome::Match);
    }

    #[test]
    fn discovery_finds_place_blocks_and_const_op_blocks() {
        // Place block 20; Op::Get with const block 21; ROM always; never 10000.
        let mut b = B::default();
        let p = b.place_int(20, 0);
        let g = b.node(Node::Get(p));
        let blk = b.int(21);
        let idx = b.int(0);
        let raw_get = b.instr(Op::Get, vec![blk, idx]);
        let add = b.pure(Op::Add, vec![g, raw_get]);
        let tmp = b.place_int(TEMP_RUNTIME_BLOCK, 0);
        let s = b.set(tmp, add);
        let cfg = b.single_block(vec![s]);
        assert_eq!(discover_read_blocks(&cfg), vec![20, 21, ROM_BLOCK]);
    }

    #[test]
    fn memory_is_deterministic_and_rom_shaped() {
        let cfg = add_cfg();
        let a = build_memory(&cfg, 7);
        let b = build_memory(&cfg, 7);
        assert_eq!(format!("{a:?}"), format!("{b:?}"), "same seed, same memory");
        let c = build_memory(&cfg, 8);
        assert_ne!(
            format!("{a:?}"),
            format!("{c:?}"),
            "different seed, different memory"
        );
        let rom = a
            .iter()
            .find(|(block, _)| *block == ROM_BLOCK)
            .expect("ROM present");
        assert!(rom.1[0].is_nan());
        assert_eq!(rom.1[1].to_bits(), f64::INFINITY.to_bits());
        assert_eq!(rom.1[2].to_bits(), f64::NEG_INFINITY.to_bits());
        assert_eq!(rom.1.len(), FILL_LEN);
    }

    #[test]
    fn diff_outcome_is_deterministic() {
        let cfg = add_cfg();
        let config = DiffConfig {
            memory_seed: 123,
            rng_seed: 456,
            eval_budget: 100_000,
        };
        let a = diff_levels(&cfg, Level::Minimal, Level::Standard, &config);
        let b = diff_levels(&cfg, Level::Minimal, Level::Standard, &config);
        assert_eq!(a, b);
    }
}
