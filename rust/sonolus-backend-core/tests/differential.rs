//! Corpus differential-interpretation tests (PORT.md T2.3).
//!
//! Runs the `crate::diff` harness over every frontend CFG in the mini-corpus
//! (`rust/testdata/`), comparing `minimal` against `fast` and `standard` with
//! randomized memory and seeded RNG. With the optimization registry empty the
//! levels are identical, so these pass trivially today; once wave passes land
//! they become the real per-wave safety net.
//!
//! Also proves the harness has teeth: injecting the broken-transform canary
//! (`common/canary.rs`) into the compared side must produce mismatches.

#[path = "common/canary.rs"]
mod canary;
mod common;

use std::fs;

use sonolus_backend_core::cfg::Cfg;
use sonolus_backend_core::decode::decode_cfg;
use sonolus_backend_core::diff::{DiffConfig, DiffSummary, diff_levels, diff_with};
use sonolus_backend_core::pipeline::{Level, compile_cfg};

/// Two memory randomizations per CFG (different fills hit different paths).
const MEMORY_SEEDS: [u64; 2] = [0x5EED_0001, 0x5EED_0002];
/// Generous for real callbacks; random memory can send corpus loops on long
/// walks, and budget-exceeded cases are counted inconclusive, not failed.
const EVAL_BUDGET: u64 = 200_000;

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

fn run_corpus_diff(test_level: Level) {
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
            let outcome = diff_levels(cfg, Level::Minimal, test_level, &config);
            summary.record(format!("{} mem-seed#{i}", &hash[..12]), &outcome);
        }
    }
    println!(
        "corpus differential minimal-vs-{}: {}",
        test_level.name(),
        summary.report()
    );
    assert_eq!(summary.cases, entry_count * MEMORY_SEEDS.len());
    assert!(entry_count > 0, "the corpus must not be empty");
    summary.assert_no_mismatches();
}

#[test]
fn corpus_differential_minimal_vs_fast() {
    run_corpus_diff(Level::Fast);
}

#[test]
fn corpus_differential_minimal_vs_standard() {
    run_corpus_diff(Level::Standard);
}

/// `DoD`: the harness must CATCH a deliberately miscompiling pass when it is
/// injected into the compared side, over the same corpus + config the clean
/// runs use. (Add(x, c) -> x turns some loops infinite; those runs become
/// inconclusive by design, so the catch must come from straight-line
/// constant addends — assert it does.)
#[test]
fn corpus_differential_catches_canary_miscompile() {
    let cfgs = corpus_cfgs();
    let mut summary = DiffSummary::default();
    for (hash, cfg) in &cfgs {
        let config = DiffConfig {
            memory_seed: MEMORY_SEEDS[0],
            rng_seed: MEMORY_SEEDS[0].rotate_left(17) ^ 0xD1FF,
            eval_budget: EVAL_BUDGET,
        };
        let outcome = diff_with(
            cfg,
            |c| compile_cfg(c, Level::Minimal),
            canary::compile_with_canary,
            &config,
        );
        summary.record(hash[..12].to_string(), &outcome);
    }
    println!(
        "canary corpus differential: {} cases, {} matched, {} inconclusive, {} mismatches",
        summary.cases,
        summary.matched,
        summary.inconclusive,
        summary.mismatches.len()
    );
    if let Some((label, mismatch)) = summary.mismatches.first() {
        println!("first canary catch: {label}: {mismatch}");
    }
    assert!(
        !summary.mismatches.is_empty(),
        "the corpus differential harness failed to catch the canary miscompile \
         (Add(x, c) -> x) anywhere in {} cases",
        summary.cases
    );
}
