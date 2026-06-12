//! W3 loop-invariant code motion (PORT.md T3.7) — the legacy
//! `LoopInvariantCodeMotion` (`sonolus/backend/optimize/licm.py`) successor,
//! redesigned for value-SSA MIR (decision D2).
//!
//! For every natural loop (innermost first), pure never-trapping instructions
//! whose operands are all defined outside the loop are **moved** to a
//! preheader block, so the loop pays one slotted-temp load per iteration
//! instead of recomputing the expression. The pass runs post-Mem2Reg in the
//! registry (stage W3), where MIR is in *value SSA* form — multi-use and
//! cross-block uses are legal and `ssa::destruct_ssa` (unconditional in
//! `compile_cfg`) re-establishes the lowering contract afterwards — so a
//! hoisted value's in-loop uses simply become cross-block uses that
//! destruction materializes through a single-slot temp (compute + store once
//! in the preheader, a load per use site). This is the same end shape legacy
//! LICM+CSE produced, without legacy's copy-then-deduplicate dance: legacy
//! *copied* the invariant expression into the preheader and relied on the
//! following CSE pass to merge the in-loop original against the copy; moving
//! needs no follow-up pass to be correct (a registry GVN/DCE re-run after W3
//! remains a possible quality follow-up, e.g. to merge identical hoisted
//! trees across sibling loops' preheaders).
//!
//! # Hoisting conditions (all required)
//!
//! A scheduled `Inst::Op` in loop body block `b` is hoisted iff:
//!
//! 1. **Pure** (`effects::op_effects`): no memory write, draw, log, or
//!    control effect. RNG ops (`Random`/`RandomInteger`) are not pure and are
//!    additionally absent from the totality whitelist — draws are never
//!    moved, duplicated, or reordered (draw order is part of the differential
//!    contract).
//! 2. **Never-trapping** ([`super::dce::op_is_total`], the 24-op whitelist):
//!    the preheader executes once per loop *entry*, including entries where
//!    the loop body runs zero iterations or the op's block is skipped by an
//!    in-loop branch — speculation is only safe when executing the op early
//!    is observable through nothing but its value. This makes hoisting
//!    correct without any guaranteed-execution proof (legacy hoisted
//!    trap-capable pure ops like `Divide` and would raise on zero-trip loops
//!    where the baseline does not — a real divergence under the §3.7
//!    differential contract, so the legacy behavior is deliberately dropped).
//! 3. **Loop-invariant operands**: every operand is a constant, a value
//!    defined outside the loop body, or a value this loop already hoisted
//!    (chains hoist together: defs precede uses in RPO × schedule order, so a
//!    single scan suffices). An operand defined outside the body dominates
//!    the preheader: every path into the body enters through the header
//!    (natural-loop property), and after retargeting every non-back edge the
//!    header is only reachable through the preheader, so a def that
//!    dominated the in-loop use also dominates the preheader.
//! 4. **Guaranteed execution per iteration** (profitability, not safety): the
//!    block dominates every loop latch — the op runs exactly once per
//!    completed iteration, so moving it saves `cost − 3` evaluations per
//!    iteration. Ops on conditional in-loop paths execute less than once per
//!    iteration (possibly never), where hoisting could pessimize; legacy made
//!    the same restriction.
//! 5. **Cost ≥ 4** (the legacy `_cost >= 4` threshold, same model: constants
//!    cost 1, value operands 3 — a value operand of an in-loop instruction is
//!    a cross-block use, which destruction materializes as a 3-evaluation
//!    temp load): the in-loop site becomes a 3-evaluation load, so cheaper
//!    expressions are never extracted.
//!
//! # What is deliberately not hoisted
//!
//! - **Loads, all of them.** Post-W2 `Mem2Reg`, constant-index temps are
//!   promoted to SSA values; surviving temp loads are dynamic-index (the
//!   index evaluation can trap on NaN/out-of-range — speculation-unsafe) or
//!   belong to promotion-refused temps (the read-before-write kind, exactly
//!   the risky case). Runtime block loads (`BlockRef::Concrete`/`Value`) are
//!   immovable: another callback or the runtime may write between iterations,
//!   and MIR carries no callback writability metadata (legacy hoisted reads
//!   of blocks not writable by the current callback via
//!   `OptimizerConfig.callback`; that information does not exist in the Rust
//!   backend — a dropped legacy capability, revisit only if block metadata is
//!   ever plumbed through). The single-slot `gvnN`/`m2rN` loads would gain
//!   nothing (a load replaced by a load).
//! - **`ShortCircuit`** instructions and everything inside lazy rhs trees
//!   (D11): the rhs is conditionally evaluated by definition; nothing moves
//!   out of, into, or across the lazy boundary. Lazy-tree instructions are
//!   unscheduled, so the schedule scan never even sees them.
//! - **Stores, phis, effectful or trap-capable ops** (conditions 1–2).
//!
//! # Preheader creation
//!
//! The preheader for a loop with header `H` is the unique block through which
//! every non-back edge into `H` flows. When `H` has exactly one non-body
//! predecessor whose terminator is `Jump(H)`, that block already *is* the
//! preheader and is reused (hoisted instructions append to its schedule; no
//! CFG change). Otherwise a fresh block `P` (`Jump(H)`) is appended and every
//! non-body predecessor edge targeting `H` is retargeted to `P`; `H`'s phi
//! arguments keyed by retargeted predecessors collapse into a single argument
//! keyed by `P` (re-keyed directly when there is one such predecessor,
//! otherwise routed through a fresh phi in `P` — the legacy
//! `_get_or_create_preheader` shape). Loops whose header is the entry block
//! are skipped (block 0 is the function entry by convention; `Mem2Reg`'s entry
//! split makes this shape rare), as are loops with no non-back edges and
//! irreducible regions (which produce no natural loop at all — the T2.1
//! contract: no loop means do not touch).
//!
//! # Nested loops and rounds
//!
//! Loops are processed innermost-first (descending loop id: parents precede
//! children in the forest), so inner-loop hoists land in the inner preheader,
//! which sits inside the outer loop's body and can be hoisted again. Within
//! one round the dominator tree and loop forest are computed once up front;
//! preheader insertion is pure edge splitting, which preserves dominance and
//! loop membership among pre-existing blocks, and *freshly created* blocks
//! are conservatively treated as loop-body members (their contents wait for
//! the next round). Rounds repeat until a fixpoint: each hoist strictly
//! decreases the total loop depth of scheduled instructions, so the fixpoint
//! terminates; the iteration cap is a defensive bound, like DCE's.
//!
//! # Micro-unroll (the optional half of T3.7)
//!
//! Deliberately deferred — see the task close-out rationale in PORT.md's
//! worklog: tiny-constant-trip-loop unrolling needs body cloning (including
//! owned lazy trees), phi rewiring per peeled iteration, and a cost model
//! trading dispatch round-trips against static node growth; the W3 gate
//! metric (`eval_count` parity) is driven by hoisting, not unrolling, and W4/W5
//! (if-conversion, flattening, fused tiling) change the cost landscape it
//! would optimize against.
//!
//! # Pass discipline
//!
//! Deterministic: loops in forest order, bodies in RPO, schedules in order,
//! hash sets are membership-only (no iteration order escapes). Iterative
//! (explicit loops, invariant §3.4). Invalidation per the `analysis` module
//! docs: preheader creation drops everything (`invalidate_cfg`); pure
//! schedule motion drops value-level results (`invalidate_values`).

use std::collections::HashSet;

use crate::analysis::{Analyses, DomTree, LoopForest};
use crate::effects::op_effects;
use crate::mir::{BlockId, Inst, Mir, Terminator, Value};
use crate::passes::Pass;
use crate::passes::dce::op_is_total;

/// The W3 LICM pass. See the module docs.
#[derive(Debug, Default, Clone, Copy)]
pub struct LicmPass;

/// The legacy extraction threshold (`licm.py::_cost >= 4`): an in-loop value
/// use lowers to a 3-evaluation temp load, so anything cheaper stays put.
const HOIST_COST: u64 = 4;

impl Pass for LicmPass {
    fn name(&self) -> &'static str {
        "licm"
    }

    fn run(&self, mir: &mut Mir, analyses: &mut Analyses) -> bool {
        if mir.blocks.is_empty() {
            return false;
        }
        let mut total = RoundChanges::default();
        // Fixpoint over rounds (module docs): hoists strictly decrease the
        // total loop depth of scheduled instructions, so this terminates; the
        // cap is a defensive bound only.
        let max_rounds = mir.blocks.len() + 8;
        for _ in 0..max_rounds {
            let round = run_round(mir);
            total.cfg |= round.cfg;
            total.values |= round.values;
            if !round.any() {
                break;
            }
        }
        if total.cfg {
            analyses.invalidate_cfg();
        } else if total.values {
            analyses.invalidate_values();
        }
        total.any()
    }
}

/// What kinds of mutation a round performed (drives analysis invalidation).
#[derive(Debug, Default, Clone, Copy)]
struct RoundChanges {
    /// CFG shape changed (a preheader block was created).
    cfg: bool,
    /// Instructions moved between schedules (CFG shape intact).
    values: bool,
}

impl RoundChanges {
    fn any(self) -> bool {
        self.cfg || self.values
    }
}

/// One round: fresh analyses, then every loop innermost-first.
fn run_round(mir: &mut Mir) -> RoundChanges {
    let mut changes = RoundChanges::default();
    let dom = DomTree::compute(mir);
    let forest = LoopForest::compute(mir, &dom);
    if forest.loops.is_empty() {
        return changes;
    }
    // Blocks created during this round (fresh preheaders) are out of range of
    // the forest's bitsets and conservatively treated as loop-body members;
    // their contents are revisited next round.
    let round_blocks = mir.blocks.len();
    // Defining block of every scheduled instruction and phi (None for
    // constants and lazy-tree-owned instructions — the latter are never
    // operands of eager instructions in contract MIR, and a None operand is
    // simply not invariant).
    let mut def_block: Vec<Option<BlockId>> = vec![None; mir.insts.len()];
    for (b, block) in mir.blocks.iter().enumerate() {
        for &v in block.phis.iter().chain(&block.insts) {
            def_block[v as usize] = Some(b);
        }
    }
    // Innermost first: a parent's id is always smaller than its children's,
    // so descending id order processes children before parents.
    for l in (0..forest.loops.len()).rev() {
        process_loop(
            mir,
            &dom,
            &forest,
            l,
            round_blocks,
            &mut def_block,
            &mut changes,
        );
    }
    changes
}

/// Hoists one loop's invariant instructions into its preheader.
fn process_loop(
    mir: &mut Mir,
    dom: &DomTree,
    forest: &LoopForest,
    l: usize,
    round_blocks: usize,
    def_block: &mut Vec<Option<BlockId>>,
    changes: &mut RoundChanges,
) {
    let header = forest.loops[l].header;
    if header == 0 {
        // Block 0 is the function entry by convention; a preheader cannot be
        // placed before it. (Mem2Reg's entry split makes this shape rare.)
        return;
    }
    // Reachable predecessors of the header outside the body = the non-back
    // edges. Stale-analysis note: nothing in this round retargets edges into
    // a *different* loop's header (each loop has its own header and a fresh
    // preheader's only successor is its own header), so the predecessor list
    // computed at round start stays correct for every loop processed here.
    let non_body: Vec<BlockId> = dom
        .preds(header)
        .iter()
        .copied()
        .filter(|&p| !forest.contains(l, p))
        .collect();
    if non_body.is_empty() {
        return;
    }

    // Collect candidates in body-RPO × schedule order (defs precede uses in
    // that order, so chains are recognized in one scan via `hoisted_set`).
    let latches = &forest.loops[l].latches;
    let mut hoisted: Vec<Value> = Vec::new();
    let mut hoisted_set: HashSet<Value> = HashSet::new();
    for &b in &forest.loops[l].body {
        // Profitability gate (module docs condition 4): only blocks that
        // dominate every latch run exactly once per completed iteration.
        if !latches.iter().all(|&latch| dom.dominates(b, latch)) {
            continue;
        }
        for &v in &mir.blocks[b].insts {
            let Inst::Op { op, args, .. } = mir.inst(v) else {
                continue; // loads/stores/short-circuits/phis never move
            };
            if !op_effects(*op).is_pure() || !op_is_total(*op) {
                continue; // effects (incl. RNG) and possible traps stay put
            }
            let mut cost = 1u64;
            let mut invariant = true;
            for &a in args {
                if mir.is_const(a) {
                    cost += 1;
                    continue;
                }
                cost += 3;
                if hoisted_set.contains(&a) {
                    continue; // hoisted earlier in this scan: invariant
                }
                let outside = match def_block[a as usize] {
                    Some(db) => db < round_blocks && !forest.contains(l, db),
                    None => false,
                };
                if !outside {
                    invariant = false;
                    break;
                }
            }
            if invariant && cost >= HOIST_COST {
                hoisted.push(v);
                hoisted_set.insert(v);
            }
        }
    }
    if hoisted.is_empty() {
        return;
    }

    let pre = get_or_create_preheader(mir, header, &non_body, def_block, changes);

    // Move: drop from the body schedules, append to the preheader in
    // collection order (defs before uses among the hoisted, per the scan
    // order; operands defined outside the loop dominate the preheader).
    for &b in &forest.loops[l].body {
        mir.blocks[b].insts.retain(|v| !hoisted_set.contains(v));
    }
    for &v in &hoisted {
        mir.blocks[pre].insts.push(v);
        def_block[v as usize] = Some(pre);
    }
    changes.values = true;
}

/// Returns the loop's preheader, creating one if no usable block exists. See
/// the module docs ("Preheader creation") for the reuse condition and the phi
/// re-keying rules.
fn get_or_create_preheader(
    mir: &mut Mir,
    header: BlockId,
    non_body: &[BlockId],
    def_block: &mut Vec<Option<BlockId>>,
    changes: &mut RoundChanges,
) -> BlockId {
    if let [p] = *non_body
        && mir.blocks[p].terminator == Terminator::Jump(header)
    {
        return p; // already a dedicated preheader: reuse, no CFG change
    }

    let pre = mir.push_block();
    mir.blocks[pre].terminator = Terminator::Jump(header);

    // Retarget every non-body predecessor edge aimed at the header. Edges
    // from unreachable blocks (not in `non_body`, which is reachable-only)
    // keep their old target: they never execute and their phi keying stays
    // consistent untouched.
    for &p in non_body {
        match &mut mir.blocks[p].terminator {
            Terminator::Jump(t) => {
                if *t == header {
                    *t = pre;
                }
            }
            Terminator::Branch { cases, default, .. } => {
                for (_, t) in cases.iter_mut() {
                    if *t == header {
                        *t = pre;
                    }
                }
                if let Some(d) = default
                    && *d == header
                {
                    *d = pre;
                }
            }
            Terminator::Exit => {}
        }
    }

    // Re-key the header's phi arguments: per phi, the arguments keyed by
    // retargeted predecessors collapse into one argument keyed by the
    // preheader (directly when there is a single retargeted predecessor,
    // through a fresh phi in the preheader otherwise — the legacy shape).
    let phis: Vec<Value> = mir.blocks[header].phis.clone();
    for phi in phis {
        let Inst::Phi { args } = mir.inst(phi) else {
            continue;
        };
        let moved: Vec<(BlockId, Value)> = args
            .iter()
            .copied()
            .filter(|(p, _)| non_body.contains(p))
            .collect();
        if moved.is_empty() {
            continue;
        }
        let replacement = if let [(_, v)] = *moved {
            // The single value dominates its (sole retargeted) predecessor,
            // which is the preheader's only predecessor.
            v
        } else {
            let new_phi = mir.push_inst(Inst::Phi { args: moved });
            def_block.resize(mir.insts.len(), None);
            def_block[new_phi as usize] = Some(pre);
            mir.blocks[pre].phis.push(new_phi);
            new_phi
        };
        let Inst::Phi { args } = &mut mir.insts[phi as usize] else {
            unreachable!("matched above");
        };
        let mut replaced = false;
        let mut new_args = Vec::with_capacity(args.len());
        for &(p, v) in args.iter() {
            if non_body.contains(&p) {
                if !replaced {
                    new_args.push((pre, replacement));
                    replaced = true;
                }
            } else {
                new_args.push((p, v));
            }
        }
        *args = new_args;
    }

    changes.cfg = true;
    pre
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::analysis::Analyses;
    use crate::cfg::{
        BasicBlock, BlockValue, Cfg, Edge, EdgeCond, IndexValue, Node, Place as CfgPlace,
        TempBlockDef,
    };
    use crate::diff::{DiffConfig, DiffOutcome, diff_with};
    use crate::interpret::Interpreter;
    use crate::mir::{BlockRef, CaseCond, IndexRef, Place, TempId};
    use crate::ops::Op;
    use crate::passes::Pipeline;
    use crate::passes::dce::DcePass;
    use crate::passes::gvn::GvnRewritePass;
    use crate::passes::mem2reg::Mem2Reg;
    use crate::passes::sccp::Sccp;
    use crate::pipeline::compile_cfg_with_pipeline;

    fn run_pass(mir: &mut Mir) -> bool {
        let mut analyses = Analyses::new();
        LicmPass.run(mir, &mut analyses)
    }

    fn sched(mir: &mut Mir, block: BlockId, inst: Inst) -> Value {
        let v = mir.push_inst(inst);
        mir.blocks[block].insts.push(v);
        v
    }

    fn concrete_place(block: i64, index: i64) -> Place {
        Place {
            block: BlockRef::Concrete(block),
            index: IndexRef::Const(index),
            offset: 0,
        }
    }

    fn temp_place(t: TempId) -> Place {
        Place {
            block: BlockRef::Temp(t),
            index: IndexRef::Const(0),
            offset: 0,
        }
    }

    /// The standard test loop: b0 (entry) -> b1 (header) -> {exit b3, body
    /// b2}; b2 -> b1 (latch). The header branches on a fresh load each
    /// iteration (variant test). Returns (mir, b0, b1, b2, b3).
    fn loop_mir() -> (Mir, BlockId, BlockId, BlockId, BlockId) {
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let b2 = mir.push_block();
        let b3 = mir.push_block();
        mir.blocks[b0].terminator = Terminator::Jump(b1);
        let test = sched(
            &mut mir,
            b1,
            Inst::Load {
                place: concrete_place(21, 0),
            },
        );
        mir.blocks[b1].terminator = Terminator::Branch {
            test,
            cases: vec![(CaseCond::Int(0), b3)],
            default: Some(b2),
        };
        mir.blocks[b2].terminator = Terminator::Jump(b1);
        mir.blocks[b3].terminator = Terminator::Exit;
        (mir, b0, b1, b2, b3)
    }

    /// Schedules `x = Load(20[0])` in `b0` and returns it (an out-of-loop
    /// value to build invariant expressions from).
    fn outer_load(mir: &mut Mir, b0: BlockId) -> Value {
        sched(
            mir,
            b0,
            Inst::Load {
                place: concrete_place(20, 0),
            },
        )
    }

    fn insts_of(mir: &Mir, b: BlockId) -> Vec<Value> {
        mir.blocks[b].insts.clone()
    }

    #[test]
    fn invariant_total_op_hoists_to_existing_preheader() {
        // v = Add(x, x) in the loop body, x defined in b0: moves to b0 (the
        // existing dedicated preheader — no new block).
        let (mut mir, b0, _b1, b2, _b3) = loop_mir();
        let x = outer_load(&mut mir, b0);
        let v = sched(
            &mut mir,
            b2,
            Inst::Op {
                op: Op::Add,
                pure_node: true,
                args: vec![x, x],
            },
        );
        sched(
            &mut mir,
            b2,
            Inst::Store {
                place: concrete_place(20, 1),
                value: v,
            },
        );
        let blocks_before = mir.blocks.len();
        assert!(run_pass(&mut mir));
        assert_eq!(
            mir.blocks.len(),
            blocks_before,
            "b0 was reused, no new block"
        );
        assert_eq!(insts_of(&mir, b0), vec![x, v], "hoisted after the load");
        assert!(
            !mir.blocks[b2].insts.contains(&v),
            "the op left the loop body"
        );
        // Idempotence: nothing left to do.
        assert!(!run_pass(&mut mir));
    }

    #[test]
    fn invariant_chain_hoists_together_in_one_run() {
        // w = Add(x, 1); v = Multiply(w, x): both move, in def-before-use
        // order.
        let (mut mir, b0, _b1, b2, _b3) = loop_mir();
        let x = outer_load(&mut mir, b0);
        let one = mir.push_inst(Inst::ConstInt(1));
        let w = sched(
            &mut mir,
            b2,
            Inst::Op {
                op: Op::Add,
                pure_node: true,
                args: vec![x, one],
            },
        );
        let v = sched(
            &mut mir,
            b2,
            Inst::Op {
                op: Op::Multiply,
                pure_node: true,
                args: vec![w, x],
            },
        );
        sched(
            &mut mir,
            b2,
            Inst::Store {
                place: concrete_place(20, 1),
                value: v,
            },
        );
        assert!(run_pass(&mut mir));
        assert_eq!(insts_of(&mir, b0), vec![x, w, v]);
        assert_eq!(
            mir.blocks[b2].insts.len(),
            1,
            "only the store remains in the body"
        );
    }

    #[test]
    fn trap_capable_op_is_not_hoisted() {
        // Divide can raise: the preheader would execute it even when the loop
        // body never does (zero-trip), so it must stay.
        let (mut mir, b0, _b1, b2, _b3) = loop_mir();
        let x = outer_load(&mut mir, b0);
        let one = mir.push_inst(Inst::ConstInt(1));
        let v = sched(
            &mut mir,
            b2,
            Inst::Op {
                op: Op::Divide,
                pure_node: true,
                args: vec![one, x],
            },
        );
        sched(
            &mut mir,
            b2,
            Inst::Store {
                place: concrete_place(20, 1),
                value: v,
            },
        );
        assert!(!run_pass(&mut mir));
        assert!(mir.blocks[b2].insts.contains(&v));
    }

    #[test]
    fn rng_draw_is_not_hoisted() {
        // Random is neither pure nor total: never moved (draw order is part
        // of the optimizer contract).
        let (mut mir, _b0, _b1, b2, _b3) = loop_mir();
        let lo = mir.push_inst(Inst::ConstInt(0));
        let hi = mir.push_inst(Inst::ConstInt(1));
        let draw = sched(
            &mut mir,
            b2,
            Inst::Op {
                op: Op::Random,
                pure_node: false,
                args: vec![lo, hi],
            },
        );
        sched(
            &mut mir,
            b2,
            Inst::Store {
                place: concrete_place(20, 1),
                value: draw,
            },
        );
        assert!(!run_pass(&mut mir));
        assert!(mir.blocks[b2].insts.contains(&draw));
    }

    #[test]
    fn loads_are_never_hoisted_even_with_invariant_looking_places() {
        // A constant-index temp load with a store to the same temp in the
        // loop (aliasing), and a runtime block load (immovable by design):
        // both stay.
        let (mut mir, _b0, _b1, b2, _b3) = loop_mir();
        let t = mir.push_temp("t", 1);
        let c = mir.push_inst(Inst::ConstInt(7));
        sched(
            &mut mir,
            b2,
            Inst::Store {
                place: temp_place(t),
                value: c,
            },
        );
        let temp_load = sched(
            &mut mir,
            b2,
            Inst::Load {
                place: temp_place(t),
            },
        );
        let runtime_load = sched(
            &mut mir,
            b2,
            Inst::Load {
                place: concrete_place(20, 0),
            },
        );
        let sum = sched(
            &mut mir,
            b2,
            Inst::Op {
                op: Op::Add,
                pure_node: true,
                args: vec![temp_load, runtime_load],
            },
        );
        sched(
            &mut mir,
            b2,
            Inst::Store {
                place: concrete_place(20, 1),
                value: sum,
            },
        );
        assert!(!run_pass(&mut mir));
        assert!(mir.blocks[b2].insts.contains(&temp_load));
        assert!(mir.blocks[b2].insts.contains(&runtime_load));
        assert!(
            mir.blocks[b2].insts.contains(&sum),
            "the Add's operands are in-loop loads: variant"
        );
    }

    #[test]
    fn lazy_rhs_contents_are_not_hoisted() {
        // sc = And(x, lazy Add(y, y)) in the loop: the lazy Add is invariant
        // and total but conditionally evaluated — nothing crosses the lazy
        // boundary, and the ShortCircuit itself never moves.
        let (mut mir, b0, _b1, b2, _b3) = loop_mir();
        let x = outer_load(&mut mir, b0);
        let y = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(20, 2),
            },
        );
        let lazy_add = mir.push_inst(Inst::Op {
            op: Op::Add,
            pure_node: true,
            args: vec![y, y],
        }); // unscheduled: owned by the lazy tree
        let sc = sched(
            &mut mir,
            b2,
            Inst::ShortCircuit {
                op: Op::And,
                pure_node: true,
                lhs: x,
                rhs: lazy_add,
            },
        );
        sched(
            &mut mir,
            b2,
            Inst::Store {
                place: concrete_place(20, 1),
                value: sc,
            },
        );
        assert!(!run_pass(&mut mir));
        assert!(mir.blocks[b2].insts.contains(&sc));
        let scheduled = mir.scheduled_mask();
        assert!(
            !scheduled[lazy_add as usize],
            "the lazy Add stays unscheduled"
        );
    }

    #[test]
    fn variant_operand_blocks_hoisting() {
        // v = Add(load-in-loop, 1): the operand is defined inside the body.
        let (mut mir, _b0, _b1, b2, _b3) = loop_mir();
        let inner = sched(
            &mut mir,
            b2,
            Inst::Load {
                place: concrete_place(20, 0),
            },
        );
        let one = mir.push_inst(Inst::ConstInt(1));
        let v = sched(
            &mut mir,
            b2,
            Inst::Op {
                op: Op::Add,
                pure_node: true,
                args: vec![inner, one],
            },
        );
        sched(
            &mut mir,
            b2,
            Inst::Store {
                place: concrete_place(20, 1),
                value: v,
            },
        );
        assert!(!run_pass(&mut mir));
        assert!(mir.blocks[b2].insts.contains(&v));
    }

    #[test]
    fn cheap_op_is_not_hoisted() {
        // Add(1, 2) costs 3 < 4: a temp load would not be cheaper. (SCCP
        // folds these in the real pipeline; the threshold still gates them.)
        let (mut mir, _b0, _b1, b2, _b3) = loop_mir();
        let c1 = mir.push_inst(Inst::ConstInt(1));
        let c2 = mir.push_inst(Inst::ConstInt(2));
        let v = sched(
            &mut mir,
            b2,
            Inst::Op {
                op: Op::Add,
                pure_node: true,
                args: vec![c1, c2],
            },
        );
        sched(
            &mut mir,
            b2,
            Inst::Store {
                place: concrete_place(20, 1),
                value: v,
            },
        );
        assert!(!run_pass(&mut mir));
        assert!(mir.blocks[b2].insts.contains(&v));
    }

    #[test]
    fn conditional_in_loop_block_is_not_hoisted_from() {
        // The loop body forks: b2 branches to b4 (conditional) or b5; both
        // rejoin at the latch b5. b4 does not dominate the latch, so its
        // invariant op stays (profitability gate).
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block(); // header
        let b2 = mir.push_block(); // body fork
        let b4 = mir.push_block(); // conditional arm
        let b5 = mir.push_block(); // latch
        let b3 = mir.push_block(); // exit
        mir.blocks[b0].terminator = Terminator::Jump(b1);
        let test1 = sched(
            &mut mir,
            b1,
            Inst::Load {
                place: concrete_place(21, 0),
            },
        );
        mir.blocks[b1].terminator = Terminator::Branch {
            test: test1,
            cases: vec![(CaseCond::Int(0), b3)],
            default: Some(b2),
        };
        let test2 = sched(
            &mut mir,
            b2,
            Inst::Load {
                place: concrete_place(21, 1),
            },
        );
        mir.blocks[b2].terminator = Terminator::Branch {
            test: test2,
            cases: vec![(CaseCond::Int(0), b5)],
            default: Some(b4),
        };
        mir.blocks[b4].terminator = Terminator::Jump(b5);
        mir.blocks[b5].terminator = Terminator::Jump(b1);
        mir.blocks[b3].terminator = Terminator::Exit;
        let x = outer_load(&mut mir, b0);
        let v = sched(
            &mut mir,
            b4,
            Inst::Op {
                op: Op::Add,
                pure_node: true,
                args: vec![x, x],
            },
        );
        sched(
            &mut mir,
            b4,
            Inst::Store {
                place: concrete_place(20, 1),
                value: v,
            },
        );
        assert!(!run_pass(&mut mir));
        assert!(mir.blocks[b4].insts.contains(&v));
    }

    #[test]
    fn fresh_preheader_is_created_for_branching_entry() {
        // The entry *branches* into the header (no dedicated preheader), so a
        // new block must be created and the entry edge retargeted.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block(); // header
        let b2 = mir.push_block(); // latch
        let b3 = mir.push_block(); // exit
        let entry_test = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(21, 2),
            },
        );
        mir.blocks[b0].terminator = Terminator::Branch {
            test: entry_test,
            cases: vec![(CaseCond::Int(0), b3)],
            default: Some(b1),
        };
        let test = sched(
            &mut mir,
            b1,
            Inst::Load {
                place: concrete_place(21, 0),
            },
        );
        mir.blocks[b1].terminator = Terminator::Branch {
            test,
            cases: vec![(CaseCond::Int(0), b3)],
            default: Some(b2),
        };
        mir.blocks[b2].terminator = Terminator::Jump(b1);
        mir.blocks[b3].terminator = Terminator::Exit;
        let x = outer_load(&mut mir, b0);
        let v = sched(
            &mut mir,
            b2,
            Inst::Op {
                op: Op::Add,
                pure_node: true,
                args: vec![x, x],
            },
        );
        sched(
            &mut mir,
            b2,
            Inst::Store {
                place: concrete_place(20, 1),
                value: v,
            },
        );
        let blocks_before = mir.blocks.len();
        assert!(run_pass(&mut mir));
        assert_eq!(mir.blocks.len(), blocks_before + 1, "one preheader created");
        let pre = blocks_before;
        assert_eq!(mir.blocks[pre].terminator, Terminator::Jump(b1));
        assert_eq!(insts_of(&mir, pre), vec![v]);
        // The entry's default edge now targets the preheader.
        let Terminator::Branch { default, .. } = &mir.blocks[b0].terminator else {
            panic!("entry still branches");
        };
        assert_eq!(*default, Some(pre));
    }

    #[test]
    fn header_phis_are_rekeyed_through_a_fresh_preheader() {
        // Two non-body predecessors with different phi values: the header phi
        // collapses its outside args into one keyed by the new preheader,
        // which receives a fresh phi merging the two.
        let mut mir = Mir::new();
        let b0 = mir.push_block(); // fork
        let ba = mir.push_block(); // pred A
        let bb = mir.push_block(); // pred B
        let b1 = mir.push_block(); // header
        let b2 = mir.push_block(); // latch
        let b3 = mir.push_block(); // exit
        let fork_test = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(21, 2),
            },
        );
        mir.blocks[b0].terminator = Terminator::Branch {
            test: fork_test,
            cases: vec![(CaseCond::Int(0), ba)],
            default: Some(bb),
        };
        mir.blocks[ba].terminator = Terminator::Jump(b1);
        mir.blocks[bb].terminator = Terminator::Jump(b1);
        let va = sched(
            &mut mir,
            ba,
            Inst::Load {
                place: concrete_place(20, 3),
            },
        );
        let vb = sched(
            &mut mir,
            bb,
            Inst::Load {
                place: concrete_place(20, 4),
            },
        );
        // Header phi over (ba: va, bb: vb, b2: v_latch).
        let v_latch = mir.push_inst(Inst::ConstInt(9));
        let phi = mir.push_inst(Inst::Phi {
            args: vec![(ba, va), (bb, vb), (b2, v_latch)],
        });
        mir.blocks[b1].phis.push(phi);
        let test = sched(
            &mut mir,
            b1,
            Inst::Load {
                place: concrete_place(21, 0),
            },
        );
        mir.blocks[b1].terminator = Terminator::Branch {
            test,
            cases: vec![(CaseCond::Int(0), b3)],
            default: Some(b2),
        };
        mir.blocks[b2].terminator = Terminator::Jump(b1);
        mir.blocks[b3].terminator = Terminator::Exit;
        // Something to hoist (forces preheader creation).
        let x = outer_load(&mut mir, b0);
        let v = sched(
            &mut mir,
            b2,
            Inst::Op {
                op: Op::Add,
                pure_node: true,
                args: vec![x, x],
            },
        );
        sched(
            &mut mir,
            b2,
            Inst::Store {
                place: concrete_place(20, 1),
                value: v,
            },
        );
        let blocks_before = mir.blocks.len();
        assert!(run_pass(&mut mir));
        let pre = blocks_before;
        assert_eq!(mir.blocks[pre].terminator, Terminator::Jump(b1));
        // The preheader gained a phi merging (ba: va, bb: vb).
        assert_eq!(mir.blocks[pre].phis.len(), 1);
        let new_phi = mir.blocks[pre].phis[0];
        let Inst::Phi { args } = mir.inst(new_phi) else {
            panic!("preheader phi");
        };
        assert_eq!(args.as_slice(), &[(ba, va), (bb, vb)]);
        // The header phi now has exactly (pre: new_phi) and the latch arg.
        let Inst::Phi { args } = mir.inst(phi) else {
            panic!("header phi survives");
        };
        assert_eq!(args.as_slice(), &[(pre, new_phi), (b2, v_latch)]);
    }

    #[test]
    fn nested_loops_hoist_all_the_way_out_over_rounds() {
        // Outer loop (header b1) contains inner loop (header b2, guaranteed:
        // it is the only path to the outer latch). An expression invariant to
        // BOTH loops ends up outside the outer loop after the multi-round
        // fixpoint, even though the inner hoist created a fresh preheader.
        let mut mir = Mir::new();
        let b0 = mir.push_block(); // entry (branches: forces fresh preheaders)
        let b1 = mir.push_block(); // outer header
        let b2 = mir.push_block(); // inner header (also the outer body)
        let b4 = mir.push_block(); // inner latch
        let b5 = mir.push_block(); // outer latch (after the inner loop)
        let b3 = mir.push_block(); // exit
        let entry_test = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(21, 3),
            },
        );
        mir.blocks[b0].terminator = Terminator::Branch {
            test: entry_test,
            cases: vec![(CaseCond::Int(0), b3)],
            default: Some(b1),
        };
        let outer_test = sched(
            &mut mir,
            b1,
            Inst::Load {
                place: concrete_place(21, 0),
            },
        );
        mir.blocks[b1].terminator = Terminator::Branch {
            test: outer_test,
            cases: vec![(CaseCond::Int(0), b3)],
            default: Some(b2),
        };
        let inner_test = sched(
            &mut mir,
            b2,
            Inst::Load {
                place: concrete_place(21, 1),
            },
        );
        mir.blocks[b2].terminator = Terminator::Branch {
            test: inner_test,
            cases: vec![(CaseCond::Int(0), b5)],
            default: Some(b4),
        };
        mir.blocks[b4].terminator = Terminator::Jump(b2);
        mir.blocks[b5].terminator = Terminator::Jump(b1);
        mir.blocks[b3].terminator = Terminator::Exit;
        let x = outer_load(&mut mir, b0);
        let v = sched(
            &mut mir,
            b4,
            Inst::Op {
                op: Op::Add,
                pure_node: true,
                args: vec![x, x],
            },
        );
        sched(
            &mut mir,
            b4,
            Inst::Store {
                place: concrete_place(20, 1),
                value: v,
            },
        );
        assert!(run_pass(&mut mir));
        // The op must not remain in any block of either loop body: with the
        // analyses recomputed, its final block must sit outside both loops.
        let dom = DomTree::compute(&mir);
        let forest = LoopForest::compute(&mir, &dom);
        let home = (0..mir.blocks.len())
            .find(|&b| mir.blocks[b].insts.contains(&v))
            .expect("the op is still scheduled somewhere");
        assert_eq!(
            forest.depth(home),
            0,
            "the doubly-invariant op must end up outside both loops (in block {home})"
        );
        assert!(!run_pass(&mut mir), "fixpoint reached");
    }

    #[test]
    fn entry_header_and_irreducible_regions_are_skipped() {
        // A self-loop on the entry block: no preheader is possible; no panic,
        // no change.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let test = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(21, 0),
            },
        );
        let x = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(20, 0),
            },
        );
        let v = sched(
            &mut mir,
            b0,
            Inst::Op {
                op: Op::Add,
                pure_node: true,
                args: vec![x, x],
            },
        );
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: concrete_place(20, 1),
                value: v,
            },
        );
        let b1 = mir.push_block();
        mir.blocks[b0].terminator = Terminator::Branch {
            test,
            cases: vec![(CaseCond::Int(0), b1)],
            default: Some(b0),
        };
        mir.blocks[b1].terminator = Terminator::Exit;
        assert!(!run_pass(&mut mir));

        // An irreducible two-entry cycle produces no natural loop: no change.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let b2 = mir.push_block();
        let test = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(21, 0),
            },
        );
        mir.blocks[b0].terminator = Terminator::Branch {
            test,
            cases: vec![(CaseCond::Int(0), b1)],
            default: Some(b2),
        };
        mir.blocks[b1].terminator = Terminator::Jump(b2);
        mir.blocks[b2].terminator = Terminator::Jump(b1);
        assert!(!run_pass(&mut mir));
    }

    // ------------------------------------------------------------------------------
    // End to end: a frontend counter loop, compiled through the real pipeline.
    // ------------------------------------------------------------------------------

    /// Frontend CFG: `a = in[-3][0]; for i in 0..5 { out[0] += (a+100)*(a+200) }`.
    /// The multiply chain is loop-invariant after `Mem2Reg` promotes `a`.
    #[allow(clippy::too_many_lines)] // one literal frontend CFG
    fn counter_loop_cfg() -> Cfg {
        let mut cfg = Cfg::default();
        cfg.strings.push("i".to_owned());
        cfg.strings.push("a".to_owned());
        cfg.temp_blocks.push(TempBlockDef { name: 0, size: 1 }); // i
        cfg.temp_blocks.push(TempBlockDef { name: 1, size: 1 }); // a
        let node = |cfg: &mut Cfg, n: Node| {
            cfg.nodes.push(n);
            cfg.nodes.len() - 1
        };
        let place = |cfg: &mut Cfg, block: BlockValue, index: i64| {
            cfg.places.push(CfgPlace {
                block,
                index: IndexValue::Int(index),
                offset: 0,
            });
            cfg.places.len() - 1
        };
        let counter_p = place(&mut cfg, BlockValue::Temp(0), 0);
        let a_p = place(&mut cfg, BlockValue::Temp(1), 0);
        let input_p = place(&mut cfg, BlockValue::Int(-3), 0);
        let out_p = place(&mut cfg, BlockValue::Int(20), 0);

        // b0: a <- in; i <- 0.
        let get_in = node(&mut cfg, Node::Get(input_p));
        let set_a = node(
            &mut cfg,
            Node::Set {
                place: a_p,
                value: get_in,
            },
        );
        let zero = node(&mut cfg, Node::ConstInt(0));
        let set_i = node(
            &mut cfg,
            Node::Set {
                place: counter_p,
                value: zero,
            },
        );
        let zt0 = node(&mut cfg, Node::ConstInt(0));
        cfg.blocks.push(BasicBlock {
            statements: vec![set_a, set_i],
            test: zt0,
            outgoing: vec![Edge {
                cond: EdgeCond::None,
                target: 1,
            }],
        });

        // b1 (header): test Less(i, 5); 0 -> b3 (exit), default -> b2 (body).
        let get_i = node(&mut cfg, Node::Get(counter_p));
        let five = node(&mut cfg, Node::ConstInt(5));
        let less = node(
            &mut cfg,
            Node::PureInstr {
                op: Op::Less,
                args: vec![get_i, five],
            },
        );
        cfg.blocks.push(BasicBlock {
            statements: vec![],
            test: less,
            outgoing: vec![
                Edge {
                    cond: EdgeCond::Int(0),
                    target: 3,
                },
                Edge {
                    cond: EdgeCond::None,
                    target: 2,
                },
            ],
        });

        // b2 (body): out += (a + 100) * (a + 200); i += 1.
        let get_out = node(&mut cfg, Node::Get(out_p));
        let get_a1 = node(&mut cfg, Node::Get(a_p));
        let c100 = node(&mut cfg, Node::ConstInt(100));
        let add1 = node(
            &mut cfg,
            Node::PureInstr {
                op: Op::Add,
                args: vec![get_a1, c100],
            },
        );
        let get_a2 = node(&mut cfg, Node::Get(a_p));
        let c200 = node(&mut cfg, Node::ConstInt(200));
        let add2 = node(
            &mut cfg,
            Node::PureInstr {
                op: Op::Add,
                args: vec![get_a2, c200],
            },
        );
        let mul = node(
            &mut cfg,
            Node::PureInstr {
                op: Op::Multiply,
                args: vec![add1, add2],
            },
        );
        let sum = node(
            &mut cfg,
            Node::PureInstr {
                op: Op::Add,
                args: vec![get_out, mul],
            },
        );
        let set_out = node(
            &mut cfg,
            Node::Set {
                place: out_p,
                value: sum,
            },
        );
        let get_i2 = node(&mut cfg, Node::Get(counter_p));
        let one = node(&mut cfg, Node::ConstInt(1));
        let inc = node(
            &mut cfg,
            Node::PureInstr {
                op: Op::Add,
                args: vec![get_i2, one],
            },
        );
        let set_i2 = node(
            &mut cfg,
            Node::Set {
                place: counter_p,
                value: inc,
            },
        );
        let zt2 = node(&mut cfg, Node::ConstInt(0));
        cfg.blocks.push(BasicBlock {
            statements: vec![set_out, set_i2],
            test: zt2,
            outgoing: vec![Edge {
                cond: EdgeCond::None,
                target: 1,
            }],
        });

        // b3: exit.
        let zt3 = node(&mut cfg, Node::ConstInt(0));
        cfg.blocks.push(BasicBlock {
            statements: vec![],
            test: zt3,
            outgoing: vec![],
        });
        cfg
    }

    /// The full standard registry prefix up to (but excluding) LICM.
    fn pre_licm_passes() -> Vec<Box<dyn Pass>> {
        vec![
            Box::new(Sccp),
            Box::new(GvnRewritePass),
            Box::new(DcePass),
            Box::new(Mem2Reg),
            Box::new(Sccp),
            Box::new(GvnRewritePass),
            Box::new(DcePass),
        ]
    }

    #[test]
    #[allow(clippy::float_cmp)] // exact f64 results, no arithmetic drift expected
    fn end_to_end_counter_loop_reduces_eval_count() {
        let cfg = counter_loop_cfg();
        let run = |with_licm: bool| {
            let mut passes = pre_licm_passes();
            if with_licm {
                passes.push(Box::new(LicmPass));
            }
            let nodes = compile_cfg_with_pipeline(&cfg, &Pipeline::new(passes)).unwrap();
            let mut interp = Interpreter::new(0);
            interp.set_block(-3, vec![3.0]);
            interp.set_block(20, vec![0.0]); // out (the default fill is -1.0)
            interp.run(&nodes).unwrap();
            (interp.block(20).unwrap()[0], interp.eval_count())
        };
        let (out_without, evals_without) = run(false);
        let (out_with, evals_with) = run(true);
        // 5 iterations of += (3+100)*(3+200) = 103 * 203 = 20909.
        assert_eq!(out_without, 5.0 * 103.0 * 203.0);
        assert_eq!(out_with, out_without, "behavior unchanged");
        assert!(
            evals_with < evals_without,
            "LICM must reduce dynamic evaluations on a 5-trip loop \
             ({evals_with} >= {evals_without})"
        );
        println!(
            "counter-loop eval count: {evals_without} -> {evals_with} \
             ({} fewer evaluations over 5 iterations)",
            evals_without - evals_with
        );
    }

    #[test]
    fn end_to_end_counter_loop_matches_minimal_differentially() {
        let cfg = counter_loop_cfg();
        for seed in [1u64, 2, 3] {
            let config = DiffConfig {
                memory_seed: seed,
                rng_seed: seed ^ 0xD1FF,
                eval_budget: 200_000,
            };
            let outcome = diff_with(
                &cfg,
                |c| crate::pipeline::compile_cfg(c, crate::pipeline::Level::Minimal),
                |c| {
                    let mut passes = pre_licm_passes();
                    passes.push(Box::new(LicmPass));
                    compile_cfg_with_pipeline(c, &Pipeline::new(passes))
                },
                &config,
            );
            assert!(
                matches!(outcome, DiffOutcome::Match),
                "seed {seed}: {outcome:?}"
            );
        }
    }

    #[test]
    #[allow(clippy::float_cmp)] // exact f64 results, no arithmetic drift expected
    fn zero_trip_loop_stays_equivalent_when_hoisting() {
        // in[-3][1] = 0 makes the loop run zero iterations; the hoisted total
        // op executes in the preheader with no observable difference.
        let cfg = {
            let mut c = counter_loop_cfg();
            // Replace the ConstInt(5) Less bound with Get(-3[1]) so the trip
            // count is a runtime value.
            let bound_place = c.places.len();
            c.places.push(CfgPlace {
                block: BlockValue::Int(-3),
                index: IndexValue::Int(1),
                offset: 0,
            });
            let bound_get = c.nodes.len();
            c.nodes.push(Node::Get(bound_place));
            for n in &mut c.nodes {
                if let Node::PureInstr { op: Op::Less, args } = n {
                    args[1] = bound_get;
                }
            }
            c
        };
        let run = |with_licm: bool, trips: f64| {
            let mut passes = pre_licm_passes();
            if with_licm {
                passes.push(Box::new(LicmPass));
            }
            let nodes = compile_cfg_with_pipeline(&cfg, &Pipeline::new(passes)).unwrap();
            let mut interp = Interpreter::new(0);
            interp.set_block(-3, vec![3.0, trips]);
            interp.set_block(20, vec![0.0]); // out (absent until written otherwise)
            interp.run(&nodes).unwrap();
            interp.block(20).unwrap()[0]
        };
        for trips in [0.0, 1.0, 4.0] {
            assert_eq!(
                run(false, trips),
                run(true, trips),
                "behavior must match at {trips} trips"
            );
        }
    }

    #[test]
    fn pass_is_deterministic() {
        let cfg = counter_loop_cfg();
        let compile = || {
            let mut passes = pre_licm_passes();
            passes.push(Box::new(LicmPass));
            let nodes = compile_cfg_with_pipeline(&cfg, &Pipeline::new(passes)).unwrap();
            crate::nodes::format_engine_node(&nodes.arena, nodes.root)
        };
        assert_eq!(compile(), compile());
    }
}
