//! CFG fuzz tests (PORT.md T2.3): proptest-generated well-formed frontend
//! CFGs (`common/fuzzgen.rs`), differentially interpreted minimal-vs-fast and
//! minimal-vs-standard via `sonolus_backend_core::diff`.
//!
//! # Case count
//!
//! Default 256 cases (fast enough for `cargo test` / PR CI). Scale up with
//! the `SONOLUS_FUZZ_CASES` env var — wave gates run ≥ 30 minutes or 1M cases
//! (e.g. `SONOLUS_FUZZ_CASES=1000000 cargo test --release -p
//! sonolus-backend-core --test fuzz`).
//!
//! # Reproducing a failure
//!
//! Failing cases are persisted by proptest to
//! `tests/proptest-regressions/fuzz.txt` (checked in; lines are `cc <seed>`
//! entries). Re-running this test replays every persisted case before
//! generating new ones, so a CI failure reproduces locally by just running
//! the test again on the same tree — commit the updated file with the fix.
//! The panic message also includes the shrunk failing `Program` debug dump.
//!
//! The canary test runs with a fixed RNG seed and persistence disabled: its
//! failures are intentional and must be deterministic, and must never pollute
//! the regression file.

#[path = "common/canary.rs"]
mod canary;
#[path = "common/fuzzgen.rs"]
mod fuzzgen;

use std::cell::Cell;
use std::time::Instant;

use proptest::prelude::*;
use proptest::test_runner::{
    Config, FileFailurePersistence, RngAlgorithm, TestCaseError, TestError, TestRng, TestRunner,
};
use sonolus_backend_core::cfg::cfg_to_text;
use sonolus_backend_core::diff::{
    DiffConfig, DiffOutcome, build_memory, diff_levels, diff_with, run_with_memory,
};
use sonolus_backend_core::pipeline::{Level, compile_cfg};

/// Per-side eval budget. Generated programs are termination-bounded by
/// construction (counter loops only), so this is a backstop, not a tuning
/// knob; budget-exceeded cases are counted inconclusive.
const EVAL_BUDGET: u64 = 500_000;

fn fuzz_cases() -> u32 {
    std::env::var("SONOLUS_FUZZ_CASES")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(256)
}

/// The persistence file, pinned explicitly: proptest's default
/// `SourceParallel` resolution needs a `lib.rs`/`main.rs` ancestor, which
/// integration-test files don't have (it would warn and scatter
/// `tests/fuzz.proptest-regressions` instead).
const PERSISTENCE_FILE: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/tests/proptest-regressions/fuzz.txt"
);

fn fuzz_config(cases: u32) -> Config {
    Config {
        cases,
        failure_persistence: Some(Box::new(FileFailurePersistence::Direct(PERSISTENCE_FILE))),
        source_file: Some(file!()),
        ..Config::default()
    }
}

/// Generator sanity: every generated CFG compiles at every level and runs at
/// minimal without panics (traps are allowed — and counted, so a generator
/// regression that makes everything trap is visible).
#[test]
fn generated_cfgs_compile_at_all_levels_and_run_at_minimal() {
    let cases = fuzz_cases();
    let mut runner = TestRunner::new(fuzz_config(cases));
    let ran_clean = Cell::new(0u64);
    let trapped = Cell::new(0u64);
    let started = Instant::now();
    let result = runner.run(&fuzzgen::program(), |program| {
        let cfg = fuzzgen::build_cfg(&program);
        let mut minimal_nodes = None;
        for level in [Level::Minimal, Level::Fast, Level::Standard] {
            match compile_cfg(&cfg, level) {
                Ok(nodes) => {
                    if level == Level::Minimal {
                        minimal_nodes = Some(nodes);
                    }
                }
                Err(e) => {
                    return Err(TestCaseError::fail(format!(
                        "compile failed at {}: {e}",
                        level.name()
                    )));
                }
            }
        }
        let nodes = minimal_nodes.expect("minimal compiled");
        let memory = build_memory(&cfg, 0xF00D);
        let observation = run_with_memory(&nodes, &memory, 0xF00D, EVAL_BUDGET);
        if observation.result.is_ok() {
            ran_clean.set(ran_clean.get() + 1);
        } else {
            trapped.set(trapped.get() + 1);
        }
        Ok(())
    });
    println!(
        "generator sanity: {cases} cases, {} ran clean, {} trapped, in {:?}",
        ran_clean.get(),
        trapped.get(),
        started.elapsed()
    );
    if let Err(e) = result {
        panic!("generator sanity failure: {e}");
    }
    assert!(
        ran_clean.get() > 0,
        "every generated case trapped — the generator is too trap-happy"
    );
}

/// The fuzz differential: generate CFG -> diff minimal-vs-fast and
/// minimal-vs-standard with fuzzed memory/RNG seeds. Identity today (empty
/// pass registry); the standing miscompile net once wave passes land.
#[test]
fn fuzz_differential_minimal_vs_optimized_levels() {
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
        for level in [Level::Fast, Level::Standard] {
            match diff_levels(&cfg, Level::Minimal, level, &config) {
                DiffOutcome::Match => matched.set(matched.get() + 1),
                DiffOutcome::Inconclusive { .. } => inconclusive.set(inconclusive.get() + 1),
                DiffOutcome::Mismatch(m) => {
                    return Err(TestCaseError::fail(format!(
                        "minimal vs {}: {m}",
                        level.name()
                    )));
                }
            }
        }
        Ok(())
    });
    println!(
        "fuzz differential: {cases} cases ({} comparisons matched, {} inconclusive) in {:?}",
        matched.get(),
        inconclusive.get(),
        started.elapsed()
    );
    if let Err(e) = result {
        panic!(
            "fuzz differential failure (persisted to tests/proptest-regressions/fuzz.txt; \
             re-running this test replays it first): {e}"
        );
    }
}

/// Fixed RNG seed for the canary run: the catch and the shrink result must be
/// deterministic so the assertions below cannot flake.
const CANARY_RNG_SEED: [u8; 32] = [7; 32];

/// `DoD`: the FUZZER catches the deliberately broken transform
/// (`Add(x, c) -> x` for nonzero const `c`, `common/canary.rs`) and proptest
/// SHRINKS the failing
/// case to a small counterexample.
#[test]
fn fuzzer_catches_and_shrinks_canary_miscompile() {
    let config = Config {
        cases: 4096, // stops at the first failure; the canary is hit long before this
        // The default shrink budget is cases * 4; give shrinking plenty of
        // room so the minimized case is a true local minimum.
        max_shrink_iters: 65_536,
        failure_persistence: None,
        source_file: Some(file!()),
        ..Config::default()
    };
    let mut runner = TestRunner::new_with_rng(
        config,
        TestRng::from_seed(RngAlgorithm::ChaCha, &CANARY_RNG_SEED),
    );
    let diff_config = DiffConfig {
        memory_seed: 0xCAFE,
        rng_seed: 0xBEEF,
        eval_budget: 200_000,
    };
    let started = Instant::now();
    // Loop-free programs: the canary kills counter increments, so loop-bearing
    // failures shrink poorly (whole-loop scaffolding is load-bearing) or turn
    // budget-inconclusive; see `fuzzgen::program_loop_free`.
    let result = runner.run(&fuzzgen::program_loop_free(), |program| {
        let cfg = fuzzgen::build_cfg(&program);
        match diff_with(
            &cfg,
            |c| compile_cfg(c, Level::Minimal),
            canary::compile_with_canary,
            &diff_config,
        ) {
            DiffOutcome::Mismatch(m) => Err(TestCaseError::fail(format!("{m}"))),
            _ => Ok(()),
        }
    });
    let Err(err) = result else {
        panic!("the fuzzer failed to catch the canary miscompile within the case budget");
    };
    let TestError::Fail(reason, shrunk) = err else {
        panic!("expected a shrunk failure, got: {err:?}");
    };
    let cfg = fuzzgen::build_cfg(&shrunk);
    println!("canary caught in {:?}: {reason}", started.elapsed());
    println!("shrunk program: {shrunk:?}");
    println!(
        "shrunk CFG ({} blocks, {} nodes):\n{}",
        cfg.blocks.len(),
        cfg.nodes.len(),
        cfg_to_text(&cfg)
    );
    // Shrinking must actually minimize: a handful of nodes in a handful of
    // blocks (an unshrunk failing case is typically tens of times larger).
    // Note the loop scaffolding (entry + header + body + after = 4 blocks)
    // can be load-bearing: the canary breaks `c <- Add(c, 1)` counter
    // increments, so a failure mode caught through a broken loop cannot
    // shrink below one loop. Bounds are deliberately loose so strategy
    // tweaks don't break this test.
    assert!(
        cfg.blocks.len() <= 5,
        "shrunk case still has {} blocks — shrinking is not working",
        cfg.blocks.len()
    );
    assert!(
        cfg.nodes.len() <= 32,
        "shrunk case still has {} nodes — shrinking is not working",
        cfg.nodes.len()
    );
}
