//! Per-pass CFG fuzz for the T3.7 LICM pass:
//!
//! 1. the standard generator (`fuzzgen::program` — bounded counter loops up
//!    to 5 trips, nesting via `prop_recursive`), `minimal` vs
//!    `[Mem2Reg, SCCP, GVN, DCE, LICM]` (the value-SSA MIR shape the registry
//!    hands the pass, every `compile_cfg` including the unconditional
//!    `destruct_ssa` that legalizes the hoisted cross-block uses);
//! 2. the **dynamic-indexing-heavy** profile vs the full `standard` level
//!    (the registry now ends with LICM at W3) — unpromoted dynamic-index
//!    temps inside loops are exactly what the pass must refuse to touch.
//!
//! Default 256 cases (PR CI); scale with `SONOLUS_FUZZ_CASES`, e.g.
//! `SONOLUS_FUZZ_CASES=50000 cargo test --release -p sonolus-backend-core
//! --test fuzz_licm`. Failures persist to
//! `tests/proptest-regressions/fuzz_licm.txt`
//! (`FileFailurePersistence::Direct` — proptest's default resolution is
//! broken in integration tests, see tests/fuzz.rs) and replay first.

#[path = "common/fuzzgen.rs"]
mod fuzzgen;

use std::cell::Cell;
use std::time::Instant;

use proptest::prelude::*;
use proptest::test_runner::{Config, FileFailurePersistence, TestCaseError, TestRunner};
use sonolus_backend_core::diff::{DiffConfig, DiffOutcome, diff_with};
use sonolus_backend_core::passes::Pipeline;
use sonolus_backend_core::passes::dce::DcePass;
use sonolus_backend_core::passes::gvn::GvnRewritePass;
use sonolus_backend_core::passes::licm::LicmPass;
use sonolus_backend_core::passes::mem2reg::Mem2Reg;
use sonolus_backend_core::passes::sccp::Sccp;
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
    "/tests/proptest-regressions/fuzz_licm.txt"
);

fn fuzz_config(cases: u32) -> Config {
    Config {
        cases,
        failure_persistence: Some(Box::new(FileFailurePersistence::Direct(PERSISTENCE_FILE))),
        source_file: Some(file!()),
        ..Config::default()
    }
}

fn w2_plus_licm() -> Pipeline {
    Pipeline::new(vec![
        Box::new(Mem2Reg),
        Box::new(Sccp),
        Box::new(GvnRewritePass),
        Box::new(DcePass),
        Box::new(LicmPass),
    ])
}

/// The per-pass fuzz differential on the standard (loop-bearing) profile.
#[test]
fn fuzz_differential_minimal_vs_w2_plus_licm() {
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
            |c| compile_cfg_with_pipeline(c, &w2_plus_licm()),
            &config,
        );
        match outcome {
            DiffOutcome::Match => matched.set(matched.get() + 1),
            DiffOutcome::Inconclusive { .. } => inconclusive.set(inconclusive.get() + 1),
            DiffOutcome::Mismatch(m) => {
                return Err(TestCaseError::fail(format!("minimal vs w2+licm: {m}")));
            }
        }
        Ok(())
    });
    println!(
        "licm fuzz differential: {cases} cases ({} matched, {} inconclusive) in {:?}",
        matched.get(),
        inconclusive.get(),
        started.elapsed()
    );
    if let Err(e) = result {
        panic!(
            "licm fuzz differential failure (persisted to \
             tests/proptest-regressions/fuzz_licm.txt; re-running replays it first): {e}"
        );
    }
}

/// The dynamic-indexing-heavy profile against the full standard registry:
/// unpromoted (dynamic-index) temps inside loops are the loads LICM must
/// leave alone.
#[test]
fn fuzz_differential_dynamic_heavy_vs_standard() {
    let cases = fuzz_cases();
    let mut runner = TestRunner::new(fuzz_config(cases));
    let matched = Cell::new(0u64);
    let inconclusive = Cell::new(0u64);
    let strategy = (fuzzgen::program_dynamic_heavy(), any::<u64>(), any::<u64>());
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
            |c| compile_cfg(c, Level::Standard),
            &config,
        );
        match outcome {
            DiffOutcome::Match => matched.set(matched.get() + 1),
            DiffOutcome::Inconclusive { .. } => inconclusive.set(inconclusive.get() + 1),
            DiffOutcome::Mismatch(m) => {
                return Err(TestCaseError::fail(format!(
                    "dynamic-heavy minimal vs standard: {m}"
                )));
            }
        }
        Ok(())
    });
    println!(
        "licm dynamic-heavy fuzz: {cases} cases ({} matched, {} inconclusive) in {:?}",
        matched.get(),
        inconclusive.get(),
        started.elapsed()
    );
    if let Err(e) = result {
        panic!(
            "licm dynamic-heavy fuzz failure (persisted to \
             tests/proptest-regressions/fuzz_licm.txt; re-running replays it first): {e}"
        );
    }
}
