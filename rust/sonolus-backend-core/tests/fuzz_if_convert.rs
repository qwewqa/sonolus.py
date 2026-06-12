//! Per-pass CFG fuzz for the T3.8 if-conversion pass:
//!
//! 1. the **diamond/triangle-heavy** profile (`fuzzgen::program_diamond_heavy`
//!    — constant-index temp writes in 0..=2-statement diamond arms, so
//!    `Mem2Reg` manufactures exactly the single-phi joins the pass converts;
//!    empty arms produce triangles; RNG/log/lazy-read arm values exercise the
//!    exactness and refusal rules) `minimal` vs
//!    `[Mem2Reg, SCCP, GVN, DCE, IfConvert]` (the value-SSA MIR shape the
//!    registry hands the pass, every `compile_cfg` including the
//!    unconditional `destruct_ssa` that legalizes the lazy arm roots);
//! 2. the same diamond-heavy profile vs the full `standard` level (the
//!    registry now ends with `IfConvert` at W4 — switch formation runs first,
//!    feeding the non-zero-cond `Equal` select path).
//!
//! Default 256 cases (PR CI); scale with `SONOLUS_FUZZ_CASES`, e.g.
//! `SONOLUS_FUZZ_CASES=50000 cargo test --release -p sonolus-backend-core
//! --test fuzz_if_convert`. Failures persist to
//! `tests/proptest-regressions/fuzz_if_convert.txt`
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
use sonolus_backend_core::passes::if_convert::IfConvert;
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
    "/tests/proptest-regressions/fuzz_if_convert.txt"
);

fn fuzz_config(cases: u32) -> Config {
    Config {
        cases,
        failure_persistence: Some(Box::new(FileFailurePersistence::Direct(PERSISTENCE_FILE))),
        source_file: Some(file!()),
        ..Config::default()
    }
}

fn w2_plus_if_convert() -> Pipeline {
    Pipeline::new(vec![
        Box::new(Mem2Reg),
        Box::new(Sccp),
        Box::new(GvnRewritePass),
        Box::new(DcePass),
        Box::new(IfConvert),
    ])
}

/// The per-pass fuzz differential on the diamond-heavy profile.
#[test]
fn fuzz_differential_diamond_heavy_minimal_vs_w2_plus_if_convert() {
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
            |c| compile_cfg(c, Level::Minimal),
            |c| compile_cfg_with_pipeline(c, &w2_plus_if_convert()),
            &config,
        );
        match outcome {
            DiffOutcome::Match => matched.set(matched.get() + 1),
            DiffOutcome::Inconclusive { .. } => inconclusive.set(inconclusive.get() + 1),
            DiffOutcome::Mismatch(m) => {
                return Err(TestCaseError::fail(format!(
                    "minimal vs w2+if-convert: {m}"
                )));
            }
        }
        Ok(())
    });
    println!(
        "if-convert fuzz differential: {cases} cases ({} matched, {} inconclusive) in {:?}",
        matched.get(),
        inconclusive.get(),
        started.elapsed()
    );
    if let Err(e) = result {
        panic!(
            "if-convert fuzz differential failure (persisted to \
             tests/proptest-regressions/fuzz_if_convert.txt; re-running replays it first): {e}"
        );
    }
}

/// The diamond-heavy profile against the full standard registry (switch
/// formation first manufactures the single-case non-zero-cond branches that
/// take the Equal select path; LICM and the W1/W2 passes shape the operands).
#[test]
fn fuzz_differential_diamond_heavy_vs_standard() {
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
            |c| compile_cfg(c, Level::Minimal),
            |c| compile_cfg(c, Level::Standard),
            &config,
        );
        match outcome {
            DiffOutcome::Match => matched.set(matched.get() + 1),
            DiffOutcome::Inconclusive { .. } => inconclusive.set(inconclusive.get() + 1),
            DiffOutcome::Mismatch(m) => {
                return Err(TestCaseError::fail(format!(
                    "diamond-heavy minimal vs standard: {m}"
                )));
            }
        }
        Ok(())
    });
    println!(
        "if-convert diamond-heavy fuzz: {cases} cases ({} matched, {} inconclusive) in {:?}",
        matched.get(),
        inconclusive.get(),
        started.elapsed()
    );
    if let Err(e) = result {
        panic!(
            "if-convert diamond-heavy fuzz failure (persisted to \
             tests/proptest-regressions/fuzz_if_convert.txt; re-running replays it first): {e}"
        );
    }
}
