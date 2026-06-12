//! Per-transform differential tests for the T3.6 switch-formation pass
//! (PORT.md invariant §3.7): every frontend CFG in the mini-corpus, compiled
//! `minimal` vs `minimal`+\[`SwitchForm`\] (standalone — exercises the
//! pre-promotion variant-(b) re-load merges) and vs the production W2 prefix
//! +\[`SwitchForm`\] (the registry position: post-GVN/Mem2Reg, value-identity
//! merges), zero mismatches allowed, two memory seeds each. The full
//! `standard` level (which now includes the pass via the registry) stays
//! covered by `tests/differential_mem2reg.rs` and tests/differential.rs.
//!
//! Also checks the pass actually fires on the corpus and reports the dispatch
//! movement it is responsible for (the headline T3.6 metric).

mod common;

use std::fs;

use sonolus_backend_core::cfg::Cfg;
use sonolus_backend_core::decode::decode_cfg;
use sonolus_backend_core::diff::{DiffConfig, DiffSummary, diff_with};
use sonolus_backend_core::interpret::Interpreter;
use sonolus_backend_core::passes::dce::DcePass;
use sonolus_backend_core::passes::gvn::GvnRewritePass;
use sonolus_backend_core::passes::mem2reg::Mem2Reg;
use sonolus_backend_core::passes::sccp::Sccp;
use sonolus_backend_core::passes::switch_form::SwitchForm;
use sonolus_backend_core::passes::{Pass, Pipeline};
use sonolus_backend_core::pipeline::{Level, compile_cfg, compile_cfg_with_pipeline};

/// Two memory randomizations per CFG (same scheme as tests/differential.rs).
const MEMORY_SEEDS: [u64; 2] = [0x5EED_0001, 0x5EED_0002];
const EVAL_BUDGET: u64 = 200_000;

fn switch_form_pipeline() -> Pipeline {
    Pipeline::new(vec![Box::new(SwitchForm)])
}

/// The production prefix the pass runs after (the registry through W2).
fn w2_prefix() -> Vec<Box<dyn Pass>> {
    vec![
        Box::new(Sccp) as Box<dyn Pass>,
        Box::new(GvnRewritePass),
        Box::new(DcePass),
        Box::new(Mem2Reg),
        Box::new(Sccp),
        Box::new(GvnRewritePass),
        Box::new(DcePass),
    ]
}

fn w2_plus_switch_form_pipeline() -> Pipeline {
    let mut passes = w2_prefix();
    passes.push(Box::new(SwitchForm));
    Pipeline::new(passes)
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
fn corpus_differential_minimal_vs_switch_form_only() {
    run_corpus_diff("switch-form-only", &switch_form_pipeline);
}

#[test]
fn corpus_differential_minimal_vs_w2_plus_switch_form() {
    run_corpus_diff("w2+switch-form", &w2_plus_switch_form_pipeline);
}

/// The pass must fire on real frontend code, and its dispatch movement must
/// be nonnegative everywhere it changes anything: replay every corpus vector
/// through the W2 prefix with and without the pass and compare per-vector
/// dispatch/eval counts.
#[test]
fn corpus_dispatch_movement_and_fire_rate() {
    let dir = common::testdata_dir();
    let manifest = common::load_manifest();
    let mut entries_changed = 0usize;
    let mut entries_total = 0usize;
    let mut dispatch_without: u64 = 0;
    let mut dispatch_with: u64 = 0;
    let mut eval_without: u64 = 0;
    let mut eval_with: u64 = 0;
    for entry in manifest.entries.iter().filter(|e| e.vectors > 0) {
        let bytes = fs::read(dir.join("cfgs").join(format!("{}.scfg", entry.hash)))
            .unwrap_or_else(|e| panic!("missing frontend CFG {}: {e}", entry.hash));
        let cfg = decode_cfg(&bytes).expect("corpus CFG decodes");
        let without = compile_cfg_with_pipeline(&cfg, &Pipeline::new(w2_prefix()))
            .expect("w2 prefix compiles");
        let with = compile_cfg_with_pipeline(&cfg, &w2_plus_switch_form_pipeline())
            .expect("w2+switch-form compiles");
        entries_total += 1;
        let changed = sonolus_backend_core::nodes::format_engine_node(&with.arena, with.root)
            != sonolus_backend_core::nodes::format_engine_node(&without.arena, without.root);
        if changed {
            entries_changed += 1;
        }
        let file = common::load_vector_file(&entry.hash);
        for (i, vector) in file.vectors.iter().enumerate() {
            let run = |nodes: &sonolus_backend_core::nodes::EngineNodes| {
                let tape: Vec<f64> = vector
                    .rng
                    .iter()
                    .map(|(_, _, _, value)| common::decode_value(value))
                    .collect();
                let mut interp = Interpreter::with_tape(tape);
                for (block, values) in &vector.inputs {
                    interp.set_block(*block, values.iter().map(common::decode_value).collect());
                }
                interp
                    .run(nodes)
                    .unwrap_or_else(|e| panic!("{}#{i}: replay failed: {e}", entry.hash));
                (interp.dispatch_count(), interp.eval_count())
            };
            let (dw, ew) = run(&without);
            let (dv, ev) = run(&with);
            assert!(
                dv <= dw,
                "{}#{i}: switch formation increased dispatch: {dv} > {dw}",
                entry.hash
            );
            dispatch_without += dw;
            dispatch_with += dv;
            eval_without += ew;
            eval_with += ev;
        }
    }
    println!(
        "switch formation on corpus: {entries_changed}/{entries_total} entries changed; \
         dispatch {dispatch_without} -> {dispatch_with}, eval {eval_without} -> {eval_with}"
    );
    assert!(
        entries_changed > 0,
        "the pass must fire somewhere on the corpus"
    );
    assert!(
        dispatch_with <= dispatch_without,
        "aggregate dispatch must not regress"
    );
    assert!(
        eval_with <= eval_without,
        "aggregate eval count must not regress"
    );
}
