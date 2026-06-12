//! Per-transform verification for SCCP (PORT.md T3.1, invariant §3.7):
//! corpus differential, fuzz differential, and effectiveness, all comparing
//! `minimal` against an explicit `minimal + [SCCP]` pipeline via
//! `diff_with`/`compile_cfg_with_pipeline` (the T2.3 injection point).
//!
//! The fuzz case count scales with `SONOLUS_FUZZ_CASES` (default 256), e.g.
//! `SONOLUS_FUZZ_CASES=20000 cargo test --release -p sonolus-backend-core
//! --test sccp`. Failing fuzz cases persist to
//! `tests/proptest-regressions/sccp.txt` and replay on the next run.

#[path = "common/fuzzgen.rs"]
mod fuzzgen;

mod common;

use std::cell::Cell;
use std::fs;
use std::time::Instant;

use proptest::prelude::*;
use proptest::test_runner::{Config, FileFailurePersistence, TestCaseError, TestRunner};
use sonolus_backend_core::cfg::Cfg;
use sonolus_backend_core::decode::decode_cfg;
use sonolus_backend_core::diff::{
    DiffConfig, DiffOutcome, DiffSummary, build_memory, diff_with, run_with_memory,
};
use sonolus_backend_core::passes::Pipeline;
use sonolus_backend_core::passes::sccp::Sccp;
use sonolus_backend_core::pipeline::{
    Level, compile_cfg, compile_cfg_with_pipeline, compile_cfg_with_pipeline_stats,
};

/// Same seeds/budget as the standing corpus differential (differential.rs).
const MEMORY_SEEDS: [u64; 2] = [0x5EED_0001, 0x5EED_0002];
const EVAL_BUDGET: u64 = 200_000;

fn sccp_pipeline() -> Pipeline {
    Pipeline::new(vec![Box::new(Sccp)])
}

fn corpus_cfgs() -> Vec<(String, Cfg)> {
    let dir = common::testdata_dir();
    let manifest = common::load_manifest();
    manifest
        .entries
        .iter()
        .map(|entry| {
            let bytes = fs::read(dir.join("cfgs").join(format!("{}.scfg", entry.hash)))
                .unwrap_or_else(|e| panic!("missing frontend CFG {}: {e}", entry.hash));
            let cfg = decode_cfg(&bytes)
                .unwrap_or_else(|e| panic!("{}: frontend CFG failed to decode: {e}", entry.hash));
            (entry.hash.clone(), cfg)
        })
        .collect()
}

/// Per-transform corpus differential: full corpus x two memory seeds,
/// minimal vs minimal+[SCCP]. Must be mismatch-free, forever.
#[test]
fn corpus_differential_minimal_vs_sccp() {
    let cfgs = corpus_cfgs();
    let entry_count = cfgs.len();
    let mut summary = DiffSummary::default();
    for (hash, cfg) in &cfgs {
        for (i, &seed) in MEMORY_SEEDS.iter().enumerate() {
            let config = DiffConfig {
                memory_seed: seed,
                rng_seed: seed.rotate_left(17) ^ 0xD1FF,
                eval_budget: EVAL_BUDGET,
            };
            let outcome = diff_with(
                cfg,
                |c| compile_cfg(c, Level::Minimal),
                |c| compile_cfg_with_pipeline(c, &sccp_pipeline()),
                &config,
            );
            summary.record(format!("{} mem-seed#{i}", &hash[..12]), &outcome);
        }
    }
    println!("sccp corpus differential: {}", summary.report());
    assert_eq!(summary.cases, entry_count * MEMORY_SEEDS.len());
    assert!(entry_count > 0, "the corpus must not be empty");
    summary.assert_no_mismatches();
}

fn fuzz_cases() -> u32 {
    std::env::var("SONOLUS_FUZZ_CASES")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(256)
}

/// Pinned persistence path (proptest's default resolution is broken in
/// integration tests; see tests/fuzz.rs).
const PERSISTENCE_FILE: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/tests/proptest-regressions/sccp.txt"
);

/// Per-transform fuzz differential: generated CFGs, fuzzed memory/RNG seeds,
/// minimal vs minimal+[SCCP].
#[test]
fn fuzz_differential_minimal_vs_sccp() {
    let cases = fuzz_cases();
    let config = Config {
        cases,
        failure_persistence: Some(Box::new(FileFailurePersistence::Direct(PERSISTENCE_FILE))),
        source_file: Some(file!()),
        ..Config::default()
    };
    let mut runner = TestRunner::new(config);
    let matched = Cell::new(0u64);
    let inconclusive = Cell::new(0u64);
    let fired = Cell::new(0u64);
    let strategy = (fuzzgen::program(), any::<u64>(), any::<u64>());
    let started = Instant::now();
    let result = runner.run(&strategy, |(program, memory_seed, rng_seed)| {
        let cfg = fuzzgen::build_cfg(&program);
        // Effectiveness signal: how often the pass actually mutates the MIR.
        if let Ok(mut mir) = sonolus_backend_core::mir::build_mir(&cfg)
            && sccp_pipeline().run(
                &mut mir,
                &mut sonolus_backend_core::analysis::Analyses::new(),
            )
        {
            fired.set(fired.get() + 1);
        }
        let config = DiffConfig {
            memory_seed,
            rng_seed,
            eval_budget: EVAL_BUDGET,
        };
        match diff_with(
            &cfg,
            |c| compile_cfg(c, Level::Minimal),
            |c| compile_cfg_with_pipeline(c, &sccp_pipeline()),
            &config,
        ) {
            DiffOutcome::Match => matched.set(matched.get() + 1),
            DiffOutcome::Inconclusive { .. } => inconclusive.set(inconclusive.get() + 1),
            DiffOutcome::Mismatch(m) => {
                return Err(TestCaseError::fail(format!("minimal vs sccp: {m}")));
            }
        }
        Ok(())
    });
    println!(
        "sccp fuzz differential: {cases} cases ({} matched, {} inconclusive, pass fired on {}) \
         in {:?}",
        matched.get(),
        inconclusive.get(),
        fired.get(),
        started.elapsed()
    );
    if let Err(e) = result {
        panic!(
            "sccp fuzz differential failure (persisted to \
             tests/proptest-regressions/sccp.txt; re-running replays it first): {e}"
        );
    }
}

/// Effectiveness: SCCP must actually fire on the corpus — aggregate static
/// node count strictly drops, aggregate dynamic eval count strictly drops
/// (and never rises on any case), and at least some entries change.
///
/// Context for the (small) corpus numbers: these are *frontend* CFGs, where
/// the Python tracer already folds constant expressions at trace time, and
/// W1 SCCP runs before `Mem2Reg` (W2, T3.4) so constants stored to temps are
/// invisible (`Load` = Bottom). The legacy SCCP ran *after* legacy SSA
/// promotion, which is where most of its corpus wins came from.
#[test]
fn corpus_effectiveness_static_nodes_and_evals_drop() {
    let cfgs = corpus_cfgs();
    let mut minimal_pipeline_nodes = 0u64;
    let mut sccp_pipeline_nodes = 0u64;
    let mut minimal_evals = 0u64;
    let mut sccp_evals = 0u64;
    let mut entries_changed = 0usize;
    let mut pass_fired = 0usize;
    let mut compared_runs = 0usize;
    let empty = Pipeline::new(vec![]);
    for (hash, cfg) in &cfgs {
        // How often the pass itself reports a mutation (a more sensitive
        // "fires" signal than final node-count movement).
        let mut mir = sonolus_backend_core::mir::build_mir(cfg)
            .unwrap_or_else(|e| panic!("{hash}: build_mir failed: {e}"));
        if sccp_pipeline().run(
            &mut mir,
            &mut sonolus_backend_core::analysis::Analyses::new(),
        ) {
            pass_fired += 1;
        }
        let (minimal_nodes, minimal_stats) = compile_cfg_with_pipeline_stats(cfg, &empty)
            .unwrap_or_else(|e| panic!("{hash}: minimal compile failed: {e}"));
        let (sccp_nodes, sccp_stats) = compile_cfg_with_pipeline_stats(cfg, &sccp_pipeline())
            .unwrap_or_else(|e| panic!("{hash}: sccp compile failed: {e}"));
        minimal_pipeline_nodes += u64::from(minimal_stats.node_count);
        sccp_pipeline_nodes += u64::from(sccp_stats.node_count);
        if sccp_stats.node_count != minimal_stats.node_count {
            entries_changed += 1;
        }
        // Dynamic evals: compare only runs that complete identically and
        // within budget on both sides (the differential test above is the
        // correctness net; this is the quality metric).
        let memory = build_memory(cfg, MEMORY_SEEDS[0]);
        let base = run_with_memory(&minimal_nodes, &memory, MEMORY_SEEDS[0], EVAL_BUDGET);
        let test = run_with_memory(&sccp_nodes, &memory, MEMORY_SEEDS[0], EVAL_BUDGET);
        if base.budget_exceeded || test.budget_exceeded {
            continue;
        }
        compared_runs += 1;
        minimal_evals += base.eval_count;
        sccp_evals += test.eval_count;
        assert!(
            test.eval_count <= base.eval_count,
            "{hash}: SCCP increased eval count {} -> {}",
            base.eval_count,
            test.eval_count
        );
    }
    println!(
        "sccp effectiveness over {} corpus entries (pass fired on {}, {} with node-count \
         change, {} runs eval-compared):\n  static nodes: {minimal_pipeline_nodes} -> \
         {sccp_pipeline_nodes} ({:+.2}%)\n  dyn evals:    {minimal_evals} -> {sccp_evals} \
         ({:+.2}%)",
        cfgs.len(),
        pass_fired,
        entries_changed,
        compared_runs,
        pct(minimal_pipeline_nodes, sccp_pipeline_nodes),
        pct(minimal_evals, sccp_evals),
    );
    assert!(entries_changed > 0, "SCCP never fired on the corpus");
    assert!(
        sccp_pipeline_nodes < minimal_pipeline_nodes,
        "aggregate static nodes must drop: {minimal_pipeline_nodes} -> {sccp_pipeline_nodes}"
    );
    assert!(
        sccp_evals < minimal_evals,
        "aggregate eval count must drop: {minimal_evals} -> {sccp_evals}"
    );
}

#[allow(clippy::cast_precision_loss)]
fn pct(before: u64, after: u64) -> f64 {
    if before == 0 {
        return 0.0;
    }
    (after as f64 - before as f64) / before as f64 * 100.0
}
