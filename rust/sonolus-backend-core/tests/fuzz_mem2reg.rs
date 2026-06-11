//! Per-pass CFG fuzz for the T3.4 Mem2Reg pass — the ledger's **top-risk
//! transform**, so it gets two nets:
//!
//! 1. the standard generator (`fuzzgen::program`), `minimal` vs
//!    `minimal`+\[Mem2Reg\] standalone (every `compile_cfg` includes the
//!    unconditional `destruct_ssa`, so this also exercises the W2
//!    legalization);
//! 2. a **dynamic-indexing-heavy** profile (`fuzzgen::program_dynamic_heavy`)
//!    that hammers the promotion/escape boundary — mixed const/dynamic
//!    accesses to the same temp pool, lazy (`And`/`Or`) temp reads, loops —
//!    checked both against \[Mem2Reg\] standalone and the full `standard`
//!    level (the [W1, mem2reg, W1-rerun] registry).
//!
//! Default 256 cases (PR CI); scale with `SONOLUS_FUZZ_CASES`, e.g.
//! `SONOLUS_FUZZ_CASES=50000 cargo test --release -p sonolus-backend-core
//! --test fuzz_mem2reg`. Failures persist to
//! `tests/proptest-regressions/fuzz_mem2reg.txt`
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
use sonolus_backend_core::passes::mem2reg::Mem2Reg;
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
    "/tests/proptest-regressions/fuzz_mem2reg.txt"
);

fn fuzz_config(cases: u32) -> Config {
    Config {
        cases,
        failure_persistence: Some(Box::new(FileFailurePersistence::Direct(PERSISTENCE_FILE))),
        source_file: Some(file!()),
        ..Config::default()
    }
}

/// The per-pass fuzz differential on the standard generator profile.
#[test]
fn fuzz_differential_minimal_vs_mem2reg_only() {
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
            |c| compile_cfg_with_pipeline(c, &Pipeline::new(vec![Box::new(Mem2Reg)])),
            &config,
        );
        match outcome {
            DiffOutcome::Match => matched.set(matched.get() + 1),
            DiffOutcome::Inconclusive { .. } => inconclusive.set(inconclusive.get() + 1),
            DiffOutcome::Mismatch(m) => {
                return Err(TestCaseError::fail(format!("minimal vs mem2reg-only: {m}")));
            }
        }
        Ok(())
    });
    println!(
        "mem2reg fuzz differential: {cases} cases ({} matched, {} inconclusive) in {:?}",
        matched.get(),
        inconclusive.get(),
        started.elapsed()
    );
    if let Err(e) = result {
        panic!(
            "mem2reg fuzz differential failure (persisted to \
             tests/proptest-regressions/fuzz_mem2reg.txt; re-running replays it first): {e}"
        );
    }
}

/// The dedicated dynamic-indexing-heavy net: minimal vs mem2reg-only AND
/// minimal vs the full standard pipeline, on the profile that stresses the
/// promotion/escape boundary hardest.
#[test]
fn fuzz_differential_dynamic_heavy() {
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
        let sides: [(&str, Box<dyn Fn(&_) -> _>); 2] = [
            (
                "mem2reg-only",
                Box::new(|c: &sonolus_backend_core::cfg::Cfg| {
                    compile_cfg_with_pipeline(c, &Pipeline::new(vec![Box::new(Mem2Reg)]))
                }),
            ),
            (
                "standard",
                Box::new(|c: &sonolus_backend_core::cfg::Cfg| compile_cfg(c, Level::Standard)),
            ),
        ];
        for (label, compile_test) in &sides {
            let outcome = diff_with(
                &cfg,
                |c| compile_cfg(c, Level::Minimal),
                compile_test,
                &config,
            );
            match outcome {
                DiffOutcome::Match => matched.set(matched.get() + 1),
                DiffOutcome::Inconclusive { .. } => inconclusive.set(inconclusive.get() + 1),
                DiffOutcome::Mismatch(m) => {
                    return Err(TestCaseError::fail(format!(
                        "dynamic-heavy minimal vs {label}: {m}"
                    )));
                }
            }
        }
        Ok(())
    });
    println!(
        "mem2reg dynamic-heavy fuzz: {cases} cases ({} comparisons matched, {} inconclusive) in {:?}",
        matched.get(),
        inconclusive.get(),
        started.elapsed()
    );
    if let Err(e) = result {
        panic!(
            "mem2reg dynamic-heavy fuzz failure (persisted to \
             tests/proptest-regressions/fuzz_mem2reg.txt; re-running replays it first): {e}"
        );
    }
}
