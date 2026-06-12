//! Post-out-of-SSA copy coalescing and copy-only-block threading (PORT.md
//! T3.5). **Not a registry pass**: this is the second half of the Boissinot
//! out-of-SSA coalescing, invoked by [`crate::ssa::destruct_ssa`] only when
//! destruction actually rewrote SSA values — it never runs on MIR that was
//! already in lowerable form, so the `minimal` baseline is untouched by
//! construction.
//!
//! # What it cleans up
//!
//! `destruct_ssa`'s value-class coalescing eliminates copies *within* a
//! congruence class, but three temp-to-temp copy shapes survive it (the T3.4
//! worklog's reported wastes):
//!
//! - **Residual parallel copies** between phi classes whose value-level union
//!   was blocked (all-or-nothing class merging, plus the deliberately
//!   conservative phi-interference extensions).
//! - **GVN leader temps** (`gvnN`): when a CSE leader value is also slotted
//!   (phi argument / cross-block / multi-use), its class store is followed by
//!   `Load ssa.k; Store gvnN` — the same value stored twice.
//! - **`Mem2Reg` reroute temps** (`m2rN`): a lazy-tree load of a promoted slot
//!   reads `m2rN`, fed by `Store m2rN <- value`; when `value` is slotted the
//!   store becomes the same `Load ssa.k; Store m2rN` copy.
//!
//! # How
//!
//! 1. **Candidates**: adjacent `l = Load tA[0]; Store tB[0] <- l` pairs of
//!    size-1 temps (constant index 0, offset 0 on both — the only shape
//!    `destruct_ssa` and the optimizer passes emit), where the load's only
//!    use is the store. Collected in deterministic block/schedule order.
//! 2. **Merging**: union-find over temps. A candidate pair merges iff the two
//!    (possibly already merged) classes do not interfere in the
//!    temp-granularity interference graph ([`crate::alloc::temp_interference`]
//!    — the exact graph slot allocation colors). Non-adjacency there means
//!    the allocator could have given both temps one slot, in which case the
//!    copy reads and writes the same cell; soundness is precisely the
//!    allocator's own slot-sharing argument (every store to a temp cliques
//!    against everything live after it, including uninitialized-read ranges,
//!    so a merged class can never clobber a live value of its other members).
//!    Merged classes union their adjacency sets (conservative). The class
//!    representative is the smallest temp index (deterministic).
//! 3. **Rewrite**: every `BlockRef::Temp` in the arena is redirected to its
//!    class representative.
//! 4. **Self-copy removal**: adjacent `l = Load t[0]; Store t[0] <- l` pairs
//!    (same temp after the rewrite, load used only by the store) are
//!    deleted — a load of an in-bounds constant-index temp cell cannot trap,
//!    and writing the just-read value back is a no-op.
//! 5. **Threading**: edge-split blocks (created by `destruct_ssa` for
//!    parallel copies) whose copies were all coalesced away are now empty
//!    `Jump` blocks costing one dispatcher round trip each; every predecessor
//!    edge into such a block is retargeted to its jump target (conditions and
//!    edge order untouched), leaving the block unreachable (lowering drops
//!    unreachable blocks). Only blocks the split step created are threaded —
//!    pre-existing empty blocks are pass-pipeline business, not lowering's.
//!
//! Behavioral equivalence: temp memory (block 10000) is excluded from the
//! behavioral contract (PORT.md T3.4, `diff.rs`), so merging temp storage and
//! dropping no-op copies is observable only through eval/dispatch counts.
//!
//! # Determinism and iteration
//!
//! Candidates, unions, rewrites, and threading all walk blocks and schedules
//! in index order; the union-find representative is the minimum temp index;
//! adjacency sets are queried for membership only. No recursion.

use std::collections::HashMap;

use crate::alloc::temp_interference;
use crate::mir::{BlockId, BlockRef, IndexRef, Inst, Mir, Terminator, Value};

/// What [`coalesce_and_thread`] did (test introspection).
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub(crate) struct CoalesceStats {
    /// Copy-related temp classes merged.
    pub merged: usize,
    /// Self-copy `Load t; Store t` pairs removed from schedules.
    pub removed_copies: usize,
    /// Empty split blocks threaded out.
    pub threaded_blocks: usize,
}

/// Runs the full post-destruction cleanup (module docs): coalesce copy-related
/// temps, drop the self-copies that produces, and thread split blocks that
/// ended up empty.
pub(crate) fn coalesce_and_thread(mir: &mut Mir, split_blocks: &[BlockId]) -> CoalesceStats {
    let merged = coalesce_temp_copies(mir);
    let removed_copies = remove_self_copies(mir);
    let threaded_blocks = thread_empty_split_blocks(mir, split_blocks);
    CoalesceStats {
        merged,
        removed_copies,
        threaded_blocks,
    }
}

/// `Some((src, dst))` when `load_v; store_v` is a scalar temp-to-temp copy:
/// `load_v = Load src[0]` (const index 0, offset 0, size-1 temp) consumed by
/// `store_v = Store dst[0] <- load_v` of the same shape.
fn scalar_copy(mir: &Mir, load_v: Value, store_v: Value) -> Option<(usize, usize)> {
    let Inst::Load { place: lp } = mir.inst(load_v) else {
        return None;
    };
    let Inst::Store { place: sp, value } = mir.inst(store_v) else {
        return None;
    };
    if *value != load_v {
        return None;
    }
    let (BlockRef::Temp(src), IndexRef::Const(0), 0) = (lp.block, lp.index, lp.offset) else {
        return None;
    };
    let (BlockRef::Temp(dst), IndexRef::Const(0), 0) = (sp.block, sp.index, sp.offset) else {
        return None;
    };
    (mir.temps[src].size == 1 && mir.temps[dst].size == 1).then_some((src, dst))
}

/// Use counts over scheduled references: operands of scheduled instructions,
/// lazy-tree interiors, and terminator tests. Post-destruction MIR has no
/// phis; phi arguments are counted anyway for robustness on hand-built MIR.
fn count_uses(mir: &Mir) -> Vec<u32> {
    let scheduled = mir.scheduled_mask();
    let mut counts = vec![0u32; mir.insts.len()];
    let mut lazy_stack: Vec<Value> = Vec::new();
    for block in &mir.blocks {
        for &phi in &block.phis {
            if let Inst::Phi { args } = mir.inst(phi) {
                for &(_, a) in args {
                    counts[a as usize] += 1;
                }
            }
        }
        for &v in &block.insts {
            let inst = mir.inst(v);
            Mir::for_each_operand(inst, |o| counts[o as usize] += 1);
            Mir::for_each_lazy_root(inst, |root| lazy_stack.push(root));
            while let Some(lv) = lazy_stack.pop() {
                if scheduled[lv as usize] || mir.is_const(lv) {
                    continue;
                }
                Mir::for_each_operand(mir.inst(lv), |o| {
                    counts[o as usize] += 1;
                    lazy_stack.push(o);
                });
            }
        }
        if let Terminator::Branch { test, .. } = &block.terminator {
            counts[*test as usize] += 1;
        }
    }
    counts
}

/// Iterative union-find lookup over the parent table.
fn find(parent: &[usize], mut t: usize) -> usize {
    while parent[t] != t {
        t = parent[t];
    }
    t
}

/// Phase 1–3 of the module docs: merge copy-related non-interfering temps and
/// redirect every temp reference to its class representative. Returns the
/// number of merges performed.
fn coalesce_temp_copies(mir: &mut Mir) -> usize {
    // 1. Candidates, in block/schedule order.
    let counts = count_uses(mir);
    let mut candidates: Vec<(usize, usize)> = Vec::new();
    for block in &mir.blocks {
        for w in block.insts.windows(2) {
            if let Some((src, dst)) = scalar_copy(mir, w[0], w[1])
                && counts[w[0] as usize] == 1
                && src != dst
            {
                candidates.push((src, dst));
            }
        }
    }
    if candidates.is_empty() {
        return 0;
    }

    // 2. Interference-checked unions.
    let graph = temp_interference(mir);
    let mut parent: Vec<usize> = (0..mir.temps.len()).collect();
    // Members and merged adjacency live at the class representative.
    let mut members: Vec<Vec<usize>> = (0..mir.temps.len()).map(|t| vec![t]).collect();
    let mut adj = graph.adj;
    let mut merged = 0usize;
    for &(a, b) in &candidates {
        let (ra, rb) = (find(&parent, a), find(&parent, b));
        if ra == rb {
            continue;
        }
        let interferes = members[rb]
            .iter()
            .any(|&t| adj[ra].contains(&u32::try_from(t).expect("temp id fits u32")));
        if interferes {
            continue;
        }
        // Deterministic: the smaller temp index wins.
        let (winner, loser) = if ra < rb { (ra, rb) } else { (rb, ra) };
        parent[loser] = winner;
        let moved_members = std::mem::take(&mut members[loser]);
        members[winner].extend(moved_members);
        let moved_adj = std::mem::take(&mut adj[loser]);
        adj[winner].extend(moved_adj);
        merged += 1;
    }
    if merged == 0 {
        return 0;
    }

    // 3. Redirect every temp reference (the whole arena: lazy-tree loads are
    // unscheduled instructions; dead instructions are harmless to rewrite).
    for inst in &mut mir.insts {
        let (Inst::Load { place } | Inst::Store { place, .. }) = inst else {
            continue;
        };
        if let BlockRef::Temp(t) = place.block {
            let root = find(&parent, t);
            if root != t {
                place.block = BlockRef::Temp(root);
            }
        }
    }
    merged
}

/// Phase 4 of the module docs: delete adjacent `l = Load t[0]; Store t[0] <- l`
/// pairs (the load consumed only by the store). Returns the number of pairs
/// removed.
fn remove_self_copies(mir: &mut Mir) -> usize {
    let counts = count_uses(mir);
    let mut removed = 0usize;
    for b in 0..mir.blocks.len() {
        let insts = &mir.blocks[b].insts;
        let mut keep: Vec<Value> = Vec::with_capacity(insts.len());
        let mut i = 0;
        while i < insts.len() {
            if i + 1 < insts.len()
                && let Some((src, dst)) = scalar_copy(mir, insts[i], insts[i + 1])
                && src == dst
                && counts[insts[i] as usize] == 1
            {
                removed += 1;
                i += 2;
                continue;
            }
            keep.push(insts[i]);
            i += 1;
        }
        if keep.len() != insts.len() {
            mir.blocks[b].insts = keep;
        }
    }
    removed
}

/// Phase 5 of the module docs: retarget predecessor edges around split blocks
/// that hold no instructions. Returns the number of blocks threaded out.
fn thread_empty_split_blocks(mir: &mut Mir, split_blocks: &[BlockId]) -> usize {
    let mut redirect: HashMap<BlockId, BlockId> = HashMap::new();
    for &s in split_blocks {
        if mir.blocks[s].insts.is_empty()
            && mir.blocks[s].phis.is_empty()
            && let Terminator::Jump(t) = mir.blocks[s].terminator
        {
            // Split blocks jump to pre-existing phi blocks, never to other
            // split blocks — no redirect chains can form.
            debug_assert!(!split_blocks.contains(&t));
            redirect.insert(s, t);
        }
    }
    if redirect.is_empty() {
        return 0;
    }
    for block in &mut mir.blocks {
        match &mut block.terminator {
            Terminator::Jump(t) => {
                if let Some(&n) = redirect.get(t) {
                    *t = n;
                }
            }
            Terminator::Branch { cases, default, .. } => {
                for (_, t) in cases.iter_mut() {
                    if let Some(&n) = redirect.get(t) {
                        *t = n;
                    }
                }
                if let Some(d) = default
                    && let Some(&n) = redirect.get(d)
                {
                    *d = n;
                }
            }
            Terminator::Exit => {}
        }
    }
    redirect.len()
}

#[cfg(test)]
mod tests {
    // Toolchain note: clippy 1.96 newly lints test code under --all-targets;
    // exact f64 equality is the assertion contract here (ARCHITECTURE §6).
    #![allow(clippy::float_cmp, clippy::too_many_lines)]
    use super::*;
    use crate::alloc::allocate_temps;
    use crate::interpret::Interpreter;
    use crate::lower::lower_mir;
    use crate::mir::{IndexRef, Place, TempId};
    use crate::ops::Op;

    fn temp_place(t: TempId) -> Place {
        Place {
            block: BlockRef::Temp(t),
            index: IndexRef::Const(0),
            offset: 0,
        }
    }

    fn concrete_place(block: i64, index: i64) -> Place {
        Place {
            block: BlockRef::Concrete(block),
            index: IndexRef::Const(index),
            offset: 0,
        }
    }

    fn sched(mir: &mut Mir, block: usize, inst: Inst) -> Value {
        let v = mir.push_inst(inst);
        mir.blocks[block].insts.push(v);
        v
    }

    fn run_mir(mir: &Mir, inputs: &[(i64, Vec<f64>)]) -> Interpreter {
        let alloc = allocate_temps(mir).expect("allocation succeeds");
        let cfg = lower_mir(mir, &alloc).expect("lowering succeeds");
        let nodes = crate::emit::cfg_to_engine_nodes(&cfg).expect("emit succeeds");
        let mut interp = Interpreter::new(0);
        interp.record_writes();
        for (block, values) in inputs {
            interp.set_block(*block, values.clone());
        }
        interp.run(&nodes).expect("interpretation succeeds");
        interp
    }

    fn read_cell(interp: &Interpreter, block: i64, index: usize) -> f64 {
        interp.block(block).expect("block exists")[index]
    }

    /// The number of scheduled instructions across all blocks.
    fn scheduled_count(mir: &Mir) -> usize {
        mir.blocks.iter().map(|b| b.insts.len()).sum()
    }

    #[test]
    fn nonoverlapping_copy_chain_is_merged_and_copy_removed() {
        // The gvn/m2r double-store shape: v stored to tA, read out, then
        // copied to tB whose reads follow — tA is dead at the copy, so the
        // temps merge and the copy disappears.
        let mut mir = Mir::new();
        let ta = mir.push_temp("ssa.0", 1);
        let tb = mir.push_temp("gvn0", 1);
        let b0 = mir.push_block();
        let v = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(-3, 0),
            },
        );
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: temp_place(ta),
                value: v,
            },
        );
        let l1 = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: temp_place(ta),
            },
        );
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: concrete_place(20, 0),
                value: l1,
            },
        );
        let l2 = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: temp_place(ta),
            },
        );
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: temp_place(tb),
                value: l2,
            },
        );
        let l3 = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: temp_place(tb),
            },
        );
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: concrete_place(20, 1),
                value: l3,
            },
        );
        let before = scheduled_count(&mir);
        let stats = coalesce_and_thread(&mut mir, &[]);
        assert_eq!(stats.merged, 1, "tA and tB must merge");
        assert_eq!(stats.removed_copies, 1, "the copy pair must be removed");
        assert_eq!(scheduled_count(&mir), before - 2);
        // Everything now reads/writes the representative (the smaller index).
        for inst in &mir.insts {
            if let Inst::Load { place } | Inst::Store { place, .. } = inst
                && let BlockRef::Temp(t) = place.block
            {
                assert_eq!(t, ta, "all temp refs redirect to the representative");
            }
        }
        let interp = run_mir(&mir, &[(-3, vec![42.0])]);
        assert_eq!(read_cell(&interp, 20, 0), 42.0);
        assert_eq!(read_cell(&interp, 20, 1), 42.0);
    }

    #[test]
    fn interfering_copy_is_not_merged_and_behavior_is_preserved() {
        // tA is overwritten while tB still holds the copied value: merging
        // would make 20[1] read 9 instead of the input. The interference
        // graph must block the merge.
        let mut mir = Mir::new();
        let ta = mir.push_temp("a", 1);
        let tb = mir.push_temp("b", 1);
        let b0 = mir.push_block();
        let v = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(-3, 0),
            },
        );
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: temp_place(ta),
                value: v,
            },
        );
        let l = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: temp_place(ta),
            },
        );
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: temp_place(tb),
                value: l,
            },
        );
        let c9 = mir.push_inst(Inst::ConstInt(9));
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: temp_place(ta),
                value: c9,
            },
        );
        let la = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: temp_place(ta),
            },
        );
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: concrete_place(20, 0),
                value: la,
            },
        );
        let lb = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: temp_place(tb),
            },
        );
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: concrete_place(20, 1),
                value: lb,
            },
        );
        let stats = coalesce_and_thread(&mut mir, &[]);
        assert_eq!(stats.merged, 0, "interfering temps must not merge");
        assert_eq!(stats.removed_copies, 0);
        let interp = run_mir(&mir, &[(-3, vec![7.0])]);
        assert_eq!(read_cell(&interp, 20, 0), 9.0, "tA was overwritten");
        assert_eq!(read_cell(&interp, 20, 1), 7.0, "tB kept the copied value");
    }

    #[test]
    fn sequentialized_swap_copies_survive_coalescing() {
        // A swap's sequentialized parallel copy (through the scratch temp):
        // every copy is load-bearing; the interference graph must block all
        // three merges and the swap must still run correctly.
        let mut mir = Mir::new();
        let a = mir.push_temp("a", 1);
        let b = mir.push_temp("b", 1);
        let s = mir.push_temp("scratch", 1);
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        mir.blocks[b0].terminator = Terminator::Jump(b1);
        let c1 = mir.push_inst(Inst::ConstInt(1));
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: temp_place(a),
                value: c1,
            },
        );
        let c2 = mir.push_inst(Inst::ConstInt(2));
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: temp_place(b),
                value: c2,
            },
        );
        // Sequentialized swap: s <- a; a <- b; b <- s.
        let l1 = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: temp_place(a),
            },
        );
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: temp_place(s),
                value: l1,
            },
        );
        let l2 = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: temp_place(b),
            },
        );
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: temp_place(a),
                value: l2,
            },
        );
        let l3 = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: temp_place(s),
            },
        );
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: temp_place(b),
                value: l3,
            },
        );
        let la = sched(
            &mut mir,
            b1,
            Inst::Load {
                place: temp_place(a),
            },
        );
        sched(
            &mut mir,
            b1,
            Inst::Store {
                place: concrete_place(20, 0),
                value: la,
            },
        );
        let lb = sched(
            &mut mir,
            b1,
            Inst::Load {
                place: temp_place(b),
            },
        );
        sched(
            &mut mir,
            b1,
            Inst::Store {
                place: concrete_place(20, 1),
                value: lb,
            },
        );
        let stats = coalesce_and_thread(&mut mir, &[]);
        assert_eq!(stats.merged, 0, "every swap copy is load-bearing");
        let interp = run_mir(&mir, &[]);
        assert_eq!(read_cell(&interp, 20, 0), 2.0);
        assert_eq!(read_cell(&interp, 20, 1), 1.0);
    }

    #[test]
    fn lazy_tree_reads_of_merged_temps_stay_correct() {
        // The m2r shape: the copied-to temp is read only inside a lazy tree;
        // merging must keep the conditional read seeing the right value.
        let mut mir = Mir::new();
        let ta = mir.push_temp("ssa.0", 1);
        let tm = mir.push_temp("m2r0", 1);
        let b0 = mir.push_block();
        let v = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(-3, 1),
            },
        );
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: temp_place(ta),
                value: v,
            },
        );
        let l1 = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: temp_place(ta),
            },
        );
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: concrete_place(20, 0),
                value: l1,
            },
        );
        let l2 = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: temp_place(ta),
            },
        );
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: temp_place(tm),
                value: l2,
            },
        );
        let lhs = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(-3, 0),
            },
        );
        let lazy_load = mir.push_inst(Inst::Load {
            place: temp_place(tm),
        });
        let sc = sched(
            &mut mir,
            b0,
            Inst::ShortCircuit {
                op: Op::And,
                pure_node: true,
                lhs,
                rhs: lazy_load,
            },
        );
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: concrete_place(20, 1),
                value: sc,
            },
        );
        let stats = coalesce_and_thread(&mut mir, &[]);
        assert_eq!(stats.merged, 1, "ssa.0 and m2r0 must merge");
        assert_eq!(stats.removed_copies, 1);
        // And(0, _) short-circuits; And(1, v) takes the lazy read.
        let interp = run_mir(&mir, &[(-3, vec![0.0, 6.0])]);
        assert_eq!(read_cell(&interp, 20, 1), 0.0);
        let interp = run_mir(&mir, &[(-3, vec![1.0, 6.0])]);
        assert_eq!(read_cell(&interp, 20, 1), 6.0);
    }

    #[test]
    fn copy_chains_merge_transitively() {
        // a -> b -> c with disjoint lifetimes: both copies merge into one
        // class and both disappear.
        let mut mir = Mir::new();
        let a = mir.push_temp("a", 1);
        let b = mir.push_temp("b", 1);
        let c = mir.push_temp("c", 1);
        let b0 = mir.push_block();
        let v = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(-3, 0),
            },
        );
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: temp_place(a),
                value: v,
            },
        );
        let l1 = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: temp_place(a),
            },
        );
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: temp_place(b),
                value: l1,
            },
        );
        let l2 = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: temp_place(b),
            },
        );
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: temp_place(c),
                value: l2,
            },
        );
        let l3 = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: temp_place(c),
            },
        );
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: concrete_place(20, 0),
                value: l3,
            },
        );
        let stats = coalesce_and_thread(&mut mir, &[]);
        assert_eq!(stats.merged, 2);
        assert_eq!(stats.removed_copies, 2);
        let interp = run_mir(&mir, &[(-3, vec![5.0])]);
        assert_eq!(read_cell(&interp, 20, 0), 5.0);
    }

    #[test]
    fn empty_split_blocks_thread_and_nonempty_ones_stay() {
        // b0 branches to s1 (empty split block -> b2) and s2 (split block
        // with a remaining copy -> b2). s1 must thread out; s2 must stay; the
        // branch's edge order and conditions must be untouched.
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 1);
        let b0 = mir.push_block();
        let s1 = mir.push_block();
        let s2 = mir.push_block();
        let b2 = mir.push_block();
        let test = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(-3, 0),
            },
        );
        mir.blocks[b0].terminator = Terminator::Branch {
            test,
            cases: vec![(crate::mir::CaseCond::Int(0), s1)],
            default: Some(s2),
        };
        mir.blocks[s1].terminator = Terminator::Jump(b2);
        mir.blocks[s2].terminator = Terminator::Jump(b2);
        let c7 = mir.push_inst(Inst::ConstInt(7));
        sched(
            &mut mir,
            s2,
            Inst::Store {
                place: temp_place(t),
                value: c7,
            },
        );
        let c1 = mir.push_inst(Inst::ConstInt(1));
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: temp_place(t),
                value: c1,
            },
        );
        let lt = sched(
            &mut mir,
            b2,
            Inst::Load {
                place: temp_place(t),
            },
        );
        sched(
            &mut mir,
            b2,
            Inst::Store {
                place: concrete_place(20, 0),
                value: lt,
            },
        );
        let stats = coalesce_and_thread(&mut mir, &[s1, s2]);
        assert_eq!(stats.threaded_blocks, 1, "only the empty block threads");
        let Terminator::Branch { cases, default, .. } = &mir.blocks[b0].terminator else {
            panic!("branch terminator survives");
        };
        assert_eq!(
            cases.as_slice(),
            &[(crate::mir::CaseCond::Int(0), b2)],
            "the cond-0 edge retargets to b2 with its condition intact"
        );
        assert_eq!(*default, Some(s2), "the non-empty split block stays");
        // Behavior: input 0 -> threaded path -> t = 1; else -> s2 -> t = 7.
        let interp = run_mir(&mir, &[(-3, vec![0.0])]);
        assert_eq!(read_cell(&interp, 20, 0), 1.0);
        let interp = run_mir(&mir, &[(-3, vec![1.0])]);
        assert_eq!(read_cell(&interp, 20, 0), 7.0);
    }

    #[test]
    fn coalescing_is_deterministic() {
        // Same MIR twice -> identical instruction arenas and schedules.
        let build = || {
            let mut mir = Mir::new();
            let a = mir.push_temp("a", 1);
            let b = mir.push_temp("b", 1);
            let b0 = mir.push_block();
            let v = sched(
                &mut mir,
                b0,
                Inst::Load {
                    place: concrete_place(-3, 0),
                },
            );
            sched(
                &mut mir,
                b0,
                Inst::Store {
                    place: temp_place(a),
                    value: v,
                },
            );
            let l = sched(
                &mut mir,
                b0,
                Inst::Load {
                    place: temp_place(a),
                },
            );
            sched(
                &mut mir,
                b0,
                Inst::Store {
                    place: temp_place(b),
                    value: l,
                },
            );
            let l2 = sched(
                &mut mir,
                b0,
                Inst::Load {
                    place: temp_place(b),
                },
            );
            sched(
                &mut mir,
                b0,
                Inst::Store {
                    place: concrete_place(20, 0),
                    value: l2,
                },
            );
            mir
        };
        let mut m1 = build();
        let mut m2 = build();
        let s1 = coalesce_and_thread(&mut m1, &[]);
        let s2 = coalesce_and_thread(&mut m2, &[]);
        assert_eq!(s1, s2);
        assert_eq!(m1.insts, m2.insts);
        assert_eq!(m1.blocks, m2.blocks);
    }
}
