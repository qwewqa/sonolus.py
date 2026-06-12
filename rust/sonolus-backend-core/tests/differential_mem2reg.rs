//! Per-transform differential tests for the T3.4 Mem2Reg pass (PORT.md
//! invariant §3.7): every frontend CFG in the mini-corpus, compiled `minimal`
//! vs `minimal`+\[Mem2Reg\] (standalone, no W1 — every `compile_cfg` includes
//! the unconditional `destruct_ssa`, so this *is* the "[mem2reg, destruct]"
//! comparison: promoted MIR cannot lower without destruction) and vs the W2
//! block \[Mem2Reg, SCCP, GVN, DCE\], plus the full `standard` level — zero
//! mismatches allowed, two memory seeds each. Stays in the suite permanently.
//!
//! Also asserts effectiveness (the W2 quality claim): corpus aggregate
//! eval_count at `standard` must strictly improve on the recorded W1-era
//! baseline (`rust/baselines/rust-corpus.json`), and reports the promotion
//! statistics (how many temps promote and why others refuse) over the corpus.

mod common;

use std::fs;

use sonolus_backend_core::cfg::Cfg;
use sonolus_backend_core::decode::decode_cfg;
use sonolus_backend_core::diff::{DiffConfig, DiffSummary, diff_with};
use sonolus_backend_core::interpret::Interpreter;
use sonolus_backend_core::mir::build_mir;
use sonolus_backend_core::passes::dce::DcePass;
use sonolus_backend_core::passes::gvn::GvnRewritePass;
use sonolus_backend_core::passes::mem2reg::{Mem2Reg, PromotionStats, promotion_stats};
use sonolus_backend_core::passes::sccp::Sccp;
use sonolus_backend_core::passes::{Pass, Pipeline};
use sonolus_backend_core::pipeline::{
    Level, compile_cfg, compile_cfg_stats, compile_cfg_with_pipeline,
};

/// Two memory randomizations per CFG (same scheme as tests/differential.rs).
const MEMORY_SEEDS: [u64; 2] = [0x5EED_0001, 0x5EED_0002];
const EVAL_BUDGET: u64 = 200_000;

fn mem2reg_pipeline() -> Pipeline {
    Pipeline::new(vec![Box::new(Mem2Reg)])
}

fn w2_block_pipeline() -> Pipeline {
    Pipeline::new(vec![
        Box::new(Mem2Reg),
        Box::new(Sccp),
        Box::new(GvnRewritePass),
        Box::new(DcePass),
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
fn corpus_differential_minimal_vs_mem2reg_only() {
    // minimal vs [mem2reg] — and, because compile_cfg runs destruct_ssa
    // unconditionally after every pipeline, simultaneously the explicit
    // minimal vs [mem2reg, destruct] comparison.
    run_corpus_diff("mem2reg-only", &mem2reg_pipeline);
}

#[test]
fn corpus_differential_minimal_vs_w2_block() {
    // minimal vs [mem2reg, sccp, gvn, dce] — the W2 wave standalone.
    run_corpus_diff("mem2reg+w1rerun", &w2_block_pipeline);
}

#[test]
fn corpus_differential_minimal_vs_full_standard() {
    // minimal vs the full standard registry ([W1, mem2reg, W1-rerun]).
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

/// Corpus promotion statistics, measured where the pass actually runs: after
/// the W1 prefix (SCCP has folded computed indices by then). Reports the
/// aggregate and asserts the pass fires broadly and that read-before-write
/// never occurs on real frontend code (the module-doc claim).
#[test]
fn corpus_promotion_statistics() {
    let cfgs = corpus_cfgs();
    let mut agg = PromotionStats::default();
    let mut entries_with_promotion = 0usize;
    for (hash, cfg) in &cfgs {
        let mut mir = build_mir(cfg).unwrap_or_else(|e| panic!("{hash}: build_mir: {e}"));
        let w1 = Pipeline::new(vec![
            Box::new(Sccp) as Box<dyn Pass>,
            Box::new(GvnRewritePass),
            Box::new(DcePass),
        ]);
        w1.run(
            &mut mir,
            &mut sonolus_backend_core::analysis::Analyses::new(),
        );
        let stats = promotion_stats(&mir);
        agg.temps_total += stats.temps_total;
        agg.temps_accessed += stats.temps_accessed;
        agg.promoted += stats.promoted;
        agg.refused_dynamic_index += stats.refused_dynamic_index;
        agg.refused_out_of_bounds += stats.refused_out_of_bounds;
        agg.refused_read_before_write += stats.refused_read_before_write;
        if stats.promoted > 0 {
            entries_with_promotion += 1;
        }
    }
    println!(
        "corpus promotion (post-W1): {} temps total, {} accessed, {} promoted, \
         {} refused-dynamic, {} refused-oob, {} refused-read-before-write \
         ({entries_with_promotion}/{} entries promote something)",
        agg.temps_total,
        agg.temps_accessed,
        agg.promoted,
        agg.refused_dynamic_index,
        agg.refused_out_of_bounds,
        agg.refused_read_before_write,
        cfgs.len()
    );
    assert!(agg.promoted > 0, "the pass must fire on the corpus");
    assert_eq!(
        agg.refused_out_of_bounds, 0,
        "real frontend code never accesses temps out of bounds"
    );
    // Read-before-write refusals DO occur on real frontend code (~2% of
    // accessed temps): statically possible but dynamically unreachable reads
    // — exactly the situation legacy ToSSA mapped to an `err` SSA place and
    // documented as out of contract (e.g. matching a just-created
    // VarArray[Num, 1]: the size>0 check is not folded at IR build time).
    // The safe-refusal policy keeps such temps in memory; pin the order of
    // magnitude so a refusal-rate regression is visible.
    assert!(
        agg.refused_read_before_write * 20 <= agg.temps_accessed,
        "read-before-write refusals exploded: {} of {} accessed temps",
        agg.refused_read_before_write,
        agg.temps_accessed
    );
}

/// G3.2 regression (deterministic, independent of proptest persistence): a
/// **dead temp store whose value tree contains a trap-capable op**, plus a
/// read of a *different* slot of the same temp. Minimal evaluates the dead
/// store's `Arcsin` and traps when its input is outside [-1, 1]; the shrunk
/// 1M-fuzz case showed the standard pipeline losing that trap: Mem2Reg
/// removes the dead store (leaving the value tree scheduled) and replaces the
/// `u[0]` load with the reaching constant 0, the re-run GVN's sub-zero rule
/// collapses `Subtract(Arcsin(x), 0)`, and the rules sweep then cascaded into
/// the orphaned trap-capable `Arcsin`. Optimized code must trap whenever
/// minimal traps, with the same error (PORT.md §3.7).
#[test]
fn dead_temp_store_with_trapping_value_tree_keeps_the_trap_at_every_level() {
    use sonolus_backend_core::cfg::{
        BasicBlock, BlockValue, IndexValue, Node, Place, TempBlockDef,
    };
    use sonolus_backend_core::ops::Op;

    let mut cfg = Cfg::default();
    cfg.strings.push("t".to_owned());
    cfg.strings.push("u".to_owned());
    cfg.temp_blocks.push(TempBlockDef { name: 0, size: 2 }); // t
    cfg.temp_blocks.push(TempBlockDef { name: 1, size: 1 }); // u
    let node = |cfg: &mut Cfg, n: Node| {
        cfg.nodes.push(n);
        cfg.nodes.len() - 1
    };
    let place = |cfg: &mut Cfg, block: BlockValue, index: i64| {
        cfg.places.push(Place {
            block,
            index: IndexValue::Int(index),
            offset: 0,
        });
        cfg.places.len() - 1
    };
    let mut stmts = Vec::new();
    // Initialize every temp cell (mirrors the fuzz generator's entry init —
    // makes both temps promotable: no read-before-write refusal).
    for (t, size) in [(0usize, 2i64), (1, 1)] {
        for i in 0..size {
            let v = node(&mut cfg, Node::ConstInt(if t == 0 { 1 } else { 0 }));
            let p = place(&mut cfg, BlockValue::Temp(t), i);
            stmts.push(node(&mut cfg, Node::Set { place: p, value: v }));
        }
    }
    // The dead store: t[0] <- Subtract(Arcsin(Get(-3[0])), Get(u[0])).
    // t[0] is never read afterwards; u[0]'s reaching value is the constant 0.
    let in_p = place(&mut cfg, BlockValue::Int(-3), 0);
    let get_in = node(&mut cfg, Node::Get(in_p));
    let arcsin = node(
        &mut cfg,
        Node::PureInstr {
            op: Op::Arcsin,
            args: vec![get_in],
        },
    );
    let u_p = place(&mut cfg, BlockValue::Temp(1), 0);
    let get_u = node(&mut cfg, Node::Get(u_p));
    let sub = node(
        &mut cfg,
        Node::PureInstr {
            op: Op::Subtract,
            args: vec![arcsin, get_u],
        },
    );
    let t0_p = place(&mut cfg, BlockValue::Temp(0), 0);
    stmts.push(node(
        &mut cfg,
        Node::Set {
            place: t0_p,
            value: sub,
        },
    ));
    // Observable read of the *other* slot: 20[0] <- Get(t[1]).
    let t1_p = place(&mut cfg, BlockValue::Temp(0), 1);
    let get_t1 = node(&mut cfg, Node::Get(t1_p));
    let out_p = place(&mut cfg, BlockValue::Int(20), 0);
    stmts.push(node(
        &mut cfg,
        Node::Set {
            place: out_p,
            value: get_t1,
        },
    ));
    let test = node(&mut cfg, Node::ConstInt(0));
    cfg.blocks.push(BasicBlock {
        statements: stmts,
        test,
        outgoing: Vec::new(),
    });

    let run_level = |level: Level, input: f64| {
        let nodes = compile_cfg(&cfg, level)
            .unwrap_or_else(|e| panic!("{} failed to compile: {e}", level.name()));
        let mut interp = Interpreter::new(0);
        interp.set_block(-3, vec![input]);
        let result = interp.run(&nodes).map_err(|e| e.to_string());
        let out = interp.block(20).map(<[f64]>::to_vec);
        (result, out)
    };
    for level in [Level::Fast, Level::Standard] {
        // Trap input: Arcsin(2.5) raises in minimal; the optimized levels
        // must produce the identical error.
        let (base, _) = run_level(Level::Minimal, 2.5);
        let (test, _) = run_level(level, 2.5);
        let base_err = base.expect_err("minimal must trap on Arcsin(2.5)");
        assert!(
            base_err.contains("expected a number in range from -1 up to 1"),
            "unexpected baseline error: {base_err}"
        );
        assert_eq!(
            test.expect_err(&format!("{} must trap exactly like minimal", level.name())),
            base_err,
            "{}: trap error must match minimal's",
            level.name()
        );
        // Clean input: identical results and writes.
        let (base, base_out) = run_level(Level::Minimal, 0.5);
        let (test, test_out) = run_level(level, 0.5);
        assert_eq!(
            base.expect("minimal runs clean"),
            test.expect("optimized runs clean")
        );
        assert_eq!(base_out, test_out, "{}: writes must match", level.name());
    }
}

/// The recorded W1-era corpus aggregates (rust/baselines/rust-corpus.json).
fn baseline_aggregates() -> (u64, u64) {
    let path = common::testdata_dir()
        .join("..")
        .join("baselines")
        .join("rust-corpus.json");
    let doc: serde_json::Value =
        serde_json::from_slice(&fs::read(&path).expect("baseline file readable"))
            .expect("baseline parses");
    let agg = &doc["aggregates"];
    (
        agg["eval_count"].as_u64().expect("eval_count"),
        agg["static_nodes"].as_u64().expect("static_nodes"),
    )
}

/// Corpus metrics ratchet: replaying every corpus vector at `standard` (the
/// same measurement tools/metrics.py corpus mode performs) must not regress
/// eval count or static nodes vs the stored baseline. The baseline file is
/// rewritten at each wave gate (`tools/metrics.py corpus --update`), so this
/// pins quality to the last gate's snapshot; strict per-wave improvement
/// claims are recorded in the PORT.md worklog, not asserted here.
#[test]
fn corpus_ratchet_eval_and_static_not_regressed() {
    let dir = common::testdata_dir();
    let manifest = common::load_manifest();
    let mut eval_total: u64 = 0;
    let mut dispatch_total: u64 = 0;
    let mut static_total: u64 = 0;
    let mut vectors = 0u64;
    for entry in manifest.entries.iter().filter(|e| e.vectors > 0) {
        let bytes = fs::read(dir.join("cfgs").join(format!("{}.scfg", entry.hash)))
            .unwrap_or_else(|e| panic!("missing frontend CFG {}: {e}", entry.hash));
        let cfg = decode_cfg(&bytes).expect("corpus CFG decodes");
        let (nodes, _stats) = compile_cfg_stats(&cfg, Level::Standard).expect("standard compiles");
        static_total += sonolus_backend_core::nodes::tree_node_count(&nodes.arena, nodes.root);
        let file = common::load_vector_file(&entry.hash);
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
            let result = interp
                .run(&nodes)
                .unwrap_or_else(|e| panic!("{}#{i}: replay failed at standard: {e}", entry.hash));
            let expected = common::decode_value(&vector.result);
            assert!(
                common::values_match(result, expected),
                "{}#{i}: result {result:?} != recorded {expected:?}",
                entry.hash
            );
            eval_total += interp.eval_count();
            dispatch_total += interp.dispatch_count();
            vectors += 1;
        }
    }
    let (baseline_eval, baseline_static) = baseline_aggregates();
    println!(
        "corpus standard metrics: eval {eval_total} (ratchet {baseline_eval}), \
         static {static_total} (ratchet {baseline_static}), dispatch {dispatch_total}, \
         {vectors} vectors"
    );
    assert!(
        eval_total <= baseline_eval,
        "corpus eval count regressed vs the ratchet baseline: {eval_total} > {baseline_eval}"
    );
    assert!(
        static_total <= baseline_static,
        "corpus static nodes regressed vs the ratchet baseline: {static_total} > {baseline_static}"
    );
}
