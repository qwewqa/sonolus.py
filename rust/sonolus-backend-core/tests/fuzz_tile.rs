//! Per-transform CFG fuzz for the T3.12 emission-time fused-op tiling:
//!
//! 1. the standard generator (`fuzzgen::program`), `minimal` vs
//!    `minimal`+tile — the transform alone on raw frontend shapes,
//! 2. the **RMW-heavy** profile (`fuzzgen::program_rmw_heavy` — increment
//!    idioms, post-increment pairs, near-miss `SetAdd`/aliasing shapes,
//!    static place offsets), full standard passes *without* tiling vs *with*
//!    tiling — isolating the seam on the trees where the tiles do their real
//!    work (fire rate printed per run), and
//! 3. the same profile with the full W5 seam on the test side (tiling AND
//!    flattening) vs neither — the tile→flatten composition.
//!
//! Default 256 cases (PR CI); scale with `SONOLUS_FUZZ_CASES`, e.g.
//! `SONOLUS_FUZZ_CASES=50000 cargo test --release -p sonolus-backend-core
//! --test fuzz_tile`. Failures persist to
//! `tests/proptest-regressions/fuzz_tile.txt` (`FileFailurePersistence::
//! Direct` — proptest's default resolution is broken in integration tests,
//! see tests/fuzz.rs) and replay first.

#[path = "common/fuzzgen.rs"]
mod fuzzgen;

use std::cell::Cell;
use std::time::Instant;

use proptest::prelude::*;
use proptest::test_runner::{Config, FileFailurePersistence, TestCaseError, TestRunner};
use sonolus_backend_core::diff::{DiffConfig, DiffOutcome, diff_with};
use sonolus_backend_core::passes::{Pipeline, passes_for_level};
use sonolus_backend_core::pipeline::{Level, compile_cfg, compile_cfg_with_pipeline};
use sonolus_backend_core::tile::tile_engine_nodes_stats;

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
    "/tests/proptest-regressions/fuzz_tile.txt"
);

fn fuzz_config(cases: u32) -> Config {
    Config {
        cases,
        failure_persistence: Some(Box::new(FileFailurePersistence::Direct(PERSISTENCE_FILE))),
        source_file: Some(file!()),
        ..Config::default()
    }
}

/// The transform alone: minimal vs minimal+tile on the standard profile
/// (raw frontend shapes — RMW idioms reach emission unoptimized here).
#[test]
fn fuzz_differential_minimal_vs_minimal_plus_tile() {
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
            |c| compile_cfg_with_pipeline(c, &Pipeline::new(vec![]).with_tile(true)),
            &config,
        );
        match outcome {
            DiffOutcome::Match => matched.set(matched.get() + 1),
            DiffOutcome::Inconclusive { .. } => inconclusive.set(inconclusive.get() + 1),
            DiffOutcome::Mismatch(m) => {
                return Err(TestCaseError::fail(format!("minimal vs minimal+tile: {m}")));
            }
        }
        Ok(())
    });
    println!(
        "tile fuzz differential: {cases} cases ({} matched, {} inconclusive) in {:?}",
        matched.get(),
        inconclusive.get(),
        started.elapsed()
    );
    if let Err(e) = result {
        panic!(
            "tile fuzz differential failure (persisted to \
             tests/proptest-regressions/fuzz_tile.txt; re-running replays it first): {e}"
        );
    }
}

/// The seam isolated on optimizer-shaped trees: full standard passes,
/// untiled vs tiled, on the RMW-heavy profile. Prints the tile fire rate
/// (programs with >= 1 fired tile / total) — the profile exists to keep it
/// high.
#[test]
fn fuzz_differential_rmw_heavy_untiled_vs_tiled() {
    let cases = fuzz_cases();
    let mut runner = TestRunner::new(fuzz_config(cases));
    let matched = Cell::new(0u64);
    let inconclusive = Cell::new(0u64);
    let fired_programs = Cell::new(0u64);
    let fired_tiles = Cell::new(0u64);
    let strategy = (fuzzgen::program_rmw_heavy(), any::<u64>(), any::<u64>());
    let started = Instant::now();
    let result = runner.run(&strategy, |(program, memory_seed, rng_seed)| {
        let cfg = fuzzgen::build_cfg(&program);
        // Fire-rate instrumentation on the exact tree the test side tiles.
        if let Ok(untiled) =
            compile_cfg_with_pipeline(&cfg, &Pipeline::new(passes_for_level(Level::Standard)))
        {
            let (_, stats) = tile_engine_nodes_stats(&untiled);
            if stats.total() > 0 {
                fired_programs.set(fired_programs.get() + 1);
                fired_tiles.set(fired_tiles.get() + stats.total());
            }
        }
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
                    &Pipeline::new(passes_for_level(Level::Standard)).with_tile(true),
                )
            },
            &config,
        );
        match outcome {
            DiffOutcome::Match => matched.set(matched.get() + 1),
            DiffOutcome::Inconclusive { .. } => inconclusive.set(inconclusive.get() + 1),
            DiffOutcome::Mismatch(m) => {
                return Err(TestCaseError::fail(format!(
                    "rmw-heavy standard untiled vs tiled: {m}"
                )));
            }
        }
        Ok(())
    });
    println!(
        "tile rmw-heavy fuzz: {cases} cases ({} matched, {} inconclusive; fire rate {}/{cases} \
         programs, {} tiles) in {:?}",
        matched.get(),
        inconclusive.get(),
        fired_programs.get(),
        fired_tiles.get(),
        started.elapsed()
    );
    if let Err(e) = result {
        panic!(
            "tile rmw-heavy fuzz failure (persisted to \
             tests/proptest-regressions/fuzz_tile.txt; re-running replays it first): {e}"
        );
    }
}

/// The tile→flatten composition: full standard passes with the whole W5
/// emission seam (tile + flatten) vs neither, on the RMW-heavy profile.
#[test]
fn fuzz_differential_rmw_heavy_tile_flatten_composition() {
    let cases = fuzz_cases();
    let mut runner = TestRunner::new(fuzz_config(cases));
    let matched = Cell::new(0u64);
    let inconclusive = Cell::new(0u64);
    let strategy = (fuzzgen::program_rmw_heavy(), any::<u64>(), any::<u64>());
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
                    &Pipeline::new(passes_for_level(Level::Standard))
                        .with_tile(true)
                        .with_flatten(true),
                )
            },
            &config,
        );
        match outcome {
            DiffOutcome::Match => matched.set(matched.get() + 1),
            DiffOutcome::Inconclusive { .. } => inconclusive.set(inconclusive.get() + 1),
            DiffOutcome::Mismatch(m) => {
                return Err(TestCaseError::fail(format!(
                    "rmw-heavy standard vs standard+tile+flatten: {m}"
                )));
            }
        }
        Ok(())
    });
    println!(
        "tile+flatten composition fuzz: {cases} cases ({} matched, {} inconclusive) in {:?}",
        matched.get(),
        inconclusive.get(),
        started.elapsed()
    );
    if let Err(e) = result {
        panic!(
            "tile+flatten composition fuzz failure (persisted to \
             tests/proptest-regressions/fuzz_tile.txt; re-running replays it first): {e}"
        );
    }
}
