//! Per-transform differential tests for the T3.7 LICM pass (PORT.md invariant
//! §3.7): every frontend CFG in the mini-corpus, compiled `minimal` vs
//!
//! 1. `minimal`+\[LICM\] standalone (near-inert on unpromoted memory-form MIR
//!    — operands are in-loop loads — but pins the preheader surgery wherever
//!    it does fire),
//! 2. the W2 block + LICM (`[Mem2Reg, SCCP, GVN, DCE, LICM]` — the MIR shape
//!    the registry hands the pass), and
//! 3. the full `standard` level (the registry now ends with LICM at W3).
//!
//! Zero mismatches allowed, two memory seeds each. Stays in the suite
//! permanently.

mod common;

use std::fs;

use sonolus_backend_core::cfg::Cfg;
use sonolus_backend_core::decode::decode_cfg;
use sonolus_backend_core::diff::{DiffConfig, DiffSummary, diff_with};
use sonolus_backend_core::passes::Pipeline;
use sonolus_backend_core::passes::dce::DcePass;
use sonolus_backend_core::passes::gvn::GvnRewritePass;
use sonolus_backend_core::passes::licm::LicmPass;
use sonolus_backend_core::passes::mem2reg::Mem2Reg;
use sonolus_backend_core::passes::sccp::Sccp;
use sonolus_backend_core::pipeline::{Level, compile_cfg, compile_cfg_with_pipeline};

/// Two memory randomizations per CFG (same scheme as tests/differential.rs).
const MEMORY_SEEDS: [u64; 2] = [0x5EED_0001, 0x5EED_0002];
const EVAL_BUDGET: u64 = 200_000;

fn licm_pipeline() -> Pipeline {
    Pipeline::new(vec![Box::new(LicmPass)])
}

fn w2_plus_licm_pipeline() -> Pipeline {
    Pipeline::new(vec![
        Box::new(Mem2Reg),
        Box::new(Sccp),
        Box::new(GvnRewritePass),
        Box::new(DcePass),
        Box::new(LicmPass),
    ])
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

fn run_corpus_diff(label: &str, pipeline: &dyn Fn() -> Pipeline) {
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
                |c| compile_cfg_with_pipeline(c, &pipeline()),
                &config,
            );
            summary.record(format!("{} mem-seed#{i}", &hash[..12]), &outcome);
        }
    }
    println!(
        "corpus differential minimal-vs-{label}: {}",
        summary.report()
    );
    assert_eq!(summary.cases, entry_count * MEMORY_SEEDS.len());
    summary.assert_no_mismatches();
}

#[test]
fn corpus_differential_minimal_vs_licm_only() {
    run_corpus_diff("licm-only", &licm_pipeline);
}

#[test]
fn corpus_differential_minimal_vs_w2_plus_licm() {
    run_corpus_diff("w2+licm", &w2_plus_licm_pipeline);
}

/// Where the pass actually fires on real frontend code: run the registry
/// prefix up to LICM ([W1, mem2reg, W1-rerun]), then LICM alone, and count
/// the corpus entries it mutates. Reported (not ratcheted) — the corpus is
/// dominated by loop-free callbacks, so the fire rate documents how much of
/// the eval movement can possibly come from this corpus at all.
#[test]
fn corpus_licm_fire_statistics() {
    use sonolus_backend_core::analysis::Analyses;
    use sonolus_backend_core::mir::build_mir;
    use sonolus_backend_core::passes::Pass;

    let cfgs = corpus_cfgs();
    let mut with_loops = 0usize;
    let mut fired = 0usize;
    for (hash, cfg) in &cfgs {
        let mut mir = build_mir(cfg).unwrap_or_else(|e| panic!("{hash}: build_mir: {e}"));
        let mut analyses = Analyses::new();
        let prefix = Pipeline::new(vec![
            Box::new(Sccp) as Box<dyn Pass>,
            Box::new(GvnRewritePass),
            Box::new(DcePass),
            Box::new(Mem2Reg),
            Box::new(Sccp),
            Box::new(GvnRewritePass),
            Box::new(DcePass),
        ]);
        prefix.run(&mut mir, &mut analyses);
        let dom = sonolus_backend_core::analysis::DomTree::compute(&mir);
        let loops = sonolus_backend_core::analysis::LoopForest::compute(&mir, &dom);
        if !loops.loops.is_empty() {
            with_loops += 1;
        }
        if LicmPass.run(&mut mir, &mut analyses) {
            fired += 1;
        }
    }
    println!(
        "corpus licm fire rate: {fired}/{} entries mutated ({} entries have natural loops)",
        cfgs.len(),
        with_loops
    );
}

#[test]
fn corpus_differential_minimal_vs_full_standard() {
    // minimal vs the full standard registry ([W1, mem2reg, W1-rerun, licm]).
    let cfgs = corpus_cfgs();
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
                |c| compile_cfg(c, Level::Standard),
                &config,
            );
            summary.record(format!("{} mem-seed#{i}", &hash[..12]), &outcome);
        }
    }
    println!(
        "corpus differential minimal-vs-standard: {}",
        summary.report()
    );
    summary.assert_no_mismatches();
}
