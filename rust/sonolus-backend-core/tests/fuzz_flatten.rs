//! Per-transform CFG fuzz for the T3.10 emission-time
//! `FlattenAssociativeOps`:
//!
//! 1. the standard generator (`fuzzgen::program`), `minimal` vs
//!    `minimal`+flatten — the transform alone on raw frontend shapes
//!    (binarized Add/Multiply chains, right-nested And/Or, Mod-clamped
//!    dynamic indices that must *not* flatten), and
//! 2. the **diamond-heavy** profile, full standard passes *without*
//!    flattening vs *with* flattening — isolating the seam on the
//!    if-converted trees (T3.8 arm `Execute` chains, `And`/`Or` value nodes,
//!    GVN const-first `Add` shapes) where the transform does its real work.
//!
//! Default 256 cases (PR CI); scale with `SONOLUS_FUZZ_CASES`, e.g.
//! `SONOLUS_FUZZ_CASES=50000 cargo test --release -p sonolus-backend-core
//! --test fuzz_flatten`. Failures persist to
//! `tests/proptest-regressions/fuzz_flatten.txt`
//! (`FileFailurePersistence::Direct` — proptest's default resolution is
//! broken in integration tests, see tests/fuzz.rs) and replay first.

#[path = "common/fuzzgen.rs"]
mod fuzzgen;

use std::cell::Cell;
use std::time::Instant;

use proptest::prelude::*;
use proptest::test_runner::{Config, FileFailurePersistence, TestCaseError, TestRunner};
use sonolus_backend_core::diff::{DiffConfig, DiffOutcome, diff_with};
use sonolus_backend_core::passes::{Pipeline, passes_for_level};
use sonolus_backend_core::pipeline::{Level, compile_cfg, compile_cfg_with_pipeline};

/// Per-side eval budget (same rationale as tests/fuzz.rs).
const EVAL_BUDGET: u64 = 500_000;

fn fuzz_cases() -> u32 {
    std::env::var("SONOLUS_FUZZ_CASES")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(256)
}

const PERSISTENCE_FILE: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/tests/proptest-regressions/fuzz_flatten.txt"
);

fn fuzz_config(cases: u32) -> Config {
    Config {
        cases,
        failure_persistence: Some(Box::new(FileFailurePersistence::Direct(PERSISTENCE_FILE))),
        source_file: Some(file!()),
        ..Config::default()
    }
}

/// The transform alone: minimal vs minimal+flatten on the standard profile.
#[test]
fn fuzz_differential_minimal_vs_minimal_plus_flatten() {
    let cases = fuzz_cases();
    let mut runner = TestRunner::new(fuzz_config(cases));
    let matched = Cell::new(0u64);
    let inconclusive = Cell::new(0u64);
    let strategy = (fuzzgen::program(), any::<u64>(), any::<u64>());
    let started = Instant::now();
    let result = runner.run(&strategy, |(program, memory_seed, rng_seed)| {
        let cfg = fuzzgen::build_cfg(&program);
        let config = DiffConfig {
            memory_seed,
            rng_seed,
            eval_budget: EVAL_BUDGET,
        };
        let outcome = diff_with(
            &cfg,
            |c| compile_cfg(c, Level::Minimal),
            |c| compile_cfg_with_pipeline(c, &Pipeline::new(vec![]).with_flatten(true)),
            &config,
        );
        match outcome {
            DiffOutcome::Match => matched.set(matched.get() + 1),
            DiffOutcome::Inconclusive { .. } => inconclusive.set(inconclusive.get() + 1),
            DiffOutcome::Mismatch(m) => {
                return Err(TestCaseError::fail(format!(
                    "minimal vs minimal+flatten: {m}"
                )));
            }
        }
        Ok(())
    });
    println!(
        "flatten fuzz differential: {cases} cases ({} matched, {} inconclusive) in {:?}",
        matched.get(),
        inconclusive.get(),
        started.elapsed()
    );
    if let Err(e) = result {
        panic!(
            "flatten fuzz differential failure (persisted to \
             tests/proptest-regressions/fuzz_flatten.txt; re-running replays it first): {e}"
        );
    }
}

/// The seam isolated on optimizer-shaped trees: full standard passes,
/// unflattened vs flattened, on the diamond-heavy profile (if-converted
/// `Execute`/`And`/`Or` value trees are exactly the shapes the splice walks).
#[test]
fn fuzz_differential_diamond_heavy_unflattened_vs_flattened() {
    let cases = fuzz_cases();
    let mut runner = TestRunner::new(fuzz_config(cases));
    let matched = Cell::new(0u64);
    let inconclusive = Cell::new(0u64);
    let strategy = (fuzzgen::program_diamond_heavy(), any::<u64>(), any::<u64>());
    let started = Instant::now();
    let result = runner.run(&strategy, |(program, memory_seed, rng_seed)| {
        let cfg = fuzzgen::build_cfg(&program);
        let config = DiffConfig {
            memory_seed,
            rng_seed,
            eval_budget: EVAL_BUDGET,
        };
        let outcome = diff_with(
            &cfg,
            |c| compile_cfg_with_pipeline(c, &Pipeline::new(passes_for_level(Level::Standard))),
            |c| {
                compile_cfg_with_pipeline(
                    c,
                    &Pipeline::new(passes_for_level(Level::Standard)).with_flatten(true),
                )
            },
            &config,
        );
        match outcome {
            DiffOutcome::Match => matched.set(matched.get() + 1),
            DiffOutcome::Inconclusive { .. } => inconclusive.set(inconclusive.get() + 1),
            DiffOutcome::Mismatch(m) => {
                return Err(TestCaseError::fail(format!(
                    "diamond-heavy standard unflattened vs flattened: {m}"
                )));
            }
        }
        Ok(())
    });
    println!(
        "flatten diamond-heavy fuzz: {cases} cases ({} matched, {} inconclusive) in {:?}",
        matched.get(),
        inconclusive.get(),
        started.elapsed()
    );
    if let Err(e) = result {
        panic!(
            "flatten diamond-heavy fuzz failure (persisted to \
             tests/proptest-regressions/fuzz_flatten.txt; re-running replays it first): {e}"
        );
    }
}
