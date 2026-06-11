//! Analysis verification (PORT.md T2.1 DoD): the dominator tree is checked
//! against a naive O(n²) iterative-dataflow reference on randomized small
//! CFGs and on every corpus CFG; the loop forest is checked for internal
//! consistency (bodies contain their header, back edges target the header,
//! nesting is a forest); liveness is checked for spot invariants (uses live
//! at their use point, kill-first blocks exclude the temp from live-in, dead
//! ends have empty live-out, the block-level fixpoint agrees with the
//! per-point cursor walk).

use std::fs;
use std::path::{Path, PathBuf};

use serde::Deserialize;
use sonolus_backend_core::analysis::{Analyses, DomTree, Liveness, LoopForest, inst_effect};
use sonolus_backend_core::decode::decode_cfg;
use sonolus_backend_core::mir::{CaseCond, Inst, Mir, Terminator, build_mir};

// ----------------------------------------------------------------------------------
// Naive dominator reference
// ----------------------------------------------------------------------------------

/// Per-block dominator sets via the classic O(n²) iterative dataflow
/// (`dom(b) = {b} ∪ ⋂ dom(p)`), with `None` for unreachable blocks.
fn naive_dom_sets(mir: &Mir) -> Vec<Option<Vec<bool>>> {
    let n = mir.blocks.len();
    if n == 0 {
        return Vec::new();
    }
    // Reachability (iterative DFS).
    let mut reachable = vec![false; n];
    let mut stack = vec![0usize];
    reachable[0] = true;
    while let Some(b) = stack.pop() {
        for s in mir.blocks[b].terminator.successors() {
            if !reachable[s] {
                reachable[s] = true;
                stack.push(s);
            }
        }
    }
    // Distinct reachable preds.
    let preds: Vec<Vec<usize>> = mir
        .predecessors()
        .into_iter()
        .enumerate()
        .map(|(b, ps)| {
            if reachable[b] {
                ps.into_iter().filter(|&p| reachable[p]).collect()
            } else {
                Vec::new()
            }
        })
        .collect();
    // dom(entry) = {entry}; dom(b) starts as "all reachable".
    let mut dom: Vec<Option<Vec<bool>>> = (0..n)
        .map(|b| {
            if !reachable[b] {
                None
            } else if b == 0 {
                let mut s = vec![false; n];
                s[0] = true;
                Some(s)
            } else {
                Some(reachable.clone())
            }
        })
        .collect();
    let mut changed = true;
    while changed {
        changed = false;
        for b in 0..n {
            if !reachable[b] || b == 0 {
                continue;
            }
            let mut new: Vec<bool> = reachable.clone();
            for &p in &preds[b] {
                let pd = dom[p].as_ref().expect("reachable preds have dom sets");
                for (w, &pw) in new.iter_mut().zip(pd) {
                    *w &= pw;
                }
            }
            new[b] = true;
            if dom[b].as_ref() != Some(&new) {
                dom[b] = Some(new);
                changed = true;
            }
        }
    }
    dom
}

/// Checks the CHK dominator tree against the naive reference: reachability,
/// the full `dominates` relation, idoms, and dominance frontiers.
fn check_dominators(mir: &Mir, dt: &DomTree) {
    let n = mir.blocks.len();
    let dom_sets = naive_dom_sets(mir);
    for b in 0..n {
        assert_eq!(
            dt.is_reachable(b),
            dom_sets[b].is_some(),
            "reachability mismatch at block {b}"
        );
    }
    for a in 0..n {
        for b in 0..n {
            let expected = dom_sets[b].as_ref().is_some_and(|s| s[a]);
            assert_eq!(
                dt.dominates(a, b),
                expected,
                "dominates({a}, {b}) disagrees with the naive reference"
            );
        }
    }
    // idom(b) = the strict dominator with the largest dominator set.
    for b in 0..n {
        let Some(set) = &dom_sets[b] else {
            assert_eq!(dt.idom(b), None);
            continue;
        };
        let expected = (0..n).filter(|&a| a != b && set[a]).max_by_key(|&a| {
            dom_sets[a]
                .as_ref()
                .map_or(0, |s| s.iter().filter(|&&x| x).count())
        });
        assert_eq!(dt.idom(b), expected, "idom({b}) disagrees");
    }
    // Frontiers: b ∈ DF(x) iff b is a join (≥2 distinct reachable preds) with
    // a pred dominated by x, and x does not strictly dominate b — except the
    // CHK entry-sentinel case (the entry is never in its own frontier; see
    // the dom.rs module docs).
    for x in 0..n {
        let mut expected: Vec<usize> = (0..n)
            .filter(|&b| {
                dt.is_reachable(b)
                    && dt.preds(b).len() >= 2
                    && dt.preds(b).iter().any(|&p| dt.dominates(x, p))
                    && !(dt.dominates(x, b) && x != b)
                    && !(x == 0 && b == 0)
            })
            .collect();
        expected.sort_unstable();
        let mut actual: Vec<usize> = dt.frontier(x).to_vec();
        actual.sort_unstable();
        assert_eq!(actual, expected, "frontier({x}) disagrees");
    }
    // RPO sanity: entry first, exactly the reachable blocks, idoms precede.
    if n > 0 && dt.is_reachable(0) {
        assert_eq!(dt.rpo()[0], 0);
    }
    assert_eq!(
        dt.rpo().len(),
        (0..n).filter(|&b| dt.is_reachable(b)).count()
    );
    for &b in dt.rpo() {
        if let Some(d) = dt.idom(b) {
            assert!(dt.rpo_number(d) < dt.rpo_number(b), "idom precedes in RPO");
        }
    }
}

// ----------------------------------------------------------------------------------
// Loop-forest consistency
// ----------------------------------------------------------------------------------

fn check_loops(mir: &Mir, dt: &DomTree, forest: &LoopForest) {
    let n = mir.blocks.len();
    // Every back edge appears as a latch of the loop with that header, and
    // every latch has a back edge to its header.
    let mut back_edges: Vec<(usize, usize)> = Vec::new();
    for u in 0..n {
        if !dt.is_reachable(u) {
            continue;
        }
        for h in mir.blocks[u].terminator.successors() {
            if dt.dominates(h, u) && back_edges.last() != Some(&(u, h)) {
                back_edges.push((u, h));
            }
        }
    }
    for &(u, h) in &back_edges {
        let l = forest
            .loops
            .iter()
            .find(|l| l.header == h)
            .unwrap_or_else(|| panic!("back edge {u}->{h} has no loop"));
        assert!(l.latches.contains(&u), "latch {u} missing from loop at {h}");
    }
    for (i, l) in forest.loops.iter().enumerate() {
        assert!(!l.latches.is_empty(), "loop {i} has no latches");
        for &latch in &l.latches {
            assert!(
                back_edges.contains(&(latch, l.header)),
                "latch {latch} of loop {i} has no back edge to header {}",
                l.header
            );
            assert!(l.body.contains(&latch), "latches are in the body");
        }
        // Body: header first, sorted strictly by RPO, all dominated by the
        // header, all reachable.
        assert_eq!(l.body.first(), Some(&l.header), "body contains the header");
        for w in l.body.windows(2) {
            assert!(
                dt.rpo_number(w[0]) < dt.rpo_number(w[1]),
                "body is RPO-sorted and duplicate-free"
            );
        }
        for &b in &l.body {
            assert!(dt.dominates(l.header, b), "header dominates the body");
            assert!(forest.contains(i, b));
        }
        // Nesting is a forest: parents precede children, bodies nest, depths
        // increment.
        match l.parent {
            None => assert_eq!(l.depth, 1),
            Some(p) => {
                assert!(p < i, "parents precede children");
                assert_eq!(l.depth, forest.loops[p].depth + 1);
                assert!(
                    forest.loops[p].children.contains(&i),
                    "child link is mirrored"
                );
                for &b in &l.body {
                    assert!(
                        forest.loops[p].body.contains(&b),
                        "nested bodies are contained"
                    );
                }
            }
        }
        for &c in &l.children {
            assert_eq!(forest.loops[c].parent, Some(i));
        }
    }
    // Loops ordered by header RPO; headers unique.
    for w in forest.loops.windows(2) {
        assert!(
            dt.rpo_number(w[0].header) < dt.rpo_number(w[1].header),
            "loops ordered by header RPO"
        );
    }
    // loop_of / depth are the innermost containing loop.
    for b in 0..n {
        let containing: Vec<usize> = (0..forest.loops.len())
            .filter(|&l| forest.contains(l, b))
            .collect();
        let innermost = containing
            .iter()
            .copied()
            .max_by_key(|&l| forest.loops[l].depth);
        assert_eq!(forest.loop_of(b), innermost, "loop_of({b})");
        assert_eq!(
            forest.depth(b),
            innermost.map_or(0, |l| forest.loops[l].depth),
            "depth({b})"
        );
    }
}

// ----------------------------------------------------------------------------------
// Liveness spot invariants
// ----------------------------------------------------------------------------------

fn check_liveness(mir: &Mir, live: &Liveness) {
    let scheduled = mir.scheduled_mask();
    for (b, block) in mir.blocks.iter().enumerate() {
        // Dead ends: nothing is live out of an exit block.
        if matches!(block.terminator, Terminator::Exit) {
            assert!(live.value_out(b).is_empty(), "exit block {b} live-out");
            assert!(live.temp_out(b).is_empty(), "exit block {b} temp live-out");
        }
        // Phi args are live out of their predecessors.
        for &phi in &block.phis {
            if let Inst::Phi { args } = mir.inst(phi) {
                for &(p, a) in args {
                    if !mir.is_const(a) {
                        assert!(
                            live.value_out(p).contains(a as usize),
                            "phi arg {a} live out of pred {p}"
                        );
                    }
                }
            }
        }
        // Walk every program point backward: each instruction's uses must be
        // live just before it; the walk must land exactly on the stored
        // live-in (gen/kill fixpoint == per-point transfer).
        let mut cursor = live.cursor_at_end(mir, b);
        while let Some(v) = cursor.step_back() {
            let eff = inst_effect(mir, &scheduled, v);
            for &u in &eff.value_uses {
                assert!(
                    cursor.value_live(u),
                    "value {u} not live at its use by {v} in block {b}"
                );
            }
            for &t in &eff.temp_uses {
                assert!(
                    cursor.temp_live(t),
                    "temp {t} not live at its use by {v} in block {b}"
                );
            }
        }
        let mut head_values = cursor.live_values().clone();
        for &phi in &block.phis {
            head_values.remove(phi as usize);
        }
        assert_eq!(
            &head_values,
            live.value_in(b),
            "cursor head point != value live-in at block {b}"
        );
        assert_eq!(
            cursor.live_temps(),
            live.temp_in(b),
            "cursor head point != temp live-in at block {b}"
        );
        // First-access invariant: a temp whose first access in the block is a
        // killing store is not live-in; first access as a use means live-in.
        let mut seen = vec![false; mir.temps.len()];
        for &v in &block.insts {
            let eff = inst_effect(mir, &scheduled, v);
            for &t in &eff.temp_uses {
                if !seen[t] {
                    seen[t] = true;
                    assert!(
                        live.temp_in(b).contains(t),
                        "temp {t} first used in block {b} must be live-in"
                    );
                }
            }
            if let Some((t, kills)) = eff.temp_def
                && !seen[t]
            {
                seen[t] = true;
                if kills {
                    assert!(
                        !live.temp_in(b).contains(t),
                        "temp {t} first killed in block {b} must not be live-in"
                    );
                }
            }
        }
    }
}

// ----------------------------------------------------------------------------------
// Randomized small CFGs
// ----------------------------------------------------------------------------------

struct SplitMix64(u64);

impl SplitMix64 {
    fn next(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    }

    fn below(&mut self, n: usize) -> usize {
        usize::try_from(self.next() % u64::try_from(n).expect("n fits u64")).expect("fits usize")
    }
}

/// A random CFG skeleton: self-loops, unreachable blocks, parallel edges,
/// irreducible regions and multiple exits all arise naturally.
fn random_mir(rng: &mut SplitMix64) -> Mir {
    let n = 1 + rng.below(10);
    let mut mir = Mir::new();
    for _ in 0..n {
        mir.push_block();
    }
    for b in 0..n {
        let terminator = match rng.below(10) {
            0 | 1 => Terminator::Exit,
            2..=5 => Terminator::Jump(rng.below(n)),
            _ => {
                let test = mir.push_inst(Inst::ConstInt(0));
                let n_cases = 1 + rng.below(3);
                let cases = (0..n_cases)
                    .map(|i| {
                        (
                            CaseCond::Int(i64::try_from(i).expect("small")),
                            rng.below(n),
                        )
                    })
                    .collect();
                let default = (rng.below(2) == 0).then(|| rng.below(n));
                Terminator::Branch {
                    test,
                    cases,
                    default,
                }
            }
        };
        mir.blocks[b].terminator = terminator;
    }
    mir
}

#[test]
fn randomized_cfgs_match_naive_reference() {
    let mut rng = SplitMix64(0xC0FF_EE00_D15E_A5E5);
    for case in 0..1000 {
        let mir = random_mir(&mut rng);
        let mut analyses = Analyses::new();
        let (dt, forest, live) = analyses.all(&mir);
        let ctx = || {
            format!(
                "case {case}: {:?}",
                mir.blocks.iter().map(|b| &b.terminator).collect::<Vec<_>>()
            )
        };
        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            check_dominators(&mir, dt);
            check_loops(&mir, dt, forest);
            check_liveness(&mir, live);
        }));
        assert!(result.is_ok(), "{}", ctx());
    }
}

// ----------------------------------------------------------------------------------
// Corpus
// ----------------------------------------------------------------------------------

#[derive(Debug, Deserialize)]
struct Manifest {
    entries: Vec<Entry>,
}

#[derive(Debug, Deserialize)]
struct Entry {
    hash: String,
}

fn testdata_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("testdata")
}

#[test]
fn corpus_cfgs_pass_all_analysis_checks() {
    let dir = testdata_dir();
    let manifest: Manifest =
        serde_json::from_slice(&fs::read(dir.join("manifest.json")).expect("manifest readable"))
            .expect("manifest parses");
    assert!(!manifest.entries.is_empty());
    let mut cfgs = 0usize;
    let mut blocks = 0usize;
    let mut reachable = 0usize;
    let mut loops_found = 0usize;
    let mut max_depth = 0u32;
    let mut blocks_in_loops = 0usize;
    for entry in &manifest.entries {
        let bytes = fs::read(dir.join("cfgs").join(format!("{}.scfg", entry.hash)))
            .unwrap_or_else(|e| panic!("missing corpus CFG {}: {e}", entry.hash));
        let cfg =
            decode_cfg(&bytes).unwrap_or_else(|e| panic!("{}: decode failed: {e}", entry.hash));
        let mir =
            build_mir(&cfg).unwrap_or_else(|e| panic!("{}: build_mir failed: {e}", entry.hash));
        let mut analyses = Analyses::new();
        let (dt, forest, live) = analyses.all(&mir);
        check_dominators(&mir, dt);
        check_loops(&mir, dt, forest);
        check_liveness(&mir, live);
        // Builder MIR is block-local in values: every use is defined in the
        // same block, so value live-in must be empty everywhere.
        for b in 0..mir.blocks.len() {
            assert!(
                live.value_in(b).is_empty(),
                "{}: builder MIR has a cross-block value live into block {b}",
                entry.hash
            );
        }
        cfgs += 1;
        blocks += mir.blocks.len();
        reachable += (0..mir.blocks.len())
            .filter(|&b| dt.is_reachable(b))
            .count();
        loops_found += forest.loops.len();
        max_depth = max_depth.max(forest.loops.iter().map(|l| l.depth).max().unwrap_or(0));
        blocks_in_loops += (0..mir.blocks.len())
            .filter(|&b| forest.loop_of(b).is_some())
            .count();
    }
    println!(
        "analysis corpus: {cfgs} CFGs, {blocks} MIR blocks ({reachable} reachable), \
         {loops_found} natural loops (max depth {max_depth}, {blocks_in_loops} blocks in loops)"
    );
}
