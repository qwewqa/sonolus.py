//! Temp-block slot allocation (PORT.md T1.3): liveness analysis at temp-block
//! granularity, interference construction, and deterministic first-fit slot
//! coloring within the 4096-slot temporary memory budget (legacy `TEMP_SIZE`).
//!
//! # Liveness model (vs the legacy `LivenessAnalysis`)
//!
//! Backward dataflow over MIR blocks (worklist, no recursion), tracking temp
//! blocks:
//!
//! - A `Load` whose place block is a temp makes that temp live (any size).
//! - A `Store` to a **size-1** temp kills it (a size-1 block has a single cell,
//!   so any in-bounds write is a full definition — same rule as legacy
//!   `get_defs`). Stores to larger temps (arrays) define without killing, and
//!   do not count as uses (same as legacy `get_uses` for `IRSet`).
//! - Loads inside a `ShortCircuit` lazy tree are *conditional* uses; they count
//!   as uses at the short-circuit's schedule point (may-use ⊆ live).
//!
//! Like legacy, arrays (size > 1) get the `array_defs`/`is_array_init`
//! refinement (T3.5; legacy `LivenessAnalysis.preprocess_arrays`): a forward
//! may-defined fixpoint (union over predecessors) finds, per array store,
//! whether it can be the array's *first* write. Going backward, a may-first
//! write kills the array's liveness (nothing above it can read a written
//! value), and an array that cannot have been written yet at a block's end is
//! filtered from that block's live-out — so an array is live from its first
//! possible write to its last use, not from entry. A loop-carried write is
//! never an init (the back edge feeds the write's own def into its block), so
//! writes-then-reads across iterations keep the array live around the loop.
//! Reads on paths with no possible prior write are out of contract exactly as
//! in legacy (`ToSSA`'s `err` places): the cells hold whatever the allocator's
//! sharing left there.
//!
//! Deliberately **more conservative** than legacy in one way (sound — live
//! ranges only grow; the budget guarantee below still holds):
//!
//! - No dead-store skipping (`can_skip`): dead stores keep their uses live.
//!   The minimal pipeline performs **no dead-code elimination at all** — every
//!   store executes, faithful to legacy `MINIMAL_PASSES` (`AllocateBasic` keeps
//!   everything); dead stores are made safe by interference instead (below).
//!
//! # Interference
//!
//! Like legacy `Allocate.get_interference`, every `Store` instruction
//! contributes a clique over the temps live after it — plus the stored temp
//! itself even when it is dead afterwards (legacy instead *deletes* dead
//! stores; keeping them means their writes must not clobber anything live).
//! Size-0 temps never interfere (they hold no data; legacy filters `size > 0`).
//!
//! # Assignment
//!
//! Greedy first-fit interval packing in a fixed, stable order (T3.5 — the
//! architecture's "greedy coloring of the SSA interference graph"):
//!
//! 1. **Sized temps** (size ≥ 2, the unpromoted arrays) first, in **decreasing
//!    size, then temp-table index** order (the table index is the stable
//!    identity from the decoded CFG's first-encounter-ordered temp table —
//!    name strings are never consulted, unlike legacy which tie-breaks on
//!    names).
//! 2. **Scalar temps** (size 1 — user scalars and, post-W2, the out-of-SSA
//!    class temps plus `gvnN`/`m2rN` temps) next, in **maximum-cardinality-
//!    search order** over the scalar interference subgraph: repeatedly pick
//!    the scalar with the most already-picked neighbors, breaking ties by
//!    first occurrence over an RPO walk of the blocks (unreachable blocks
//!    appended in id order), then temp-table index. On a chordal graph MCS
//!    visits each vertex after a clique of its neighbors, making the greedy
//!    coloring optimal (= max clique = max scalar pressure); SSA-derived
//!    interference is chordal, and on pydori the MCS coloring is exactly
//!    pressure-optimal on the heaviest callbacks.
//!
//! Because the post-coalescing merged-class graph is not always chordal,
//! greedy coloring is order-sensitive with no guaranteed winner: scalars are
//! therefore *also* colored in plain temp-table order (the pre-T3.5
//! allocator), and the lower high-water mark wins (ties prefer the table
//! order, whose code-position-correlated layouts DAG-dedup better — see
//! `allocate_temps`). The allocator is thus never worse than the T1.3 one on
//! any input.
//!
//! First-fit places each temp at the lowest gap that fits among its
//! already-placed neighbours, so the high-water mark never exceeds the sum of
//! unique temp sizes — which is exactly what legacy `AllocateBasic` (no reuse
//! at all) always uses, *regardless of the visit order*. Hence the Rust
//! minimal allocator is never worse than the legacy minimal one, and any
//! slack in the interference graph makes it strictly better.
//!
//! Exceeding the budget produces [`TempLimitError`], whose message matches the
//! legacy `ValueError("Temporary memory limit exceeded")` text. The bound check
//! is `offset + size > 4096` (the legacy `Allocate` rule; `AllocateBasic` is
//! off-by-one stricter, raising even when the total is exactly 4096).
//!
//! # Size-0 temps
//!
//! Legal in the encoding, absent from the entire checked-in corpus (verified),
//! and unaddressable (no cell belongs to them). They get offset 0 and are
//! excluded from interference and the high-water mark. Divergence note: legacy
//! `Allocate` (standard level) maps them to offset −1, making any *access*
//! trap at runtime ("Index must be non-negative"); legacy `AllocateBasic`
//! (minimal) gives them the running index, silently aliasing the next temp.
//! Accessing a size-0 temp is out of contract either way; offset 0 keeps
//! minimal-level behavior non-trapping like legacy minimal.
//!
//! # Scalar slots
//!
//! Out-of-SSA (`ssa::destruct_ssa`) materializes SSA values as fresh size-1
//! temp blocks appended to the same table, so SSA scalar slots are colored by
//! this very allocator with the same rules. The minimal pipeline does not use
//! that path (PORT.md decision D10); it activated with W2 `Mem2Reg` (T3.4),
//! and T3.5 added the scalar coloring order above plus post-destruct copy
//! coalescing (`crate::coalesce`), which consumes the same liveness +
//! interference construction through [`temp_interference`].

use std::collections::HashSet;
use std::fmt;

use crate::analysis::BitSet;
use crate::mir::{BlockRef, Inst, Mir, TempId, Value};

/// The temporary-memory budget (legacy `allocate.TEMP_SIZE`).
pub const TEMP_SIZE: u64 = 4096;

/// The runtime block id temp places are rewritten to (legacy block 10000).
pub const TEMP_RUNTIME_BLOCK: i64 = 10000;

/// Temporary memory limit exceeded (message matches the legacy `ValueError`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TempLimitError;

impl fmt::Display for TempLimitError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "Temporary memory limit exceeded")
    }
}

impl std::error::Error for TempLimitError {}

/// The result of slot allocation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Allocation {
    /// Per temp-table entry: the assigned base offset in block 10000, or
    /// `None` when the temp does not appear in the MIR.
    pub offsets: Vec<Option<u32>>,
    /// High-water mark: `max(offset + size)` over all placed temps.
    pub slots_used: u32,
}

/// The temp (if any) a place reads/writes through its block field.
fn place_temp(inst: &Inst) -> Option<(TempId, bool)> {
    match inst {
        Inst::Load { place } => match place.block {
            BlockRef::Temp(t) => Some((t, false)),
            _ => None,
        },
        Inst::Store { place, .. } => match place.block {
            BlockRef::Temp(t) => Some((t, true)),
            _ => None,
        },
        _ => None,
    }
}

/// Collects the temps loaded anywhere inside a `ShortCircuit` lazy tree
/// (conditional uses; see the module docs). Stops at scheduled values and
/// constants; iterative.
fn lazy_temp_uses(mir: &Mir, scheduled: &[bool], root: Value, out: &mut Vec<TempId>) {
    let mut stack = vec![root];
    while let Some(v) = stack.pop() {
        if scheduled[v as usize] {
            continue;
        }
        match mir.inst(v) {
            Inst::ConstInt(_) | Inst::ConstFloat(_) | Inst::Phi { .. } => {}
            Inst::Op { args, .. } => stack.extend(args.iter().copied()),
            Inst::ShortCircuit { lhs, rhs, .. } => {
                stack.push(*lhs);
                stack.push(*rhs);
            }
            Inst::Load { place } | Inst::Store { place, .. } => {
                if let BlockRef::Temp(t) = place.block {
                    out.push(t);
                }
                if let BlockRef::Value(v) = place.block {
                    stack.push(v);
                }
                if let crate::mir::IndexRef::Value(v) = place.index {
                    stack.push(v);
                }
                if let Inst::Store { value, .. } = mir.inst(v) {
                    stack.push(*value);
                }
            }
        }
    }
}

/// The effect of one scheduled instruction on temp liveness.
enum Effect {
    None,
    Use(TempId),
    /// Store to a temp: `kills` iff size == 1.
    Def {
        temp: TempId,
        kills: bool,
    },
    /// Conditional uses from a lazy tree.
    Uses(Vec<TempId>),
}

fn effect(mir: &Mir, scheduled: &[bool], v: Value) -> Effect {
    match mir.inst(v) {
        Inst::ShortCircuit { rhs, .. } => {
            let mut uses = Vec::new();
            lazy_temp_uses(mir, scheduled, *rhs, &mut uses);
            if uses.is_empty() {
                Effect::None
            } else {
                Effect::Uses(uses)
            }
        }
        inst => match place_temp(inst) {
            Some((t, false)) => Effect::Use(t),
            Some((t, true)) => Effect::Def {
                temp: t,
                kills: mir.temps[t].size == 1,
            },
            None => Effect::None,
        },
    }
}

/// The temp-granularity interference graph (plus presence), shared between
/// slot allocation and the T3.5 post-out-of-SSA copy coalescer
/// (`crate::coalesce`): a copy-related pair of temps may be merged exactly
/// when the allocator could have given them the same slot, which is this
/// graph's non-adjacency.
#[derive(Debug, Clone)]
pub(crate) struct TempInterference {
    /// Per temp: does it appear anywhere in the MIR?
    pub present: Vec<bool>,
    /// Adjacency sets (queried for membership; callers needing ordered
    /// iteration must sort).
    pub adj: Vec<HashSet<u32>>,
}

/// Computes temp-block liveness and builds the interference graph (module
/// docs: a clique over the live-after set ∪ {stored temp} at every `Store`).
#[allow(clippy::too_many_lines)] // one linear dataflow pipeline
pub(crate) fn temp_interference(mir: &Mir) -> TempInterference {
    let n_temps = mir.temps.len();
    let n_blocks = mir.blocks.len();
    let scheduled = mir.scheduled_mask();

    // Array-defs refinement (module docs; legacy `preprocess_arrays`): a
    // forward may-defined fixpoint over arrays (size > 1), union over
    // predecessors.
    let mut arrays_mask = BitSet::new(n_temps);
    for (t, def) in mir.temps.iter().enumerate() {
        if def.size > 1 {
            arrays_mask.insert(t);
        }
    }
    let mut array_gen: Vec<BitSet> = Vec::with_capacity(n_blocks);
    for block in &mir.blocks {
        let mut gen_set = BitSet::new(n_temps);
        for &v in &block.insts {
            if let Effect::Def { temp, kills: false } = effect(mir, &scheduled, v)
                && mir.temps[temp].size > 1
            {
                gen_set.insert(temp);
            }
        }
        array_gen.push(gen_set);
    }
    let mut ad_in: Vec<BitSet> = (0..n_blocks).map(|_| BitSet::new(n_temps)).collect();
    let mut ad_out: Vec<BitSet> = (0..n_blocks).map(|_| BitSet::new(n_temps)).collect();
    {
        let mut worklist: Vec<usize> = (0..n_blocks).rev().collect();
        let mut queued = vec![true; n_blocks];
        while let Some(b) = worklist.pop() {
            queued[b] = false;
            let mut new_out = ad_in[b].clone();
            new_out.union_with(&array_gen[b]);
            if new_out != ad_out[b] {
                let successors: Vec<usize> = mir.blocks[b].terminator.successors().collect();
                ad_out[b] = new_out;
                for succ in successors {
                    if ad_in[succ].union_with(&ad_out[b].clone()) && !queued[succ] {
                        queued[succ] = true;
                        worklist.push(succ);
                    }
                }
            }
        }
    }
    // Per scheduled instruction: is it a may-first write to its array?
    let mut init_at: Vec<Vec<bool>> = Vec::with_capacity(n_blocks);
    for (b, block) in mir.blocks.iter().enumerate() {
        let mut flags = vec![false; block.insts.len()];
        let mut running = ad_in[b].clone();
        for (i, &v) in block.insts.iter().enumerate() {
            if let Effect::Def { temp, kills: false } = effect(mir, &scheduled, v)
                && mir.temps[temp].size > 1
                && !running.contains(temp)
            {
                flags[i] = true;
                running.insert(temp);
            }
        }
        init_at.push(flags);
    }
    // Arrays a block's end cannot have seen a write to yet are filtered from
    // its live-out (legacy `process_block`'s seed filter).
    let not_yet_defined: Vec<BitSet> = (0..n_blocks)
        .map(|b| {
            let mut mask = arrays_mask.clone();
            mask.subtract(&ad_out[b]);
            mask
        })
        .collect();

    // Presence + per-block gen/kill.
    let mut present = vec![false; n_temps];
    let mut gen_sets: Vec<BitSet> = Vec::with_capacity(n_blocks);
    let mut kill_sets: Vec<BitSet> = Vec::with_capacity(n_blocks);
    for (b, block) in mir.blocks.iter().enumerate() {
        let mut gen_set = BitSet::new(n_temps);
        let mut kill = BitSet::new(n_temps);
        for (i, &v) in block.insts.iter().enumerate().rev() {
            match effect(mir, &scheduled, v) {
                Effect::None => {}
                Effect::Use(t) => {
                    present[t] = true;
                    gen_set.insert(t);
                    kill.remove(t);
                }
                Effect::Uses(ts) => {
                    for t in ts {
                        present[t] = true;
                        gen_set.insert(t);
                        kill.remove(t);
                    }
                }
                Effect::Def { temp, kills } => {
                    present[temp] = true;
                    if kills || init_at[b][i] {
                        gen_set.remove(temp);
                        kill.insert(temp);
                    }
                }
            }
        }
        gen_sets.push(gen_set);
        kill_sets.push(kill);
    }

    // Backward fixpoint: live_out = union of successors' live_in.
    let preds = mir.predecessors();
    let mut live_in: Vec<BitSet> = (0..n_blocks).map(|_| BitSet::new(n_temps)).collect();
    let mut live_out: Vec<BitSet> = (0..n_blocks).map(|_| BitSet::new(n_temps)).collect();
    let mut worklist: Vec<usize> = (0..n_blocks).rev().collect();
    let mut queued = vec![true; n_blocks];
    while let Some(b) = worklist.pop() {
        queued[b] = false;
        for succ in mir.blocks[b].terminator.successors() {
            live_out[b].union_with(&live_in[succ].clone());
        }
        // live_in = gen ∪ (filtered live_out − kill)
        let mut new_in = live_out[b].clone();
        new_in.subtract(&not_yet_defined[b]);
        new_in.subtract(&kill_sets[b]);
        new_in.union_with(&gen_sets[b]);
        if new_in != live_in[b] {
            live_in[b] = new_in;
            for &p in &preds[b] {
                if !queued[p] {
                    queued[p] = true;
                    worklist.push(p);
                }
            }
        }
    }

    // Interference: a clique over (live-after ∪ {stored temp}) at every Store.
    let mut interference: Vec<HashSet<u32>> = vec![HashSet::new(); n_temps];
    let mut scratch: Vec<usize> = Vec::new();
    for (b, block) in mir.blocks.iter().enumerate() {
        let mut live = live_out[b].clone();
        live.subtract(&not_yet_defined[b]);
        for (i, &v) in block.insts.iter().enumerate().rev() {
            match effect(mir, &scheduled, v) {
                Effect::None => {}
                Effect::Use(t) => live.insert(t),
                Effect::Uses(ts) => {
                    for t in ts {
                        live.insert(t);
                    }
                }
                Effect::Def { temp, kills } => {
                    scratch.clear();
                    scratch.extend(live.iter().filter(|&t| mir.temps[t].size > 0));
                    if mir.temps[temp].size > 0 && !live.contains(temp) {
                        scratch.push(temp);
                    }
                    for x in 0..scratch.len() {
                        for y in (x + 1)..scratch.len() {
                            let (a, c) = (scratch[x], scratch[y]);
                            interference[a].insert(u32::try_from(c).expect("temp id fits u32"));
                            interference[c].insert(u32::try_from(a).expect("temp id fits u32"));
                        }
                    }
                    if kills || init_at[b][i] {
                        live.remove(temp);
                    }
                }
            }
        }
        // Stores to non-temp places also clique the live set in legacy; those
        // are covered because legacy's clique is over the same live set we
        // accumulate — but a clique with no def adds no *new* pairs beyond the
        // ones formed at the defs of the participating temps, except for
        // load-only (never-stored) temps, which by construction share slots
        // only with other never-stored temps (see module docs).
    }
    TempInterference {
        present,
        adj: interference,
    }
}

/// First-occurrence order of temps over an RPO walk of the blocks
/// (unreachable blocks appended in id order): per temp, the position of its
/// first appearance, or `usize::MAX` for temps that never appear. This is the
/// MCS tie-break for the scalar coloring order (module docs).
fn first_occurrence(mir: &Mir) -> Vec<usize> {
    let scheduled = mir.scheduled_mask();
    let mut walk = mir.reverse_postorder();
    let mut in_walk = vec![false; mir.blocks.len()];
    for &b in &walk {
        in_walk[b] = true;
    }
    for (b, seen) in in_walk.iter().enumerate() {
        if !seen {
            walk.push(b);
        }
    }
    let mut first = vec![usize::MAX; mir.temps.len()];
    let mut seq = 0usize;
    let mut note = |t: TempId, first: &mut Vec<usize>| {
        if first[t] == usize::MAX {
            first[t] = seq;
            seq += 1;
        }
    };
    for &b in &walk {
        for &v in &mir.blocks[b].insts {
            match effect(mir, &scheduled, v) {
                Effect::None => {}
                Effect::Use(t) | Effect::Def { temp: t, .. } => note(t, &mut first),
                Effect::Uses(ts) => {
                    for t in ts {
                        note(t, &mut first);
                    }
                }
            }
        }
    }
    first
}

/// Computes liveness, builds the interference graph, and colors slots.
///
/// # Errors
///
/// [`TempLimitError`] when any temp cannot be placed within the 4096-slot
/// budget.
pub fn allocate_temps(mir: &Mir) -> Result<Allocation, TempLimitError> {
    let n_temps = mir.temps.len();
    let TempInterference { present, adj } = temp_interference(mir);

    // Sized temps always go first, in (decreasing size, temp index) order.
    let mut sized: Vec<usize> = (0..n_temps)
        .filter(|&t| present[t] && mir.temps[t].size > 1)
        .collect();
    sized.sort_by_key(|&t| (std::cmp::Reverse(mir.temps[t].size), t));

    // Scalar order 1: maximum-cardinality search (module docs), tie-broken by
    // (first occurrence, temp index). O(S^2) selection; deterministic
    // (weights change only by increments, ties resolved by the pre-sorted
    // candidate order).
    let first = first_occurrence(mir);
    let mut candidates: Vec<usize> = (0..n_temps)
        .filter(|&t| present[t] && mir.temps[t].size == 1)
        .collect();
    candidates.sort_by_key(|&t| (first[t], t));
    let mut mcs: Vec<usize> = Vec::with_capacity(candidates.len());
    {
        let mut weight: Vec<usize> = vec![0; n_temps];
        let mut picked = vec![false; n_temps];
        for _ in 0..candidates.len() {
            let mut best: Option<usize> = None;
            for &t in &candidates {
                if !picked[t] && best.is_none_or(|b| weight[t] > weight[b]) {
                    best = Some(t);
                }
            }
            let t = best.expect("an unpicked scalar remains");
            picked[t] = true;
            mcs.push(t);
            for &o in &adj[t] {
                let o = o as usize;
                if !picked[o] && mir.temps[o].size == 1 {
                    weight[o] += 1;
                }
            }
        }
    }
    // Scalar order 2: plain temp-table order — exactly the pre-T3.5 (T1.3)
    // allocator. Greedy coloring is order-sensitive without a guaranteed
    // winner on non-chordal graphs, so both orders are colored and the lower
    // high-water mark wins: the allocator is never worse than the T1.3 one on
    // any input, and the budget guarantee is preserved per order. Ties prefer
    // the TABLE order: it correlates with code position, so structurally
    // repeated code regions get matching offsets and their emitted subtrees
    // keep DAG-deduping (measured on pydori: an MCS tie preference costs ~5%
    // dag_size for zero slot benefit).
    let table: Vec<usize> = (0..n_temps)
        .filter(|&t| present[t] && mir.temps[t].size == 1)
        .collect();

    let color = |scalars: &[usize]| -> Result<(Vec<Option<u32>>, u64), TempLimitError> {
        let mut offsets: Vec<Option<u32>> = vec![None; n_temps];
        let mut slots_used: u64 = 0;
        let mut intervals: Vec<(u64, u64)> = Vec::new();
        for &t in sized.iter().chain(scalars) {
            let size = mir.temps[t].size;
            intervals.clear();
            for &o in &adj[t] {
                let o = o as usize;
                if let Some(off) = offsets[o] {
                    intervals.push((u64::from(off), u64::from(off) + mir.temps[o].size));
                }
            }
            intervals.sort_unstable();
            let mut offset: u64 = 0;
            for &(start, end) in &intervals {
                if offset + size <= start {
                    break;
                }
                offset = offset.max(end);
            }
            if offset + size > TEMP_SIZE {
                return Err(TempLimitError);
            }
            offsets[t] = Some(u32::try_from(offset).expect("offset fits u32"));
            slots_used = slots_used.max(offset + size);
        }
        Ok((offsets, slots_used))
    };
    let by_mcs = color(&mcs);
    let by_table = color(&table);
    let (mut offsets, slots_used) = match (by_mcs, by_table) {
        (Ok(m), Ok(t)) => {
            if m.1 < t.1 {
                m
            } else {
                t
            }
        }
        (Ok(m), Err(TempLimitError)) => m,
        (Err(TempLimitError), Ok(t)) => t,
        (Err(TempLimitError), Err(TempLimitError)) => return Err(TempLimitError),
    };
    // Size-0 temps that appear get offset 0 (module docs).
    for t in 0..n_temps {
        if present[t] && mir.temps[t].size == 0 {
            offsets[t] = Some(0);
        }
    }
    Ok(Allocation {
        offsets,
        slots_used: u32::try_from(slots_used).expect("slots_used <= 4096"),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::mir::{BlockRef, CaseCond, IndexRef, Inst, Mir, Place, Terminator};
    use crate::ops::Op;

    fn temp_place(t: TempId) -> Place {
        Place {
            block: BlockRef::Temp(t),
            index: IndexRef::Const(0),
            offset: 0,
        }
    }

    /// Schedules `inst` at the end of `block`.
    fn sched(mir: &mut Mir, block: usize, inst: Inst) -> Value {
        let v = mir.push_inst(inst);
        mir.blocks[block].insts.push(v);
        v
    }

    fn store_const(mir: &mut Mir, block: usize, t: TempId, value: i64) {
        let c = mir.push_inst(Inst::ConstInt(value));
        sched(
            mir,
            block,
            Inst::Store {
                place: temp_place(t),
                value: c,
            },
        );
    }

    fn load(mir: &mut Mir, block: usize, t: TempId) -> Value {
        sched(
            mir,
            block,
            Inst::Load {
                place: temp_place(t),
            },
        )
    }

    /// Stores a value to a concrete block so the loaded value is "used".
    fn store_out(mir: &mut Mir, block: usize, value: Value) {
        sched(
            mir,
            block,
            Inst::Store {
                place: Place {
                    block: BlockRef::Concrete(20),
                    index: IndexRef::Const(0),
                    offset: 0,
                },
                value,
            },
        );
    }

    #[test]
    fn sequential_lifetimes_share_a_slot() {
        // a = 1; out = a; b = 2; out = b  — a and b never overlap.
        let mut mir = Mir::new();
        let a = mir.push_temp("a", 1);
        let b = mir.push_temp("b", 1);
        let blk = mir.push_block();
        store_const(&mut mir, blk, a, 1);
        let va = load(&mut mir, blk, a);
        store_out(&mut mir, blk, va);
        store_const(&mut mir, blk, b, 2);
        let vb = load(&mut mir, blk, b);
        store_out(&mut mir, blk, vb);
        let alloc = allocate_temps(&mir).unwrap();
        assert_eq!(alloc.offsets[a], Some(0));
        assert_eq!(alloc.offsets[b], Some(0));
        assert_eq!(alloc.slots_used, 1);
    }

    #[test]
    fn overlapping_lifetimes_get_distinct_slots() {
        // a = 1; b = 2; out = a; out = b — both live across b's store.
        let mut mir = Mir::new();
        let a = mir.push_temp("a", 1);
        let b = mir.push_temp("b", 1);
        let blk = mir.push_block();
        store_const(&mut mir, blk, a, 1);
        store_const(&mut mir, blk, b, 2);
        let va = load(&mut mir, blk, a);
        store_out(&mut mir, blk, va);
        let vb = load(&mut mir, blk, b);
        store_out(&mut mir, blk, vb);
        let alloc = allocate_temps(&mir).unwrap();
        assert_eq!(alloc.offsets[a], Some(0));
        assert_eq!(alloc.offsets[b], Some(1));
        assert_eq!(alloc.slots_used, 2);
    }

    #[test]
    fn dead_store_does_not_clobber_live_temp() {
        // a = 1; dead = 2; out = a — `dead` is never read, but its store still
        // executes (no DCE at minimal), so it must not share a's slot.
        let mut mir = Mir::new();
        let a = mir.push_temp("a", 1);
        let dead = mir.push_temp("dead", 1);
        let blk = mir.push_block();
        store_const(&mut mir, blk, a, 1);
        store_const(&mut mir, blk, dead, 2);
        let va = load(&mut mir, blk, a);
        store_out(&mut mir, blk, va);
        let alloc = allocate_temps(&mir).unwrap();
        assert_ne!(alloc.offsets[a], alloc.offsets[dead]);
    }

    #[test]
    fn arrays_are_not_killed_by_stores_and_pack_by_size() {
        // arr (size 3) is written then read across a's whole lifetime; a is a
        // scalar. Decreasing-size order places arr first at 0, a at 3.
        let mut mir = Mir::new();
        let a = mir.push_temp("a", 1);
        let arr = mir.push_temp("arr", 3);
        let blk = mir.push_block();
        store_const(&mut mir, blk, arr, 9);
        store_const(&mut mir, blk, a, 1);
        let varr = load(&mut mir, blk, arr);
        store_out(&mut mir, blk, varr);
        let va = load(&mut mir, blk, a);
        store_out(&mut mir, blk, va);
        let alloc = allocate_temps(&mir).unwrap();
        assert_eq!(alloc.offsets[arr], Some(0));
        assert_eq!(alloc.offsets[a], Some(3));
        assert_eq!(alloc.slots_used, 4);
    }

    #[test]
    fn loop_liveness_keeps_temp_alive_around_back_edge() {
        // block0: i = 0 -> block1: out = i; branch back to 1 or exit.
        // j is defined and used only inside block1 *after* i's use, but i stays
        // live around the back edge, so they must not share.
        let mut mir = Mir::new();
        let i = mir.push_temp("i", 1);
        let j = mir.push_temp("j", 1);
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        store_const(&mut mir, b0, i, 0);
        mir.blocks[b0].terminator = Terminator::Jump(b1);
        let vi = load(&mut mir, b1, i);
        store_out(&mut mir, b1, vi);
        store_const(&mut mir, b1, j, 5);
        let vj = load(&mut mir, b1, j);
        store_out(&mut mir, b1, vj);
        let test = load(&mut mir, b1, j);
        mir.blocks[b1].terminator = Terminator::Branch {
            test,
            cases: vec![(CaseCond::Int(0), b1)],
            default: None,
        };
        let alloc = allocate_temps(&mir).unwrap();
        assert_ne!(alloc.offsets[i], alloc.offsets[j]);
    }

    #[test]
    fn lazy_short_circuit_loads_count_as_uses() {
        // x = 1; y = And(loaded c, lazy load of x); out = y.
        // x's only read is inside the lazy tree; without counting it, x would
        // be dead at its own store and could alias c.
        let mut mir = Mir::new();
        let c = mir.push_temp("c", 1);
        let x = mir.push_temp("x", 1);
        let blk = mir.push_block();
        store_const(&mut mir, blk, c, 1);
        store_const(&mut mir, blk, x, 2);
        let vc = load(&mut mir, blk, c);
        let lazy_load = mir.push_inst(Inst::Load {
            place: temp_place(x),
        }); // unscheduled
        let sc = sched(
            &mut mir,
            blk,
            Inst::ShortCircuit {
                op: Op::And,
                pure_node: true,
                lhs: vc,
                rhs: lazy_load,
            },
        );
        store_out(&mut mir, blk, sc);
        let alloc = allocate_temps(&mir).unwrap();
        assert_ne!(alloc.offsets[c], alloc.offsets[x]);
    }

    #[test]
    fn unused_temps_get_no_offset() {
        let mut mir = Mir::new();
        let unused = mir.push_temp("unused", 1);
        let used = mir.push_temp("used", 1);
        let blk = mir.push_block();
        store_const(&mut mir, blk, used, 1);
        let alloc = allocate_temps(&mir).unwrap();
        assert_eq!(alloc.offsets[unused], None);
        assert_eq!(alloc.offsets[used], Some(0));
        assert_eq!(alloc.slots_used, 1);
    }

    #[test]
    fn size_zero_temps_get_offset_zero_and_no_slots() {
        let mut mir = Mir::new();
        let z = mir.push_temp("z", 0);
        let a = mir.push_temp("a", 1);
        let blk = mir.push_block();
        store_const(&mut mir, blk, a, 1);
        store_const(&mut mir, blk, z, 2);
        let va = load(&mut mir, blk, a);
        store_out(&mut mir, blk, va);
        let alloc = allocate_temps(&mir).unwrap();
        assert_eq!(alloc.offsets[z], Some(0));
        assert_eq!(alloc.offsets[a], Some(0));
        assert_eq!(alloc.slots_used, 1);
    }

    #[test]
    fn budget_exceeded_is_a_clean_error() {
        // Two interfering 3000-slot arrays cannot fit in 4096.
        let mut mir = Mir::new();
        let a = mir.push_temp("a", 3000);
        let b = mir.push_temp("b", 3000);
        let blk = mir.push_block();
        store_const(&mut mir, blk, a, 1);
        store_const(&mut mir, blk, b, 2);
        let va = load(&mut mir, blk, a);
        store_out(&mut mir, blk, va);
        let vb = load(&mut mir, blk, b);
        store_out(&mut mir, blk, vb);
        let err = allocate_temps(&mir).unwrap_err();
        assert_eq!(err.to_string(), "Temporary memory limit exceeded");
    }

    #[test]
    fn exactly_full_budget_is_allowed() {
        let mut mir = Mir::new();
        let a = mir.push_temp("a", 4096);
        let blk = mir.push_block();
        store_const(&mut mir, blk, a, 1);
        let va = load(&mut mir, blk, a);
        store_out(&mut mir, blk, va);
        let alloc = allocate_temps(&mir).unwrap();
        assert_eq!(alloc.offsets[a], Some(0));
        assert_eq!(alloc.slots_used, 4096);
    }

    #[test]
    fn assignment_is_deterministic_and_ordered_by_size_then_index() {
        // Three mutually interfering temps: sizes 2, 5, 2 with table order
        // s2a, s5, s2b. Order: s5 (size 5) -> 0, s2a -> 5, s2b -> 7.
        let mut mir = Mir::new();
        let s2a = mir.push_temp("s2a", 2);
        let s5 = mir.push_temp("s5", 5);
        let s2b = mir.push_temp("s2b", 2);
        let blk = mir.push_block();
        for t in [s2a, s5, s2b] {
            store_const(&mut mir, blk, t, 1);
        }
        for t in [s2a, s5, s2b] {
            let v = load(&mut mir, blk, t);
            store_out(&mut mir, blk, v);
        }
        let first = allocate_temps(&mir).unwrap();
        let second = allocate_temps(&mir).unwrap();
        assert_eq!(first, second, "allocation must be deterministic");
        assert_eq!(first.offsets[s5], Some(0));
        assert_eq!(first.offsets[s2a], Some(5));
        assert_eq!(first.offsets[s2b], Some(7));
        assert_eq!(first.slots_used, 9);
    }

    #[test]
    fn scalar_coloring_order_is_first_occurrence_not_table_order() {
        // Crown graph S3: scalars a1..a3, b1..b3 with ai-bj edges for i != j
        // (bipartite, optimal 2 slots). The table order interleaves the parts
        // (a1, b1, a2, b2, a3, b3), which greedy-colors to 3 slots; the
        // first-occurrence order groups them (a1, a2, a3, b1, b2, b3 — set by
        // the preamble below), which colors to the optimal 2. Edges are built
        // from pair cliques: per edge (u, v), store u then v (v's store sees
        // u live-after), then read both to end the ranges.
        let mut mir = Mir::new();
        let a1 = mir.push_temp("a1", 1);
        let b1 = mir.push_temp("b1", 1);
        let a2 = mir.push_temp("a2", 1);
        let b2 = mir.push_temp("b2", 1);
        let a3 = mir.push_temp("a3", 1);
        let b3 = mir.push_temp("b3", 1);
        let blk = mir.push_block();
        // Preamble: pin first-occurrence order without creating edges
        // (singleton cliques only).
        for t in [a1, a2, a3, b1, b2, b3] {
            store_const(&mut mir, blk, t, 0);
            let v = load(&mut mir, blk, t);
            store_out(&mut mir, blk, v);
        }
        // Crown edges.
        for (u, v) in [(a1, b2), (a1, b3), (a2, b1), (a2, b3), (a3, b1), (a3, b2)] {
            store_const(&mut mir, blk, u, 1);
            store_const(&mut mir, blk, v, 1);
            let lu = load(&mut mir, blk, u);
            store_out(&mut mir, blk, lu);
            let lv = load(&mut mir, blk, v);
            store_out(&mut mir, blk, lv);
        }
        let alloc = allocate_temps(&mir).unwrap();
        assert_eq!(
            alloc.slots_used, 2,
            "first-occurrence greedy coloring must reach the optimum"
        );
        for t in [a1, a2, a3] {
            assert_eq!(alloc.offsets[t], Some(0));
        }
        for t in [b1, b2, b3] {
            assert_eq!(alloc.offsets[t], Some(1));
        }
        let again = allocate_temps(&mir).unwrap();
        assert_eq!(alloc, again, "coloring must be deterministic");
    }

    #[test]
    fn scalar_budget_overflow_is_a_clean_error() {
        // Two interfering 2000-slot arrays fill 0..4000; 97 mutually
        // interfering scalars cannot fit in the remaining 96 slots.
        let mut mir = Mir::new();
        let big_a = mir.push_temp("big_a", 2000);
        let big_b = mir.push_temp("big_b", 2000);
        let scalars: Vec<TempId> = (0..97).map(|i| mir.push_temp(format!("s{i}"), 1)).collect();
        let blk = mir.push_block();
        store_const(&mut mir, blk, big_a, 1);
        store_const(&mut mir, blk, big_b, 1);
        for &t in &scalars {
            store_const(&mut mir, blk, t, 1);
        }
        for t in [big_a, big_b].iter().chain(&scalars) {
            let v = load(&mut mir, blk, *t);
            store_out(&mut mir, blk, v);
        }
        let err = allocate_temps(&mir).unwrap_err();
        assert_eq!(err.to_string(), "Temporary memory limit exceeded");
    }

    #[test]
    fn first_fit_fills_gaps() {
        // big (4) interferes with a and b; a and b do not interfere with each
        // other (disjoint lifetimes), both fit at offset 4... a gets 4; b gets
        // 4 as well since only big constrains it.
        let mut mir = Mir::new();
        let big = mir.push_temp("big", 4);
        let a = mir.push_temp("a", 1);
        let b = mir.push_temp("b", 1);
        let blk = mir.push_block();
        store_const(&mut mir, blk, big, 9);
        store_const(&mut mir, blk, a, 1);
        let va = load(&mut mir, blk, a);
        store_out(&mut mir, blk, va);
        store_const(&mut mir, blk, b, 2);
        let vb = load(&mut mir, blk, b);
        store_out(&mut mir, blk, vb);
        let vbig = load(&mut mir, blk, big);
        store_out(&mut mir, blk, vbig);
        let alloc = allocate_temps(&mir).unwrap();
        assert_eq!(alloc.offsets[big], Some(0));
        assert_eq!(alloc.offsets[a], Some(4));
        assert_eq!(alloc.offsets[b], Some(4));
        assert_eq!(alloc.slots_used, 5);
    }
}
