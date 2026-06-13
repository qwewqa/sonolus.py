//! Per-pass CFG fuzz for the T3.11 switch-normalization pass, two nets over
//! the **affine-progression profile** (`fuzzgen::program_affine_heavy` —
//! strided/offset case sets the general profiles never produce, plus refusal
//! coverage: float `+0.5` conds, perturbed non-affine sets, base-0 strided
//! sets, below-threshold sizes):
//!
//! 1. `minimal` vs `minimal`+\[`NormalizeSwitch`\] standalone — pre-formed
//!    multi-way `Switch` shapes are normalized with no other pass involved;
//! 2. `minimal` vs the full `standard` level — the composition surface
//!    (if/elif chains merged by W3 switch formation, shaped by W4, then
//!    normalized by the W5 registry entry).
//!
//! Both nets also count the **fire rate** (programs where the pass actually
//! mutates the MIR, measured by running it directly after the relevant
//! prefix) and assert it is nonzero — a fuzz net that never exercises the
//! transform is vacuous.
//!
//! Default 256 cases (PR CI); scale with `SONOLUS_FUZZ_CASES`, e.g.
//! `SONOLUS_FUZZ_CASES=50000 cargo test --release -p sonolus-backend-core
//! --test fuzz_normalize_switch`. Failures persist to
//! `tests/proptest-regressions/fuzz_normalize_switch.txt`
//! (`FileFailurePersistence::Direct` — proptest's default resolution is
//! broken in integration tests, see tests/fuzz.rs) and replay first.

#[path = "common/fuzzgen.rs"]
mod fuzzgen;

use std::cell::Cell;
use std::time::Instant;

use proptest::prelude::*;
use proptest::test_runner::{Config, FileFailurePersistence, TestCaseError, TestRunner};
use sonolus_backend_core::analysis::Analyses;
use sonolus_backend_core::diff::{DiffConfig, DiffOutcome, diff_with};
use sonolus_backend_core::mir::build_mir;
use sonolus_backend_core::passes::normalize_switch::NormalizeSwitch;
use sonolus_backend_core::passes::{Pass, Pipeline, passes_for_level};
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
    "/tests/proptest-regressions/fuzz_normalize_switch.txt"
);

fn fuzz_config(cases: u32) -> Config {
    Config {
        cases,
        failure_persistence: Some(Box::new(FileFailurePersistence::Direct(PERSISTENCE_FILE))),
        source_file: Some(file!()),
        ..Config::default()
    }
}

/// The registry prefix before the W5 entry.
fn w4_prefix() -> Vec<Box<dyn Pass>> {
    let mut prefix = passes_for_level(Level::Standard);
    let pos = prefix
        .iter()
        .position(|p| p.name() == "normalize-switch")
        .expect("normalize-switch is registered at standard");
    prefix.truncate(pos);
    prefix
}

/// Whether the pass fires on this CFG after running `prefix`.
fn pass_fires(cfg: &sonolus_backend_core::cfg::Cfg, prefix: Vec<Box<dyn Pass>>) -> bool {
    let Ok(mut mir) = build_mir(cfg) else {
        return false;
    };
    let mut analyses = Analyses::new();
    Pipeline::new(prefix).run(&mut mir, &mut analyses);
    NormalizeSwitch.run(&mut mir, &mut analyses)
}

/// Net 1: the standalone per-pass fuzz differential on the affine profile.
#[test]
fn fuzz_differential_minimal_vs_normalize_only() {
    let cases = fuzz_cases();
    let mut runner = TestRunner::new(fuzz_config(cases));
    let matched = Cell::new(0u64);
    let inconclusive = Cell::new(0u64);
    let fired = Cell::new(0u64);
    let strategy = (fuzzgen::program_affine_heavy(), any::<u64>(), any::<u64>());
    let started = Instant::now();
    let result = runner.run(&strategy, |(program, memory_seed, rng_seed)| {
        let cfg = fuzzgen::build_cfg(&program);
        if pass_fires(&cfg, Vec::new()) {
            fired.set(fired.get() + 1);
        }
        let config = DiffConfig {
            memory_seed,
            rng_seed,
            eval_budget: EVAL_BUDGET,
        };
        let outcome = diff_with(
            &cfg,
            |c| compile_cfg(c, Level::Minimal),
            |c| compile_cfg_with_pipeline(c, &Pipeline::new(vec![Box::new(NormalizeSwitch)])),
            &config,
        );
        match outcome {
            DiffOutcome::Match => matched.set(matched.get() + 1),
            DiffOutcome::Inconclusive { .. } => inconclusive.set(inconclusive.get() + 1),
            DiffOutcome::Mismatch(m) => {
                return Err(TestCaseError::fail(format!(
                    "minimal vs normalize-switch-only: {m}"
                )));
            }
        }
        Ok(())
    });
    println!(
        "normalize-switch fuzz differential: {cases} cases ({} matched, {} inconclusive, \
         fire rate {}/{cases}) in {:?}",
        matched.get(),
        inconclusive.get(),
        fired.get(),
        started.elapsed()
    );
    assert!(
        fired.get() > 0,
        "the affine profile must make the pass fire standalone"
    );
    if let Err(e) = result {
        panic!(
            "normalize-switch fuzz differential failure (persisted to \
             tests/proptest-regressions/fuzz_normalize_switch.txt; re-running replays it \
             first): {e}"
        );
    }
}

/// Net 2: minimal vs the full standard pipeline on the affine profile (the
/// switch-form/shape/if-convert/normalize composition).
#[test]
fn fuzz_differential_minimal_vs_standard() {
    let cases = fuzz_cases();
    let mut runner = TestRunner::new(fuzz_config(cases));
    let matched = Cell::new(0u64);
    let inconclusive = Cell::new(0u64);
    let fired = Cell::new(0u64);
    let strategy = (fuzzgen::program_affine_heavy(), any::<u64>(), any::<u64>());
    let started = Instant::now();
    let result = runner.run(&strategy, |(program, memory_seed, rng_seed)| {
        let cfg = fuzzgen::build_cfg(&program);
        if pass_fires(&cfg, w4_prefix()) {
            fired.set(fired.get() + 1);
        }
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
                    "affine-heavy minimal vs standard: {m}"
                )));
            }
        }
        Ok(())
    });
    println!(
        "normalize-switch composition fuzz: {cases} cases ({} matched, {} inconclusive, \
         post-W4 fire rate {}/{cases}) in {:?}",
        matched.get(),
        inconclusive.get(),
        fired.get(),
        started.elapsed()
    );
    assert!(
        fired.get() > 0,
        "the affine profile must make the pass fire after the W4 prefix"
    );
    if let Err(e) = result {
        panic!(
            "normalize-switch composition fuzz failure (persisted to \
             tests/proptest-regressions/fuzz_normalize_switch.txt; re-running replays it \
             first): {e}"
        );
    }
}
