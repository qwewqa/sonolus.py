//! W3 switch formation (PORT.md T3.6) — the successor of the legacy
//! `RewriteToSwitch` pass (`sonolus/backend/optimize/simplify.py`), redesigned
//! for MIR (decision D2). It recognizes post-GVN comparison chains on a single
//! scrutinee and rewrites them into one multi-way block, so the emitter's
//! dense-form selection (T1.2 / legacy `finalize.py`) can produce
//! `SwitchIntegerWithDefault` O(1) dispatch where the merged cases are dense
//! `0..n`, and even the sparse `SwitchWithDefault` form replaces a chain of
//! `JumpLoop` round-trips with a single dispatch. Headline metric:
//! **`dispatch_count`**, with `eval_count` riding along (the chain's
//! intermediate `Equal`s and dispatcher evaluations disappear).
//!
//! # Transform 1 — Equal-test branches become single-case switches
//!
//! The frontend lowers `if x == c:` to a block testing `Equal(x, c)` with
//! edges `{0: else, None: then}` (legacy `ifs_to_switch` recognized exactly
//! this `{None, 0}` shape). On MIR that is
//!
//! ```text
//! Branch { test: Equal(a, b), cases: [(0-valued cond, F)], default: Some(T) }
//! ```
//!
//! with exactly one of `a`/`b` a **finite constant** `c` (post-GVN canonical
//! order puts constants first, but both positions are accepted — the per-pass
//! differential pipelines run without GVN). It becomes
//!
//! ```text
//! Branch { test: scrutinee, cases: [(c, T)], default: Some(F) }
//! ```
//!
//! preserving the constant's int/float tag on the case cond (the tag is
//! load-bearing for output; `is_dense` accepts integral floats). Exactness:
//! `Equal` computes Python `==` over the two f64s and yields 1/0; the branch
//! takes the 0-valued case iff the result is 0. The rewritten branch's cond
//! matching is the *same* Python `==` between scrutinee and `c` (interpreter
//! `SwitchWithDefault` / emitter `If(Equal(test, c), ...)`), so every input —
//! including NaN scrutinee (`NaN == c` is false ⇒ default = old else) and
//! `-0.0` (`-0.0 == 0`) — takes the identical edge. **Non-finite constants are
//! refused**: a NaN cond is forbidden by the encoding (and would never match),
//! and ±inf conds would be emitted *raw* into `SwitchWithDefault` pairs where
//! the legacy `IRConst` path routes non-finite constants through ROM reads —
//! the legacy pass allowed them; this port deliberately does not.
//!
//! The replaced `Equal` is unscheduled when (and only when) it has no
//! remaining references — `Equal` is pure and on the never-trapping whitelist
//! (DCE's `op_is_total`), so dropping its evaluation is unobservable; its
//! scrutinee operand stays referenced by the new test and its constant operand
//! needs no schedule slot, so no transitive orphan can arise (contrast the
//! G3.2 GVN sweep lesson: nothing trap-capable is ever cascaded here — only
//! the rewritten `Equal`s themselves, layer by layer for nested-`Equal`
//! tests). An `Equal` with other uses stays scheduled; the rewrite is still
//! valid, it just saves nothing.
//!
//! Nested `Equal` tests collapse to a per-block fixpoint: rewriting
//! `Branch { test: Equal(Equal(a, c2), 0), ... }` once leaves an
//! `Equal`-on-`0` shape that converts again (each step replaces the test with
//! one of its operands — strictly smaller arena index, so it terminates).
//!
//! # Transform 2 — same-scrutinee chain merging
//!
//! ```text
//! A: Branch { test: x, cases: CA, default: Some(B) }
//! B: Branch { test: x', cases: CB, default: DB }      (B empty otherwise)
//!   ⇒  A: Branch { test: x, cases: sort(CA ∪ (CB \ values(CA))), default: DB }
//! ```
//!
//! (the legacy `combine_blocks`, with value identity replacing structural
//! test equality). Preconditions, each load-bearing:
//!
//! - `B ≠ A`, `B ≠ entry`, `B` has exactly one predecessor block (`A`) and is
//!   not a case target of `A` (predecessor lists deduplicate parallel edges,
//!   so the case-target check closes that hole — legacy counted incoming
//!   *edges*).
//! - `B` has **no phis** and **no scheduled instructions** beyond what the
//!   scrutinee-equivalence rule itself accounts for (below). An empty block
//!   provably contains no store, no RNG draw, and no trap-capable evaluation,
//!   which is the entire effect-safety argument: merging deletes B's
//!   execution, so B's execution must be unobservable. RNG ops are therefore
//!   never removed, duplicated, or reordered by this pass, and nothing is
//!   ever hoisted across (or out of) a lazy `ShortCircuit` boundary (D11) —
//!   the pass never looks inside lazy trees except to *conservatively refuse*
//!   (a lazy tree with a potential write blocks the variant-(b) re-load
//!   elision via `inst_effects_deep`).
//! - **Same scrutinee value**, one of two proofs:
//!   - *(a) value identity*: `x' == x` (the same SSA `Value`; the pass runs
//!     post-GVN/Mem2Reg where promoted scrutinees are shared values or phis —
//!     a `Value`'s runtime value is fixed at its single definition, which
//!     already dominates `A`'s use of it), with `B.insts` empty;
//!   - *(b) re-load of the same cell*: `x` and `x'` are both `Load`s of the
//!     **structurally identical constant place** (same `Temp`/`Concrete`
//!     block, same constant index, same offset; never the concrete temp
//!     runtime block 10000), `B.insts == [x']` and `x'` has no other
//!     reference, `x` is scheduled in `A` with **no possibly-clobbering
//!     instruction after it** (stores to an aliasing place — whole-block
//!     granularity, with `BlockRef::Value` and block-10000 treated as
//!     aliasing everything relevant — plus any op/lazy-tree whose effects
//!     include `writes_memory`). Then B's re-load reads the unmodified cell:
//!     same value, and it cannot trap where A's identical load did not — so
//!     eliding it is unobservable. This is the post-GVN shape of a shared
//!     extracted scrutinee (`gvnN` temp loads) and of unpromoted scrutinees
//!     (legacy compared `IRGet` trees structurally and merged the same way;
//!     its empty-statements guard played the role of the clobber scan).
//! - Case conds of `CB` whose numeric value (Python `==`, via
//!   [`CaseCond::value`]; conds are never NaN) duplicates one in `CA` are
//!   dropped as unreachable — `x` equal to that value already left at `A`
//!   (legacy keyed a dict by cond value). The merged list is re-sorted
//!   ascending, the order the lowering/emitter contract requires; cond values
//!   stay pairwise distinct so order is not observable.
//! - **Phi keying**: kept targets' phi args keyed by `B` are re-keyed to `A`;
//!   a kept target that has phis *and* already has `A` as a predecessor (or
//!   *is* `A`) refuses the merge (re-keying would collide two args on one
//!   key); dropped targets get their `B`-keyed args pruned (legacy UCE
//!   hygiene). No kept target may be `B` itself (`B` is cleared).
//! - `DB` may be `None`: an unmatched scrutinee exits the callback in both
//!   the chained and the merged form (the emitter's no-default dispatchers
//!   use the exit index).
//!
//! After a merge, `B` is cleared in place (empty schedule, `Exit` terminator)
//! exactly like DCE's unreachable-clearing, keeping every `BlockId` stable.
//! Merging iterates to a fixpoint so whole `if/elif/elif/...` chains collapse
//! into one block; each merge permanently clears one block, bounding the
//! loop.
//!
//! # What legacy did that this pass deliberately does not
//!
//! - Merge on **structural** test equality of arbitrary expressions: MIR
//!   value identity plus the constant-cell re-load rule covers the real
//!   shapes (promoted scrutinees, `gvnN` loads, unpromoted temp/concrete
//!   reads); general recomputed-expression equivalence is GVN's job, not
//!   re-proved here.
//! - Allow non-finite case conds (above).
//! - `remove_unreachable`: clearing the absorbed block in place subsumes it
//!   (lowering's RPO never visits unreachable blocks).
//!
//! # Pass discipline
//!
//! Deterministic: ascending block/instruction scans, first-candidate-wins
//! merges, `Vec`s only (no hash-map iteration anywhere). Iterative: explicit
//! loops, no recursion (invariant §3.4). Mid-level IR stays binary (§3.3) —
//! only terminators and schedules are touched, never instruction shapes.
//! Invalidation: every mutation changes CFG shape (terminators), so a changed
//! run calls `invalidate_all`.

use crate::alloc::TEMP_RUNTIME_BLOCK;
use crate::analysis::Analyses;
use crate::effects::{inst_effects_deep, op_effects};
use crate::mir::{BlockId, BlockRef, CaseCond, IndexRef, Inst, Mir, Place, Terminator, Value};
use crate::ops::Op;
use crate::passes::Pass;

/// The T3.6 pass. See the module docs.
#[derive(Debug, Default)]
pub struct SwitchForm;

impl Pass for SwitchForm {
    fn name(&self) -> &'static str {
        "switch-form"
    }

    fn run(&self, mir: &mut Mir, analyses: &mut Analyses) -> bool {
        if mir.blocks.is_empty() {
            return false;
        }
        let mut changed = rewrite_equal_tests(mir);
        changed |= merge_chains(mir);
        if changed {
            analyses.invalidate_all();
        }
        changed
    }
}

// ----------------------------------------------------------------------------------
// Transform 1: Equal-test branches -> single-case switches
// ----------------------------------------------------------------------------------

/// Whether a case cond compares numerically equal to zero (Python `==`: `0`,
/// `0.0`, and `-0.0` all match; conds are never NaN).
#[allow(clippy::float_cmp)]
fn cond_is_zero(cond: CaseCond) -> bool {
    cond.value() == 0.0
}

/// The case cond for a constant comparison operand, tag-preserving. Non-finite
/// floats are refused (module docs: NaN conds are outside the encoding's
/// domain and ±inf conds would emit raw where the legacy `IRConst` path uses
/// ROM reads).
fn constant_case(mir: &Mir, v: Value) -> Option<CaseCond> {
    match *mir.inst(v) {
        Inst::ConstInt(c) => Some(CaseCond::Int(c)),
        Inst::ConstFloat(f) if f.is_finite() => Some(CaseCond::Float(f)),
        _ => None,
    }
}

/// Transform 1 over every block (per-block fixpoint for nested `Equal`s),
/// then unscheduling of the rewritten `Equal`s that lost their last use.
fn rewrite_equal_tests(mir: &mut Mir) -> bool {
    let scheduled = mir.scheduled_mask();
    let mut rewritten: Vec<Value> = Vec::new();
    let mut changed = false;
    for b in 0..mir.blocks.len() {
        // Each iteration replaces the test with one of its operands (strictly
        // smaller arena index), so this terminates.
        while let Terminator::Branch {
            test,
            cases,
            default: Some(default),
        } = &mir.blocks[b].terminator
        {
            let (test, then_target) = (*test, *default);
            let &[(zero_cond, else_target)] = cases.as_slice() else {
                break;
            };
            if !cond_is_zero(zero_cond) {
                break;
            }
            let Inst::Op {
                op: Op::Equal,
                args,
                ..
            } = mir.inst(test)
            else {
                break;
            };
            // Binary by construction (§3.3); defensive for hand-built MIR.
            let &[a, bv] = args.as_slice() else {
                break;
            };
            let (scrutinee, case) = match (constant_case(mir, a), constant_case(mir, bv)) {
                (Some(c), None) => (bv, c),
                (None, Some(c)) => (a, c),
                // Both constant (SCCP's job) or neither (no switch to form).
                _ => break,
            };
            // The new test must be an ordinary eager value: scheduled
            // somewhere (insts or phis) or a constant. A lazy-owned or
            // dangling value here would be out-of-contract MIR; refuse rather
            // than propagate it into a terminator (D11).
            if !(mir.is_const(scrutinee) || scheduled[scrutinee as usize]) {
                break;
            }
            mir.blocks[b].terminator = Terminator::Branch {
                test: scrutinee,
                cases: vec![(case, then_target)],
                default: Some(else_target),
            };
            rewritten.push(test);
            changed = true;
        }
    }
    changed |= unschedule_dead_equals(mir, &rewritten);
    changed
}

/// Unschedules every rewritten `Equal` whose reference count reached zero.
/// Iterates because nested-`Equal` rewrites cascade (the outer `Equal`'s
/// removal frees the inner one), strictly shrinking `remaining` each round.
/// Only the rewritten `Equal`s themselves are ever unscheduled — pure, total,
/// no transitive orphan cascade (module docs).
fn unschedule_dead_equals(mir: &mut Mir, rewritten: &[Value]) -> bool {
    let mut changed = false;
    let mut remaining: Vec<Value> = rewritten.to_vec();
    while !remaining.is_empty() {
        let counts = count_refs(mir);
        let (dead, alive): (Vec<Value>, Vec<Value>) =
            remaining.iter().partition(|&&v| counts[v as usize] == 0);
        if dead.is_empty() {
            break;
        }
        let mut dead_mask = vec![false; mir.insts.len()];
        for &v in &dead {
            dead_mask[v as usize] = true;
        }
        let mut removed = 0usize;
        for block in &mut mir.blocks {
            let before = block.insts.len();
            block.insts.retain(|&v| !dead_mask[v as usize]);
            removed += before - block.insts.len();
        }
        if removed == 0 {
            // The dead candidates were not scheduled anywhere (defensive);
            // nothing changed and nothing further can cascade.
            break;
        }
        changed = true;
        remaining = alive;
    }
    changed
}

/// Reference counts over everything that can reference a value: phi
/// arguments, operands of scheduled instructions (including the owned lazy
/// `ShortCircuit` rhs trees), and terminator tests. Mirrors GVN's
/// `count_scheduled_uses` (each pass owns its counting, like DCE and
/// `lower::count_uses`).
fn count_refs(mir: &Mir) -> Vec<u32> {
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
            if let Inst::ShortCircuit { rhs, .. } = inst {
                lazy_stack.push(*rhs);
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
        }
        if let Terminator::Branch { test, .. } = &block.terminator {
            counts[*test as usize] += 1;
        }
    }
    counts
}

// ----------------------------------------------------------------------------------
// Transform 2: same-scrutinee chain merging
// ----------------------------------------------------------------------------------

/// Transform 2 to fixpoint. First candidate (ascending block order) wins each
/// round; predecessors and reference counts are recomputed after every merge
/// (a merge changes both). Each merge clears one block for good, bounding the
/// loop by the block count.
fn merge_chains(mir: &mut Mir) -> bool {
    let mut changed = false;
    loop {
        let preds = mir.predecessors();
        let counts = count_refs(mir);
        let mut merged = false;
        for a in 0..mir.blocks.len() {
            if try_merge(mir, &preds, &counts, a) {
                merged = true;
                changed = true;
                break;
            }
        }
        if !merged {
            break;
        }
    }
    changed
}

/// Attempts to merge `A`'s default successor chain block into `A` (module
/// docs). Returns whether a merge happened.
fn try_merge(mir: &mut Mir, preds: &[Vec<BlockId>], counts: &[u32], a: BlockId) -> bool {
    let Terminator::Branch {
        test,
        cases,
        default: Some(b),
    } = &mir.blocks[a].terminator
    else {
        return false;
    };
    let (test_a, cases_a, b) = (*test, cases.clone(), *b);
    if b == a || b == 0 {
        return false;
    }
    if preds[b].as_slice() != [a] {
        return false;
    }
    // preds deduplicates parallel edges: a case edge A->B alongside the
    // default would still leave preds[b] == [a], so check explicitly.
    if cases_a.iter().any(|&(_, t)| t == b) {
        return false;
    }
    if !mir.blocks[b].phis.is_empty() {
        return false;
    }
    let Terminator::Branch {
        test: test_b,
        cases: cases_b,
        default: default_b,
    } = &mir.blocks[b].terminator
    else {
        return false;
    };
    let (test_b, cases_b, default_b) = (*test_b, cases_b.clone(), *default_b);

    // Same-scrutinee proof: value identity (a) or same-cell re-load (b).
    let equivalent = if test_b == test_a {
        mir.blocks[b].insts.is_empty()
    } else {
        loads_same_unclobbered_cell(mir, counts, a, test_a, b, test_b)
    };
    if !equivalent {
        return false;
    }

    // B-cases whose cond value duplicates an A-case are unreachable: that
    // scrutinee value already left at A (Python ==; conds are never NaN).
    #[allow(clippy::float_cmp)]
    let kept: Vec<(CaseCond, BlockId)> = cases_b
        .iter()
        .copied()
        .filter(|&(c, _)| !cases_a.iter().any(|&(ca, _)| ca.value() == c.value()))
        .collect();

    // Guards on the targets the merged terminator will carry.
    for t in kept.iter().map(|&(_, t)| t).chain(default_b) {
        if t == b {
            // The target block is about to be cleared; a self-edge of B (or
            // B as its own default) would change behavior. Refuse.
            return false;
        }
        if !mir.blocks[t].phis.is_empty() && (t == a || preds[t].contains(&a)) {
            // Re-keying B->A would collide with an existing A-keyed phi arg
            // (or require a self-pred arg on A's own phis). Refuse.
            return false;
        }
    }

    // Apply: merged case list (sorted ascending — the terminator contract;
    // values are pairwise distinct so the order is not observable), B's
    // default becomes A's.
    let mut new_cases = cases_a;
    new_cases.extend(kept.iter().copied());
    new_cases.sort_by(|x, y| x.0.value().total_cmp(&y.0.value()));
    mir.blocks[a].terminator = Terminator::Branch {
        test: test_a,
        cases: new_cases,
        default: default_b,
    };

    // Clear B in place (BlockIds stay stable, like DCE's unreachable
    // clearing). In variant (b) this also unschedules B's re-load — its
    // only reference was B's old terminator.
    mir.blocks[b].insts.clear();
    mir.blocks[b].terminator = Terminator::Exit;

    // Phi-arg hygiene in B's former successors: kept targets re-key B->A;
    // dropped targets lose their B-keyed args (B predeceases them).
    let mut kept_targets: Vec<BlockId> = kept.iter().map(|&(_, t)| t).chain(default_b).collect();
    kept_targets.sort_unstable();
    kept_targets.dedup();
    let mut all_targets: Vec<BlockId> = cases_b.iter().map(|&(_, t)| t).chain(default_b).collect();
    all_targets.sort_unstable();
    all_targets.dedup();
    for t in all_targets {
        let is_kept = kept_targets.binary_search(&t).is_ok();
        let phis = mir.blocks[t].phis.clone();
        for phi in phis {
            if let Inst::Phi { args } = &mut mir.insts[phi as usize] {
                if is_kept {
                    for (p, _) in args.iter_mut() {
                        if *p == b {
                            *p = a;
                        }
                    }
                } else {
                    args.retain(|&(p, _)| p != b);
                }
            }
        }
    }
    true
}

/// Variant (b) of the same-scrutinee proof: `test_a` and `test_b` are loads
/// of the structurally identical constant cell, `B`'s schedule is exactly its
/// own re-load (no other reference to it), and nothing after `test_a` in `A`
/// can clobber the cell. See the module docs for the full argument.
fn loads_same_unclobbered_cell(
    mir: &Mir,
    counts: &[u32],
    a: BlockId,
    test_a: Value,
    b: BlockId,
    test_b: Value,
) -> bool {
    if mir.blocks[b].insts.as_slice() != [test_b] {
        return false;
    }
    if counts[test_b as usize] != 1 {
        return false;
    }
    let (Inst::Load { place: pa }, Inst::Load { place: pb }) = (mir.inst(test_a), mir.inst(test_b))
    else {
        return false;
    };
    if pa != pb {
        return false;
    }
    if !matches!(pa.index, IndexRef::Const(_)) {
        return false;
    }
    match pa.block {
        // The concrete temp runtime block aliases temps post-allocation.
        BlockRef::Concrete(c) => {
            if c == TEMP_RUNTIME_BLOCK {
                return false;
            }
        }
        BlockRef::Temp(_) => {}
        BlockRef::Value(_) => return false,
    }
    let place = *pa;
    // test_a must be scheduled in A (its evaluation point bounds the clobber
    // window), with nothing after it that may write the cell.
    let Some(pos) = mir.blocks[a].insts.iter().position(|&v| v == test_a) else {
        return false;
    };
    mir.blocks[a].insts[pos + 1..]
        .iter()
        .all(|&v| !may_clobber(mir, &place, v))
}

/// Whether executing `v` may write the loaded place. Conservative: stores are
/// checked by block-level aliasing (any index), ops by their `writes_memory`
/// effect, `ShortCircuit` by the deep effects of its owned lazy tree (D11 —
/// the lazy side *may* run, so a potential write inside it blocks the merge).
fn may_clobber(mir: &Mir, loaded: &Place, v: Value) -> bool {
    match mir.inst(v) {
        Inst::Store { place, .. } => places_may_alias(loaded, place),
        Inst::Op { op, .. } => op_effects(*op).writes_memory,
        Inst::ShortCircuit { .. } => inst_effects_deep(mir, v).writes_memory,
        Inst::ConstInt(_) | Inst::ConstFloat(_) | Inst::Load { .. } | Inst::Phi { .. } => false,
    }
}

/// Block-granular place aliasing: computed block refs alias everything;
/// distinct temps never alias each other; a concrete block aliases a temp
/// only when it is the temp runtime block (10000); distinct concrete blocks
/// never alias. Indices are deliberately ignored (coarse, always safe).
fn places_may_alias(a: &Place, b: &Place) -> bool {
    match (a.block, b.block) {
        (BlockRef::Value(_), _) | (_, BlockRef::Value(_)) => true,
        (BlockRef::Temp(x), BlockRef::Temp(y)) => x == y,
        (BlockRef::Temp(_), BlockRef::Concrete(c)) | (BlockRef::Concrete(c), BlockRef::Temp(_)) => {
            c == TEMP_RUNTIME_BLOCK
        }
        (BlockRef::Concrete(x), BlockRef::Concrete(y)) => x == y,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::analysis::Analyses;
    use crate::mir::TempId;

    fn run_pass(mir: &mut Mir) -> bool {
        let mut analyses = Analyses::new();
        SwitchForm.run(mir, &mut analyses)
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

    fn store_to(mir: &mut Mir, block: BlockId, out_index: i64, value: i64) {
        let c = mir.push_inst(Inst::ConstInt(value));
        sched(
            mir,
            block,
            Inst::Store {
                place: concrete_place(20, out_index),
                value: c,
            },
        );
    }

    fn equal(mir: &mut Mir, block: BlockId, lhs: Value, rhs: Value) -> Value {
        sched(
            mir,
            block,
            Inst::Op {
                op: Op::Equal,
                pure_node: true,
                args: vec![lhs, rhs],
            },
        )
    }

    /// `if x == 5` MIR: entry loads x, tests Equal(5, x) with edges
    /// `{0: else, None: then}` (the frontend shape, const canonically first).
    fn if_equal_mir(case_const: Inst, const_first: bool) -> (Mir, Value, Value) {
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b_then = mir.push_block();
        let b_else = mir.push_block();
        store_to(&mut mir, b_then, 0, 10);
        store_to(&mut mir, b_else, 0, 20);
        let x = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(21, 0),
            },
        );
        let c = mir.push_inst(case_const);
        let eq = if const_first {
            equal(&mut mir, b0, c, x)
        } else {
            equal(&mut mir, b0, x, c)
        };
        mir.blocks[b0].terminator = Terminator::Branch {
            test: eq,
            cases: vec![(CaseCond::Int(0), b_else)],
            default: Some(b_then),
        };
        (mir, x, eq)
    }

    #[test]
    fn if_equal_branch_converts_to_switch() {
        for const_first in [true, false] {
            let (mut mir, x, eq) = if_equal_mir(Inst::ConstInt(5), const_first);
            assert!(run_pass(&mut mir));
            assert_eq!(
                mir.blocks[0].terminator,
                Terminator::Branch {
                    test: x,
                    cases: vec![(CaseCond::Int(5), 1)],
                    default: Some(2),
                },
                "const_first={const_first}"
            );
            // The Equal lost its last use and is unscheduled; the load stays.
            assert!(!mir.blocks[0].insts.contains(&eq));
            assert_eq!(mir.blocks[0].insts.as_slice(), &[x]);
        }
    }

    #[test]
    fn float_const_converts_with_float_tag() {
        let (mut mir, x, _) = if_equal_mir(Inst::ConstFloat(2.0), true);
        assert!(run_pass(&mut mir));
        let Terminator::Branch { test, cases, .. } = &mir.blocks[0].terminator else {
            panic!("must stay a branch");
        };
        assert_eq!(*test, x);
        assert_eq!(cases.as_slice(), &[(CaseCond::Float(2.0), 1)]);
    }

    #[test]
    fn non_finite_consts_are_refused() {
        for c in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
            let (mut mir, _, eq) = if_equal_mir(Inst::ConstFloat(c), true);
            assert!(!run_pass(&mut mir), "const {c} must be refused");
            let Terminator::Branch { test, .. } = &mir.blocks[0].terminator else {
                panic!();
            };
            assert_eq!(*test, eq, "the Equal test must survive");
        }
    }

    #[test]
    fn non_constant_comparison_is_refused() {
        // Equal(x, y) with both operands loads: no switch to form.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let b2 = mir.push_block();
        store_to(&mut mir, b1, 0, 1);
        store_to(&mut mir, b2, 0, 2);
        let x = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(21, 0),
            },
        );
        let y = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(21, 1),
            },
        );
        let eq = equal(&mut mir, b0, x, y);
        mir.blocks[b0].terminator = Terminator::Branch {
            test: eq,
            cases: vec![(CaseCond::Int(0), b2)],
            default: Some(b1),
        };
        assert!(!run_pass(&mut mir));
    }

    #[test]
    fn non_equal_ops_and_non_zero_conds_are_refused() {
        // Less-test branch: untouched.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let b2 = mir.push_block();
        store_to(&mut mir, b1, 0, 1);
        store_to(&mut mir, b2, 0, 2);
        let x = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(21, 0),
            },
        );
        let c = mir.push_inst(Inst::ConstInt(5));
        let less = sched(
            &mut mir,
            b0,
            Inst::Op {
                op: Op::Less,
                pure_node: true,
                args: vec![x, c],
            },
        );
        mir.blocks[b0].terminator = Terminator::Branch {
            test: less,
            cases: vec![(CaseCond::Int(0), b2)],
            default: Some(b1),
        };
        assert!(!run_pass(&mut mir));

        // Equal-test branch but the single case cond is 1, not 0: untouched.
        let (mut mir, _, _) = if_equal_mir(Inst::ConstInt(5), true);
        let Terminator::Branch { test, default, .. } = mir.blocks[0].terminator.clone() else {
            panic!();
        };
        mir.blocks[0].terminator = Terminator::Branch {
            test,
            cases: vec![(CaseCond::Int(1), 2)],
            default,
        };
        assert!(!run_pass(&mut mir));
    }

    #[test]
    fn missing_default_is_refused() {
        let (mut mir, _, _) = if_equal_mir(Inst::ConstInt(5), true);
        let Terminator::Branch { test, cases, .. } = mir.blocks[0].terminator.clone() else {
            panic!();
        };
        mir.blocks[0].terminator = Terminator::Branch {
            test,
            cases,
            default: None,
        };
        assert!(!run_pass(&mut mir));
    }

    #[test]
    fn equal_with_other_uses_stays_scheduled() {
        // The Equal's value is also stored: the branch is rewritten but the
        // Equal keeps its schedule slot.
        let (mut mir, x, eq) = if_equal_mir(Inst::ConstInt(5), true);
        sched(
            &mut mir,
            0,
            Inst::Store {
                place: concrete_place(20, 7),
                value: eq,
            },
        );
        assert!(run_pass(&mut mir));
        let Terminator::Branch { test, .. } = &mir.blocks[0].terminator else {
            panic!();
        };
        assert_eq!(*test, x);
        assert!(mir.blocks[0].insts.contains(&eq), "Equal still has a user");
    }

    #[test]
    fn equal_referenced_from_lazy_tree_stays_scheduled() {
        // Out-of-contract-but-possible: a lazy tree referencing the scheduled
        // Equal counts as a use (count_refs walks lazy trees).
        let (mut mir, x, eq) = if_equal_mir(Inst::ConstInt(5), true);
        let one = mir.push_inst(Inst::ConstInt(1));
        sched(
            &mut mir,
            1,
            Inst::ShortCircuit {
                op: Op::And,
                pure_node: true,
                lhs: one,
                rhs: eq,
            },
        );
        assert!(run_pass(&mut mir));
        let Terminator::Branch { test, .. } = &mir.blocks[0].terminator else {
            panic!();
        };
        assert_eq!(*test, x);
        assert!(mir.blocks[0].insts.contains(&eq));
    }

    #[test]
    fn nested_equal_tests_collapse_and_cascade() {
        // Branch test Equal(Equal(x, 7), 0) with {0: else, None: then}:
        // converts twice — outer: switch on Equal(x,7) with case (0, then);
        // that is again an Equal-on-0 shape, so it converts to a switch on x
        // with case (7, else). Both Equals end up unscheduled.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b_then = mir.push_block();
        let b_else = mir.push_block();
        store_to(&mut mir, b_then, 0, 10);
        store_to(&mut mir, b_else, 0, 20);
        let x = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(21, 0),
            },
        );
        let seven = mir.push_inst(Inst::ConstInt(7));
        let inner = equal(&mut mir, b0, x, seven);
        let zero = mir.push_inst(Inst::ConstInt(0));
        let outer = equal(&mut mir, b0, inner, zero);
        mir.blocks[b0].terminator = Terminator::Branch {
            test: outer,
            cases: vec![(CaseCond::Int(0), b_else)],
            default: Some(b_then),
        };
        assert!(run_pass(&mut mir));
        // Semantics check: x == 7 -> inner = 1 -> outer = Equal(1, 0) = 0 ->
        // case 0 -> else. x != 7 -> inner = 0 -> outer = 1 -> default -> then.
        assert_eq!(
            mir.blocks[b0].terminator,
            Terminator::Branch {
                test: x,
                cases: vec![(CaseCond::Int(7), b_else)],
                default: Some(b_then),
            }
        );
        assert_eq!(mir.blocks[b0].insts.as_slice(), &[x], "both Equals swept");
    }

    /// A two-link chain on the same Value:
    /// b0: Branch{x, [(1, t1)], default: b1}; b1 (empty): Branch{x, [(2, t2)],
    /// default: e}. Returns (mir, x, t1, t2, e).
    fn value_chain_mir() -> (Mir, Value, BlockId, BlockId, BlockId) {
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let t1 = mir.push_block();
        let t2 = mir.push_block();
        let e = mir.push_block();
        store_to(&mut mir, t1, 0, 11);
        store_to(&mut mir, t2, 0, 22);
        store_to(&mut mir, e, 0, 33);
        let x = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(21, 0),
            },
        );
        mir.blocks[b0].terminator = Terminator::Branch {
            test: x,
            cases: vec![(CaseCond::Int(1), t1)],
            default: Some(b1),
        };
        mir.blocks[b1].terminator = Terminator::Branch {
            test: x,
            cases: vec![(CaseCond::Int(2), t2)],
            default: Some(e),
        };
        (mir, x, t1, t2, e)
    }

    #[test]
    fn same_value_chain_merges_into_multiway_block() {
        let (mut mir, x, t1, t2, e) = value_chain_mir();
        assert!(run_pass(&mut mir));
        assert_eq!(
            mir.blocks[0].terminator,
            Terminator::Branch {
                test: x,
                cases: vec![(CaseCond::Int(1), t1), (CaseCond::Int(2), t2)],
                default: Some(e),
            }
        );
        // The absorbed block is cleared in place.
        assert!(mir.blocks[1].insts.is_empty());
        assert_eq!(mir.blocks[1].terminator, Terminator::Exit);
        // Idempotent: a second run is a no-op.
        assert!(!run_pass(&mut mir));
    }

    #[test]
    fn duplicate_cond_values_are_dropped_as_unreachable() {
        // b1's case 1 duplicates b0's case 1 (x == 1 already left at b0),
        // including across int/float tags (Python ==).
        let (mut mir, x, t1, t2, e) = value_chain_mir();
        let Terminator::Branch { cases, .. } = &mut mir.blocks[1].terminator else {
            panic!();
        };
        cases.insert(0, (CaseCond::Float(1.0), t2));
        assert!(run_pass(&mut mir));
        assert_eq!(
            mir.blocks[0].terminator,
            Terminator::Branch {
                test: x,
                cases: vec![(CaseCond::Int(1), t1), (CaseCond::Int(2), t2)],
                default: Some(e),
            }
        );
    }

    #[test]
    fn different_scrutinee_values_do_not_merge() {
        // b1 branches on its own load (a different Value reading a different
        // cell): never merged.
        let (mut mir, _, _, t2, e) = value_chain_mir();
        let y = sched(
            &mut mir,
            1,
            Inst::Load {
                place: concrete_place(21, 5),
            },
        );
        mir.blocks[1].terminator = Terminator::Branch {
            test: y,
            cases: vec![(CaseCond::Int(2), t2)],
            default: Some(e),
        };
        assert!(!run_pass(&mut mir));
    }

    #[test]
    fn effectful_intermediate_block_does_not_merge() {
        // The chain block contains a store: merging would delete it.
        let (mut mir, _, _, _, _) = value_chain_mir();
        store_to(&mut mir, 1, 9, 99);
        assert!(!run_pass(&mut mir));
        assert!(matches!(
            mir.blocks[1].terminator,
            Terminator::Branch { .. }
        ));
    }

    #[test]
    fn phi_bearing_intermediate_block_does_not_merge() {
        let (mut mir, x, _, _, _) = value_chain_mir();
        let phi = mir.push_inst(Inst::Phi { args: vec![(0, x)] });
        mir.blocks[1].phis.push(phi);
        assert!(!run_pass(&mut mir));
    }

    #[test]
    fn multi_predecessor_intermediate_block_does_not_merge() {
        // A second block also jumps to b1: preds(b1) != [b0].
        let (mut mir, _, _, _, _) = value_chain_mir();
        let extra = mir.push_block();
        mir.blocks[extra].terminator = Terminator::Jump(1);
        // Keep `extra` reachable so this is not just dead-edge noise.
        mir.blocks[3].terminator = Terminator::Jump(extra);
        assert!(!run_pass(&mut mir));
    }

    #[test]
    fn kept_target_phis_are_rekeyed_to_the_absorber() {
        // The chain's final default block has a phi keyed by b1: after the
        // merge its predecessor is b0.
        let (mut mir, x, _, _, e) = value_chain_mir();
        let phi = mir.push_inst(Inst::Phi { args: vec![(1, x)] });
        mir.blocks[e].phis.push(phi);
        sched(
            &mut mir,
            e,
            Inst::Store {
                place: concrete_place(20, 4),
                value: phi,
            },
        );
        assert!(run_pass(&mut mir));
        let Inst::Phi { args } = mir.inst(phi) else {
            panic!();
        };
        assert_eq!(args.as_slice(), &[(0, x)], "phi re-keyed b1 -> b0");
    }

    #[test]
    fn kept_target_with_existing_absorber_pred_refuses_merge() {
        // t1 is a case target of b0 AND b1's default, and t1 has a phi:
        // re-keying b1 -> b0 would collide with the existing b0 arg.
        let (mut mir, x, t1, _, _) = value_chain_mir();
        let Terminator::Branch { default, .. } = &mut mir.blocks[1].terminator else {
            panic!();
        };
        *default = Some(t1);
        let c9 = mir.push_inst(Inst::ConstInt(9));
        let phi = mir.push_inst(Inst::Phi {
            args: vec![(0, x), (1, c9)],
        });
        mir.blocks[t1].phis.push(phi);
        assert!(!run_pass(&mut mir));
        // Without the phi the same shape merges fine.
        mir.blocks[t1].phis.clear();
        assert!(run_pass(&mut mir));
    }

    #[test]
    fn dropped_target_phi_args_are_pruned() {
        // b1's case 1 duplicates b0's case 1 and targets a phi-bearing block
        // u (only reachable that way): the edge is dropped and u's b1-keyed
        // phi arg pruned.
        let (mut mir, x, _, _, _) = value_chain_mir();
        let u = mir.push_block();
        let phi = mir.push_inst(Inst::Phi { args: vec![(1, x)] });
        mir.blocks[u].phis.push(phi);
        let Terminator::Branch { cases, .. } = &mut mir.blocks[1].terminator else {
            panic!();
        };
        cases.insert(0, (CaseCond::Int(1), u));
        assert!(run_pass(&mut mir));
        let Inst::Phi { args } = mir.inst(phi) else {
            panic!();
        };
        assert!(args.is_empty(), "stale b1-keyed arg pruned: {args:?}");
    }

    #[test]
    fn self_targeting_chain_block_refuses_merge() {
        // b1's kept case targets b1 itself; clearing b1 would change the
        // self-loop's behavior.
        let (mut mir, x, _, _, e) = value_chain_mir();
        mir.blocks[1].terminator = Terminator::Branch {
            test: x,
            cases: vec![(CaseCond::Int(2), 1)],
            default: Some(e),
        };
        assert!(!run_pass(&mut mir));
    }

    /// A two-link chain where each block re-loads the same temp cell
    /// (variant (b)): `b0: [load_a] Branch{load_a, [(1, t1)], b1}`;
    /// `b1: [load_b] Branch{load_b, [(2, t2)], e}`.
    fn reload_chain_mir(place: Place) -> (Mir, Value, Value, BlockId, BlockId, BlockId) {
        let mut mir = Mir::new();
        let _ = mir.push_temp("s", 2);
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let t1 = mir.push_block();
        let t2 = mir.push_block();
        let e = mir.push_block();
        store_to(&mut mir, t1, 0, 11);
        store_to(&mut mir, t2, 0, 22);
        store_to(&mut mir, e, 0, 33);
        let load_a = sched(&mut mir, b0, Inst::Load { place });
        let load_b = sched(&mut mir, b1, Inst::Load { place });
        mir.blocks[b0].terminator = Terminator::Branch {
            test: load_a,
            cases: vec![(CaseCond::Int(1), t1)],
            default: Some(b1),
        };
        mir.blocks[b1].terminator = Terminator::Branch {
            test: load_b,
            cases: vec![(CaseCond::Int(2), t2)],
            default: Some(e),
        };
        (mir, load_a, load_b, t1, t2, e)
    }

    #[test]
    fn same_cell_reload_chain_merges() {
        for place in [temp_place(0), concrete_place(21, 3)] {
            let (mut mir, load_a, _, t1, t2, e) = reload_chain_mir(place);
            assert!(run_pass(&mut mir), "place {place:?}");
            assert_eq!(
                mir.blocks[1].terminator,
                Terminator::Exit,
                "chain block cleared"
            );
            assert!(mir.blocks[1].insts.is_empty(), "re-load elided");
            assert_eq!(
                mir.blocks[0].terminator,
                Terminator::Branch {
                    test: load_a,
                    cases: vec![(CaseCond::Int(1), t1), (CaseCond::Int(2), t2)],
                    default: Some(e),
                }
            );
        }
    }

    #[test]
    fn reload_of_different_cell_does_not_merge() {
        // Same temp, different index: not the same scrutinee.
        let (mut mir, _, load_b, t2, e, _) = {
            let (mir, a, b, t1, t2, e) = reload_chain_mir(temp_place(0));
            let _ = (a, t1);
            (mir, a, b, t2, e, 0)
        };
        let other = Place {
            block: BlockRef::Temp(0),
            index: IndexRef::Const(1),
            offset: 0,
        };
        mir.insts[load_b as usize] = Inst::Load { place: other };
        let _ = (t2, e);
        assert!(!run_pass(&mut mir));
    }

    #[test]
    fn intervening_aliasing_store_blocks_the_reload_merge() {
        // A store to the same temp *after* the scrutinee load in b0: the
        // re-load in b1 may observe it, so the merge must be refused. A store
        // to a different temp (or before the load) is fine.
        let s = temp_place(0);
        let (mut mir, load_a, _, _, _, _) = reload_chain_mir(s);
        let c = mir.push_inst(Inst::ConstInt(7));
        sched(&mut mir, 0, Inst::Store { place: s, value: c });
        assert!(!run_pass(&mut mir), "aliasing store after the load");

        let (mut mir, _, _, _, _, _) = reload_chain_mir(s);
        let u = mir.push_temp("u", 1);
        let c = mir.push_inst(Inst::ConstInt(7));
        sched(
            &mut mir,
            0,
            Inst::Store {
                place: temp_place(u),
                value: c,
            },
        );
        assert!(run_pass(&mut mir), "non-aliasing store after the load");

        let (mut mir, _, _, _, _, _) = reload_chain_mir(s);
        let c = mir.push_inst(Inst::ConstInt(7));
        let st = mir.push_inst(Inst::Store { place: s, value: c });
        mir.blocks[0].insts.insert(0, st);
        assert!(run_pass(&mut mir), "aliasing store BEFORE the load is fine");
        let _ = load_a;
    }

    #[test]
    fn computed_block_store_blocks_the_reload_merge() {
        // A store through a computed block ref may alias anything.
        let (mut mir, _, _, _, _, _) = reload_chain_mir(concrete_place(21, 3));
        let bid = sched(
            &mut mir,
            0,
            Inst::Load {
                place: concrete_place(20, 9),
            },
        );
        let c = mir.push_inst(Inst::ConstInt(7));
        sched(
            &mut mir,
            0,
            Inst::Store {
                place: Place {
                    block: BlockRef::Value(bid),
                    index: IndexRef::Const(0),
                    offset: 0,
                },
                value: c,
            },
        );
        assert!(!run_pass(&mut mir));
    }

    #[test]
    fn lazy_tree_with_write_blocks_the_reload_merge() {
        // A ShortCircuit after the scrutinee load whose lazy side may write
        // memory (DebugLog counts as observable): conservative refusal (D11 —
        // the pass only ever looks at lazy trees to refuse).
        let (mut mir, _, _, _, _, _) = reload_chain_mir(temp_place(0));
        let one = mir.push_inst(Inst::ConstInt(1));
        let log = mir.push_inst(Inst::Op {
            op: Op::DebugLog,
            pure_node: false,
            args: vec![one],
        });
        let lhs = mir.push_inst(Inst::ConstInt(1));
        sched(
            &mut mir,
            0,
            Inst::ShortCircuit {
                op: Op::And,
                pure_node: true,
                lhs,
                rhs: log,
            },
        );
        assert!(!run_pass(&mut mir));
    }

    #[test]
    fn reload_with_extra_reference_does_not_merge() {
        // The chain block's re-load is also stored somewhere: clearing the
        // block would orphan that use.
        let (mut mir, _, load_b, _, _, _) = reload_chain_mir(temp_place(0));
        sched(
            &mut mir,
            3,
            Inst::Store {
                place: concrete_place(20, 8),
                value: load_b,
            },
        );
        assert!(!run_pass(&mut mir));
    }

    #[test]
    fn dynamic_index_reload_does_not_merge() {
        // Dynamic-index loads can trap and are never treated as the same
        // scrutinee.
        let (mut mir, load_a, load_b, _, _, _) = reload_chain_mir(temp_place(0));
        let idx = mir.push_inst(Inst::ConstInt(0));
        let dyn_place = Place {
            block: BlockRef::Temp(0),
            index: IndexRef::Value(idx),
            offset: 0,
        };
        mir.insts[load_a as usize] = Inst::Load { place: dyn_place };
        mir.insts[load_b as usize] = Inst::Load { place: dyn_place };
        assert!(!run_pass(&mut mir));
    }

    #[test]
    fn long_chain_collapses_to_one_dense_switch_block() {
        // Frontend-shaped chain `if x==0 {..} elif x==1 {..} elif x==2 {..}
        // else {..}` (Equal tests, {0, None} edges) collapses into one block
        // whose cases are dense 0..3 — the emitter's SwitchIntegerWithDefault
        // shape.
        let mut mir = Mir::new();
        let checks: Vec<BlockId> = (0..3).map(|_| mir.push_block()).collect();
        let arms: Vec<BlockId> = (0..3).map(|_| mir.push_block()).collect();
        let e = mir.push_block();
        for (i, &arm) in arms.iter().enumerate() {
            store_to(&mut mir, arm, 0, i64::try_from(i).unwrap() + 10);
        }
        store_to(&mut mir, e, 0, 99);
        let mut x = None;
        for i in 0..3 {
            let b = checks[i];
            let load = sched(
                &mut mir,
                b,
                Inst::Load {
                    place: concrete_place(21, 0),
                },
            );
            x.get_or_insert(load);
            let c = mir.push_inst(Inst::ConstInt(i64::try_from(i).unwrap()));
            let eq = equal(&mut mir, b, c, load);
            let next = if i + 1 < 3 { checks[i + 1] } else { e };
            mir.blocks[b].terminator = Terminator::Branch {
                test: eq,
                cases: vec![(CaseCond::Int(0), next)],
                default: Some(arms[i]),
            };
        }
        assert!(run_pass(&mut mir));
        assert_eq!(
            mir.blocks[checks[0]].terminator,
            Terminator::Branch {
                test: x.unwrap(),
                cases: vec![
                    (CaseCond::Int(0), arms[0]),
                    (CaseCond::Int(1), arms[1]),
                    (CaseCond::Int(2), arms[2]),
                ],
                default: Some(e),
            }
        );
        for &b in &checks[1..] {
            assert_eq!(mir.blocks[b].terminator, Terminator::Exit);
            assert!(mir.blocks[b].insts.is_empty());
        }
        assert!(!run_pass(&mut mir), "idempotent");
    }

    #[test]
    fn empty_mir_is_a_no_op() {
        let mut mir = Mir::new();
        assert!(!run_pass(&mut mir));
        let _ = mir.push_block();
        assert!(!run_pass(&mut mir));
    }

    // ------------------------------------------------------------------------------
    // End-to-end: frontend CFG -> pipeline with/without switch formation
    // ------------------------------------------------------------------------------

    use crate::cfg::{
        BasicBlock, BlockValue, Cfg, Edge, EdgeCond, IndexValue, Node, Place as CfgPlace,
        TempBlockDef,
    };
    use crate::diff::{DiffConfig, DiffOutcome, diff_with};
    use crate::interpret::Interpreter;
    use crate::nodes::format_engine_node;
    use crate::passes::Pipeline;
    use crate::passes::dce::DcePass;
    use crate::passes::gvn::GvnRewritePass;
    use crate::passes::mem2reg::Mem2Reg;
    use crate::passes::sccp::Sccp;
    use crate::pipeline::{Level, compile_cfg, compile_cfg_with_pipeline};

    /// The frontend CFG for an if/elif chain on a temp:
    ///
    /// ```text
    /// t <- Get(-3[0])
    /// if t == 0 { 20[0] <- 10 } elif t == 1 { 20[0] <- 11 }
    /// elif t == 2 { 20[0] <- 12 } else { 20[0] <- 99 }
    /// ```
    ///
    /// Exactly the legacy `ifs_to_switch` + `combine_blocks` showcase: each
    /// check block tests `Equal(Get(t), k)` with `{0: next, None: arm}`.
    fn if_chain_cfg() -> Cfg {
        let mut cfg = Cfg::default();
        cfg.strings.push("t".to_owned());
        cfg.temp_blocks.push(TempBlockDef { name: 0, size: 1 });
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
        // Blocks: 0..3 = checks 0..2 (entry = check 0), 3..6 = arms 0..2,
        // 6 = else arm, 7 = exit.
        let arm_base = 3;
        let else_arm = 6;
        let exit = 7;
        for k in 0..3usize {
            let mut stmts = Vec::new();
            if k == 0 {
                // Entry: t <- Get(-3[0]).
                let in_p = place(&mut cfg, BlockValue::Int(-3), 0);
                let get_in = node(&mut cfg, Node::Get(in_p));
                let t_p = place(&mut cfg, BlockValue::Temp(0), 0);
                stmts.push(node(
                    &mut cfg,
                    Node::Set {
                        place: t_p,
                        value: get_in,
                    },
                ));
            }
            let t_p = place(&mut cfg, BlockValue::Temp(0), 0);
            let get_t = node(&mut cfg, Node::Get(t_p));
            let c = node(&mut cfg, Node::ConstInt(i64::try_from(k).unwrap()));
            let eq = node(
                &mut cfg,
                Node::PureInstr {
                    op: Op::Equal,
                    args: vec![get_t, c],
                },
            );
            let next = if k + 1 < 3 { k + 1 } else { else_arm };
            cfg.blocks.push(BasicBlock {
                statements: stmts,
                test: eq,
                outgoing: vec![
                    Edge {
                        cond: EdgeCond::Int(0),
                        target: next,
                    },
                    Edge {
                        cond: EdgeCond::None,
                        target: arm_base + k,
                    },
                ],
            });
        }
        for (i, value) in [10i64, 11, 12, 99].into_iter().enumerate() {
            // Arms 0..2 then the else arm, all jumping to the exit.
            let out_p = place(&mut cfg, BlockValue::Int(20), 0);
            let v = node(&mut cfg, Node::ConstInt(value));
            let set = node(
                &mut cfg,
                Node::Set {
                    place: out_p,
                    value: v,
                },
            );
            let zt = node(&mut cfg, Node::ConstInt(0));
            cfg.blocks.push(BasicBlock {
                statements: vec![set],
                test: zt,
                outgoing: vec![Edge {
                    cond: EdgeCond::None,
                    target: exit,
                }],
            });
            let _ = i;
        }
        let zt = node(&mut cfg, Node::ConstInt(0));
        cfg.blocks.push(BasicBlock {
            statements: vec![],
            test: zt,
            outgoing: vec![],
        });
        cfg
    }

    /// The standard registry prefix up to (not including) W3: the comparison
    /// baseline that isolates this pass's contribution.
    fn w2_pipeline() -> Pipeline {
        Pipeline::new(vec![
            Box::new(Sccp) as Box<dyn crate::passes::Pass>,
            Box::new(GvnRewritePass),
            Box::new(DcePass),
            Box::new(Mem2Reg),
            Box::new(Sccp),
            Box::new(GvnRewritePass),
            Box::new(DcePass),
        ])
    }

    fn w2_plus_switch_form() -> Pipeline {
        Pipeline::new(vec![
            Box::new(Sccp) as Box<dyn crate::passes::Pass>,
            Box::new(GvnRewritePass),
            Box::new(DcePass),
            Box::new(Mem2Reg),
            Box::new(Sccp),
            Box::new(GvnRewritePass),
            Box::new(DcePass),
            Box::new(SwitchForm),
        ])
    }

    #[test]
    fn if_chain_becomes_one_dense_switch_and_dispatch_drops() {
        let cfg = if_chain_cfg();
        let without = compile_cfg_with_pipeline(&cfg, &w2_pipeline()).unwrap();
        let with = compile_cfg_with_pipeline(&cfg, &w2_plus_switch_form()).unwrap();
        // The merged chain is dense {0, 1, 2}: the emitter must select
        // SwitchIntegerWithDefault (O(1) dispatch).
        let with_dump = format_engine_node(&with.arena, with.root);
        assert!(
            with_dump.contains("SwitchIntegerWithDefault"),
            "dense merged cases must emit an integer switch:\n{with_dump}"
        );
        // Behavior identical everywhere; dispatch/eval counts never worse,
        // and strictly lower on every input that walks past the first check
        // (input 0 exits at the first comparison either way).
        for input in [0.0, 1.0, 2.0, 7.5, f64::NAN] {
            let run = |nodes: &crate::nodes::EngineNodes| {
                let mut interp = Interpreter::new(0);
                interp.set_block(-3, vec![input]);
                interp.run(nodes).unwrap();
                let out = interp.block(20).unwrap()[0];
                (out, interp.dispatch_count(), interp.eval_count())
            };
            let (out_without, dispatch_without, eval_without) = run(&without);
            let (out_with, dispatch_with, eval_with) = run(&with);
            // Exact f64 equality is the contract: both sides computed the
            // same stored constant.
            #[allow(clippy::float_cmp)]
            {
                assert_eq!(out_with, out_without, "input {input}");
            }
            let walks_the_chain = input != 0.0;
            assert!(
                dispatch_with <= dispatch_without
                    && (!walks_the_chain || dispatch_with < dispatch_without),
                "input {input}: dispatch {dispatch_with} vs {dispatch_without}"
            );
            assert!(
                eval_with <= eval_without && (!walks_the_chain || eval_with < eval_without),
                "input {input}: eval {eval_with} vs {eval_without}"
            );
        }
        // And the standard level (which now includes this pass via the
        // registry) produces the same dense form.
        let standard = compile_cfg(&cfg, Level::Standard).unwrap();
        assert!(
            format_engine_node(&standard.arena, standard.root).contains("SwitchIntegerWithDefault")
        );
    }

    #[test]
    fn if_chain_diffs_clean_against_minimal() {
        let cfg = if_chain_cfg();
        for seed in [0u64, 1, 42] {
            let config = DiffConfig {
                memory_seed: seed,
                rng_seed: seed ^ 0xABCD,
                eval_budget: 100_000,
            };
            for (label, pipeline) in [
                (
                    "switch-form only",
                    Pipeline::new(vec![Box::new(SwitchForm) as Box<dyn crate::passes::Pass>]),
                ),
                ("w2 + switch-form", w2_plus_switch_form()),
            ] {
                let outcome = diff_with(
                    &cfg,
                    |c| compile_cfg(c, Level::Minimal),
                    |c| compile_cfg_with_pipeline(c, &pipeline),
                    &config,
                );
                assert_eq!(outcome, DiffOutcome::Match, "{label}, seed {seed}");
            }
        }
    }

    #[test]
    fn changed_flag_and_invalidation_pipeline_contract() {
        // Through the Pipeline so the debug fingerprint guard verifies the
        // changed flag on both a changing and a non-changing input.
        let cfg = if_chain_cfg();
        let mut mir = crate::mir::build_mir(&cfg).unwrap();
        let mut analyses = Analyses::new();
        let pipeline = Pipeline::new(vec![Box::new(SwitchForm) as Box<dyn crate::passes::Pass>]);
        assert!(pipeline.run(&mut mir, &mut analyses), "first run changes");
        assert!(!pipeline.run(&mut mir, &mut analyses), "fixpoint reached");
    }
}
