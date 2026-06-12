//! Per-transform differential tests for the T3.9 block-shaping pass (PORT.md
//! invariant §3.7): every frontend CFG in the mini-corpus, compiled `minimal`
//! vs
//!
//! 1. `minimal`+\[shape\] standalone (memory-form MIR has no phis yet, but
//!    exit shaping, threading, merging, and duplication all fire on raw
//!    frontend shapes),
//! 2. the registry context (`[Mem2Reg, SCCP, GVN, DCE, SwitchForm, LICM,
//!    Shape]` — the phi-bearing value-SSA MIR the registry hands the pass),
//!    and
//! 3. the full `standard` level (the registry now ends with the shape pass
//!    at W4).
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
use sonolus_backend_core::passes::shape::ShapePass;
use sonolus_backend_core::passes::switch_form::SwitchForm;
use sonolus_backend_core::pipeline::{Level, compile_cfg, compile_cfg_with_pipeline};

/// Two memory randomizations per CFG (same scheme as tests/differential.rs).
const MEMORY_SEEDS: [u64; 2] = [0x5EED_0001, 0x5EED_0002];
const EVAL_BUDGET: u64 = 200_000;

fn shape_pipeline() -> Pipeline {
    Pipeline::new(vec![Box::new(ShapePass)])
}

fn registry_context_pipeline() -> Pipeline {
    Pipeline::new(vec![
        Box::new(Mem2Reg),
        Box::new(Sccp),
        Box::new(GvnRewritePass),
        Box::new(DcePass),
        Box::new(SwitchForm),
        Box::new(LicmPass),
        Box::new(ShapePass),
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
fn corpus_differential_minimal_vs_shape_only() {
    run_corpus_diff("shape-only", &shape_pipeline);
}

#[test]
fn corpus_differential_minimal_vs_registry_context_shape() {
    run_corpus_diff("w2+w3+shape", &registry_context_pipeline);
}

#[test]
fn corpus_differential_minimal_vs_full_standard() {
    // minimal vs the full standard registry ([W1, mem2reg, W1-rerun,
    // switch-form, licm, shape]).
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

/// Where the pass actually fires on real frontend code: run the registry
/// prefix up to (but excluding) the shape pass, then the shape pass alone,
/// and count the corpus entries it mutates. Reported (not ratcheted) — the
/// documentation value is how much of the corpus the W4 shaping can touch at
/// all.
#[test]
fn corpus_shape_fire_statistics() {
    use sonolus_backend_core::analysis::Analyses;
    use sonolus_backend_core::mir::build_mir;
    use sonolus_backend_core::passes::Pass;

    let cfgs = corpus_cfgs();
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
            Box::new(SwitchForm),
            Box::new(LicmPass),
        ]);
        prefix.run(&mut mir, &mut analyses);
        if ShapePass.run(&mut mir, &mut analyses) {
            fired += 1;
        }
    }
    println!(
        "corpus shape fire rate: {fired}/{} entries mutated",
        cfgs.len()
    );
    assert!(
        fired > 0,
        "the shape pass must fire somewhere on the corpus"
    );
}
