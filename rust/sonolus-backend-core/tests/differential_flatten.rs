//! Per-transform differential tests for the T3.10 emission-time
//! `FlattenAssociativeOps` (PORT.md invariant §3.7): every frontend CFG in
//! the mini-corpus, compared across the flattening seam:
//!
//! 1. `minimal` vs `minimal`+flatten — the transform alone on raw frontend
//!    shapes (binarized Add/Multiply chains, right-nested And/Or chains),
//! 2. full standard passes *without* flattening vs *with* flattening — the
//!    transform isolated at its realistic seam (post-W4 trees with
//!    if-converted Execute chains and GVN-canonicalized const-first Adds),
//!    base and test sharing every mid-level pass, and
//! 3. the same isolation for every [`SharingPolicy`] variant (the policy must
//!    never affect behavior, only node shapes).
//!
//! `minimal` vs full `standard` (which now flattens) is covered by
//! tests/differential.rs. Zero mismatches allowed, two memory seeds each.
//! Stays in the suite permanently.

mod common;

use std::fs;

use sonolus_backend_core::cfg::Cfg;
use sonolus_backend_core::decode::decode_cfg;
use sonolus_backend_core::diff::{DiffConfig, DiffSummary, diff_with};
use sonolus_backend_core::flatten::{SharingPolicy, flatten_engine_nodes};
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
fn corpus_differential_minimal_vs_minimal_plus_flatten() {
    run_corpus_diff(
        "minimal vs minimal+flatten",
        |c| compile_cfg(c, Level::Minimal),
        |c| compile_cfg_with_pipeline(c, &Pipeline::new(vec![]).with_flatten(true)),
    );
}

#[test]
fn corpus_differential_standard_unflattened_vs_flattened() {
    run_corpus_diff(
        "standard-passes vs standard-passes+flatten",
        |c| compile_cfg_with_pipeline(c, &Pipeline::new(passes_for_level(Level::Standard))),
        |c| {
            compile_cfg_with_pipeline(
                c,
                &Pipeline::new(passes_for_level(Level::Standard)).with_flatten(true),
            )
        },
    );
}

#[test]
fn corpus_differential_sharing_policy_variants() {
    for policy in [
        SharingPolicy::Always,
        SharingPolicy::UnsharedOnly,
        SharingPolicy::SharedUpTo(4),
    ] {
        run_corpus_diff(
            &format!("standard-passes vs standard-passes+flatten({policy:?})"),
            |c| compile_cfg_with_pipeline(c, &Pipeline::new(passes_for_level(Level::Standard))),
            move |c| {
                let nodes = compile_cfg_with_pipeline(
                    c,
                    &Pipeline::new(passes_for_level(Level::Standard)),
                )?;
                Ok(flatten_engine_nodes(&nodes, policy))
            },
        );
    }
}

/// The T3.10 cost model, pinned on the corpus (PORT.md worklog table): on
/// every tracked metric (`eval_count` via vector replay, `static_nodes`,
/// `dag_size`), `Always` dominates or ties the sharing-aware policies, which
/// dominate or tie the unflattened tree; the only growth `Always` can cause
/// is serialized argument duplication (`dag_args`, untracked) versus
/// `UnsharedOnly`.
#[test]
fn corpus_cost_model_policy_table() {
    use sonolus_backend_core::interpret::Interpreter;
    use sonolus_backend_core::nodes::tree_node_count;
    use sonolus_backend_core::output::{OutputNode, generate_output_nodes};

    #[derive(Default, Clone, Copy)]
    struct Totals {
        eval: u64,
        static_nodes: u64,
        dag_nodes: u64,
        dag_args: u64,
    }

    let dir = common::testdata_dir();
    let manifest = common::load_manifest();
    let variants: [(&str, Option<SharingPolicy>); 4] = [
        ("unflattened", None),
        ("Always", Some(SharingPolicy::Always)),
        ("UnsharedOnly", Some(SharingPolicy::UnsharedOnly)),
        ("SharedUpTo(4)", Some(SharingPolicy::SharedUpTo(4))),
    ];
    let mut totals = [Totals::default(); 4];

    for entry in manifest.entries.iter().filter(|e| e.vectors > 0) {
        let bytes = fs::read(dir.join("cfgs").join(format!("{}.scfg", entry.hash)))
            .unwrap_or_else(|e| panic!("missing frontend CFG {}: {e}", entry.hash));
        let cfg = decode_cfg(&bytes).expect("corpus CFG decodes");
        let unflattened =
            compile_cfg_with_pipeline(&cfg, &Pipeline::new(passes_for_level(Level::Standard)))
                .expect("standard compiles");
        let file = common::load_vector_file(&entry.hash);
        for (slot, (_, policy)) in variants.iter().enumerate() {
            let nodes = match policy {
                None => unflattened.clone(),
                Some(p) => flatten_engine_nodes(&unflattened, *p),
            };
            totals[slot].static_nodes += tree_node_count(&nodes.arena, nodes.root);
            let out = generate_output_nodes(&nodes.arena, nodes.root).expect("output generates");
            totals[slot].dag_nodes += out.nodes.len() as u64;
            totals[slot].dag_args += out
                .nodes
                .iter()
                .map(|n| match n {
                    OutputNode::Func { args, .. } => args.len() as u64,
                    OutputNode::Value { .. } => 0,
                })
                .sum::<u64>();
            for (i, vector) in file.vectors.iter().enumerate() {
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
                    .run(&nodes)
                    .unwrap_or_else(|e| panic!("{}#{i}: replay failed: {e}", entry.hash));
                totals[slot].eval += interp.eval_count();
            }
        }
    }

    println!("policy            eval   static      dag dag_args");
    for ((name, _), t) in variants.iter().zip(totals.iter()) {
        println!(
            "{name:<14} {:>7} {:>8} {:>8} {:>8}",
            t.eval, t.static_nodes, t.dag_nodes, t.dag_args
        );
    }
    let [unflat, always, unshared, bounded] = totals;
    for (name, t) in [
        ("Always", always),
        ("UnsharedOnly", unshared),
        ("SharedUpTo(4)", bounded),
    ] {
        assert!(t.eval <= unflat.eval, "{name}: eval must not regress");
        assert!(
            t.static_nodes <= unflat.static_nodes,
            "{name}: static must not regress"
        );
        assert!(
            t.dag_nodes <= unflat.dag_nodes,
            "{name}: dag node count must not regress (module-docs proof)"
        );
    }
    for (name, t) in [("UnsharedOnly", unshared), ("SharedUpTo(4)", bounded)] {
        assert!(
            always.eval <= t.eval
                && always.static_nodes <= t.static_nodes
                && always.dag_nodes <= t.dag_nodes,
            "Always must dominate {name} on every tracked metric"
        );
    }
}
