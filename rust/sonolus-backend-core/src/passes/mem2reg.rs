//! W2 Mem2Reg/SROA for `TempBlock`s (PORT.md T3.4) — the promotion pass that
//! makes the SSA machinery load-bearing (decision D10).
//!
//! Turns every scalar slot of a *promotable* temp block into SSA values via
//! the Braun [`SsaBuilder`] (one [`Var`] per `(temp, slot)` pair): loads
//! become uses of the reaching definition, stores become definitions, and
//! join points get phis. The pass runs after wave W1 in the registry, so SCCP
//! has already folded computed-but-constant indices into `IndexRef::Const` —
//! being post-W1 is what makes the constant-index domain large in practice.
//! The W1 passes are re-run after this pass (registry entries below W2) so
//! folding/CSE finally see through what used to be opaque memory.
//!
//! # Promotion domain and escape analysis
//!
//! Promotion is whole-temp-block granular: a temp `t` is promoted iff **every
//! access to it in reachable code** is a `Load`/`Store` whose place is
//! `{block: Temp(t), index: Const(i), offset: o}` with `0 <= i + o <
//! t.size`, **and** the read-before-write check below passes. The full escape
//! list (any one refuses the whole temp — no partial SROA of mixed blocks, a
//! possible future refinement if metrics warrant):
//!
//! - **Dynamic index** (`IndexRef::Value`): the accessed slot is unknown
//!   statically; the whole temp stays memory.
//! - **Out-of-range constant access** (`i + o` outside `[0, size)`, including
//!   any access to a size-0 temp): out of contract for real frontend code,
//!   and the runtime behavior is allocation-dependent; stays memory.
//! - **Possible read-before-write** (below).
//!
//! These are *all* the escape conditions, because MIR has no other way to
//! name a temp: `BlockRef::Temp` appears only in `Load`/`Store` places, and a
//! dynamically computed place component (`BlockRef::Value`/`IndexRef::Value`)
//! holds a *value*, never a `TempId` — a temp's eventual runtime block id
//! (10000 + offset) does not exist before allocation, so no in-IR construct
//! can alias it ("address-taken" is unrepresentable; the legacy `ToSSA` makes
//! the same assumption). A load of a temp slot *used as* a block id is just
//! an ordinary constant-index load and promotes fine. Accesses in
//! **unreachable blocks** are ignored both for the escape analysis and the
//! rewrite: they never execute, and leaving them as memory ops is harmless
//! (at worst the temp keeps its allocation). `build_mir`, SCCP, and DCE all
//! remove or empty unreachable blocks, so this is defensive.
//!
//! # Read-before-write refusal (the undef policy)
//!
//! Braun's `read_variable` yields an undef placeholder when a variable may be
//! read before it is written. No constant is a faithful materialization of
//! such a read: an unwritten temp slot reads whatever the *slot allocator*
//! left there (the interpreter's `-1.0` default fill only when no other temp
//! got that cell), which is pipeline-specific by contract — the differential
//! harness deliberately excludes block 10000 from comparison, the fuzz
//! generator initializes every temp cell, and real frontend code never reads
//! a temp before writing it (the legacy `ToSSA` maps such reads to an `err`
//! place and calls them out of contract). So the SAFE choice is taken: a temp
//! with **any statically possible read-before-write path** is refused
//! entirely. The check is a forward must-be-stored dataflow per candidate
//! slot (`in[entry] = ∅`, `in[b] = ⋂ preds out[p]`, `out[b] = in[b] ∪
//! stores[b]`), with loads checked against the running in-block state; loads
//! inside lazy trees are checked at their owner's program point (they *may*
//! execute there). Refusals are counted in [`PromotionStats`] — on the
//! checked-in corpus this fires for ~2% of accessed temps (160/8992 post-W1),
//! all of the legacy "`err` place" kind: statically possible but dynamically
//! unreachable reads (e.g. matching a just-created `VarArray[Num, 1]` whose
//! `size > 0` guard is not folded at IR build time). Such temps simply stay
//! memory.
//!
//! As a corollary, the `SsaBuilder`'s undef path (`ConstInt(0)`) is
//! unreachable for promoted vars: a Braun lookup walk reaching the function
//! entry without a definition is exactly a def-free path from entry — which
//! this analysis refused. (When the entry block has predecessors — a loop
//! back to block 0 — its *content* is first split into a fresh block so the
//! entry has no preds and the walk/seal discipline stays canonical.)
//!
//! # `ShortCircuit` lazy trees (decision D11)
//!
//! A load of a promoted slot inside a lazy rhs tree evaluates *conditionally*
//! at its owner's program point. Replacing it is safe because (a) lazy trees
//! cannot contain stores (`build_mir` rejects `Set` in expression position;
//! lowering rejects `InvalidLazyInst`), so nothing can change the slot
//! between the owner's evaluation start and the conditional load, and (b) a
//! promoted-slot load has a constant in-bounds index — it cannot trap and has
//! no effects, so evaluating it or not is observable only through its value.
//! The reaching definition **at the owner's program point** is therefore the
//! exact value the lazy load would produce whenever it runs:
//!
//! - reaching def is a **constant** → the lazy `Load` is rewritten in place
//!   into that constant (lazy trees may reference constants);
//! - otherwise → the value is materialized through a fresh single-slot temp:
//!   `Store m2rN <- value` is scheduled directly **before the owner** (an
//!   unconditional temp write — unobservable; temp memory is excluded from
//!   the behavioral contract) and the lazy `Load` is rewritten in place to
//!   read `m2rN` (still conditional, still trap-free). This keeps lazy trees
//!   self-contained (they never reference scheduled values), preserving the
//!   builder invariant lowering relies on, at the same store+load cost the
//!   unpromoted form paid.
//!
//! # Output contract
//!
//! The pass leaves the MIR in *value SSA* form: values may now be used
//! multiple times and across blocks, and phis exist. This is exactly what the
//! re-run W1 passes consume, and `ssa::destruct_ssa` (wired unconditionally
//! into `compile_cfg` after the pass pipeline) re-establishes the lowering
//! contract (single-use, schedule = evaluation order) before allocation.
//! Removing a promoted load drops a pure, trap-free evaluation (allowed);
//! removing a promoted store drops only a temp-memory write (excluded from
//! the contract); the stored value's defining instruction stays scheduled at
//! its original position, preserving any trap/effect it carries.
//!
//! # Determinism and iteration
//!
//! Blocks are processed in RPO with Braun sealing once all (reachable)
//! predecessors are processed; all tree walks are explicit stacks (invariant
//! §3.4); hash maps are key-lookup only. Same MIR in, same MIR out.

use std::collections::HashMap;

use crate::analysis::{Analyses, BitSet};
use crate::mir::{
    BlockId, BlockRef, IndexRef, Inst, Mir, MirBlock, Place, TempId, Terminator, Value,
};
use crate::passes::Pass;
use crate::ssa::{SsaBuilder, Var};

/// The W2 Mem2Reg/SROA pass. See the module docs.
#[derive(Debug, Default, Clone, Copy)]
pub struct Mem2Reg;

impl Pass for Mem2Reg {
    fn name(&self) -> &'static str {
        "mem2reg"
    }

    fn run(&self, mir: &mut Mir, analyses: &mut Analyses) -> bool {
        let stats = run_mem2reg(mir);
        if stats.changed() {
            // Schedules, instructions, and (with phis or an entry split) the
            // CFG-level phi placement all change; drop everything.
            analyses.invalidate_all();
            true
        } else {
            false
        }
    }
}

/// Promotion outcome counters (per-CFG; aggregated by the corpus tests).
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct PromotionStats {
    /// Temp-table entries.
    pub temps_total: usize,
    /// Temps with at least one access in reachable code.
    pub temps_accessed: usize,
    /// Temps fully promoted to SSA values.
    pub promoted: usize,
    /// Refusals: some access has a dynamic index.
    pub refused_dynamic_index: usize,
    /// Refusals: some constant-index access is out of `[0, size)` (includes
    /// size-0 temps).
    pub refused_out_of_bounds: usize,
    /// Refusals: a load is statically reachable before a dominating store.
    pub refused_read_before_write: usize,
    /// Eager loads replaced by reaching definitions.
    pub loads_replaced: usize,
    /// Eager stores removed.
    pub stores_removed: usize,
    /// Lazy-tree loads rewritten in place to constants.
    pub lazy_loads_const: usize,
    /// Lazy-tree loads rerouted through a fresh `m2rN` temp.
    pub lazy_loads_rerouted: usize,
    /// Phis surviving after construction (trivial phis are removed on the fly).
    pub phis_created: usize,
    /// Whether the entry block was split (it had predecessors).
    pub entry_split: bool,
}

impl PromotionStats {
    /// Whether the pass mutated the MIR.
    pub fn changed(&self) -> bool {
        self.loads_replaced > 0
            || self.stores_removed > 0
            || self.lazy_loads_const > 0
            || self.lazy_loads_rerouted > 0
            || self.entry_split
    }
}

/// The promotion *analysis* only (no mutation): which temps would be promoted
/// and why others are refused. Used by tests and corpus reporting.
pub fn promotion_stats(mir: &Mir) -> PromotionStats {
    let reachable = reachable_mask(mir);
    let (mut stats, _promo) = analyze(mir, &reachable);
    stats.temps_total = mir.temps.len();
    stats
}

/// Runs the full pass over `mir`, returning the stats. Public so tests and
/// the corpus effectiveness report can drive it directly.
pub fn run_mem2reg(mir: &mut Mir) -> PromotionStats {
    if mir.blocks.is_empty() {
        return PromotionStats::default();
    }
    let reachable = reachable_mask(mir);
    let (mut stats, promo) = analyze(mir, &reachable);
    stats.temps_total = mir.temps.len();
    if stats.promoted == 0 {
        // No mutation at all (the entry split below is promotion-only).
        return stats;
    }
    // Normalize: Braun construction (and the read-before-write corollary in
    // the module docs) wants an entry block without predecessors. The split
    // does not change promotion decisions (it only relocates the entry's
    // content), so the analysis above stays valid; only reachability ids do.
    stats.entry_split = split_looping_entry(mir);
    let reachable = if stats.entry_split {
        reachable_mask(mir)
    } else {
        reachable
    };

    let phis_before: usize = mir.blocks.iter().map(|b| b.phis.len()).sum();
    rewrite(mir, &reachable, &promo, &mut stats);
    let phis_after: usize = mir.blocks.iter().map(|b| b.phis.len()).sum();
    stats.phis_created = phis_after.saturating_sub(phis_before);
    stats
}

// ----------------------------------------------------------------------------------
// CFG helpers
// ----------------------------------------------------------------------------------

/// Reachability from the entry block.
fn reachable_mask(mir: &Mir) -> Vec<bool> {
    let mut reachable = vec![false; mir.blocks.len()];
    if mir.blocks.is_empty() {
        return reachable;
    }
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
    reachable
}

/// Reverse postorder over reachable blocks, successors in edge-sorted order
/// (same traversal as lowering). Iterative.
fn reverse_postorder(mir: &Mir) -> Vec<BlockId> {
    let mut visited = vec![false; mir.blocks.len()];
    visited[0] = true;
    let mut postorder: Vec<BlockId> = Vec::new();
    let mut stack: Vec<(BlockId, usize)> = vec![(0, 0)];
    while let Some(&mut (block, ref mut next)) = stack.last_mut() {
        let succ = mir.blocks[block].terminator.successors().nth(*next);
        *next += 1;
        if let Some(dst) = succ {
            if !visited[dst] {
                visited[dst] = true;
                stack.push((dst, 0));
            }
        } else {
            postorder.push(block);
            stack.pop();
        }
    }
    postorder.reverse();
    postorder
}

/// If the entry block has predecessors (a loop back to block 0), moves its
/// content into a fresh block and leaves block 0 as a bare `Jump` to it, so
/// the entry has no predecessors (module docs). Returns whether it fired.
fn split_looping_entry(mir: &mut Mir) -> bool {
    let entry_has_pred = mir
        .blocks
        .iter()
        .any(|b| b.terminator.successors().any(|s| s == 0));
    if !entry_has_pred {
        return false;
    }
    let moved = mir.blocks.len();
    let old_entry = std::mem::take(&mut mir.blocks[0]);
    mir.blocks.push(old_entry);
    // Retarget every edge that pointed at the entry (including the moved
    // block's own back edge).
    for block in &mut mir.blocks {
        match &mut block.terminator {
            Terminator::Jump(t) => {
                if *t == 0 {
                    *t = moved;
                }
            }
            Terminator::Branch { cases, default, .. } => {
                for (_, t) in cases.iter_mut() {
                    if *t == 0 {
                        *t = moved;
                    }
                }
                if *default == Some(0) {
                    *default = Some(moved);
                }
            }
            Terminator::Exit => {}
        }
    }
    mir.blocks[0] = MirBlock {
        phis: Vec::new(),
        insts: Vec::new(),
        terminator: Terminator::Jump(moved),
    };
    // Phi args keyed by the old entry now come from the moved block
    // (defensive: pipeline MIR has no phis before this pass).
    for b in 0..mir.blocks.len() {
        for pi in 0..mir.blocks[b].phis.len() {
            let phi = mir.blocks[b].phis[pi];
            if let Inst::Phi { args } = &mut mir.insts[phi as usize] {
                for (p, _) in args.iter_mut() {
                    if *p == 0 {
                        *p = moved;
                    }
                }
            }
        }
    }
    true
}

// ----------------------------------------------------------------------------------
// Escape analysis + read-before-write refusal
// ----------------------------------------------------------------------------------

/// Why a temp is not promoted (priority order: dynamic > out-of-bounds >
/// read-before-write; one count per temp).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
enum Refusal {
    None,
    ReadBeforeWrite,
    OutOfBounds,
    Dynamic,
}

/// Promotion decisions: per-temp flag plus the dense `Var` numbering of
/// promoted slots (`var_base[t] + slot`).
struct Promotion {
    promoted: Vec<bool>,
    var_base: Vec<u32>,
}

impl Promotion {
    /// The `Var` for a place iff it is a promoted, in-bounds constant-index
    /// temp access.
    fn place_var(&self, place: &Place) -> Option<Var> {
        let BlockRef::Temp(t) = place.block else {
            return None;
        };
        if !self.promoted[t] {
            return None;
        }
        let IndexRef::Const(i) = place.index else {
            // The escape analysis refused dynamic-index temps.
            return None;
        };
        let slot = i.checked_add(place.offset)?;
        let slot = u32::try_from(slot).ok()?;
        Some(Var(self.var_base[t] + slot))
    }
}

/// The effective slot of a constant-index temp access, or `None` when dynamic.
fn const_slot(place: &Place) -> Option<i64> {
    match place.index {
        IndexRef::Const(i) => i.checked_add(place.offset),
        IndexRef::Value(_) => None,
    }
}

/// Walks a lazy rhs tree, calling `f` for every owned (unscheduled) `Load`
/// or (out-of-contract, defensive) `Store`. Stops at constants and at
/// scheduled values. Iterative.
fn for_each_lazy_access(mir: &Mir, scheduled: &[bool], rhs: Value, mut f: impl FnMut(Value)) {
    let mut stack = vec![rhs];
    while let Some(v) = stack.pop() {
        if mir.is_const(v) || scheduled.get(v as usize).copied().unwrap_or(false) {
            continue;
        }
        match mir.inst(v) {
            Inst::ConstInt(_) | Inst::ConstFloat(_) | Inst::Phi { .. } => {}
            Inst::Op { args, .. } => {
                for &a in args.iter().rev() {
                    stack.push(a);
                }
            }
            Inst::ShortCircuit { lhs, rhs, .. } => {
                stack.push(*rhs);
                stack.push(*lhs);
            }
            Inst::Load { place } => {
                f(v);
                // Dynamic components are themselves lazy nodes (nested places).
                if let IndexRef::Value(iv) = place.index {
                    stack.push(iv);
                }
                if let BlockRef::Value(bv) = place.block {
                    stack.push(bv);
                }
            }
            // A store inside a lazy tree is out of contract (build_mir rejects
            // it); surfaced to the caller, which refuses the temp.
            Inst::Store { place, value } => {
                f(v);
                if let IndexRef::Value(iv) = place.index {
                    stack.push(iv);
                }
                if let BlockRef::Value(bv) = place.block {
                    stack.push(bv);
                }
                stack.push(*value);
            }
        }
    }
}

/// Escape analysis + read-before-write refusal over reachable blocks.
/// Returns (stats with the refusal counters filled, promotion decisions).
#[allow(clippy::too_many_lines)] // one linear analysis: escape scan, must-store fixpoint, violation scan
fn analyze(mir: &Mir, reachable: &[bool]) -> (PromotionStats, Promotion) {
    let scheduled = mir.scheduled_mask();
    let n_temps = mir.temps.len();
    let mut accessed = vec![false; n_temps];
    let mut refusal = vec![Refusal::None; n_temps];

    let classify = |place: &Place, refusal: &mut Vec<Refusal>, accessed: &mut Vec<bool>| {
        let BlockRef::Temp(t) = place.block else {
            return;
        };
        accessed[t] = true;
        match place.index {
            IndexRef::Value(_) => refusal[t] = refusal[t].max(Refusal::Dynamic),
            IndexRef::Const(i) => {
                let in_bounds = i
                    .checked_add(place.offset)
                    .and_then(|s| u64::try_from(s).ok())
                    .is_some_and(|s| s < mir.temps[t].size);
                if !in_bounds {
                    refusal[t] = refusal[t].max(Refusal::OutOfBounds);
                }
            }
        }
    };

    for (b, block) in mir.blocks.iter().enumerate() {
        if !reachable[b] {
            continue;
        }
        for &v in &block.insts {
            match mir.inst(v) {
                Inst::Load { place } | Inst::Store { place, .. } => {
                    classify(place, &mut refusal, &mut accessed);
                }
                Inst::ShortCircuit { rhs, .. } => {
                    for_each_lazy_access(mir, &scheduled, *rhs, |access| match mir.inst(access) {
                        Inst::Load { place } => classify(place, &mut refusal, &mut accessed),
                        Inst::Store { place, .. } => {
                            // Out-of-contract lazy store: refuse the temp
                            // outright (conditional writes cannot be promoted).
                            if let BlockRef::Temp(t) = place.block {
                                accessed[t] = true;
                                refusal[t] = Refusal::Dynamic;
                            }
                        }
                        _ => unreachable!("for_each_lazy_access yields loads/stores"),
                    });
                }
                _ => {}
            }
        }
    }

    // Candidate slot numbering (escape-free temps only).
    let mut candidate = vec![false; n_temps];
    let mut slot_base = vec![0u32; n_temps];
    let mut n_slots = 0u32;
    for t in 0..n_temps {
        if accessed[t] && refusal[t] == Refusal::None {
            candidate[t] = true;
            slot_base[t] = n_slots;
            n_slots += u32::try_from(mir.temps[t].size).unwrap_or(u32::MAX);
        }
    }

    if n_slots > 0 {
        // Per-block must-store gen sets.
        let slot_of = |place: &Place| -> Option<usize> {
            let BlockRef::Temp(t) = place.block else {
                return None;
            };
            if !candidate[t] {
                return None;
            }
            let s = const_slot(place).expect("candidate accesses are constant-index");
            usize::try_from(s).ok().map(|s| slot_base[t] as usize + s)
        };
        let mut store_sets: Vec<BitSet> = Vec::with_capacity(mir.blocks.len());
        for block in &mir.blocks {
            let mut set = BitSet::new(n_slots as usize);
            for &v in &block.insts {
                if let Inst::Store { place, .. } = mir.inst(v)
                    && let Some(s) = slot_of(place)
                {
                    set.insert(s);
                }
            }
            store_sets.push(set);
        }
        // Forward must-be-stored fixpoint: in[entry] = ∅; in[b] = ⋂ preds
        // out[p]; `None` = ⊤ (unvisited). Monotone decreasing; terminates.
        let mut in_sets: Vec<Option<BitSet>> = vec![None; mir.blocks.len()];
        in_sets[0] = Some(BitSet::new(n_slots as usize));
        let mut work: Vec<BlockId> = vec![0];
        while let Some(b) = work.pop() {
            let mut out = in_sets[b].clone().expect("queued blocks have in-sets");
            out.union_with(&store_sets[b]);
            for s in mir.blocks[b].terminator.successors() {
                let changed = match &mut in_sets[s] {
                    None => {
                        in_sets[s] = Some(out.clone());
                        true
                    }
                    Some(cur) => cur.intersect_with(&out),
                };
                if changed {
                    work.push(s);
                }
            }
        }
        // Violation scan: loads (eager, and lazy at their owner's point)
        // against the running in-block state.
        for (b, block) in mir.blocks.iter().enumerate() {
            if !reachable[b] {
                continue;
            }
            let mut state = in_sets[b]
                .clone()
                .expect("reachable blocks were visited by the fixpoint");
            for &v in &block.insts {
                match mir.inst(v) {
                    Inst::Load { place } => {
                        if let Some(s) = slot_of(place)
                            && !state.contains(s)
                            && let BlockRef::Temp(t) = place.block
                        {
                            refusal[t] = refusal[t].max(Refusal::ReadBeforeWrite);
                        }
                    }
                    Inst::Store { place, .. } => {
                        if let Some(s) = slot_of(place) {
                            state.insert(s);
                        }
                    }
                    Inst::ShortCircuit { rhs, .. } => {
                        for_each_lazy_access(mir, &scheduled, *rhs, |access| {
                            // Lazy stores were refused above; only loads matter.
                            let Inst::Load { place } = mir.inst(access) else {
                                return;
                            };
                            if let Some(s) = slot_of(place)
                                && !state.contains(s)
                                && let BlockRef::Temp(t) = place.block
                            {
                                refusal[t] = refusal[t].max(Refusal::ReadBeforeWrite);
                            }
                        });
                    }
                    _ => {}
                }
            }
        }
    }

    // Final decisions + dense Var numbering.
    let mut stats = PromotionStats {
        temps_total: n_temps,
        ..PromotionStats::default()
    };
    let mut promoted = vec![false; n_temps];
    let mut var_base = vec![0u32; n_temps];
    let mut n_vars = 0u32;
    for t in 0..n_temps {
        if !accessed[t] {
            continue;
        }
        stats.temps_accessed += 1;
        match refusal[t] {
            Refusal::None => {
                promoted[t] = true;
                var_base[t] = n_vars;
                n_vars += u32::try_from(mir.temps[t].size).expect("temp sizes fit u32");
                stats.promoted += 1;
            }
            Refusal::Dynamic => stats.refused_dynamic_index += 1,
            Refusal::OutOfBounds => stats.refused_out_of_bounds += 1,
            Refusal::ReadBeforeWrite => stats.refused_read_before_write += 1,
        }
    }
    (stats, Promotion { promoted, var_base })
}

// ----------------------------------------------------------------------------------
// Rewrite
// ----------------------------------------------------------------------------------

fn temp_place(t: TempId) -> Place {
    Place {
        block: BlockRef::Temp(t),
        index: IndexRef::Const(0),
        offset: 0,
    }
}

/// The Braun-driven rewrite walk (module docs).
#[allow(clippy::too_many_lines)] // one linear walk + fixups
fn rewrite(mir: &mut Mir, reachable: &[bool], promo: &Promotion, stats: &mut PromotionStats) {
    let scheduled = mir.scheduled_mask();
    let n_blocks = mir.blocks.len();

    // Braun builder over reachable predecessor edges only.
    let mut ssa = SsaBuilder::new(n_blocks);
    let preds = mir.predecessors();
    let mut remaining = vec![0usize; n_blocks];
    for (b, ps) in preds.iter().enumerate() {
        for &p in ps {
            if reachable[p] && reachable[b] {
                ssa.add_pred(b, p);
                remaining[b] += 1;
            }
        }
    }
    let mut sealed = vec![false; n_blocks];
    for b in 0..n_blocks {
        if remaining[b] == 0 {
            // The entry (no preds after the split) and unreachable blocks.
            ssa.seal_block(mir, b);
            sealed[b] = true;
        }
    }

    // Replaced eager loads -> reaching values (applied in one global fixup).
    let mut replacement: HashMap<Value, Value> = HashMap::new();
    let resolve_local = |v: Value, replacement: &HashMap<Value, Value>| -> Value {
        let mut v = v;
        while let Some(&n) = replacement.get(&v) {
            v = n;
        }
        v
    };
    let mut m2r_counter = 0usize;

    for b in reverse_postorder(mir) {
        let old = std::mem::take(&mut mir.blocks[b].insts);
        let mut new: Vec<Value> = Vec::with_capacity(old.len());
        for v in old {
            match mir.inst(v).clone() {
                Inst::Load { place } if promo.place_var(&place).is_some() => {
                    let var = promo.place_var(&place).expect("checked");
                    let val = ssa.read_variable(mir, var, b);
                    replacement.insert(v, val);
                    stats.loads_replaced += 1;
                }
                Inst::Store { place, value } if promo.place_var(&place).is_some() => {
                    let var = promo.place_var(&place).expect("checked");
                    let val = resolve_local(value, &replacement);
                    ssa.write_variable(var, b, val);
                    stats.stores_removed += 1;
                }
                Inst::ShortCircuit { rhs, .. } => {
                    // Rewrite promoted lazy loads in place; m2r stores go
                    // directly before the owner (module docs). Loads that are
                    // referenced as *place components* of other lazy accesses
                    // must stay `Load`-shaped (the lowering place grammar
                    // nests only places), so they always take the m2r route.
                    let mut targets: Vec<Value> = Vec::new();
                    let mut component_refs: Vec<Value> = Vec::new();
                    for_each_lazy_access(mir, &scheduled, rhs, |access| {
                        let (Inst::Load { place } | Inst::Store { place, .. }) = mir.inst(access)
                        else {
                            unreachable!("for_each_lazy_access yields loads/stores");
                        };
                        if let BlockRef::Value(bv) = place.block {
                            component_refs.push(bv);
                        }
                        if let IndexRef::Value(iv) = place.index {
                            component_refs.push(iv);
                        }
                        if matches!(mir.inst(access), Inst::Load { .. })
                            && promo.place_var(place).is_some()
                        {
                            targets.push(access);
                        }
                    });
                    let mut value_temp: Vec<(Value, TempId)> = Vec::new();
                    for load in targets {
                        let Inst::Load { place } = mir.inst(load).clone() else {
                            unreachable!("targets are loads");
                        };
                        let var = promo.place_var(&place).expect("targets are promoted");
                        let val = ssa.read_variable(mir, var, b);
                        if mir.is_const(val) && !component_refs.contains(&load) {
                            let const_copy = mir.inst(val).clone();
                            mir.insts[load as usize] = const_copy;
                            stats.lazy_loads_const += 1;
                        } else {
                            let tmp = if let Some(&(_, t)) =
                                value_temp.iter().find(|&&(v2, _)| v2 == val)
                            {
                                t
                            } else {
                                let t = mir.push_temp(format!("m2r{m2r_counter}"), 1);
                                m2r_counter += 1;
                                let store = mir.push_inst(Inst::Store {
                                    place: temp_place(t),
                                    value: val,
                                });
                                new.push(store);
                                value_temp.push((val, t));
                                t
                            };
                            mir.insts[load as usize] = Inst::Load {
                                place: temp_place(tmp),
                            };
                            stats.lazy_loads_rerouted += 1;
                        }
                    }
                    new.push(v);
                }
                _ => new.push(v),
            }
        }
        mir.blocks[b].insts = new;
        // Seal successors whose predecessors are now all processed.
        let mut succs: Vec<BlockId> = mir.blocks[b].terminator.successors().collect();
        succs.sort_unstable();
        succs.dedup();
        for s in succs {
            if reachable[s] {
                remaining[s] -= 1;
                if remaining[s] == 0 && !sealed[s] {
                    ssa.seal_block(mir, s);
                    sealed[s] = true;
                }
            }
        }
    }
    debug_assert!(
        sealed.iter().enumerate().all(|(b, &s)| s || !reachable[b]),
        "every reachable block must be sealed after the walk"
    );
    let unsealed: Vec<BlockId> = sealed
        .iter()
        .enumerate()
        .filter(|&(_, &s)| !s)
        .map(|(b, _)| b)
        .collect();
    for b in unsealed {
        ssa.seal_block(mir, b);
    }

    // Global fixup: every reference to a removed load now uses the reaching
    // value. Plain value operands (op args, store values, ShortCircuit
    // lhs/rhs, phi args, terminator tests) take the value directly. Dynamic
    // *place components* must keep the lowering place grammar (a component
    // lowers to a nested `Get` or a constant `IndexRef::Const` /
    // `BlockRef::Concrete`): convertible constants become constant
    // components (the original load's runtime value was this constant, so
    // behavior is identical); load-defined values stay `Load`-shaped;
    // everything else (phis, ops, unconvertible constants — which must keep
    // their runtime `ensure_int` trap) is materialized through an m2r temp
    // directly before the accessing instruction, the exact memory shape the
    // unpromoted form had.
    if !replacement.is_empty() {
        for b in 0..n_blocks {
            let old = std::mem::take(&mut mir.blocks[b].insts);
            let mut new: Vec<Value> = Vec::with_capacity(old.len());
            for v in old {
                let mut inst = mir.insts[v as usize].clone();
                let mut changed = false;
                if let Inst::Load { place } | Inst::Store { place, .. } = &mut inst {
                    for is_block in [true, false] {
                        let comp = if is_block {
                            match place.block {
                                BlockRef::Value(cv) => Some(cv),
                                _ => None,
                            }
                        } else {
                            match place.index {
                                IndexRef::Value(cv) => Some(cv),
                                IndexRef::Const(_) => None,
                            }
                        };
                        let Some(cv) = comp else { continue };
                        let r = resolve_local(cv, &replacement);
                        if r == cv {
                            continue;
                        }
                        changed = true;
                        if let Some(i) = place_const(mir, r) {
                            if is_block {
                                place.block = BlockRef::Concrete(i);
                            } else {
                                place.index = IndexRef::Const(i);
                            }
                            continue;
                        }
                        let fixed = if matches!(mir.inst(r), Inst::Load { .. }) {
                            r
                        } else {
                            let t = mir.push_temp(format!("m2r{m2r_counter}"), 1);
                            m2r_counter += 1;
                            let store = mir.push_inst(Inst::Store {
                                place: temp_place(t),
                                value: r,
                            });
                            new.push(store);
                            let load = mir.push_inst(Inst::Load {
                                place: temp_place(t),
                            });
                            new.push(load);
                            load
                        };
                        if is_block {
                            place.block = BlockRef::Value(fixed);
                        } else {
                            place.index = IndexRef::Value(fixed);
                        }
                    }
                }
                // Plain value operands (skips the place components already
                // handled above: their values are never replacement keys).
                Mir::for_each_operand_mut(&mut inst, |o| {
                    let r = resolve_local(*o, &replacement);
                    if r != *o {
                        *o = r;
                        changed = true;
                    }
                });
                if changed {
                    mir.insts[v as usize] = inst;
                }
                new.push(v);
            }
            mir.blocks[b].insts = new;
            // Phi args (defensive: pre-existing phis) and terminator tests.
            for pi in 0..mir.blocks[b].phis.len() {
                let phi = mir.blocks[b].phis[pi];
                if let Inst::Phi { args } = &mut mir.insts[phi as usize] {
                    for (_, a) in args.iter_mut() {
                        *a = resolve_local(*a, &replacement);
                    }
                }
            }
            if let Terminator::Branch { test, .. } = &mut mir.blocks[b].terminator {
                *test = resolve_local(*test, &replacement);
            }
        }
    }

    // Resolve trivial-phi forwarding everywhere and drop dead phis.
    ssa.finish(mir);
}

/// A constant value legal as a constant place component (mirrors SCCP's
/// `to_place_i64`): integral and within ±2^48 so emitter offset arithmetic
/// cannot overflow. Anything else must stay dynamic (preserves the runtime
/// `ensure_int`/range trap).
#[allow(clippy::cast_possible_truncation, clippy::float_cmp)]
fn place_const(mir: &Mir, v: Value) -> Option<i64> {
    const BOUND: i64 = 281_474_976_710_656; // 2^48
    match *mir.inst(v) {
        Inst::ConstInt(i) if (-BOUND..=BOUND).contains(&i) => Some(i),
        Inst::ConstFloat(f) => {
            #[allow(clippy::cast_precision_loss)]
            let bound = BOUND as f64;
            (f.is_finite() && f.trunc() == f && (-bound..=bound).contains(&f)).then_some(f as i64)
        }
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    // Toolchain note: clippy 1.96 newly lints test code under --all-targets;
    // exact f64 equality is the assertion contract here (ARCHITECTURE §6).
    // terse local names are the test-builder convention in this module.
    #![allow(clippy::float_cmp, clippy::similar_names)]
    use super::*;
    use crate::cfg::{
        BasicBlock, BlockValue, Cfg, Edge, EdgeCond, IndexValue, Node, Place as CfgPlace,
        TempBlockDef,
    };
    use crate::diff::{DiffConfig, DiffOutcome, diff_with};
    use crate::mir::{CaseCond, build_mir};
    use crate::ops::Op;
    use crate::passes::Pipeline;
    use crate::pipeline::{Level, compile_cfg, compile_cfg_with_pipeline};

    fn mem2reg_pipeline() -> Pipeline {
        Pipeline::new(vec![Box::new(Mem2Reg)])
    }

    fn run_pass(mir: &mut Mir) -> bool {
        // Through a Pipeline so the debug changed-flag guard is active.
        mem2reg_pipeline().run(mir, &mut Analyses::new())
    }

    /// Asserts minimal and minimal+[`Mem2Reg`] behave identically on a frontend
    /// CFG (two memory seeds).
    fn assert_diff_match(cfg: &Cfg) {
        for seed in [0x5EED_0001u64, 0x5EED_0002] {
            let config = DiffConfig {
                memory_seed: seed,
                rng_seed: seed.rotate_left(11) ^ 0xACE,
                eval_budget: 100_000,
            };
            let outcome = diff_with(
                cfg,
                |c| compile_cfg(c, Level::Minimal),
                |c| compile_cfg_with_pipeline(c, &mem2reg_pipeline()),
                &config,
            );
            assert_eq!(outcome, DiffOutcome::Match, "seed {seed:#x}");
        }
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

    fn temp_slot(t: TempId, index: i64) -> Place {
        Place {
            block: BlockRef::Temp(t),
            index: IndexRef::Const(index),
            offset: 0,
        }
    }

    fn temp_accesses(mir: &Mir, t: TempId) -> usize {
        mir.blocks
            .iter()
            .flat_map(|b| &b.insts)
            .filter(|&&v| match mir.inst(v) {
                Inst::Load { place } | Inst::Store { place, .. } => {
                    place.block == BlockRef::Temp(t)
                }
                _ => false,
            })
            .count()
    }

    // ------------------------- straight-line promotion -------------------------

    #[test]
    fn straight_line_store_load_promotes() {
        // t[0] <- 7; 20[0] <- load t[0]  =>  20[0] <- 7 (no temp access left).
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 1);
        let b0 = mir.push_block();
        let c = mir.push_inst(Inst::ConstInt(7));
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: temp_slot(t, 0),
                value: c,
            },
        );
        let load = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: temp_slot(t, 0),
            },
        );
        let out = sched(
            &mut mir,
            b0,
            Inst::Store {
                place: concrete_place(20, 0),
                value: load,
            },
        );
        assert!(run_pass(&mut mir));
        assert_eq!(temp_accesses(&mir, t), 0, "temp fully promoted");
        let Inst::Store { value, .. } = mir.inst(out) else {
            panic!("out store survives");
        };
        assert_eq!(mir.inst(*value), &Inst::ConstInt(7));
        assert_eq!(mir.blocks[b0].insts, vec![out], "store + load removed");
    }

    #[test]
    fn multi_slot_temp_promotes_per_slot() {
        // t[0] <- 1; t[1] <- 2; 20[0] <- t[1]; 20[1] <- t[0] (offset form).
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 2);
        let b0 = mir.push_block();
        let c1 = mir.push_inst(Inst::ConstInt(1));
        let c2 = mir.push_inst(Inst::ConstInt(2));
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: temp_slot(t, 0),
                value: c1,
            },
        );
        // Slot 1 via index 0 + offset 1 (offset folds into the slot).
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: Place {
                    block: BlockRef::Temp(t),
                    index: IndexRef::Const(0),
                    offset: 1,
                },
                value: c2,
            },
        );
        let l1 = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: temp_slot(t, 1),
            },
        );
        let s1 = sched(
            &mut mir,
            b0,
            Inst::Store {
                place: concrete_place(20, 0),
                value: l1,
            },
        );
        let l0 = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: temp_slot(t, 0),
            },
        );
        let s0 = sched(
            &mut mir,
            b0,
            Inst::Store {
                place: concrete_place(20, 1),
                value: l0,
            },
        );
        assert!(run_pass(&mut mir));
        assert_eq!(temp_accesses(&mir, t), 0);
        let Inst::Store { value, .. } = mir.inst(s1) else {
            panic!()
        };
        assert_eq!(mir.inst(*value), &Inst::ConstInt(2), "slot 1 reads c2");
        let Inst::Store { value, .. } = mir.inst(s0) else {
            panic!()
        };
        assert_eq!(mir.inst(*value), &Inst::ConstInt(1), "slot 0 reads c1");
    }

    // ------------------------- diamond / loop phis -------------------------

    /// 0 -> {1, 2} -> 3; t written differently in 1/2, read in 3.
    fn diamond_mir() -> (Mir, TempId, Value, Value) {
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 1);
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let b2 = mir.push_block();
        let b3 = mir.push_block();
        let test = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(-3, 0),
            },
        );
        mir.blocks[b0].terminator = Terminator::Branch {
            test,
            cases: vec![(CaseCond::Int(0), b1)],
            default: Some(b2),
        };
        mir.blocks[b1].terminator = Terminator::Jump(b3);
        mir.blocks[b2].terminator = Terminator::Jump(b3);
        let c10 = mir.push_inst(Inst::ConstInt(10));
        let c20 = mir.push_inst(Inst::ConstInt(20));
        sched(
            &mut mir,
            b1,
            Inst::Store {
                place: temp_slot(t, 0),
                value: c10,
            },
        );
        sched(
            &mut mir,
            b2,
            Inst::Store {
                place: temp_slot(t, 0),
                value: c20,
            },
        );
        let load = sched(
            &mut mir,
            b3,
            Inst::Load {
                place: temp_slot(t, 0),
            },
        );
        sched(
            &mut mir,
            b3,
            Inst::Store {
                place: concrete_place(20, 0),
                value: load,
            },
        );
        (mir, t, c10, c20)
    }

    #[test]
    fn diamond_promotion_places_one_phi_at_the_join() {
        let (mut mir, t, c10, c20) = diamond_mir();
        assert!(run_pass(&mut mir));
        assert_eq!(temp_accesses(&mir, t), 0);
        // Exactly one phi, at block 3, with hand-computed args.
        let phis: Vec<(usize, Value)> = mir
            .blocks
            .iter()
            .enumerate()
            .flat_map(|(b, blk)| blk.phis.iter().map(move |&p| (b, p)))
            .collect();
        assert_eq!(phis.len(), 1, "one join phi");
        let (b, phi) = phis[0];
        assert_eq!(b, 3);
        let Inst::Phi { args } = mir.inst(phi) else {
            panic!()
        };
        let mut got = args.clone();
        got.sort_unstable();
        assert_eq!(got, vec![(1, c10), (2, c20)]);
        // The out store now uses the phi.
        let store = *mir.blocks[3].insts.last().expect("store kept");
        let Inst::Store { value, .. } = mir.inst(store) else {
            panic!()
        };
        assert_eq!(*value, phi);
    }

    #[test]
    fn same_value_diamond_needs_no_phi() {
        // Both arms store the same constant: trivial phi removed on the fly.
        let (mut mir, _, _, _) = diamond_mir();
        // Rewrite block 2's store to use c10's value too.
        let c10 = {
            let Inst::Store { value, .. } = mir.inst(mir.blocks[1].insts[0]) else {
                panic!()
            };
            *value
        };
        let s2 = mir.blocks[2].insts[0];
        let Inst::Store { value, .. } = &mut mir.insts[s2 as usize] else {
            panic!()
        };
        *value = c10;
        assert!(run_pass(&mut mir));
        assert!(
            mir.blocks.iter().all(|b| b.phis.is_empty()),
            "no phi needed"
        );
    }

    #[test]
    fn loop_carried_variable_gets_header_phi() {
        // 0: t <- 0; jump 1. 1: l = t; n = Add(l, 1); t <- n; branch(in0) {0:2, d:1}.
        // 2: out <- t. The header (1) needs a loop-carried phi.
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 1);
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let b2 = mir.push_block();
        mir.blocks[b0].terminator = Terminator::Jump(b1);
        let zero = mir.push_inst(Inst::ConstInt(0));
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: temp_slot(t, 0),
                value: zero,
            },
        );
        let load = sched(
            &mut mir,
            b1,
            Inst::Load {
                place: temp_slot(t, 0),
            },
        );
        let one = mir.push_inst(Inst::ConstInt(1));
        let add = sched(
            &mut mir,
            b1,
            Inst::Op {
                op: Op::Add,
                pure_node: true,
                args: vec![load, one],
            },
        );
        sched(
            &mut mir,
            b1,
            Inst::Store {
                place: temp_slot(t, 0),
                value: add,
            },
        );
        let test = sched(
            &mut mir,
            b1,
            Inst::Load {
                place: concrete_place(-3, 0),
            },
        );
        mir.blocks[b1].terminator = Terminator::Branch {
            test,
            cases: vec![(CaseCond::Int(0), b2)],
            default: Some(b1),
        };
        let after = sched(
            &mut mir,
            b2,
            Inst::Load {
                place: temp_slot(t, 0),
            },
        );
        let out = sched(
            &mut mir,
            b2,
            Inst::Store {
                place: concrete_place(20, 0),
                value: after,
            },
        );
        assert!(run_pass(&mut mir));
        assert_eq!(temp_accesses(&mir, t), 0);
        assert_eq!(mir.blocks[b1].phis.len(), 1, "loop-carried phi at header");
        let phi = mir.blocks[b1].phis[0];
        let Inst::Phi { args } = mir.inst(phi) else {
            panic!()
        };
        let mut got = args.clone();
        got.sort_unstable();
        assert_eq!(got, vec![(b0, zero), (b1, add)]);
        // The Add consumes the phi; the after-loop store consumes the Add.
        let Inst::Op { args, .. } = mir.inst(add) else {
            panic!()
        };
        assert_eq!(args[0], phi);
        let Inst::Store { value, .. } = mir.inst(out) else {
            panic!()
        };
        assert_eq!(*value, add);
    }

    #[test]
    fn looping_entry_is_split_before_promotion() {
        // Entry stores then loops back to itself via a branch: the entry is
        // split so Braun sees a pred-free entry; promotion still works.
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 1);
        let b0 = mir.push_block();
        let c = mir.push_inst(Inst::ConstInt(3));
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: temp_slot(t, 0),
                value: c,
            },
        );
        let load = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: temp_slot(t, 0),
            },
        );
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: concrete_place(20, 0),
                value: load,
            },
        );
        let test = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(-3, 0),
            },
        );
        let b1 = mir.push_block();
        mir.blocks[b0].terminator = Terminator::Branch {
            test,
            cases: vec![(CaseCond::Int(0), b1)],
            default: Some(b0),
        };
        assert!(run_pass(&mut mir));
        assert_eq!(temp_accesses(&mir, t), 0, "promoted despite looping entry");
        assert!(
            mir.blocks[0].insts.is_empty(),
            "entry content moved to the split block"
        );
    }

    // ------------------------- escape conditions -------------------------

    #[test]
    fn dynamic_index_refuses_whole_temp_but_not_others() {
        // t (size 4) has one dynamic access -> entirely memory; u promotes.
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 4);
        let u = mir.push_temp("u", 1);
        let b0 = mir.push_block();
        let c = mir.push_inst(Inst::ConstInt(9));
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: temp_slot(t, 0),
                value: c,
            },
        );
        let cu = mir.push_inst(Inst::ConstInt(5));
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: temp_slot(u, 0),
                value: cu,
            },
        );
        let idx = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(-3, 0),
            },
        );
        let dyn_load = sched(
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
                place: concrete_place(20, 0),
                value: dyn_load,
            },
        );
        let lu = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: temp_slot(u, 0),
            },
        );
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: concrete_place(20, 1),
                value: lu,
            },
        );
        let stats = run_mem2reg(&mut mir);
        assert_eq!(stats.promoted, 1);
        assert_eq!(stats.refused_dynamic_index, 1);
        assert_eq!(temp_accesses(&mir, t), 2, "t stays memory entirely");
        assert_eq!(temp_accesses(&mir, u), 0, "u promoted");
    }

    #[test]
    fn out_of_bounds_const_access_refuses() {
        // Load t[2] of a size-2 temp via offset: 1 + 1 = 2 >= size.
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 2);
        let b0 = mir.push_block();
        let c = mir.push_inst(Inst::ConstInt(1));
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: temp_slot(t, 0),
                value: c,
            },
        );
        let bad = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: Place {
                    block: BlockRef::Temp(t),
                    index: IndexRef::Const(1),
                    offset: 1,
                },
            },
        );
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: concrete_place(20, 0),
                value: bad,
            },
        );
        let stats = run_mem2reg(&mut mir);
        assert_eq!(stats.promoted, 0);
        assert_eq!(stats.refused_out_of_bounds, 1);
        assert!(temp_accesses(&mir, t) > 0);
    }

    #[test]
    fn temp_loaded_as_block_id_still_promotes() {
        // The "nested place" case: a promoted slot's value used as a dynamic
        // BLOCK id. The temp itself is not address-taken (BlockRef::Value
        // holds a value, never a TempId), so promotion applies and the
        // dynamic place's block ref is rewired to the stored value.
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 1);
        let b0 = mir.push_block();
        let c20 = mir.push_inst(Inst::ConstInt(20));
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: temp_slot(t, 0),
                value: c20,
            },
        );
        let bid = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: temp_slot(t, 0),
            },
        );
        let nested = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: Place {
                    block: BlockRef::Value(bid),
                    index: IndexRef::Const(0),
                    offset: 0,
                },
            },
        );
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: concrete_place(21, 0),
                value: nested,
            },
        );
        let stats = run_mem2reg(&mut mir);
        assert_eq!(stats.promoted, 1);
        assert_eq!(temp_accesses(&mir, t), 0);
        let _ = c20;
        let Inst::Load { place } = mir.inst(nested) else {
            panic!()
        };
        assert_eq!(
            place.block,
            BlockRef::Concrete(20),
            "constant reaching value becomes a concrete block component"
        );
    }

    // ------------------------- read-before-write -------------------------

    #[test]
    fn read_before_write_path_refuses() {
        // 0 -> {1 (stores), 2 (no store)} -> 3 (loads): the 0->2->3 path
        // reads t before any store.
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 1);
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let b2 = mir.push_block();
        let b3 = mir.push_block();
        let test = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(-3, 0),
            },
        );
        mir.blocks[b0].terminator = Terminator::Branch {
            test,
            cases: vec![(CaseCond::Int(0), b1)],
            default: Some(b2),
        };
        mir.blocks[b1].terminator = Terminator::Jump(b3);
        mir.blocks[b2].terminator = Terminator::Jump(b3);
        let c = mir.push_inst(Inst::ConstInt(1));
        sched(
            &mut mir,
            b1,
            Inst::Store {
                place: temp_slot(t, 0),
                value: c,
            },
        );
        let load = sched(
            &mut mir,
            b3,
            Inst::Load {
                place: temp_slot(t, 0),
            },
        );
        sched(
            &mut mir,
            b3,
            Inst::Store {
                place: concrete_place(20, 0),
                value: load,
            },
        );
        let stats = run_mem2reg(&mut mir);
        assert_eq!(stats.promoted, 0);
        assert_eq!(stats.refused_read_before_write, 1);
        assert!(temp_accesses(&mir, t) > 0, "temp untouched");
    }

    #[test]
    fn store_on_all_paths_is_not_refused() {
        let (mut mir, ..) = diamond_mir();
        let stats = run_mem2reg(&mut mir);
        assert_eq!(stats.refused_read_before_write, 0);
        assert_eq!(stats.promoted, 1);
    }

    #[test]
    fn load_before_store_in_same_block_refuses() {
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 1);
        let b0 = mir.push_block();
        let load = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: temp_slot(t, 0),
            },
        );
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: concrete_place(20, 0),
                value: load,
            },
        );
        let c = mir.push_inst(Inst::ConstInt(1));
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: temp_slot(t, 0),
                value: c,
            },
        );
        let stats = run_mem2reg(&mut mir);
        assert_eq!(stats.promoted, 0);
        assert_eq!(stats.refused_read_before_write, 1);
    }

    // ------------------------- lazy trees -------------------------

    #[test]
    fn lazy_load_of_promoted_slot_becomes_constant() {
        // t <- 7; out <- And(in0, t): the lazy load is rewritten to 7.
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 1);
        let b0 = mir.push_block();
        let c7 = mir.push_inst(Inst::ConstInt(7));
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: temp_slot(t, 0),
                value: c7,
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
            place: temp_slot(t, 0),
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
                place: concrete_place(20, 0),
                value: sc,
            },
        );
        let stats = run_mem2reg(&mut mir);
        assert_eq!(stats.promoted, 1);
        assert_eq!(stats.lazy_loads_const, 1);
        assert_eq!(mir.inst(lazy_load), &Inst::ConstInt(7), "in-place rewrite");
        assert_eq!(temp_accesses(&mir, t), 0);
    }

    #[test]
    fn lazy_load_of_nonconst_value_reroutes_through_m2r_temp() {
        // t <- (load -3[1]); out <- And(in0, t): the reaching def is not a
        // constant, so the lazy load reads a fresh m2r temp stored before the
        // owner.
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 1);
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
                place: temp_slot(t, 0),
                value: v,
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
            place: temp_slot(t, 0),
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
                place: concrete_place(20, 0),
                value: sc,
            },
        );
        let temps_before = mir.temps.len();
        let stats = run_mem2reg(&mut mir);
        assert_eq!(stats.promoted, 1);
        assert_eq!(stats.lazy_loads_rerouted, 1);
        assert_eq!(mir.temps.len(), temps_before + 1, "one m2r temp");
        let m2r = temps_before;
        // The lazy load now reads the m2r temp.
        let Inst::Load { place } = mir.inst(lazy_load) else {
            panic!()
        };
        assert_eq!(place.block, BlockRef::Temp(m2r));
        // A store to the m2r temp sits directly before the owner.
        let sc_pos = mir.blocks[b0]
            .insts
            .iter()
            .position(|&x| x == sc)
            .expect("owner scheduled");
        let before = mir.blocks[b0].insts[sc_pos - 1];
        let Inst::Store { place, value } = mir.inst(before) else {
            panic!("m2r store directly before the owner");
        };
        assert_eq!(place.block, BlockRef::Temp(m2r));
        assert_eq!(*value, v);
        // The original temp is gone from the schedule.
        assert_eq!(temp_accesses(&mir, t), 0);
    }

    // ------------------------- end-to-end + differential -------------------------

    /// Tiny frontend-CFG builder (mirror of the one in sccp.rs tests).
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
        fn temp_place(&mut self, t: usize, index: i64) -> usize {
            self.cfg.places.push(CfgPlace {
                block: BlockValue::Temp(t),
                index: IndexValue::Int(index),
                offset: 0,
            });
            self.cfg.places.len() - 1
        }
        fn int(&mut self, v: i64) -> usize {
            self.node(Node::ConstInt(v))
        }
        fn get(&mut self, place: usize) -> usize {
            self.node(Node::Get(place))
        }
        fn set(&mut self, place: usize, value: usize) -> usize {
            self.node(Node::Set { place, value })
        }
        fn pure(&mut self, op: Op, args: Vec<usize>) -> usize {
            self.node(Node::PureInstr { op, args })
        }
        fn block(&mut self, statements: Vec<usize>, test: usize, outgoing: Vec<Edge>) {
            self.cfg.blocks.push(BasicBlock {
                statements,
                test,
                outgoing,
            });
        }
    }

    /// in0-driven diamond writing through a temp, with a use after the join,
    /// plus a multi-use load (the value feeds two outputs).
    fn diamond_cfg() -> Cfg {
        let mut b = B::default();
        let t = b.temp("t", 1);
        let in0 = b.place_int(-3, 0);
        let test = b.get(in0);
        b.block(
            vec![],
            test,
            vec![edge(EdgeCond::Int(0), 1), edge(EdgeCond::None, 2)],
        );
        for (i, v) in [(1usize, 10i64), (2, 20)] {
            let tp = b.temp_place(t, 0);
            let c = b.int(v);
            let s = b.set(tp, c);
            let zt = b.int(0);
            b.block(vec![s], zt, vec![edge(EdgeCond::None, 3)]);
            let _ = i;
        }
        let tp = b.temp_place(t, 0);
        let g = b.get(tp);
        let out0 = b.place_int(20, 0);
        let s0 = b.set(out0, g);
        let tp2 = b.temp_place(t, 0);
        let g2 = b.get(tp2);
        let one = b.int(1);
        let plus = b.pure(Op::Add, vec![g2, one]);
        let out1 = b.place_int(20, 1);
        let s1 = b.set(out1, plus);
        let zt = b.int(0);
        b.block(vec![s0, s1], zt, vec![]);
        b.cfg
    }

    fn edge(cond: EdgeCond, target: usize) -> Edge {
        Edge { cond, target }
    }

    #[test]
    fn diamond_cfg_promotes_and_matches_minimal() {
        let cfg = diamond_cfg();
        let mut mir = build_mir(&cfg).unwrap();
        let stats = run_mem2reg(&mut mir);
        assert_eq!(stats.promoted, 1);
        assert!(stats.phis_created >= 1);
        assert_diff_match(&cfg);
    }

    #[test]
    fn loop_cfg_promotes_and_matches_minimal() {
        // i = 0; while (i < in0) { 20[i floor-clamped? no — plain] i = i + 1 }
        // out <- i. Bounded by the eval budget; in0 is randomized memory.
        let mut b = B::default();
        let t = b.temp("i", 1);
        let tp = b.temp_place(t, 0);
        let zero = b.int(0);
        let s = b.set(tp, zero);
        let zt0 = b.int(0);
        b.block(vec![s], zt0, vec![edge(EdgeCond::None, 1)]);
        // Header: branch Less(i, 5).
        let tp_r = b.temp_place(t, 0);
        let g = b.get(tp_r);
        let five = b.int(5);
        let less = b.pure(Op::Less, vec![g, five]);
        b.block(
            vec![],
            less,
            vec![edge(EdgeCond::Int(0), 3), edge(EdgeCond::None, 2)],
        );
        // Body: i = i + 1.
        let tp_r2 = b.temp_place(t, 0);
        let g2 = b.get(tp_r2);
        let one = b.int(1);
        let add = b.pure(Op::Add, vec![g2, one]);
        let tp_w = b.temp_place(t, 0);
        let s2 = b.set(tp_w, add);
        let zt2 = b.int(0);
        b.block(vec![s2], zt2, vec![edge(EdgeCond::None, 1)]);
        // After: out <- i.
        let tp_r3 = b.temp_place(t, 0);
        let g3 = b.get(tp_r3);
        let out = b.place_int(20, 0);
        let s3 = b.set(out, g3);
        let zt3 = b.int(0);
        b.block(vec![s3], zt3, vec![]);
        let cfg = b.cfg;
        let mut mir = build_mir(&cfg).unwrap();
        let stats = run_mem2reg(&mut mir);
        assert_eq!(stats.promoted, 1);
        assert!(stats.phis_created >= 1, "loop-carried phi");
        assert_diff_match(&cfg);
    }

    #[test]
    fn mixed_const_dynamic_cfg_matches_minimal() {
        // One temp accessed dynamically (stays memory), one promoted.
        let mut b = B::default();
        let arr = b.temp("arr", 4);
        let x = b.temp("x", 1);
        // arr[0..3] <- 0 (initialize, keeps reads defined).
        let mut stmts = Vec::new();
        for i in 0..4 {
            let p = b.temp_place(arr, i);
            let z = b.int(0);
            stmts.push(b.set(p, z));
        }
        // x <- in0
        let in0 = b.place_int(-3, 0);
        let g_in = b.get(in0);
        let xp = b.temp_place(x, 0);
        stmts.push(b.set(xp, g_in));
        // idx temp pattern: i0 <- Mod(x, 4); arr[i0] <- 7 (dynamic store).
        let i0 = b.temp("i0", 1);
        let xp_r = b.temp_place(x, 0);
        let gx = b.get(xp_r);
        let four = b.int(4);
        let m = b.pure(Op::Mod, vec![gx, four]);
        let fl = b.pure(Op::Floor, vec![m]);
        let i0w = b.temp_place(i0, 0);
        stmts.push(b.set(i0w, fl));
        let i0r = b.temp_place(i0, 0);
        let dyn_place = {
            b.cfg.places.push(CfgPlace {
                block: BlockValue::Temp(arr),
                index: IndexValue::Place(i0r),
                offset: 0,
            });
            b.cfg.places.len() - 1
        };
        let seven = b.int(7);
        stmts.push(b.set(dyn_place, seven));
        // out <- arr[1] + x
        let a1 = b.temp_place(arr, 1);
        let ga = b.get(a1);
        let xr2 = b.temp_place(x, 0);
        let gx2 = b.get(xr2);
        let sum = b.pure(Op::Add, vec![ga, gx2]);
        let out = b.place_int(20, 0);
        stmts.push(b.set(out, sum));
        let zt = b.int(0);
        b.block(stmts, zt, vec![]);
        let cfg = b.cfg;
        let mut mir = build_mir(&cfg).unwrap();
        let stats = run_mem2reg(&mut mir);
        assert_eq!(stats.refused_dynamic_index, 1, "arr stays memory");
        assert!(stats.promoted >= 1, "x (and i0) promote");
        assert_diff_match(&cfg);
    }

    #[test]
    fn w1_rerun_synergy_folds_loads_of_stored_constants() {
        // t <- 4; out <- Add(t, 1): after mem2reg + SCCP the store value is
        // the constant 5.
        let mut b = B::default();
        let t = b.temp("t", 1);
        let tp = b.temp_place(t, 0);
        let four = b.int(4);
        let s = b.set(tp, four);
        let tr = b.temp_place(t, 0);
        let g = b.get(tr);
        let one = b.int(1);
        let add = b.pure(Op::Add, vec![g, one]);
        let out = b.place_int(20, 0);
        let s2 = b.set(out, add);
        let zt = b.int(0);
        b.block(vec![s, s2], zt, vec![]);
        let cfg = b.cfg;
        let mut mir = build_mir(&cfg).unwrap();
        let pipeline = Pipeline::new(vec![Box::new(Mem2Reg), Box::new(crate::passes::sccp::Sccp)]);
        pipeline.run(&mut mir, &mut Analyses::new());
        let stores: Vec<&Inst> = mir
            .blocks
            .iter()
            .flat_map(|blk| &blk.insts)
            .filter_map(|&v| match mir.inst(v) {
                Inst::Store { value, .. } => Some(mir.inst(*value)),
                _ => None,
            })
            .collect();
        assert_eq!(stores, vec![&Inst::ConstInt(5)], "folded through the slot");
        assert_diff_match(&cfg);
    }

    #[test]
    fn multi_use_promotion_compiles_and_runs_at_standard() {
        // The diamond CFG's joined value is used twice: destruct_ssa must
        // legalize the multi-use before lowering. End-to-end at standard.
        let cfg = diamond_cfg();
        let nodes = compile_cfg(&cfg, Level::Standard).expect("standard compiles");
        let mut interp = crate::interpret::Interpreter::new(0);
        interp.set_block(-3, vec![0.0]);
        interp.run(&nodes).unwrap();
        assert_eq!(interp.block(20).unwrap()[0], 10.0);
        assert_eq!(interp.block(20).unwrap()[1], 11.0);
        let mut interp = crate::interpret::Interpreter::new(0);
        interp.set_block(-3, vec![1.0]);
        interp.run(&nodes).unwrap();
        assert_eq!(interp.block(20).unwrap()[0], 20.0);
        assert_eq!(interp.block(20).unwrap()[1], 21.0);
    }

    #[test]
    fn promoted_value_does_not_move_across_memory_writes() {
        // t <- (load 21[0]); 21[0] <- 99; out <- t. The promoted value must
        // keep its pre-write evaluation (destruct's order legalization).
        let mut b = B::default();
        let t = b.temp("t", 1);
        let src = b.place_int(21, 0);
        let g = b.get(src);
        let tp = b.temp_place(t, 0);
        let s1 = b.set(tp, g);
        let src_w = b.place_int(21, 0);
        let c99 = b.int(99);
        let s2 = b.set(src_w, c99);
        let tr = b.temp_place(t, 0);
        let g2 = b.get(tr);
        let out = b.place_int(20, 0);
        let s3 = b.set(out, g2);
        let zt = b.int(0);
        b.block(vec![s1, s2, s3], zt, vec![]);
        let cfg = b.cfg;
        assert_diff_match(&cfg);
        // And the concrete value: 20[0] must be the ORIGINAL 21[0] fill, not 99.
        let nodes = compile_cfg_with_pipeline(&cfg, &mem2reg_pipeline()).expect("compiles");
        let mut interp = crate::interpret::Interpreter::new(0);
        interp.set_block(21, vec![5.0]);
        interp.run(&nodes).unwrap();
        assert_eq!(interp.block(20).unwrap()[0], 5.0);
        assert_eq!(interp.block(21).unwrap()[0], 99.0);
    }

    #[test]
    fn unaccessed_and_inert_mir_reports_no_change() {
        let mut mir = Mir::new();
        let _t = mir.push_temp("t", 1);
        let b0 = mir.push_block();
        let c = mir.push_inst(Inst::ConstInt(1));
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: concrete_place(20, 0),
                value: c,
            },
        );
        assert!(!run_pass(&mut mir), "nothing to promote");
    }

    #[test]
    fn deterministic_output() {
        let cfg = diamond_cfg();
        let build = || {
            let mut mir = build_mir(&cfg).unwrap();
            run_mem2reg(&mut mir);
            format!("{mir:?}")
        };
        assert_eq!(build(), build());
    }

    #[test]
    fn promotion_stats_analysis_matches_run() {
        let cfg = diamond_cfg();
        let mir = build_mir(&cfg).unwrap();
        let analysis = promotion_stats(&mir);
        let mut mir2 = build_mir(&cfg).unwrap();
        let run = run_mem2reg(&mut mir2);
        assert_eq!(analysis.promoted, run.promoted);
        assert_eq!(analysis.refused_dynamic_index, run.refused_dynamic_index);
        assert_eq!(
            analysis.refused_read_before_write,
            run.refused_read_before_write
        );
    }
}
