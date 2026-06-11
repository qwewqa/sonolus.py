//! Natural-loop forest (PORT.md T2.1).
//!
//! A *back edge* is a CFG edge `latch -> header` whose target dominates its
//! source. Each header with at least one back edge defines one natural loop
//! (multiple back edges to the same header merge into a single loop, LLVM
//! style); the body is the header plus every block that reaches a latch
//! without passing through the header (backward reachability over reachable
//! predecessors, explicit work stack — invariant §3.4).
//!
//! # Irreducible control flow (the contract)
//!
//! Frontend CFGs can be irreducible (arbitrary `JumpLoop`-style flow). This
//! analysis performs **natural-loop detection only**: a retreating edge whose
//! target does *not* dominate its source is not a back edge, so an
//! irreducible region (e.g. a two-entry loop) simply produces **no loop** —
//! its blocks report depth 0 / no containing loop unless they also sit inside
//! an enclosing natural loop. Detection never crashes or diverges on
//! irreducible, self-looping, or otherwise weird-but-valid CFGs; passes that
//! consume the forest (LICM, unrolling) must treat "no loop" as "do not
//! touch", which is always sound.
//!
//! # Nesting and determinism
//!
//! For natural loops with distinct headers, two bodies are either disjoint or
//! strictly nested, so parenthood is well defined: the parent of a loop is
//! the innermost *other* loop whose body contains its header. Loops are
//! ordered by the RPO number of their header — an outer loop's header
//! dominates an inner loop's header, so parents always precede children and
//! `parent < child` holds on loop ids (the forest is acyclic by
//! construction). Bodies and latch lists are ordered by RPO number; the
//! header is always `body[0]`.

use crate::analysis::{BitSet, DomTree};
use crate::mir::{BlockId, Mir};

/// Index into [`LoopForest::loops`].
pub type LoopId = usize;

/// One natural loop.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Loop {
    pub header: BlockId,
    /// Body blocks ordered by RPO number; `body[0]` is the header.
    pub body: Vec<BlockId>,
    /// Back-edge sources, ordered by RPO number.
    pub latches: Vec<BlockId>,
    /// The innermost enclosing loop. Always `parent < self`'s id.
    pub parent: Option<LoopId>,
    /// Directly nested loops, in loop-id (= header RPO) order.
    pub children: Vec<LoopId>,
    /// Nesting depth: 1 for top-level loops.
    pub depth: u32,
}

/// The natural-loop forest of a [`Mir`]'s CFG.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LoopForest {
    /// All loops, ordered by the RPO number of their header (parents before
    /// children).
    pub loops: Vec<Loop>,
    /// Per block: the innermost containing loop.
    loop_of: Vec<Option<LoopId>>,
    /// Per loop: body membership bitset (for O(1) `contains`).
    body_sets: Vec<BitSet>,
}

impl LoopForest {
    /// Computes the loop forest from the dominator tree's CFG view.
    pub fn compute(mir: &Mir, dom: &DomTree) -> Self {
        let n = mir.blocks.len();

        // Back edges grouped by header, headers in RPO order: iterate latch
        // candidates in RPO and bucket per header, then sort buckets by the
        // header's RPO number.
        let mut latches_of: Vec<Vec<BlockId>> = vec![Vec::new(); n];
        let mut headers: Vec<BlockId> = Vec::new();
        for &u in dom.rpo() {
            for h in mir.blocks[u].terminator.successors() {
                if dom.dominates(h, u) && latches_of[h].last() != Some(&u) {
                    if latches_of[h].is_empty() {
                        headers.push(h);
                    }
                    latches_of[h].push(u);
                }
            }
        }
        headers.sort_by_key(|&h| dom.rpo_number(h).expect("headers are reachable"));

        // Bodies via backward reachability from the latches, stopping at the
        // header.
        let mut loops: Vec<Loop> = Vec::with_capacity(headers.len());
        let mut body_sets: Vec<BitSet> = Vec::with_capacity(headers.len());
        for &h in &headers {
            let mut body = BitSet::new(n);
            body.insert(h);
            let mut stack: Vec<BlockId> = Vec::new();
            for &latch in &latches_of[h] {
                if !body.contains(latch) {
                    body.insert(latch);
                    stack.push(latch);
                }
            }
            while let Some(b) = stack.pop() {
                for &p in dom.preds(b) {
                    if !body.contains(p) {
                        body.insert(p);
                        stack.push(p);
                    }
                }
            }
            let mut body_blocks: Vec<BlockId> = body.iter().collect();
            body_blocks.sort_by_key(|&b| dom.rpo_number(b).expect("loop bodies are reachable"));
            let mut latches = latches_of[h].clone();
            latches.sort_by_key(|&b| dom.rpo_number(b).expect("latches are reachable"));
            loops.push(Loop {
                header: h,
                body: body_blocks,
                latches,
                parent: None,
                children: Vec::new(),
                depth: 0,
            });
            body_sets.push(body);
        }

        // Nesting: the parent is the innermost other loop containing the
        // header. Containing loops have dominating (smaller-RPO) headers, so
        // scanning earlier loops backward finds the innermost first.
        for i in 0..loops.len() {
            let header = loops[i].header;
            let parent = (0..i).rev().find(|&j| body_sets[j].contains(header));
            loops[i].parent = parent;
            loops[i].depth = match parent {
                Some(p) => {
                    loops[p].children.push(i);
                    loops[p].depth + 1
                }
                None => 1,
            };
        }

        // Innermost loop per block: write outer-first; inner overwrites.
        let mut loop_of: Vec<Option<LoopId>> = vec![None; n];
        for (i, l) in loops.iter().enumerate() {
            for &b in &l.body {
                loop_of[b] = Some(i);
            }
        }

        Self {
            loops,
            loop_of,
            body_sets,
        }
    }

    /// The innermost loop containing `b`, if any.
    pub fn loop_of(&self, b: BlockId) -> Option<LoopId> {
        self.loop_of[b]
    }

    /// Loop-nesting depth of `b` (0 = not in any loop).
    pub fn depth(&self, b: BlockId) -> u32 {
        self.loop_of[b].map_or(0, |l| self.loops[l].depth)
    }

    /// Is `b` in the body of loop `l` (including nested loops' blocks)?
    pub fn contains(&self, l: LoopId, b: BlockId) -> bool {
        self.body_sets[l].contains(b)
    }

    /// Top-level loops, in loop-id order.
    pub fn roots(&self) -> impl Iterator<Item = LoopId> + '_ {
        self.loops
            .iter()
            .enumerate()
            .filter(|(_, l)| l.parent.is_none())
            .map(|(i, _)| i)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::analysis::testutil::graph;

    fn forest(succs: &[&[usize]]) -> (Mir, DomTree, LoopForest) {
        let mir = graph(succs);
        let dom = DomTree::compute(&mir);
        let forest = LoopForest::compute(&mir, &dom);
        (mir, dom, forest)
    }

    #[test]
    fn straight_line_has_no_loops() {
        let (_, _, f) = forest(&[&[1], &[2], &[]]);
        assert!(f.loops.is_empty());
        for b in 0..3 {
            assert_eq!(f.loop_of(b), None);
            assert_eq!(f.depth(b), 0);
        }
    }

    #[test]
    fn simple_loop() {
        // 0 -> 1; 1 -> 2; 2 -> {1, 3}
        let (_, _, f) = forest(&[&[1], &[2], &[1, 3], &[]]);
        assert_eq!(f.loops.len(), 1);
        let l = &f.loops[0];
        assert_eq!(l.header, 1);
        assert_eq!(l.body, vec![1, 2]);
        assert_eq!(l.latches, vec![2]);
        assert_eq!(l.parent, None);
        assert_eq!(l.depth, 1);
        assert_eq!(f.depth(0), 0);
        assert_eq!(f.depth(1), 1);
        assert_eq!(f.depth(2), 1);
        assert_eq!(f.depth(3), 0);
        assert_eq!(f.roots().collect::<Vec<_>>(), vec![0]);
    }

    #[test]
    fn nested_loops() {
        // 0 -> 1 (outer hdr) -> 2 (inner hdr) -> 3 -> {2, 4}; 4 -> {1, 5}; 5.
        let (_, _, f) = forest(&[&[1], &[2], &[3], &[2, 4], &[1, 5], &[]]);
        assert_eq!(f.loops.len(), 2);
        let outer = &f.loops[0];
        let inner = &f.loops[1];
        assert_eq!(outer.header, 1);
        assert_eq!(outer.body, vec![1, 2, 3, 4]);
        assert_eq!(outer.latches, vec![4]);
        assert_eq!(outer.parent, None);
        assert_eq!(outer.children, vec![1]);
        assert_eq!(outer.depth, 1);
        assert_eq!(inner.header, 2);
        assert_eq!(inner.body, vec![2, 3]);
        assert_eq!(inner.latches, vec![3]);
        assert_eq!(inner.parent, Some(0));
        assert_eq!(inner.depth, 2);
        assert_eq!(
            (0..6).map(|b| f.depth(b)).collect::<Vec<_>>(),
            vec![0, 1, 2, 2, 1, 0]
        );
        assert_eq!(f.loop_of(3), Some(1), "innermost loop wins");
        assert!(f.contains(0, 3), "outer body contains inner blocks");
    }

    #[test]
    fn self_loop() {
        // 0 -> 1; 1 -> {1, 2}
        let (_, _, f) = forest(&[&[1], &[1, 2], &[]]);
        assert_eq!(f.loops.len(), 1);
        let l = &f.loops[0];
        assert_eq!(l.header, 1);
        assert_eq!(l.body, vec![1]);
        assert_eq!(l.latches, vec![1]);
        assert_eq!(f.depth(1), 1);
    }

    #[test]
    fn self_loop_on_entry() {
        // 0 -> {0, 1}
        let (_, _, f) = forest(&[&[0, 1], &[]]);
        assert_eq!(f.loops.len(), 1);
        assert_eq!(f.loops[0].header, 0);
        assert_eq!(f.loops[0].body, vec![0]);
    }

    #[test]
    fn irreducible_two_entry_loop_produces_no_loop() {
        // 0 -> {1, 2}; 1 -> 2; 2 -> 1: a cycle with two entries. Neither
        // 1 nor 2 dominates the other, so there is no back edge — the
        // documented contract: irreducible regions produce no loop.
        let (_, _, f) = forest(&[&[1, 2], &[2], &[1]]);
        assert!(f.loops.is_empty());
        for b in 0..3 {
            assert_eq!(f.depth(b), 0);
        }
    }

    #[test]
    fn natural_loop_with_irreducible_sibling() {
        // A natural loop (4 -> 5 -> {4, ...}) next to an irreducible region
        // (1 <-> 2 entered from both sides): only the natural loop is found.
        // 0 -> {1, 2, 4}; 1 -> 2; 2 -> 1; 4 -> 5; 5 -> {4, 3}; 3 exit.
        let (_, _, f) = forest(&[&[1, 2, 4], &[2], &[1], &[], &[5], &[4, 3]]);
        assert_eq!(f.loops.len(), 1);
        assert_eq!(f.loops[0].header, 4);
        assert_eq!(f.loops[0].body, vec![4, 5]);
        assert_eq!(f.depth(1), 0);
        assert_eq!(f.depth(2), 0);
    }

    #[test]
    fn two_back_edges_merge_into_one_loop() {
        // 0 -> 1; 1 -> {2, 3}; 2 -> 1; 3 -> {1, 4}: two latches, one loop.
        let (_, _, f) = forest(&[&[1], &[2, 3], &[1], &[1, 4], &[]]);
        assert_eq!(f.loops.len(), 1);
        let l = &f.loops[0];
        assert_eq!(l.header, 1);
        assert_eq!(l.latches.len(), 2);
        assert!(l.latches.contains(&2) && l.latches.contains(&3));
        assert_eq!(l.body.len(), 3);
    }

    #[test]
    fn sibling_loops() {
        // 0 -> 1; 1 -> {1', 2}... two sequential loops:
        // 0 -> 1; 1 -> {1, 2}; 2 -> {2, 3}; 3 exit.
        let (_, _, f) = forest(&[&[1], &[1, 2], &[2, 3], &[]]);
        assert_eq!(f.loops.len(), 2);
        assert_eq!(f.loops[0].header, 1);
        assert_eq!(f.loops[1].header, 2);
        assert_eq!(f.loops[0].parent, None);
        assert_eq!(f.loops[1].parent, None);
        assert_eq!(f.roots().collect::<Vec<_>>(), vec![0, 1]);
    }

    #[test]
    fn unreachable_cycle_produces_no_loop() {
        // 0 -> 1; unreachable 2 <-> 3 cycle.
        let (_, _, f) = forest(&[&[1], &[], &[3], &[2]]);
        assert!(f.loops.is_empty());
    }

    #[test]
    fn deterministic() {
        let succs: &[&[usize]] = &[&[1], &[2], &[3], &[2, 4], &[1, 5], &[]];
        let (mir, dom, f1) = forest(succs);
        let f2 = LoopForest::compute(&mir, &dom);
        assert_eq!(f1, f2);
    }
}
