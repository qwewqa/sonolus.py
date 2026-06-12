//! W4 JumpLoop-aware CFG shaping (PORT.md T3.9): block merging, exit
//! combining, and tiny-block duplication into predecessors.
//!
//! Every MIR block becomes one `JumpLoop` dispatcher case at emission, so
//! every block boundary on an executed path costs a dispatcher round trip
//! (`dispatch_count`) plus the block's `Execute` wrapper evaluations, and
//! every SSA value that crosses a block boundary is materialized by
//! `ssa::destruct_ssa` as a temp-slot `Set`+`Get` pair (the dominant G3.3
//! quality gap — see the PORT.md deviation log). This pass reshapes the CFG so
//! that fewer boundaries exist on executed paths:
//!
//! # Transform 1 — phi simplification
//!
//! - **Dead phis** (no reference outside their own argument list) are removed.
//!   Pure by definition; mutually-dead phi *cycles* are left (rare; they cost
//!   one slot at destruction, never wrong results).
//! - **Trivial phis** (every non-self argument is one identical `Value` `v`)
//!   are replaced by `v` everywhere and removed — the Braun construction rule,
//!   re-applied late: SCCP edge pruning, DCE branch simplification, and this
//!   pass's own merges all strand single-predecessor (= single-argument) phis
//!   that no registry pass cleaned up before destruction slotted them.
//!   Soundness: `v`'s definition dominates every predecessor edge that carries
//!   it, hence dominates the phi's block and every use the phi dominated.
//!   Replacement is by `Value` identity only (two distinct constant arena
//!   entries with equal content are not unified — GVN's job, not re-proved
//!   here).
//!
//! # Transform 2 — empty-block threading with phi-argument copying
//!
//! DCE's jump threading (T3.3) redirects edges through empty jump-only blocks
//! but stops when the *destination* has phis (re-keying could collide). This
//! pass finishes the job: an edge `P -> E` where `E` is empty (no phis, no
//! scheduled instructions, non-entry), `E != P`, and `E` jumps to `T != E` is
//! redirected to `T` even when `T` has phis, by **copying** the phi arguments:
//! every phi of `T` gains the argument `(P, arg[E])` (`E` may still have other
//! predecessors, so its own keys stay; `dce::clear_unreachable` prunes them
//! when `E` dies). Dominance: `arg[E]` is defined in no `E` (empty), and its
//! definition dominates `E`, so by the path argument it dominates every
//! predecessor `P` of `E` (every path to `P` extended by `E` must contain the
//! definition, which is not `E` itself). Refusals, each load-bearing:
//!
//! - **`P` must end in `Jump`** (single successor). This is a cost rule
//!   imposed by `ssa::destruct_ssa`'s out-of-SSA placement, not a correctness
//!   rule: phi-argument copies for a single-successor predecessor are placed
//!   inline at its end, so the threaded-away block really disappears (one
//!   dispatcher round trip saved per traversal); for a *multi-successor*
//!   predecessor, destruction re-splits the now-critical edge into a fresh
//!   copy block — recreating exactly the block that was threaded away (zero
//!   dynamic gain), and when `E` was shared by several such predecessors the
//!   copies get duplicated into one split block per edge (a static loss).
//!   Measured on `preview/PreviewStage.render`: unrestricted threading left
//!   the emitted tree bit-identical. Branch-edge diamonds are T3.8
//!   if-conversion's job, not threadable here.
//! - some phi of `T` has no argument keyed by `E` (malformed input; defensive);
//! - `P` already reaches `T` directly (an existing edge, or one committed
//!   earlier in this scan) and some phi's existing `P` argument differs from
//!   `arg[E]` — when all arguments agree the edge is redirected without a new
//!   key (defensive: a `Jump` source has no second edge in practice);
//! - `T == P` (would manufacture a self-loop the input did not have).
//!
//! Chains of empty no-phi blocks are walked through first (same guards as
//! DCE: bounded walk, never through the entry, the edge's own source, or a
//! self-loop), so the final hop's phi copy uses the *last* chain block's key.
//! Note the single-predecessor instance of this shape (`P: Jump(E)`, `E`
//! sole-pred) is already covered by transform 4's merge; the phi hop earns
//! its keep on *shared* empty blocks with several jumping predecessors.
//!
//! # Transform 3 — exit shaping
//!
//! The emitted exit is the `JumpLoop` tail: a block whose dispatcher yields
//! the exit index ends the callback *without* another round trip, while
//! jumping to an empty exit block costs one. So:
//!
//! - `Jump(E)` where `E` is an empty exit block (no phis/insts, `Exit`
//!   terminator) becomes `Exit`;
//! - `Branch { default: Some(E) }` with `E` an empty exit block drops the
//!   default — the emitter's no-default dispatchers route unmatched
//!   scrutinees to the exit index (the same observable outcome);
//! - a *case* edge to an empty exit block is dropped when there is **no
//!   default** (matched and unmatched scrutinees both exit), unless dropping
//!   it would break a dense `0..n` case set (the emitter's
//!   `SwitchIntegerWithDefault` O(1) selection — a static-shape cost model,
//!   not a correctness rule). A branch whose cases all drop becomes `Exit`;
//!   its test value stays scheduled, so its evaluation (and any trap) still
//!   happens, in the same last-in-block position.
//!
//! # Transform 4 — branch simplification + chain merging (DCE reuse)
//!
//! After transform 1, single-predecessor jump targets are phi-free, and DCE's
//! merge (schedule concatenation + terminator take-over + successor phi
//! re-keying) absorbs them. Reused directly — this is the step that turns
//! cross-block values into same-block values that lower inline (the `Get`/
//! `Set` drop), and DCE itself does not run after W2 in the registry. DCE's
//! branch simplification runs alongside it: threading can leave a branch
//! whose every edge reaches the default's target, which collapses to a jump
//! and then merges — a fully-decided diamond folds into its head block.
//!
//! # Transform 5 — tiny-block duplication into predecessors
//!
//! A block `T` with several predecessors, at least one of which reaches it by
//! an unconditional `Jump`, costs each jumping predecessor a dispatcher round
//! trip that disappears if the predecessor absorbs a *copy* of `T`. For every
//! predecessor `P` with `Jump(T)`: `T`'s scheduled instructions are cloned
//! into `P` (operands remapped through the clone map; `T`'s phis are not
//! cloned — each use is substituted with the phi's `P`-keyed argument), `P`'s
//! terminator becomes a remapped copy of `T`'s, and every phi in `T`'s
//! successors gains `(P, arg[T])`. `T` keeps serving its other predecessors
//! (or goes unreachable and is cleared when there are none).
//!
//! **Safety argument** (why trap-capable and effectful instructions may be
//! duplicated here): control that entered `T` through `P` executed exactly
//! `P`'s schedule followed by `T`'s. After duplication that same path executes
//! `P`'s schedule followed by the clone — the identical instruction sequence,
//! in the identical order, exactly once. No instruction is ever executed on a
//! path that did not execute it before, so writes, traps, logs, and `Break`s
//! are all preserved per-path. Refusals, each load-bearing:
//!
//! - **RNG ops are never duplicated** (hard rule): although the per-path
//!   argument covers draw order/count too, RNG instructions are categorically
//!   refused — the draw stream is contract and stays out of any cloning
//!   machinery.
//! - **No value defined in `T` (phi or instruction) may be referenced outside
//!   `T`** — a phi argument anywhere (an edge use), another block's operand,
//!   lazy interior, or terminator test. A path through a duplicated
//!   predecessor would bypass the original definition, which is unsound, not
//!   merely unprofitable. (This also refuses loop-carried self-uses: `T`'s own
//!   phis' arguments are edge uses.)
//! - **`T` must not be its own successor** (duplicating a self-loop is loop
//!   peeling, out of scope).
//! - **Cost model**: the per-predecessor copy cost is the cloned instruction
//!   count (scheduled plus owned lazy-tree interiors; substituted phis are
//!   free) **plus** the phi count of `T`'s successors (each new predecessor
//!   edge into a phi-bearing successor costs one out-of-SSA copy, inline or
//!   in a split block). It must be at most [`DUP_MAX_CLONE_INSTS`], and the
//!   total across all jumping predecessors at most [`DUP_MAX_TOTAL_INSTS`] —
//!   bounded static growth (`static_nodes` is already the weakest ratchet
//!   metric; the emitter's DAG dedup absorbs much of the clone cost in
//!   `dag_size` but not in the per-block node trees).
//! - `ShortCircuit` lazy trees are cloned **whole with their owning
//!   instruction** (D11: a lazy tree is never split or shared across paths;
//!   the owned interiors count toward the clone budget). A `Store`/`Phi`
//!   inside a lazy tree (out of contract) refuses the block.
//!
//! Beyond the dispatch win, duplication localizes values: a clone's operands
//! defined in `P` (including substituted phi arguments) become same-block
//! uses, which destruction no longer needs to slot.
//!
//! # Transform 6 — exit combining
//!
//! Structurally identical exit blocks (no phis, `Exit` terminator, pairwise
//! structurally equal schedules — recursively equal instruction trees with
//! positional matching of in-block definitions, exact matching of shared
//! values, and content matching of constants) are combined: the lowest-id
//! reachable block of each class absorbs the predecessors of every other
//! member (the legacy `CombineExitBlocks` covered only *empty* exit
//! blocks; the structural form also catches identical epilogues). No
//! instruction is duplicated or re-ordered — each path simply executes the
//! leader's copy of the identical sequence — so RNG/trap/effect behavior is
//! untouched. Purely a static-size win (`static_nodes`, block count);
//! dispatch is unchanged.
//!
//! # Pass shape
//!
//! Transforms 1–4 plus `dce::clear_unreachable` iterate to a bounded local
//! fixpoint (each enables the others; every changing iteration strictly
//! shrinks a monotone measure — phi count, edges-into-empty-blocks, case
//! edges, reachable blocks — with the DCE-style iteration cap as the hard
//! bound). Duplication then runs **once** (its growth is the one
//! non-monotone step; one round captures the metric value and keeps
//! termination trivial), followed by the cleanup fixpoint again, then exit
//! combining once, then a final cleanup. See the W4 ordering note in
//! `passes::mod` for how this composes with T3.8's if-conversion.
//!
//! # Pass discipline
//!
//! Deterministic (ascending block/instruction/edge scans, first-leader-wins
//! exit grouping, no hash-map iteration), iterative (explicit work stacks,
//! invariant §3.4), strictly binary MIR untouched (only schedules, phis, and
//! terminators move; instruction shapes are cloned verbatim). Every mutation
//! changes CFG-level facts, so a changed run calls `invalidate_all`.

use std::collections::HashMap;

use crate::analysis::Analyses;
use crate::effects::op_effects;
use crate::mir::{BlockId, CaseCond, Inst, Mir, Place, Terminator, Value};
use crate::passes::Pass;
use crate::passes::dce;

/// Max cloned instructions (scheduled + owned lazy-tree interiors) per
/// duplicated predecessor. Tiny blocks only: the win per traversal is one
/// dispatcher round trip plus the block's `Execute` overhead (~2 evals), so
/// large clones trade too much static size for it.
const DUP_MAX_CLONE_INSTS: usize = 3;
/// Max total cloned instructions across all jumping predecessors of one
/// block (bounds worst-case growth when a tiny block has many predecessors).
const DUP_MAX_TOTAL_INSTS: usize = 12;

/// The T3.9 pass. See the module docs.
#[derive(Debug, Default)]
pub struct ShapePass;

impl Pass for ShapePass {
    fn name(&self) -> &'static str {
        "shape"
    }

    fn run(&self, mir: &mut Mir, analyses: &mut Analyses) -> bool {
        if mir.blocks.is_empty() {
            return false;
        }
        let mut changed = cleanup_fixpoint(mir);
        if duplicate_tiny(mir) {
            changed = true;
            cleanup_fixpoint(mir);
        }
        if combine_exits(mir) {
            changed = true;
            cleanup_fixpoint(mir);
        }
        if changed {
            analyses.invalidate_all();
        }
        changed
    }
}

/// Transforms 1–4 + unreachable clearing to a bounded local fixpoint.
fn cleanup_fixpoint(mir: &mut Mir) -> bool {
    let mut total = false;
    // Termination: every changing iteration strictly shrinks a monotone
    // measure (phis, edges into empty blocks, case edges, reachable blocks,
    // schedule contents only ever concatenate under merging). Real inputs
    // converge in 2–4 iterations; the cap is the hard bound.
    let max_iters = mir.blocks.len() + mir.insts.len() + 8;
    for _ in 0..max_iters {
        let mut iter = false;
        iter |= simplify_phis(mir);
        iter |= thread_edges(mir);
        // DCE's branch simplification (duplicate-of-default removal, branch →
        // jump): threading can make every edge of a branch reach one target,
        // which then collapses and merges — a whole decided diamond folds
        // into its head.
        iter |= dce::simplify_branches(mir);
        iter |= shape_exits(mir);
        iter |= dce::merge_chains(mir);
        iter |= dce::clear_unreachable(mir);
        total |= iter;
        if !iter {
            break;
        }
    }
    total
}

// ----------------------------------------------------------------------------------
// Transform 1: phi simplification
// ----------------------------------------------------------------------------------

/// Reference counts for every value, from scheduled-instruction operands,
/// lazy-tree interiors, terminator tests, and phi arguments. Phi self-args
/// are *not* counted (a phi referenced only by itself is dead).
fn reference_counts(mir: &Mir) -> Vec<u32> {
    let scheduled = mir.scheduled_mask();
    let mut counts = vec![0u32; mir.insts.len()];
    let mut lazy_stack: Vec<Value> = Vec::new();
    for block in &mir.blocks {
        for &phi in &block.phis {
            if let Inst::Phi { args } = mir.inst(phi) {
                for &(_, a) in args {
                    if a != phi {
                        counts[a as usize] += 1;
                    }
                }
            }
        }
        for &v in &block.insts {
            let inst = mir.inst(v);
            Mir::for_each_operand(inst, |o| counts[o as usize] += 1);
            // Owned lazy interiors of ANY lazy owner (ShortCircuit rhs,
            // Select arms — T3.8's second D11 species). The roots were
            // counted as operands above; this walk counts the deep nodes.
            Mir::for_each_lazy_root(inst, |r| lazy_stack.push(r));
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

/// Dead-phi removal + trivial-phi elimination (module docs, transform 1).
fn simplify_phis(mir: &mut Mir) -> bool {
    let mut changed = false;

    // Trivial phis: every non-self argument is one identical Value.
    let mut forward: HashMap<Value, Value> = HashMap::new();
    for block in &mir.blocks {
        for &phi in &block.phis {
            let Inst::Phi { args } = mir.inst(phi) else {
                continue;
            };
            let mut unique: Option<Value> = None;
            let mut trivial = true;
            for &(_, a) in args {
                if a == phi {
                    continue;
                }
                match unique {
                    None => unique = Some(a),
                    Some(u) if u == a => {}
                    Some(_) => {
                        trivial = false;
                        break;
                    }
                }
            }
            if trivial && let Some(v) = unique {
                forward.insert(phi, v);
            }
        }
    }
    if !forward.is_empty() {
        // Resolve forwarding chains (phi -> phi -> v); a cycle of mutually
        // trivial phis (no external value anywhere) is skipped.
        let resolve = |start: Value| -> Option<Value> {
            let mut v = start;
            for _ in 0..=forward.len() {
                match forward.get(&v) {
                    Some(&next) => v = next,
                    None => return Some(v),
                }
            }
            None // cycle
        };
        let resolved: HashMap<Value, Value> = {
            let mut out = HashMap::new();
            for &phi in forward.keys() {
                if let Some(v) = resolve(phi)
                    && v != phi
                {
                    out.insert(phi, v);
                }
            }
            out
        };
        if !resolved.is_empty() {
            for inst in &mut mir.insts {
                Mir::for_each_operand_mut(inst, |o| {
                    if let Some(&v) = resolved.get(o) {
                        *o = v;
                    }
                });
            }
            for block in &mut mir.blocks {
                if let Terminator::Branch { test, .. } = &mut block.terminator
                    && let Some(&v) = resolved.get(test)
                {
                    *test = v;
                }
                let before = block.phis.len();
                block.phis.retain(|p| !resolved.contains_key(p));
                changed |= block.phis.len() != before;
            }
        }
    }

    // Dead phis: zero references outside their own argument list.
    let counts = reference_counts(mir);
    for block in &mut mir.blocks {
        let before = block.phis.len();
        block.phis.retain(|&p| counts[p as usize] > 0);
        changed |= block.phis.len() != before;
    }
    changed
}

// ----------------------------------------------------------------------------------
// Transform 2: empty-block threading with phi-argument copying
// ----------------------------------------------------------------------------------

/// Whether `block` is an empty (no phis, no insts) jump-only block that an
/// edge may skip through (never the entry, never the edge's source, never a
/// self-loop).
fn skippable(mir: &Mir, src: BlockId, b: BlockId) -> Option<BlockId> {
    if b == 0 || b == src {
        return None;
    }
    let block = &mir.blocks[b];
    if !block.phis.is_empty() || !block.insts.is_empty() {
        return None;
    }
    let Terminator::Jump(u) = block.terminator else {
        return None;
    };
    (u != b).then_some(u)
}

/// The phi argument of `phi` keyed by predecessor `key`, if present.
fn phi_arg(mir: &Mir, phi: Value, key: BlockId) -> Option<Value> {
    let Inst::Phi { args } = mir.inst(phi) else {
        return None;
    };
    args.iter().find(|&&(p, _)| p == key).map(|&(_, a)| a)
}

/// Walks one edge `src -> start` through empty jump-only blocks and commits
/// the rewrite (including phi-argument copies for a final phi-bearing hop).
/// Returns the new target, or `None` when the edge is unchanged.
fn thread_one(mir: &mut Mir, src: BlockId, start: BlockId) -> Option<BlockId> {
    let mut t = start;
    let mut budget = mir.blocks.len() + 1;
    // (from, to) of a committed-on-success final hop into a phi-bearing block.
    let mut phi_hop: Option<(BlockId, BlockId)> = None;
    loop {
        if budget == 0 {
            return None; // empty-block cycle: leave the edge alone
        }
        budget -= 1;
        let Some(u) = skippable(mir, src, t) else {
            break;
        };
        if mir.blocks[u].phis.is_empty() {
            t = u; // plain skip (DCE's rule)
            continue;
        }
        // Final hop into a phi-bearing destination: only from a Jump source
        // (the destruct_ssa cost rule in the module docs — a multi-successor
        // source's edge would just be re-split into a fresh copy block).
        if !matches!(mir.blocks[src].terminator, Terminator::Jump(_)) {
            break;
        }
        if u == src {
            break; // would manufacture a self-loop on src
        }
        // Is src already a direct predecessor of u (its current terminator
        // targets u)? Then no key is added; all args must agree.
        let src_reaches_u = mir.blocks[src].terminator.successors().any(|s| s == u);
        let phis = &mir.blocks[u].phis;
        let feasible = phis.iter().all(|&phi| {
            let Some(arg_e) = phi_arg(mir, phi, t) else {
                return false; // malformed input: missing key (defensive)
            };
            if src_reaches_u {
                phi_arg(mir, phi, src) == Some(arg_e)
            } else {
                true
            }
        });
        if !feasible {
            break;
        }
        if !src_reaches_u {
            phi_hop = Some((t, u));
        }
        t = u;
        break;
    }
    if t == start {
        return None;
    }
    if let Some((from, to)) = phi_hop {
        let phis = mir.blocks[to].phis.clone();
        for phi in phis {
            let arg = phi_arg(mir, phi, from).expect("feasibility checked");
            if let Inst::Phi { args } = &mut mir.insts[phi as usize] {
                args.push((src, arg));
            }
        }
    }
    Some(t)
}

/// Transform 2 over every edge of every block (jump target, then cases in
/// ascending order, then the default — deterministic).
fn thread_edges(mir: &mut Mir) -> bool {
    let mut changed = false;
    for b in 0..mir.blocks.len() {
        match mir.blocks[b].terminator.clone() {
            Terminator::Jump(t) => {
                if let Some(nt) = thread_one(mir, b, t) {
                    mir.blocks[b].terminator = Terminator::Jump(nt);
                    changed = true;
                }
            }
            Terminator::Branch { cases, default, .. } => {
                for i in 0..cases.len() {
                    let t = cases[i].1;
                    if let Some(nt) = thread_one(mir, b, t)
                        && let Terminator::Branch { cases, .. } = &mut mir.blocks[b].terminator
                    {
                        cases[i].1 = nt;
                        changed = true;
                    }
                }
                if let Some(d) = default
                    && let Some(nd) = thread_one(mir, b, d)
                    && let Terminator::Branch { default, .. } = &mut mir.blocks[b].terminator
                {
                    *default = Some(nd);
                    changed = true;
                }
            }
            Terminator::Exit => {}
        }
    }
    changed
}

// ----------------------------------------------------------------------------------
// Transform 3: exit shaping
// ----------------------------------------------------------------------------------

/// An empty exit block: jumping to it is observably identical to exiting.
fn is_empty_exit(mir: &Mir, b: BlockId) -> bool {
    let block = &mir.blocks[b];
    block.phis.is_empty() && block.insts.is_empty() && block.terminator == Terminator::Exit
}

/// The emitter's dense-case test (`finalize.py`): all conds integral and
/// exactly `0..n-1` — the `SwitchIntegerWithDefault` O(1) form.
#[allow(clippy::cast_precision_loss, clippy::float_cmp)] // exact Python == semantics
fn is_dense(cases: &[(CaseCond, BlockId)]) -> bool {
    cases
        .iter()
        .enumerate()
        .all(|(i, &(c, _))| c.value() == i as f64)
}

/// Transform 3 (module docs): jumps and branch edges into empty exit blocks
/// become direct exits.
fn shape_exits(mir: &mut Mir) -> bool {
    let mut changed = false;
    for b in 0..mir.blocks.len() {
        let new_term = match &mir.blocks[b].terminator {
            Terminator::Jump(t) => is_empty_exit(mir, *t).then_some(Terminator::Exit),
            Terminator::Branch {
                test,
                cases,
                default,
            } => {
                let new_default = match default {
                    Some(d) if is_empty_exit(mir, *d) => None,
                    other => *other,
                };
                let new_cases: Vec<(CaseCond, BlockId)> = if new_default.is_none() {
                    // With no default, a matched empty-exit case and an
                    // unmatched scrutinee behave identically (both exit) —
                    // unless dropping the case would degrade a dense O(1)
                    // case set into a sparse linear one.
                    let kept: Vec<(CaseCond, BlockId)> = cases
                        .iter()
                        .copied()
                        .filter(|&(_, t)| !is_empty_exit(mir, t))
                        .collect();
                    if kept.len() != cases.len() && (!is_dense(cases) || is_dense(&kept)) {
                        kept
                    } else {
                        cases.clone()
                    }
                } else {
                    cases.clone()
                };
                if new_default == *default && new_cases.len() == cases.len() {
                    None
                } else if new_cases.is_empty() {
                    // Every edge exited; the test value stays scheduled and
                    // still evaluates (last in the block, like the
                    // dispatcher it replaced).
                    Some(Terminator::Exit)
                } else {
                    Some(Terminator::Branch {
                        test: *test,
                        cases: new_cases,
                        default: new_default,
                    })
                }
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
// Transform 5: tiny-block duplication into predecessors
// ----------------------------------------------------------------------------------

/// Per-block facts for the duplication decision.
struct DupFacts {
    /// Instructions a clone would copy: scheduled insts + owned lazy
    /// interiors (phis are substituted, not cloned).
    clone_insts: usize,
    /// Refusal: RNG draw anywhere in the block (hard rule), a `Store`/`Phi`
    /// inside a lazy tree (out of contract), or a `Phi` in the schedule.
    refused: bool,
}

/// Scans `T`'s schedule (including owned lazy trees) for the duplication
/// facts.
fn dup_facts(mir: &Mir, scheduled: &[bool], t: BlockId) -> DupFacts {
    let mut facts = DupFacts {
        clone_insts: 0,
        refused: false,
    };
    let mut lazy_stack: Vec<Value> = Vec::new();
    for &v in &mir.blocks[t].insts {
        facts.clone_insts += 1;
        match mir.inst(v) {
            Inst::Op { op, .. } => {
                if op_effects(*op).rng {
                    facts.refused = true;
                }
            }
            Inst::Phi { .. } => facts.refused = true, // malformed; defensive
            _ => {}
        }
        // Owned lazy trees of ANY lazy owner (ShortCircuit rhs, Select arms —
        // T3.8's second D11 species).
        Mir::for_each_lazy_root(mir.inst(v), |r| lazy_stack.push(r));
        while let Some(lv) = lazy_stack.pop() {
            if mir.is_const(lv) || scheduled[lv as usize] {
                continue;
            }
            facts.clone_insts += 1;
            match mir.inst(lv) {
                Inst::Op { op, .. } => {
                    if op_effects(*op).rng {
                        facts.refused = true;
                    }
                }
                // Stores inside Select arms are in-contract post-T3.8, but
                // `clone_lazy_tree` deliberately does not clone them; phis in
                // lazy trees stay out of contract. Refuse both for
                // duplication (conservative; revisit with W5 tuning).
                Inst::Store { .. } | Inst::Phi { .. } => facts.refused = true,
                _ => {}
            }
            Mir::for_each_operand(mir.inst(lv), |o| lazy_stack.push(o));
        }
    }
    facts
}

/// Values used outside their defining block: another block's operands or
/// lazy interiors, any terminator test in another block, or **any phi
/// argument anywhere** (phi-argument uses materialize on edges, outside the
/// defining block by construction). Mirrors `ssa::collect_use_facts`'s
/// `cross_block` (private there).
fn used_outside_def_block(mir: &Mir) -> Vec<bool> {
    let scheduled = mir.scheduled_mask();
    let mut def_block: Vec<Option<BlockId>> = vec![None; mir.insts.len()];
    for (b, block) in mir.blocks.iter().enumerate() {
        for &v in block.insts.iter().chain(&block.phis) {
            def_block[v as usize] = Some(b);
        }
    }
    let mut outside = vec![false; mir.insts.len()];
    let mut lazy_stack: Vec<Value> = Vec::new();
    for (b, block) in mir.blocks.iter().enumerate() {
        let note = |o: Value, outside: &mut Vec<bool>| {
            if def_block[o as usize].is_some_and(|db| db != b) {
                outside[o as usize] = true;
            }
        };
        for &v in &block.insts {
            let inst = mir.inst(v);
            Mir::for_each_operand(inst, |o| note(o, &mut outside));
            // Owned lazy interiors of ANY lazy owner (ShortCircuit rhs,
            // Select arms — T3.8's second D11 species).
            Mir::for_each_lazy_root(inst, |r| lazy_stack.push(r));
            while let Some(lv) = lazy_stack.pop() {
                if mir.is_const(lv) || scheduled[lv as usize] {
                    continue;
                }
                Mir::for_each_operand(mir.inst(lv), |o| {
                    if scheduled[o as usize] {
                        note(o, &mut outside);
                    } else {
                        lazy_stack.push(o);
                    }
                });
            }
        }
        if let Terminator::Branch { test, .. } = &block.terminator {
            note(*test, &mut outside);
        }
        for &phi in &block.phis {
            if let Inst::Phi { args } = mir.inst(phi) {
                for &(_, a) in args {
                    // Edge uses: always outside the defining block.
                    if def_block[a as usize].is_some() {
                        outside[a as usize] = true;
                    }
                }
            }
        }
    }
    outside
}

/// Remaps a value through the clone map (identity for values defined outside
/// the duplicated block and for constants).
fn remap(map: &HashMap<Value, Value>, v: Value) -> Value {
    map.get(&v).copied().unwrap_or(v)
}

fn remap_place(map: &HashMap<Value, Value>, place: &Place) -> Place {
    let mut p = *place;
    if let crate::mir::BlockRef::Value(v) = &mut p.block {
        *v = remap(map, *v);
    }
    if let crate::mir::IndexRef::Value(v) = &mut p.index {
        *v = remap(map, *v);
    }
    p
}

/// Deep-clones a lazy tree rooted at `root` (D11: the whole owned tree moves
/// with its owning instruction). Constants are shared; scheduled values
/// (defensive: out of the builder contract) are remapped like eager operands;
/// owned interiors are cloned node by node, iteratively.
fn clone_lazy_tree(
    mir: &mut Mir,
    scheduled: &[bool],
    root: Value,
    map: &HashMap<Value, Value>,
) -> Value {
    enum W {
        Visit(Value),
        Build(Value),
    }
    let mut work = vec![W::Visit(root)];
    let mut results: Vec<Value> = Vec::new();
    while let Some(item) = work.pop() {
        match item {
            W::Visit(v) => {
                if mir.is_const(v) {
                    results.push(v);
                    continue;
                }
                if scheduled.get(v as usize).copied().unwrap_or(false) {
                    results.push(remap(map, v));
                    continue;
                }
                work.push(W::Build(v));
                let mut kids: Vec<Value> = Vec::new();
                Mir::for_each_operand(mir.inst(v), |o| kids.push(o));
                for &k in kids.iter().rev() {
                    work.push(W::Visit(k));
                }
            }
            W::Build(v) => {
                let cloned = match mir.inst(v).clone() {
                    Inst::Op {
                        op,
                        pure_node,
                        args,
                    } => {
                        let new_args = results.split_off(results.len() - args.len());
                        Inst::Op {
                            op,
                            pure_node,
                            args: new_args,
                        }
                    }
                    Inst::ShortCircuit { op, pure_node, .. } => {
                        // Operands arrive as [lhs, rhs] in order (a nested
                        // ShortCircuit's lhs is itself a tree node and was
                        // cloned by its own Visit).
                        let pair = results.split_off(results.len() - 2);
                        Inst::ShortCircuit {
                            op,
                            pure_node,
                            lhs: pair[0],
                            rhs: pair[1],
                        }
                    }
                    Inst::Select { .. } => {
                        // A converted inner diamond moved into an outer arm
                        // tree (T3.8 fixpoint). Operands arrive as
                        // [test, then_root, else_root] in order.
                        let trio = results.split_off(results.len() - 3);
                        Inst::Select {
                            test: trio[0],
                            then_root: trio[1],
                            else_root: trio[2],
                        }
                    }
                    Inst::Load { place } => {
                        // Place value operands (block, then index) arrive on
                        // the results stack in `for_each_operand` order.
                        let mut count = 0usize;
                        crate::mir::Mir::for_each_operand(mir.inst(v), |_| count += 1);
                        let ops = results.split_off(results.len() - count);
                        let mut iter = ops.into_iter();
                        let mut p = place;
                        if let crate::mir::BlockRef::Value(bv) = &mut p.block {
                            *bv = iter.next().expect("block operand");
                        }
                        if let crate::mir::IndexRef::Value(iv) = &mut p.index {
                            *iv = iter.next().expect("index operand");
                        }
                        Inst::Load { place: p }
                    }
                    // Stores/phis inside a lazy tree are refused upfront
                    // (dup_facts); constants never reach Build.
                    Inst::Store { .. }
                    | Inst::Phi { .. }
                    | Inst::ConstInt(_)
                    | Inst::ConstFloat(_) => unreachable!("refused by dup_facts"),
                };
                results.push(mir.push_inst(cloned));
            }
        }
    }
    results.pop().expect("lazy clone produced a root")
}

/// Clones one scheduled instruction of the duplicated block into the arena,
/// remapping operands through `map`, and records the clone in `map`.
fn clone_inst(
    mir: &mut Mir,
    scheduled: &[bool],
    v: Value,
    map: &mut HashMap<Value, Value>,
) -> Value {
    let cloned = match mir.inst(v).clone() {
        Inst::Op {
            op,
            pure_node,
            args,
        } => Inst::Op {
            op,
            pure_node,
            args: args.iter().map(|&a| remap(map, a)).collect(),
        },
        Inst::Load { place } => Inst::Load {
            place: remap_place(map, &place),
        },
        Inst::Store { place, value } => Inst::Store {
            place: remap_place(map, &place),
            value: remap(map, value),
        },
        Inst::Select {
            test,
            then_root,
            else_root,
        } => {
            let new_test = remap(map, test);
            let new_then = clone_lazy_tree(mir, scheduled, then_root, map);
            let new_else = clone_lazy_tree(mir, scheduled, else_root, map);
            Inst::Select {
                test: new_test,
                then_root: new_then,
                else_root: new_else,
            }
        }
        Inst::ShortCircuit {
            op,
            pure_node,
            lhs,
            rhs,
        } => {
            let new_lhs = remap(map, lhs);
            let new_rhs = clone_lazy_tree(mir, scheduled, rhs, map);
            Inst::ShortCircuit {
                op,
                pure_node,
                lhs: new_lhs,
                rhs: new_rhs,
            }
        }
        // Scheduled constants are legal (if unusual); share-by-clone keeps
        // the schedule slot. Phis in a schedule are refused by dup_facts.
        c @ (Inst::ConstInt(_) | Inst::ConstFloat(_)) => c,
        Inst::Phi { .. } => unreachable!("refused by dup_facts"),
    };
    let nv = mir.push_inst(cloned);
    map.insert(v, nv);
    nv
}

/// Transform 5 (module docs): duplicates tiny blocks into their
/// unconditionally-jumping predecessors.
#[allow(clippy::too_many_lines)] // one straight-line decision ladder + apply
fn duplicate_tiny(mir: &mut Mir) -> bool {
    let mut changed = false;
    for t in 0..mir.blocks.len() {
        if t == 0 {
            continue;
        }
        // Predecessors and use facts are recomputed per candidate: earlier
        // duplications rewrite predecessor terminators and schedule fresh
        // clone values the stale masks would not cover.
        let preds = mir.predecessors();
        if preds[t].len() < 2 {
            continue; // single-pred blocks are merge territory (transform 4)
        }
        let jump_preds: Vec<BlockId> = preds[t]
            .iter()
            .copied()
            .filter(|&p| p != t && mir.blocks[p].terminator == Terminator::Jump(t))
            .collect();
        if jump_preds.is_empty() {
            continue;
        }
        // Self-successor: duplicating would be loop peeling; refuse.
        if mir.blocks[t].terminator.successors().any(|s| s == t) {
            continue;
        }
        let scheduled = mir.scheduled_mask();
        // No value defined in T may be referenced outside T.
        let outside = used_outside_def_block(mir);
        let defs_escape = mir.blocks[t]
            .insts
            .iter()
            .chain(&mir.blocks[t].phis)
            .any(|&v| outside[v as usize]);
        if defs_escape {
            continue;
        }
        let facts = dup_facts(mir, &scheduled, t);
        let succs: Vec<BlockId> = {
            let mut s: Vec<BlockId> = mir.blocks[t].terminator.successors().collect();
            s.sort_unstable();
            s.dedup();
            s
        };
        // Per-predecessor cost: clone + one out-of-SSA copy per successor phi
        // (module docs).
        let succ_phi_count: usize = succs.iter().map(|&s| mir.blocks[s].phis.len()).sum();
        let per_pred_cost = facts.clone_insts + succ_phi_count;
        if facts.refused
            || per_pred_cost > DUP_MAX_CLONE_INSTS
            || per_pred_cost * jump_preds.len() > DUP_MAX_TOTAL_INSTS
        {
            continue;
        }
        // Every phi of T must have an argument for every jumping predecessor
        // (substitution source), and every phi of T's successors must have a
        // T-keyed argument to copy. Defensive: malformed inputs refuse.
        let phis_ok = jump_preds.iter().all(|&p| {
            mir.blocks[t]
                .phis
                .iter()
                .all(|&phi| phi_arg(mir, phi, p).is_some())
        });
        let succ_phis_ok = succs.iter().all(|&s| {
            mir.blocks[s]
                .phis
                .iter()
                .all(|&phi| phi_arg(mir, phi, t).is_some())
        });
        if !phis_ok || !succ_phis_ok {
            continue;
        }

        // Apply: clone T into each jumping predecessor.
        for &p in &jump_preds {
            let mut map: HashMap<Value, Value> = HashMap::new();
            for &phi in &mir.blocks[t].phis.clone() {
                let arg = phi_arg(mir, phi, p).expect("checked above");
                map.insert(phi, arg);
            }
            for &v in &mir.blocks[t].insts.clone() {
                let nv = clone_inst(mir, &scheduled, v, &mut map);
                mir.blocks[p].insts.push(nv);
            }
            let new_term = match &mir.blocks[t].terminator {
                Terminator::Jump(s) => Terminator::Jump(*s),
                Terminator::Branch {
                    test,
                    cases,
                    default,
                } => Terminator::Branch {
                    test: remap(&map, *test),
                    cases: cases.clone(),
                    default: *default,
                },
                Terminator::Exit => Terminator::Exit,
            };
            mir.blocks[p].terminator = new_term;
            // Successor phis gain (p, arg[t]). The argument is defined
            // outside T (defs-escape refusal) or constant, so it is available
            // at p's end (dominance argument in the module docs). p was a
            // jump-only predecessor of t, so p cannot already key these phis
            // (t is not its own successor).
            for &s in &succs {
                let phis = mir.blocks[s].phis.clone();
                for phi in phis {
                    let arg = phi_arg(mir, phi, t).expect("checked above");
                    if let Inst::Phi { args } = &mut mir.insts[phi as usize] {
                        debug_assert!(args.iter().all(|&(k, _)| k != p));
                        args.push((p, arg));
                    }
                }
            }
        }
        // T no longer has the jumping predecessors: prune its phi args.
        let phis = mir.blocks[t].phis.clone();
        for phi in phis {
            if let Inst::Phi { args } = &mut mir.insts[phi as usize] {
                args.retain(|&(k, _)| !jump_preds.contains(&k));
            }
        }
        changed = true;
    }
    changed
}

// ----------------------------------------------------------------------------------
// Transform 6: exit combining
// ----------------------------------------------------------------------------------

/// Compares two values for structural equality during exit-block comparison
/// (module docs): positional in-block definitions via `map`, shared
/// outside-definitions by identity, constants by content, lazy interiors by
/// simultaneous structural walk. Iterative.
fn values_structurally_equal(
    mir: &Mir,
    scheduled: &[bool],
    a_defs: &[bool],
    map: &HashMap<Value, Value>,
    va: Value,
    vb: Value,
) -> bool {
    let mut stack: Vec<(Value, Value)> = vec![(va, vb)];
    while let Some((a, b)) = stack.pop() {
        if map.get(&a) == Some(&b) {
            continue; // positionally matched in-block definitions
        }
        let (ca, cb) = (mir.is_const(a), mir.is_const(b));
        if ca || cb {
            if !(ca && cb) {
                return false;
            }
            // Content equality; `Inst` PartialEq is exact except f64 ==
            // (NaN-vs-NaN conservatively refuses; -0.0 == 0.0 is fine — both
            // emit the identical int-0 node).
            if mir.inst(a) != mir.inst(b) {
                return false;
            }
            continue;
        }
        let (sa, sb) = (
            scheduled.get(a as usize).copied().unwrap_or(false),
            scheduled.get(b as usize).copied().unwrap_or(false),
        );
        if sa || sb {
            // Scheduled values not positionally matched: equal only when they
            // are the same shared value defined outside both blocks.
            if a == b && !a_defs.get(a as usize).copied().unwrap_or(false) {
                continue;
            }
            return false;
        }
        // Both are unscheduled lazy interiors: structural compare.
        match (mir.inst(a), mir.inst(b)) {
            (
                Inst::Op {
                    op: oa,
                    pure_node: pa,
                    args: aa,
                },
                Inst::Op {
                    op: ob,
                    pure_node: pb,
                    args: ab,
                },
            ) => {
                if oa != ob || pa != pb || aa.len() != ab.len() {
                    return false;
                }
                stack.extend(aa.iter().copied().zip(ab.iter().copied()));
            }
            (
                Inst::ShortCircuit {
                    op: oa,
                    pure_node: pa,
                    lhs: la,
                    rhs: ra,
                },
                Inst::ShortCircuit {
                    op: ob,
                    pure_node: pb,
                    lhs: lb,
                    rhs: rb,
                },
            ) => {
                if oa != ob || pa != pb {
                    return false;
                }
                stack.push((*la, *lb));
                stack.push((*ra, *rb));
            }
            (Inst::Load { place: la }, Inst::Load { place: lb }) => {
                if !push_place_pairs(la, lb, &mut stack) {
                    return false;
                }
            }
            _ => return false,
        }
    }
    true
}

/// Structural place comparison: equal constant parts, value parts paired for
/// the caller's walk. `false` = definitely unequal.
fn push_place_pairs(pa: &Place, pb: &Place, stack: &mut Vec<(Value, Value)>) -> bool {
    use crate::mir::{BlockRef, IndexRef};
    if pa.offset != pb.offset {
        return false;
    }
    match (pa.block, pb.block) {
        (BlockRef::Concrete(x), BlockRef::Concrete(y)) if x == y => {}
        (BlockRef::Temp(x), BlockRef::Temp(y)) if x == y => {}
        (BlockRef::Value(x), BlockRef::Value(y)) => stack.push((x, y)),
        _ => return false,
    }
    match (pa.index, pb.index) {
        (IndexRef::Const(x), IndexRef::Const(y)) if x == y => {}
        (IndexRef::Value(x), IndexRef::Value(y)) => stack.push((x, y)),
        _ => return false,
    }
    true
}

/// Whether two exit blocks (no phis, `Exit` terminators — caller-checked)
/// have structurally identical schedules.
fn exit_blocks_equal(mir: &Mir, scheduled: &[bool], a: BlockId, b: BlockId) -> bool {
    let (ia, ib) = (&mir.blocks[a].insts, &mir.blocks[b].insts);
    if ia.len() != ib.len() {
        return false;
    }
    let mut a_defs = vec![false; mir.insts.len()];
    for &v in ia.iter().chain(ib) {
        a_defs[v as usize] = true;
    }
    let mut map: HashMap<Value, Value> = HashMap::new();
    for (&va, &vb) in ia.iter().zip(ib) {
        let equal = match (mir.inst(va), mir.inst(vb)) {
            (
                Inst::Op {
                    op: oa,
                    pure_node: pa,
                    args: aa,
                },
                Inst::Op {
                    op: ob,
                    pure_node: pb,
                    args: ab,
                },
            ) => {
                oa == ob
                    && pa == pb
                    && aa.len() == ab.len()
                    && aa.iter().zip(ab).all(|(&x, &y)| {
                        values_structurally_equal(mir, scheduled, &a_defs, &map, x, y)
                    })
            }
            (
                Inst::ShortCircuit {
                    op: oa,
                    pure_node: pa,
                    lhs: la,
                    rhs: ra,
                },
                Inst::ShortCircuit {
                    op: ob,
                    pure_node: pb,
                    lhs: lb,
                    rhs: rb,
                },
            ) => {
                oa == ob
                    && pa == pb
                    && values_structurally_equal(mir, scheduled, &a_defs, &map, *la, *lb)
                    && values_structurally_equal(mir, scheduled, &a_defs, &map, *ra, *rb)
            }
            (Inst::Load { place: la }, Inst::Load { place: lb }) => {
                let mut pairs: Vec<(Value, Value)> = Vec::new();
                push_place_pairs(la, lb, &mut pairs)
                    && pairs.iter().all(|&(x, y)| {
                        values_structurally_equal(mir, scheduled, &a_defs, &map, x, y)
                    })
            }
            (
                Inst::Store {
                    place: la,
                    value: xa,
                },
                Inst::Store {
                    place: lb,
                    value: xb,
                },
            ) => {
                let mut pairs: Vec<(Value, Value)> = Vec::new();
                push_place_pairs(la, lb, &mut pairs)
                    && pairs.iter().all(|&(x, y)| {
                        values_structurally_equal(mir, scheduled, &a_defs, &map, x, y)
                    })
                    && values_structurally_equal(mir, scheduled, &a_defs, &map, *xa, *xb)
            }
            (x @ (Inst::ConstInt(_) | Inst::ConstFloat(_)), y) => x == y,
            _ => false,
        };
        if !equal {
            return false;
        }
        map.insert(va, vb);
    }
    true
}

/// Transform 6 (module docs): combines structurally identical exit blocks.
/// The leader (survivor) is the lowest-id reachable block of each structural
/// class — deterministic insertion order.
fn combine_exits(mir: &mut Mir) -> bool {
    let scheduled = mir.scheduled_mask();
    let mut reachable = vec![false; mir.blocks.len()];
    for b in mir.reverse_postorder() {
        reachable[b] = true;
    }
    let mut leaders: Vec<BlockId> = Vec::new();
    let mut redirect: HashMap<BlockId, BlockId> = HashMap::new();
    for (b, &live) in reachable.iter().enumerate() {
        if !live {
            continue;
        }
        let block = &mir.blocks[b];
        if block.terminator != Terminator::Exit || !block.phis.is_empty() {
            continue;
        }
        match leaders
            .iter()
            .find(|&&l| exit_blocks_equal(mir, &scheduled, l, b))
        {
            Some(&l) => {
                redirect.insert(b, l);
            }
            None => leaders.push(b),
        }
    }
    if redirect.is_empty() {
        return false;
    }
    let rewrite = |t: &mut BlockId| {
        if let Some(&l) = redirect.get(t) {
            *t = l;
        }
    };
    for block in &mut mir.blocks {
        match &mut block.terminator {
            Terminator::Jump(t) => rewrite(t),
            Terminator::Branch { cases, default, .. } => {
                for (_, t) in cases.iter_mut() {
                    rewrite(t);
                }
                if let Some(d) = default {
                    rewrite(d);
                }
            }
            Terminator::Exit => {}
        }
    }
    // The duplicates are unreachable now (leaders have no phis, so no keying
    // fixup is needed); the caller's cleanup clears them.
    true
}

#[cfg(test)]
mod tests {
    // Toolchain note: clippy lints test code under --all-targets; exact f64
    // equality and terse local names are the test-builder convention here.
    #![allow(clippy::float_cmp, clippy::similar_names)]
    use super::*;
    use crate::alloc::allocate_temps;
    use crate::emit::cfg_to_engine_nodes;
    use crate::interpret::Interpreter;
    use crate::lower::lower_mir;
    use crate::mir::{BlockRef, IndexRef};
    use crate::nodes::format_engine_node;
    use crate::ops::Op;
    use crate::ssa::destruct_ssa;

    fn run_pass(mir: &mut Mir) -> bool {
        let mut analyses = Analyses::new();
        ShapePass.run(mir, &mut analyses)
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

    fn phi(mir: &mut Mir, block: BlockId, args: Vec<(BlockId, Value)>) -> Value {
        let v = mir.push_inst(Inst::Phi { args });
        mir.blocks[block].phis.push(v);
        v
    }

    fn op(mir: &mut Mir, block: BlockId, op: Op, args: Vec<Value>) -> Value {
        sched(
            mir,
            block,
            Inst::Op {
                op,
                pure_node: op.pure(),
                args,
            },
        )
    }

    fn store(mir: &mut Mir, block: BlockId, place: Place, value: Value) -> Value {
        sched(mir, block, Inst::Store { place, value })
    }

    fn load(mir: &mut Mir, block: BlockId, place: Place) -> Value {
        sched(mir, block, Inst::Load { place })
    }

    fn reachable(mir: &Mir) -> Vec<bool> {
        let mut r = vec![false; mir.blocks.len()];
        for b in mir.reverse_postorder() {
            r[b] = true;
        }
        r
    }

    /// Destructs, allocates, lowers, emits, and runs hand-built MIR. Returns
    /// (block-21 memory, log, eval count, dispatch count, formatted tree).
    fn finish_and_run(mir: &Mir, in20: &[f64]) -> (Vec<f64>, Vec<f64>, u64, u64, String) {
        let mut m = mir.clone();
        destruct_ssa(&mut m).expect("destruct");
        let alloc = allocate_temps(&m).expect("alloc");
        let lowered = lower_mir(&m, &alloc).expect("lower");
        let nodes = cfg_to_engine_nodes(&lowered).expect("emit");
        let mut interp = Interpreter::new(0);
        interp.set_block(20, in20.to_vec());
        interp.set_block(21, vec![0.0; 4]);
        interp.run(&nodes).expect("run");
        (
            interp.block(21).unwrap_or(&[]).to_vec(),
            interp.log().to_vec(),
            interp.eval_count(),
            interp.dispatch_count(),
            format_engine_node(&nodes.arena, nodes.root),
        )
    }

    fn count_op(formatted: &str, op_name: &str) -> usize {
        formatted.matches(&format!("{op_name}(")).count()
    }

    // ------------------------------------------------------------------
    // Transform 1: phi simplification (+ transform 4 merge cascade)
    // ------------------------------------------------------------------

    #[test]
    fn trivial_phi_eliminates_and_chain_merges() {
        // b0: x = Load 20[0]; Jump b1
        // b1: p = phi[(b0, x)]; y = Add(p, 1); Store 21[0] <- y; Exit
        // The single-arg phi is trivial; b1 then merges into b0 (DCE's merge,
        // which its own phi guard previously refused).
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let x = load(&mut mir, b0, concrete_place(20, 0));
        mir.blocks[b0].terminator = Terminator::Jump(b1);
        let p = phi(&mut mir, b1, vec![(b0, x)]);
        let c1 = mir.push_inst(Inst::ConstInt(1));
        let y = op(&mut mir, b1, Op::Add, vec![p, c1]);
        store(&mut mir, b1, concrete_place(21, 0), y);
        assert!(run_pass(&mut mir));
        assert_eq!(mir.blocks[b0].terminator, Terminator::Exit);
        assert_eq!(mir.blocks[b0].insts.len(), 3, "x, y, store all in b0");
        assert!(mir.blocks[b1].insts.is_empty() && mir.blocks[b1].phis.is_empty());
        let Inst::Op { args, .. } = mir.inst(y) else {
            panic!("y is an op");
        };
        assert_eq!(args[0], x, "phi use rewritten to its single argument");
    }

    #[test]
    fn same_value_phi_is_trivial_even_with_multiple_preds() {
        // Both arms carry the same value: phi(x, x) -> x.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let b2 = mir.push_block();
        let b3 = mir.push_block();
        let x = load(&mut mir, b0, concrete_place(20, 0));
        mir.blocks[b0].terminator = Terminator::Branch {
            test: x,
            cases: vec![(CaseCond::Int(0), b1)],
            default: Some(b2),
        };
        // Arms must be non-empty so threading does not consume them first.
        let d1 = store(&mut mir, b1, concrete_place(21, 1), x);
        let _ = d1;
        mir.blocks[b1].terminator = Terminator::Jump(b3);
        store(&mut mir, b2, concrete_place(21, 2), x);
        mir.blocks[b2].terminator = Terminator::Jump(b3);
        let p = phi(&mut mir, b3, vec![(b1, x), (b2, x)]);
        store(&mut mir, b3, concrete_place(21, 0), p);
        assert!(run_pass(&mut mir));
        assert!(
            mir.blocks[b3].phis.is_empty() || !reachable(&mir)[b3],
            "trivial phi removed (or whole block restructured)"
        );
        // The store now uses x directly wherever it ended up.
        let uses_x = mir.insts.iter().any(|inst| {
            matches!(inst, Inst::Store { place, value } if *value == x
                && matches!(place.index, IndexRef::Const(0)))
        });
        assert!(uses_x, "phi use rewritten to the shared value");
    }

    #[test]
    fn dead_phi_is_removed() {
        // A non-trivial phi (two different args) with no references.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let b2 = mir.push_block();
        let b3 = mir.push_block();
        let x = load(&mut mir, b0, concrete_place(20, 0));
        mir.blocks[b0].terminator = Terminator::Branch {
            test: x,
            cases: vec![(CaseCond::Int(0), b1)],
            default: Some(b2),
        };
        let ca = mir.push_inst(Inst::ConstInt(1));
        let cb = mir.push_inst(Inst::ConstInt(2));
        store(&mut mir, b1, concrete_place(21, 1), ca);
        mir.blocks[b1].terminator = Terminator::Jump(b3);
        store(&mut mir, b2, concrete_place(21, 2), cb);
        mir.blocks[b2].terminator = Terminator::Jump(b3);
        phi(&mut mir, b3, vec![(b1, ca), (b2, cb)]);
        store(&mut mir, b3, concrete_place(21, 0), x);
        assert!(run_pass(&mut mir));
        for b in 0..mir.blocks.len() {
            assert!(mir.blocks[b].phis.is_empty(), "dead phi removed");
        }
    }

    #[test]
    fn mutually_trivial_phi_cycle_is_left_alone() {
        // p1 = phi[(b0, p2)]; p2 = phi[(b1, p1)] in a two-block loop: a cycle
        // with no external value resolves to nothing; the pass must not spin
        // or replace. (Referenced by stores so dead-phi removal stays out.)
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let b2 = mir.push_block();
        let x = load(&mut mir, b0, concrete_place(20, 0));
        // Pre-create the phi values to allow the cycle.
        let p1 = mir.push_inst(Inst::Phi { args: vec![] });
        let p2 = mir.push_inst(Inst::Phi { args: vec![] });
        mir.blocks[b1].phis.push(p1);
        mir.blocks[b2].phis.push(p2);
        mir.insts[p1 as usize] = Inst::Phi {
            args: vec![(b0, p2), (b2, p2)],
        };
        mir.insts[p2 as usize] = Inst::Phi {
            args: vec![(b1, p1)],
        };
        mir.blocks[b0].terminator = Terminator::Jump(b1);
        store(&mut mir, b1, concrete_place(21, 0), p1);
        mir.blocks[b1].terminator = Terminator::Branch {
            test: x,
            cases: vec![(CaseCond::Int(0), b2)],
            default: None,
        };
        store(&mut mir, b2, concrete_place(21, 1), p2);
        mir.blocks[b2].terminator = Terminator::Jump(b1);
        // The pass may shape other things but must keep both phis (cycle).
        let _ = run_pass(&mut mir);
        assert!(mir.blocks[b1].phis.contains(&p1));
        assert!(mir.blocks[b2].phis.contains(&p2));
    }

    #[test]
    fn decided_diamond_folds_into_its_head() {
        // A diamond whose arms became empty and whose join phi is dead:
        // threading + branch simplification + merging collapse it entirely.
        // b0: x; Branch{0: b1, default: b2}; b1/b2 empty -> b3: store; Exit
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let b2 = mir.push_block();
        let b3 = mir.push_block();
        let x = load(&mut mir, b0, concrete_place(20, 0));
        mir.blocks[b0].terminator = Terminator::Branch {
            test: x,
            cases: vec![(CaseCond::Int(0), b1)],
            default: Some(b2),
        };
        mir.blocks[b1].terminator = Terminator::Jump(b3);
        mir.blocks[b2].terminator = Terminator::Jump(b3);
        let c = mir.push_inst(Inst::ConstInt(7));
        store(&mut mir, b3, concrete_place(21, 0), c);
        assert!(run_pass(&mut mir));
        assert_eq!(
            mir.blocks[b0].terminator,
            Terminator::Exit,
            "diamond folded into the head"
        );
        assert_eq!(reachable(&mir).iter().filter(|&&r| r).count(), 1);
        assert!(
            mir.blocks[b0]
                .insts
                .iter()
                .any(|&v| matches!(mir.inst(v), Inst::Store { .. })),
            "join store merged into the head"
        );
    }

    // ------------------------------------------------------------------
    // Transform 2: empty-block threading with phi-argument copying
    // ------------------------------------------------------------------

    /// Shared empty block: both b0 (branch default) and b1 (jump) reach bE,
    /// which jumps into the phi join bJ. bD is the join's other predecessor
    /// (different argument, so the phi is not trivial); bJ carries four
    /// stores so duplication's cost model stays out of the picture.
    fn threading_fixture() -> (Mir, BlockId, BlockId, BlockId, BlockId, Value, Value) {
        // b0: x; Branch{0: bD, default: bE}
        // bD: store; Jump bJ
        // b1 is unreachable-from-entry by branch but reached via... no:
        // b0 default -> bE; b1: store; Jump bE  -- b1 needs a real pred, so
        // route it as a second case.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let bd = mir.push_block();
        let b1 = mir.push_block();
        let be = mir.push_block();
        let bj = mir.push_block();
        let x = load(&mut mir, b0, concrete_place(20, 0));
        mir.blocks[b0].terminator = Terminator::Branch {
            test: x,
            cases: vec![(CaseCond::Int(0), bd), (CaseCond::Int(1), b1)],
            default: Some(be),
        };
        let ca = mir.push_inst(Inst::ConstInt(4));
        let cb = mir.push_inst(Inst::ConstInt(9));
        store(&mut mir, bd, concrete_place(21, 1), ca);
        mir.blocks[bd].terminator = Terminator::Jump(bj);
        store(&mut mir, b1, concrete_place(21, 2), x);
        mir.blocks[b1].terminator = Terminator::Jump(be);
        mir.blocks[be].terminator = Terminator::Jump(bj);
        let p = phi(&mut mir, bj, vec![(bd, ca), (be, cb)]);
        store(&mut mir, bj, concrete_place(21, 0), p);
        store(&mut mir, bj, concrete_place(21, 3), p);
        store(&mut mir, bj, concrete_place(20, 5), p);
        store(&mut mir, bj, concrete_place(20, 6), p);
        (mir, b0, b1, be, bj, cb, p)
    }

    #[test]
    fn jump_source_threads_into_phi_join_with_arg_copy() {
        let (mut mir, b0, b1, be, bj, cb, p) = threading_fixture();
        assert!(run_pass(&mut mir));
        assert_eq!(
            mir.blocks[b1].terminator,
            Terminator::Jump(bj),
            "jump source threaded through the shared empty block"
        );
        let Inst::Phi { args } = mir.inst(p) else {
            panic!("phi");
        };
        assert!(
            args.contains(&(b1, cb)),
            "phi gained the copied argument keyed by the jump source: {args:?}"
        );
        assert!(args.contains(&(be, cb)), "bE still serves the branch edge");
        // The branch default stays on bE (multi-successor source).
        let Terminator::Branch { default, .. } = &mir.blocks[b0].terminator else {
            panic!("branch");
        };
        assert_eq!(*default, Some(be), "branch-source edge not threaded");
    }

    #[test]
    fn missing_phi_key_refuses_the_hop() {
        // bJ has three predecessors (bD, bE, and b0 directly) but its phi
        // lacks the bE key (malformed input): the hop is infeasible and b1's
        // edge must stay on bE. The remaining args differ, so the phi is not
        // trivial.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let bd = mir.push_block();
        let b1 = mir.push_block();
        let be = mir.push_block();
        let bj = mir.push_block();
        let x = load(&mut mir, b0, concrete_place(20, 0));
        // b0 reaches bj directly (case 2) and be via the default, so be has
        // two predecessors (no chain merge) and the phi's surviving keys are
        // genuine predecessors (no hygiene pruning).
        mir.blocks[b0].terminator = Terminator::Branch {
            test: x,
            cases: vec![
                (CaseCond::Int(0), bd),
                (CaseCond::Int(1), b1),
                (CaseCond::Int(2), bj),
            ],
            default: Some(be),
        };
        let ca = mir.push_inst(Inst::ConstInt(4));
        let cc = mir.push_inst(Inst::ConstInt(6));
        store(&mut mir, bd, concrete_place(21, 1), ca);
        mir.blocks[bd].terminator = Terminator::Jump(bj);
        store(&mut mir, b1, concrete_place(21, 2), x);
        mir.blocks[b1].terminator = Terminator::Jump(be);
        mir.blocks[be].terminator = Terminator::Jump(bj);
        // No (be, _) argument — deliberately malformed.
        let p = phi(&mut mir, bj, vec![(bd, ca), (b0, cc)]);
        store(&mut mir, bj, concrete_place(21, 0), p);
        store(&mut mir, bj, concrete_place(21, 3), p);
        store(&mut mir, bj, concrete_place(20, 5), p);
        store(&mut mir, bj, concrete_place(20, 6), p);
        let _ = run_pass(&mut mir);
        assert_eq!(
            mir.blocks[b1].terminator,
            Terminator::Jump(be),
            "edge unchanged when the phi lacks the chain key"
        );
    }

    // ------------------------------------------------------------------
    // Transform 3: exit shaping
    // ------------------------------------------------------------------

    #[test]
    fn jump_to_empty_exit_becomes_exit() {
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let c = mir.push_inst(Inst::ConstInt(3));
        store(&mut mir, b0, concrete_place(21, 0), c);
        mir.blocks[b0].terminator = Terminator::Jump(b1);
        // b1 is an empty exit block.
        assert!(run_pass(&mut mir));
        assert_eq!(mir.blocks[b0].terminator, Terminator::Exit);
    }

    #[test]
    fn branch_default_to_empty_exit_is_dropped() {
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let bx = mir.push_block();
        let x = load(&mut mir, b0, concrete_place(20, 0));
        mir.blocks[b0].terminator = Terminator::Branch {
            test: x,
            cases: vec![(CaseCond::Int(0), b1)],
            default: Some(bx),
        };
        store(&mut mir, b1, concrete_place(21, 0), x);
        assert!(run_pass(&mut mir));
        let Terminator::Branch { cases, default, .. } = &mir.blocks[b0].terminator else {
            panic!("still a branch: {:?}", mir.blocks[b0].terminator);
        };
        assert_eq!(*default, None, "default to empty exit dropped");
        assert_eq!(cases.len(), 1);
    }

    #[test]
    fn case_to_empty_exit_drops_without_default_when_density_is_kept() {
        // cases [(0, b1), (1, bx)], no default: dropping the trailing case
        // keeps the set dense ([(0, b1)]).
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let bx = mir.push_block();
        let x = load(&mut mir, b0, concrete_place(20, 0));
        mir.blocks[b0].terminator = Terminator::Branch {
            test: x,
            cases: vec![(CaseCond::Int(0), b1), (CaseCond::Int(1), bx)],
            default: None,
        };
        store(&mut mir, b1, concrete_place(21, 0), x);
        assert!(run_pass(&mut mir));
        let Terminator::Branch { cases, .. } = &mir.blocks[b0].terminator else {
            panic!("still a branch");
        };
        assert_eq!(cases.len(), 1, "empty-exit case dropped");
        assert_eq!(cases[0].1, b1);
    }

    #[test]
    fn case_drop_refused_when_it_breaks_a_dense_switch() {
        // cases [(0, bx), (1, b1)], no default: dropping case 0 would leave
        // the sparse [(1, b1)] — a dense O(1) switch degrading to a linear
        // one. Refused by the cost rule.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let bx = mir.push_block();
        let b1 = mir.push_block();
        let x = load(&mut mir, b0, concrete_place(20, 0));
        mir.blocks[b0].terminator = Terminator::Branch {
            test: x,
            cases: vec![(CaseCond::Int(0), bx), (CaseCond::Int(1), b1)],
            default: None,
        };
        store(&mut mir, b1, concrete_place(21, 0), x);
        let _ = run_pass(&mut mir);
        let Terminator::Branch { cases, .. } = &mir.blocks[b0].terminator else {
            panic!("still a branch");
        };
        assert_eq!(cases.len(), 2, "dense set kept intact");
    }

    // ------------------------------------------------------------------
    // Transform 5: tiny-block duplication
    // ------------------------------------------------------------------

    /// b0 branches to two arms that both jump into a tiny join block.
    fn dup_fixture(join_insts: impl FnOnce(&mut Mir, BlockId)) -> (Mir, BlockId, BlockId, BlockId) {
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let b2 = mir.push_block();
        let bt = mir.push_block();
        let x = load(&mut mir, b0, concrete_place(20, 0));
        mir.blocks[b0].terminator = Terminator::Branch {
            test: x,
            cases: vec![(CaseCond::Int(0), b1)],
            default: Some(b2),
        };
        store(&mut mir, b1, concrete_place(21, 1), x);
        mir.blocks[b1].terminator = Terminator::Jump(bt);
        store(&mut mir, b2, concrete_place(21, 2), x);
        mir.blocks[b2].terminator = Terminator::Jump(bt);
        join_insts(&mut mir, bt);
        (mir, b1, b2, bt)
    }

    #[test]
    fn tiny_block_duplicates_into_jump_preds_and_dies() {
        let (mut mir, b1, b2, bt) = dup_fixture(|mir, bt| {
            let c = mir.push_inst(Inst::ConstInt(5));
            store(mir, bt, concrete_place(21, 0), c);
        });
        assert!(run_pass(&mut mir));
        assert!(!reachable(&mir)[bt], "fully duplicated block went dead");
        for arm in [b1, b2] {
            assert_eq!(mir.blocks[arm].terminator, Terminator::Exit);
            assert_eq!(
                mir.blocks[arm].insts.len(),
                2,
                "arm has its own store plus the clone"
            );
        }
    }

    #[test]
    fn phi_in_tiny_block_is_substituted_per_predecessor() {
        // bt: p = phi[(b1, ca), (b2, cb)]; Store 21[0] <- p; Exit. The clones
        // must store ca in b1 and cb in b2 (no phi survives anywhere).
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let b2 = mir.push_block();
        let bt = mir.push_block();
        let x = load(&mut mir, b0, concrete_place(20, 0));
        mir.blocks[b0].terminator = Terminator::Branch {
            test: x,
            cases: vec![(CaseCond::Int(0), b1)],
            default: Some(b2),
        };
        let ca = mir.push_inst(Inst::ConstInt(11));
        let cb = mir.push_inst(Inst::ConstInt(22));
        store(&mut mir, b1, concrete_place(21, 1), x);
        mir.blocks[b1].terminator = Terminator::Jump(bt);
        store(&mut mir, b2, concrete_place(21, 2), x);
        mir.blocks[b2].terminator = Terminator::Jump(bt);
        let p = phi(&mut mir, bt, vec![(b1, ca), (b2, cb)]);
        store(&mut mir, bt, concrete_place(21, 0), p);
        assert!(run_pass(&mut mir));
        assert!(!reachable(&mir)[bt]);
        let arm_stores = |b: BlockId, expect: Value| {
            mir.blocks[b].insts.iter().any(|&v| {
                matches!(mir.inst(v), Inst::Store { place, value } if *value == expect
                        && matches!(place.index, IndexRef::Const(0)))
            })
        };
        assert!(arm_stores(b1, ca), "b1 clone stores the b1-keyed argument");
        assert!(arm_stores(b2, cb), "b2 clone stores the b2-keyed argument");
    }

    #[test]
    fn successor_phis_gain_arguments_for_new_predecessors() {
        // bt is tiny and jumps to bs, which has a phi keyed by bt. After
        // duplication, b1 and b2 jump (or branch) to bs and the phi must have
        // arguments for them.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let b2 = mir.push_block();
        let bt = mir.push_block();
        let bs = mir.push_block();
        let x = load(&mut mir, b0, concrete_place(20, 0));
        mir.blocks[b0].terminator = Terminator::Branch {
            test: x,
            cases: vec![(CaseCond::Int(0), b1)],
            default: Some(b2),
        };
        store(&mut mir, b1, concrete_place(21, 1), x);
        mir.blocks[b1].terminator = Terminator::Jump(bt);
        store(&mut mir, b2, concrete_place(21, 2), x);
        mir.blocks[b2].terminator = Terminator::Jump(bt);
        let c = mir.push_inst(Inst::ConstInt(5));
        store(&mut mir, bt, concrete_place(21, 0), c);
        mir.blocks[bt].terminator = Terminator::Jump(bs);
        let outer = mir.push_inst(Inst::ConstInt(8));
        let p = phi(&mut mir, bs, vec![(bt, outer)]);
        store(&mut mir, bs, concrete_place(21, 3), p);
        assert!(run_pass(&mut mir));
        // The phi may have been simplified away (all args are `outer`); the
        // store must then use `outer` directly. Either way behavior holds:
        let store_uses = mir.insts.iter().any(|inst| {
            matches!(inst, Inst::Store { place, value } if *value == outer
                && matches!(place.index, IndexRef::Const(3)))
        });
        assert!(
            store_uses,
            "successor's phi resolved to the copied argument"
        );
        assert!(!reachable(&mir)[bt]);
    }

    #[test]
    fn rng_bearing_tiny_block_is_not_duplicated() {
        let (mut mir, b1, b2, bt) = dup_fixture(|mir, bt| {
            let c0 = mir.push_inst(Inst::ConstInt(0));
            let c1 = mir.push_inst(Inst::ConstInt(2));
            let r = op(mir, bt, Op::Random, vec![c0, c1]);
            store(mir, bt, concrete_place(21, 0), r);
        });
        let _ = run_pass(&mut mir);
        assert!(reachable(&mir)[bt], "RNG block kept");
        assert_eq!(mir.blocks[b1].terminator, Terminator::Jump(bt));
        assert_eq!(mir.blocks[b2].terminator, Terminator::Jump(bt));
    }

    #[test]
    fn outside_use_refuses_duplication() {
        // bt defines y, referenced by its successor's phi (an edge use):
        // duplication would bypass the definition on cloned paths — refused.
        // bs has a second predecessor so the chain merge cannot first
        // localize the use.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let b2 = mir.push_block();
        let bt = mir.push_block();
        let bs = mir.push_block();
        let x = load(&mut mir, b0, concrete_place(20, 0));
        mir.blocks[b0].terminator = Terminator::Branch {
            test: x,
            cases: vec![(CaseCond::Int(0), b1), (CaseCond::Int(1), bs)],
            default: Some(b2),
        };
        store(&mut mir, b1, concrete_place(21, 1), x);
        mir.blocks[b1].terminator = Terminator::Jump(bt);
        store(&mut mir, b2, concrete_place(21, 2), x);
        mir.blocks[b2].terminator = Terminator::Jump(bt);
        let y = load(&mut mir, bt, concrete_place(20, 1));
        mir.blocks[bt].terminator = Terminator::Jump(bs);
        let c = mir.push_inst(Inst::ConstInt(5));
        let p = phi(&mut mir, bs, vec![(bt, y), (b0, c)]);
        store(&mut mir, bs, concrete_place(21, 0), p);
        let _ = run_pass(&mut mir);
        assert!(reachable(&mir)[bt], "block with escaping def kept");
        assert_eq!(mir.blocks[b1].terminator, Terminator::Jump(bt));
        assert_eq!(mir.blocks[b2].terminator, Terminator::Jump(bt));
    }

    #[test]
    fn clone_cost_threshold_refuses_duplication() {
        let (mut mir, b1, _b2, bt) = dup_fixture(|mir, bt| {
            // 4 scheduled instructions > DUP_MAX_CLONE_INSTS.
            let c = mir.push_inst(Inst::ConstInt(5));
            for i in 0..4 {
                store(mir, bt, concrete_place(21, i), c);
            }
        });
        let _ = run_pass(&mut mir);
        assert!(reachable(&mir)[bt], "oversized block kept");
        assert_eq!(mir.blocks[b1].terminator, Terminator::Jump(bt));
    }

    #[test]
    fn successor_phi_cost_counts_toward_the_threshold() {
        // bt: 2 stores, jumps to bs with 2 phis -> per-pred cost 4 > 3.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let b2 = mir.push_block();
        let bt = mir.push_block();
        let bs = mir.push_block();
        let x = load(&mut mir, b0, concrete_place(20, 0));
        mir.blocks[b0].terminator = Terminator::Branch {
            test: x,
            cases: vec![(CaseCond::Int(0), b1)],
            default: Some(b2),
        };
        store(&mut mir, b1, concrete_place(21, 1), x);
        mir.blocks[b1].terminator = Terminator::Jump(bt);
        store(&mut mir, b2, concrete_place(21, 2), x);
        mir.blocks[b2].terminator = Terminator::Jump(bt);
        let c = mir.push_inst(Inst::ConstInt(5));
        store(&mut mir, bt, concrete_place(21, 0), c);
        store(&mut mir, bt, concrete_place(21, 3), c);
        mir.blocks[bt].terminator = Terminator::Jump(bs);
        let o1 = mir.push_inst(Inst::ConstInt(1));
        let o2 = mir.push_inst(Inst::ConstInt(2));
        let p1 = phi(&mut mir, bs, vec![(bt, o1)]);
        let p2 = phi(&mut mir, bs, vec![(bt, o2)]);
        store(&mut mir, bs, concrete_place(20, 6), p1);
        store(&mut mir, bs, concrete_place(20, 7), p2);
        let _ = run_pass(&mut mir);
        assert!(reachable(&mir)[bt], "successor-phi cost refused the clone");
    }

    #[test]
    fn lazy_tree_is_cloned_whole_with_its_owner() {
        // bt: sc = And(x', DebugLog(7)) as a bare effectful statement (clone
        // budget: 2 scheduled + 1 lazy interior = 3). Duplication must
        // deep-clone the owned lazy tree (distinct arena values), and the
        // duplicated program must behave identically.
        let (mut mir, b1, b2, bt) = dup_fixture(|mir, bt| {
            let xp = load(mir, bt, concrete_place(20, 1));
            let c7 = mir.push_inst(Inst::ConstInt(7));
            let log = mir.push_inst(Inst::Op {
                op: Op::DebugLog,
                pure_node: false,
                args: vec![c7],
            });
            sched(
                mir,
                bt,
                Inst::ShortCircuit {
                    op: Op::And,
                    pure_node: true,
                    lhs: xp,
                    rhs: log,
                },
            );
        });
        let before = mir.clone();
        assert!(run_pass(&mut mir));
        assert!(!reachable(&mir)[bt], "tiny lazy-bearing block duplicated");
        for arm in [b1, b2] {
            let sc = mir.blocks[arm]
                .insts
                .iter()
                .find(|&&v| matches!(mir.inst(v), Inst::ShortCircuit { .. }))
                .copied()
                .expect("arm has a cloned ShortCircuit");
            let Inst::ShortCircuit { rhs, .. } = mir.inst(sc) else {
                unreachable!()
            };
            assert!(
                *rhs as usize >= before.insts.len(),
                "lazy tree deep-cloned, not shared"
            );
        }
        // Behavior: log fires only when the lhs (20[1]) is truthy.
        for in20 in [&[0.0, 0.0][..], &[0.0, 1.0][..], &[5.0, 1.0][..]] {
            let (m_before, log_before, _, disp_before, _) = finish_and_run(&before, in20);
            let (m_after, log_after, _, disp_after, _) = finish_and_run(&mir, in20);
            assert_eq!(m_before, m_after, "memory equal for {in20:?}");
            assert_eq!(log_before, log_after, "log equal for {in20:?}");
            assert!(disp_after < disp_before, "dispatch dropped for {in20:?}");
        }
    }

    #[test]
    fn oversized_lazy_tree_refuses_duplication() {
        // The owned lazy interiors count toward the clone budget.
        let (mut mir, _b1, _b2, bt) = dup_fixture(|mir, bt| {
            let xp = load(mir, bt, concrete_place(20, 1));
            let c7 = mir.push_inst(Inst::ConstInt(7));
            let mut node = mir.push_inst(Inst::Op {
                op: Op::DebugLog,
                pure_node: false,
                args: vec![c7],
            });
            for _ in 0..3 {
                node = mir.push_inst(Inst::Op {
                    op: Op::Abs,
                    pure_node: true,
                    args: vec![node],
                });
            }
            let sc = sched(
                mir,
                bt,
                Inst::ShortCircuit {
                    op: Op::And,
                    pure_node: true,
                    lhs: xp,
                    rhs: node,
                },
            );
            store(mir, bt, concrete_place(21, 0), sc);
        });
        let _ = run_pass(&mut mir);
        assert!(reachable(&mir)[bt], "oversized lazy tree refused");
    }

    #[test]
    fn trap_capable_duplication_preserves_per_path_behavior() {
        // bt divides by 20[1] — trap-capable, but duplication executes the
        // clone exactly once per original path, so behavior (including the
        // trap) is identical. Run both a trapping and a non-trapping input.
        let (mut mir, _b1, _b2, bt) = dup_fixture(|mir, bt| {
            let a = load(mir, bt, concrete_place(20, 1));
            let c1 = mir.push_inst(Inst::ConstInt(1));
            let q = op(mir, bt, Op::Divide, vec![c1, a]);
            store(mir, bt, concrete_place(21, 0), q);
        });
        let before = mir.clone();
        assert!(run_pass(&mut mir));
        assert!(!reachable(&mir)[bt], "trap-capable tiny block duplicated");
        // Non-trapping input: identical results, fewer dispatches.
        let (m_b, _, _, disp_b, _) = finish_and_run(&before, &[1.0, 4.0]);
        let (m_a, _, _, disp_a, _) = finish_and_run(&mir, &[1.0, 4.0]);
        assert_eq!(m_b, m_a);
        assert!(disp_a < disp_b);
        // Trapping input (division by zero): identical error on both sides.
        let run_err = |mir: &Mir| {
            let mut m = mir.clone();
            destruct_ssa(&mut m).unwrap();
            let alloc = allocate_temps(&m).unwrap();
            let lowered = lower_mir(&m, &alloc).unwrap();
            let nodes = cfg_to_engine_nodes(&lowered).unwrap();
            let mut interp = Interpreter::new(0);
            interp.set_block(20, vec![1.0, 0.0]);
            interp
                .run(&nodes)
                .expect_err("division by zero")
                .to_string()
        };
        assert_eq!(run_err(&before), run_err(&mir));
    }

    // ------------------------------------------------------------------
    // Transform 6: exit combining
    // ------------------------------------------------------------------

    /// Two exit blocks with separately-built but structurally identical
    /// schedules (load + op + store), plus one differing exit.
    fn exits_fixture() -> (Mir, BlockId, BlockId, BlockId) {
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let e1 = mir.push_block();
        let e2 = mir.push_block();
        let e3 = mir.push_block();
        let x = load(&mut mir, b0, concrete_place(20, 0));
        mir.blocks[b0].terminator = Terminator::Branch {
            test: x,
            cases: vec![(CaseCond::Int(0), e1), (CaseCond::Int(1), e2)],
            default: Some(e3),
        };
        for e in [e1, e2] {
            let l = load(&mut mir, e, concrete_place(20, 1));
            let c = mir.push_inst(Inst::ConstInt(3));
            let y = op(&mut mir, e, Op::Add, vec![l, c]);
            store(&mut mir, e, concrete_place(21, 0), y);
        }
        // e3 differs (different constant).
        let l = load(&mut mir, e3, concrete_place(20, 1));
        let c = mir.push_inst(Inst::ConstInt(4));
        let y = op(&mut mir, e3, Op::Add, vec![l, c]);
        store(&mut mir, e3, concrete_place(21, 0), y);
        (mir, e1, e2, e3)
    }

    #[test]
    fn structurally_identical_exit_blocks_are_combined() {
        let (mut mir, e1, e2, e3) = exits_fixture();
        assert!(run_pass(&mut mir));
        let r = reachable(&mir);
        assert!(r[e1], "leader survives");
        assert!(!r[e2], "duplicate combined into the leader");
        assert!(r[e3], "differing exit block kept");
        let Terminator::Branch { cases, .. } = &mir.blocks[0].terminator else {
            panic!("branch");
        };
        assert_eq!(cases[0].1, e1);
        assert_eq!(cases[1].1, e1, "case edge re-pointed to the leader");
    }

    #[test]
    fn empty_exit_blocks_combine_via_case_edges() {
        // Two empty exits as case targets with a real default: exit shaping
        // cannot drop the cases (default present), combining merges them.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let x1 = mir.push_block();
        let x2 = mir.push_block();
        let bd = mir.push_block();
        let x = load(&mut mir, b0, concrete_place(20, 0));
        mir.blocks[b0].terminator = Terminator::Branch {
            test: x,
            cases: vec![(CaseCond::Int(0), x1), (CaseCond::Int(1), x2)],
            default: Some(bd),
        };
        store(&mut mir, bd, concrete_place(21, 0), x);
        assert!(run_pass(&mut mir));
        let Terminator::Branch { cases, .. } = &mir.blocks[b0].terminator else {
            panic!("branch");
        };
        assert_eq!(cases[0].1, cases[1].1, "empty exits combined");
        let _ = (x1, x2);
    }

    #[test]
    fn differing_exit_blocks_stay_separate() {
        let (mut mir, e1, e3) = {
            let (mir, e1, _e2, e3) = exits_fixture();
            (mir, e1, e3)
        };
        let _ = run_pass(&mut mir);
        let r = reachable(&mir);
        assert!(r[e1] && r[e3], "non-identical exits both reachable");
    }

    // ------------------------------------------------------------------
    // End-to-end + pass contract
    // ------------------------------------------------------------------

    #[test]
    fn end_to_end_dispatch_and_get_set_reduction() {
        // The threading fixture's real payoff, measured end to end on the
        // interpreter: a diamond whose arms feed a join through value-bearing
        // edges. Shaping threads the jump arm, then duplicates the tiny join
        // into both predecessors — removing dispatcher round trips AND the
        // out-of-SSA temp-slot Set/Get pairs for the edge values.
        //
        // b0: x = 20[0]; Branch{0: b1, default: b2}
        // b1: y1 = Add(20[1], 1); Jump bJ
        // b2: y2 = Subtract(20[2], 2); Jump bE; bE: Jump bJ
        // bJ: p = phi[(b1, y1), (bE, y2)]; 21[0] <- p; Exit
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let b2 = mir.push_block();
        let be = mir.push_block();
        let bj = mir.push_block();
        let x = load(&mut mir, b0, concrete_place(20, 0));
        mir.blocks[b0].terminator = Terminator::Branch {
            test: x,
            cases: vec![(CaseCond::Int(0), b1)],
            default: Some(b2),
        };
        let l1 = load(&mut mir, b1, concrete_place(20, 1));
        let c1 = mir.push_inst(Inst::ConstInt(1));
        let y1 = op(&mut mir, b1, Op::Add, vec![l1, c1]);
        mir.blocks[b1].terminator = Terminator::Jump(bj);
        let l2 = load(&mut mir, b2, concrete_place(20, 2));
        let c2 = mir.push_inst(Inst::ConstInt(2));
        let y2 = op(&mut mir, b2, Op::Subtract, vec![l2, c2]);
        mir.blocks[b2].terminator = Terminator::Jump(be);
        mir.blocks[be].terminator = Terminator::Jump(bj);
        let p = phi(&mut mir, bj, vec![(b1, y1), (be, y2)]);
        store(&mut mir, bj, concrete_place(21, 0), p);

        let before = mir.clone();
        assert!(run_pass(&mut mir));

        for in20 in [&[0.0, 7.0, 9.0][..], &[5.0, 7.0, 9.0][..]] {
            let (m_b, log_b, eval_b, disp_b, tree_b) = finish_and_run(&before, in20);
            let (m_a, log_a, eval_a, disp_a, tree_a) = finish_and_run(&mir, in20);
            assert_eq!(m_b, m_a, "behavior preserved for {in20:?}");
            assert_eq!(log_b, log_a);
            assert!(
                disp_a < disp_b,
                "dispatch reduced for {in20:?}: {disp_b} -> {disp_a}"
            );
            assert!(
                eval_a < eval_b,
                "eval count reduced for {in20:?}: {eval_b} -> {eval_a}"
            );
            let gets_sets = |t: &str| count_op(t, "Get") + count_op(t, "Set");
            assert!(
                gets_sets(&tree_a) < gets_sets(&tree_b),
                "static Get/Set reduced: {} -> {}",
                gets_sets(&tree_b),
                gets_sets(&tree_a)
            );
        }
    }

    #[test]
    fn pass_is_idempotent_and_reports_honest_changed_flags() {
        let (mut mir, _b1, _b2, _bt) = dup_fixture(|mir, bt| {
            let c = mir.push_inst(Inst::ConstInt(5));
            store(mir, bt, concrete_place(21, 0), c);
        });
        assert!(run_pass(&mut mir), "first run changes");
        assert!(!run_pass(&mut mir), "second run is a no-op");
    }

    #[test]
    fn inert_mir_reports_no_change() {
        // A single straight-line block: nothing fires; the changed flag must
        // be false (the Pipeline debug fingerprint enforces honesty).
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let c = mir.push_inst(Inst::ConstInt(1));
        store(&mut mir, b0, concrete_place(21, 0), c);
        let pipeline = crate::passes::Pipeline::new(vec![Box::new(ShapePass)]);
        let mut analyses = Analyses::new();
        assert!(!pipeline.run(&mut mir, &mut analyses));
    }

    #[test]
    fn empty_mir_is_a_no_op() {
        let mut mir = Mir::new();
        assert!(!run_pass(&mut mir));
    }
}
