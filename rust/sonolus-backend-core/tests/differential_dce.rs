//! Per-transform differential tests for the T3.3 DCE pass (PORT.md invariant
//! §3.7): every frontend CFG in the mini-corpus, compiled `minimal` vs
//! `minimal`+\[DcePass\] only (the pass standalone, without SCCP/GVN), run on
//! randomized memory with seeded RNG — zero mismatches allowed. Stays in the
//! suite permanently as the pass's standing safety net.
//!
//! Also asserts effectiveness: the pass must actually fire on the corpus
//! (aggregate static node count strictly drops, with per-entry movement
//! reported).

mod common;

use std::fs;

use sonolus_backend_core::cfg::Cfg;
use sonolus_backend_core::decode::decode_cfg;
use sonolus_backend_core::diff::{DiffConfig, DiffSummary, diff_with};
use sonolus_backend_core::passes::Pipeline;
use sonolus_backend_core::passes::dce::DcePass;
use sonolus_backend_core::pipeline::{
    Level, compile_cfg, compile_cfg_stats, compile_cfg_with_pipeline,
    compile_cfg_with_pipeline_stats,
};

/// Two memory randomizations per CFG (same scheme as tests/differential.rs).
const MEMORY_SEEDS: [u64; 2] = [0x5EED_0001, 0x5EED_0002];
const EVAL_BUDGET: u64 = 200_000;

fn dce_pipeline() -> Pipeline {
    Pipeline::new(vec![Box::new(DcePass)])
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

#[test]
fn corpus_differential_minimal_vs_dce_only() {
    let cfgs = corpus_cfgs();
    let entry_count = cfgs.len();
    assert!(entry_count > 0, "the corpus must not be empty");
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
                |c| compile_cfg_with_pipeline(c, &dce_pipeline()),
                &config,
            );
            summary.record(format!("{} mem-seed#{i}", &hash[..12]), &outcome);
        }
    }
    println!("corpus differential minimal-vs-dce: {}", summary.report());
    assert_eq!(summary.cases, entry_count * MEMORY_SEEDS.len());
    summary.assert_no_mismatches();
}

#[test]
fn corpus_effectiveness_static_nodes_drop() {
    let cfgs = corpus_cfgs();
    let mut minimal_total: u64 = 0;
    let mut dce_total: u64 = 0;
    let mut entries_changed = 0usize;
    for (hash, cfg) in &cfgs {
        let (_, minimal_stats) = compile_cfg_stats(cfg, Level::Minimal)
            .unwrap_or_else(|e| panic!("{hash}: minimal compile failed: {e}"));
        let (_, dce_stats) = compile_cfg_with_pipeline_stats(cfg, &dce_pipeline())
            .unwrap_or_else(|e| panic!("{hash}: dce compile failed: {e}"));
        minimal_total += u64::from(minimal_stats.node_count);
        dce_total += u64::from(dce_stats.node_count);
        assert!(
            dce_stats.node_count <= minimal_stats.node_count,
            "{hash}: DCE must never grow the node tree ({} -> {})",
            minimal_stats.node_count,
            dce_stats.node_count
        );
        if dce_stats.node_count < minimal_stats.node_count {
            entries_changed += 1;
        }
    }
    println!(
        "corpus effectiveness (dce standalone): static nodes {minimal_total} -> {dce_total} \
         ({entries_changed}/{} entries improved)",
        cfgs.len()
    );
    assert!(
        dce_total < minimal_total,
        "the pass must fire on the corpus: {minimal_total} -> {dce_total}"
    );
    assert!(entries_changed > 0);
}
