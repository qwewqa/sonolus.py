//! Per-transform differential test for the T3.2 GVN + rewrite-rules pass
//! (PORT.md invariant §3.7): full corpus × multiple memory seeds, `minimal`
//! vs `minimal + [gvn]` only (no other optimization passes), zero mismatches.
//!
//! Also reports the corpus-aggregate static metric movement of the pass
//! (emitted node count and MIR instruction count), so effectiveness is
//! visible in the test log.

mod common;

use std::fs;

use sonolus_backend_core::cfg::Cfg;
use sonolus_backend_core::decode::decode_cfg;
use sonolus_backend_core::diff::{DiffConfig, DiffSummary, diff_with};
use sonolus_backend_core::passes::Pipeline;
use sonolus_backend_core::passes::gvn::GvnRewritePass;
use sonolus_backend_core::pipeline::{
    Level, compile_cfg, compile_cfg_with_pipeline, compile_cfg_with_pipeline_stats,
};

/// Two memory randomizations per CFG (mirrors tests/differential.rs).
const MEMORY_SEEDS: [u64; 2] = [0x6E0D_0001, 0x6E0D_0002];
const EVAL_BUDGET: u64 = 200_000;

fn gvn_pipeline() -> Pipeline {
    Pipeline::new(vec![Box::new(GvnRewritePass)])
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
fn corpus_differential_minimal_vs_gvn_only() {
    let cfgs = corpus_cfgs();
    let entry_count = cfgs.len();
    let mut summary = DiffSummary::default();
    for (hash, cfg) in &cfgs {
        for (i, &seed) in MEMORY_SEEDS.iter().enumerate() {
            let config = DiffConfig {
                memory_seed: seed,
                rng_seed: seed.rotate_left(17) ^ 0x6E0D,
                eval_budget: EVAL_BUDGET,
            };
            let outcome = diff_with(
                cfg,
                |c| compile_cfg(c, Level::Minimal),
                |c| compile_cfg_with_pipeline(c, &gvn_pipeline()),
                &config,
            );
            summary.record(format!("{} mem-seed#{i}", &hash[..12]), &outcome);
        }
    }
    println!("corpus differential minimal-vs-[gvn]: {}", summary.report());
    assert_eq!(summary.cases, entry_count * MEMORY_SEEDS.len());
    assert!(entry_count > 0, "the corpus must not be empty");
    summary.assert_no_mismatches();
}

/// Every captured behavioral I/O vector replays correctly against the
/// `minimal + [gvn]` compilation (real recorded inputs and RNG tapes — the
/// per-level replay `tests/identity_levels.rs` performs, scoped to this
/// pass alone).
#[test]
fn corpus_vectors_replay_with_gvn() {
    let manifest = common::load_manifest();
    let mut replayed = 0u64;
    for (hash, cfg) in corpus_cfgs() {
        let entry = manifest
            .entries
            .iter()
            .find(|e| e.hash == hash)
            .expect("entry exists");
        if entry.vectors == 0 {
            continue;
        }
        let nodes = compile_cfg_with_pipeline(&cfg, &gvn_pipeline())
            .unwrap_or_else(|e| panic!("{hash}: gvn compile failed: {e}"));
        let file = common::load_vector_file(&hash);
        for (i, vector) in file.vectors.iter().enumerate() {
            let label = format!("{}#{i} pipeline=[gvn]", &hash[..12]);
            common::run_and_check(&label, &nodes, vector);
            replayed += 1;
        }
    }
    assert_eq!(replayed, manifest.vector_total, "every vector replayed");
    assert!(replayed > 0);
}

/// Corpus-aggregate effectiveness report: total emitted nodes and MIR
/// instructions, minimal vs minimal+[gvn]. The pass must never make the
/// emitted node count worse in aggregate; the absolute movement is printed
/// for the wave-gate log.
#[test]
fn corpus_aggregate_metrics_report() {
    let cfgs = corpus_cfgs();
    let (mut base_nodes, mut gvn_nodes) = (0u64, 0u64);
    let (mut base_insts, mut gvn_insts) = (0u64, 0u64);
    let mut improved = 0usize;
    let mut regressed = 0usize;
    for (hash, cfg) in &cfgs {
        let (_, base) = compile_cfg_with_pipeline_stats(cfg, &Pipeline::new(vec![]))
            .unwrap_or_else(|e| panic!("{hash}: minimal compile failed: {e}"));
        let (_, test) = compile_cfg_with_pipeline_stats(cfg, &gvn_pipeline())
            .unwrap_or_else(|e| panic!("{hash}: gvn compile failed: {e}"));
        base_nodes += u64::from(base.node_count);
        gvn_nodes += u64::from(test.node_count);
        base_insts += u64::from(base.mir_insts);
        gvn_insts += u64::from(test.mir_insts);
        match test.node_count.cmp(&base.node_count) {
            std::cmp::Ordering::Less => improved += 1,
            std::cmp::Ordering::Greater => regressed += 1,
            std::cmp::Ordering::Equal => {}
        }
    }
    println!(
        "gvn corpus aggregate: emitted nodes {base_nodes} -> {gvn_nodes} \
         ({improved} entries improved, {regressed} regressed of {}); \
         MIR arena insts {base_insts} -> {gvn_insts}",
        cfgs.len()
    );
    assert!(
        gvn_nodes <= base_nodes,
        "GVN+rules must not grow the corpus-aggregate emitted node count: \
         {base_nodes} -> {gvn_nodes}"
    );
    assert!(
        improved > 0,
        "GVN+rules should improve at least one corpus entry"
    );
}
