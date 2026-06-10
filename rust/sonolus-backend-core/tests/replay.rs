//! Corpus replay (PORT.md task T1.2 DoD): for every behavioral I/O vector in
//! the checked-in mini-corpus, decode the linked **post-pass** CFG, emit the
//! engine-node tree (T1.2 emitter), generate output nodes, then run the tree
//! on the T1.1 interpreter with the vector's inputs and RNG tape. The observed
//! `result`, `log`, and `writes` must match the values recorded from the
//! legacy Python interpreter.
//!
//! Comparison semantics are shared with `pipeline_replay.rs` via
//! `common/mod.rs` (raw-bit equality with NaN==NaN and +0.0==-0.0; writes
//! last-write-wins excluding the temp memory block — excluded by contract,
//! even though this replay currently uses the same Python allocation).

mod common;

use std::fs;

use sonolus_backend_core::decode::decode_cfg;
use sonolus_backend_core::emit::cfg_to_engine_nodes;

#[test]
fn every_corpus_vector_replays_on_the_rust_pipeline() {
    let dir = common::testdata_dir();
    let manifest = common::load_manifest();
    let mut replayed = 0u64;
    for entry in manifest.entries.iter().filter(|e| e.vectors > 0) {
        let file = common::load_vector_file(&entry.hash);
        for (i, vector) in file.vectors.iter().enumerate() {
            let label = format!("{}#{i} (level {})", &entry.hash[..12], vector.level);
            let post = vector.post_cfg.as_ref().unwrap_or_else(|| {
                panic!("{label}: vector has no post_cfg link (post-pass encode reject?)")
            });
            let post_bytes = fs::read(dir.join("post_cfgs").join(format!("{post}.scfg")))
                .unwrap_or_else(|e| panic!("{label}: missing post-pass CFG {post}: {e}"));
            let cfg = decode_cfg(&post_bytes)
                .unwrap_or_else(|e| panic!("{label}: post-pass CFG failed to decode: {e}"));
            let nodes =
                cfg_to_engine_nodes(&cfg).unwrap_or_else(|e| panic!("{label}: emit failed: {e}"));
            common::run_and_check(&label, &nodes, vector);
            replayed += 1;
        }
    }
    assert_eq!(
        replayed, manifest.vector_total,
        "every corpus vector must be replayed"
    );
    assert!(replayed > 0, "the corpus must contain vectors");
}
