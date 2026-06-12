//! MIR analyses for the optimizer (PORT.md T2.1): dominator tree, natural-loop
//! forest, liveness — with on-demand caching and explicit invalidation.
//!
//! # Using the analyses from a pass
//!
//! Passes receive an [`Analyses`] alongside the [`Mir`]. Results are computed
//! lazily on first request and cached until invalidated:
//!
//! ```
//! use sonolus_backend_core::analysis::Analyses;
//! use sonolus_backend_core::mir::Mir;
//!
//! let mut mir = Mir::new();
//! let entry = mir.push_block();
//! let mut analyses = Analyses::new();
//! let dom = analyses.dom_tree(&mir);
//! assert!(dom.is_reachable(entry));
//! // ... a pass mutates instructions (not control flow) ...
//! analyses.invalidate_values();
//! let live = analyses.liveness(&mir);
//! assert!(live.value_in(entry).is_empty());
//! ```
//!
//! # Invalidation discipline (read this before writing a pass)
//!
//! The cache cannot observe MIR mutation, so **every pass that mutates the MIR
//! must invalidate before the next analysis request**:
//!
//! - Changed control flow (terminators, block list, phi *placement*)?
//!   Call [`Analyses::invalidate_cfg`] (drops everything — liveness depends
//!   on CFG shape too).
//! - Changed only instructions/operands/schedules/temp sizes (CFG shape
//!   intact)? Call [`Analyses::invalidate_values`] (drops liveness, keeps the
//!   dominator tree and loop forest).
//! - Unsure? [`Analyses::invalidate_all`].
//!
//! Forgetting is hard to do silently: in debug builds every getter
//! fingerprints the MIR (CFG shape for dominators/loops, full instruction
//! content for liveness) and panics if a cached result is served for a
//! mutated MIR. Release builds skip the check (it is O(MIR) per request).
//!
//! # Determinism
//!
//! All results are built from `Vec`s and dense bitsets ordered by block / RPO
//! / arena index — same MIR in, same analysis out, byte for byte (invariant
//! §3.5). No `HashMap` order reaches any result.

mod bitset;
mod dom;
mod liveness;
mod loops;

pub use bitset::{BitSet, BitSetIter};
pub use dom::DomTree;
pub use liveness::{InstEffect, Liveness, LivenessCursor, inst_effect};
pub use loops::{Loop, LoopForest, LoopId};

use std::hash::{Hash, Hasher};

use crate::mir::{BlockRef, CaseCond, IndexRef, Inst, Mir, Place, Terminator};

/// Lazily computed, explicitly invalidated analysis cache for one [`Mir`].
///
/// See the module docs for the invalidation discipline. The struct does not
/// borrow the MIR; every getter takes `&Mir` so passes can interleave reads
/// and (invalidated) writes.
#[derive(Debug, Default)]
pub struct Analyses {
    dom: Option<DomTree>,
    loops: Option<LoopForest>,
    liveness: Option<Liveness>,
    /// Debug-build fingerprints of the MIR the cached results were computed
    /// from (`None` when nothing relying on them is cached).
    cfg_fp: Option<u64>,
    values_fp: Option<u64>,
}

impl Analyses {
    pub fn new() -> Self {
        Self::default()
    }

    /// The dominator tree (cached).
    pub fn dom_tree(&mut self, mir: &Mir) -> &DomTree {
        self.debug_check(mir);
        self.ensure_dom(mir);
        self.dom.as_ref().expect("just ensured")
    }

    /// The natural-loop forest (cached; computes the dominator tree too).
    pub fn loop_forest(&mut self, mir: &Mir) -> &LoopForest {
        self.debug_check(mir);
        self.ensure_loops(mir);
        self.loops.as_ref().expect("just ensured")
    }

    /// Liveness over values and temps (cached).
    pub fn liveness(&mut self, mir: &Mir) -> &Liveness {
        self.debug_check(mir);
        self.ensure_liveness(mir);
        self.liveness.as_ref().expect("just ensured")
    }

    /// All three analyses at once (for passes that need simultaneous borrows).
    pub fn all(&mut self, mir: &Mir) -> (&DomTree, &LoopForest, &Liveness) {
        self.debug_check(mir);
        self.ensure_dom(mir);
        self.ensure_loops(mir);
        self.ensure_liveness(mir);
        (
            self.dom.as_ref().expect("ensured"),
            self.loops.as_ref().expect("ensured"),
            self.liveness.as_ref().expect("ensured"),
        )
    }

    /// Drops every cached result.
    pub fn invalidate_all(&mut self) {
        self.dom = None;
        self.loops = None;
        self.liveness = None;
        self.cfg_fp = None;
        self.values_fp = None;
    }

    /// The CFG shape changed (terminators, block list, phi placement): drops
    /// everything (liveness depends on the shape too).
    pub fn invalidate_cfg(&mut self) {
        self.invalidate_all();
    }

    /// Instructions/operands/schedules changed but the CFG shape did not:
    /// drops liveness, keeps the dominator tree and loop forest.
    pub fn invalidate_values(&mut self) {
        self.liveness = None;
        self.values_fp = None;
    }

    fn ensure_dom(&mut self, mir: &Mir) {
        if self.dom.is_none() {
            self.dom = Some(DomTree::compute(mir));
            if cfg!(debug_assertions) {
                self.cfg_fp = Some(cfg_shape_fingerprint(mir));
            }
        }
    }

    fn ensure_loops(&mut self, mir: &Mir) {
        if self.loops.is_none() {
            self.ensure_dom(mir);
            let dom = self.dom.as_ref().expect("just ensured");
            self.loops = Some(LoopForest::compute(mir, dom));
        }
    }

    fn ensure_liveness(&mut self, mir: &Mir) {
        if self.liveness.is_none() {
            self.liveness = Some(Liveness::compute(mir));
            if cfg!(debug_assertions) {
                self.values_fp = Some(values_fingerprint(mir));
            }
        }
    }

    /// Debug guard: cached results must match the current MIR. A mismatch
    /// means a pass mutated the MIR without invalidating.
    fn debug_check(&self, mir: &Mir) {
        if !cfg!(debug_assertions) {
            return;
        }
        if let Some(fp) = self.cfg_fp {
            assert_eq!(
                fp,
                cfg_shape_fingerprint(mir),
                "MIR control flow changed without invalidation: call \
                 Analyses::invalidate_cfg() (or invalidate_all()) after \
                 mutating terminators, blocks, or phi placement"
            );
        }
        if let Some(fp) = self.values_fp {
            assert_eq!(
                fp,
                values_fingerprint(mir),
                "MIR instructions changed without invalidation: call \
                 Analyses::invalidate_values() (or invalidate_all()) after \
                 mutating instructions, operands, schedules, or temps"
            );
        }
    }
}

fn hash_terminator(term: &Terminator, h: &mut impl Hasher) {
    match term {
        Terminator::Jump(t) => {
            0u8.hash(h);
            t.hash(h);
        }
        Terminator::Branch { cases, default, .. } => {
            1u8.hash(h);
            cases.len().hash(h);
            for (c, t) in cases {
                match c {
                    CaseCond::Int(v) => {
                        0u8.hash(h);
                        v.hash(h);
                    }
                    CaseCond::Float(v) => {
                        1u8.hash(h);
                        v.to_bits().hash(h);
                    }
                }
                t.hash(h);
            }
            default.hash(h);
        }
        Terminator::Exit => 2u8.hash(h),
    }
}

/// Fingerprint of the CFG *shape*: block count and terminator edges. Branch
/// test values are excluded (they do not affect dominators or loops).
fn cfg_shape_fingerprint(mir: &Mir) -> u64 {
    let mut h = std::hash::DefaultHasher::new();
    mir.blocks.len().hash(&mut h);
    for block in &mir.blocks {
        hash_terminator(&block.terminator, &mut h);
    }
    h.finish()
}

fn hash_place(place: &Place, h: &mut impl Hasher) {
    match place.block {
        BlockRef::Concrete(v) => {
            0u8.hash(h);
            v.hash(h);
        }
        BlockRef::Temp(t) => {
            1u8.hash(h);
            t.hash(h);
        }
        BlockRef::Value(v) => {
            2u8.hash(h);
            v.hash(h);
        }
    }
    match place.index {
        IndexRef::Const(v) => {
            0u8.hash(h);
            v.hash(h);
        }
        IndexRef::Value(v) => {
            1u8.hash(h);
            v.hash(h);
        }
    }
    place.offset.hash(h);
}

fn hash_inst(inst: &Inst, h: &mut impl Hasher) {
    match inst {
        Inst::ConstInt(v) => {
            0u8.hash(h);
            v.hash(h);
        }
        Inst::ConstFloat(v) => {
            1u8.hash(h);
            v.to_bits().hash(h);
        }
        Inst::Op {
            op,
            pure_node,
            args,
        } => {
            2u8.hash(h);
            op.hash(h);
            pure_node.hash(h);
            args.hash(h);
        }
        Inst::ShortCircuit {
            op,
            pure_node,
            lhs,
            rhs,
        } => {
            3u8.hash(h);
            op.hash(h);
            pure_node.hash(h);
            lhs.hash(h);
            rhs.hash(h);
        }
        Inst::Load { place } => {
            4u8.hash(h);
            hash_place(place, h);
        }
        Inst::Store { place, value } => {
            5u8.hash(h);
            hash_place(place, h);
            value.hash(h);
        }
        Inst::Phi { args } => {
            6u8.hash(h);
            args.hash(h);
        }
        Inst::Select {
            test,
            then_root,
            else_root,
        } => {
            7u8.hash(h);
            test.hash(h);
            then_root.hash(h);
            else_root.hash(h);
        }
    }
}

/// A full-MIR fingerprint for debug-build consistency checks outside this
/// module (the [`crate::passes`] changed-flag guard). It covers exactly what a
/// pass could mutate — CFG shape, instructions, schedules, phis, branch tests,
/// and temp sizes — by reusing [`values_fingerprint`].
#[cfg(debug_assertions)]
pub fn debug_mir_fingerprint(mir: &Mir) -> u64 {
    values_fingerprint(mir)
}

/// Fingerprint of everything liveness depends on: the shape, plus
/// instructions, schedules, phis, branch tests, and temp sizes.
fn values_fingerprint(mir: &Mir) -> u64 {
    let mut h = std::hash::DefaultHasher::new();
    mir.blocks.len().hash(&mut h);
    for block in &mir.blocks {
        hash_terminator(&block.terminator, &mut h);
        if let Terminator::Branch { test, .. } = &block.terminator {
            test.hash(&mut h);
        }
        block.phis.hash(&mut h);
        block.insts.hash(&mut h);
    }
    mir.insts.len().hash(&mut h);
    for inst in &mir.insts {
        hash_inst(inst, &mut h);
    }
    mir.temps.len().hash(&mut h);
    for t in &mir.temps {
        t.size.hash(&mut h);
    }
    h.finish()
}

/// Test-only CFG-skeleton builder shared by the analysis unit tests.
#[cfg(test)]
pub(crate) mod testutil {
    use crate::mir::{CaseCond, Inst, Mir, Terminator};

    /// Builds a MIR whose blocks have the given successor lists: no
    /// successors = `Exit`, one = `Jump`, several = `Branch` on an
    /// (unscheduled) constant test with `Int` cases and the last successor as
    /// the default.
    pub(crate) fn graph(succs: &[&[usize]]) -> Mir {
        let mut mir = Mir::new();
        for _ in succs {
            mir.push_block();
        }
        for (b, ss) in succs.iter().enumerate() {
            let terminator = match ss {
                [] => Terminator::Exit,
                [t] => Terminator::Jump(*t),
                many => {
                    let test = mir.push_inst(Inst::ConstInt(0));
                    let (default, cases) = many.split_last().expect("len >= 2");
                    Terminator::Branch {
                        test,
                        cases: cases
                            .iter()
                            .enumerate()
                            .map(|(i, &t)| {
                                (CaseCond::Int(i64::try_from(i).expect("small index")), t)
                            })
                            .collect(),
                        default: Some(*default),
                    }
                }
            };
            mir.blocks[b].terminator = terminator;
        }
        mir
    }
}

#[cfg(test)]
mod tests {
    use super::testutil::graph;
    use super::*;
    use crate::mir::{BlockRef, IndexRef, Place, Terminator};

    fn looped_mir() -> Mir {
        // 0 -> 1; 1 -> {1, 2} with a temp load feeding an output store.
        let mut mir = graph(&[&[1], &[1, 2], &[]]);
        let t = mir.push_temp("t", 1);
        let v = mir.push_inst(Inst::Load {
            place: Place {
                block: BlockRef::Temp(t),
                index: IndexRef::Const(0),
                offset: 0,
            },
        });
        mir.blocks[1].insts.push(v);
        let store = mir.push_inst(Inst::Store {
            place: Place {
                block: BlockRef::Concrete(20),
                index: IndexRef::Const(0),
                offset: 0,
            },
            value: v,
        });
        mir.blocks[1].insts.push(store);
        mir
    }

    #[test]
    fn lazy_compute_and_cache() {
        let mir = looped_mir();
        let mut analyses = Analyses::new();
        assert!(analyses.dom.is_none());
        let _ = analyses.dom_tree(&mir);
        assert!(analyses.dom.is_some());
        assert!(analyses.loops.is_none(), "loops not computed yet");
        let _ = analyses.loop_forest(&mir);
        assert!(analyses.loops.is_some());
        let _ = analyses.liveness(&mir);
        assert!(analyses.liveness.is_some());
        let (dom, loops, live) = analyses.all(&mir);
        assert!(dom.is_reachable(2));
        assert_eq!(loops.loops.len(), 1);
        assert!(live.temp_in(1).contains(0));
    }

    #[test]
    fn invalidate_values_keeps_cfg_analyses() {
        let mut mir = looped_mir();
        let mut analyses = Analyses::new();
        let _ = analyses.all(&mir);
        // Mutate an instruction (shape intact): swap the loaded temp index.
        let load = mir.blocks[1].insts[0];
        if let Inst::Load { place } = &mut mir.insts[load as usize] {
            place.offset = 5;
        }
        analyses.invalidate_values();
        assert!(analyses.dom.is_some(), "dominators survive");
        assert!(analyses.loops.is_some(), "loops survive");
        assert!(analyses.liveness.is_none(), "liveness dropped");
        // Both getters succeed (no fingerprint mismatch).
        let _ = analyses.dom_tree(&mir);
        let _ = analyses.liveness(&mir);
    }

    #[test]
    fn invalidate_cfg_drops_everything() {
        let mut mir = looped_mir();
        let mut analyses = Analyses::new();
        let _ = analyses.all(&mir);
        mir.blocks[0].terminator = Terminator::Jump(2);
        analyses.invalidate_cfg();
        assert!(analyses.dom.is_none());
        assert!(analyses.loops.is_none());
        assert!(analyses.liveness.is_none());
        let dom = analyses.dom_tree(&mir);
        assert!(!dom.is_reachable(1), "recomputed against the new CFG");
    }

    #[test]
    #[should_panic(expected = "MIR control flow changed without invalidation")]
    fn forgotten_cfg_invalidation_panics_in_debug() {
        let mut mir = looped_mir();
        let mut analyses = Analyses::new();
        let _ = analyses.dom_tree(&mir);
        mir.blocks[0].terminator = Terminator::Jump(2);
        let _ = analyses.dom_tree(&mir); // no invalidation: must panic
    }

    #[test]
    #[should_panic(expected = "MIR instructions changed without invalidation")]
    fn forgotten_values_invalidation_panics_in_debug() {
        let mut mir = looped_mir();
        let mut analyses = Analyses::new();
        let _ = analyses.liveness(&mir);
        let load = mir.blocks[1].insts[0];
        if let Inst::Load { place } = &mut mir.insts[load as usize] {
            place.block = BlockRef::Concrete(99);
        }
        let _ = analyses.liveness(&mir); // no invalidation: must panic
    }

    #[test]
    fn values_only_change_does_not_trip_cfg_analyses() {
        let mut mir = looped_mir();
        let mut analyses = Analyses::new();
        let _ = analyses.dom_tree(&mir);
        // Instruction mutation does not affect the CFG-shape fingerprint.
        let load = mir.blocks[1].insts[0];
        if let Inst::Load { place } = &mut mir.insts[load as usize] {
            place.offset = 9;
        }
        let _ = analyses.dom_tree(&mir); // fine: shape unchanged
    }
}
