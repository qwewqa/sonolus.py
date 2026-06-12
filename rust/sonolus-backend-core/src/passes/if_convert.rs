//! W4 expression-level if-conversion (PORT.md T3.8).
//!
//! Converts small two-way CFG diamonds and triangles into a single block
//! computing each join phi's value as an [`Inst::Select`] (the target
//! runtime's `If` op as a value node) or, where the phi value on one side
//! *is* the branch test itself, an [`Inst::ShortCircuit`] `And`/`Or`. The
//! runtime's `If`/`And`/`Or` are control-flow ops usable as value nodes —
//! `If(c, t, f)` evaluates `c` and then **only** the taken arm — so a
//! converted arm remains conditionally evaluated, exactly like the original
//! arm block (decision D3: W4 is expression-level if-conversion, not
//! structured control-flow reconstruction).
//!
//! # Why (G3.3 diagnosis)
//!
//! The dominant quality gap vs the legacy backend is **cross-block SSA value
//! materialization**: every cross-block value lowers to a class-temp
//! `Set`+`Get` pair, and small diamonds force values cross-block (arm copies
//! plus a join read per phi, plus 2–3 `JumpLoop` dispatcher round trips).
//! Converting collapses the whole diamond into the head block: each phi
//! becomes an `If` value, the arm blocks vanish (`dispatch_count` drops), and
//! formerly-cross-block phi values become same-block single-use values that
//! the lowering already splices inline (`Get`/`Set` traffic drops). The
//! dominant pydori shapes (`while i < n and ...` condition diamonds, hot
//! loop-latch triangles) carry **multiple phis** and **arm stores to
//! unpromoted temps**, so both are in scope (see below).
//!
//! # Recognized shapes
//!
//! The head `H` must end in a two-way branch — `Branch { test, cases:
//! [(c, C)], default: Some(D) }` with `C != D` (post-W3 switch formation
//! leaves exactly this shape for two-way tests; denser switches are already
//! O(1) dispatch and are not touched):
//!
//! - **Diamond**: `C` and `D` are both *arm blocks* (single predecessor `H`,
//!   no phis, terminator `Jump(J)` to a common join `J`), and `J`'s
//!   predecessors are exactly `{C, D}`.
//! - **Triangle**: one side is an arm block jumping to `J` and `J` *is* the
//!   other side's target (`J`'s predecessors exactly `{H, arm}`); the
//!   join-edge side's phi arguments are values already available at `H`.
//!
//! `J` carries **one or more phis**; each phi `p_i` becomes its own select
//! `s_i`, evaluated in phi-list order at the end of `H` (one shared,
//! S4-slotted test — never re-evaluated). After conversion `H` jumps to `J`
//! unconditionally and `J` — whose only remaining predecessor is `H` — is
//! merged into `H` (phi args in `J`'s successors are re-keyed to `H`; no
//! collision is possible because pre-merge `H`'s only successor is `J`). The
//! merge is what turns the selects into same-block single-use values; it
//! deliberately overlaps with T3.9's general block merging only on this one
//! local edge (the conversion is incomplete without it).
//!
//! # Arm representation (D11: the lazy-tree machinery is reused)
//!
//! Each select side is built from:
//!
//! - the phi argument — a **constant**, an **external value** (defined at or
//!   before `H`; as a lazy arm root it is legalized by `destruct_ssa`'s
//!   existing S5 rule into an unscheduled class-temp load: the value still
//!   evaluates exactly once, unconditionally, at its original definition
//!   point, and only the *read* is conditional), or an **in-block expression
//!   tree** (the tree's instructions are unscheduled and become the select's
//!   owned lazy tree — the same species as a `ShortCircuit` rhs; nothing is
//!   deleted or duplicated, the nodes move from a dispatched block into the
//!   expression);
//! - plus the arm block's **statement roots** (instructions with no value
//!   uses: stores, logs): each attaches to exactly one select on its side
//!   and is wrapped before that select's value via a binary
//!   `Execute(stmt, rest)` chain — the runtime evaluates `stmt` then `rest`
//!   and yields `rest`, so the statements run iff the arm is taken, in
//!   order, exactly like the original arm block. Stores inside lazy trees
//!   are part of the lowering contract for this purpose (`lower.rs`,
//!   `ssa.rs` S5 docs: a lazy store can never alias a class temp).
//!
//! # Pure-total tree hoisting
//!
//! When the in-order conversion is impossible only because a phi-argument
//! tree completes *before* a later arm statement (e.g. the hot loop-latch
//! shape `v = load(t); ...; store(t, v + x)` — the load must stay before the
//! store), and that tree is **pure and total** (every member on DCE's
//! `op_is_total` whitelist / `load_is_total`), the tree is hoisted to `H`
//! instead: it executes unconditionally — unobservable for never-trapping
//! pure code (the LICM speculation argument) — and its root reaches the arm
//! as an external value. Because hoisting moves the tree across every arm
//! instruction scheduled before it, it additionally requires all tree
//! members to **precede the arm's first memory writer** (deep effects),
//! else a hoisted load could read a pre-store value (found by the 50k
//! diamond-heavy fuzz). Hoisting is all-or-nothing per side and only
//! attempted when the un-hoisted plan fails the order check.
//!
//! # Exactness (effects, traps, RNG)
//!
//! The conversion never re-evaluates, drops, duplicates, or reorders
//! anything observable:
//!
//! - The selects sit at the end of `H`, exactly where the branch dispatched;
//!   each taken arm evaluates iff its path was taken, at the same point.
//! - The single gate is the **global completion-order check**: the
//!   depth-first completion order of all kept arm trees — statement buckets
//!   then value tree, select by select in phi order — must equal the arm
//!   block's schedule. Every member (trap-capable loads, stores, `DebugLog`,
//!   RNG draws) therefore completes in the original order; RNG draw count
//!   and order are preserved exactly. This is stricter than necessary for
//!   pure members; deliberately so (one simple, checkable rule — D13).
//! - Members may reference values defined at or before `H` (already
//!   cross-block uses before conversion, S5-slotted after — no new traffic).
//! - The walk descends only through **eager** operand edges (`ShortCircuit`
//!   lhs, `Select` test); owned lazy subtrees travel with their owner,
//!   untouched. Nothing reaches through an existing lazy boundary (D11).
//!
//! # Refusals (each rejects the whole candidate)
//!
//! - Joins with zero phis (no value to produce — pure shaping is T3.9's
//!   charter), or phis missing an argument for either path.
//! - Phi arguments that are (or arm members that reference) one of the
//!   join's own phis or anything defined in a dissolved block: sibling-phi
//!   references have parallel-copy semantics a sequential select chain must
//!   not imitate, and the rest is malformed SSA (defensive).
//! - `Op::Break` in an arm (control unwinding is not a tree value), `Phi`
//!   instructions in arm blocks, stores whose *value* is consumed (a store
//!   is statement-only), arm instructions referenced from outside their
//!   tree (count != 1), shared within a tree (re-evaluation), or not
//!   reachable from any root (would be deleted).
//! - Arm schedules whose completion order cannot be reproduced (above),
//!   even after the hoist fallback.
//! - Arm blocks larger than [`MAX_ARM_INSTS`] (cost model, below).
//! - The entry block as join or arm; `H`, arms, and `J` must be distinct;
//!   join predecessors must be exactly the converted edges.
//!
//! # Select condition forms
//!
//! For a zero-valued case cond (`0`/`0.0`/`-0.0` — the frontend's `if`),
//! `Select(test, default-side, case-side)` mirrors the branch exactly
//! (Python `==` against zero on one side, `If`'s `!= 0.0` on the other,
//! NaN-truthiness included). For a non-zero case cond `c` the pass schedules
//! one shared `Equal(test, c)` (pure, total) and selects on it — the same
//! `If(Equal(t, c), …)` shape the emitter's two-way dispatcher generates, so
//! no new node cost vs the dispatched form.
//!
//! `And`/`Or` special forms (single-phi, zero-cond, content-free side only):
//! when the phi argument on the side *kept on a non-zero test* is the test
//! value itself, `Or(test, else-arm)` returns `test` exactly where a select
//! would need the test twice; symmetrically `And(test, then-arm)` when the
//! zero side's argument is the test. Both are exact by the short-circuit
//! result-value semantics (the result is the last evaluated value).
//!
//! # Cost model (D13: measured, not legacy-derived)
//!
//! Converting removes 2–3 dispatcher round trips and the per-phi
//! store/store/load materialization per execution, and moves arm nodes —
//! without duplication — from blocks into the expression. The only
//! per-candidate knob is [`MAX_ARM_INSTS`], a per-arm-block instruction cap
//! guarding against folding very large straight-line arms into one
//! statement. Measured on the corpus and pydori (PORT.md T3.8 worklog):
//! conversion is profitable on dispatch/eval/static at every cap tried; the
//! cap is kept generous as a safety valve rather than a tuned threshold.
//!
//! # Pass discipline
//!
//! Deterministic: ascending head-block scans to a fixpoint (each conversion
//! permanently clears at least two blocks, bounding the loop); `Vec`s and
//! masks only. Iterative everywhere (invariant §3.4). The MIR stays binary
//! (§3.3: `Select` is fixed 3-operand like `Lerp`; the `Execute` wrappers
//! are binary). Every mutation changes CFG shape, so a changed run calls
//! `invalidate_all`.

use crate::analysis::Analyses;
use crate::effects::op_effects;
use crate::mir::{BlockId, CaseCond, Inst, Mir, Terminator, Value};
use crate::ops::Op;
use crate::passes::Pass;
use crate::passes::dce::{load_is_total, op_is_total};

/// Per-arm-block instruction cap (cost model; module docs).
pub const MAX_ARM_INSTS: usize = 24;

/// The T3.8 pass. See the module docs.
#[derive(Debug, Default)]
pub struct IfConvert;

impl Pass for IfConvert {
    fn name(&self) -> &'static str {
        "if-convert"
    }

    fn run(&self, mir: &mut Mir, analyses: &mut Analyses) -> bool {
        if mir.blocks.is_empty() {
            return false;
        }
        let mut changed = false;
        // Fixpoint over ascending head ids: converting a head can expose the
        // same head again (it absorbed the join's terminator — nested
        // diamonds collapse outward) or a different head (an inner diamond's
        // conversion makes the outer one's arms convertible). Each
        // conversion clears >= 2 blocks for good, so the loop is bounded.
        loop {
            let mut fired = false;
            for h in 0..mir.blocks.len() {
                loop {
                    let mut step = try_convert(mir, h);
                    step |= simplify_select_branch(mir, h);
                    if !step {
                        break;
                    }
                    fired = true;
                }
            }
            changed |= fired;
            if !fired {
                break;
            }
        }
        if changed {
            analyses.invalidate_all();
        }
        changed
    }
}

/// Whether a case cond compares numerically equal to zero (Python `==`: `0`,
/// `0.0`, and `-0.0` all match; conds are never NaN).
#[allow(clippy::float_cmp)]
fn cond_is_zero(cond: CaseCond) -> bool {
    cond.value() == 0.0
}

/// One select side's spec for one phi.
#[derive(Debug, Clone)]
struct ArmSpec {
    /// The phi argument: an in-block tree root (now lazy), an external
    /// value (S5-slotted), or a constant.
    root: Value,
    /// Statement roots (count-0 forest roots, schedule order) wrapped
    /// before `root` via a binary `Execute` chain at apply time.
    bucket: Vec<Value>,
}

/// One side of the branch: the arm block to dissolve (`None` for a
/// join-edge side) and the phi-argument key (the join's predecessor on this
/// path).
struct Side {
    block: Option<BlockId>,
    phi_key: BlockId,
}

/// One side's validated plan.
#[derive(Debug, Clone)]
struct SidePlan {
    /// Parallel to the join's phi list.
    arms: Vec<ArmSpec>,
    /// Pure-total value-tree members promoted to the head (schedule order).
    hoisted: Vec<Value>,
}

impl SidePlan {
    /// Whether this side contributes nothing beyond its phi arguments (no
    /// statements, no hoisted code) — the precondition for the `And`/`Or`
    /// special form on this side.
    fn content_free(&self) -> bool {
        self.hoisted.is_empty() && self.arms.iter().all(|a| a.bucket.is_empty())
    }
}

/// Attempts one conversion with head block `h`. Returns `true` iff the MIR
/// was mutated (the caller re-tries the same head).
#[allow(clippy::too_many_lines)] // one shape-check + build sequence, kept linear for auditability
fn try_convert(mir: &mut Mir, h: BlockId) -> bool {
    // ---- Cheap terminator shape check first. ----
    let Terminator::Branch {
        test,
        cases,
        default: Some(default_bb),
    } = &mir.blocks[h].terminator
    else {
        return false;
    };
    let (test, default_bb) = (*test, *default_bb);
    let &[(cond, case_bb)] = cases.as_slice() else {
        return false;
    };
    if case_bb == default_bb || case_bb == h || default_bb == h {
        return false;
    }

    let preds = mir.predecessors();
    let scheduled = mir.scheduled_mask();
    // The test feeds the selects (or the synthesized Equal) as an eager
    // operand: it must be an ordinary value (defensive, like switch_form).
    if !(mir.is_const(test) || scheduled[test as usize]) {
        return false;
    }

    // ---- Classify the shape. ----
    // An arm block: single predecessor `h`, no phis, `Jump` terminator (the
    // jump target is returned). The entry block never qualifies.
    let arm_target = |s: BlockId| -> Option<BlockId> {
        if s == 0 || !mir.blocks[s].phis.is_empty() || preds[s].as_slice() != [h] {
            return None;
        }
        match mir.blocks[s].terminator {
            Terminator::Jump(j) => Some(j),
            _ => None,
        }
    };
    let preds_are = |j: BlockId, a: BlockId, b: BlockId| -> bool {
        preds[j].len() == 2 && preds[j].contains(&a) && preds[j].contains(&b)
    };
    let case_target = arm_target(case_bb);
    let default_target = arm_target(default_bb);
    let (join, case_side, default_side) = match (case_target, default_target) {
        // Diamond: both arms jump to the same join.
        (Some(jc), Some(jd)) if jc == jd => {
            let j = jc;
            if j == h || j == case_bb || j == default_bb || j == 0 {
                return false;
            }
            if !preds_are(j, case_bb, default_bb) {
                return false;
            }
            (
                j,
                Side {
                    block: Some(case_bb),
                    phi_key: case_bb,
                },
                Side {
                    block: Some(default_bb),
                    phi_key: default_bb,
                },
            )
        }
        // Triangle: the case side is the arm, the default edge goes straight
        // to the join.
        (Some(jc), _) if jc == default_bb => {
            let j = default_bb;
            if j == h || j == 0 || !preds_are(j, h, case_bb) {
                return false;
            }
            (
                j,
                Side {
                    block: Some(case_bb),
                    phi_key: case_bb,
                },
                Side {
                    block: None,
                    phi_key: h,
                },
            )
        }
        // Triangle, mirrored: the default side is the arm.
        (_, Some(jd)) if jd == case_bb => {
            let j = case_bb;
            if j == h || j == 0 || !preds_are(j, h, default_bb) {
                return false;
            }
            (
                j,
                Side {
                    block: None,
                    phi_key: h,
                },
                Side {
                    block: Some(default_bb),
                    phi_key: default_bb,
                },
            )
        }
        _ => return false,
    };

    // ---- The join's phis, with both paths' arguments. ----
    let phis: Vec<Value> = mir.blocks[join].phis.clone();
    if phis.is_empty() {
        return false;
    }
    let arg_of = |phi: Value, key: BlockId| -> Option<Value> {
        let Inst::Phi { args } = mir.inst(phi) else {
            return None;
        };
        if args.len() != 2 {
            return None;
        }
        args.iter().find(|&&(p, _)| p == key).map(|&(_, a)| a)
    };
    let mut case_args: Vec<Value> = Vec::with_capacity(phis.len());
    let mut default_args: Vec<Value> = Vec::with_capacity(phis.len());
    for &phi in &phis {
        let (Some(ca), Some(da)) = (
            arg_of(phi, case_side.phi_key),
            arg_of(phi, default_side.phi_key),
        ) else {
            return false;
        };
        case_args.push(ca);
        default_args.push(da);
    }

    // ---- Plan and validate each side. ----
    let counts = count_refs(mir);
    let def_block = block_of_defs(mir);
    let dissolved = [case_side.block, default_side.block, Some(join)];
    let in_dissolved = |v: Value| -> bool {
        dissolved
            .iter()
            .flatten()
            .any(|&b| def_block[v as usize] == Some(b))
    };
    // The test must not be (or be defined in) anything this conversion
    // dissolves — including the join's phis (defensive; valid SSA cannot).
    if in_dissolved(test) {
        return false;
    }
    let external_ok =
        |v: Value| -> bool { (mir.is_const(v) || scheduled[v as usize]) && !in_dissolved(v) };
    let plan_side = |side: &Side, args: &[Value]| -> Option<SidePlan> {
        match side.block {
            Some(block) if !mir.blocks[block].insts.is_empty() => plan_block_side(
                mir,
                &scheduled,
                &counts,
                &def_block,
                &in_dissolved,
                block,
                args,
            ),
            _ => {
                // Join-edge side or empty arm block: every argument must be
                // external or constant.
                if !args.iter().all(|&a| external_ok(a)) {
                    return None;
                }
                Some(SidePlan {
                    arms: args
                        .iter()
                        .map(|&a| ArmSpec {
                            root: a,
                            bucket: Vec::new(),
                        })
                        .collect(),
                    hoisted: Vec::new(),
                })
            }
        }
    };
    let (Some(case_plan), Some(default_plan)) = (
        plan_side(&case_side, &case_args),
        plan_side(&default_side, &default_args),
    ) else {
        return false;
    };

    // ---- Apply. ----
    // Hoisted pure-total trees move to the head (unconditional execution is
    // unobservable for never-trapping pure code).
    let hoists: Vec<Value> = case_plan
        .hoisted
        .iter()
        .chain(&default_plan.hoisted)
        .copied()
        .collect();
    mir.blocks[h].insts.extend(hoists);
    // Disconnect both arm blocks entirely — empty exits, like switch_form's
    // absorbed blocks, so no stale edge keeps pointing at the join. Their
    // remaining (non-hoisted) instructions become the selects' owned lazy
    // trees; nothing is deleted from the arena.
    for b in [case_side.block, default_side.block].into_iter().flatten() {
        mir.blocks[b].insts.clear();
        mir.blocks[b].terminator = Terminator::Exit;
    }
    // Branch semantics: the case edge is taken iff `test == cond` (Python
    // `==`); otherwise the default edge. Normalize to If's `!= 0.0` test.
    let zero_cond = cond_is_zero(cond);
    let sel_test = if zero_cond {
        test
    } else {
        // One shared Equal(test, cond) — the emitter's own two-way dispatch
        // shape, tag-preserving (Equal is pure and total).
        let cv = mir.push_inst(match cond {
            CaseCond::Int(c) => Inst::ConstInt(c),
            CaseCond::Float(c) => Inst::ConstFloat(c),
        });
        let eq = mir.push_inst(Inst::Op {
            op: Op::Equal,
            pure_node: true,
            args: vec![test, cv],
        });
        mir.blocks[h].insts.push(eq);
        eq
    };
    // Wrap one side's arm: Execute(stmt_1, Execute(stmt_2, ... value)).
    let wrap = |mir: &mut Mir, spec: &ArmSpec| -> Value {
        let mut w = spec.root;
        for &s in spec.bucket.iter().rev() {
            w = mir.push_inst(Inst::Op {
                op: Op::Execute,
                pure_node: false,
                args: vec![s, w],
            });
        }
        w
    };
    let single_phi = phis.len() == 1;
    let mut selects: Vec<Value> = Vec::with_capacity(phis.len());
    for i in 0..phis.len() {
        // then-side (select test != 0): the case side when the cond is
        // non-zero (it selects on Equal), the default side otherwise.
        let (then_spec, then_free, else_spec, else_free) = if zero_cond {
            (
                &default_plan.arms[i],
                default_plan.content_free(),
                &case_plan.arms[i],
                case_plan.content_free(),
            )
        } else {
            (
                &case_plan.arms[i],
                case_plan.content_free(),
                &default_plan.arms[i],
                default_plan.content_free(),
            )
        };
        let select = if single_phi && zero_cond && then_free && then_spec.root == test {
            // Or(test, else): test != 0 -> test (the then value), else the
            // rhs tree runs. Exact, and evaluates the test once.
            let rhs = wrap(mir, else_spec);
            mir.push_inst(Inst::ShortCircuit {
                op: Op::Or,
                pure_node: true,
                lhs: test,
                rhs,
            })
        } else if single_phi && zero_cond && else_free && else_spec.root == test {
            // And(test, then): test == 0 -> test (the else value).
            let rhs = wrap(mir, then_spec);
            mir.push_inst(Inst::ShortCircuit {
                op: Op::And,
                pure_node: true,
                lhs: test,
                rhs,
            })
        } else {
            let then_root = wrap(mir, then_spec);
            let else_root = wrap(mir, else_spec);
            mir.push_inst(Inst::Select {
                test: sel_test,
                then_root,
                else_root,
            })
        };
        mir.blocks[h].insts.push(select);
        selects.push(select);
    }

    // ---- Replace the phis and rewire. ----
    mir.blocks[join].phis.clear(); // the phi insts become orphans
    for (phi, select) in phis.iter().zip(&selects) {
        replace_all_uses(mir, *phi, *select);
    }

    // Merge the join into the head (its only remaining predecessor): the
    // head absorbs the join's schedule and terminator; phi args in the
    // join's successors re-key to the head (no collision: pre-merge the
    // head's only successor is the join — module docs).
    let join_insts = std::mem::take(&mut mir.blocks[join].insts);
    mir.blocks[h].insts.extend(join_insts);
    mir.blocks[h].terminator =
        std::mem::replace(&mut mir.blocks[join].terminator, Terminator::Exit);
    let mut succs: Vec<BlockId> = mir.blocks[h].terminator.successors().collect();
    succs.sort_unstable();
    succs.dedup();
    for s in succs {
        for pi in 0..mir.blocks[s].phis.len() {
            let p = mir.blocks[s].phis[pi];
            if let Inst::Phi { args } = &mut mir.insts[p as usize] {
                for (pred, _) in args.iter_mut() {
                    if *pred == join {
                        *pred = h;
                    }
                }
            }
        }
    }
    true
}

/// Plans one non-empty arm block: partitions its schedule into per-phi value
/// trees and statement-root trees, assigns statement roots to selects, and
/// validates the whole plan with the global completion-order check (module
/// docs), falling back once to pure-total tree hoisting.
#[allow(clippy::too_many_lines)] // one validation sequence, kept linear for auditability
fn plan_block_side(
    mir: &Mir,
    scheduled: &[bool],
    counts: &[u32],
    def_block: &[Option<BlockId>],
    in_dissolved: &dyn Fn(Value) -> bool,
    block: BlockId,
    args: &[Value],
) -> Option<SidePlan> {
    let insts = &mir.blocks[block].insts;
    if insts.len() > MAX_ARM_INSTS {
        return None;
    }
    let in_block = |v: Value| def_block[v as usize] == Some(block);
    let pos_of = |v: Value| insts.iter().position(|&x| x == v);

    // Roots: per-phi in-block value roots (count == 1, the phi argument) and
    // statement roots (count == 0, schedule order). External/const args are
    // positionless roots.
    let mut tree_root: Vec<Option<Value>> = Vec::with_capacity(args.len());
    for &a in args {
        if in_block(a) {
            if counts[a as usize] != 1 {
                return None; // the phi must be the only consumer
            }
            tree_root.push(Some(a));
        } else {
            if !((mir.is_const(a) || scheduled[a as usize]) && !in_dissolved(a)) {
                return None;
            }
            tree_root.push(None);
        }
    }
    let stmt_roots: Vec<Value> = insts
        .iter()
        .copied()
        .filter(|&v| counts[v as usize] == 0)
        .collect();

    // Walk every root's tree once (shared visited set): completion orders,
    // member validation, pure-totality.
    let mut visited = vec![false; insts.len()];
    let mut completions: Vec<(Value, Vec<Value>, bool)> = Vec::new(); // (root, completion, pure_total)
    let all_roots: Vec<Value> = tree_root
        .iter()
        .flatten()
        .copied()
        .chain(stmt_roots.iter().copied())
        .collect();
    for &root in &all_roots {
        let comp = walk_tree(
            mir,
            scheduled,
            counts,
            in_dissolved,
            &in_block,
            &pos_of,
            &mut visited,
            root,
        )?;
        let pure_total = comp.iter().all(|&m| member_is_pure_total(mir, m));
        completions.push((root, comp, pure_total));
    }
    // Full coverage: every arm instruction belongs to exactly one tree.
    if visited.iter().filter(|&&x| x).count() != insts.len() {
        return None;
    }
    let comp_of = |root: Value| -> &Vec<Value> {
        &completions
            .iter()
            .find(|(r, _, _)| *r == root)
            .expect("root was walked")
            .1
    };
    let is_pure_total = |root: Value| -> bool {
        completions
            .iter()
            .find(|(r, _, _)| *r == root)
            .expect("root was walked")
            .2
    };
    // Hoisting moves a tree's evaluation to the head — across every arm
    // instruction scheduled *before* its members. Crossing a memory writer
    // would let a hoisted load read a pre-store value (caught by the T3.8
    // 50k diamond-heavy fuzz), so a tree is hoistable only when all its
    // members precede the arm's first writer (deep effects: a member's owned
    // lazy trees count). Crossing readers/RNG/trap-capable members is fine:
    // the tree itself is pure, total, and RNG-free.
    let first_writer_pos = insts
        .iter()
        .position(|&v| crate::effects::inst_effects_deep(mir, v).writes_memory)
        .unwrap_or(insts.len());
    let hoistable = |root: Value| -> bool {
        is_pure_total(root)
            && comp_of(root)
                .iter()
                .all(|&m| pos_of(m).expect("member is in the block") < first_writer_pos)
    };

    // Attempt the plan; on order failure retry with every hoistable value
    // tree hoisted (all-or-nothing — module docs).
    let attempt = |hoist: bool| -> Option<SidePlan> {
        let hoisted_roots: Vec<Value> = if hoist {
            tree_root
                .iter()
                .flatten()
                .copied()
                .filter(|&r| hoistable(r))
                .collect()
        } else {
            Vec::new()
        };
        // Positioned (kept-lazy) value roots, by phi index.
        let kept: Vec<Option<Value>> = tree_root
            .iter()
            .map(|r| r.filter(|root| !hoisted_roots.contains(root)))
            .collect();
        // Statement roots attach to the first phi whose kept value root
        // completes after them; trailing ones go to the last select.
        let mut buckets: Vec<Vec<Value>> = vec![Vec::new(); args.len()];
        for &s in &stmt_roots {
            let s_pos = pos_of(s).expect("stmt root is in the block");
            let target = kept
                .iter()
                .position(|r| r.is_some_and(|root| pos_of(root).expect("in-block root") > s_pos))
                .unwrap_or(args.len() - 1);
            buckets[target].push(s);
        }
        // Global completion-order check: bucket trees then the value tree,
        // select by select in phi order, must equal the schedule minus the
        // hoisted members.
        let mut expected: Vec<Value> = Vec::with_capacity(insts.len());
        for i in 0..args.len() {
            for &s in &buckets[i] {
                expected.extend(comp_of(s));
            }
            if let Some(root) = kept[i] {
                expected.extend(comp_of(root));
            }
        }
        let hoisted_members: Vec<Value> = {
            // In schedule order: hoisted trees sorted by root position, each
            // tree in its own completion order.
            let mut roots = hoisted_roots.clone();
            roots.sort_by_key(|&r| pos_of(r).expect("in-block root"));
            roots.iter().flat_map(|&r| comp_of(r).clone()).collect()
        };
        let remaining: Vec<Value> = insts
            .iter()
            .copied()
            .filter(|v| !hoisted_members.contains(v))
            .collect();
        if expected != remaining {
            return None;
        }
        Some(SidePlan {
            arms: (0..args.len())
                .map(|i| ArmSpec {
                    root: args[i],
                    bucket: std::mem::take(&mut buckets[i]),
                })
                .collect(),
            hoisted: hoisted_members,
        })
    };
    attempt(false).or_else(|| attempt(true))
}

/// Walks one root's tree through eager operand edges, validating members and
/// returning the completion (depth-first post-) order. `None` = refusal.
#[allow(clippy::too_many_arguments)] // per-candidate facts, one validation
fn walk_tree(
    mir: &Mir,
    scheduled: &[bool],
    counts: &[u32],
    in_dissolved: &dyn Fn(Value) -> bool,
    in_block: &dyn Fn(Value) -> bool,
    pos_of: &dyn Fn(Value) -> Option<usize>,
    visited: &mut [bool],
    root: Value,
) -> Option<Vec<Value>> {
    enum W {
        Visit(Value),
        Emit(Value),
    }
    let mut order: Vec<Value> = Vec::new();
    let mut work = vec![W::Visit(root)];
    while let Some(item) = work.pop() {
        match item {
            W::Emit(v) => order.push(v),
            W::Visit(v) => {
                let pos = pos_of(v).expect("only in-block values are visited");
                if visited[pos] {
                    return None; // shared across/within trees: would re-evaluate
                }
                visited[pos] = true;
                // Non-root members are referenced exactly once (their tree
                // parent); the root's references are the caller's business
                // (phi argument: 1; statement root: 0).
                if v != root && counts[v as usize] != 1 {
                    return None;
                }
                // Eager operand edges only (module docs): lazy subtrees
                // travel with their owner, untouched.
                let mut eager: Vec<Value> = Vec::new();
                match mir.inst(v) {
                    Inst::Op { op, args, .. } => {
                        if op.control_flow() {
                            return None; // Break: unwinding is not a value
                        }
                        eager.extend_from_slice(args);
                    }
                    inst @ Inst::Load { .. } => {
                        // Eager edges = the place's dynamic components.
                        Mir::for_each_operand(inst, |o| eager.push(o));
                    }
                    inst @ Inst::Store { .. } => {
                        // A store is statement-only: legal as a statement
                        // root (count 0), never as a value member or a phi
                        // argument (its "value" is unusable).
                        if v != root || counts[v as usize] != 0 {
                            return None;
                        }
                        Mir::for_each_operand(inst, |o| eager.push(o));
                    }
                    Inst::ShortCircuit { lhs, .. } => eager.push(*lhs),
                    Inst::Select { test, .. } => eager.push(*test),
                    // Phis/consts are never scheduled in a block body.
                    Inst::Phi { .. } | Inst::ConstInt(_) | Inst::ConstFloat(_) => return None,
                }
                work.push(W::Emit(v));
                let mut kids: Vec<Value> = Vec::new();
                for o in eager {
                    if mir.is_const(o) {
                        continue;
                    }
                    if in_block(o) {
                        kids.push(o);
                        continue;
                    }
                    // An out-of-block reference: it must be an ordinary
                    // value defined at/before H (S5 slots it), never one of
                    // the join's phis or anything in a dissolved block
                    // (parallel-phi semantics / malformed SSA — module docs).
                    if !scheduled[o as usize] || in_dissolved(o) {
                        return None;
                    }
                }
                for &k in kids.iter().rev() {
                    work.push(W::Visit(k));
                }
            }
        }
    }
    Some(order)
}

/// Whether one arm-tree member is pure and total (hoistable to the head):
/// never-trapping pure ops (DCE's whitelist) and provably in-bounds
/// constant-place loads. Lazy owners (`ShortCircuit`/`Select`) and stores
/// are conservatively not hoistable.
fn member_is_pure_total(mir: &Mir, v: Value) -> bool {
    match mir.inst(v) {
        Inst::Op { op, .. } => op_effects(*op).is_pure() && op_is_total(*op),
        Inst::Load { place } => load_is_total(mir, place),
        _ => false,
    }
}

/// Exact Python `==` between a constant instruction and a case cond (the
/// interpreter's case matching; mirrors `mir::cond_matches` — no f64
/// rounding of large ints).
#[allow(clippy::float_cmp, clippy::cast_possible_truncation)]
fn const_matches_cond(k: &Inst, cond: CaseCond) -> bool {
    fn int_eq_float(i: i64, f: f64) -> bool {
        f.is_finite()
            && f == f.trunc()
            && (-9_223_372_036_854_775_808.0..9_223_372_036_854_775_808.0).contains(&f)
            && (f as i64) == i
    }
    match (k, cond) {
        (Inst::ConstInt(a), CaseCond::Int(b)) => a == &b,
        (Inst::ConstInt(a), CaseCond::Float(b)) => int_eq_float(*a, b),
        (Inst::ConstFloat(a), CaseCond::Int(b)) => int_eq_float(b, *a),
        (Inst::ConstFloat(a), CaseCond::Float(b)) => *a == b,
        _ => unreachable!("only constant instructions are compared"),
    }
}

/// Post-conversion cleanup: a branch whose test is a **select with two
/// constant arms** takes an edge that is decided by the select's own test
/// alone (each arm's cond match is known at compile time), so the branch
/// re-tests the select's test directly. Applied only when the branch is the
/// select's sole consumer: the select is then unscheduled — its arms are
/// constants, so nothing trap-capable or effectful is dropped, and the test
/// stays referenced by the branch (no orphan; the G3.2 lesson).
///
/// This is the natural second half of converting a `while a and b:` header:
/// the loop-flag phi becomes a constant-armed select consumed by the merged
/// loop branch, and this rewrite turns it back into a plain branch on the
/// flag — keeping the dispatch win of the merge without paying an `If` node
/// plus class-temp traffic per iteration (measured on the corpus/pydori —
/// PORT.md T3.8 worklog).
///
/// Dropped case targets (conds matching neither arm — unreachable edges)
/// get their `h`-keyed phi args pruned, like `switch_form`'s dropped targets.
/// If both arms land on the same target the branch becomes a `Jump`.
fn simplify_select_branch(mir: &mut Mir, h: BlockId) -> bool {
    let Terminator::Branch {
        test,
        cases,
        default,
    } = &mir.blocks[h].terminator
    else {
        return false;
    };
    let sel = *test;
    let Inst::Select {
        test: t,
        then_root,
        else_root,
    } = *mir.inst(sel)
    else {
        return false;
    };
    if !(mir.is_const(then_root) && mir.is_const(else_root)) {
        return false;
    }
    // The branch must be the select's only consumer, and the select must be
    // scheduled here (it is by construction right after a conversion;
    // defensive otherwise).
    let counts = count_refs(mir);
    if counts[sel as usize] != 1 {
        return false;
    }
    let Some(pos) = mir.blocks[h].insts.iter().position(|&v| v == sel) else {
        return false;
    };
    // The edge each arm value takes: its matching case, else the default;
    // no match and no default means the runtime exits — refused (a 2-target
    // branch cannot express a conditional exit).
    let edge_for = |k: Value| -> Option<BlockId> {
        cases
            .iter()
            .find(|&&(c, _)| const_matches_cond(mir.inst(k), c))
            .map(|&(_, target)| target)
            .or(*default)
    };
    let (Some(then_t), Some(else_t)) = (edge_for(then_root), edge_for(else_root)) else {
        return false;
    };
    let old_targets: Vec<BlockId> = mir.blocks[h].terminator.successors().collect();
    mir.blocks[h].insts.remove(pos); // unschedule the select (const arms)
    mir.blocks[h].terminator = if then_t == else_t {
        Terminator::Jump(then_t)
    } else {
        Terminator::Branch {
            test: t,
            cases: vec![(CaseCond::Int(0), else_t)],
            default: Some(then_t),
        }
    };
    // Prune h-keyed phi args in targets that lost their edge from h.
    for target in old_targets {
        if target == then_t || target == else_t {
            continue;
        }
        for pi in 0..mir.blocks[target].phis.len() {
            let p = mir.blocks[target].phis[pi];
            if let Inst::Phi { args } = &mut mir.insts[p as usize] {
                args.retain(|&(pred, _)| pred != h);
            }
        }
    }
    true
}

/// Defining block of every scheduled instruction and phi.
fn block_of_defs(mir: &Mir) -> Vec<Option<BlockId>> {
    let mut def = vec![None; mir.insts.len()];
    for (b, block) in mir.blocks.iter().enumerate() {
        for &v in block.insts.iter().chain(&block.phis) {
            def[v as usize] = Some(b);
        }
    }
    def
}

/// Redirects every reference to `from` onto `to`: operands of all arena
/// instructions (covers phi args and lazy-tree internals — lazy members are
/// arena instructions too) and terminator tests.
fn replace_all_uses(mir: &mut Mir, from: Value, to: Value) {
    for inst in &mut mir.insts {
        Mir::for_each_operand_mut(inst, |o| {
            if *o == from {
                *o = to;
            }
        });
    }
    for block in &mut mir.blocks {
        if let Terminator::Branch { test, .. } = &mut block.terminator
            && *test == from
        {
            *test = to;
        }
    }
}

/// Reference counts over everything that can reference a value: phi
/// arguments, operands of scheduled instructions (including owned lazy
/// trees), and terminator tests. Mirrors `switch_form`'s `count_refs` (each
/// pass owns its counting).
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

#[cfg(test)]
mod tests {
    // Test-builder conventions shared with the other pass modules: terse
    // names, tiny constants (casts cannot truncate).
    #![allow(clippy::many_single_char_names, clippy::float_cmp)]
    use super::*;
    use crate::mir::{BlockRef, IndexRef, Place};

    fn run_pass(mir: &mut Mir) -> bool {
        let mut analyses = Analyses::new();
        IfConvert.run(mir, &mut analyses)
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

    fn load(mir: &mut Mir, block: BlockId, place: Place) -> Value {
        sched(mir, block, Inst::Load { place })
    }

    fn add_phi(mir: &mut Mir, block: BlockId, args: Vec<(BlockId, Value)>) -> Value {
        let phi = mir.push_inst(Inst::Phi { args });
        mir.blocks[block].phis.push(phi);
        phi
    }

    fn store_out(mir: &mut Mir, block: BlockId, value: Value) {
        sched(
            mir,
            block,
            Inst::Store {
                place: concrete_place(20, 0),
                value,
            },
        );
    }

    /// The canonical single-phi diamond:
    ///
    /// ```text
    /// b0: test = Load(21[0]); branch {cond: b2(case), default: b1}
    /// b1 (then/default): a = Load(21[1])           -> b3
    /// b2 (else/case):    b = Load(21[2])           -> b3
    /// b3 (join): phi(b1: a, b2: b); Store(20[0], phi)
    /// ```
    fn diamond(cond: CaseCond) -> (Mir, Value, Value, Value, Value) {
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let b2 = mir.push_block();
        let b3 = mir.push_block();
        let test = load(&mut mir, b0, concrete_place(21, 0));
        mir.blocks[b0].terminator = Terminator::Branch {
            test,
            cases: vec![(cond, b2)],
            default: Some(b1),
        };
        let a = load(&mut mir, b1, concrete_place(21, 1));
        mir.blocks[b1].terminator = Terminator::Jump(b3);
        let b = load(&mut mir, b2, concrete_place(21, 2));
        mir.blocks[b2].terminator = Terminator::Jump(b3);
        let phi = add_phi(&mut mir, b3, vec![(b1, a), (b2, b)]);
        store_out(&mut mir, b3, phi);
        (mir, test, a, b, phi)
    }

    /// Re-keys the (single) phi argument flowing from `pred`.
    fn rekey_phi(mir: &mut Mir, join: BlockId, pred: BlockId, value: Value) {
        let phi = mir.blocks[join].phis[0];
        let Inst::Phi { args } = &mut mir.insts[phi as usize] else {
            unreachable!()
        };
        for (p, a) in args.iter_mut() {
            if *p == pred {
                *a = value;
            }
        }
    }

    /// The select scheduled in `block`, if any.
    fn find_select(mir: &Mir, block: BlockId) -> Option<Value> {
        mir.blocks[block]
            .insts
            .iter()
            .copied()
            .find(|&v| matches!(mir.inst(v), Inst::Select { .. }))
    }

    fn find_selects(mir: &Mir, block: BlockId) -> Vec<Value> {
        mir.blocks[block]
            .insts
            .iter()
            .copied()
            .filter(|&v| matches!(mir.inst(v), Inst::Select { .. }))
            .collect()
    }

    #[test]
    fn zero_cond_diamond_converts_to_select() {
        let (mut mir, test, a, b, phi) = diamond(CaseCond::Int(0));
        assert!(run_pass(&mut mir));
        // The head absorbed the join: select + the join's store, Exit.
        let sel = find_select(&mir, 0).expect("a Select in the head");
        let Inst::Select {
            test: t,
            then_root,
            else_root,
        } = *mir.inst(sel)
        else {
            unreachable!()
        };
        // Case cond 0 -> case side (b) is the else arm; default (a) is then.
        assert_eq!(t, test);
        assert_eq!(then_root, a);
        assert_eq!(else_root, b);
        // Arms are unscheduled (owned by the select); arm blocks cleared.
        let scheduled = mir.scheduled_mask();
        assert!(!scheduled[a as usize] && !scheduled[b as usize]);
        assert!(mir.blocks[1].insts.is_empty() && mir.blocks[2].insts.is_empty());
        assert_eq!(mir.blocks[1].terminator, Terminator::Exit);
        // The join was merged into the head; the phi is gone and its use
        // (the store) now consumes the select.
        assert!(mir.blocks[3].phis.is_empty() && mir.blocks[3].insts.is_empty());
        assert_eq!(mir.blocks[0].terminator, Terminator::Exit);
        let store = *mir.blocks[0].insts.last().expect("the absorbed store");
        let Inst::Store { value, .. } = mir.inst(store) else {
            panic!("the join's store must follow the select");
        };
        assert_eq!(*value, sel);
        let counts = count_refs(&mir);
        assert_eq!(counts[phi as usize], 0, "the phi is fully orphaned");
    }

    #[test]
    fn float_zero_cond_converts_too() {
        for c in [CaseCond::Float(0.0), CaseCond::Float(-0.0)] {
            let (mut mir, test, _, _, _) = diamond(c);
            assert!(run_pass(&mut mir), "cond {c:?} is zero-valued");
            let sel = find_select(&mir, 0).expect("select");
            let Inst::Select { test: t, .. } = mir.inst(sel) else {
                unreachable!()
            };
            assert_eq!(*t, test, "no Equal synthesis for a zero cond");
        }
    }

    #[test]
    fn nonzero_cond_selects_on_synthesized_equal() {
        for (cond, want_const) in [
            (CaseCond::Int(5), Inst::ConstInt(5)),
            (CaseCond::Float(2.5), Inst::ConstFloat(2.5)),
        ] {
            let (mut mir, test, a, b, _) = diamond(cond);
            assert!(run_pass(&mut mir));
            let sel = find_select(&mir, 0).expect("select");
            let Inst::Select {
                test: t,
                then_root,
                else_root,
            } = *mir.inst(sel)
            else {
                unreachable!()
            };
            // Equal(test, c) scheduled in the head; case side becomes then.
            let Inst::Op {
                op: Op::Equal,
                args,
                ..
            } = mir.inst(t)
            else {
                panic!("select test must be the synthesized Equal");
            };
            assert_eq!(args[0], test);
            assert_eq!(mir.inst(args[1]), &want_const, "tag-preserving cond");
            assert!(mir.blocks[0].insts.contains(&t), "Equal is scheduled");
            assert_eq!(then_root, b, "case arm is taken when Equal != 0");
            assert_eq!(else_root, a);
        }
    }

    #[test]
    fn triangle_with_case_arm_converts() {
        // b0: branch {0: b1(arm), default: b2(join)}; arm computes, the
        // join-edge side reuses a value defined in the head.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let b2 = mir.push_block();
        let test = load(&mut mir, b0, concrete_place(21, 0));
        let h_val = load(&mut mir, b0, concrete_place(21, 3));
        mir.blocks[b0].terminator = Terminator::Branch {
            test,
            cases: vec![(CaseCond::Int(0), b1)],
            default: Some(b2),
        };
        let arm_val = load(&mut mir, b1, concrete_place(21, 1));
        mir.blocks[b1].terminator = Terminator::Jump(b2);
        let phi = add_phi(&mut mir, b2, vec![(b0, h_val), (b1, arm_val)]);
        store_out(&mut mir, b2, phi);
        assert!(run_pass(&mut mir));
        let sel = find_select(&mir, 0).expect("select");
        let Inst::Select {
            test: t,
            then_root,
            else_root,
        } = *mir.inst(sel)
        else {
            unreachable!()
        };
        assert_eq!(t, test);
        // Case (test == 0) is the arm; default (test != 0) is the h value.
        assert_eq!(then_root, h_val);
        assert_eq!(else_root, arm_val);
        // h_val stays scheduled (External: it evaluates eagerly as before;
        // destruct_ssa S5 slots it), the arm value is unscheduled.
        let scheduled = mir.scheduled_mask();
        assert!(scheduled[h_val as usize]);
        assert!(!scheduled[arm_val as usize]);
    }

    #[test]
    fn triangle_with_default_arm_converts() {
        // Mirrored triangle: case edge goes straight to the join.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let b2 = mir.push_block();
        let test = load(&mut mir, b0, concrete_place(21, 0));
        mir.blocks[b0].terminator = Terminator::Branch {
            test,
            cases: vec![(CaseCond::Int(0), b2)],
            default: Some(b1),
        };
        let arm_val = load(&mut mir, b1, concrete_place(21, 1));
        mir.blocks[b1].terminator = Terminator::Jump(b2);
        let seven = mir.push_inst(Inst::ConstInt(7));
        let phi = add_phi(&mut mir, b2, vec![(b0, seven), (b1, arm_val)]);
        store_out(&mut mir, b2, phi);
        assert!(run_pass(&mut mir));
        let sel = find_select(&mir, 0).expect("select");
        let Inst::Select {
            then_root,
            else_root,
            ..
        } = *mir.inst(sel)
        else {
            unreachable!()
        };
        // default (test != 0) is the arm; case (test == 0) is the constant.
        assert_eq!(then_root, arm_val);
        assert_eq!(else_root, seven);
    }

    #[test]
    fn or_form_when_then_value_is_the_test() {
        // x or y: phi(default-side: test, case-side: arm) on a 0-cond.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let b2 = mir.push_block();
        let test = load(&mut mir, b0, concrete_place(21, 0));
        mir.blocks[b0].terminator = Terminator::Branch {
            test,
            cases: vec![(CaseCond::Int(0), b1)],
            default: Some(b2),
        };
        let arm_val = load(&mut mir, b1, concrete_place(21, 1));
        mir.blocks[b1].terminator = Terminator::Jump(b2);
        let phi = add_phi(&mut mir, b2, vec![(b0, test), (b1, arm_val)]);
        store_out(&mut mir, b2, phi);
        assert!(run_pass(&mut mir));
        let sc = mir.blocks[0]
            .insts
            .iter()
            .copied()
            .find(|&v| matches!(mir.inst(v), Inst::ShortCircuit { .. }))
            .expect("an Or, not a Select");
        let Inst::ShortCircuit {
            op: Op::Or,
            lhs,
            rhs,
            ..
        } = *mir.inst(sc)
        else {
            panic!("expected Or form, got {:?}", mir.inst(sc));
        };
        assert_eq!(lhs, test);
        assert_eq!(rhs, arm_val);
        assert!(find_select(&mir, 0).is_none());
    }

    #[test]
    fn and_form_when_else_value_is_the_test() {
        // x and y as a diamond: case side (test == 0) keeps the test value.
        let (mut mir, test, a, _b, _) = diamond(CaseCond::Int(0));
        // Re-key the phi: case side (b2) passes the test value itself; the
        // case arm block must be empty for the special form.
        rekey_phi(&mut mir, 3, 2, test);
        mir.blocks[2].insts.clear();
        assert!(run_pass(&mut mir));
        let sc = mir.blocks[0]
            .insts
            .iter()
            .copied()
            .find(|&v| matches!(mir.inst(v), Inst::ShortCircuit { .. }))
            .expect("an And, not a Select");
        let Inst::ShortCircuit {
            op: Op::And,
            lhs,
            rhs,
            ..
        } = *mir.inst(sc)
        else {
            panic!("expected And form, got {:?}", mir.inst(sc));
        };
        assert_eq!(lhs, test);
        assert_eq!(rhs, a, "then arm is the rhs");
    }

    #[test]
    fn multi_inst_arm_tree_converts_in_order() {
        // Arm: x = Load; y = Load; r = Add(x, y) — a 3-member tree whose
        // schedule equals DFS completion order.
        let (mut mir, _, _, _, _) = diamond(CaseCond::Int(0));
        mir.blocks[1].insts.clear();
        let x = load(&mut mir, 1, concrete_place(21, 4));
        let y = load(&mut mir, 1, concrete_place(21, 5));
        let r = sched(
            &mut mir,
            1,
            Inst::Op {
                op: Op::Add,
                pure_node: true,
                args: vec![x, y],
            },
        );
        rekey_phi(&mut mir, 3, 1, r);
        assert!(run_pass(&mut mir));
        let sel = find_select(&mir, 0).expect("select");
        let Inst::Select { then_root, .. } = *mir.inst(sel) else {
            unreachable!()
        };
        assert_eq!(then_root, r);
        let scheduled = mir.scheduled_mask();
        for v in [x, y, r] {
            assert!(!scheduled[v as usize], "tree member {v} is lazy now");
        }
    }

    #[test]
    fn rng_arm_converts_exactly_once_per_taken_path() {
        // An RNG draw in an arm stays convertible: the arm evaluates iff
        // taken, exactly like the original arm block (module docs).
        let (mut mir, _, _, _, _) = diamond(CaseCond::Int(0));
        mir.blocks[1].insts.clear();
        let lo = mir.push_inst(Inst::ConstInt(0));
        let hi = mir.push_inst(Inst::ConstInt(10));
        let draw = sched(
            &mut mir,
            1,
            Inst::Op {
                op: Op::Random,
                pure_node: false,
                args: vec![lo, hi],
            },
        );
        rekey_phi(&mut mir, 3, 1, draw);
        assert!(run_pass(&mut mir));
        let sel = find_select(&mir, 0).expect("select");
        let Inst::Select { then_root, .. } = *mir.inst(sel) else {
            unreachable!()
        };
        assert_eq!(then_root, draw);
        let scheduled = mir.scheduled_mask();
        assert!(!scheduled[draw as usize], "the draw is lazy, never hoisted");
    }

    #[test]
    fn arm_with_short_circuit_member_keeps_lazy_subtree() {
        // The arm root is And(load, lazy load): the owned lazy rhs travels
        // with its owner, never inspected (D11 boundary).
        let (mut mir, _, _, _, _) = diamond(CaseCond::Int(0));
        mir.blocks[1].insts.clear();
        let lhs = load(&mut mir, 1, concrete_place(21, 4));
        let lazy = mir.push_inst(Inst::Load {
            place: concrete_place(21, 5),
        });
        let sc = sched(
            &mut mir,
            1,
            Inst::ShortCircuit {
                op: Op::And,
                pure_node: true,
                lhs,
                rhs: lazy,
            },
        );
        rekey_phi(&mut mir, 3, 1, sc);
        assert!(run_pass(&mut mir));
        let sel = find_select(&mir, 0).expect("select");
        let Inst::Select { then_root, .. } = *mir.inst(sel) else {
            unreachable!()
        };
        assert_eq!(then_root, sc);
        let Inst::ShortCircuit { rhs, .. } = mir.inst(sc) else {
            unreachable!()
        };
        assert_eq!(*rhs, lazy, "the lazy subtree is untouched");
    }

    #[test]
    fn store_in_arm_wraps_in_execute() {
        // Arm: store(22[0], c); v = Load(21[1]) — the statement wraps before
        // the value via Execute, evaluated iff the arm is taken.
        let (mut mir, _, _, _, _) = diamond(CaseCond::Int(0));
        mir.blocks[1].insts.clear();
        let c = mir.push_inst(Inst::ConstInt(9));
        let st = sched(
            &mut mir,
            1,
            Inst::Store {
                place: concrete_place(22, 0),
                value: c,
            },
        );
        let v = load(&mut mir, 1, concrete_place(21, 1));
        rekey_phi(&mut mir, 3, 1, v);
        assert!(run_pass(&mut mir));
        let sel = find_select(&mir, 0).expect("select");
        let Inst::Select { then_root, .. } = *mir.inst(sel) else {
            unreachable!()
        };
        let Inst::Op {
            op: Op::Execute,
            args,
            ..
        } = mir.inst(then_root)
        else {
            panic!("then arm must be Execute(store, value)");
        };
        assert_eq!(args.as_slice(), &[st, v]);
        let scheduled = mir.scheduled_mask();
        assert!(!scheduled[st as usize], "the store moved into the lazy arm");
    }

    #[test]
    fn bare_statement_load_wraps_too() {
        // A bare expression statement (trap-capable load, value unused) is a
        // statement root: preserved via Execute, never deleted.
        let (mut mir, _, a, _, _) = diamond(CaseCond::Int(0));
        let extra = mir.push_inst(Inst::Load {
            place: Place {
                block: BlockRef::Concrete(21),
                index: IndexRef::Const(70000), // would trap at runtime
                offset: 0,
            },
        });
        mir.blocks[1].insts.push(extra);
        // Schedule order is [a, extra]: the trailing statement attaches to
        // the (only) select; the value tree `a` is pure-total, so the hoist
        // fallback moves it to the head and the order works out.
        assert!(run_pass(&mut mir));
        let sel = find_select(&mir, 0).expect("select");
        let Inst::Select { then_root, .. } = *mir.inst(sel) else {
            unreachable!()
        };
        let Inst::Op {
            op: Op::Execute,
            args,
            ..
        } = mir.inst(then_root)
        else {
            panic!("then arm must be Execute(extra, a)");
        };
        assert_eq!(args.as_slice(), &[extra, a]);
        assert!(
            mir.blocks[0].insts.contains(&a),
            "the pure-total value tree hoisted to the head"
        );
    }

    #[test]
    fn multi_phi_diamond_converts_per_phi() {
        // The pydori while-cond shape: two phis, one arm with a store and a
        // value tree, the other side plain values.
        let (mut mir, test, a, b, _) = diamond(CaseCond::Int(0));
        // b1 (then arm): store(22[0], c) before its value `a`. Add a second
        // phi with constant args.
        let c = mir.push_inst(Inst::ConstInt(9));
        let st = mir.push_inst(Inst::Store {
            place: concrete_place(22, 0),
            value: c,
        });
        mir.blocks[1].insts.insert(0, st);
        let one = mir.push_inst(Inst::ConstInt(1));
        let two = mir.push_inst(Inst::ConstInt(2));
        let phi2 = add_phi(&mut mir, 3, vec![(1, one), (2, two)]);
        store_out(&mut mir, 3, phi2);
        assert!(run_pass(&mut mir));
        let selects = find_selects(&mir, 0);
        assert_eq!(selects.len(), 2, "one select per phi, in phi order");
        // Select 1 (first phi): then = Execute(store, a), else = b.
        let Inst::Select {
            test: t1,
            then_root,
            else_root,
        } = *mir.inst(selects[0])
        else {
            unreachable!()
        };
        assert_eq!(t1, test);
        let Inst::Op {
            op: Op::Execute,
            args,
            ..
        } = mir.inst(then_root)
        else {
            panic!("first select's then arm wraps the store");
        };
        assert_eq!(args.as_slice(), &[st, a]);
        assert_eq!(else_root, b);
        // Select 2 (second phi): constants on both sides, same shared test.
        let Inst::Select {
            test: t2,
            then_root: tr2,
            else_root: er2,
        } = *mir.inst(selects[1])
        else {
            unreachable!()
        };
        assert_eq!(t2, test, "the test is shared, never re-evaluated");
        assert_eq!((tr2, er2), (one, two));
    }

    #[test]
    fn hot_latch_load_hoists_before_store() {
        // The pydori loop-latch shape: v = load(..); store(..). The value
        // tree completes before the store, so keeping it lazy would reorder;
        // it is pure-total and hoists to the head instead.
        let (mut mir, _, _, _, _) = diamond(CaseCond::Int(0));
        mir.blocks[1].insts.clear();
        let v = load(&mut mir, 1, concrete_place(21, 4));
        let c = mir.push_inst(Inst::ConstInt(9));
        let st = sched(
            &mut mir,
            1,
            Inst::Store {
                place: concrete_place(22, 0),
                value: c,
            },
        );
        rekey_phi(&mut mir, 3, 1, v);
        assert!(run_pass(&mut mir));
        assert!(
            mir.blocks[0].insts.contains(&v),
            "the load hoisted to the head (pure-total speculation)"
        );
        let sel = find_select(&mir, 0).expect("select");
        let Inst::Select { then_root, .. } = *mir.inst(sel) else {
            unreachable!()
        };
        let Inst::Op {
            op: Op::Execute,
            args,
            ..
        } = mir.inst(then_root)
        else {
            panic!("then arm must be Execute(store, hoisted-v)");
        };
        assert_eq!(args.as_slice(), &[st, v]);
    }

    #[test]
    fn out_of_order_pure_arm_hoists() {
        // Arm schedule [y, x, Add(x, y)]: completion order of the tree is
        // [x, y, add] — keeping it lazy would swap the loads. Both loads are
        // total, so the whole tree hoists instead.
        let (mut mir, _, _, _, _) = diamond(CaseCond::Int(0));
        mir.blocks[1].insts.clear();
        let y = load(&mut mir, 1, concrete_place(21, 5));
        let x = load(&mut mir, 1, concrete_place(21, 4));
        let r = sched(
            &mut mir,
            1,
            Inst::Op {
                op: Op::Add,
                pure_node: true,
                args: vec![x, y],
            },
        );
        rekey_phi(&mut mir, 3, 1, r);
        assert!(run_pass(&mut mir));
        for v in [x, y, r] {
            assert!(
                mir.blocks[0].insts.contains(&v),
                "member {v} hoisted in schedule order"
            );
        }
    }

    #[test]
    fn while_head_select_branch_simplifies_to_plain_branch() {
        // The `while a and b` shape: a 2-phi diamond whose join branches on
        // the boolean flag phi. After conversion the flag select has two
        // constant arms and the merged branch is its only consumer — the
        // branch re-tests the select's test and the select unschedules.
        let (mut mir, test, a, _b, _) = diamond(CaseCond::Int(0));
        // First phi: boolean flag (then: 1, else: 0); second phi: a value.
        let one = mir.push_inst(Inst::ConstInt(1));
        let zero = mir.push_inst(Inst::ConstInt(0));
        rekey_phi(&mut mir, 3, 1, one);
        rekey_phi(&mut mir, 3, 2, zero);
        mir.blocks[1].insts.clear();
        mir.blocks[2].insts.clear();
        let flag_phi = mir.blocks[3].phis[0];
        let val_phi = add_phi(&mut mir, 3, vec![(1, a), (2, test)]);
        mir.blocks[1].insts.push(a); // a stays the value arm's tree
        // The join branches on the flag phi (the loop continue test).
        let b4 = mir.push_block();
        let b5 = mir.push_block();
        // Replace the join's store with a branch on the flag.
        mir.blocks[3].insts.clear();
        mir.blocks[3].terminator = Terminator::Branch {
            test: flag_phi,
            cases: vec![(CaseCond::Int(0), b5)],
            default: Some(b4),
        };
        store_out(&mut mir, b4, val_phi);
        mir.blocks[b4].terminator = Terminator::Exit;
        mir.blocks[b5].terminator = Terminator::Exit;
        assert!(run_pass(&mut mir));
        // The head's terminator branches directly on the original test; no
        // select for the flag survives (the value select remains).
        let Terminator::Branch {
            test: t,
            cases,
            default,
        } = &mir.blocks[0].terminator
        else {
            panic!("the head must still branch");
        };
        assert_eq!(*t, test, "the branch re-tests the select's own test");
        assert_eq!(cases.as_slice(), &[(CaseCond::Int(0), b5)]);
        assert_eq!(*default, Some(b4));
        assert_eq!(
            find_selects(&mir, 0).len(),
            1,
            "only the value select survives; the flag select unscheduled"
        );
    }

    // ---------------- Refusals ----------------

    #[test]
    fn hoist_across_earlier_store_is_refused() {
        // Regression for the 50k diamond-heavy fuzz find: arm
        // [store(22[0]), v = load(22[0]), store(23[0])] — the value tree
        // reads a cell an *earlier* arm store wrote, so hoisting it to the
        // head would read the pre-store value. No lazy order exists either
        // (v completes before the second store): refused outright.
        let (mut mir, _, _, _, _) = diamond(CaseCond::Int(0));
        mir.blocks[1].insts.clear();
        let c = mir.push_inst(Inst::ConstInt(9));
        sched(
            &mut mir,
            1,
            Inst::Store {
                place: concrete_place(22, 0),
                value: c,
            },
        );
        let v = load(&mut mir, 1, concrete_place(22, 0));
        let c2 = mir.push_inst(Inst::ConstInt(8));
        sched(
            &mut mir,
            1,
            Inst::Store {
                place: concrete_place(23, 0),
                value: c2,
            },
        );
        rekey_phi(&mut mir, 3, 1, v);
        assert!(!run_pass(&mut mir));
    }

    #[test]
    fn out_of_order_trapping_arm_is_refused() {
        // Same shape but with trap-capable members (Sin can raise): no lazy
        // order, no hoist — refused.
        let (mut mir, _, _, _, _) = diamond(CaseCond::Int(0));
        mir.blocks[1].insts.clear();
        let ly = load(&mut mir, 1, concrete_place(21, 5));
        let y = sched(
            &mut mir,
            1,
            Inst::Op {
                op: Op::Sin,
                pure_node: true,
                args: vec![ly],
            },
        );
        let lx = load(&mut mir, 1, concrete_place(21, 4));
        let x = sched(
            &mut mir,
            1,
            Inst::Op {
                op: Op::Sin,
                pure_node: true,
                args: vec![lx],
            },
        );
        let r = sched(
            &mut mir,
            1,
            Inst::Op {
                op: Op::Add,
                pure_node: true,
                args: vec![x, y],
            },
        );
        rekey_phi(&mut mir, 3, 1, r);
        assert!(!run_pass(&mut mir));
    }

    #[test]
    fn trailing_statement_after_effectful_value_is_refused() {
        // Arm [draw = Random(..), store]: the RNG value completes before the
        // store and is not hoistable — converting would reorder the draw
        // after the store.
        let (mut mir, _, _, _, _) = diamond(CaseCond::Int(0));
        mir.blocks[1].insts.clear();
        let lo = mir.push_inst(Inst::ConstInt(0));
        let hi = mir.push_inst(Inst::ConstInt(10));
        let draw = sched(
            &mut mir,
            1,
            Inst::Op {
                op: Op::Random,
                pure_node: false,
                args: vec![lo, hi],
            },
        );
        let c = mir.push_inst(Inst::ConstInt(9));
        sched(
            &mut mir,
            1,
            Inst::Store {
                place: concrete_place(22, 0),
                value: c,
            },
        );
        rekey_phi(&mut mir, 3, 1, draw);
        assert!(!run_pass(&mut mir));
    }

    #[test]
    fn zero_phi_join_is_refused() {
        let (mut mir, _, _, _, _) = diamond(CaseCond::Int(0));
        let phi = mir.blocks[3].phis[0];
        mir.blocks[3].phis.clear();
        // Keep the store's operand valid: store a constant instead.
        let c = mir.push_inst(Inst::ConstInt(1));
        replace_all_uses(&mut mir, phi, c);
        // The arms are now dead loads, but that is DCE's business, not ours.
        assert!(!run_pass(&mut mir));
    }

    #[test]
    fn shared_in_block_arg_across_phis_is_refused() {
        // Two phis consuming the same in-block value: count == 2 on the
        // would-be tree root.
        let (mut mir, _, a, b, _) = diamond(CaseCond::Int(0));
        let phi2 = add_phi(&mut mir, 3, vec![(1, a), (2, b)]);
        store_out(&mut mir, 3, phi2);
        assert!(!run_pass(&mut mir));
        assert_eq!(mir.blocks[3].phis.len(), 2);
    }

    #[test]
    fn sibling_phi_argument_is_refused() {
        // phi2's argument is phi1 itself: parallel-phi read semantics a
        // sequential select chain must not imitate.
        let (mut mir, _, _, _, phi1) = diamond(CaseCond::Int(0));
        let seven = mir.push_inst(Inst::ConstInt(7));
        let phi2 = add_phi(&mut mir, 3, vec![(1, phi1), (2, seven)]);
        store_out(&mut mir, 3, phi2);
        assert!(!run_pass(&mut mir));
    }

    #[test]
    fn arm_value_with_outside_use_is_refused() {
        let (mut mir, _, a, _, _) = diamond(CaseCond::Int(0));
        // The then-arm value is also consumed by the join (count == 2).
        store_out(&mut mir, 3, a);
        assert!(!run_pass(&mut mir));
    }

    #[test]
    fn store_as_phi_argument_is_refused() {
        // A store's "value" is unusable; a phi consuming it is malformed.
        let (mut mir, _, _, _, _) = diamond(CaseCond::Int(0));
        mir.blocks[1].insts.clear();
        let c = mir.push_inst(Inst::ConstInt(9));
        let st = sched(
            &mut mir,
            1,
            Inst::Store {
                place: concrete_place(22, 0),
                value: c,
            },
        );
        rekey_phi(&mut mir, 3, 1, st);
        assert!(!run_pass(&mut mir));
    }

    #[test]
    fn scheduled_lazy_rhs_in_arm_is_refused() {
        // A ShortCircuit member whose rhs is a *scheduled* arm instruction:
        // the eager-edge walk never reaches it (lazy boundary), so the arm
        // is not fully consumed — refused, not silently mis-ordered.
        let (mut mir, _, _, _, _) = diamond(CaseCond::Int(0));
        mir.blocks[1].insts.clear();
        let lhs = load(&mut mir, 1, concrete_place(21, 4));
        let rhs = load(&mut mir, 1, concrete_place(21, 5)); // scheduled!
        let sc = sched(
            &mut mir,
            1,
            Inst::ShortCircuit {
                op: Op::And,
                pure_node: true,
                lhs,
                rhs,
            },
        );
        rekey_phi(&mut mir, 3, 1, sc);
        assert!(!run_pass(&mut mir));
    }

    #[test]
    fn break_in_arm_is_refused() {
        let (mut mir, _, _, _, _) = diamond(CaseCond::Int(0));
        mir.blocks[1].insts.clear();
        let one = mir.push_inst(Inst::ConstInt(1));
        let seven = mir.push_inst(Inst::ConstInt(7));
        let brk = sched(
            &mut mir,
            1,
            Inst::Op {
                op: Op::Break,
                pure_node: false,
                args: vec![one, seven],
            },
        );
        rekey_phi(&mut mir, 3, 1, brk);
        assert!(!run_pass(&mut mir));
    }

    #[test]
    fn oversized_arm_is_refused_at_the_cap() {
        let build = |n: usize| {
            let (mut mir, _, _, _, _) = diamond(CaseCond::Int(0));
            mir.blocks[1].insts.clear();
            let mut v = load(&mut mir, 1, concrete_place(21, 4));
            for _ in 0..n - 1 {
                v = sched(
                    &mut mir,
                    1,
                    Inst::Op {
                        op: Op::Negate,
                        pure_node: true,
                        args: vec![v],
                    },
                );
            }
            rekey_phi(&mut mir, 3, 1, v);
            mir
        };
        let mut at_cap = build(MAX_ARM_INSTS);
        assert!(run_pass(&mut at_cap), "exactly at the cap converts");
        let mut over_cap = build(MAX_ARM_INSTS + 1);
        assert!(!run_pass(&mut over_cap), "one over the cap is refused");
    }

    #[test]
    fn extra_join_predecessor_is_refused() {
        let (mut mir, _, _, _, _) = diamond(CaseCond::Int(0));
        // A third block also jumps into the join.
        let b4 = mir.push_block();
        mir.blocks[b4].terminator = Terminator::Jump(3);
        assert!(!run_pass(&mut mir));
    }

    #[test]
    fn three_way_branches_are_refused() {
        let (mut mir, _, _, _, _) = diamond(CaseCond::Int(0));
        let b4 = mir.push_block();
        mir.blocks[b4].terminator = Terminator::Jump(3);
        let Terminator::Branch { cases, .. } = &mut mir.blocks[0].terminator else {
            unreachable!()
        };
        cases.push((CaseCond::Int(1), b4));
        assert!(!run_pass(&mut mir));
    }

    #[test]
    fn entry_block_join_is_refused() {
        // Block 0 as join: shape-wise a diamond, but the entry must keep its
        // identity (an entry phi reads undefined state pre-loop).
        let mut mir = Mir::new();
        let b0 = mir.push_block(); // join AND entry
        let b1 = mir.push_block(); // head
        let b2 = mir.push_block();
        let b3 = mir.push_block();
        let test = load(&mut mir, b1, concrete_place(21, 0));
        mir.blocks[b1].terminator = Terminator::Branch {
            test,
            cases: vec![(CaseCond::Int(0), b3)],
            default: Some(b2),
        };
        let a = load(&mut mir, b2, concrete_place(21, 1));
        mir.blocks[b2].terminator = Terminator::Jump(b0);
        let b = load(&mut mir, b3, concrete_place(21, 2));
        mir.blocks[b3].terminator = Terminator::Jump(b0);
        let phi = add_phi(&mut mir, b0, vec![(b2, a), (b3, b)]);
        store_out(&mut mir, b0, phi);
        mir.blocks[b0].terminator = Terminator::Jump(b1);
        assert!(!run_pass(&mut mir));
    }

    #[test]
    fn nested_diamonds_collapse_to_one_block() {
        // Outer diamond whose then-arm is itself a diamond: the fixpoint
        // converts inner first (its join merges into its head, making the
        // head a plain Jump arm), then outer.
        let mut mir = Mir::new();
        let b0 = mir.push_block(); // outer head
        let b1 = mir.push_block(); // inner head (outer then-arm)
        let b2 = mir.push_block(); // outer else-arm
        let b3 = mir.push_block(); // inner then-arm
        let b4 = mir.push_block(); // inner else-arm
        let b5 = mir.push_block(); // inner join
        let b6 = mir.push_block(); // outer join
        let t0 = load(&mut mir, b0, concrete_place(21, 0));
        mir.blocks[b0].terminator = Terminator::Branch {
            test: t0,
            cases: vec![(CaseCond::Int(0), b2)],
            default: Some(b1),
        };
        let t1 = load(&mut mir, b1, concrete_place(21, 1));
        mir.blocks[b1].terminator = Terminator::Branch {
            test: t1,
            cases: vec![(CaseCond::Int(0), b4)],
            default: Some(b3),
        };
        let v3 = load(&mut mir, b3, concrete_place(21, 2));
        mir.blocks[b3].terminator = Terminator::Jump(b5);
        let v4 = load(&mut mir, b4, concrete_place(21, 3));
        mir.blocks[b4].terminator = Terminator::Jump(b5);
        let inner_phi = add_phi(&mut mir, b5, vec![(b3, v3), (b4, v4)]);
        mir.blocks[b5].terminator = Terminator::Jump(b6);
        let v2 = load(&mut mir, b2, concrete_place(21, 4));
        mir.blocks[b2].terminator = Terminator::Jump(b6);
        let outer_phi = add_phi(&mut mir, b6, vec![(b5, inner_phi), (b2, v2)]);
        store_out(&mut mir, b6, outer_phi);
        assert!(run_pass(&mut mir));
        // Everything reachable collapsed into the entry.
        assert_eq!(mir.reverse_postorder(), vec![0]);
        let outer_sel = find_select(&mir, 0).expect("outer select");
        let Inst::Select { then_root, .. } = *mir.inst(outer_sel) else {
            unreachable!()
        };
        assert!(
            matches!(mir.inst(then_root), Inst::Select { .. }),
            "the inner select lives inside the outer arm tree"
        );
    }

    #[test]
    fn pipeline_changed_flag_contract_holds() {
        // Through the Pipeline (debug builds re-fingerprint after each pass:
        // a lying changed flag would panic). True on a converting MIR, false
        // on the already-converted result.
        use crate::passes::Pipeline;
        let (mut mir, _, _, _, _) = diamond(CaseCond::Int(0));
        let mut analyses = Analyses::new();
        let pipeline = Pipeline::new(vec![Box::new(IfConvert) as Box<dyn Pass>]);
        assert!(pipeline.run(&mut mir, &mut analyses));
        let mut analyses = Analyses::new();
        let pipeline = Pipeline::new(vec![Box::new(IfConvert) as Box<dyn Pass>]);
        assert!(!pipeline.run(&mut mir, &mut analyses), "fixpoint reached");
    }

    #[test]
    fn standard_level_includes_if_convert_after_w3() {
        use crate::passes::passes_for_level;
        use crate::pipeline::Level;
        let names: Vec<&str> = passes_for_level(Level::Standard)
            .iter()
            .map(|p| p.name())
            .collect();
        let fast: Vec<&str> = passes_for_level(Level::Fast)
            .iter()
            .map(|p| p.name())
            .collect();
        assert!(names.contains(&"if-convert"));
        assert!(!fast.contains(&"if-convert"));
        let licm_pos = names.iter().position(|&n| n == "licm").expect("licm");
        let ic_pos = names
            .iter()
            .position(|&n| n == "if-convert")
            .expect("if-convert");
        assert!(ic_pos > licm_pos, "W4 entries follow W3");
    }

    // ---------------- End to end ----------------

    /// Frontend CFG: `out[0] = (in[0] != 0) ? in[1] + in[2] : in[3] * 2`
    /// through a temp `x` (the diamond's phi after `Mem2Reg`).
    #[allow(clippy::too_many_lines)] // one literal frontend CFG
    fn e2e_cfg() -> crate::cfg::Cfg {
        use crate::cfg::{
            BasicBlock, BlockValue, Cfg, Edge, EdgeCond, IndexValue, Node, Place as CfgPlace,
            TempBlockDef,
        };
        let mut cfg = Cfg::default();
        cfg.strings.push("x".to_owned());
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
        let x_p = place(&mut cfg, BlockValue::Temp(0), 0);
        let in0 = place(&mut cfg, BlockValue::Int(21), 0);
        let in1 = place(&mut cfg, BlockValue::Int(21), 1);
        let in2 = place(&mut cfg, BlockValue::Int(21), 2);
        let in3 = place(&mut cfg, BlockValue::Int(21), 3);
        let out_p = place(&mut cfg, BlockValue::Int(20), 0);
        // b0: branch on in[0] -> {0: b2, default: b1}.
        let test = node(&mut cfg, Node::Get(in0));
        cfg.blocks.push(BasicBlock {
            statements: vec![],
            test,
            outgoing: vec![
                Edge {
                    cond: EdgeCond::Int(0),
                    target: 2,
                },
                Edge {
                    cond: EdgeCond::None,
                    target: 1,
                },
            ],
        });
        // b1 (then): x = in[1] + in[2].
        let g1 = node(&mut cfg, Node::Get(in1));
        let g2 = node(&mut cfg, Node::Get(in2));
        let add = node(
            &mut cfg,
            Node::PureInstr {
                op: Op::Add,
                args: vec![g1, g2],
            },
        );
        let s_then = node(
            &mut cfg,
            Node::Set {
                place: x_p,
                value: add,
            },
        );
        let zt1 = node(&mut cfg, Node::ConstInt(0));
        cfg.blocks.push(BasicBlock {
            statements: vec![s_then],
            test: zt1,
            outgoing: vec![Edge {
                cond: EdgeCond::None,
                target: 3,
            }],
        });
        // b2 (else): x = in[3] * 2.
        let g3 = node(&mut cfg, Node::Get(in3));
        let two = node(&mut cfg, Node::ConstInt(2));
        let mul = node(
            &mut cfg,
            Node::PureInstr {
                op: Op::Multiply,
                args: vec![g3, two],
            },
        );
        let s_else = node(
            &mut cfg,
            Node::Set {
                place: x_p,
                value: mul,
            },
        );
        let zt2 = node(&mut cfg, Node::ConstInt(0));
        cfg.blocks.push(BasicBlock {
            statements: vec![s_else],
            test: zt2,
            outgoing: vec![Edge {
                cond: EdgeCond::None,
                target: 3,
            }],
        });
        // b3 (join): out[0] = x.
        let gx = node(&mut cfg, Node::Get(x_p));
        let s_out = node(
            &mut cfg,
            Node::Set {
                place: out_p,
                value: gx,
            },
        );
        let zt3 = node(&mut cfg, Node::ConstInt(0));
        cfg.blocks.push(BasicBlock {
            statements: vec![s_out],
            test: zt3,
            outgoing: vec![],
        });
        cfg
    }

    #[test]
    fn end_to_end_diamond_reduces_dispatch_and_eval() {
        use crate::interpret::Interpreter;
        use crate::passes::Pipeline;
        use crate::passes::mem2reg::Mem2Reg;
        use crate::pipeline::compile_cfg_with_pipeline;
        let cfg = e2e_cfg();
        let run = |with_ic: bool, test_val: f64| {
            let mut passes: Vec<Box<dyn Pass>> = vec![Box::new(Mem2Reg)];
            if with_ic {
                passes.push(Box::new(IfConvert));
            }
            let nodes = compile_cfg_with_pipeline(&cfg, &Pipeline::new(passes)).unwrap();
            let mut interp = Interpreter::new(0);
            interp.set_block(21, vec![test_val, 10.0, 20.0, 7.0]);
            interp.set_block(20, vec![0.0]);
            interp.run(&nodes).unwrap();
            (
                interp.block(20).unwrap()[0],
                interp.eval_count(),
                interp.dispatch_count(),
            )
        };
        for (test_val, want) in [(1.0, 30.0), (0.0, 14.0), (f64::NAN, 30.0)] {
            let (out_without, evals_without, disp_without) = run(false, test_val);
            let (out_with, evals_with, disp_with) = run(true, test_val);
            assert_eq!(out_without, want, "baseline behavior");
            assert_eq!(out_with, want, "converted behavior");
            assert!(
                disp_with < disp_without,
                "dispatches must drop: {disp_with} >= {disp_without}"
            );
            assert!(
                evals_with < evals_without,
                "evals must drop: {evals_with} >= {evals_without}"
            );
            println!(
                "test={test_val}: eval {evals_without} -> {evals_with}, \
                 dispatch {disp_without} -> {disp_with}"
            );
        }
    }

    #[test]
    #[allow(clippy::too_many_lines)] // one literal loop CFG, kept linear
    fn converted_lazy_arm_store_does_not_clobber_loop_state() {
        // End-to-end pin for the W4 composition-fuzz miscompile (shape-heavy
        // profile, persisted seed in tests/proptest-regressions/fuzz_shape.txt):
        // a counter loop whose body is a triangle; the arm stores into a
        // dynamically-indexed — hence unpromotable — array temp. Conversion
        // moves those stores into the select's lazy arm, where the allocator
        // must model them as may-defs: pre-fix they counted only as uses, the
        // array built no interference, was overlaid on the loop's class-temp
        // slots, and the lazy store clobbered the loop counter at runtime
        // (observed as a wrong DebugLog count: 4 -> 2 on the fuzz seed).
        //
        // b0: jump b1
        // b1: c = phi[(b0, 0), (b4, cp)]; t = Less(c, 2); branch t {0: b5} b2
        // b2: lg = DebugLog(0); f = Floor(lg); branch f {0: b3} b4
        // b3 (arm): d = Load(21[1]); arr[d] <- 100; arr[1] <- 100;
        //           arr[2] <- 100; arr[3] <- 100; jump b4
        // b4 (join): x = phi[(b2, 7), (b3, 1)]; Store(20[0], x);
        //            cp = Add(c, 1); jump b1
        // b5: exit
        fn run_mir(mut mir: Mir) -> (Vec<f64>, f64) {
            crate::ssa::destruct_ssa(&mut mir).unwrap();
            let alloc = crate::alloc::allocate_temps(&mir).unwrap();
            let lowered = crate::lower::lower_mir(&mir, &alloc).unwrap();
            let nodes = crate::emit::cfg_to_engine_nodes(&lowered).unwrap();
            let mut interp = crate::interpret::Interpreter::new(0);
            interp.set_block(21, vec![0.0, 0.0]);
            interp.set_block(20, vec![0.0]);
            interp.run(&nodes).unwrap();
            (interp.log().to_vec(), interp.block(20).unwrap()[0])
        }
        let mut mir = Mir::new();
        let arr = mir.push_temp("arr", 4);
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let b2 = mir.push_block();
        let b3 = mir.push_block();
        let b4 = mir.push_block();
        let b5 = mir.push_block();
        mir.blocks[b0].terminator = Terminator::Jump(b1);
        let zero = mir.push_inst(Inst::ConstInt(0));
        let c_phi = add_phi(&mut mir, b1, vec![(b0, zero)]); // cp keyed below
        let two = mir.push_inst(Inst::ConstInt(2));
        let t = sched(
            &mut mir,
            b1,
            Inst::Op {
                op: Op::Less,
                pure_node: true,
                args: vec![c_phi, two],
            },
        );
        mir.blocks[b1].terminator = Terminator::Branch {
            test: t,
            cases: vec![(CaseCond::Int(0), b5)],
            default: Some(b2),
        };
        let lg = sched(
            &mut mir,
            b2,
            Inst::Op {
                op: Op::DebugLog,
                pure_node: false,
                args: vec![zero],
            },
        );
        let f = sched(
            &mut mir,
            b2,
            Inst::Op {
                op: Op::Floor,
                pure_node: true,
                args: vec![lg],
            },
        );
        mir.blocks[b2].terminator = Terminator::Branch {
            test: f,
            cases: vec![(CaseCond::Int(0), b3)],
            default: Some(b4),
        };
        let hundred = mir.push_inst(Inst::ConstInt(100));
        let d = load(&mut mir, b3, concrete_place(21, 1));
        sched(
            &mut mir,
            b3,
            Inst::Store {
                place: Place {
                    block: BlockRef::Temp(arr),
                    index: IndexRef::Value(d),
                    offset: 0,
                },
                value: hundred,
            },
        );
        for i in 1..4 {
            sched(
                &mut mir,
                b3,
                Inst::Store {
                    place: Place {
                        block: BlockRef::Temp(arr),
                        index: IndexRef::Const(i),
                        offset: 0,
                    },
                    value: hundred,
                },
            );
        }
        mir.blocks[b3].terminator = Terminator::Jump(b4);
        let seven = mir.push_inst(Inst::ConstInt(7));
        let one = mir.push_inst(Inst::ConstInt(1));
        let x_phi = add_phi(&mut mir, b4, vec![(b2, seven), (b3, one)]);
        store_out(&mut mir, b4, x_phi);
        let cp = sched(
            &mut mir,
            b4,
            Inst::Op {
                op: Op::Add,
                pure_node: true,
                args: vec![c_phi, one],
            },
        );
        mir.blocks[b4].terminator = Terminator::Jump(b1);
        mir.blocks[b5].terminator = Terminator::Exit;
        let Inst::Phi { args } = &mut mir.insts[c_phi as usize] else {
            unreachable!()
        };
        args.push((b4, cp));
        let baseline = mir.clone();
        assert!(run_pass(&mut mir), "the triangle must convert");
        assert!(
            matches!(mir.blocks[b2].terminator, Terminator::Jump(j) if j == b1),
            "the join merged into the body head"
        );
        let (base_log, base_out) = run_mir(baseline);
        let (conv_log, conv_out) = run_mir(mir);
        assert_eq!(base_log, vec![0.0, 0.0], "two iterations, one log each");
        assert_eq!(
            conv_log, base_log,
            "the lazy arm stores must not clobber the loop counter"
        );
        assert_eq!(conv_out, base_out);
    }

    #[test]
    fn end_to_end_matches_minimal_differentially() {
        use crate::diff::{DiffConfig, DiffOutcome, diff_with};
        use crate::passes::Pipeline;
        use crate::passes::mem2reg::Mem2Reg;
        use crate::pipeline::{Level, compile_cfg, compile_cfg_with_pipeline};
        let cfg = e2e_cfg();
        for seed in [1u64, 2, 3] {
            let config = DiffConfig {
                memory_seed: seed,
                rng_seed: seed ^ 0xD1FF,
                eval_budget: 200_000,
            };
            let outcome = diff_with(
                &cfg,
                |c| compile_cfg(c, Level::Minimal),
                |c| {
                    compile_cfg_with_pipeline(
                        c,
                        &Pipeline::new(vec![
                            Box::new(Mem2Reg) as Box<dyn Pass>,
                            Box::new(IfConvert) as Box<dyn Pass>,
                        ]),
                    )
                },
                &config,
            );
            assert!(
                matches!(outcome, DiffOutcome::Match),
                "seed {seed}: {outcome:?}"
            );
        }
    }
}
