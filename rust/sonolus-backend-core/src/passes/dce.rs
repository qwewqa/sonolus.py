//! W1 dead-code elimination + branch simplification + jump threading
//! (PORT.md T3.3) — the legacy `DeadCodeElimination` /
//! `AdvancedDeadCodeElimination` / mid-pipeline `CoalesceFlow` +
//! `UnreachableCodeElimination` replacement, redesigned for MIR.
//!
//! The pass iterates the following sub-transforms to a **bounded local
//! fixpoint** (each enables the others: a collapsed branch makes its test
//! dead; a swept schedule empties a block; an emptied block threads away):
//!
//! 1. **Branch simplification** — constant-test branches take their matching
//!    edge (Python `==` numeric matching, exactly like the build-time UCE port
//!    in `mir.rs`; this also runs post-SCCP in the registry pipeline, so the
//!    matching must stay equivalent), case edges that share the default's
//!    target are dropped (legacy `CoalesceFlow`), and a branch whose every
//!    remaining edge is the default becomes a jump. A branch with cases but
//!    **no default** is never collapsed: an unmatched scrutinee exits the
//!    callback, so "all case targets equal" does not make it unconditional.
//! 2. **Jump threading** — edges through empty (no phis, no scheduled
//!    instructions) jump-only blocks are redirected to the chain's final
//!    destination. Cycles of empty blocks leave the edge untouched (bounded
//!    walk; keeps the pass idempotent); the entry block and phi-bearing
//!    destinations stop the walk (the legacy `CoalesceFlow` guards — threading
//!    into a phi-bearing block would change its predecessor keying).
//! 3. **Unreachable-block clearing** — blocks unreachable from the entry are
//!    emptied in place (schedule, phis, terminator → `Exit`). Lowering's RPO
//!    only visits reachable blocks, so clearing (rather than splicing the
//!    block list) keeps every `BlockId` stable while still removing the dead
//!    code from allocation's and the emitter's view. Phi arguments keyed by
//!    blocks that are no longer predecessors are pruned (legacy UCE).
//! 4. **Chain merging** — a block whose terminator jumps to a single-predecessor,
//!    phi-free, non-entry block absorbs it (schedule concatenation + terminator
//!    take-over), the MIR equivalent of legacy `CoalesceFlow`'s block merge.
//!    Phi args in the absorbed block's successors are re-keyed to the absorber.
//! 5. **Dead-store elimination** (temp blocks, whole-temp granularity) — an
//!    eager store to a temp slot whose temp is not live after the store (per
//!    `analysis::liveness`, which already treats any lazy-tree or dynamic-index
//!    load of a temp as a use of the whole temp) is dead. This catches stores
//!    to never-loaded temps, stores after the last load, and (for one-cell
//!    temps, where stores kill) stores overwritten before any load — the
//!    legacy `AdvancedDeadCodeElimination` strength. **Precision**: per-point
//!    liveness at whole-temp granularity; no per-slot tracking (a store to
//!    `t[0]` is kept alive by a later load of `t[1]`) — slot-precision is a
//!    possible follow-up once W2's SROA changes the temp landscape.
//! 6. **Dead-instruction sweep** (mark-live + sweep): roots are the scheduled
//!    instructions whose *execution* is observable; everything reachable from
//!    a root, a branch test, or a live phi's arguments stays; the rest leaves
//!    the schedules (the arena keeps the dead entries — it is append-only by
//!    design, same as the rewrite driver). This is also the schedule sweep
//!    rewrite passes rely on before lowering (`LowerError::MultiUse`).
//!
//! # What counts as observable (trap conservatism)
//!
//! The differential contract (invariant §3.7) compares minimal against
//! minimal+\[this pass\] on randomized memory, **including error outcomes**.
//! Legacy DCE freely dropped dead code that could raise (a dead `Divide` by
//! zero, a dead load with a NaN dynamic index); doing that here would make the
//! optimized side succeed where the baseline traps — a real, fuzzer-visible
//! mismatch. So a dead instruction is removed only when executing it is
//! provably unobservable:
//!
//! - no memory write / draw / log / `Break` (`effects::op_effects`),
//! - **no RNG draw** — `Random`/`RandomInteger` are *never* removed even when
//!   unused: the draw count/order is part of the differential contract,
//! - **cannot raise**: the op is on the total-op whitelist
//!   ([`op_is_total`], derived from the `interpret.rs` `py_*` kernels), and
//!   loads/stores have a constant, provably in-bounds index on a non-computed
//!   block ([`load_is_total`]).
//!
//! A dead `ShortCircuit` additionally requires its whole owned lazy rhs tree
//! to be unobservable (D11): if the lazy side contains an effect or a possible
//! trap, those run *conditionally* when the value is computed, so the
//! instruction must stay even though its value is unused.
//!
//! # What is deliberately not here
//!
//! - **Constant folding** — SCCP's job (T3.1). This pass only *consumes*
//!   constants (test matching).
//! - **Dominance-based branch threading** ("the test was already branched on
//!   by a dominating block"): pre-SSA MIR cannot express it — a branch test
//!   must be defined in its own block (the lowering contract), so two blocks
//!   can never branch on the same `Value`; revisit after W2's `Mem2Reg` if
//!   memory-value numbering ever makes it expressible. Skipped as designed.
//! - Exit combining / tiny-block duplication — W4 (T3.9).
//!
//! # Pass discipline
//!
//! Deterministic (ascending block/instruction order everywhere), iterative
//! (explicit work stacks, invariant §3.4), pure (no state), accurate changed
//! flag, and invalidation per the `analysis` module docs (CFG-shape changes
//! drop everything; schedule-only changes drop liveness).

use crate::analysis::{Analyses, Liveness, inst_effect};
use crate::effects::op_effects;
use crate::mir::{
    BlockId, BlockRef, CaseCond, IndexRef, Inst, Mir, Place, TempId, Terminator, Value,
};
use crate::ops::Op;
use crate::passes::Pass;

/// The T3.3 pass. See the module docs.
#[derive(Debug, Default)]
pub struct DcePass;

impl Pass for DcePass {
    fn name(&self) -> &'static str {
        "dce"
    }

    fn run(&self, mir: &mut Mir, analyses: &mut Analyses) -> bool {
        let changes = run_to_fixpoint(mir);
        if changes.cfg {
            analyses.invalidate_all();
        } else if changes.values {
            analyses.invalidate_values();
        }
        changes.any()
    }
}

/// What kinds of mutation happened (drives analysis invalidation).
#[derive(Debug, Default, Clone, Copy)]
struct Changes {
    /// CFG shape (terminators, edges, phi placement/args) changed.
    cfg: bool,
    /// Only instruction schedules changed (CFG shape intact).
    values: bool,
}

impl Changes {
    fn any(self) -> bool {
        self.cfg || self.values
    }
}

/// Runs all sub-transforms to a bounded local fixpoint.
fn run_to_fixpoint(mir: &mut Mir) -> Changes {
    let mut total = Changes::default();
    if mir.blocks.is_empty() {
        return total;
    }
    // Termination: every changing iteration strictly shrinks one of the
    // monotone measures (scheduled instructions, phis, case edges, reachable
    // blocks, empty-chain hop counts), all bounded by MIR size. Real inputs
    // converge in 2–4 iterations; the cap is the hard bound.
    let max_iters = mir.blocks.len() + mir.insts.len() + 8;
    for _ in 0..max_iters {
        let mut iter = Changes::default();
        iter.cfg |= simplify_branches(mir);
        iter.cfg |= thread_jumps(mir);
        iter.cfg |= clear_unreachable(mir);
        iter.cfg |= merge_chains(mir);
        let (insts_removed, phis_removed) = sweep_dead(mir);
        iter.values |= insts_removed;
        // Phi placement is a CFG-level fact per the analysis invalidation
        // discipline.
        iter.cfg |= phis_removed;
        total.cfg |= iter.cfg;
        total.values |= iter.values;
        if !iter.any() {
            break;
        }
    }
    total
}

// ----------------------------------------------------------------------------------
// Branch simplification
// ----------------------------------------------------------------------------------

/// Python `==` between a case cond and a constant test instruction (exact, no
/// f64 rounding of large ints — mirrors the build-time UCE port in `mir.rs`).
#[allow(clippy::float_cmp)] // exact Python == semantics
fn cond_matches(cond: CaseCond, test: &Inst) -> bool {
    #[allow(clippy::cast_possible_truncation)]
    fn int_eq_float(i: i64, f: f64) -> bool {
        // Exact Python int == float: f must be integral, in i64 range, and
        // round-trip to the same i64. 2^63 as f64 is exactly
        // 9223372036854775808.0; any f64 strictly below it fits.
        f.is_finite()
            && f == f.trunc()
            && (-9_223_372_036_854_775_808.0..9_223_372_036_854_775_808.0).contains(&f)
            && (f as i64) == i
    }
    match (cond, test) {
        (CaseCond::Int(c), Inst::ConstInt(v)) => c == *v,
        (CaseCond::Int(c), Inst::ConstFloat(v)) => int_eq_float(c, *v),
        (CaseCond::Float(c), Inst::ConstInt(v)) => int_eq_float(*v, c),
        (CaseCond::Float(c), Inst::ConstFloat(v)) => c == *v,
        _ => false,
    }
}

/// Constant-test folding, duplicate-of-default case removal, and degenerate
/// branch → jump. See the module docs (transform 1).
///
/// `pub(crate)`: the W4 shape pass (`passes::shape`) reuses this inside its
/// own fixpoint — its threading can make every edge of a branch reach the
/// default's target, which this transform then collapses to a jump (enabling
/// the chain merge). A test value orphaned by the collapse stays scheduled
/// (same evaluation point); only DCE's own sweep removes it when provably
/// unobservable.
pub(crate) fn simplify_branches(mir: &mut Mir) -> bool {
    let mut changed = false;
    for b in 0..mir.blocks.len() {
        let Terminator::Branch {
            test,
            cases,
            default,
        } = &mir.blocks[b].terminator
        else {
            continue;
        };
        let new_term = if mir.is_const(*test) {
            // Take the first matching case (cases are sorted ascending — the
            // decoded edge order legacy UCE scans), else the default, else
            // exit (what the emitted dispatcher does with an unmatched
            // scrutinee and no default). The test is a constant, so dropping
            // its terminator use loses nothing.
            let test_inst = mir.inst(*test);
            let target = cases
                .iter()
                .find(|&&(c, _)| cond_matches(c, test_inst))
                .map(|&(_, t)| t)
                .or(*default);
            Some(match target {
                Some(t) => Terminator::Jump(t),
                None => Terminator::Exit,
            })
        } else if let Some(d) = *default {
            // Cases sharing the default's target are redundant: test == c
            // reaches d either way (legacy CoalesceFlow). The test value stays
            // scheduled, so its evaluation (and any potential trap) is
            // preserved; the sweep removes it only when provably unobservable.
            let kept: Vec<(CaseCond, BlockId)> =
                cases.iter().copied().filter(|&(_, t)| t != d).collect();
            if kept.is_empty() {
                Some(Terminator::Jump(d))
            } else if kept.len() != cases.len() {
                Some(Terminator::Branch {
                    test: *test,
                    cases: kept,
                    default: Some(d),
                })
            } else {
                None
            }
        } else if cases.is_empty() {
            // No cases, no default (not constructible by the builder;
            // defensive): evaluate-test-then-exit is just exit — the test
            // value is scheduled separately and keeps evaluating.
            Some(Terminator::Exit)
        } else {
            // Cases but no default: an unmatched test exits, so this is NOT
            // collapsible even when every case shares one target.
            None
        };
        if let Some(t) = new_term {
            mir.blocks[b].terminator = t;
            changed = true;
        }
    }
    changed
}

// ----------------------------------------------------------------------------------
// Jump threading
// ----------------------------------------------------------------------------------

/// Walks an edge target through a chain of empty jump-only blocks and returns
/// the final destination. Returns `start` unchanged when the walk cannot
/// finish within budget (a cycle of empty blocks) so the pass stays
/// idempotent. Guards (legacy `CoalesceFlow`): never skips the entry block,
/// the edge's own source, or into a phi-bearing destination.
fn skip_empty_chain(mir: &Mir, src: BlockId, start: BlockId) -> BlockId {
    let mut t = start;
    let mut budget = mir.blocks.len() + 1;
    loop {
        if budget == 0 {
            return start; // empty-block cycle: leave the edge alone
        }
        budget -= 1;
        if t == 0 || t == src {
            return t;
        }
        let block = &mir.blocks[t];
        if !block.phis.is_empty() || !block.insts.is_empty() {
            return t;
        }
        let Terminator::Jump(u) = block.terminator else {
            return t;
        };
        if u == t || !mir.blocks[u].phis.is_empty() {
            // Self-loop, or skipping would change the destination's phi
            // predecessor keying.
            return t;
        }
        t = u;
    }
}

/// Redirects every terminator edge through empty jump-only blocks (transform 2).
fn thread_jumps(mir: &mut Mir) -> bool {
    let mut changed = false;
    for b in 0..mir.blocks.len() {
        let new_term = match &mir.blocks[b].terminator {
            Terminator::Jump(t) => {
                let nt = skip_empty_chain(mir, b, *t);
                (nt != *t).then_some(Terminator::Jump(nt))
            }
            Terminator::Branch {
                test,
                cases,
                default,
            } => {
                let mut any = false;
                let new_cases: Vec<(CaseCond, BlockId)> = cases
                    .iter()
                    .map(|&(c, t)| {
                        let nt = skip_empty_chain(mir, b, t);
                        any |= nt != t;
                        (c, nt)
                    })
                    .collect();
                let new_default = default.map(|d| {
                    let nd = skip_empty_chain(mir, b, d);
                    any |= nd != d;
                    nd
                });
                any.then_some(Terminator::Branch {
                    test: *test,
                    cases: new_cases,
                    default: new_default,
                })
            }
            Terminator::Exit => None,
        };
        if let Some(t) = new_term {
            mir.blocks[b].terminator = t;
            changed = true;
        }
    }
    changed
}

// ----------------------------------------------------------------------------------
// Unreachable clearing + chain merging
// ----------------------------------------------------------------------------------

/// Empties unreachable blocks in place and prunes stale phi args (transform 3).
///
/// `pub(crate)`: the W4 shape pass (`passes::shape`) reuses this exact
/// hygiene step inside its own fixpoint (its transforms leave blocks
/// unreachable and phi args keyed by ex-predecessors, the same way this
/// pass's transforms do).
pub(crate) fn clear_unreachable(mir: &mut Mir) -> bool {
    let mut reachable = vec![false; mir.blocks.len()];
    reachable[0] = true;
    let mut stack: Vec<BlockId> = vec![0];
    while let Some(b) = stack.pop() {
        for s in mir.blocks[b].terminator.successors() {
            if !reachable[s] {
                reachable[s] = true;
                stack.push(s);
            }
        }
    }
    let mut changed = false;
    for (b, block) in mir.blocks.iter_mut().enumerate() {
        if reachable[b] {
            continue;
        }
        if !block.insts.is_empty() || !block.phis.is_empty() || block.terminator != Terminator::Exit
        {
            block.insts.clear();
            block.phis.clear();
            block.terminator = Terminator::Exit;
            changed = true;
        }
    }
    // Defensive phi-arg hygiene (a no-op while W1 MIR has no phis): drop args
    // keyed by blocks that are no longer predecessors, like legacy UCE.
    let preds = mir.predecessors();
    for (b, block_preds) in preds.iter().enumerate() {
        let phis = mir.blocks[b].phis.clone();
        for phi in phis {
            let Inst::Phi { args } = &mut mir.insts[phi as usize] else {
                continue;
            };
            let before = args.len();
            args.retain(|(p, _)| block_preds.contains(p));
            changed |= args.len() != before;
        }
    }
    changed
}

/// Merges single-predecessor jump chains (transform 4).
///
/// `pub(crate)`: the W4 shape pass (`passes::shape`) reuses this merge inside
/// its own fixpoint — after its trivial-phi elimination, single-predecessor
/// phi-bearing targets become phi-free and this exact merge absorbs them.
pub(crate) fn merge_chains(mir: &mut Mir) -> bool {
    let mut changed = false;
    loop {
        // Predecessors are recomputed after every merge (a merge changes
        // them); the scan is ascending, so the result is deterministic.
        let preds = mir.predecessors();
        let mut candidate = None;
        for b in 0..mir.blocks.len() {
            let Terminator::Jump(t) = mir.blocks[b].terminator else {
                continue;
            };
            if t == b || t == 0 {
                continue;
            }
            if preds[t].len() != 1 || preds[t][0] != b {
                continue;
            }
            if !mir.blocks[t].phis.is_empty() {
                // A phi in a single-pred block is degenerate but possible
                // mid-SSA; merging would orphan it. Conservative skip (W1 MIR
                // has no phis; revisit with W2 if it matters).
                continue;
            }
            candidate = Some((b, t));
            break;
        }
        let Some((b, t)) = candidate else {
            return changed;
        };
        let moved = std::mem::take(&mut mir.blocks[t].insts);
        let term = std::mem::replace(&mut mir.blocks[t].terminator, Terminator::Exit);
        mir.blocks[b].insts.extend(moved);
        mir.blocks[b].terminator = term;
        // Re-key phi args in the absorbed block's former successors
        // (defensive; mirrors legacy CoalesceFlow's `args[block] =
        // args.pop(next_block)`).
        let succs: Vec<BlockId> = mir.blocks[b].terminator.successors().collect();
        for s in succs {
            let phis = mir.blocks[s].phis.clone();
            for phi in phis {
                if let Inst::Phi { args } = &mut mir.insts[phi as usize] {
                    for (p, _) in args.iter_mut() {
                        if *p == t {
                            *p = b;
                        }
                    }
                }
            }
        }
        changed = true;
    }
}

// ----------------------------------------------------------------------------------
// Observability classification (trap conservatism)
// ----------------------------------------------------------------------------------

/// Pure ops that can never raise for **any** f64 inputs, per the `interpret.rs`
/// `py_*` kernels. Everything else is conservatively kept when dead. Notable
/// exclusions and why:
///
/// - `Divide`/`Mod`/`Power`/`Rem`, `Unlerp(Clamped)`, `Remap(Clamped)`:
///   division by zero / domain / overflow errors.
/// - `Floor`/`Ceil`/`Round`/`Trunc`: NaN/inf → `ValueError`/`OverflowError`.
/// - `Sin`/`Cos`/`Tan`: infinite input → `ValueError`; `Sinh`/`Cosh`:
///   overflow; `Log`/`Arcsin`/`Arccos`: domain errors.
/// - `Frac` IS total: floor-mod by the constant 1 cannot divide by zero and
///   NaN/inf pass through.
///
/// The unit test below pins every whitelisted op as `pure` in the op table.
/// `pub(crate)`: the W3 LICM pass (`passes::licm`) uses the same whitelist as
/// its speculation-safety gate (hoisting to a preheader executes an op the
/// loop body might never have executed, so only never-raising ops may move).
pub(crate) fn op_is_total(op: Op) -> bool {
    matches!(
        op,
        Op::Abs
            | Op::Add
            | Op::Arctan
            | Op::Arctan2
            | Op::Clamp
            | Op::Degree
            | Op::Equal
            | Op::Frac
            | Op::Greater
            | Op::GreaterOr
            | Op::Lerp
            | Op::LerpClamped
            | Op::Less
            | Op::LessOr
            | Op::Max
            | Op::Min
            | Op::Multiply
            | Op::Negate
            | Op::Not
            | Op::NotEqual
            | Op::Radian
            | Op::Sign
            | Op::Subtract
            | Op::Tanh
    )
}

/// Whether a constant temp index (plus place offset) is provably in bounds.
/// In-bounds temp accesses land inside the allocated block-10000 window
/// (≤ 4096 slots), so the runtime index assert cannot fire.
fn temp_index_in_bounds(mir: &Mir, t: TempId, index: i64, offset: i64) -> bool {
    let Some(total) = index.checked_add(offset) else {
        return false;
    };
    u64::try_from(total).is_ok_and(|x| x < mir.temps[t].size)
}

/// A load that can never trap: constant index on a non-computed block, with
/// the folded `index + offset` provably inside the runtime assert range
/// (`0..=65535` for concrete blocks; the temp's own size for temps). Any
/// dynamic component may evaluate to NaN/non-integral/out-of-range and trap.
/// `pub(crate)`: the W4 if-conversion pass (`passes::if_convert`) uses the
/// same proof for its pure-total arm-tree hoisting (an arm value tree may
/// move to the head — executed on the untaken path too — only when nothing
/// in it can raise or write).
pub(crate) fn load_is_total(mir: &Mir, place: &Place) -> bool {
    let IndexRef::Const(i) = place.index else {
        return false;
    };
    match place.block {
        // Any i64 block id reads fine (missing blocks read the default fill);
        // only the index is asserted.
        BlockRef::Concrete(_) => i
            .checked_add(place.offset)
            .is_some_and(|x| (0..=65535).contains(&x)),
        BlockRef::Temp(t) => temp_index_in_bounds(mir, t, i, place.offset),
        BlockRef::Value(_) => false,
    }
}

/// Whether a dead `ShortCircuit` may be removed: every instruction **owned**
/// by its lazy rhs tree must be effect-free, RNG-free, and total — evaluating
/// or skipping the tree must be unobservable (the tree's effects run only
/// conditionally, so a kept owner preserves them exactly; D11). Scheduled
/// values referenced from the tree (out of contract, defensive) are not
/// owned: they evaluate eagerly either way and are classified on their own.
fn lazy_tree_removable(mir: &Mir, scheduled: &[bool], rhs: Value) -> bool {
    let mut stack = vec![rhs];
    while let Some(v) = stack.pop() {
        if mir.is_const(v) || scheduled[v as usize] {
            continue;
        }
        match mir.inst(v) {
            Inst::ConstInt(_) | Inst::ConstFloat(_) => {}
            Inst::Op { op, args, .. } => {
                let e = op_effects(*op);
                if e.writes_memory || e.rng || !op_is_total(*op) {
                    return false;
                }
                stack.extend(args.iter().copied());
            }
            Inst::ShortCircuit { lhs, rhs, .. } => {
                // The fold itself is total; both sides are part of the tree.
                stack.push(*lhs);
                stack.push(*rhs);
            }
            Inst::Select {
                test,
                then_root,
                else_root,
            } => {
                // The select itself is total (a 3-arg `If` cannot trap); the
                // test and both arms are part of the tree.
                stack.push(*test);
                stack.push(*then_root);
                stack.push(*else_root);
            }
            Inst::Load { place } => {
                if !load_is_total(mir, place) {
                    return false;
                }
                // A total load has no value operands (constant index,
                // non-computed block): nothing further to walk.
            }
            // A store inside a lazy tree (a W4 if-conversion arm statement)
            // is a conditional write the owner must preserve; phis are out
            // of contract — keep the owner rather than guessing.
            Inst::Store { .. } | Inst::Phi { .. } => return false,
        }
    }
    true
}

// ----------------------------------------------------------------------------------
// Dead-store elimination + mark-live sweep
// ----------------------------------------------------------------------------------

fn mark(mir: &Mir, live: &mut [bool], work: &mut Vec<Value>, v: Value) {
    if !mir.is_const(v) && !live[v as usize] {
        live[v as usize] = true;
        work.push(v);
    }
}

/// Transforms 5 + 6: dead-store analysis, then mark-live + sweep. Returns
/// `(schedules changed, phis changed)`.
fn sweep_dead(mir: &mut Mir) -> (bool, bool) {
    let scheduled = mir.scheduled_mask();
    let liveness = Liveness::compute(mir);

    // Dead temp stores: an eager, provably non-trapping store to a temp that
    // is not live immediately after it (whole-temp granularity; the module
    // docs spell out the precision). Dynamic-index stores are never removed —
    // their index evaluation can trap, which the baseline observes.
    let mut dead_store = vec![false; mir.insts.len()];
    for b in 0..mir.blocks.len() {
        let mut cursor = liveness.cursor_at_end(mir, b);
        while cursor.pos() > 0 {
            // Before stepping, the cursor's sets describe the point just
            // *after* the instruction at `pos() - 1`.
            let v = mir.blocks[b].insts[cursor.pos() - 1];
            if let Inst::Store { place, .. } = mir.inst(v)
                && let BlockRef::Temp(t) = place.block
                && let IndexRef::Const(i) = place.index
                && temp_index_in_bounds(mir, t, i, place.offset)
                && !cursor.temp_live(t)
            {
                dead_store[v as usize] = true;
            }
            cursor.step_back();
        }
    }

    // Mark: roots are scheduled instructions whose execution is observable
    // (module docs), plus branch tests; live phis pull in their arguments.
    let mut live = vec![false; mir.insts.len()];
    let mut work: Vec<Value> = Vec::new();
    for block in &mir.blocks {
        for &v in &block.insts {
            let keep = match mir.inst(v) {
                // Non-temp-dead stores write observable memory (or are
                // conservatively kept for trap/aliasing reasons).
                Inst::Store { .. } => !dead_store[v as usize],
                Inst::Op { op, .. } => {
                    let e = op_effects(*op);
                    // RNG draws are NEVER removed: draw count/order is part
                    // of the differential contract even when the value is
                    // unused. Non-total pure ops are kept for trap fidelity.
                    e.writes_memory || e.rng || !op_is_total(*op)
                }
                Inst::Load { place } => !load_is_total(mir, place),
                Inst::ShortCircuit { rhs, .. } => !lazy_tree_removable(mir, &scheduled, *rhs),
                Inst::Select {
                    then_root,
                    else_root,
                    ..
                } => {
                    // Removable only when *both* arms are removable (either
                    // may be the taken one). The eager test is a separate
                    // scheduled instruction, classified on its own.
                    !lazy_tree_removable(mir, &scheduled, *then_root)
                        || !lazy_tree_removable(mir, &scheduled, *else_root)
                }
                Inst::ConstInt(_) | Inst::ConstFloat(_) | Inst::Phi { .. } => false,
            };
            if keep {
                mark(mir, &mut live, &mut work, v);
            }
        }
        if let Terminator::Branch { test, .. } = &block.terminator {
            mark(mir, &mut live, &mut work, *test);
        }
    }
    while let Some(v) = work.pop() {
        if let Inst::Phi { args } = mir.inst(v) {
            // Liveness's inst_effect attributes phi-arg uses to predecessor
            // block ends; for marking, a live phi simply uses its args.
            for &(_, a) in args {
                mark(mir, &mut live, &mut work, a);
            }
            continue;
        }
        // inst_effect surfaces exactly the live-relevant value uses: eager
        // operands, place components, and lazy-tree references to scheduled
        // values (defensive).
        let eff = inst_effect(mir, &scheduled, v);
        for &u in &eff.value_uses {
            mark(mir, &mut live, &mut work, u);
        }
    }

    // Sweep schedules and phi lists (dead arena entries stay behind, like the
    // rewrite driver's replaced instructions — the arena is append-only).
    let mut insts_changed = false;
    let mut phis_changed = false;
    for block in &mut mir.blocks {
        let before = block.insts.len();
        block.insts.retain(|&v| live[v as usize]);
        insts_changed |= block.insts.len() != before;
        let before = block.phis.len();
        block.phis.retain(|&p| live[p as usize]);
        phis_changed |= block.phis.len() != before;
    }
    (insts_changed, phis_changed)
}

#[cfg(test)]
mod tests {
    // Toolchain note: clippy 1.96 newly lints test code under --all-targets;
    // terse local names are the test-builder convention in this module.
    #![allow(clippy::similar_names)]
    use super::*;
    use crate::analysis::Analyses;
    use crate::cfg::{
        BasicBlock, BlockValue, Cfg, Edge, EdgeCond, IndexValue, Node, Place as CfgPlace,
        TempBlockDef,
    };
    use crate::diff::{DiffConfig, DiffOutcome, diff_with};
    use crate::passes::Pipeline;
    use crate::pipeline::{Level, compile_cfg, compile_cfg_stats, compile_cfg_with_pipeline_stats};

    fn run_pass(mir: &mut Mir) -> bool {
        let mut analyses = Analyses::new();
        DcePass.run(mir, &mut analyses)
    }

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

    fn sched(mir: &mut Mir, block: BlockId, inst: Inst) -> Value {
        let v = mir.push_inst(inst);
        mir.blocks[block].insts.push(v);
        v
    }

    fn scheduled_insts(mir: &Mir, block: BlockId) -> Vec<&Inst> {
        mir.blocks[block]
            .insts
            .iter()
            .map(|&v| mir.inst(v))
            .collect()
    }

    #[test]
    fn dead_pure_op_is_removed() {
        // An unused Add of two constants disappears; the observable store stays.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let c1 = mir.push_inst(Inst::ConstInt(1));
        let c2 = mir.push_inst(Inst::ConstInt(2));
        sched(
            &mut mir,
            b0,
            Inst::Op {
                op: Op::Add,
                pure_node: true,
                args: vec![c1, c2],
            },
        );
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: concrete_place(20, 0),
                value: c1,
            },
        );
        assert!(run_pass(&mut mir));
        assert_eq!(mir.blocks[b0].insts.len(), 1);
        assert!(matches!(
            mir.inst(mir.blocks[b0].insts[0]),
            Inst::Store { .. }
        ));
    }

    #[test]
    fn dead_chain_cascades_in_one_run() {
        // load -> Negate -> (unused): both removed in a single pass run.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let load = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(20, 0),
            },
        );
        sched(
            &mut mir,
            b0,
            Inst::Op {
                op: Op::Negate,
                pure_node: true,
                args: vec![load],
            },
        );
        assert!(run_pass(&mut mir));
        assert!(mir.blocks[b0].insts.is_empty());
    }

    #[test]
    fn unused_rng_draw_is_kept_and_its_dead_store_removed() {
        // t <- Random(0, 1) with t never loaded: the store dies, the draw stays
        // (draw count/order is differentially observable).
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 1);
        let b0 = mir.push_block();
        let lo = mir.push_inst(Inst::ConstInt(0));
        let hi = mir.push_inst(Inst::ConstInt(1));
        let draw = sched(
            &mut mir,
            b0,
            Inst::Op {
                op: Op::Random,
                pure_node: false,
                args: vec![lo, hi],
            },
        );
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: temp_place(t),
                value: draw,
            },
        );
        assert!(run_pass(&mut mir));
        let insts = scheduled_insts(&mir, b0);
        assert_eq!(insts.len(), 1, "store removed, draw kept: {insts:?}");
        assert!(matches!(insts[0], Inst::Op { op: Op::Random, .. }));
    }

    #[test]
    fn trap_capable_pure_op_is_kept() {
        // An unused Divide may raise (division by zero) — the baseline would
        // observe the trap, so it stays.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let load = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(20, 0),
            },
        );
        let one = mir.push_inst(Inst::ConstInt(1));
        sched(
            &mut mir,
            b0,
            Inst::Op {
                op: Op::Divide,
                pure_node: true,
                args: vec![one, load],
            },
        );
        assert!(!run_pass(&mut mir), "nothing is removable");
        assert_eq!(mir.blocks[b0].insts.len(), 2);
    }

    #[test]
    fn dynamic_index_load_is_kept_when_dead() {
        // A dead load with a dynamic index can trap on NaN/out-of-range.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let idx = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(20, 0),
            },
        );
        sched(
            &mut mir,
            b0,
            Inst::Load {
                place: Place {
                    block: BlockRef::Concrete(21),
                    index: IndexRef::Value(idx),
                    offset: 0,
                },
            },
        );
        assert!(!run_pass(&mut mir));
        assert_eq!(mir.blocks[b0].insts.len(), 2);
    }

    #[test]
    fn out_of_range_const_index_load_is_kept() {
        // Index -1 trips the runtime assert; index 70000 exceeds 65535.
        for index in [-1, 70_000] {
            let mut mir = Mir::new();
            let b0 = mir.push_block();
            sched(
                &mut mir,
                b0,
                Inst::Load {
                    place: concrete_place(20, index),
                },
            );
            assert!(!run_pass(&mut mir), "index {index} must be kept");
            assert_eq!(mir.blocks[b0].insts.len(), 1);
        }
    }

    #[test]
    fn unused_short_circuit_with_lazy_effect_is_kept() {
        // sc = And(c, lazy DebugLog(7)) unused: the log runs conditionally
        // when c is truthy — the instruction must stay.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let c = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(20, 0),
            },
        );
        let seven = mir.push_inst(Inst::ConstInt(7));
        let log = mir.push_inst(Inst::Op {
            op: Op::DebugLog,
            pure_node: false,
            args: vec![seven],
        }); // unscheduled: lazy
        sched(
            &mut mir,
            b0,
            Inst::ShortCircuit {
                op: Op::And,
                pure_node: true,
                lhs: c,
                rhs: log,
            },
        );
        assert!(!run_pass(&mut mir));
        assert_eq!(mir.blocks[b0].insts.len(), 2);
    }

    #[test]
    fn unused_short_circuit_with_lazy_rng_is_kept() {
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let c = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(20, 0),
            },
        );
        let zero = mir.push_inst(Inst::ConstInt(0));
        let two = mir.push_inst(Inst::ConstInt(2));
        let rng = mir.push_inst(Inst::Op {
            op: Op::RandomInteger,
            pure_node: false,
            args: vec![zero, two],
        }); // lazy conditional draw
        sched(
            &mut mir,
            b0,
            Inst::ShortCircuit {
                op: Op::Or,
                pure_node: true,
                lhs: c,
                rhs: rng,
            },
        );
        assert!(!run_pass(&mut mir));
        assert_eq!(mir.blocks[b0].insts.len(), 2);
    }

    #[test]
    fn unused_pure_short_circuit_is_removed() {
        // sc = And(load 20[0], lazy And(1, load t[0])) unused: the whole lazy
        // tree is total and effect-free, the eager lhs is a total load — all
        // of it goes.
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 1);
        let b0 = mir.push_block();
        let lhs = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(20, 0),
            },
        );
        let one = mir.push_inst(Inst::ConstInt(1));
        let lazy_load = mir.push_inst(Inst::Load {
            place: temp_place(t),
        });
        let inner = mir.push_inst(Inst::ShortCircuit {
            op: Op::And,
            pure_node: true,
            lhs: one,
            rhs: lazy_load,
        });
        sched(
            &mut mir,
            b0,
            Inst::ShortCircuit {
                op: Op::And,
                pure_node: true,
                lhs,
                rhs: inner,
            },
        );
        assert!(run_pass(&mut mir));
        assert!(mir.blocks[b0].insts.is_empty());
    }

    #[test]
    fn unused_short_circuit_with_trap_capable_lazy_tree_is_kept() {
        // The lazy side divides — conditionally trapping, so the owner stays.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let c = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(20, 0),
            },
        );
        let one = mir.push_inst(Inst::ConstInt(1));
        let zero = mir.push_inst(Inst::ConstInt(0));
        let div = mir.push_inst(Inst::Op {
            op: Op::Divide,
            pure_node: true,
            args: vec![one, zero],
        }); // lazy
        sched(
            &mut mir,
            b0,
            Inst::ShortCircuit {
                op: Op::And,
                pure_node: true,
                lhs: c,
                rhs: div,
            },
        );
        assert!(!run_pass(&mut mir));
        assert_eq!(mir.blocks[b0].insts.len(), 2);
    }

    #[test]
    fn stores_to_never_loaded_temp_are_removed() {
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 4);
        let b0 = mir.push_block();
        let c = mir.push_inst(Inst::ConstInt(7));
        for i in 0..3 {
            sched(
                &mut mir,
                b0,
                Inst::Store {
                    place: Place {
                        block: BlockRef::Temp(t),
                        index: IndexRef::Const(i),
                        offset: 0,
                    },
                    value: c,
                },
            );
        }
        assert!(run_pass(&mut mir));
        assert!(mir.blocks[b0].insts.is_empty());
    }

    #[test]
    fn dynamic_index_load_keeps_all_stores_to_the_temp() {
        // store t[0]; store t[2]; v = load t[dyn]; out <- v.
        // Whole-temp granularity: the dynamic load may read any slot.
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 4);
        let b0 = mir.push_block();
        let c = mir.push_inst(Inst::ConstInt(7));
        for i in [0, 2] {
            sched(
                &mut mir,
                b0,
                Inst::Store {
                    place: Place {
                        block: BlockRef::Temp(t),
                        index: IndexRef::Const(i),
                        offset: 0,
                    },
                    value: c,
                },
            );
        }
        let idx = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(20, 0),
            },
        );
        let v = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: Place {
                    block: BlockRef::Temp(t),
                    index: IndexRef::Value(idx),
                    offset: 0,
                },
            },
        );
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: concrete_place(21, 0),
                value: v,
            },
        );
        assert!(!run_pass(&mut mir), "everything is live");
        assert_eq!(mir.blocks[b0].insts.len(), 5);
    }

    #[test]
    fn store_after_last_load_is_removed() {
        // store t <- 1; out <- load t; store t <- 2 (dead: nothing reads t
        // again).
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 1);
        let b0 = mir.push_block();
        let one = mir.push_inst(Inst::ConstInt(1));
        let two = mir.push_inst(Inst::ConstInt(2));
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: temp_place(t),
                value: one,
            },
        );
        let v = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: temp_place(t),
            },
        );
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: concrete_place(20, 0),
                value: v,
            },
        );
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: temp_place(t),
                value: two,
            },
        );
        assert!(run_pass(&mut mir));
        let insts = scheduled_insts(&mir, b0);
        assert_eq!(insts.len(), 3, "{insts:?}");
        assert!(matches!(
            insts[2],
            Inst::Store {
                place: Place {
                    block: BlockRef::Concrete(20),
                    ..
                },
                ..
            }
        ));
    }

    #[test]
    fn store_overwritten_before_load_is_removed() {
        // One-cell temps kill on store: the first store is dead.
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 1);
        let b0 = mir.push_block();
        let one = mir.push_inst(Inst::ConstInt(1));
        let two = mir.push_inst(Inst::ConstInt(2));
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: temp_place(t),
                value: one,
            },
        );
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: temp_place(t),
                value: two,
            },
        );
        let v = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: temp_place(t),
            },
        );
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: concrete_place(20, 0),
                value: v,
            },
        );
        assert!(run_pass(&mut mir));
        let insts = scheduled_insts(&mir, b0);
        assert_eq!(insts.len(), 3);
        let Inst::Store { value, .. } = insts[0] else {
            panic!("first kept inst must be the surviving store: {insts:?}");
        };
        assert_eq!(mir.inst(*value), &Inst::ConstInt(2));
    }

    #[test]
    fn dynamic_index_store_is_kept_even_if_never_loaded() {
        // The index evaluation can trap; conservatively kept (module docs).
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 4);
        let b0 = mir.push_block();
        let idx = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(20, 0),
            },
        );
        let c = mir.push_inst(Inst::ConstInt(7));
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: Place {
                    block: BlockRef::Temp(t),
                    index: IndexRef::Value(idx),
                    offset: 0,
                },
                value: c,
            },
        );
        assert!(!run_pass(&mut mir));
        assert_eq!(mir.blocks[b0].insts.len(), 2);
    }

    #[test]
    fn out_of_bounds_const_index_store_is_kept() {
        // Out-of-bounds writes are allocation-dependent / trapping; kept.
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 2);
        let b0 = mir.push_block();
        let c = mir.push_inst(Inst::ConstInt(7));
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: Place {
                    block: BlockRef::Temp(t),
                    index: IndexRef::Const(5),
                    offset: 0,
                },
                value: c,
            },
        );
        assert!(!run_pass(&mut mir));
        assert_eq!(mir.blocks[b0].insts.len(), 1);
    }

    /// Three-block diamond on a branch: entry branches to two stores.
    fn branch_mir(
        test_inst: Inst,
        cases: Vec<(CaseCond, BlockId)>,
        default: Option<BlockId>,
    ) -> Mir {
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let b2 = mir.push_block();
        for (b, val) in [(b1, 10), (b2, 20)] {
            let c = mir.push_inst(Inst::ConstInt(val));
            sched(
                &mut mir,
                b,
                Inst::Store {
                    place: concrete_place(20, 0),
                    value: c,
                },
            );
        }
        let test = mir.push_inst(test_inst);
        mir.blocks[b0].terminator = Terminator::Branch {
            test,
            cases,
            default,
        };
        mir
    }

    #[test]
    fn const_test_branch_folds_to_matching_case() {
        let mut mir = branch_mir(Inst::ConstInt(1), vec![(CaseCond::Int(1), 1)], Some(2));
        assert!(run_pass(&mut mir));
        // Entry jumps (and merges) into b1; b2 is unreachable and cleared.
        assert!(mir.blocks[2].insts.is_empty());
        let stores: Vec<i64> = mir.blocks[0]
            .insts
            .iter()
            .filter_map(|&v| match mir.inst(v) {
                Inst::Store { value, .. } => match mir.inst(*value) {
                    Inst::ConstInt(c) => Some(*c),
                    _ => None,
                },
                _ => None,
            })
            .collect();
        assert_eq!(stores, vec![10]);
    }

    #[test]
    fn float_const_test_matches_int_case() {
        // Python ==: 1.0 matches the int cond 1.
        let mut mir = branch_mir(Inst::ConstFloat(1.0), vec![(CaseCond::Int(1), 1)], Some(2));
        assert!(run_pass(&mut mir));
        assert!(mir.blocks[2].insts.is_empty(), "default arm is dead");
        assert!(!mir.blocks[0].insts.is_empty());
    }

    #[test]
    fn int_const_test_matches_float_case() {
        let mut mir = branch_mir(Inst::ConstInt(2), vec![(CaseCond::Float(2.0), 1)], Some(2));
        assert!(run_pass(&mut mir));
        assert!(mir.blocks[2].insts.is_empty(), "default arm is dead");
    }

    #[test]
    fn nan_const_test_takes_the_default() {
        let mut mir = branch_mir(
            Inst::ConstFloat(f64::NAN),
            vec![(CaseCond::Float(1.5), 1)],
            Some(2),
        );
        // NaN == anything is false in Python; the default edge wins.
        assert!(run_pass(&mut mir));
        assert!(mir.blocks[1].insts.is_empty(), "case arm is dead");
        assert!(!mir.blocks[0].insts.is_empty());
    }

    #[test]
    fn const_test_with_no_match_and_no_default_becomes_exit() {
        let mut mir = branch_mir(Inst::ConstInt(5), vec![(CaseCond::Int(1), 1)], None);
        assert!(run_pass(&mut mir));
        assert_eq!(mir.blocks[0].terminator, Terminator::Exit);
        assert!(mir.blocks[1].insts.is_empty());
        assert!(mir.blocks[2].insts.is_empty());
    }

    #[test]
    fn duplicate_default_cases_collapse_to_jump() {
        // Branch {1: b1, 2: b1, default: b1} on a non-const test -> Jump(b1);
        // the (total) test load is then swept.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let c = mir.push_inst(Inst::ConstInt(10));
        sched(
            &mut mir,
            b1,
            Inst::Store {
                place: concrete_place(20, 0),
                value: c,
            },
        );
        let test = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(21, 0),
            },
        );
        mir.blocks[b0].terminator = Terminator::Branch {
            test,
            cases: vec![(CaseCond::Int(1), b1), (CaseCond::Int(2), b1)],
            default: Some(b1),
        };
        assert!(run_pass(&mut mir));
        // b1 merged into b0; the dead total test load swept.
        assert_eq!(mir.blocks[b0].terminator, Terminator::Exit);
        let insts = scheduled_insts(&mir, b0);
        assert_eq!(insts.len(), 1, "{insts:?}");
        assert!(matches!(insts[0], Inst::Store { .. }));
    }

    #[test]
    fn cases_without_default_are_not_collapsed() {
        // Branch {1: b1, 2: b1} with NO default: an unmatched test exits, so
        // this must stay a branch.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let c = mir.push_inst(Inst::ConstInt(10));
        sched(
            &mut mir,
            b1,
            Inst::Store {
                place: concrete_place(20, 0),
                value: c,
            },
        );
        let test = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(21, 0),
            },
        );
        mir.blocks[b0].terminator = Terminator::Branch {
            test,
            cases: vec![(CaseCond::Int(1), b1), (CaseCond::Int(2), b1)],
            default: None,
        };
        assert!(!run_pass(&mut mir));
        assert!(matches!(
            mir.blocks[b0].terminator,
            Terminator::Branch { .. }
        ));
    }

    #[test]
    fn empty_block_chains_thread_and_merge() {
        // b0 -> e1 -> e2 -> b3(store): collapses to a single block.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let e1 = mir.push_block();
        let e2 = mir.push_block();
        let b3 = mir.push_block();
        mir.blocks[b0].terminator = Terminator::Jump(e1);
        mir.blocks[e1].terminator = Terminator::Jump(e2);
        mir.blocks[e2].terminator = Terminator::Jump(b3);
        let c = mir.push_inst(Inst::ConstInt(10));
        sched(
            &mut mir,
            b3,
            Inst::Store {
                place: concrete_place(20, 0),
                value: c,
            },
        );
        assert!(run_pass(&mut mir));
        assert_eq!(mir.blocks[b0].terminator, Terminator::Exit);
        assert_eq!(mir.blocks[b0].insts.len(), 1);
        for b in [e1, e2, b3] {
            assert!(mir.blocks[b].insts.is_empty());
            assert_eq!(mir.blocks[b].terminator, Terminator::Exit);
        }
    }

    #[test]
    fn branch_edges_thread_through_empty_blocks() {
        // b0 branches {0: e1 -> b3, default: b3}: threading makes the case
        // edge a duplicate of the default, which then collapses to a jump.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let e1 = mir.push_block();
        let b3 = mir.push_block();
        mir.blocks[e1].terminator = Terminator::Jump(b3);
        let c = mir.push_inst(Inst::ConstInt(10));
        sched(
            &mut mir,
            b3,
            Inst::Store {
                place: concrete_place(20, 0),
                value: c,
            },
        );
        let test = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(21, 0),
            },
        );
        mir.blocks[b0].terminator = Terminator::Branch {
            test,
            cases: vec![(CaseCond::Int(0), e1)],
            default: Some(b3),
        };
        assert!(run_pass(&mut mir));
        assert_eq!(mir.blocks[b0].terminator, Terminator::Exit, "fully merged");
        let insts = scheduled_insts(&mir, b0);
        assert_eq!(insts.len(), 1, "test load swept, store merged: {insts:?}");
        assert!(matches!(insts[0], Inst::Store { .. }));
    }

    #[test]
    fn phi_bearing_destination_is_not_threaded() {
        // b0 -> e (empty) -> b2 where b2 has a phi: the edge must stay on e
        // (threading would change b2's phi predecessor keying), but merging
        // e into b0 (which re-keys the phi) is fine.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let e = mir.push_block();
        let b2 = mir.push_block();
        mir.blocks[b0].terminator = Terminator::Jump(e);
        mir.blocks[e].terminator = Terminator::Jump(b2);
        let c = mir.push_inst(Inst::ConstInt(1));
        let phi = mir.push_inst(Inst::Phi { args: vec![(e, c)] });
        mir.blocks[b2].phis.push(phi);
        sched(
            &mut mir,
            b2,
            Inst::Store {
                place: concrete_place(20, 0),
                value: phi,
            },
        );
        assert!(run_pass(&mut mir));
        // e was merged into b0 (single pred, no phis of its own); the phi arg
        // is re-keyed from e to b0 and b2 stays separate (it has a phi).
        assert_eq!(mir.blocks[b0].terminator, Terminator::Jump(b2));
        let Inst::Phi { args } = mir.inst(phi) else {
            panic!("phi survives");
        };
        assert_eq!(args.as_slice(), &[(b0, c)]);
    }

    #[test]
    fn phi_bearing_empty_block_is_not_threaded_or_merged() {
        // The empty block itself carries a phi: leave everything alone.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let e = mir.push_block();
        let b2 = mir.push_block();
        mir.blocks[b0].terminator = Terminator::Jump(e);
        mir.blocks[e].terminator = Terminator::Jump(b2);
        let c = mir.push_inst(Inst::ConstInt(1));
        let phi = mir.push_inst(Inst::Phi {
            args: vec![(b0, c)],
        });
        mir.blocks[e].phis.push(phi);
        sched(
            &mut mir,
            b2,
            Inst::Store {
                place: concrete_place(20, 0),
                value: phi,
            },
        );
        let changed = run_pass(&mut mir);
        assert_eq!(mir.blocks[b0].terminator, Terminator::Jump(e));
        assert_eq!(mir.blocks[e].phis.len(), 1);
        // b2 merging into e is fine (b2 has no phis) — either way the phi and
        // the store survive.
        let _ = changed;
        let all_stores: usize = mir
            .blocks
            .iter()
            .flat_map(|b| &b.insts)
            .filter(|&&v| matches!(mir.inst(v), Inst::Store { .. }))
            .count();
        assert_eq!(all_stores, 1);
    }

    #[test]
    fn empty_block_cycles_terminate_and_stay_put() {
        // b0 -> e1 <-> e2 (empty cycle): the walk must terminate without
        // retargeting (idempotence), and the pass must converge.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let e1 = mir.push_block();
        let e2 = mir.push_block();
        mir.blocks[b0].terminator = Terminator::Jump(e1);
        mir.blocks[e1].terminator = Terminator::Jump(e2);
        mir.blocks[e2].terminator = Terminator::Jump(e1);
        let first = run_pass(&mut mir);
        let second = run_pass(&mut mir);
        assert!(
            !second,
            "second run must be a no-op (got change after {first})"
        );
    }

    #[test]
    fn unreachable_blocks_are_cleared() {
        // b1 is never targeted: its store and edge disappear.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let c = mir.push_inst(Inst::ConstInt(1));
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: concrete_place(20, 0),
                value: c,
            },
        );
        sched(
            &mut mir,
            b1,
            Inst::Store {
                place: concrete_place(21, 0),
                value: c,
            },
        );
        mir.blocks[b1].terminator = Terminator::Jump(b0);
        assert!(run_pass(&mut mir));
        assert!(mir.blocks[b1].insts.is_empty());
        assert_eq!(mir.blocks[b1].terminator, Terminator::Exit);
        assert_eq!(mir.blocks[b0].insts.len(), 1);
    }

    #[test]
    fn dead_phi_is_swept_and_live_phi_keeps_args_alive() {
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        mir.blocks[b0].terminator = Terminator::Jump(b1);
        let used = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(20, 0),
            },
        );
        let dead_arg = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(20, 1),
            },
        );
        let live_phi = mir.push_inst(Inst::Phi {
            args: vec![(b0, used)],
        });
        let dead_phi = mir.push_inst(Inst::Phi {
            args: vec![(b0, dead_arg)],
        });
        mir.blocks[b1].phis.push(live_phi);
        mir.blocks[b1].phis.push(dead_phi);
        sched(
            &mut mir,
            b1,
            Inst::Store {
                place: concrete_place(21, 0),
                value: live_phi,
            },
        );
        assert!(run_pass(&mut mir));
        assert_eq!(mir.blocks[b1].phis.as_slice(), &[live_phi]);
        // The dead phi's argument load was only used by it: swept too.
        assert_eq!(mir.blocks[b0].insts.as_slice(), &[used]);
    }

    #[test]
    fn pass_is_idempotent() {
        // A mixed program: dead store, dead pure chain, branch with duplicate
        // default, empty block. One run cleans it; the second is a no-op.
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 1);
        let b0 = mir.push_block();
        let e1 = mir.push_block();
        let b2 = mir.push_block();
        let c = mir.push_inst(Inst::ConstInt(3));
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: temp_place(t),
                value: c,
            },
        );
        let test = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(20, 0),
            },
        );
        mir.blocks[b0].terminator = Terminator::Branch {
            test,
            cases: vec![(CaseCond::Int(0), e1)],
            default: Some(b2),
        };
        mir.blocks[e1].terminator = Terminator::Jump(b2);
        sched(
            &mut mir,
            b2,
            Inst::Store {
                place: concrete_place(21, 0),
                value: c,
            },
        );
        assert!(run_pass(&mut mir), "first run cleans up");
        assert!(!run_pass(&mut mir), "second run is a no-op");
    }

    #[test]
    fn empty_mir_is_a_no_op() {
        let mut mir = Mir::new();
        assert!(!run_pass(&mut mir));
        let b0 = mir.push_block();
        assert!(!run_pass(&mut mir));
        assert_eq!(mir.blocks[b0].terminator, Terminator::Exit);
    }

    #[test]
    fn whitelisted_total_ops_are_pure() {
        // Every op the sweep treats as removable-when-dead must be pure in
        // the generated op table (no writes, no RNG; reads would be Loads).
        for op in Op::all() {
            if op_is_total(op) {
                assert!(op.pure(), "{} is whitelisted but not pure", op.name());
            }
        }
        // And the known trap-capable pure ops must NOT be whitelisted.
        for op in [
            Op::Divide,
            Op::Mod,
            Op::Power,
            Op::Rem,
            Op::Floor,
            Op::Ceil,
            Op::Round,
            Op::Trunc,
            Op::Sin,
            Op::Cos,
            Op::Tan,
            Op::Sinh,
            Op::Cosh,
            Op::Log,
            Op::Arcsin,
            Op::Arccos,
            Op::Unlerp,
            Op::UnlerpClamped,
            Op::Remap,
            Op::RemapClamped,
        ] {
            assert!(!op_is_total(op), "{} can raise", op.name());
        }
    }

    // ------------------------------------------------------------------------------
    // End-to-end: frontend CFG -> minimal+[dce] pipeline
    // ------------------------------------------------------------------------------

    /// Tiny frontend-CFG builder (mirrors the one in diff.rs tests).
    #[derive(Default)]
    struct B {
        cfg: Cfg,
    }

    impl B {
        fn node(&mut self, n: Node) -> usize {
            self.cfg.nodes.push(n);
            self.cfg.nodes.len() - 1
        }
        fn temp(&mut self, name: &str, size: u64) -> usize {
            self.cfg.strings.push(name.to_owned());
            self.cfg.temp_blocks.push(TempBlockDef {
                name: self.cfg.strings.len() - 1,
                size,
            });
            self.cfg.temp_blocks.len() - 1
        }
        fn place_int(&mut self, block: i64, index: i64) -> usize {
            self.cfg.places.push(CfgPlace {
                block: BlockValue::Int(block),
                index: IndexValue::Int(index),
                offset: 0,
            });
            self.cfg.places.len() - 1
        }
        fn temp_place(&mut self, temp: usize) -> usize {
            self.cfg.places.push(CfgPlace {
                block: BlockValue::Temp(temp),
                index: IndexValue::Int(0),
                offset: 0,
            });
            self.cfg.places.len() - 1
        }
        fn int(&mut self, v: i64) -> usize {
            self.node(Node::ConstInt(v))
        }
        fn set(&mut self, place: usize, value: usize) -> usize {
            self.node(Node::Set { place, value })
        }
        fn block(&mut self, statements: Vec<usize>, test: usize, outgoing: Vec<Edge>) {
            self.cfg.blocks.push(BasicBlock {
                statements,
                test,
                outgoing,
            });
        }
    }

    fn dce_pipeline() -> Pipeline {
        Pipeline::new(vec![Box::new(DcePass)])
    }

    /// Frontend CFG with a dead temp store, a dead pure computation, and an
    /// empty diamond, plus one observable store.
    fn deadcode_cfg() -> Cfg {
        let mut b = B::default();
        let t = b.temp("dead", 1);
        let tp = b.temp_place(t);
        // dead: t <- Add(load 20[1], 1)  (t never read)
        let read_p = b.place_int(20, 1);
        let read = b.node(Node::Get(read_p));
        let one = b.int(1);
        let add = b.node(Node::PureInstr {
            op: Op::Add,
            args: vec![read, one],
        });
        let dead_set = b.set(tp, add);
        // live: 21[0] <- 7
        let out_p = b.place_int(21, 0);
        let seven = b.int(7);
        let live_set = b.set(out_p, seven);
        // diamond on load 20[0] with two EMPTY arms re-joining.
        let test_p = b.place_int(20, 0);
        let test = b.node(Node::Get(test_p));
        b.block(
            vec![dead_set, live_set],
            test,
            vec![
                Edge {
                    cond: EdgeCond::Int(0),
                    target: 1,
                },
                Edge {
                    cond: EdgeCond::None,
                    target: 2,
                },
            ],
        );
        let zt1 = b.int(0);
        b.block(
            vec![],
            zt1,
            vec![Edge {
                cond: EdgeCond::None,
                target: 3,
            }],
        );
        let zt2 = b.int(0);
        b.block(
            vec![],
            zt2,
            vec![Edge {
                cond: EdgeCond::None,
                target: 3,
            }],
        );
        // join block: 21[1] <- 9
        let out2_p = b.place_int(21, 1);
        let nine = b.int(9);
        let set2 = b.set(out2_p, nine);
        let zt3 = b.int(0);
        b.block(vec![set2], zt3, vec![]);
        b.cfg
    }

    #[test]
    fn pass_reduces_static_nodes_and_stays_lowerable() {
        let cfg = deadcode_cfg();
        let (_, minimal_stats) = compile_cfg_stats(&cfg, Level::Minimal).unwrap();
        let (_, dce_stats) = compile_cfg_with_pipeline_stats(&cfg, &dce_pipeline()).unwrap();
        assert!(
            dce_stats.node_count < minimal_stats.node_count,
            "the pass must fire: {} -> {}",
            minimal_stats.node_count,
            dce_stats.node_count
        );
        assert!(dce_stats.temp_slots_used <= minimal_stats.temp_slots_used);
    }

    #[test]
    fn deadcode_cfg_diffs_clean_against_minimal() {
        let cfg = deadcode_cfg();
        for seed in [0u64, 1, 42] {
            let config = DiffConfig {
                memory_seed: seed,
                rng_seed: seed ^ 0xABCD,
                eval_budget: 100_000,
            };
            let outcome = diff_with(
                &cfg,
                |c| compile_cfg(c, Level::Minimal),
                |c| crate::pipeline::compile_cfg_with_pipeline(c, &dce_pipeline()),
                &config,
            );
            assert_eq!(outcome, DiffOutcome::Match, "seed {seed}");
        }
    }

    #[test]
    fn unused_rng_draw_count_is_differentially_preserved() {
        // t <- Random(0, 1) with t never read: the store dies but the draw
        // must remain, keeping the RNG stream identical to minimal's.
        let mut b = B::default();
        let t = b.temp("t", 1);
        let tp = b.temp_place(t);
        let lo = b.int(0);
        let hi = b.int(1);
        let draw = b.node(Node::Instr {
            op: Op::Random,
            args: vec![lo, hi],
        });
        let dead_set = b.set(tp, draw);
        // A second, observable draw: its value depends on the stream position.
        let lo2 = b.int(0);
        let hi2 = b.int(100);
        let draw2 = b.node(Node::Instr {
            op: Op::Random,
            args: vec![lo2, hi2],
        });
        let out_p = b.place_int(20, 0);
        let live_set = b.set(out_p, draw2);
        let zt = b.int(0);
        b.block(vec![dead_set, live_set], zt, vec![]);
        let cfg = b.cfg;

        let config = DiffConfig::default();
        let outcome = diff_with(
            &cfg,
            |c| compile_cfg(c, Level::Minimal),
            |c| crate::pipeline::compile_cfg_with_pipeline(c, &dce_pipeline()),
            &config,
        );
        assert_eq!(outcome, DiffOutcome::Match);
        // And the pass did fire (the dead store is gone).
        let (_, minimal_stats) = compile_cfg_stats(&cfg, Level::Minimal).unwrap();
        let (_, dce_stats) = compile_cfg_with_pipeline_stats(&cfg, &dce_pipeline()).unwrap();
        assert!(dce_stats.node_count < minimal_stats.node_count);
    }

    #[test]
    fn changed_flag_and_invalidation_pipeline_contract() {
        // Run through the Pipeline so the debug fingerprint guard verifies the
        // changed flag, on both a changing and a non-changing input.
        let cfg = deadcode_cfg();
        let mut mir = crate::mir::build_mir(&cfg).unwrap();
        let mut analyses = Analyses::new();
        let pipeline = dce_pipeline();
        assert!(pipeline.run(&mut mir, &mut analyses), "first run changes");
        assert!(!pipeline.run(&mut mir, &mut analyses), "fixpoint reached");
    }
}
