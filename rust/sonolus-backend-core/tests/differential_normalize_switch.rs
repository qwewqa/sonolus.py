//! Per-transform differential tests for the T3.11 switch-normalization pass
//! (PORT.md invariant §3.7): every frontend CFG in the mini-corpus, compiled
//! `minimal` vs `minimal`+\[`NormalizeSwitch`\] (standalone — fires only on
//! pre-formed multi-way branches with affine non-dense conds) and vs the full
//! `standard` level (the registry position: after W1-W4, on switch-formed and
//! shaped branches), zero mismatches allowed, two memory seeds each.
//!
//! Also reports the pass's fire rate and metric movement on the corpus
//! (honestly — affine non-dense case sets may be rare in real frontend code;
//! the dedicated fuzz surface lives in `tests/fuzz_normalize_switch.rs`).

mod common;

use std::fs;

use sonolus_backend_core::analysis::Analyses;
use sonolus_backend_core::cfg::Cfg;
use sonolus_backend_core::decode::decode_cfg;
use sonolus_backend_core::diff::{DiffConfig, DiffSummary, diff_with};
use sonolus_backend_core::interpret::Interpreter;
use sonolus_backend_core::mir::build_mir;
use sonolus_backend_core::nodes::EngineNodes;
use sonolus_backend_core::passes::normalize_switch::NormalizeSwitch;
use sonolus_backend_core::passes::{Pass, Pipeline, passes_for_level};
use sonolus_backend_core::pipeline::{CompileError, Level, compile_cfg, compile_cfg_with_pipeline};

/// Two memory randomizations per CFG (same scheme as tests/differential.rs).
const MEMORY_SEEDS: [u64; 2] = [0x5EED_0001, 0x5EED_0002];
const EVAL_BUDGET: u64 = 200_000;

fn normalize_only_pipeline() -> Pipeline {
    Pipeline::new(vec![Box::new(NormalizeSwitch)])
}

/// The registry prefix before the W5 entry (everything up to and including
/// W4) — the baseline that isolates this pass's contribution.
fn w4_prefix() -> Vec<Box<dyn Pass>> {
    let mut prefix = passes_for_level(Level::Standard);
    let pos = prefix
        .iter()
        .position(|p| p.name() == "normalize-switch")
        .expect("normalize-switch is registered at standard");
    prefix.truncate(pos);
    prefix
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

fn run_corpus_diff(label: &str, compile_test: &dyn Fn(&Cfg) -> Result<EngineNodes, CompileError>) {
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
                compile_test,
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
fn corpus_differential_minimal_vs_normalize_only() {
    run_corpus_diff("normalize-switch-only", &|c| {
        compile_cfg_with_pipeline(c, &normalize_only_pipeline())
    });
}

#[test]
fn corpus_differential_minimal_vs_full_standard() {
    // The standard level now includes the pass via the registry (Stage::W5).
    run_corpus_diff("full-standard", &|c| compile_cfg(c, Level::Standard));
}

/// Fire rate and metric movement on the corpus: compile every vector-bearing
/// entry with the W4 prefix vs W4 prefix + `NormalizeSwitch` and replay the
/// stored vectors. The pass never changes block structure, so dispatch must
/// be bit-identical; static nodes can only shrink where it fires (the cost
/// model drops n case constants for e <= 4 rebase nodes); per-vector eval may
/// move by at most +3 per executed dispatch (the model's worst case: a
/// strided fire hit on its first case scans 1 constant where the rebase
/// evaluates 4 nodes) and drops otherwise. MIR-level fire rate is counted by
/// running the pass directly after the W4 prefix. Reported, not ratcheted —
/// a low/zero fire rate on real frontend code is a valid finding.
#[test]
fn corpus_fire_statistics_and_metric_movement() {
    let dir = common::testdata_dir();
    let manifest = common::load_manifest();
    let mut entries_total = 0usize;
    let mut entries_changed = 0usize;
    let mut mir_fired = 0usize;
    let mut eval_without: u64 = 0;
    let mut eval_with: u64 = 0;
    let mut static_without: u64 = 0;
    let mut static_with: u64 = 0;
    for entry in manifest.entries.iter().filter(|e| e.vectors > 0) {
        let bytes = fs::read(dir.join("cfgs").join(format!("{}.scfg", entry.hash)))
            .unwrap_or_else(|e| panic!("missing frontend CFG {}: {e}", entry.hash));
        let cfg = decode_cfg(&bytes).expect("corpus CFG decodes");

        // MIR-level fire check: W4 prefix, then the pass alone.
        let mut mir = build_mir(&cfg).expect("corpus CFG builds");
        let mut analyses = Analyses::new();
        Pipeline::new(w4_prefix()).run(&mut mir, &mut analyses);
        if NormalizeSwitch.run(&mut mir, &mut analyses) {
            mir_fired += 1;
        }

        let without =
            compile_cfg_with_pipeline(&cfg, &Pipeline::new(w4_prefix())).expect("w4 compiles");
        let with = compile_cfg(&cfg, Level::Standard).expect("standard compiles");
        entries_total += 1;
        static_without += without.arena.len() as u64;
        static_with += with.arena.len() as u64;
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
            assert_eq!(
                dv, dw,
                "{}#{i}: normalization must not change dispatch",
                entry.hash
            );
            assert!(
                ev <= ew + 3 * dv,
                "{}#{i}: eval movement outside the cost-model bound: {ev} vs {ew} (+3*{dv} max)",
                entry.hash
            );
            eval_without += ew;
            eval_with += ev;
        }
    }
    println!(
        "normalize-switch on corpus: MIR fire rate {mir_fired}/{entries_total} entries; \
         emitted tree changed on {entries_changed}/{entries_total}; \
         eval {eval_without} -> {eval_with}, static {static_without} -> {static_with}"
    );
    assert!(
        static_with <= static_without,
        "static nodes can only shrink where the pass fires"
    );
}
