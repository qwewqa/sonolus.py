//! Per-transform differential tests for the T3.12 emission-time fused-op
//! tiling (PORT.md invariant §3.7): every frontend CFG in the mini-corpus,
//! compared across the tiling seam:
//!
//! 1. `minimal` vs `minimal`+tile — the transform alone on raw frontend
//!    shapes (unoptimized RMW idioms, place-offset `Add`s),
//! 2. full standard passes *without* tiling vs *with* tiling — the transform
//!    isolated at its realistic seam (post-W4 trees with GVN const-first
//!    `Add`s and slot RMWs from out-of-SSA), base and test sharing every
//!    mid-level pass, and
//! 3. standard passes + flatten vs standard passes + tile + flatten — the
//!    behavioral equality of standard-without-tiling and standard-with-tiling
//!    at the full W5 emission seam (tile feeds flatten in the pipeline).
//!
//! `minimal` vs full `standard` (which now tiles) is covered by
//! tests/differential.rs. Zero mismatches allowed, two memory seeds each.
//! Stays in the suite permanently.

mod common;

use std::fs;

use sonolus_backend_core::cfg::Cfg;
use sonolus_backend_core::decode::decode_cfg;
use sonolus_backend_core::diff::{DiffConfig, DiffSummary, diff_with};
use sonolus_backend_core::passes::{Pipeline, passes_for_level};
use sonolus_backend_core::pipeline::{Level, compile_cfg, compile_cfg_with_pipeline};

/// Two memory randomizations per CFG (same scheme as tests/differential.rs).
const MEMORY_SEEDS: [u64; 2] = [0x5EED_0001, 0x5EED_0002];
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

fn run_corpus_diff<B, T>(label: &str, base: B, test: T)
where
    B: Fn(
        &Cfg,
    ) -> Result<
        sonolus_backend_core::nodes::EngineNodes,
        sonolus_backend_core::pipeline::CompileError,
    >,
    T: Fn(
        &Cfg,
    ) -> Result<
        sonolus_backend_core::nodes::EngineNodes,
        sonolus_backend_core::pipeline::CompileError,
    >,
{
    let cfgs = corpus_cfgs();
    let entry_count = cfgs.len();
    assert!(entry_count > 0, "the corpus must not be empty");
    let mut summary = DiffSummary::default();
    for (hash, cfg) in &cfgs {
        for (i, &seed) in MEMORY_SEEDS.iter().enumerate() {
            let config = DiffConfig {
                memory_seed: seed,
                rng_seed: seed.rotate_left(17) ^ 0xF1A7,
                eval_budget: EVAL_BUDGET,
            };
            let outcome = diff_with(cfg, &base, &test, &config);
            summary.record(format!("{} mem-seed#{i}", &hash[..12]), &outcome);
        }
    }
    println!("corpus differential {label}: {}", summary.report());
    assert_eq!(summary.cases, entry_count * MEMORY_SEEDS.len());
    summary.assert_no_mismatches();
}

#[test]
fn corpus_differential_minimal_vs_minimal_plus_tile() {
    run_corpus_diff(
        "minimal vs minimal+tile",
        |c| compile_cfg(c, Level::Minimal),
        |c| compile_cfg_with_pipeline(c, &Pipeline::new(vec![]).with_tile(true)),
    );
}

#[test]
fn corpus_differential_standard_untiled_vs_tiled() {
    run_corpus_diff(
        "standard-passes vs standard-passes+tile",
        |c| compile_cfg_with_pipeline(c, &Pipeline::new(passes_for_level(Level::Standard))),
        |c| {
            compile_cfg_with_pipeline(
                c,
                &Pipeline::new(passes_for_level(Level::Standard)).with_tile(true),
            )
        },
    );
}

#[test]
fn corpus_differential_standard_flattened_untiled_vs_tiled() {
    run_corpus_diff(
        "standard-passes+flatten vs standard-passes+tile+flatten",
        |c| {
            compile_cfg_with_pipeline(
                c,
                &Pipeline::new(passes_for_level(Level::Standard)).with_flatten(true),
            )
        },
        |c| {
            compile_cfg_with_pipeline(
                c,
                &Pipeline::new(passes_for_level(Level::Standard))
                    .with_tile(true)
                    .with_flatten(true),
            )
        },
    );
}
