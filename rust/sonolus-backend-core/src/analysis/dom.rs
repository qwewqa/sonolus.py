//! Dominator tree and dominance frontiers (PORT.md T2.1).
//!
//! Implements the iterative algorithm of Cooper, Harvey and Kennedy ("A
//! Simple, Fast Dominance Algorithm"): blocks are numbered by reverse
//! postorder (explicit-stack DFS — invariant §3.4, no recursion), immediate
//! dominators are computed by the two-finger intersection fixpoint, and
//! dominance frontiers by the per-join runner walk. This is the same
//! algorithm family as the legacy `DominanceFrontiers` pass
//! (`sonolus/backend/optimize/dominance.py`); the legacy pass seeds idoms
//! from arbitrary predecessor order while this port walks reverse postorder,
//! which converges in fewer iterations but reaches the identical fixpoint
//! (idoms are unique).
//!
//! # Unreachable blocks
//!
//! Blocks not reachable from the entry (block 0) get no RPO number and no
//! immediate dominator; [`DomTree::dominates`] is `false` whenever either
//! argument is unreachable (including `dominates(b, b)`). Edges *from*
//! unreachable blocks into the reachable region are ignored everywhere
//! (predecessor lists are filtered), matching what any reachability-based
//! consumer must assume.
//!
//! # Determinism
//!
//! The DFS visits successors in terminator order (cases ascending, then the
//! default — the decoded edge order), so RPO numbering, dominator-tree child
//! order (RPO of the child) and frontier member order (RPO of the join block)
//! are all fully determined by the input MIR.
//!
//! # Divergence from legacy
//!
//! Join detection counts distinct predecessor *blocks*, not edges: a block
//! reached only by two parallel edges from one predecessor is not a join
//! (MIR phis are keyed by predecessor block, so it needs no phi either).
//!
//! # The entry's own frontier (CHK sentinel semantics)
//!
//! When the entry has predecessors (back edges into block 0), the runner walk
//! stops at the entry sentinel, so the entry never appears in **its own**
//! dominance frontier — exactly like the published CHK algorithm and the
//! legacy pass (the textbook set definition would include it, since nothing
//! strictly dominates the entry). The entry still appears in the frontiers of
//! the blocks on its back-edge chains. Consumers placing phis from iterated
//! frontiers must special-case defs in an entry that is also a loop header
//! (or split the entry edge first).

use crate::analysis::BitSet;
use crate::mir::{BlockId, Mir};

/// The dominator tree of a [`Mir`]'s CFG, plus dominance frontiers.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DomTree {
    /// Reachable blocks in reverse postorder; `rpo[0]` is the entry.
    rpo: Vec<BlockId>,
    /// Per block: its position in `rpo`, or `usize::MAX` if unreachable.
    num: Vec<usize>,
    /// Per block: immediate dominator. `None` for the entry and for
    /// unreachable blocks.
    idom: Vec<Option<BlockId>>,
    /// Per block: dominator-tree children, ordered by RPO number.
    children: Vec<Vec<BlockId>>,
    /// Per block: dominance frontier, ordered by RPO number of the member.
    frontier: Vec<Vec<BlockId>>,
    /// Per block: distinct *reachable* predecessors, in `(block index, edge
    /// order)` order (the [`Mir::predecessors`] order, filtered).
    preds: Vec<Vec<BlockId>>,
}

const UNREACHABLE: usize = usize::MAX;

impl DomTree {
    /// Computes the dominator tree for `mir` (entry = block 0).
    pub fn compute(mir: &Mir) -> Self {
        let n = mir.blocks.len();
        let rpo = reverse_postorder(mir);
        let mut num = vec![UNREACHABLE; n];
        for (i, &b) in rpo.iter().enumerate() {
            num[b] = i;
        }
        let preds: Vec<Vec<BlockId>> = mir
            .predecessors()
            .into_iter()
            .enumerate()
            .map(|(b, ps)| {
                if num[b] == UNREACHABLE {
                    Vec::new()
                } else {
                    ps.into_iter().filter(|&p| num[p] != UNREACHABLE).collect()
                }
            })
            .collect();

        // Cooper–Harvey–Kennedy idom fixpoint. The entry's idom is itself
        // (sentinel) during computation; exposed as None afterwards.
        let mut idom: Vec<Option<BlockId>> = vec![None; n];
        if let Some(&entry) = rpo.first() {
            idom[entry] = Some(entry);
        }
        let intersect = |mut a: BlockId, mut b: BlockId, idom: &[Option<BlockId>]| -> BlockId {
            while a != b {
                while num[a] > num[b] {
                    a = idom[a].expect("blocks below the entry have provisional idoms");
                }
                while num[b] > num[a] {
                    b = idom[b].expect("blocks below the entry have provisional idoms");
                }
            }
            a
        };
        let mut changed = true;
        while changed {
            changed = false;
            for &b in rpo.iter().skip(1) {
                let mut new_idom: Option<BlockId> = None;
                for &p in &preds[b] {
                    if idom[p].is_none() {
                        continue;
                    }
                    new_idom = Some(match new_idom {
                        None => p,
                        Some(cur) => intersect(p, cur, &idom),
                    });
                }
                if new_idom != idom[b] {
                    idom[b] = new_idom;
                    changed = true;
                }
            }
        }

        // Dominator-tree children in RPO order.
        let mut children: Vec<Vec<BlockId>> = vec![Vec::new(); n];
        for &b in rpo.iter().skip(1) {
            let d = idom[b].expect("reachable non-entry blocks have idoms");
            children[d].push(b);
        }

        // Dominance frontiers (the runner walk). Within one join block `b`,
        // only `b` is ever appended, so the `last() == b` check dedups.
        let mut frontier: Vec<Vec<BlockId>> = vec![Vec::new(); n];
        for &b in &rpo {
            if preds[b].len() < 2 {
                continue;
            }
            let stop = idom[b].expect("joins have idoms (entry included via sentinel)");
            for &p in &preds[b] {
                let mut runner = p;
                while runner != stop {
                    if frontier[runner].last() != Some(&b) {
                        frontier[runner].push(b);
                    }
                    runner = idom[runner].expect("runner stays on the entry's idom chain");
                }
            }
        }

        // Drop the entry sentinel.
        if let Some(&entry) = rpo.first() {
            idom[entry] = None;
        }
        Self {
            rpo,
            num,
            idom,
            children,
            frontier,
            preds,
        }
    }

    /// Reachable blocks in reverse postorder (entry first).
    pub fn rpo(&self) -> &[BlockId] {
        &self.rpo
    }

    /// The block's RPO number, or `None` if unreachable from the entry.
    pub fn rpo_number(&self, b: BlockId) -> Option<usize> {
        (self.num[b] != UNREACHABLE).then_some(self.num[b])
    }

    pub fn is_reachable(&self, b: BlockId) -> bool {
        self.num[b] != UNREACHABLE
    }

    /// The immediate dominator: `None` for the entry and unreachable blocks.
    pub fn idom(&self, b: BlockId) -> Option<BlockId> {
        self.idom[b]
    }

    /// Does `a` dominate `b` (reflexively)? `false` if either is unreachable.
    pub fn dominates(&self, a: BlockId, b: BlockId) -> bool {
        if !self.is_reachable(a) || !self.is_reachable(b) {
            return false;
        }
        // Walk b's idom chain; idoms have strictly smaller RPO numbers, so
        // stop as soon as we pass a's number.
        let target = self.num[a];
        let mut cur = b;
        while self.num[cur] > target {
            match self.idom[cur] {
                Some(d) => cur = d,
                None => return false,
            }
        }
        cur == a
    }

    /// Dominator-tree children of `b`, ordered by RPO number.
    pub fn children(&self, b: BlockId) -> &[BlockId] {
        &self.children[b]
    }

    /// The dominance frontier of `b`, ordered by RPO number of the member.
    /// Needed by SSA construction (phi placement, W2).
    pub fn frontier(&self, b: BlockId) -> &[BlockId] {
        &self.frontier[b]
    }

    /// Distinct reachable predecessors of `b` (deterministic order). Empty
    /// for unreachable blocks.
    pub fn preds(&self, b: BlockId) -> &[BlockId] {
        &self.preds[b]
    }
}

/// Reachable blocks in reverse postorder via explicit-stack DFS (successors
/// in terminator order).
fn reverse_postorder(mir: &Mir) -> Vec<BlockId> {
    let n = mir.blocks.len();
    if n == 0 {
        return Vec::new();
    }
    let succs: Vec<Vec<BlockId>> = mir
        .blocks
        .iter()
        .map(|b| b.terminator.successors().collect())
        .collect();
    let mut visited = BitSet::new(n);
    let mut postorder: Vec<BlockId> = Vec::new();
    // (block, index of the next successor to visit)
    let mut stack: Vec<(BlockId, usize)> = vec![(0, 0)];
    visited.insert(0);
    while let Some(top) = stack.last_mut() {
        let (b, i) = *top;
        if i < succs[b].len() {
            top.1 += 1;
            let s = succs[b][i];
            if !visited.contains(s) {
                visited.insert(s);
                stack.push((s, 0));
            }
        } else {
            postorder.push(b);
            stack.pop();
        }
    }
    postorder.reverse();
    postorder
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::analysis::testutil::graph;

    fn frontier_of(dt: &DomTree, b: BlockId) -> Vec<BlockId> {
        dt.frontier(b).to_vec()
    }

    #[test]
    fn straight_line() {
        // 0 -> 1 -> 2
        let mir = graph(&[&[1], &[2], &[]]);
        let dt = DomTree::compute(&mir);
        assert_eq!(dt.rpo(), &[0, 1, 2]);
        assert_eq!(dt.idom(0), None);
        assert_eq!(dt.idom(1), Some(0));
        assert_eq!(dt.idom(2), Some(1));
        assert!(dt.dominates(0, 2));
        assert!(dt.dominates(1, 2));
        assert!(dt.dominates(2, 2));
        assert!(!dt.dominates(2, 1));
        for b in 0..3 {
            assert!(frontier_of(&dt, b).is_empty());
        }
        assert_eq!(dt.children(0), &[1]);
        assert_eq!(dt.children(1), &[2]);
    }

    #[test]
    fn diamond() {
        // 0 -> {1, 2}; 1 -> 3; 2 -> 3
        let mir = graph(&[&[1, 2], &[3], &[3], &[]]);
        let dt = DomTree::compute(&mir);
        assert_eq!(dt.idom(1), Some(0));
        assert_eq!(dt.idom(2), Some(0));
        assert_eq!(dt.idom(3), Some(0), "the join is dominated by the fork");
        assert!(!dt.dominates(1, 3));
        assert!(!dt.dominates(2, 3));
        assert_eq!(frontier_of(&dt, 1), vec![3]);
        assert_eq!(frontier_of(&dt, 2), vec![3]);
        assert!(frontier_of(&dt, 0).is_empty());
        assert!(frontier_of(&dt, 3).is_empty());
        // Children of 0 in RPO order. The DFS finishes 1's subtree (incl. 3)
        // before visiting 2, so the postorder is [3, 1, 2, 0] and the RPO is
        // [0, 2, 1, 3].
        assert_eq!(dt.rpo(), &[0, 2, 1, 3]);
        assert_eq!(dt.children(0), &[2, 1, 3]);
    }

    #[test]
    fn nested_loops() {
        // 0 -> 1 (outer header) -> 2 (inner header) -> 3 -> {2, 4};
        // 4 -> {1, 5}; 5 exit.
        let mir = graph(&[&[1], &[2], &[3], &[2, 4], &[1, 5], &[]]);
        let dt = DomTree::compute(&mir);
        assert_eq!(
            (0..6).map(|b| dt.idom(b)).collect::<Vec<_>>(),
            vec![None, Some(0), Some(1), Some(2), Some(3), Some(4)]
        );
        // Frontiers: the loop headers are in the frontiers of their bodies
        // (members in RPO order: header 1 has RPO 1, header 2 has RPO 2).
        assert_eq!(frontier_of(&dt, 1), vec![1]);
        assert_eq!(frontier_of(&dt, 2), vec![1, 2]);
        assert_eq!(frontier_of(&dt, 3), vec![1, 2]);
        assert_eq!(frontier_of(&dt, 4), vec![1]);
        assert!(frontier_of(&dt, 0).is_empty());
        assert!(frontier_of(&dt, 5).is_empty());
    }

    #[test]
    fn self_loop() {
        // 0 -> 1; 1 -> {1, 2}
        let mir = graph(&[&[1], &[1, 2], &[]]);
        let dt = DomTree::compute(&mir);
        assert_eq!(dt.idom(1), Some(0));
        assert_eq!(dt.idom(2), Some(1));
        assert_eq!(frontier_of(&dt, 1), vec![1], "self-loop is in its own DF");
    }

    #[test]
    fn irreducible_two_entry_loop() {
        // 0 -> {1, 2}; 1 -> 2; 2 -> 1: neither loop block dominates the other.
        let mir = graph(&[&[1, 2], &[2], &[1]]);
        let dt = DomTree::compute(&mir);
        assert_eq!(dt.idom(1), Some(0));
        assert_eq!(dt.idom(2), Some(0));
        assert!(!dt.dominates(1, 2));
        assert!(!dt.dominates(2, 1));
        assert_eq!(frontier_of(&dt, 1), vec![2]);
        assert_eq!(frontier_of(&dt, 2), vec![1]);
    }

    #[test]
    fn unreachable_blocks() {
        // 0 -> 1; 2 and 3 unreachable, 3 even points into the reachable part.
        let mir = graph(&[&[1], &[], &[3], &[1]]);
        let dt = DomTree::compute(&mir);
        assert_eq!(dt.rpo(), &[0, 1]);
        assert!(!dt.is_reachable(2));
        assert!(!dt.is_reachable(3));
        assert_eq!(dt.idom(2), None);
        assert_eq!(dt.idom(3), None);
        assert!(!dt.dominates(3, 1));
        assert!(!dt.dominates(2, 2), "unreachable blocks dominate nothing");
        // The unreachable 3 -> 1 edge must not make 1 a join.
        assert_eq!(dt.preds(1), &[0]);
        assert!(frontier_of(&dt, 0).is_empty());
        assert_eq!(dt.idom(1), Some(0));
    }

    #[test]
    fn multiple_exits() {
        // 0 -> {1, 2}; both exit separately.
        let mir = graph(&[&[1, 2], &[], &[]]);
        let dt = DomTree::compute(&mir);
        assert_eq!(dt.idom(1), Some(0));
        assert_eq!(dt.idom(2), Some(0));
        for b in 0..3 {
            assert!(frontier_of(&dt, b).is_empty());
        }
    }

    #[test]
    fn back_edge_to_entry() {
        // 0 -> 1; 1 -> {0, 2}: the entry has a predecessor.
        let mir = graph(&[&[1], &[0, 2], &[]]);
        let dt = DomTree::compute(&mir);
        assert_eq!(dt.idom(1), Some(0));
        assert_eq!(dt.idom(2), Some(1));
        // The entry has a single pred, so it is not a join; legacy-equivalent
        // behavior (entry never appears in its own frontier).
        assert!(frontier_of(&dt, 0).is_empty());
        assert_eq!(frontier_of(&dt, 1), Vec::<BlockId>::new());
    }

    #[test]
    fn entry_join_is_not_in_its_own_frontier() {
        // 0 -> {1, 2}; 1 -> 0; 2 -> 0: the entry is a join. CHK sentinel
        // semantics (module docs): 0 lands in the frontiers of its back-edge
        // sources but never in its own.
        let mir = graph(&[&[1, 2], &[0], &[0]]);
        let dt = DomTree::compute(&mir);
        assert_eq!(dt.preds(0), &[1, 2]);
        assert_eq!(frontier_of(&dt, 1), vec![0]);
        assert_eq!(frontier_of(&dt, 2), vec![0]);
        assert!(frontier_of(&dt, 0).is_empty());
    }

    #[test]
    fn empty_and_single_block() {
        let dt = DomTree::compute(&Mir::new());
        assert!(dt.rpo().is_empty());

        let mir = graph(&[&[]]);
        let dt = DomTree::compute(&mir);
        assert_eq!(dt.rpo(), &[0]);
        assert_eq!(dt.idom(0), None);
        assert!(dt.dominates(0, 0));
    }

    #[test]
    fn deterministic_and_deep_cfg_is_iterative() {
        // A 200k-block chain: explicit-stack DFS must not overflow.
        let n = 200_000;
        let mut mir = Mir::new();
        for _ in 0..n {
            mir.push_block();
        }
        for b in 0..n - 1 {
            mir.blocks[b].terminator = crate::mir::Terminator::Jump(b + 1);
        }
        let dt = DomTree::compute(&mir);
        assert_eq!(dt.rpo().len(), n);
        assert_eq!(dt.idom(n - 1), Some(n - 2));
        assert!(dt.dominates(0, n - 1));
        let dt2 = DomTree::compute(&mir);
        assert_eq!(dt, dt2, "computation is deterministic");
    }
}
