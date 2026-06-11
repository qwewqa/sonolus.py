//! Per-pass CFG fuzz for the T3.3 DCE pass: proptest-generated well-formed
//! frontend CFGs (`common/fuzzgen.rs`), differentially interpreted `minimal`
//! vs `minimal`+\[DcePass\] **standalone** (without SCCP/GVN — the pass must
//! be correct on its own).
//!
//! Default 256 cases (PR CI); scale with `SONOLUS_FUZZ_CASES`, e.g.
//! `SONOLUS_FUZZ_CASES=20000 cargo test --release -p sonolus-backend-core
//! --test fuzz_dce`. Failures persist to
//! `tests/proptest-regressions/fuzz_dce.txt` (`FileFailurePersistence::Direct`
//! — proptest's default resolution is broken in integration tests, see
//! tests/fuzz.rs) and replay before new cases on the next run.

#[path = "common/fuzzgen.rs"]
mod fuzzgen;

use std::cell::Cell;
use std::time::Instant;

use proptest::prelude::*;
use proptest::test_runner::{Config, FileFailurePersistence, TestCaseError, TestRunner};
use sonolus_backend_core::diff::{DiffConfig, DiffOutcome, diff_with};
use sonolus_backend_core::passes::Pipeline;
use sonolus_backend_core::passes::dce::DcePass;
use sonolus_backend_core::pipeline::{Level, compile_cfg, compile_cfg_with_pipeline};

/// Per-side eval budget (same rationale as tests/fuzz.rs: generated programs
/// terminate by construction; budget-exceeded cases count inconclusive).
const EVAL_BUDGET: u64 = 500_000;

fn fuzz_cases() -> u32 {
    std::env::var("SONOLUS_FUZZ_CASES")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(256)
}

const PERSISTENCE_FILE: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/tests/proptest-regressions/fuzz_dce.txt"
);

fn fuzz_config(cases: u32) -> Config {
    Config {
        cases,
        failure_persistence: Some(Box::new(FileFailurePersistence::Direct(PERSISTENCE_FILE))),
        source_file: Some(file!()),
        ..Config::default()
    }
}

/// The per-pass fuzz differential: minimal vs minimal+[DcePass] with fuzzed
/// memory/RNG seeds. Catches dropped traps, dropped RNG draws, broken
/// threading/merging — anything observable.
#[test]
fn fuzz_differential_minimal_vs_dce_only() {
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
            |c| compile_cfg_with_pipeline(c, &Pipeline::new(vec![Box::new(DcePass)])),
            &config,
        );
        match outcome {
            DiffOutcome::Match => matched.set(matched.get() + 1),
            DiffOutcome::Inconclusive { .. } => inconclusive.set(inconclusive.get() + 1),
            DiffOutcome::Mismatch(m) => {
                return Err(TestCaseError::fail(format!("minimal vs dce-only: {m}")));
            }
        }
        Ok(())
    });
    println!(
        "dce fuzz differential: {cases} cases ({} matched, {} inconclusive) in {:?}",
        matched.get(),
        inconclusive.get(),
        started.elapsed()
    );
    if let Err(e) = result {
        panic!(
            "dce fuzz differential failure (persisted to \
             tests/proptest-regressions/fuzz_dce.txt; re-running replays it first): {e}"
        );
    }
}
