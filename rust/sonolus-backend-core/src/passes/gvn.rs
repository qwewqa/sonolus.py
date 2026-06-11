//! W1 GVN + rewrite rules (PORT.md T3.2): one pass that
//!
//! 1. establishes **canonical commutative operand ordering** (invariant §3.3),
//! 2. drives the W1 rewrite rules ([`crate::passes::rules`]) to fixpoint
//!    through the T2.2 [`RewriteDriver`],
//! 3. performs **dominator-based global value numbering** over pure
//!    instructions and replaces dominated redundancies, and
//! 4. **sweeps** every defining instruction those steps made dead out of the
//!    schedule, so the MIR satisfies the lowering contract again
//!    (`LowerError::MultiUse` otherwise — see lower.rs).
//!
//! # Canonical commutative operand ordering (the §3.3 canonical form)
//!
//! The canonical total order on operand [`Value`]s is:
//!
//! 1. **Constants order before non-constants.**
//! 2. Constants order by ascending numeric value (`f64::total_cmp`, so
//!    `-0.0 < +0.0` and NaNs order by sign/payload deterministically), then
//!    int tag before float tag (`5` before `5.0`), then arena index.
//! 3. Non-constants order by ascending arena index (definition order, which
//!    for scheduled values equals schedule/evaluation order).
//!
//! The commutative op set is `Add`, `Multiply`, `Equal`, `NotEqual` — the ops
//! whose runtime kernels are value-commutative on every f64 input (IEEE
//! `+`/`*` and `==`/`!=` are symmetric, including NaN and signed-zero
//! operands). **`Min`/`Max` are deliberately excluded**: the interpreter's
//! `py_min`/`py_max` are position-dependent (`min(NaN, x) == NaN` but
//! `min(x, NaN) == x`; ties keep the first operand, distinguishing `±0.0`).
//! Legacy CSE (`cse.py::COMMUTATIVE_OPS`) sorted `Min`/`Max` operands anyway;
//! that reordering is observably wrong under NaN operands and the T2.3
//! differential harness (which seeds NaN via `EngineRom`) would catch it, so
//! this port is deliberately more conservative than legacy. `And`/`Or` are
//! `ShortCircuit` instructions (lazy rhs, D11) and are excluded outright.
//!
//! Operand order of an eager instruction determines the evaluation order of
//! the operands' spliced subtrees after lowering, so the IR-level swap is
//! applied only when it is **observably free**: both operand subtrees must be
//! *reorder-transparent* — every transitively reachable instruction is a
//! constant, a pure op whose kernel cannot raise, or a `Load` with statically
//! in-bounds constant block/index (reads commute with reads; nothing in a
//! transparent subtree can write, log, draw, or trap). Non-transparent
//! operand pairs keep their original order — but still **value-number
//! canonically**: GVN keys sort commutative operand class ids, so `Add(a, b)`
//! and `Add(b, a)` always receive the same value number (merging two
//! syntactic orders never reorders any evaluation: the leader keeps its own
//! order and the redundant site stops computing entirely). Lazy
//! (`ShortCircuit` rhs) trees are never reordered or value-numbered (D11).
//!
//! # Dominator-based GVN
//!
//! Value numbers are assigned walking blocks in reverse postorder (idoms
//! before their subtrees) and each schedule in order:
//!
//! - **Constants** are numbered by *numeric value bits* with the int/float tag
//!   erased: `5` and `5.0` share a class (the same relaxation output dedup
//!   ships — `output.rs` keys constants by Python `==`; the first encounter's
//!   tag survives because the leader is kept as-is). `-0.0` and `+0.0` do
//!   **not** share a class (distinct runtime values under `Sign`); NaNs are
//!   classed by exact bits.
//! - **Pure ops** ([`crate::effects`]: no flags set) are numbered by
//!   `(op, pure_node, operand classes)`, commutative operands sorted.
//! - Everything else — `Load` (a memory read is *not* pure: a later store may
//!   change it and a dynamic index may trap), `Store`, RNG draws, side
//!   effects, `ShortCircuit` (control / lazy boundary), `Phi` (control-
//!   dependent) — gets a fresh **singleton** class. Singleton classes are
//!   legitimate key components: a singleton class id denotes exactly one SSA
//!   value, so two pure ops with identical keys over singleton operands
//!   compute over the *same values* and may merge. (Pre-W2 every value was
//!   single-use, so ops over singletons could never repeat and were skipped;
//!   post-Mem2Reg shared SSA values make this the load-bearing CSE case —
//!   `Add(v, 1)` at two sites with the same `v` now merges.)
//!
//! A redundancy is a pure instruction whose class already has an occurrence
//! in a **dominating** block (or earlier in the same block). Operand classes
//! bottom out in constants or singleton classes (single SSA values whose defs
//! dominate every use), so every numbered class is a *fixed* value
//! computation — its value cannot vary between the leader's and the
//! redundant's execution points, and if it traps, the dominating leader traps
//! first (identical computation, identical error), so the redundant site is
//! unreachable. Replacement must respect the lowering
//! contract (every scheduled value used at most once, in-block): values are
//! shared **through a fresh single-slot temp**, mirroring legacy CSE's
//! extraction (`cse.py`, cost ≥ 4 with the same cost model):
//!
//! - on the first merge against leader `L`, a temp `gvnN` is allocated, a
//!   `Store gvnN <- L` is scheduled immediately after `L`, and `L`'s single
//!   use is rewritten to a `Load gvnN` scheduled immediately before the user
//!   (evaluation order is unchanged: `L` already evaluated at its slot, and
//!   the new load reads a cell only that store writes);
//! - every dominated redundancy is replaced by a fresh `Load gvnN` spliced at
//!   its position ([`crate::rewrite::apply_rewrite`]), and its now-dead
//!   defining instruction (plus transitively orphaned operands) is swept.
//!
//! Classes cheaper than the legacy cost threshold (`cost < 4`, where consts
//! cost 1 and an op costs 1 + its operands) are never extracted — a load is
//! not cheaper than recomputing them.
//!
//! # Sweep
//!
//! Every value replaced by the driver or by GVN has zero remaining
//! references; its defining instruction is unscheduled, and any operand whose
//! reference count thereby drops to zero is unscheduled too if it is a pure
//! op. The sweep runs twice — after the rules phase (GVN must never see a
//! dead-but-scheduled instruction: extracting one as a merge leader would
//! resurrect references to its operands and break the single-use lowering
//! contract) and again after GVN for its own replacements. Safety: a rules-replaced instruction never traps (folds refuse
//! Python-error inputs; identity targets like `Multiply(x, 1)` cannot raise),
//! and a GVN-replaced instruction (or any orphan in its operand tree) has an
//! identical dominating computation that traps first. **Pre-existing**
//! zero-use instructions (bare expression statements from the frontend,
//! which may trap or read memory) are never seeds and never reach a count
//! transition, so they are preserved exactly.
//!
//! # Determinism, recursion, invalidation
//!
//! Blocks are visited in RPO and schedules in order; the rewrite driver is
//! deterministic; hash maps are key-lookup only (no iteration order escapes).
//! All tree walks use explicit stacks (invariant §3.4). The pass never
//! changes CFG shape; it invalidates value-level analyses when it mutates
//! (`Analyses::invalidate_values`).

use std::collections::HashMap;

use crate::analysis::Analyses;
use crate::effects::op_effects;
use crate::mir::{BlockId, BlockRef, IndexRef, Inst, Mir, Place, TempId, Terminator, Value};
use crate::ops::Op;
use crate::passes::{Pass, rules};
use crate::rewrite::{Rewrite, RewriteDriver, apply_rewrite};

/// The W1 GVN + rewrite-rules pass (module docs).
#[derive(Debug, Default)]
pub struct GvnRewritePass;

impl Pass for GvnRewritePass {
    fn name(&self) -> &'static str {
        "gvn"
    }

    fn run(&self, mir: &mut Mir, analyses: &mut Analyses) -> bool {
        if mir.blocks.is_empty() {
            return false;
        }
        let mut changed = false;

        // 1. Canonical commutative operand ordering.
        changed |= canonicalize_commutative(mir) > 0;

        // 2. Rewrite rules to fixpoint, then sweep their dead defining
        // instructions immediately: GVN must never value-number a replaced
        // (dead but still scheduled) instruction — extracting one as a merge
        // leader would resurrect references to its operands and break the
        // single-use lowering contract.
        let rule_list = rules::w1_rules();
        let report = RewriteDriver::new(&rule_list).run(mir);
        changed |= report.rewrites > 0;
        changed |= sweep(mir, &report.replaced) > 0;

        // The driver/canonicalization mutated instructions (never CFG shape);
        // drop value-level caches before requesting the dominator tree.
        analyses.invalidate_values();

        // 3. Dominator-based GVN, then sweep its replaced redundancies.
        let gvn_replaced = run_gvn(mir, analyses);
        changed |= !gvn_replaced.is_empty();
        changed |= sweep(mir, &gvn_replaced) > 0;

        if changed {
            analyses.invalidate_values();
        }
        changed
    }
}

// ----------------------------------------------------------------------------------
// Canonical commutative operand ordering
// ----------------------------------------------------------------------------------

/// The value-commutative op set (module docs; `Min`/`Max`/`And`/`Or` excluded
/// deliberately).
fn is_commutative(op: Op) -> bool {
    matches!(op, Op::Add | Op::Multiply | Op::Equal | Op::NotEqual)
}

/// Pure ops whose kernel can never raise for any f64 inputs (used by the
/// reorder-transparency check). Conservative whitelist over the interpreter's
/// `apply_simple`/`reduce_fold`: anything not listed is assumed trappable.
fn op_cannot_trap(op: Op) -> bool {
    matches!(
        op,
        Op::Add
            | Op::Subtract
            | Op::Multiply
            | Op::Abs
            | Op::Negate
            | Op::Not
            | Op::Sign
            | Op::Tanh
            | Op::Arctan
            | Op::Arctan2
            | Op::Degree
            | Op::Radian
            | Op::Equal
            | Op::NotEqual
            | Op::Greater
            | Op::GreaterOr
            | Op::Less
            | Op::LessOr
            | Op::Max
            | Op::Min
            | Op::Clamp
            | Op::Lerp
            | Op::LerpClamped
            | Op::Frac // py_mod(x, 1) cannot divide by zero; NaN/inf flow through
    )
}

/// A `Load` whose place is fully constant and statically in-bounds: it cannot
/// trap and has no dynamic components to evaluate.
fn load_is_static_inbounds(mir: &Mir, place: &Place) -> bool {
    let IndexRef::Const(index) = place.index else {
        return false;
    };
    let Some(cell) = index.checked_add(place.offset) else {
        return false;
    };
    match place.block {
        BlockRef::Concrete(_) => (0..=65535).contains(&cell),
        BlockRef::Temp(t) => cell >= 0 && u64::try_from(cell).is_ok_and(|c| c < mir.temps[t].size),
        BlockRef::Value(_) => false,
    }
}

/// Whether the whole operand subtree rooted at `root` is reorder-transparent
/// (module docs): constants, non-trapping pure ops, and statically in-bounds
/// constant-place loads only. Iterative.
fn subtree_is_reorder_transparent(mir: &Mir, root: Value) -> bool {
    let mut stack = vec![root];
    while let Some(v) = stack.pop() {
        match mir.inst(v) {
            Inst::ConstInt(_) | Inst::ConstFloat(_) => {}
            Inst::Op { op, args, .. } => {
                if !op_effects(*op).is_pure() || !op_cannot_trap(*op) {
                    return false;
                }
                stack.extend_from_slice(args);
            }
            Inst::Load { place } => {
                if !load_is_static_inbounds(mir, place) {
                    return false;
                }
            }
            Inst::ShortCircuit { .. } | Inst::Store { .. } | Inst::Phi { .. } => return false,
        }
    }
    true
}

/// The canonical total order on operand values (module docs).
fn canonical_cmp(mir: &Mir, a: Value, b: Value) -> std::cmp::Ordering {
    fn key(mir: &Mir, v: Value) -> (u8, u64, u8, Value) {
        match mir.inst(v) {
            Inst::ConstInt(i) => {
                #[allow(clippy::cast_precision_loss)]
                let bits = total_order_bits(*i as f64);
                (0, bits, 0, v)
            }
            Inst::ConstFloat(f) => (0, total_order_bits(*f), 1, v),
            _ => (1, 0, 0, v),
        }
    }
    /// Monotone map from f64 to u64 matching `f64::total_cmp` order.
    fn total_order_bits(f: f64) -> u64 {
        let b = f.to_bits();
        if b >> 63 == 1 { !b } else { b | (1 << 63) }
    }
    key(mir, a).cmp(&key(mir, b))
}

/// Applies the canonical ordering in place to every eager scheduled
/// commutative instruction whose operand subtrees are reorder-transparent.
/// Returns the number of swaps. Lazy trees are untouched (D11).
fn canonicalize_commutative(mir: &mut Mir) -> usize {
    let order: Vec<Value> = mir
        .blocks
        .iter()
        .flat_map(|b| b.insts.iter().copied())
        .collect();
    let mut swaps = 0;
    for v in order {
        let Inst::Op { op, args, .. } = mir.inst(v) else {
            continue;
        };
        if !is_commutative(*op) || args.len() != 2 {
            continue;
        }
        let (a, b) = (args[0], args[1]);
        if canonical_cmp(mir, b, a) != std::cmp::Ordering::Less {
            continue;
        }
        if !subtree_is_reorder_transparent(mir, a) || !subtree_is_reorder_transparent(mir, b) {
            continue;
        }
        let Inst::Op { args, .. } = &mut mir.insts[v as usize] else {
            unreachable!("matched above");
        };
        args.swap(0, 1);
        swaps += 1;
    }
    swaps
}

// ----------------------------------------------------------------------------------
// Dominator-based GVN
// ----------------------------------------------------------------------------------

/// A value-number key. Constants are keyed by the bits of their numeric value
/// with the tag erased (`5` ≡ `5.0`; `-0.0` ≢ `+0.0`; NaNs by exact bits).
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
enum VnKey {
    Const(u64),
    Op {
        op: Op,
        pure_node: bool,
        args: Vec<u32>,
    },
}

/// Legacy CSE's extraction threshold (`cse.py::_cost >= 4`).
const EXTRACT_COST: u64 = 4;

#[derive(Debug, Default)]
struct OccurrenceList {
    /// Non-merged occurrences of the class, in visit (RPO × schedule) order.
    list: Vec<(Value, BlockId)>,
    /// The temp the class has been extracted into, once a merge happened.
    extracted: Option<TempId>,
}

struct Gvn {
    /// Value -> class id (assigned on first sight; operands resolve lazily).
    vn_of: Vec<Option<u32>>,
    key_map: HashMap<VnKey, u32>,
    /// Per-class legacy cost (1 for constants, 1 + Σ operands for ops).
    cost: Vec<u64>,
    occurrences: HashMap<u32, OccurrenceList>,
    /// Reference counts at GVN start (zero-use redundancies are simply
    /// dropped instead of being replaced by a load nobody reads).
    counts: Vec<u32>,
    next_temp: usize,
    replaced: Vec<Value>,
}

impl Gvn {
    fn fresh_singleton(&mut self) -> u32 {
        let id = u32::try_from(self.cost.len()).expect("class count fits u32");
        self.cost.push(1);
        id
    }

    fn intern(&mut self, key: VnKey, cost: u64) -> u32 {
        if let Some(&id) = self.key_map.get(&key) {
            return id;
        }
        let id = u32::try_from(self.cost.len()).expect("class count fits u32");
        self.cost.push(cost);
        self.key_map.insert(key, id);
        id
    }

    /// The class of an operand: a memoized const class, the operand's
    /// already-assigned class, or a fresh singleton (defensive — operands of
    /// scheduled instructions are defined earlier in the same block by the
    /// lowering contract).
    fn operand_class(&mut self, mir: &Mir, v: Value) -> u32 {
        if let Some(vn) = self.vn_of[v as usize] {
            return vn;
        }
        let vn = match mir.inst(v) {
            #[allow(clippy::cast_precision_loss)]
            Inst::ConstInt(i) => self.intern(VnKey::Const((*i as f64).to_bits()), 1),
            Inst::ConstFloat(f) => self.intern(VnKey::Const(f.to_bits()), 1),
            _ => self.fresh_singleton(),
        };
        self.vn_of[v as usize] = Some(vn);
        vn
    }
}

fn temp_cell(temp: TempId) -> Place {
    Place {
        block: BlockRef::Temp(temp),
        index: IndexRef::Const(0),
        offset: 0,
    }
}

/// Runs GVN proper (module docs). Returns the replaced (now dead, still
/// scheduled) redundant values for the sweep.
///
/// The dominator tree is computed once up front and stays valid throughout:
/// nothing in this function changes the CFG shape (only instructions,
/// schedules, and temps — value-level state, invalidated by the caller).
#[allow(clippy::too_many_lines)] // one RPO × schedule numbering walk
fn run_gvn(mir: &mut Mir, analyses: &mut Analyses) -> Vec<Value> {
    let dom = analyses.dom_tree(mir);
    let rpo: Vec<BlockId> = dom.rpo().to_vec();
    let mut gvn = Gvn {
        vn_of: vec![None; mir.insts.len()],
        key_map: HashMap::new(),
        cost: Vec::new(),
        occurrences: HashMap::new(),
        counts: count_scheduled_uses(mir),
        next_temp: 0,
        replaced: Vec::new(),
    };

    for &block in &rpo {
        // Snapshot: extraction inserts loads/stores into schedules, and those
        // new instructions are never value-numbering candidates themselves
        // (their classes are set explicitly where created).
        let snapshot: Vec<Value> = mir.blocks[block].insts.clone();
        for v in snapshot {
            let inst = mir.inst(v).clone();
            let Inst::Op {
                op,
                pure_node,
                args,
            } = inst
            else {
                // Loads, stores, ShortCircuit, phis (pre-W2 none), effectful
                // ops: singleton classes.
                let vn = gvn.fresh_singleton();
                gvn.vn_of[v as usize] = Some(vn);
                continue;
            };
            if !op_effects(op).is_pure() {
                let vn = gvn.fresh_singleton();
                gvn.vn_of[v as usize] = Some(vn);
                continue;
            }
            let mut arg_classes: Vec<u32> = Vec::with_capacity(args.len());
            let mut cost = 1u64;
            for &a in &args {
                let c = gvn.operand_class(mir, a);
                cost = cost.saturating_add(gvn.cost[c as usize]);
                arg_classes.push(c);
            }
            if is_commutative(op) {
                arg_classes.sort_unstable();
            }
            let key = VnKey::Op {
                op,
                pure_node,
                args: arg_classes,
            };
            let vn = gvn.intern(key, cost);
            gvn.vn_of[v as usize] = Some(vn);
            if gvn.cost[vn as usize] < EXTRACT_COST {
                continue; // a load would not be cheaper; never merge this class
            }
            let (leader, extracted) = {
                let occ = gvn.occurrences.entry(vn).or_default();
                let leader = occ
                    .list
                    .iter()
                    .copied()
                    .find(|&(_, lb)| dom.dominates(lb, block));
                if leader.is_none() {
                    occ.list.push((v, block));
                }
                (leader, occ.extracted)
            };
            let Some((leader, leader_block)) = leader else {
                continue;
            };
            // Merge `v` into the leader through the class temp.
            let temp = if let Some(t) = extracted {
                t
            } else {
                let t = extract_leader(
                    mir,
                    &mut gvn.next_temp,
                    leader,
                    leader_block,
                    vn,
                    &mut gvn.vn_of,
                );
                gvn.occurrences
                    .get_mut(&vn)
                    .expect("entry created above")
                    .extracted = Some(t);
                t
            };
            if gvn.counts.get(v as usize).copied().unwrap_or(0) == 0 {
                // A bare redundant statement: nothing reads it, just sweep it.
                gvn.replaced.push(v);
            } else {
                let load = Inst::Load {
                    place: temp_cell(temp),
                };
                apply_rewrite(mir, v, Rewrite::NewInst(load));
                // The replacement load *is* the class value: record its class
                // so enclosing expressions can still merge.
                let new_v = u32::try_from(mir.insts.len() - 1).expect("arena fits u32");
                gvn.vn_of.resize(mir.insts.len(), None);
                gvn.vn_of[new_v as usize] = Some(vn);
                gvn.replaced.push(v);
            }
        }
    }
    gvn.replaced
}

/// Extracts a merge leader into a fresh single-slot temp: schedules
/// `Store temp <- leader` right after the leader and rewrites the leader's
/// single use (an operand of a later instruction in its block, or the block's
/// branch test) to a load of the temp. Returns the temp.
fn extract_leader(
    mir: &mut Mir,
    next_temp: &mut usize,
    leader: Value,
    leader_block: BlockId,
    class: u32,
    vn_of: &mut Vec<Option<u32>>,
) -> TempId {
    let temp = mir.push_temp(format!("gvn{next_temp}"), 1);
    *next_temp += 1;

    let leader_pos = mir.blocks[leader_block]
        .insts
        .iter()
        .position(|&x| x == leader)
        .expect("leader is scheduled in its block");
    let store = mir.push_inst(Inst::Store {
        place: temp_cell(temp),
        value: leader,
    });
    mir.blocks[leader_block].insts.insert(leader_pos + 1, store);

    // Rewrite the leader's single use (lowering contract: at most one, in the
    // same block or the terminator test). The store just inserted also
    // references the leader and is skipped.
    let mut pos = leader_pos + 2;
    while pos < mir.blocks[leader_block].insts.len() {
        let user = mir.blocks[leader_block].insts[pos];
        let mut refs = 0usize;
        Mir::for_each_operand(mir.inst(user), |o| {
            if o == leader {
                refs += 1;
            }
        });
        if refs > 0 {
            // One load per reference (a single load value may only be
            // consumed once at lowering; >1 ref cannot occur in
            // contract-satisfying MIR, handled for robustness).
            for _ in 0..refs {
                let load = mir.push_inst(Inst::Load {
                    place: temp_cell(temp),
                });
                vn_of.resize(mir.insts.len(), None);
                vn_of[load as usize] = Some(class);
                mir.blocks[leader_block].insts.insert(pos, load);
                pos += 1;
                let mut replaced_one = false;
                let user_inst = &mut mir.insts[user as usize];
                Mir::for_each_operand_mut(user_inst, |o| {
                    if *o == leader && !replaced_one {
                        *o = load;
                        replaced_one = true;
                    }
                });
            }
            return temp;
        }
        pos += 1;
    }
    if let Terminator::Branch { test, .. } = &mir.blocks[leader_block].terminator
        && *test == leader
    {
        let load = mir.push_inst(Inst::Load {
            place: temp_cell(temp),
        });
        vn_of.resize(mir.insts.len(), None);
        vn_of[load as usize] = Some(class);
        mir.blocks[leader_block].insts.push(load);
        if let Terminator::Branch { test, .. } = &mut mir.blocks[leader_block].terminator {
            *test = load;
        }
    }
    // No use at all (a bare leader statement): the store alone is the use.
    temp
}

// ----------------------------------------------------------------------------------
// Sweep
// ----------------------------------------------------------------------------------

/// Reference counts over everything that can reference a value: operands of
/// scheduled instructions (including the lazy `ShortCircuit` rhs trees they
/// own), terminator tests, and **phi arguments** (post-W2 MIR has phis; a
/// value whose only use is a phi argument is NOT dead — sweeping it would
/// leave the phi referencing an unscheduled value, caught by the W2 fuzz as
/// `DestructError::UnscheduledCrossBlockValue`). Extends `lower::count_uses`
/// (which never sees phis) with the phi-argument case.
fn count_scheduled_uses(mir: &Mir) -> Vec<u32> {
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

/// Unschedules the replaced values (all reference-free by construction) and,
/// transitively, any pure-op operand whose reference count drops to zero.
/// Returns the number of removed schedule slots. See the module docs for why
/// pre-existing zero-use statements are untouched.
fn sweep(mir: &mut Mir, replaced: &[Value]) -> usize {
    if replaced.is_empty() {
        return 0;
    }
    let scheduled = mir.scheduled_mask();
    let mut counts = count_scheduled_uses(mir);
    let removable = |mir: &Mir, v: Value| matches!(mir.inst(v), Inst::Op { op, .. } if op_effects(*op).is_pure());
    let mut dead = vec![false; mir.insts.len()];
    let mut stack: Vec<Value> = replaced
        .iter()
        .copied()
        .filter(|&v| scheduled[v as usize] && counts[v as usize] == 0 && removable(mir, v))
        .collect();
    while let Some(v) = stack.pop() {
        if dead[v as usize] {
            continue;
        }
        dead[v as usize] = true;
        Mir::for_each_operand(mir.inst(v), |o| {
            counts[o as usize] = counts[o as usize].saturating_sub(1);
            if counts[o as usize] == 0
                && scheduled[o as usize]
                && !dead[o as usize]
                && removable(mir, o)
            {
                stack.push(o);
            }
        });
    }
    let mut removed = 0usize;
    for block in &mut mir.blocks {
        let before = block.insts.len();
        block.insts.retain(|&v| !dead[v as usize]);
        removed += before - block.insts.len();
    }
    removed
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::cfg::{
        BasicBlock, BlockValue, Cfg, Edge, EdgeCond, IndexValue, Node, Place as CfgPlace,
    };
    use crate::diff::{DiffConfig, DiffOutcome, build_memory, diff_with, run_with_memory};
    use crate::mir::CaseCond;
    use crate::passes::Pipeline;
    use crate::pipeline::{
        Level, compile_cfg, compile_cfg_with_pipeline, compile_cfg_with_pipeline_stats,
    };

    fn gvn_pipeline() -> Pipeline {
        Pipeline::new(vec![Box::new(GvnRewritePass)])
    }

    fn run_pass(mir: &mut Mir) -> bool {
        let mut analyses = Analyses::new();
        GvnRewritePass.run(mir, &mut analyses)
    }

    fn temp_place(t: TempId) -> Place {
        Place {
            block: BlockRef::Temp(t),
            index: IndexRef::Const(0),
            offset: 0,
        }
    }

    fn sched(mir: &mut Mir, block: BlockId, inst: Inst) -> Value {
        let v = mir.push_inst(inst);
        mir.blocks[block].insts.push(v);
        v
    }

    fn op(mir: &mut Mir, block: BlockId, op: Op, args: Vec<Value>) -> Value {
        sched(
            mir,
            block,
            Inst::Op {
                op,
                pure_node: true,
                args,
            },
        )
    }

    // ------------------------------------------------------------------------------
    // Canonical ordering
    // ------------------------------------------------------------------------------

    /// `Add(load, 1)` in both operand orders normalizes to the same canonical
    /// order (constants first).
    #[test]
    fn canonical_ordering_is_deterministic_across_input_orders() {
        let build = |const_first: bool| {
            let mut mir = Mir::new();
            let t = mir.push_temp("t", 1);
            let x = mir.push_temp("x", 1);
            let b0 = mir.push_block();
            let load = sched(
                &mut mir,
                b0,
                Inst::Load {
                    place: temp_place(x),
                },
            );
            let one = mir.push_inst(Inst::ConstInt(1));
            let args = if const_first {
                vec![one, load]
            } else {
                vec![load, one]
            };
            let add = op(&mut mir, b0, Op::Add, args);
            sched(
                &mut mir,
                b0,
                Inst::Store {
                    place: temp_place(t),
                    value: add,
                },
            );
            (mir, add, one, load)
        };
        for const_first in [false, true] {
            let (mut mir, add, one, load) = build(const_first);
            run_pass(&mut mir);
            let Inst::Op { args, .. } = mir.inst(add) else {
                panic!("Add survives (no rule folds Add(load, 1))");
            };
            assert_eq!(args, &vec![one, load], "canonical form: const first");
        }
    }

    /// Two int constants order by value; equal values order int tag first.
    #[test]
    fn canonical_order_consts_by_value_then_tag() {
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 1);
        let b0 = mir.push_block();
        // Multiply(5.0, 5): both consts, numeric-equal — int tag orders first.
        // (Multiply, not Add: Multiply(5.0, 5) folds, so disable folding by
        // using values that fold to the same thing either way — instead use
        // Equal, which folds too... every all-const commutative op folds.
        // Check the comparator directly instead.)
        let five_float = mir.push_inst(Inst::ConstFloat(5.0));
        let five_int = mir.push_inst(Inst::ConstInt(5));
        let six = mir.push_inst(Inst::ConstInt(6));
        let load = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: temp_place(t),
            },
        );
        assert_eq!(
            canonical_cmp(&mir, five_int, five_float),
            std::cmp::Ordering::Less,
            "equal numeric value: int tag first"
        );
        assert_eq!(
            canonical_cmp(&mir, five_float, six),
            std::cmp::Ordering::Less,
            "constants by ascending numeric value"
        );
        assert_eq!(
            canonical_cmp(&mir, six, load),
            std::cmp::Ordering::Less,
            "constants before non-constants"
        );
        let neg_zero = mir.push_inst(Inst::ConstFloat(-0.0));
        let pos_zero = mir.push_inst(Inst::ConstFloat(0.0));
        assert_eq!(
            canonical_cmp(&mir, neg_zero, pos_zero),
            std::cmp::Ordering::Less,
            "total order: -0.0 < +0.0"
        );
    }

    /// Operands with non-transparent subtrees (a memory write, an RNG draw, a
    /// dynamic-index load) are never reordered.
    #[test]
    fn non_transparent_operands_are_not_reordered() {
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 1);
        let b0 = mir.push_block();
        let zero = mir.push_inst(Inst::ConstInt(0));
        let one = mir.push_inst(Inst::ConstInt(1));
        let draw = sched(
            &mut mir,
            b0,
            Inst::Op {
                op: Op::Random,
                pure_node: false,
                args: vec![zero, one],
            },
        );
        let seven = mir.push_inst(Inst::ConstInt(7));
        // Add(draw, 7): canonical order would put 7 first, but the draw is
        // not transparent (RNG) — order must be preserved.
        let add = op(&mut mir, b0, Op::Add, vec![draw, seven]);
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: temp_place(t),
                value: add,
            },
        );
        run_pass(&mut mir);
        let Inst::Op { args, .. } = mir.inst(add) else {
            panic!("Add survives");
        };
        assert_eq!(args, &vec![draw, seven], "RNG operand not moved");
    }

    /// Same expression in two operand orders value-numbers identically even
    /// when the IR-level swap is suppressed (the VN key is always canonical):
    /// `Add(D, 7)` and `Add(7, D)` with `D = Divide(5, 0)` (pure, trapping,
    /// non-transparent) merge across blocks.
    #[test]
    fn two_operand_orders_value_number_identically() {
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 1);
        let s = mir.push_temp("s", 1);
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let cond = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: temp_place(s),
            },
        );
        mir.blocks[b0].terminator = Terminator::Branch {
            test: cond,
            cases: vec![(CaseCond::Int(0), b1)],
            default: Some(b1),
        };
        // Wait: both edges to b1 make b0 dominate b1; keep it simple.
        let build_expr = |mir: &mut Mir, block: BlockId, swapped: bool| {
            let five = mir.push_inst(Inst::ConstInt(5));
            let zero = mir.push_inst(Inst::ConstInt(0));
            let div = op(mir, block, Op::Divide, vec![five, zero]);
            let seven = mir.push_inst(Inst::ConstInt(7));
            let args = if swapped {
                vec![seven, div]
            } else {
                vec![div, seven]
            };
            op(mir, block, Op::Add, args)
        };
        let e0 = build_expr(&mut mir, b0, false);
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: temp_place(t),
                value: e0,
            },
        );
        let e1 = build_expr(&mut mir, b1, true);
        sched(
            &mut mir,
            b1,
            Inst::Store {
                place: temp_place(t),
                value: e1,
            },
        );
        mir.blocks[b1].terminator = Terminator::Exit;
        let temps_before = mir.temps.len();
        run_pass(&mut mir);
        assert_eq!(
            mir.temps.len(),
            temps_before + 1,
            "the two orders share a class: one extraction temp"
        );
        // The redundant Add in b1 was unscheduled; a load took its place.
        assert!(
            !mir.blocks[b1].insts.contains(&e1),
            "redundant Add swept from the schedule"
        );
    }

    // ------------------------------------------------------------------------------
    // GVN dominance correctness
    // ------------------------------------------------------------------------------

    /// A non-foldable pure expression tree over constants: `Multiply(Sign(c),
    /// Trunc(c))`-shaped (Sign/Trunc are outside the legacy fold set), cost
    /// >= 4, trap-free at runtime.
    fn unfoldable_expr(mir: &mut Mir, block: BlockId) -> Value {
        let c1 = mir.push_inst(Inst::ConstFloat(2.9));
        let trunc = op(mir, block, Op::Trunc, vec![c1]);
        let c2 = mir.push_inst(Inst::ConstInt(7));
        let sign = op(mir, block, Op::Sign, vec![c2]);
        op(mir, block, Op::Multiply, vec![sign, trunc])
    }

    /// Diamond CFG: entry -> {then, else} -> merge, branch test on a load.
    fn diamond() -> (Mir, TempId, BlockId, BlockId, BlockId, BlockId) {
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 4);
        let entry = mir.push_block();
        let then_b = mir.push_block();
        let else_b = mir.push_block();
        let merge = mir.push_block();
        let test = sched(
            &mut mir,
            entry,
            Inst::Load {
                place: temp_place(t),
            },
        );
        mir.blocks[entry].terminator = Terminator::Branch {
            test,
            cases: vec![(CaseCond::Int(0), else_b)],
            default: Some(then_b),
        };
        mir.blocks[then_b].terminator = Terminator::Jump(merge);
        mir.blocks[else_b].terminator = Terminator::Jump(merge);
        mir.blocks[merge].terminator = Terminator::Exit;
        (mir, t, entry, then_b, else_b, merge)
    }

    fn store_to(mir: &mut Mir, block: BlockId, t: TempId, index: i64, value: Value) {
        let place = Place {
            block: BlockRef::Temp(t),
            index: IndexRef::Const(index),
            offset: 0,
        };
        sched(mir, block, Inst::Store { place, value });
    }

    #[test]
    fn dominating_redundancy_is_merged() {
        let (mut mir, t, entry, then_b, _else_b, _merge) = diamond();
        let e_entry = unfoldable_expr(&mut mir, entry);
        store_to(&mut mir, entry, t, 1, e_entry);
        let e_then = unfoldable_expr(&mut mir, then_b);
        store_to(&mut mir, then_b, t, 2, e_then);
        let temps_before = mir.temps.len();
        assert!(run_pass(&mut mir));
        assert_eq!(mir.temps.len(), temps_before + 1, "one extraction temp");
        assert!(
            !mir.blocks[then_b].insts.contains(&e_then),
            "dominated redundancy replaced and swept"
        );
        assert!(
            mir.blocks[entry].insts.contains(&e_entry),
            "the dominating leader stays"
        );
        // The store in then_b now stores a load of the extraction temp.
        let store = *mir.blocks[then_b].insts.last().expect("store remains");
        let Inst::Store { value, .. } = mir.inst(store) else {
            panic!("expected store");
        };
        assert!(
            matches!(mir.inst(*value), Inst::Load { place } if place.block == BlockRef::Temp(temps_before)),
            "redundant site loads the extraction temp"
        );
    }

    #[test]
    fn non_dominating_siblings_are_not_merged() {
        let (mut mir, t, _entry, then_b, else_b, _merge) = diamond();
        let e_then = unfoldable_expr(&mut mir, then_b);
        store_to(&mut mir, then_b, t, 1, e_then);
        let e_else = unfoldable_expr(&mut mir, else_b);
        store_to(&mut mir, else_b, t, 2, e_else);
        let temps_before = mir.temps.len();
        run_pass(&mut mir);
        assert_eq!(
            mir.temps.len(),
            temps_before,
            "sibling occurrences must not be merged (neither dominates)"
        );
        assert!(mir.blocks[then_b].insts.contains(&e_then));
        assert!(mir.blocks[else_b].insts.contains(&e_else));
    }

    #[test]
    fn same_block_redundancy_is_merged_and_lowers() {
        // Both occurrences in the entry block; the merged MIR must still
        // satisfy the lowering contract (single-use, in-block).
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 4);
        let b0 = mir.push_block();
        let e1 = unfoldable_expr(&mut mir, b0);
        store_to(&mut mir, b0, t, 1, e1);
        let e2 = unfoldable_expr(&mut mir, b0);
        store_to(&mut mir, b0, t, 2, e2);
        mir.blocks[b0].terminator = Terminator::Exit;
        run_pass(&mut mir);
        assert!(!mir.blocks[b0].insts.contains(&e2), "second copy merged");
        let alloc = crate::alloc::allocate_temps(&mir).expect("allocates");
        crate::lower::lower_mir(&mir, &alloc).expect("merged MIR lowers cleanly");
    }

    #[test]
    fn cheap_classes_are_not_extracted() {
        // Sign(7) twice: cost 2 < 4 — a load would not be cheaper. No temp.
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 4);
        let b0 = mir.push_block();
        let c1 = mir.push_inst(Inst::ConstInt(7));
        let s1 = op(&mut mir, b0, Op::Sign, vec![c1]);
        store_to(&mut mir, b0, t, 1, s1);
        let c2 = mir.push_inst(Inst::ConstInt(7));
        let s2 = op(&mut mir, b0, Op::Sign, vec![c2]);
        store_to(&mut mir, b0, t, 2, s2);
        let temps_before = mir.temps.len();
        run_pass(&mut mir);
        assert_eq!(mir.temps.len(), temps_before, "below the cost threshold");
        assert!(mir.blocks[b0].insts.contains(&s2));
    }

    // ------------------------------------------------------------------------------
    // Tag handling
    // ------------------------------------------------------------------------------

    /// `5` and `5.0` value-number identically (constant classes erase the
    /// tag); the first-encountered occurrence is the leader and keeps its
    /// own (int) tag.
    #[test]
    fn int_and_float_constants_share_a_class_first_tag_wins() {
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 4);
        let b0 = mir.push_block();
        // Add(Divide(5, 0), 7) — unfoldable (division by zero), cost >= 4.
        let build = |mir: &mut Mir, int_tag: bool| {
            let five = if int_tag {
                mir.push_inst(Inst::ConstInt(5))
            } else {
                mir.push_inst(Inst::ConstFloat(5.0))
            };
            let zero = mir.push_inst(Inst::ConstInt(0));
            let div = op(mir, b0, Op::Divide, vec![five, zero]);
            let seven = mir.push_inst(Inst::ConstInt(7));
            (op(mir, b0, Op::Add, vec![div, seven]), five)
        };
        let (e1, five_int) = build(&mut mir, true);
        store_to(&mut mir, b0, t, 1, e1);
        let (e2, _five_float) = build(&mut mir, false);
        store_to(&mut mir, b0, t, 2, e2);
        mir.blocks[b0].terminator = Terminator::Exit;
        let temps_before = mir.temps.len();
        run_pass(&mut mir);
        assert_eq!(
            mir.temps.len(),
            temps_before + 1,
            "5 and 5.0 operands share a class: the trees merge"
        );
        assert!(!mir.blocks[b0].insts.contains(&e2), "float-5 copy merged");
        // The surviving leader still carries the first-encountered int tag.
        let Inst::Op { args, .. } = mir.inst(e1) else {
            panic!("leader Add survives");
        };
        let Inst::Op { args: div_args, .. } = mir.inst(args[0]) else {
            panic!("leader Divide survives");
        };
        assert_eq!(mir.inst(div_args[0]), &Inst::ConstInt(5));
        assert_eq!(mir.inst(five_int), &Inst::ConstInt(5));
        // -0.0 and +0.0 must NOT share a class.
        let nz = mir.push_inst(Inst::ConstFloat(-0.0));
        let pz = mir.push_inst(Inst::ConstFloat(0.0));
        let mut gvn = Gvn {
            vn_of: vec![None; mir.insts.len()],
            key_map: HashMap::new(),
            cost: Vec::new(),
            occurrences: HashMap::new(),
            counts: vec![0; mir.insts.len()],
            next_temp: 0,
            replaced: Vec::new(),
        };
        let c_nz = gvn.operand_class(&mir, nz);
        let c_pz = gvn.operand_class(&mir, pz);
        assert_ne!(c_nz, c_pz, "signed zeros are distinct runtime values");
    }

    // ------------------------------------------------------------------------------
    // Effect preservation
    // ------------------------------------------------------------------------------

    /// Two identical `Random(0, 1)` draws must never merge (RNG draws are
    /// singleton classes; draw count is part of the optimizer contract).
    #[test]
    fn rng_draws_are_never_merged() {
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 4);
        let b0 = mir.push_block();
        for i in 0..2 {
            let zero = mir.push_inst(Inst::ConstInt(0));
            let one = mir.push_inst(Inst::ConstInt(1));
            let draw = sched(
                &mut mir,
                b0,
                Inst::Op {
                    op: Op::Random,
                    pure_node: false,
                    args: vec![zero, one],
                },
            );
            store_to(&mut mir, b0, t, i, draw);
        }
        mir.blocks[b0].terminator = Terminator::Exit;
        let temps_before = mir.temps.len();
        run_pass(&mut mir);
        assert_eq!(mir.temps.len(), temps_before);
        let draws = mir.blocks[b0]
            .insts
            .iter()
            .filter(|&&v| matches!(mir.inst(v), Inst::Op { op: Op::Random, .. }))
            .count();
        assert_eq!(draws, 2, "both draws stay scheduled");
    }

    /// Two loads of the same cell are never value-numbered together (a store
    /// in between could change the value; loads are singleton classes).
    #[test]
    fn loads_are_never_merged() {
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 4);
        let x = mir.push_temp("x", 1);
        let b0 = mir.push_block();
        for i in 0..2 {
            let load = sched(
                &mut mir,
                b0,
                Inst::Load {
                    place: temp_place(x),
                },
            );
            let seven = mir.push_inst(Inst::ConstInt(7));
            // Cost-4 trees over the loads: identical syntax, but loads are
            // singletons so the Multiplies must not merge.
            let neg = op(&mut mir, b0, Op::Negate, vec![load]);
            let mul = op(&mut mir, b0, Op::Multiply, vec![seven, neg]);
            store_to(&mut mir, b0, t, i, mul);
        }
        mir.blocks[b0].terminator = Terminator::Exit;
        let temps_before = mir.temps.len();
        run_pass(&mut mir);
        assert_eq!(mir.temps.len(), temps_before, "no merges through memory");
    }

    /// Post-W2 shape: two identical pure ops over the SAME (multi-use) SSA
    /// value merge — singleton operand classes are legitimate key components
    /// because a singleton class id denotes exactly one value (module docs).
    #[test]
    fn shared_value_expressions_merge() {
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 4);
        let b0 = mir.push_block();
        let load = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: temp_place(t),
            },
        );
        let seven = mir.push_inst(Inst::ConstInt(7));
        // Two syntactically identical cost-4 trees over the SAME load value
        // (multi-use SSA, the post-Mem2Reg shape): they must merge.
        for i in 0..2 {
            let neg = op(&mut mir, b0, Op::Negate, vec![load]);
            let mul = op(&mut mir, b0, Op::Multiply, vec![seven, neg]);
            store_to(&mut mir, b0, t, i + 1, mul);
        }
        mir.blocks[b0].terminator = Terminator::Exit;
        let temps_before = mir.temps.len();
        run_pass(&mut mir);
        assert_eq!(
            mir.temps.len(),
            temps_before + 1,
            "one gvn class temp for the merged expression"
        );
        // Exactly one Multiply remains scheduled (the leader).
        let muls = mir.blocks[b0]
            .insts
            .iter()
            .filter(|&&v| {
                matches!(
                    mir.inst(v),
                    Inst::Op {
                        op: Op::Multiply,
                        ..
                    }
                )
            })
            .count();
        assert_eq!(muls, 1, "the redundant Multiply was replaced by a load");
    }

    /// W2 phi-path regression (found by the 50k fuzz): a value whose ONLY use
    /// is a phi argument must count as used — the sweep must not unschedule
    /// it when a sibling redundancy is replaced (destruct would then reject
    /// the phi arg as `UnscheduledCrossBlockValue`).
    #[test]
    fn phi_arg_uses_keep_values_alive_through_the_sweep() {
        use crate::mir::CaseCond;
        // Diamond: b0 -> {b1, b2} -> b3. Both arms compute the same
        // extractable expression; one arm's copy feeds ONLY the join phi.
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 4);
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let b2 = mir.push_block();
        let b3 = mir.push_block();
        let test = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: temp_place(t),
            },
        );
        // A dominating leader of the same class in b0 (kept alive by a store).
        let seven0 = mir.push_inst(Inst::ConstInt(7));
        let neg0 = op(&mut mir, b0, Op::Negate, vec![seven0]);
        let mul0 = op(&mut mir, b0, Op::Multiply, vec![seven0, neg0]);
        store_to(&mut mir, b0, t, 1, mul0);
        mir.blocks[b0].terminator = Terminator::Branch {
            test,
            cases: vec![(CaseCond::Int(0), b1)],
            default: Some(b2),
        };
        mir.blocks[b1].terminator = Terminator::Jump(b3);
        mir.blocks[b2].terminator = Terminator::Jump(b3);
        // b1: the redundant twin, used only by the phi.
        let seven1 = mir.push_inst(Inst::ConstInt(7));
        let neg1 = op(&mut mir, b1, Op::Negate, vec![seven1]);
        let mul1 = op(&mut mir, b1, Op::Multiply, vec![seven1, neg1]);
        // b2: a distinct value for the other arm.
        let c2 = mir.push_inst(Inst::ConstInt(2));
        let phi = mir.push_inst(Inst::Phi {
            args: vec![(b1, mul1), (b2, c2)],
        });
        mir.blocks[b3].phis.push(phi);
        sched(
            &mut mir,
            b3,
            Inst::Store {
                place: temp_place(t),
                value: phi,
            },
        );
        run_pass(&mut mir);
        // Whatever GVN decided, every phi arg must still be scheduled (or a
        // constant): the full pipeline check is that destruct accepts it.
        crate::ssa::destruct_ssa(&mut mir).expect("phi args stay materializable");
    }

    /// A pre-existing zero-use trapping statement (a bare `Divide(1, 0)`)
    /// must survive the sweep — the trap is behavior.
    #[test]
    fn pre_existing_bare_trapping_statement_is_preserved() {
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let one = mir.push_inst(Inst::ConstInt(1));
        let zero = mir.push_inst(Inst::ConstInt(0));
        let div = op(&mut mir, b0, Op::Divide, vec![one, zero]);
        mir.blocks[b0].terminator = Terminator::Exit;
        run_pass(&mut mir);
        assert!(
            mir.blocks[b0].insts.contains(&div),
            "bare trapping statement preserved exactly"
        );
    }

    // ------------------------------------------------------------------------------
    // Lazy boundary (D11)
    // ------------------------------------------------------------------------------

    /// An expression inside a `ShortCircuit` rhs is not GVN'd against an
    /// identical eager twin outside, and the lazy tree is untouched.
    #[test]
    fn lazy_trees_are_not_value_numbered() {
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 4);
        let b0 = mir.push_block();
        // Eager twin (kept alive by a store).
        let eager = unfoldable_expr(&mut mir, b0);
        store_to(&mut mir, b0, t, 1, eager);
        // Lazy twin inside And(load, <expr>): unscheduled instructions.
        let guard = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: temp_place(t),
            },
        );
        let c1 = mir.push_inst(Inst::ConstFloat(2.9));
        let lazy_trunc = mir.push_inst(Inst::Op {
            op: Op::Trunc,
            pure_node: true,
            args: vec![c1],
        });
        let c2 = mir.push_inst(Inst::ConstInt(7));
        let lazy_sign = mir.push_inst(Inst::Op {
            op: Op::Sign,
            pure_node: true,
            args: vec![c2],
        });
        let lazy_mul = mir.push_inst(Inst::Op {
            op: Op::Multiply,
            pure_node: true,
            args: vec![lazy_sign, lazy_trunc],
        });
        let sc = sched(
            &mut mir,
            b0,
            Inst::ShortCircuit {
                op: Op::And,
                pure_node: true,
                lhs: guard,
                rhs: lazy_mul,
            },
        );
        store_to(&mut mir, b0, t, 2, sc);
        mir.blocks[b0].terminator = Terminator::Exit;
        let temps_before = mir.temps.len();
        run_pass(&mut mir);
        assert_eq!(
            mir.temps.len(),
            temps_before,
            "no extraction: the lazy twin must not participate in GVN"
        );
        assert_eq!(
            mir.inst(lazy_mul),
            &Inst::Op {
                op: Op::Multiply,
                pure_node: true,
                args: vec![lazy_sign, lazy_trunc],
            },
            "lazy tree untouched"
        );
        assert!(
            mir.blocks[b0].insts.contains(&eager),
            "eager twin untouched"
        );
    }

    // ------------------------------------------------------------------------------
    // End-to-end: sweep leaves lowerable MIR; differential matches; metrics drop
    // ------------------------------------------------------------------------------

    /// Frontend CFG builder for end-to-end pipeline tests.
    #[derive(Default)]
    struct B {
        cfg: Cfg,
    }

    impl B {
        fn node(&mut self, n: Node) -> usize {
            self.cfg.nodes.push(n);
            self.cfg.nodes.len() - 1
        }
        fn place_int(&mut self, block: i64, index: i64) -> usize {
            self.cfg.places.push(CfgPlace {
                block: BlockValue::Int(block),
                index: IndexValue::Int(index),
                offset: 0,
            });
            self.cfg.places.len() - 1
        }
        fn int(&mut self, v: i64) -> usize {
            self.node(Node::ConstInt(v))
        }
        fn pure_instr(&mut self, op: Op, args: Vec<usize>) -> usize {
            self.node(Node::PureInstr { op, args })
        }
        fn set(&mut self, place: usize, value: usize) -> usize {
            self.node(Node::Set { place, value })
        }
        fn get(&mut self, place: usize) -> usize {
            self.node(Node::Get(place))
        }
    }

    /// A frontend CFG with obvious redundancy: a constant-foldable tree and a
    /// non-foldable redundant tree computed in the entry and again in a
    /// dominated block.
    fn redundant_cfg() -> Cfg {
        let mut b = B::default();
        // Entry: 20[0] <- Sin(Add(Mod(7, 3), Multiply(2, 3)))   [folds]
        //        20[1] <- Multiply(Sign(Get(20, 9)? no — keep it pure-const)
        //        20[1] <- Multiply(Sign(7), Trunc(2.9))          [GVN leader]
        // branch on Get(21, 0): {0: bb1, default: bb2}
        // bb1:   20[2] <- Multiply(Sign(7), Trunc(2.9))          [redundant]
        // bb2:   20[3] <- 1
        let p0 = b.place_int(20, 0);
        let c7 = b.int(7);
        let c3 = b.int(3);
        let m = b.pure_instr(Op::Mod, vec![c7, c3]);
        let c2 = b.int(2);
        let c3b = b.int(3);
        let mul = b.pure_instr(Op::Multiply, vec![c2, c3b]);
        let add = b.pure_instr(Op::Add, vec![m, mul]);
        let sin = b.pure_instr(Op::Sin, vec![add]);
        let s0 = b.set(p0, sin);

        let expr = |b: &mut B| {
            let c7 = b.int(7);
            let sign = b.pure_instr(Op::Sign, vec![c7]);
            let t = b.node(Node::ConstFloat(2.9));
            let trunc = b.pure_instr(Op::Trunc, vec![t]);
            b.pure_instr(Op::Multiply, vec![sign, trunc])
        };
        let p1 = b.place_int(20, 1);
        let e0 = expr(&mut b);
        let s1 = b.set(p1, e0);
        let scrut_place = b.place_int(21, 0);
        let test = b.get(scrut_place);
        b.cfg.blocks.push(BasicBlock {
            statements: vec![s0, s1],
            test,
            outgoing: vec![
                Edge {
                    cond: EdgeCond::Int(0),
                    target: 1,
                },
                Edge {
                    cond: EdgeCond::None,
                    target: 2,
                },
            ],
        });
        let p2 = b.place_int(20, 2);
        let e1 = expr(&mut b);
        let s2 = b.set(p2, e1);
        let zt1 = b.int(0);
        b.cfg.blocks.push(BasicBlock {
            statements: vec![s2],
            test: zt1,
            outgoing: vec![],
        });
        let p3 = b.place_int(20, 3);
        let one = b.int(1);
        let s3 = b.set(p3, one);
        let zt2 = b.int(0);
        b.cfg.blocks.push(BasicBlock {
            statements: vec![s3],
            test: zt2,
            outgoing: vec![],
        });
        b.cfg
    }

    #[test]
    fn effectiveness_static_nodes_and_eval_count_drop() {
        let cfg = redundant_cfg();
        let (min_nodes, min_stats) =
            compile_cfg_with_pipeline_stats(&cfg, &Pipeline::new(vec![])).expect("minimal");
        let (gvn_nodes, gvn_stats) =
            compile_cfg_with_pipeline_stats(&cfg, &gvn_pipeline()).expect("gvn");
        assert!(
            gvn_stats.node_count < min_stats.node_count,
            "static nodes must drop: {} -> {}",
            min_stats.node_count,
            gvn_stats.node_count
        );
        // Take the 0-cond branch so the redundant site executes.
        let memory = vec![(20i64, vec![0.0; 8]), (21i64, vec![0.0; 8])];
        let base = run_with_memory(&min_nodes, &memory, 1, 1_000_000);
        let test = run_with_memory(&gvn_nodes, &memory, 1, 1_000_000);
        assert!(base.result.is_ok() && test.result.is_ok());
        assert!(
            test.eval_count < base.eval_count,
            "eval count must drop: {} -> {}",
            base.eval_count,
            test.eval_count
        );
        // And the optimized program is behaviorally identical.
        for seed in [0u64, 1, 2] {
            let outcome = diff_with(
                &cfg,
                |c| compile_cfg(c, Level::Minimal),
                |c| compile_cfg_with_pipeline(c, &gvn_pipeline()),
                &DiffConfig {
                    memory_seed: seed,
                    rng_seed: seed,
                    eval_budget: 1_000_000,
                },
            );
            assert_eq!(outcome, DiffOutcome::Match);
        }
        let _ = build_memory(&cfg, 0); // exercised above via diff_with
    }

    #[test]
    fn pass_reports_no_change_on_inert_input() {
        // A store of a load: nothing for any stage of the pass to do.
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 1);
        let x = mir.push_temp("x", 1);
        let b0 = mir.push_block();
        let load = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: temp_place(x),
            },
        );
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: temp_place(t),
                value: load,
            },
        );
        mir.blocks[b0].terminator = Terminator::Exit;
        assert!(!run_pass(&mut mir), "no mutation must report no change");
    }

    #[test]
    fn determinism_same_input_same_output() {
        let cfg = redundant_cfg();
        let a = compile_cfg_with_pipeline(&cfg, &gvn_pipeline()).expect("compiles");
        let b = compile_cfg_with_pipeline(&cfg, &gvn_pipeline()).expect("compiles");
        assert_eq!(
            crate::nodes::format_engine_node(&a.arena, a.root),
            crate::nodes::format_engine_node(&b.arena, b.root)
        );
    }
}
