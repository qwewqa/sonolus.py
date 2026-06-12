//! SSA machinery (PORT.md T1.3): Braun et al. on-the-fly SSA construction and
//! Boissinot-style out-of-SSA translation with coalescing.
//!
//! Per decision D10, the `minimal` pipeline does **not** run either of these on
//! temp blocks — they ship here as tested infrastructure and become
//! load-bearing at W2 `Mem2Reg` (T3.4). The unit tests drive them with hand-built
//! MIR (loops needing phis, trivial-phi chains, swap cycles).
//!
//! # Braun construction ([`SsaBuilder`])
//!
//! Implements `write_variable` / `read_variable` / `seal_block` from Braun et
//! al. 2013 ("Simple and Efficient Construction of Static Single Assignment
//! Form"), including incomplete phis in unsealed blocks and on-the-fly trivial
//! phi removal. The *variable* abstraction is an opaque [`Var`] index: W2 maps
//! each promotable size-1 temp-block slot to a `Var` and replaces its
//! loads/stores with `read_variable`/`write_variable` calls.
//!
//! Deviations from the paper, for iterativeness and arena friendliness:
//!
//! - `read_variable`'s recursion is replaced by an explicit single-predecessor
//!   chain walk plus a worklist of phis awaiting operands (deferring
//!   `addPhiOperands` is safe because the phi is recorded as the block's
//!   definition *before* its operands are read — the paper's cycle-breaking
//!   trick).
//! - Removed trivial phis are recorded in a forwarding map; values resolve
//!   through it on every read, and *raw* references inside still-live phi
//!   operands are rewritten eagerly at removal time (so a live phi never
//!   references a dead one, keeping user re-checking a simple scan).
//!   [`SsaBuilder::finish`] resolves every operand in the whole MIR once at
//!   the end and drops dead phis.
//! - Reading a variable that is undefined on some path yields `ConstInt(0)`
//!   (legacy `ToSSA` produces an `err` SSA place for the same situation; such
//!   reads are out of contract — see the `ssa.py` comment — so any
//!   deterministic value is equally valid).
//!
//! # Out-of-SSA ([`destruct_ssa`])
//!
//! Boissinot et al. 2009 ("Revisiting Out-of-SSA Translation for Correctness,
//! Code Quality and Efficiency"), in its basic conservative form:
//!
//! 1. Edges from conditionally terminated (`Branch`) predecessors into phi
//!    blocks are split, so parallel copies only ever land at the end of plain
//!    `Jump` blocks: they run only on the taken path, and they can never be
//!    crossed by a dispatcher-spliced terminator-test evaluation.
//! 2. SSA-value liveness (phi-aware: a phi argument is a use at the end of its
//!    predecessor; a phi defines at its block's head).
//! 3. *Slotted* values are partitioned into congruence classes by
//!    interference-aware coalescing (union-find; classes merge when no two
//!    members interfere). Interference is the standard "live at the other's
//!    definition" test, without Boissinot's value-equivalence refinement
//!    (conservative: merges fewer classes, never incorrect), with two
//!    necessary extensions: phis of the same block always interfere (a
//!    parallel copy must not write one destination twice), and a phi
//!    additionally interferes with its predecessors' terminator-test values
//!    (the edge copies materialize *before* the lowered dispatcher evaluates
//!    the test, so the test's slot must survive them).
//! 4. Each class becomes a fresh **size-1 temp block** appended to the MIR
//!    temp table (exactly how legacy `FromSSA` materializes SSA places as
//!    `TempBlock`s), so the T1.3 allocator colors SSA scalar slots with the
//!    same machinery as user temp blocks. Slotted defs gain a
//!    `Store(class_temp, value)` right after the def; every use of a slotted
//!    value becomes a fresh `Load(class_temp)` directly before the user
//!    (single-use loads — exactly the lowering contract). A use *inside* a
//!    lazy `ShortCircuit` rhs tree instead becomes a fresh **unscheduled**
//!    load owned by the tree (the value must stay conditionally evaluated;
//!    reading the class temp lazily is exact because lazy trees contain no
//!    stores and the def's store ran before the owner).
//! 5. Phi argument transfers become **parallel copies** at predecessor ends
//!    (constant arguments are value sources; everything else is temp-to-temp),
//!    sequentialized with cycle breaking through one scratch temp
//!    ([`sequentialize_parallel_copies`]). Copies within one class disappear —
//!    that is the coalescing payoff.
//! 6. A second, temp-granularity coalescing round ([`crate::coalesce`], T3.5)
//!    then merges the surviving copy-related temps whose live ranges do not
//!    interfere — residual cross-class parallel copies plus the `gvnN`/`m2rN`
//!    copy chains the optimizer passes left behind — removes the self-copies
//!    that produces, and threads out any edge-split block whose copies all
//!    disappeared (each such block is a pure dispatcher round trip). This
//!    only runs when destruction actually slotted something, so the
//!    `minimal` baseline never changes.
//!
//! ## What gets slotted (the W2 legalization contract)
//!
//! `destruct_ssa` is the single point that turns *value SSA* MIR (multi-use
//! values, cross-block uses, phis — what `Mem2Reg` and the re-run W1 passes
//! produce) back into MIR satisfying the lowering contract (every scheduled
//! value used at most once, schedule = evaluation order; see `lower.rs`). It
//! runs unconditionally in `compile_cfg` after the pass pipeline and is a
//! no-op on MIR already in lowerable form (the `minimal` baseline). Slotted
//! values:
//!
//! - **S1** phis;
//! - **S2** non-constant phi arguments;
//! - **S3** values used outside their defining block (incl. another block's
//!   terminator test);
//! - **S4** values used more than once (lowering can splice a defining tree
//!   into at most one consumer);
//! - **S5** scheduled values referenced from inside a lazy tree (the inner
//!   reference becomes an unscheduled class-temp load, step 4 above) — this
//!   includes a `ShortCircuit` whose rhs root is itself a scheduled value;
//! - **S6** single-use same-block values whose splice would *reorder
//!   evaluation*: lowering moves a pending value's whole regenerated subtree
//!   from its schedule slot to its consumer's operand position, which is only
//!   semantically transparent when the regenerated statement forest's
//!   depth-first evaluation order equals the schedule order. A per-block
//!   check compares exactly those two orders (treating slotted values as
//!   order-preserving leaf loads) and slots the first out-of-order value,
//!   to a fixpoint. This is deliberately conservative — a pure constant
//!   subtree could move freely, but SCCP already folded those — and degrades
//!   exactly to the store+load the unpromoted memory form paid.

use std::collections::{HashMap, HashSet};
use std::fmt;

use crate::mir::{BlockId, BlockRef, IndexRef, Inst, Mir, Place, TempId, Terminator, Value};

/// An SSA variable handle for [`SsaBuilder`] (opaque; allocated by the caller).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct Var(pub u32);

/// Braun et al. on-the-fly SSA construction state. See the module docs.
#[derive(Debug)]
pub struct SsaBuilder {
    preds: Vec<Vec<BlockId>>,
    sealed: Vec<bool>,
    current_def: Vec<HashMap<Var, Value>>,
    /// Per unsealed block: incomplete phis in creation order.
    incomplete: Vec<Vec<(Var, Value)>>,
    /// Trivial-phi forwarding: removed phi -> replacement.
    forward: HashMap<Value, Value>,
    /// All phis ever created, in creation order, with their block.
    phis: Vec<(Value, BlockId)>,
    dead: HashSet<Value>,
    /// Phis whose operands still need filling (deferred recursion).
    pending: Vec<(Var, Value, BlockId)>,
}

impl SsaBuilder {
    /// A builder for `block_count` blocks with no predecessor edges yet
    /// (register them with [`Self::add_pred`]); all blocks start unsealed.
    pub fn new(block_count: usize) -> Self {
        Self {
            preds: vec![Vec::new(); block_count],
            sealed: vec![false; block_count],
            current_def: vec![HashMap::new(); block_count],
            incomplete: vec![Vec::new(); block_count],
            forward: HashMap::new(),
            phis: Vec::new(),
            dead: HashSet::new(),
            pending: Vec::new(),
        }
    }

    /// A builder over a complete MIR: predecessors from the terminators, all
    /// blocks unsealed.
    pub fn from_mir(mir: &Mir) -> Self {
        let mut builder = Self::new(mir.blocks.len());
        builder.preds = mir.predecessors();
        builder
    }

    /// Registers a predecessor edge (for incrementally built CFGs).
    pub fn add_pred(&mut self, block: BlockId, pred: BlockId) {
        if !self.preds[block].contains(&pred) {
            self.preds[block].push(pred);
        }
    }

    /// Resolves a value through the trivial-phi forwarding chain.
    pub fn resolve(&self, mut value: Value) -> Value {
        while let Some(&next) = self.forward.get(&value) {
            value = next;
        }
        value
    }

    /// Records `value` as the current definition of `var` in `block`.
    pub fn write_variable(&mut self, var: Var, block: BlockId, value: Value) {
        self.current_def[block].insert(var, value);
    }

    /// Reads the current value of `var` in `block`, inserting phis as needed.
    pub fn read_variable(&mut self, mir: &mut Mir, var: Var, block: BlockId) -> Value {
        let value = self.read_inner(mir, var, block);
        self.drain_pending(mir);
        self.resolve(value)
    }

    /// Seals a block: its predecessor set is final; incomplete phis get their
    /// operands now.
    pub fn seal_block(&mut self, mir: &mut Mir, block: BlockId) {
        assert!(!self.sealed[block], "block {block} sealed twice");
        self.sealed[block] = true;
        let pend = std::mem::take(&mut self.incomplete[block]);
        for (var, phi) in pend {
            self.pending.push((var, phi, block));
        }
        self.drain_pending(mir);
    }

    /// Finishes construction: every operand in the MIR is resolved through the
    /// forwarding map and dead phis are dropped. All blocks must be sealed.
    pub fn finish(self, mir: &mut Mir) {
        assert!(
            self.sealed.iter().all(|&s| s),
            "all blocks must be sealed before finish()"
        );
        assert!(self.pending.is_empty());
        let resolve = |mut value: Value| {
            while let Some(&next) = self.forward.get(&value) {
                value = next;
            }
            value
        };
        for inst in &mut mir.insts {
            Mir::for_each_operand_mut(inst, |v| *v = resolve(*v));
        }
        for block in &mut mir.blocks {
            if let Terminator::Branch { test, .. } = &mut block.terminator {
                *test = resolve(*test);
            }
            block.phis.retain(|p| !self.dead.contains(p));
        }
    }

    fn new_phi(&mut self, mir: &mut Mir, block: BlockId) -> Value {
        let phi = mir.push_inst(Inst::Phi { args: Vec::new() });
        mir.blocks[block].phis.push(phi);
        self.phis.push((phi, block));
        phi
    }

    /// The lookup walk of `readVariable`/`readVariableRecursive`, iterative.
    fn read_inner(&mut self, mir: &mut Mir, var: Var, block: BlockId) -> Value {
        let mut chain: Vec<BlockId> = Vec::new();
        let mut cur = block;
        let value = loop {
            if let Some(&v) = self.current_def[cur].get(&var) {
                break self.resolve(v);
            }
            if !self.sealed[cur] {
                // Incomplete CFG: placeholder phi, operands at seal time.
                let phi = self.new_phi(mir, cur);
                self.incomplete[cur].push((var, phi));
                self.current_def[cur].insert(var, phi);
                break phi;
            }
            match self.preds[cur].as_slice() {
                [] => {
                    // Undefined on this path (module docs): constant 0.
                    let undef = mir.push_inst(Inst::ConstInt(0));
                    self.current_def[cur].insert(var, undef);
                    break undef;
                }
                &[single] => {
                    chain.push(cur);
                    cur = single;
                }
                _ => {
                    // Multiple predecessors: record the phi as the definition
                    // *before* reading the operands (breaks read cycles).
                    let phi = self.new_phi(mir, cur);
                    self.current_def[cur].insert(var, phi);
                    self.pending.push((var, phi, cur));
                    break phi;
                }
            }
        };
        for b in chain {
            self.current_def[b].insert(var, value);
        }
        value
    }

    /// Fills operands of phis on the worklist (the deferred recursion of
    /// `addPhiOperands`).
    fn drain_pending(&mut self, mir: &mut Mir) {
        while let Some((var, phi, block)) = self.pending.pop() {
            if self.dead.contains(&phi) {
                continue;
            }
            let preds = self.preds[block].clone();
            let mut args = Vec::with_capacity(preds.len());
            for p in preds {
                let v = self.read_inner(mir, var, p);
                args.push((p, self.resolve(v)));
            }
            let Inst::Phi { args: slot } = &mut mir.insts[phi as usize] else {
                unreachable!("pending entries are phis");
            };
            *slot = args;
            self.try_remove_trivial(mir, phi);
        }
    }

    /// `tryRemoveTrivialPhi`, with a worklist instead of recursion.
    fn try_remove_trivial(&mut self, mir: &mut Mir, phi: Value) {
        let mut work = vec![phi];
        while let Some(phi) = work.pop() {
            if self.dead.contains(&phi) {
                continue;
            }
            let Inst::Phi { args } = mir.inst(phi) else {
                continue;
            };
            // A still-incomplete phi (unsealed block) is not trivial *yet*.
            if args.is_empty() && self.incomplete_contains(phi) {
                continue;
            }
            let mut same: Option<Value> = None;
            let mut trivial = true;
            for &(_, op) in args {
                let op = self.resolve(op);
                if op == phi {
                    continue; // self-reference
                }
                match same {
                    None => same = Some(op),
                    Some(s) if s == op => {}
                    Some(_) => {
                        trivial = false;
                        break;
                    }
                }
            }
            if !trivial {
                continue;
            }
            // Unreachable/self-only phi: undefined (module docs).
            let same = same.unwrap_or_else(|| mir.push_inst(Inst::ConstInt(0)));
            self.forward.insert(phi, same);
            self.dead.insert(phi);
            let &(_, block) = self
                .phis
                .iter()
                .find(|&&(p, _)| p == phi)
                .expect("phi was registered");
            mir.blocks[block].phis.retain(|&p| p != phi);
            // Rewrite raw references in live phis; re-check those users.
            for i in 0..self.phis.len() {
                let (user, _) = self.phis[i];
                if user == phi || self.dead.contains(&user) {
                    continue;
                }
                let Inst::Phi { args } = &mut mir.insts[user as usize] else {
                    continue;
                };
                let mut touched = false;
                for (_, a) in args.iter_mut() {
                    if *a == phi {
                        *a = same;
                        touched = true;
                    }
                }
                if touched {
                    work.push(user);
                }
            }
        }
    }

    fn incomplete_contains(&self, phi: Value) -> bool {
        self.incomplete
            .iter()
            .any(|list| list.iter().any(|&(_, p)| p == phi))
    }
}

// ----------------------------------------------------------------------------------
// Out-of-SSA
// ----------------------------------------------------------------------------------

/// An out-of-SSA failure (MIR outside the destruction contract).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DestructError {
    /// A value used across blocks (or as a phi argument) is neither a constant
    /// nor a scheduled instruction/phi.
    UnscheduledCrossBlockValue(Value),
    /// A phi has an argument keyed to a block that is not a predecessor.
    PhiArgNotFromPred(Value),
}

impl fmt::Display for DestructError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::UnscheduledCrossBlockValue(v) => {
                write!(f, "value {v} is used across blocks but is not scheduled")
            }
            Self::PhiArgNotFromPred(v) => {
                write!(f, "phi {v} has an argument from a non-predecessor block")
            }
        }
    }
}

impl std::error::Error for DestructError {}

/// One source of a parallel-copy move.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CopySrc {
    /// An SSA value (a constant) available at the predecessor's end.
    Value(Value),
    /// The current content of a temp.
    Temp(TempId),
}

/// One move of a parallel copy: `dst <- src`; parallel semantics (all reads
/// happen before any write).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Copy {
    pub dst: TempId,
    pub src: CopySrc,
}

/// Sequentializes a parallel copy into an ordered move list.
///
/// Destinations must be distinct. Temp-to-temp dependency cycles are broken
/// through a scratch temp obtained from `alloc_scratch` (called at most once;
/// the scratch is dead between cycles and is reused). Deterministic: ties
/// break on list order.
pub fn sequentialize_parallel_copies(
    copies: &[Copy],
    mut alloc_scratch: impl FnMut() -> TempId,
) -> Vec<Copy> {
    let mut out: Vec<Copy> = Vec::new();
    // Identity moves are no-ops.
    let mut remaining: Vec<Copy> = copies
        .iter()
        .copied()
        .filter(|c| c.src != CopySrc::Temp(c.dst))
        .collect();
    let mut scratch: Option<TempId> = None;
    while !remaining.is_empty() {
        // Emit any copy whose destination is not read by another pending copy.
        if let Some(i) = remaining.iter().enumerate().position(|(idx, c)| {
            !remaining
                .iter()
                .enumerate()
                .any(|(j, o)| j != idx && o.src == CopySrc::Temp(c.dst))
        }) {
            out.push(remaining.remove(i));
            continue;
        }
        // Only temp cycles remain: save the first destination to the scratch
        // temp and redirect its readers there.
        let d = remaining[0].dst;
        let s = *scratch.get_or_insert_with(&mut alloc_scratch);
        out.push(Copy {
            dst: s,
            src: CopySrc::Temp(d),
        });
        for c in &mut remaining {
            if c.src == CopySrc::Temp(d) {
                c.src = CopySrc::Temp(s);
            }
        }
    }
    out
}

/// Union-find over slotted values with eager member lists.
struct Classes {
    parent: HashMap<Value, Value>,
    members: HashMap<Value, Vec<Value>>,
}

impl Classes {
    fn new() -> Self {
        Self {
            parent: HashMap::new(),
            members: HashMap::new(),
        }
    }

    fn add(&mut self, v: Value) {
        if let std::collections::hash_map::Entry::Vacant(slot) = self.parent.entry(v) {
            slot.insert(v);
            self.members.insert(v, vec![v]);
        }
    }

    fn contains(&self, v: Value) -> bool {
        self.parent.contains_key(&v)
    }

    fn find(&self, mut v: Value) -> Value {
        while self.parent[&v] != v {
            v = self.parent[&v];
        }
        v
    }

    fn union(&mut self, a: Value, b: Value) {
        let (ra, rb) = (self.find(a), self.find(b));
        if ra == rb {
            return;
        }
        // Deterministic: smaller root id wins.
        let (winner, loser) = if ra < rb { (ra, rb) } else { (rb, ra) };
        self.parent.insert(loser, winner);
        let moved = self.members.remove(&loser).unwrap_or_default();
        self.members
            .get_mut(&winner)
            .expect("winner exists")
            .extend(moved);
    }
}

/// Per-value definition site.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum DefSite {
    /// Phi at the head of a block.
    PhiAt(BlockId),
    /// Scheduled instruction (block, schedule position).
    InstAt(BlockId, usize),
    /// Constant or unscheduled (lazy) instruction.
    None,
}

struct Liveness {
    preds: Vec<Vec<BlockId>>,
    def: Vec<DefSite>,
    live_in: Vec<HashSet<Value>>,
    live_out: Vec<HashSet<Value>>,
    /// Per block, per scheduled inst: used values (lazy uses flattened).
    uses: Vec<Vec<Vec<Value>>>,
    /// Per block: values used by the terminator test.
    term_uses: Vec<Vec<Value>>,
}

/// Collects the non-constant values an instruction uses. Lazy trees are walked
/// to their leaves; scheduled values referenced from inside a lazy tree
/// surface as uses (none exist in builder-produced MIR).
fn inst_value_uses(mir: &Mir, scheduled: &[bool], v: Value, out: &mut Vec<Value>) {
    match mir.inst(v) {
        Inst::ShortCircuit { lhs, rhs, .. } => {
            if !mir.is_const(*lhs) {
                out.push(*lhs);
            }
            let mut stack = vec![*rhs];
            while let Some(lv) = stack.pop() {
                if mir.is_const(lv) {
                    continue;
                }
                if scheduled[lv as usize] {
                    out.push(lv);
                    continue;
                }
                Mir::for_each_operand(mir.inst(lv), |o| stack.push(o));
            }
        }
        inst => Mir::for_each_operand(inst, |o| {
            if !mir.is_const(o) {
                out.push(o);
            }
        }),
    }
}

fn compute_liveness(mir: &Mir, scheduled: &[bool]) -> Result<Liveness, DestructError> {
    let n_blocks = mir.blocks.len();
    let preds = mir.predecessors();
    let mut def = vec![DefSite::None; mir.insts.len()];
    for (b, block) in mir.blocks.iter().enumerate() {
        for &phi in &block.phis {
            def[phi as usize] = DefSite::PhiAt(b);
        }
        for (i, &v) in block.insts.iter().enumerate() {
            def[v as usize] = DefSite::InstAt(b, i);
        }
    }
    let mut uses: Vec<Vec<Vec<Value>>> = Vec::with_capacity(n_blocks);
    let mut term_uses: Vec<Vec<Value>> = Vec::with_capacity(n_blocks);
    let mut phi_end_uses: Vec<Vec<Value>> = vec![Vec::new(); n_blocks];
    for block in &mir.blocks {
        let mut per_inst = Vec::with_capacity(block.insts.len());
        for &v in &block.insts {
            let mut u = Vec::new();
            inst_value_uses(mir, scheduled, v, &mut u);
            per_inst.push(u);
        }
        uses.push(per_inst);
        let mut t = Vec::new();
        if let Terminator::Branch { test, .. } = &block.terminator
            && !mir.is_const(*test)
        {
            t.push(*test);
        }
        term_uses.push(t);
    }
    for (b, block) in mir.blocks.iter().enumerate() {
        for &phi in &block.phis {
            let Inst::Phi { args } = mir.inst(phi) else {
                continue;
            };
            for &(p, a) in args {
                if !preds[b].contains(&p) {
                    return Err(DestructError::PhiArgNotFromPred(phi));
                }
                if !mir.is_const(a) {
                    phi_end_uses[p].push(a);
                }
            }
        }
    }
    // Backward fixpoint (round-robin; block counts in tests are small, and the
    // W2 production path may swap in a worklist + bitsets later).
    let mut live_in: Vec<HashSet<Value>> = vec![HashSet::new(); n_blocks];
    let mut live_out: Vec<HashSet<Value>> = vec![HashSet::new(); n_blocks];
    let mut changed = true;
    while changed {
        changed = false;
        for b in (0..n_blocks).rev() {
            let mut out_set: HashSet<Value> = HashSet::new();
            for succ in mir.blocks[b].terminator.successors() {
                out_set.extend(live_in[succ].iter().copied());
            }
            out_set.extend(phi_end_uses[b].iter().copied());
            let mut live = out_set.clone();
            live.extend(term_uses[b].iter().copied());
            for (i, &v) in mir.blocks[b].insts.iter().enumerate().rev() {
                live.remove(&v);
                live.extend(uses[b][i].iter().copied());
            }
            for &phi in &mir.blocks[b].phis {
                live.remove(&phi);
            }
            if live != live_in[b] || out_set != live_out[b] {
                changed = true;
                live_in[b] = live;
                live_out[b] = out_set;
            }
        }
    }
    Ok(Liveness {
        preds,
        def,
        live_in,
        live_out,
        uses,
        term_uses,
    })
}

impl Liveness {
    /// Is `query` live just after the definition of `target`?
    fn live_at_def(&self, mir: &Mir, target: Value, query: Value) -> bool {
        match self.def[target as usize] {
            DefSite::None => false,
            DefSite::PhiAt(b) => {
                if self.live_in[b].contains(&query) {
                    return true;
                }
                // The edge copies that define this phi run after the
                // predecessors' terminator tests are *scheduled* but before
                // the lowered dispatcher evaluates them: the test values must
                // survive the copies (module docs).
                self.preds[b]
                    .iter()
                    .any(|&p| self.term_uses[p].contains(&query))
            }
            DefSite::InstAt(b, pos) => {
                // Walk backward from the block end to just after position pos.
                let mut live: HashSet<Value> = self.live_out[b].clone();
                live.extend(self.term_uses[b].iter().copied());
                for (i, &v) in mir.blocks[b].insts.iter().enumerate().rev() {
                    if i == pos {
                        return live.contains(&query);
                    }
                    live.remove(&v);
                    live.extend(self.uses[b][i].iter().copied());
                }
                unreachable!("pos is a valid schedule index");
            }
        }
    }
}

/// Do two slotted values interfere? Symmetric "live at the other's def" test;
/// phis of the same block always interfere (distinct parallel-copy
/// destinations).
fn interferes(mir: &Mir, live: &Liveness, a: Value, b: Value) -> bool {
    if a == b {
        return false;
    }
    if let (DefSite::PhiAt(ba), DefSite::PhiAt(bb)) = (live.def[a as usize], live.def[b as usize])
        && ba == bb
    {
        return true;
    }
    live.live_at_def(mir, a, b) || live.live_at_def(mir, b, a)
}

fn temp_place(t: TempId) -> Place {
    Place {
        block: BlockRef::Temp(t),
        index: IndexRef::Const(0),
        offset: 0,
    }
}

/// Per-value use facts driving the slotting decisions (S1–S6, module docs).
struct UseFacts {
    /// Total references: eager operands, lazy-tree internal references,
    /// terminator tests, and phi arguments.
    counts: Vec<u32>,
    /// Used from a block other than the defining one (or by a phi arg, which
    /// materializes on a predecessor edge).
    cross_block: Vec<bool>,
    /// Referenced from inside a lazy tree (incl. as a scheduled rhs root).
    lazy_ref: Vec<bool>,
    /// Defining block of scheduled instructions and phis.
    def_block: Vec<Option<BlockId>>,
}

/// Collects [`UseFacts`] in one deterministic scan.
fn collect_use_facts(mir: &Mir, scheduled: &[bool]) -> UseFacts {
    let n = mir.insts.len();
    let mut facts = UseFacts {
        counts: vec![0; n],
        cross_block: vec![false; n],
        lazy_ref: vec![false; n],
        def_block: vec![None; n],
    };
    for (b, block) in mir.blocks.iter().enumerate() {
        for &phi in &block.phis {
            facts.def_block[phi as usize] = Some(b);
        }
        for &v in &block.insts {
            facts.def_block[v as usize] = Some(b);
        }
    }
    let note = |facts: &mut UseFacts, o: Value, b: BlockId| {
        if mir.is_const(o) {
            return;
        }
        facts.counts[o as usize] += 1;
        if facts.def_block[o as usize] != Some(b) {
            facts.cross_block[o as usize] = true;
        }
    };
    for (b, block) in mir.blocks.iter().enumerate() {
        for &v in &block.insts {
            match mir.inst(v) {
                Inst::ShortCircuit { lhs, rhs, .. } => {
                    note(&mut facts, *lhs, b);
                    if scheduled[*rhs as usize] {
                        // A scheduled rhs root must become an unscheduled
                        // class-temp load (S5): the tree may not reference it.
                        note(&mut facts, *rhs, b);
                        facts.lazy_ref[*rhs as usize] = true;
                    } else {
                        // Walk the owned tree; scheduled values referenced
                        // from inside are lazy references.
                        let mut stack = vec![*rhs];
                        while let Some(lv) = stack.pop() {
                            if mir.is_const(lv) || scheduled[lv as usize] {
                                continue;
                            }
                            Mir::for_each_operand(mir.inst(lv), |o| {
                                if !mir.is_const(o) && scheduled[o as usize] {
                                    note(&mut facts, o, b);
                                    facts.lazy_ref[o as usize] = true;
                                } else {
                                    stack.push(o);
                                }
                            });
                        }
                    }
                }
                inst => Mir::for_each_operand(inst, |o| note(&mut facts, o, b)),
            }
        }
        if let Terminator::Branch { test, .. } = &block.terminator {
            note(&mut facts, *test, b);
        }
        for &phi in &block.phis {
            let Inst::Phi { args } = mir.inst(phi) else {
                continue;
            };
            for &(_, a) in args {
                if !mir.is_const(a) {
                    facts.counts[a as usize] += 1;
                    // Phi args materialize as copies on predecessor edges:
                    // always treated as cross-block (and slotted via S2).
                    facts.cross_block[a as usize] = true;
                }
            }
        }
    }
    facts
}

/// The S6 order check for one block (module docs): compares the regenerated
/// statement forest's DFS evaluation order with the schedule, treating
/// slotted values as order-preserving leaves. Returns the values to slot
/// (empty = the block is splice-transparent).
fn order_violations(
    mir: &Mir,
    scheduled: &[bool],
    facts: &UseFacts,
    is_slotted: &HashSet<Value>,
    b: BlockId,
) -> Vec<Value> {
    enum W {
        Visit(Value),
        Emit(Value),
    }
    let insts = &mir.blocks[b].insts;
    if insts.is_empty() {
        return Vec::new();
    }
    let in_block: HashSet<Value> = insts.iter().copied().collect();
    let spliceable = |o: Value| -> bool {
        !mir.is_const(o)
            && scheduled.get(o as usize).copied().unwrap_or(false)
            && in_block.contains(&o)
            && !is_slotted.contains(&o)
            && facts.counts[o as usize] == 1
            && !facts.cross_block[o as usize]
            && !facts.lazy_ref[o as usize]
    };
    // The unique consumer of each spliceable value (counts == 1).
    let mut consumer: HashMap<Value, Value> = HashMap::new();
    for &v in insts {
        match mir.inst(v) {
            Inst::ShortCircuit { lhs, .. } => {
                if spliceable(*lhs) {
                    consumer.insert(*lhs, v);
                }
            }
            inst => Mir::for_each_operand(inst, |o| {
                if spliceable(o) {
                    consumer.insert(o, v);
                }
            }),
        }
    }
    let test_spliced = match &mir.blocks[b].terminator {
        Terminator::Branch { test, .. } if spliceable(*test) && !consumer.contains_key(test) => {
            Some(*test)
        }
        _ => None,
    };
    // DFS evaluation order of the regenerated forest: roots in schedule
    // order, each subtree postorder with operands left to right; the
    // terminator test's tree evaluates last (the dispatcher).
    let mut seq: Vec<Value> = Vec::with_capacity(insts.len());
    let emit_tree = |root: Value, seq: &mut Vec<Value>| {
        let mut work = vec![W::Visit(root)];
        while let Some(item) = work.pop() {
            match item {
                W::Emit(v) => seq.push(v),
                W::Visit(v) => {
                    work.push(W::Emit(v));
                    let mut kids: Vec<Value> = Vec::new();
                    match mir.inst(v) {
                        Inst::ShortCircuit { lhs, .. } => {
                            if consumer.get(lhs) == Some(&v) {
                                kids.push(*lhs);
                            }
                        }
                        inst => Mir::for_each_operand(inst, |o| {
                            if consumer.get(&o) == Some(&v) {
                                kids.push(o);
                            }
                        }),
                    }
                    for &k in kids.iter().rev() {
                        work.push(W::Visit(k));
                    }
                }
            }
        }
    };
    for &v in insts {
        if consumer.contains_key(&v) || test_spliced == Some(v) {
            continue;
        }
        emit_tree(v, &mut seq);
    }
    if let Some(t) = test_spliced {
        emit_tree(t, &mut seq);
    }
    debug_assert_eq!(seq.len(), insts.len(), "forest covers the schedule");
    for (i, (&got, &want)) in seq.iter().zip(insts.iter()).enumerate() {
        if got != want {
            let candidate = insts[i];
            if spliceable(candidate) {
                return vec![candidate];
            }
            // Defensive fallback: slot every spliced value in the block (the
            // mismatch culprit is always a delayed splice; see module docs).
            return insts.iter().copied().filter(|&v| spliceable(v)).collect();
        }
    }
    Vec::new()
}

/// Out-of-SSA translation and lowering-contract legalization. See the module
/// docs. Unconditional in the pipeline; a no-op (and cheap) on MIR already in
/// lowerable form.
///
/// # Errors
///
/// [`DestructError`] when the MIR violates the destruction contract.
#[allow(clippy::too_many_lines, clippy::needless_range_loop)] // block ids index several tables
pub fn destruct_ssa(mir: &mut Mir) -> Result<(), DestructError> {
    // 1. Split edges from multi-successor predecessors into phi blocks.
    let split_blocks = split_pred_edges(mir);

    let scheduled = mir.scheduled_mask();
    let facts = collect_use_facts(mir, &scheduled);

    // 2. Slotted values (first-encounter order; deterministic). S1–S5 first.
    let mut slotted: Vec<Value> = Vec::new();
    let mut is_slotted: HashSet<Value> = HashSet::new();
    {
        let add = |v: Value, slotted: &mut Vec<Value>, set: &mut HashSet<Value>| {
            if set.insert(v) {
                slotted.push(v);
            }
        };
        for block in &mir.blocks {
            for &phi in &block.phis {
                add(phi, &mut slotted, &mut is_slotted); // S1
                let Inst::Phi { args } = mir.inst(phi) else {
                    continue;
                };
                for &(_, a) in args {
                    if !mir.is_const(a) {
                        add(a, &mut slotted, &mut is_slotted); // S2
                    }
                }
            }
        }
        // S3/S4/S5 in one ascending sweep (deterministic): any scheduled
        // value used cross-block, more than once, or from inside a lazy tree.
        // (Unscheduled multi-use values stay unslotted: lowering rejects them
        // loudly, same as before this legalization existed.)
        for v in 0..mir.insts.len() {
            let value = Value::try_from(v).expect("arena fits u32");
            if scheduled[v]
                && !mir.is_const(value)
                && !matches!(mir.inst(value), Inst::Phi { .. })
                && (facts.cross_block[v] || facts.counts[v] > 1 || facts.lazy_ref[v])
            {
                add(value, &mut slotted, &mut is_slotted);
            }
        }
        // S6: order-preservation fixpoint (each round slots >= 1 value).
        loop {
            let mut added = false;
            for b in 0..mir.blocks.len() {
                for v in order_violations(mir, &scheduled, &facts, &is_slotted, b) {
                    if is_slotted.insert(v) {
                        slotted.push(v);
                        added = true;
                    }
                }
            }
            if !added {
                break;
            }
        }
    }
    if slotted.is_empty() {
        // Already in lowerable form (no phis: phis are always slotted).
        debug_assert!(mir.blocks.iter().all(|b| b.phis.is_empty()));
        return Ok(());
    }

    let live = compute_liveness(mir, &scheduled)?;
    for &v in &slotted {
        if live.def[v as usize] == DefSite::None {
            return Err(DestructError::UnscheduledCrossBlockValue(v));
        }
    }

    // 3. Coalesce phi destinations with their argument classes.
    let mut classes = Classes::new();
    for &v in &slotted {
        classes.add(v);
    }
    for b in 0..mir.blocks.len() {
        for pi in 0..mir.blocks[b].phis.len() {
            let phi = mir.blocks[b].phis[pi];
            let Inst::Phi { args } = mir.inst(phi).clone() else {
                continue;
            };
            for (_, a) in args {
                if mir.is_const(a) || !classes.contains(a) {
                    continue;
                }
                let (ra, rp) = (classes.find(a), classes.find(phi));
                if ra == rp {
                    continue;
                }
                let conflict = classes.members[&ra].iter().any(|&ma| {
                    classes.members[&rp]
                        .iter()
                        .any(|&mp| interferes(mir, &live, ma, mp))
                });
                if !conflict {
                    classes.union(ra, rp);
                }
            }
        }
    }

    // 4. One fresh size-1 temp per class (first-encounter order).
    let mut class_temp: HashMap<Value, TempId> = HashMap::new();
    for &v in &slotted {
        let root = classes.find(v);
        let next_name = class_temp.len();
        class_temp
            .entry(root)
            .or_insert_with(|| mir.push_temp(format!("ssa.{next_name}"), 1));
    }
    // Scratch temp for parallel-copy cycles (unused -> never allocated a slot).
    let scratch = mir.push_temp("ssa.scratch", 1);

    // 5. Parallel copies per predecessor block.
    let mut copies_at: Vec<Vec<Copy>> = vec![Vec::new(); mir.blocks.len()];
    for block in &mir.blocks {
        for &phi in &block.phis {
            let Inst::Phi { args } = mir.inst(phi) else {
                continue;
            };
            let dst = class_temp[&classes.find(phi)];
            for &(p, a) in args {
                if mir.is_const(a) {
                    copies_at[p].push(Copy {
                        dst,
                        src: CopySrc::Value(a),
                    });
                } else {
                    let src = class_temp[&classes.find(a)];
                    if src != dst {
                        copies_at[p].push(Copy {
                            dst,
                            src: CopySrc::Temp(src),
                        });
                    }
                }
            }
        }
    }

    // 6. Rewrite blocks: loads before uses (one per *occurrence* — a value
    // used twice by one instruction gets two loads, each consumed once),
    // stores after slotted defs, unscheduled loads inside lazy trees, copies
    // and (rewritten) terminator tests at block ends; phis disappear.
    let temp_of = |classes: &Classes, o: Value| class_temp[&classes.find(o)];
    for b in 0..mir.blocks.len() {
        let old_insts = std::mem::take(&mut mir.blocks[b].insts);
        let mut new_insts: Vec<Value> = Vec::with_capacity(old_insts.len());
        for v in old_insts {
            match mir.insts[v as usize].clone() {
                Inst::ShortCircuit {
                    op,
                    pure_node,
                    lhs,
                    rhs,
                } => {
                    // The eager lhs reloads from its class temp like any
                    // operand; the rhs must stay lazy: a scheduled (slotted)
                    // rhs root becomes an *unscheduled* class-temp load, and
                    // tree-internal references to slotted values do the same
                    // (step 4 of the module docs — exact because lazy trees
                    // contain no stores).
                    let mut new_lhs = lhs;
                    if is_slotted.contains(&lhs) {
                        let load = mir.push_inst(Inst::Load {
                            place: temp_place(temp_of(&classes, lhs)),
                        });
                        new_insts.push(load);
                        new_lhs = load;
                    }
                    let mut new_rhs = rhs;
                    if scheduled.get(rhs as usize).copied().unwrap_or(false)
                        && is_slotted.contains(&rhs)
                    {
                        new_rhs = mir.push_inst(Inst::Load {
                            place: temp_place(temp_of(&classes, rhs)),
                        });
                    } else {
                        rewrite_lazy_slotted_refs(
                            mir,
                            &scheduled,
                            &is_slotted,
                            &classes,
                            &class_temp,
                            rhs,
                        );
                    }
                    if (new_lhs, new_rhs) != (lhs, rhs) {
                        mir.insts[v as usize] = Inst::ShortCircuit {
                            op,
                            pure_node,
                            lhs: new_lhs,
                            rhs: new_rhs,
                        };
                    }
                }
                mut inst => {
                    let mut ops: Vec<Value> = Vec::new();
                    Mir::for_each_operand(&inst, |o| ops.push(o));
                    if ops.iter().any(|o| is_slotted.contains(o)) {
                        let mut new_ops: Vec<Option<Value>> = Vec::with_capacity(ops.len());
                        for &o in &ops {
                            if is_slotted.contains(&o) {
                                let load = mir.push_inst(Inst::Load {
                                    place: temp_place(temp_of(&classes, o)),
                                });
                                new_insts.push(load);
                                new_ops.push(Some(load));
                            } else {
                                new_ops.push(None);
                            }
                        }
                        let mut idx = 0;
                        Mir::for_each_operand_mut(&mut inst, |o| {
                            if let Some(load) = new_ops[idx] {
                                *o = load;
                            }
                            idx += 1;
                        });
                        mir.insts[v as usize] = inst;
                    }
                }
            }
            new_insts.push(v);
            if is_slotted.contains(&v) {
                let t = temp_of(&classes, v);
                let store = mir.push_inst(Inst::Store {
                    place: temp_place(t),
                    value: v,
                });
                new_insts.push(store);
            }
        }
        mir.blocks[b].phis.clear();
        // Edge copies (sequentialized).
        let copies = std::mem::take(&mut copies_at[b]);
        if !copies.is_empty() {
            for c in sequentialize_parallel_copies(&copies, || scratch) {
                let src_value = match c.src {
                    CopySrc::Value(v) => v,
                    CopySrc::Temp(t) => {
                        let load = mir.push_inst(Inst::Load {
                            place: temp_place(t),
                        });
                        new_insts.push(load);
                        load
                    }
                };
                let store = mir.push_inst(Inst::Store {
                    place: temp_place(c.dst),
                    value: src_value,
                });
                new_insts.push(store);
            }
        }
        // Terminator test: a slotted test reloads from its class temp.
        let mut term = mir.blocks[b].terminator.clone();
        if let Terminator::Branch { test, .. } = &mut term
            && is_slotted.contains(test)
        {
            let t = class_temp[&classes.find(*test)];
            let load = mir.push_inst(Inst::Load {
                place: temp_place(t),
            });
            new_insts.push(load);
            *test = load;
        }
        mir.blocks[b].terminator = term;
        mir.blocks[b].insts = new_insts;
    }

    // 7. Temp-granularity copy coalescing + empty-split-block threading
    // (module docs step 6; crate::coalesce). Reached only when values were
    // slotted, so lowerable (minimal) MIR is never touched.
    crate::coalesce::coalesce_and_thread(mir, &split_blocks);
    Ok(())
}

/// Rewrites references to slotted values *inside* a lazy `ShortCircuit` rhs
/// tree into fresh **unscheduled** class-temp loads (one per occurrence) —
/// the value stays conditionally evaluated, and reading the class temp lazily
/// is exact because lazy trees contain no stores and the def's store ran
/// before the owner (module docs). Iterative; single-owner trees need no
/// visited set.
fn rewrite_lazy_slotted_refs(
    mir: &mut Mir,
    scheduled: &[bool],
    is_slotted: &HashSet<Value>,
    classes: &Classes,
    class_temp: &HashMap<Value, TempId>,
    root: Value,
) {
    let mut stack = vec![root];
    while let Some(v) = stack.pop() {
        if mir.is_const(v) || scheduled.get(v as usize).copied().unwrap_or(false) {
            continue;
        }
        let mut inst = mir.insts[v as usize].clone();
        let mut ops: Vec<Value> = Vec::new();
        Mir::for_each_operand(&inst, |o| ops.push(o));
        let mut new_ops: Vec<Option<Value>> = Vec::with_capacity(ops.len());
        let mut any = false;
        for &o in &ops {
            let o_scheduled = scheduled.get(o as usize).copied().unwrap_or(false);
            if !mir.is_const(o) && o_scheduled {
                // S5 slotted every lazy-referenced scheduled value; a miss
                // here would be a slotting bug (lowering rejects it loudly).
                debug_assert!(is_slotted.contains(&o), "lazy ref to unslotted value {o}");
                if is_slotted.contains(&o) {
                    let load = mir.push_inst(Inst::Load {
                        place: temp_place(class_temp[&classes.find(o)]),
                    });
                    new_ops.push(Some(load));
                    any = true;
                    continue;
                }
            } else if !mir.is_const(o) {
                stack.push(o);
            }
            new_ops.push(None);
        }
        if any {
            let mut idx = 0;
            Mir::for_each_operand_mut(&mut inst, |o| {
                if let Some(load) = new_ops[idx] {
                    *o = load;
                }
                idx += 1;
            });
            mir.insts[v as usize] = inst;
        }
    }
}

/// Splits every edge from a conditionally terminated predecessor into a block
/// with phis (the inserted block carries the parallel copies for that edge
/// only). Splitting on *any* `Branch` predecessor — not just critical edges —
/// guarantees parallel copies only ever land in plain `Jump` blocks: a
/// `Branch` block's test expression is evaluated by the lowered dispatcher
/// *after* the block's statements, so copies appended there would clobber the
/// temps its spliced test loads read. Returns the created block ids; split
/// blocks whose copies all coalesce away are threaded back out at the end of
/// destruction (`crate::coalesce`).
fn split_pred_edges(mir: &mut Mir) -> Vec<BlockId> {
    let mut created: Vec<BlockId> = Vec::new();
    let n = mir.blocks.len();
    for b in 0..n {
        if mir.blocks[b].phis.is_empty() {
            continue;
        }
        let preds = mir.predecessors()[b].clone();
        for p in preds {
            if matches!(mir.blocks[p].terminator, Terminator::Jump(_)) {
                continue;
            }
            let m = mir.push_block();
            created.push(m);
            mir.blocks[m].terminator = Terminator::Jump(b);
            match &mut mir.blocks[p].terminator {
                Terminator::Jump(t) => {
                    if *t == b {
                        *t = m;
                    }
                }
                Terminator::Branch { cases, default, .. } => {
                    for (_, t) in cases.iter_mut() {
                        if *t == b {
                            *t = m;
                        }
                    }
                    if *default == Some(b) {
                        *default = Some(m);
                    }
                }
                Terminator::Exit => {}
            }
            for pi in 0..mir.blocks[b].phis.len() {
                let phi = mir.blocks[b].phis[pi];
                let Inst::Phi { args } = &mut mir.insts[phi as usize] else {
                    continue;
                };
                for (src, _) in args.iter_mut() {
                    if *src == p {
                        *src = m;
                    }
                }
            }
        }
    }
    created
}

#[cfg(test)]
mod tests {
    // Toolchain note: clippy 1.96 newly lints test code under --all-targets;
    // exact f64 equality is the assertion contract here (ARCHITECTURE §6).
    // test constants are tiny; the casts cannot truncate/wrap in practice.
    #![allow(
        clippy::float_cmp,
        clippy::cast_possible_truncation,
        clippy::cast_possible_wrap
    )]
    use super::*;
    use crate::alloc::allocate_temps;
    use crate::interpret::Interpreter;
    use crate::lower::lower_mir;
    use crate::mir::CaseCond;
    use crate::ops::Op;

    fn sched(mir: &mut Mir, block: usize, inst: Inst) -> Value {
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

    /// Runs a phi-free MIR end to end (allocate -> lower -> emit -> interpret)
    /// and returns the interpreter for inspection.
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

    // --- Braun construction ---------------------------------------------------

    #[test]
    fn straight_line_reads_see_writes_without_phis() {
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        mir.blocks[b0].terminator = Terminator::Jump(b1);
        let mut ssa = SsaBuilder::from_mir(&mir);
        let x = Var(0);
        let c = mir.push_inst(Inst::ConstInt(42));
        ssa.seal_block(&mut mir, b0);
        ssa.seal_block(&mut mir, b1);
        ssa.write_variable(x, b0, c);
        let read = ssa.read_variable(&mut mir, x, b1);
        assert_eq!(read, c, "single-pred chain walk finds the def");
        assert!(mir.blocks.iter().all(|b| b.phis.is_empty()));
        ssa.finish(&mut mir);
    }

    #[test]
    fn diamond_join_creates_one_phi() {
        // 0 -> {1, 2} -> 3; x written differently in 1 and 2.
        let mut mir = Mir::new();
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
        let mut ssa = SsaBuilder::from_mir(&mir);
        let x = Var(0);
        for b in [b0, b1, b2, b3] {
            ssa.seal_block(&mut mir, b);
        }
        let c1 = mir.push_inst(Inst::ConstInt(10));
        let c2 = mir.push_inst(Inst::ConstInt(20));
        ssa.write_variable(x, b1, c1);
        ssa.write_variable(x, b2, c2);
        let read = ssa.read_variable(&mut mir, x, b3);
        assert_eq!(mir.blocks[b3].phis.len(), 1);
        assert_eq!(read, mir.blocks[b3].phis[0]);
        let Inst::Phi { args } = mir.inst(read) else {
            panic!("read must be the phi");
        };
        let mut got = args.clone();
        got.sort_unstable();
        assert_eq!(got, vec![(b1, c1), (b2, c2)]);
        ssa.finish(&mut mir);
    }

    #[test]
    fn same_value_join_removes_trivial_phi() {
        // Both arms write the same value: the join phi must vanish.
        let mut mir = Mir::new();
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
        let mut ssa = SsaBuilder::from_mir(&mir);
        let x = Var(0);
        for b in [b0, b1, b2, b3] {
            ssa.seal_block(&mut mir, b);
        }
        let c = mir.push_inst(Inst::ConstInt(7));
        ssa.write_variable(x, b1, c);
        ssa.write_variable(x, b2, c);
        let read = ssa.read_variable(&mut mir, x, b3);
        assert_eq!(read, c, "trivial phi resolves to the single value");
        ssa.finish(&mut mir);
        assert!(mir.blocks[b3].phis.is_empty());
    }

    #[test]
    fn loop_with_unmodified_variable_removes_trivial_phi_cycle() {
        // 0 -> 1 (header) -> {1, 2}: x written only in 0, read in 1 and 2.
        // The header phi(x0, phi) is trivial and must collapse to x0 — the
        // classic trivial-phi cycle from the paper.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let b2 = mir.push_block();
        mir.blocks[b0].terminator = Terminator::Jump(b1);
        let mut ssa = SsaBuilder::from_mir(&mir);
        let x = Var(0);
        ssa.seal_block(&mut mir, b0);
        let c = mir.push_inst(Inst::ConstInt(5));
        ssa.write_variable(x, b0, c);
        // Build the header before sealing it (the back edge is pending).
        let read_in_header = ssa.read_variable(&mut mir, x, b1);
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
        // Back edge now known (from_mir predated this terminator).
        ssa.add_pred(b1, b1);
        ssa.add_pred(b2, b1);
        ssa.seal_block(&mut mir, b1);
        ssa.seal_block(&mut mir, b2);
        let read_after = ssa.read_variable(&mut mir, x, b2);
        assert_eq!(ssa.resolve(read_in_header), c);
        assert_eq!(read_after, c);
        ssa.finish(&mut mir);
        assert!(mir.blocks[b1].phis.is_empty(), "trivial phi cycle removed");
    }

    #[test]
    fn loop_with_modified_variable_keeps_phi() {
        // i = 0; loop { i = Add(i, 1) } while test; read i after.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let b2 = mir.push_block();
        mir.blocks[b0].terminator = Terminator::Jump(b1);
        let mut ssa = SsaBuilder::from_mir(&mir);
        let i = Var(0);
        ssa.seal_block(&mut mir, b0);
        let zero = mir.push_inst(Inst::ConstInt(0));
        ssa.write_variable(i, b0, zero);
        let i_in = ssa.read_variable(&mut mir, i, b1);
        let one = mir.push_inst(Inst::ConstInt(1));
        let add = sched(
            &mut mir,
            b1,
            Inst::Op {
                op: Op::Add,
                pure_node: true,
                args: vec![i_in, one],
            },
        );
        ssa.write_variable(i, b1, add);
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
        ssa.add_pred(b1, b1);
        ssa.add_pred(b2, b1);
        ssa.seal_block(&mut mir, b1);
        ssa.seal_block(&mut mir, b2);
        let i_after = ssa.read_variable(&mut mir, i, b2);
        let i_in_resolved = ssa.resolve(i_in);
        ssa.finish(&mut mir);
        assert_eq!(mir.blocks[b1].phis.len(), 1, "loop-carried phi survives");
        let phi = mir.blocks[b1].phis[0];
        // Reading after the loop sees the def at the end of the body (the
        // incremented value), not the header phi.
        assert_eq!(i_after, add);
        let Inst::Phi { args } = mir.inst(phi) else {
            panic!()
        };
        let mut got = args.clone();
        got.sort_unstable();
        assert_eq!(got, vec![(b0, zero), (b1, add)]);
        assert_eq!(i_in_resolved, phi);
        // i_in inside the Add was resolved by finish().
        let Inst::Op { args, .. } = mir.inst(add) else {
            panic!()
        };
        assert_eq!(args[0], phi);
    }

    #[test]
    fn undefined_reads_yield_const_zero() {
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let mut ssa = SsaBuilder::from_mir(&mir);
        ssa.seal_block(&mut mir, b0);
        let v = ssa.read_variable(&mut mir, Var(0), b0);
        assert_eq!(mir.inst(v), &Inst::ConstInt(0));
        ssa.finish(&mut mir);
    }

    // --- Parallel-copy sequentialization ---------------------------------------

    /// Simulates a move list over symbolic temp contents.
    fn simulate(
        moves: &[Copy],
        initial: &HashMap<TempId, i64>,
        value_of: impl Fn(Value) -> i64,
    ) -> HashMap<TempId, i64> {
        let mut state = initial.clone();
        for m in moves {
            let v = match m.src {
                CopySrc::Value(v) => value_of(v),
                CopySrc::Temp(t) => *state.get(&t).expect("read of initialized temp"),
            };
            state.insert(m.dst, v);
        }
        state
    }

    #[test]
    fn swap_cycle_breaks_through_scratch() {
        // {a <- b, b <- a}: needs the scratch temp.
        let copies = vec![
            Copy {
                dst: 0,
                src: CopySrc::Temp(1),
            },
            Copy {
                dst: 1,
                src: CopySrc::Temp(0),
            },
        ];
        let mut scratch_calls = 0;
        let seq = sequentialize_parallel_copies(&copies, || {
            scratch_calls += 1;
            99
        });
        assert_eq!(scratch_calls, 1);
        let initial: HashMap<TempId, i64> = [(0, 10), (1, 20)].into();
        let result = simulate(&seq, &initial, |_| unreachable!());
        assert_eq!(result[&0], 20);
        assert_eq!(result[&1], 10);
    }

    #[test]
    fn chains_do_not_need_scratch() {
        // {a <- b, b <- c}: emitting a first preserves parallel semantics.
        let copies = vec![
            Copy {
                dst: 0,
                src: CopySrc::Temp(1),
            },
            Copy {
                dst: 1,
                src: CopySrc::Temp(2),
            },
        ];
        let seq = sequentialize_parallel_copies(&copies, || panic!("no scratch needed"));
        let initial: HashMap<TempId, i64> = [(0, 1), (1, 2), (2, 3)].into();
        let result = simulate(&seq, &initial, |_| unreachable!());
        assert_eq!((result[&0], result[&1]), (2, 3));
    }

    #[test]
    fn parallel_copy_property_random_cases() {
        // Randomized: up to 6 temps, mixed value/temp sources, vs the parallel
        // semantics oracle. Deterministic PRNG (SplitMix64).
        let mut state: u64 = 0x9E37_79B9_7F4A_7C15;
        let mut next = move || {
            state = state.wrapping_add(0x9E37_79B9_7F4A_7C15);
            let mut z = state;
            z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
            z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
            z ^ (z >> 31)
        };
        for _case in 0..2000 {
            let n_temps = 2 + (next() % 5) as usize; // 2..=6
            let n_copies = 1 + (next() % n_temps as u64) as usize;
            // Distinct destinations.
            let mut dsts: Vec<TempId> = (0..n_temps).collect();
            // Shuffle.
            for i in (1..dsts.len()).rev() {
                let j = (next() % (i as u64 + 1)) as usize;
                dsts.swap(i, j);
            }
            dsts.truncate(n_copies);
            let copies: Vec<Copy> = dsts
                .iter()
                .map(|&d| {
                    let src = if next() % 4 == 0 {
                        CopySrc::Value(1000 + (next() % 4) as Value)
                    } else {
                        CopySrc::Temp((next() % n_temps as u64) as TempId)
                    };
                    Copy { dst: d, src }
                })
                .collect();
            let scratch: TempId = 50;
            let seq = sequentialize_parallel_copies(&copies, || scratch);
            // Oracle: parallel semantics.
            let initial: HashMap<TempId, i64> = (0..n_temps).map(|t| (t, 100 + t as i64)).collect();
            let value_of = |v: Value| i64::from(v);
            let mut expected = initial.clone();
            for c in &copies {
                let v = match c.src {
                    CopySrc::Value(v) => value_of(v),
                    CopySrc::Temp(t) => initial[&t],
                };
                expected.insert(c.dst, v);
            }
            let mut initial_with_scratch = initial.clone();
            initial_with_scratch.insert(scratch, -1);
            let mut actual = simulate(&seq, &initial_with_scratch, value_of);
            actual.remove(&scratch);
            assert_eq!(actual, expected, "copies: {copies:?}, seq: {seq:?}");
        }
    }

    // --- Out-of-SSA end to end --------------------------------------------------

    #[test]
    fn diamond_phi_destructs_and_runs() {
        // result = (input == 0) ? 10 : 20, written to block 20[0].
        let mut mir = Mir::new();
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
        let phi = mir.push_inst(Inst::Phi {
            args: vec![(b1, c10), (b2, c20)],
        });
        mir.blocks[b3].phis.push(phi);
        sched(
            &mut mir,
            b3,
            Inst::Store {
                place: concrete_place(20, 0),
                value: phi,
            },
        );
        destruct_ssa(&mut mir).unwrap();
        assert!(mir.blocks.iter().all(|b| b.phis.is_empty()));
        for input in [0.0, 1.0] {
            let interp = run_mir(&mir, &[(-3, vec![input])]);
            let expected = if input == 0.0 { 10.0 } else { 20.0 };
            assert_eq!(read_cell(&interp, 20, 0), expected, "input {input}");
        }
    }

    #[test]
    fn loop_counter_destructs_coalesces_and_runs() {
        // i = 0; do { i = i + 1 } while (i < limit); 20[0] = i.
        // The loop-carried phi and the incremented value coalesce (their live
        // ranges do not overlap across the back edge once the phi dies at the
        // Add), so the loop body has no copies.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let b2 = mir.push_block();
        mir.blocks[b0].terminator = Terminator::Jump(b1);
        let zero = mir.push_inst(Inst::ConstInt(0));
        let phi = mir.push_inst(Inst::Phi { args: vec![] });
        mir.blocks[b1].phis.push(phi);
        let one = mir.push_inst(Inst::ConstInt(1));
        let add = sched(
            &mut mir,
            b1,
            Inst::Op {
                op: Op::Add,
                pure_node: true,
                args: vec![phi, one],
            },
        );
        let limit = sched(
            &mut mir,
            b1,
            Inst::Load {
                place: concrete_place(-3, 0),
            },
        );
        let cmp = sched(
            &mut mir,
            b1,
            Inst::Op {
                op: Op::Less,
                pure_node: true,
                args: vec![add, limit],
            },
        );
        mir.blocks[b1].terminator = Terminator::Branch {
            test: cmp,
            cases: vec![(CaseCond::Int(0), b2)],
            default: Some(b1),
        };
        let Inst::Phi { args } = &mut mir.insts[phi as usize] else {
            panic!()
        };
        *args = vec![(b0, zero), (b1, add)];
        sched(
            &mut mir,
            b2,
            Inst::Store {
                place: concrete_place(20, 0),
                value: add,
            },
        );
        // `add` is used cross-block (b2): slotted alongside the phi.
        destruct_ssa(&mut mir).unwrap();
        let interp = run_mir(&mir, &[(-3, vec![5.0])]);
        assert_eq!(read_cell(&interp, 20, 0), 5.0);
    }

    #[test]
    fn swap_loop_needs_cycle_break_and_runs() {
        // (a, b) = (1, 2); loop twice { (a, b) = (b, a) }; 20[0..2] = (a, b).
        // The two loop phis form a parallel-copy swap on the back edge.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let b2 = mir.push_block();
        mir.blocks[b0].terminator = Terminator::Jump(b1);
        let c1 = mir.push_inst(Inst::ConstInt(1));
        let c2 = mir.push_inst(Inst::ConstInt(2));
        let phi_a = mir.push_inst(Inst::Phi { args: vec![] });
        let phi_b = mir.push_inst(Inst::Phi { args: vec![] });
        let phi_n = mir.push_inst(Inst::Phi { args: vec![] });
        mir.blocks[b1].phis.extend([phi_a, phi_b, phi_n]);
        let zero = mir.push_inst(Inst::ConstInt(0));
        let one = mir.push_inst(Inst::ConstInt(1));
        let n_next = sched(
            &mut mir,
            b1,
            Inst::Op {
                op: Op::Add,
                pure_node: true,
                args: vec![phi_n, one],
            },
        );
        let two = mir.push_inst(Inst::ConstInt(2));
        let cmp = sched(
            &mut mir,
            b1,
            Inst::Op {
                op: Op::Less,
                pure_node: true,
                args: vec![n_next, two],
            },
        );
        mir.blocks[b1].terminator = Terminator::Branch {
            test: cmp,
            cases: vec![(CaseCond::Int(0), b2)],
            default: Some(b1),
        };
        // Swap on the back edge: a <- b, b <- a.
        let Inst::Phi { args } = &mut mir.insts[phi_a as usize] else {
            panic!()
        };
        *args = vec![(b0, c1), (b1, phi_b)];
        let Inst::Phi { args } = &mut mir.insts[phi_b as usize] else {
            panic!()
        };
        *args = vec![(b0, c2), (b1, phi_a)];
        let Inst::Phi { args } = &mut mir.insts[phi_n as usize] else {
            panic!()
        };
        *args = vec![(b0, zero), (b1, n_next)];
        sched(
            &mut mir,
            b2,
            Inst::Store {
                place: concrete_place(20, 0),
                value: phi_a,
            },
        );
        sched(
            &mut mir,
            b2,
            Inst::Store {
                place: concrete_place(20, 1),
                value: phi_b,
            },
        );
        destruct_ssa(&mut mir).unwrap();
        let interp = run_mir(&mir, &[]);
        // Entry (1,2); the back edge swaps once before the second header
        // entry, where the loop exits: (2,1).
        assert_eq!(read_cell(&interp, 20, 0), 2.0);
        assert_eq!(read_cell(&interp, 20, 1), 1.0);
    }

    #[test]
    fn coalescing_eliminates_self_copies() {
        // Chain: phi(b3) <- x defined in b1/b2 with non-overlapping ranges:
        // all coalesce into one class, so no copies are inserted at all.
        let mut mir = Mir::new();
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
        let x1 = sched(
            &mut mir,
            b1,
            Inst::Load {
                place: concrete_place(-3, 1),
            },
        );
        let x2 = sched(
            &mut mir,
            b2,
            Inst::Load {
                place: concrete_place(-3, 2),
            },
        );
        let phi = mir.push_inst(Inst::Phi {
            args: vec![(b1, x1), (b2, x2)],
        });
        mir.blocks[b3].phis.push(phi);
        sched(
            &mut mir,
            b3,
            Inst::Store {
                place: concrete_place(20, 0),
                value: phi,
            },
        );
        destruct_ssa(&mut mir).unwrap();
        // One class -> one temp; loads/stores only reference that single temp.
        assert_eq!(
            mir.temps
                .iter()
                .filter(|t| t.name.starts_with("ssa."))
                .count(),
            2, // the class temp + the (unused) scratch
        );
        let interp = run_mir(&mir, &[(-3, vec![0.0, 7.0, 9.0])]);
        assert_eq!(read_cell(&interp, 20, 0), 7.0);
        let interp = run_mir(&mir, &[(-3, vec![1.0, 7.0, 9.0])]);
        assert_eq!(read_cell(&interp, 20, 0), 9.0);
    }

    #[test]
    fn interfering_values_do_not_coalesce() {
        // x defined in b0, used in b3 AFTER the phi is read: x and the phi
        // interfere, so they get distinct temps and a copy materializes.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let b2 = mir.push_block();
        let b3 = mir.push_block();
        let x = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(-3, 1),
            },
        );
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
        let c20 = mir.push_inst(Inst::ConstInt(20));
        let phi = mir.push_inst(Inst::Phi {
            args: vec![(b1, x), (b2, c20)],
        });
        mir.blocks[b3].phis.push(phi);
        let sum = sched(
            &mut mir,
            b3,
            Inst::Op {
                op: Op::Add,
                pure_node: true,
                args: vec![phi, x],
            },
        );
        sched(
            &mut mir,
            b3,
            Inst::Store {
                place: concrete_place(20, 0),
                value: sum,
            },
        );
        destruct_ssa(&mut mir).unwrap();
        // x is live into b3 past the phi def -> interference -> 2 classes.
        assert_eq!(
            mir.temps
                .iter()
                .filter(|t| t.name.starts_with("ssa."))
                .count(),
            3, // x's class + phi's class + scratch
        );
        // input 0 -> branch to b1 -> phi = x = 7 -> sum = 14.
        let interp = run_mir(&mir, &[(-3, vec![0.0, 7.0])]);
        assert_eq!(read_cell(&interp, 20, 0), 14.0);
        // input 1 -> b2 -> phi = 20 -> sum = 27.
        let interp = run_mir(&mir, &[(-3, vec![1.0, 7.0])]);
        assert_eq!(read_cell(&interp, 20, 0), 27.0);
    }

    #[test]
    fn critical_edges_are_split() {
        // b0 branches to b1 (phi block) and b2; b2 jumps to b1. The b0->b1
        // edge is critical (b0 multi-succ, b1 multi-pred): copies must not
        // execute on the b0->b2 path.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block();
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
            cases: vec![(CaseCond::Int(0), b1)],
            default: Some(b2),
        };
        mir.blocks[b2].terminator = Terminator::Jump(b1);
        mir.blocks[b1].terminator = Terminator::Exit;
        let c1 = mir.push_inst(Inst::ConstInt(111));
        let c2 = mir.push_inst(Inst::ConstInt(222));
        let phi = mir.push_inst(Inst::Phi {
            args: vec![(b0, c1), (b2, c2)],
        });
        mir.blocks[b1].phis.push(phi);
        sched(
            &mut mir,
            b1,
            Inst::Store {
                place: concrete_place(20, 0),
                value: phi,
            },
        );
        let before = mir.blocks.len();
        destruct_ssa(&mut mir).unwrap();
        assert!(mir.blocks.len() > before, "the critical edge was split");
        let interp = run_mir(&mir, &[(-3, vec![0.0])]);
        assert_eq!(read_cell(&interp, 20, 0), 111.0);
        let interp = run_mir(&mir, &[(-3, vec![1.0])]);
        assert_eq!(read_cell(&interp, 20, 0), 222.0);
    }

    #[test]
    fn copy_only_split_block_is_threaded_out() {
        // Critical-edge diamond whose phi fully coalesces with both arguments:
        // the edge-split block ends up holding no copies and must be threaded
        // back out (b0's cond-0 edge points at b1 again), preserving phi
        // semantics and the branch's edge order.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let b2 = mir.push_block();
        let y = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(-3, 1),
            },
        );
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
        mir.blocks[b2].terminator = Terminator::Jump(b1);
        mir.blocks[b1].terminator = Terminator::Exit;
        let z = sched(
            &mut mir,
            b2,
            Inst::Load {
                place: concrete_place(-3, 2),
            },
        );
        let phi = mir.push_inst(Inst::Phi {
            args: vec![(b0, y), (b2, z)],
        });
        mir.blocks[b1].phis.push(phi);
        sched(
            &mut mir,
            b1,
            Inst::Store {
                place: concrete_place(20, 0),
                value: phi,
            },
        );
        destruct_ssa(&mut mir).unwrap();
        // The split block was created (arena grew) but is no longer reachable:
        // b0's branch points straight back at b1/b2 with conditions intact.
        let Terminator::Branch { cases, default, .. } = &mir.blocks[b0].terminator else {
            panic!("branch terminator survives");
        };
        assert_eq!(cases.as_slice(), &[(CaseCond::Int(0), b1)]);
        assert_eq!(*default, Some(b2));
        assert!(
            mir.reverse_postorder().iter().all(|&b| b < 3),
            "no split block stays reachable"
        );
        let interp = run_mir(&mir, &[(-3, vec![0.0, 7.0, 9.0])]);
        assert_eq!(read_cell(&interp, 20, 0), 7.0);
        let interp = run_mir(&mir, &[(-3, vec![1.0, 7.0, 9.0])]);
        assert_eq!(read_cell(&interp, 20, 0), 9.0);
    }

    #[test]
    fn braun_to_destruct_to_execution_pipeline() {
        // Full infrastructure roundtrip: build a counting loop with the
        // SsaBuilder, destruct, allocate, lower, emit, run.
        // sum = 0; i = 0; while (i < 4) { sum = sum + i; i = i + 1 } 20[0]=sum.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block(); // header: test
        let b2 = mir.push_block(); // body
        let b3 = mir.push_block(); // after
        mir.blocks[b0].terminator = Terminator::Jump(b1);
        let mut ssa = SsaBuilder::from_mir(&mir);
        let (sum, i) = (Var(0), Var(1));
        ssa.seal_block(&mut mir, b0);
        let zero_s = mir.push_inst(Inst::ConstInt(0));
        let zero_i = mir.push_inst(Inst::ConstInt(0));
        ssa.write_variable(sum, b0, zero_s);
        ssa.write_variable(i, b0, zero_i);
        // Header: while (i < 4).
        let i_h = ssa.read_variable(&mut mir, i, b1);
        let four = mir.push_inst(Inst::ConstInt(4));
        let cmp = sched(
            &mut mir,
            b1,
            Inst::Op {
                op: Op::Less,
                pure_node: true,
                args: vec![i_h, four],
            },
        );
        mir.blocks[b1].terminator = Terminator::Branch {
            test: cmp,
            cases: vec![(CaseCond::Int(0), b3)],
            default: Some(b2),
        };
        ssa.add_pred(b3, b1);
        ssa.add_pred(b2, b1);
        // Body.
        let sum_b = ssa.read_variable(&mut mir, sum, b2);
        let i_b = ssa.read_variable(&mut mir, i, b2);
        let new_sum = sched(
            &mut mir,
            b2,
            Inst::Op {
                op: Op::Add,
                pure_node: true,
                args: vec![sum_b, i_b],
            },
        );
        ssa.write_variable(sum, b2, new_sum);
        let one = mir.push_inst(Inst::ConstInt(1));
        let i_b2 = ssa.read_variable(&mut mir, i, b2);
        let new_i = sched(
            &mut mir,
            b2,
            Inst::Op {
                op: Op::Add,
                pure_node: true,
                args: vec![i_b2, one],
            },
        );
        ssa.write_variable(i, b2, new_i);
        mir.blocks[b2].terminator = Terminator::Jump(b1);
        ssa.add_pred(b1, b2);
        ssa.seal_block(&mut mir, b1);
        ssa.seal_block(&mut mir, b2);
        ssa.seal_block(&mut mir, b3);
        let sum_after = ssa.read_variable(&mut mir, sum, b3);
        sched(
            &mut mir,
            b3,
            Inst::Store {
                place: concrete_place(20, 0),
                value: sum_after,
            },
        );
        ssa.finish(&mut mir);
        assert_eq!(mir.blocks[b1].phis.len(), 2, "sum and i both need phis");
        destruct_ssa(&mut mir).unwrap();
        let alloc = allocate_temps(&mir).unwrap();
        assert!(alloc.slots_used >= 2, "two interfering scalars at least");
        let interp = run_mir(&mir, &[]);
        assert_eq!(read_cell(&interp, 20, 0), 6.0); // 0+1+2+3
    }

    // --- W2 legalization (S4/S5/S6; module docs) --------------------------------

    #[test]
    fn lowerable_mir_is_left_untouched() {
        // Already-lowerable MIR (the minimal-pipeline shape): destruct must be
        // a complete no-op — no class temps, no scratch, identical blocks.
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 1);
        let b0 = mir.push_block();
        let c = mir.push_inst(Inst::ConstInt(7));
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: Place {
                    block: BlockRef::Temp(t),
                    index: IndexRef::Const(0),
                    offset: 0,
                },
                value: c,
            },
        );
        let load = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: Place {
                    block: BlockRef::Temp(t),
                    index: IndexRef::Const(0),
                    offset: 0,
                },
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
        let before_blocks = mir.blocks.clone();
        let before_temps = mir.temps.len();
        let before_insts = mir.insts.len();
        destruct_ssa(&mut mir).unwrap();
        assert_eq!(mir.blocks, before_blocks, "no-op on lowerable MIR");
        assert_eq!(mir.temps.len(), before_temps);
        assert_eq!(mir.insts.len(), before_insts);
    }

    #[test]
    fn multi_use_value_is_slotted_and_runs() {
        // S4: one load feeding two stores (post-Mem2Reg shape).
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let v = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(-3, 0),
            },
        );
        for i in 0..2 {
            sched(
                &mut mir,
                b0,
                Inst::Store {
                    place: concrete_place(20, i),
                    value: v,
                },
            );
        }
        destruct_ssa(&mut mir).unwrap();
        let interp = run_mir(&mir, &[(-3, vec![42.0])]);
        assert_eq!(read_cell(&interp, 20, 0), 42.0);
        assert_eq!(read_cell(&interp, 20, 1), 42.0);
    }

    #[test]
    fn same_instruction_double_use_gets_two_loads() {
        // S4 per-occurrence: Add(v, v) needs one load per operand slot.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let v = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(-3, 0),
            },
        );
        let add = sched(
            &mut mir,
            b0,
            Inst::Op {
                op: Op::Add,
                pure_node: true,
                args: vec![v, v],
            },
        );
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: concrete_place(20, 0),
                value: add,
            },
        );
        destruct_ssa(&mut mir).unwrap();
        let interp = run_mir(&mir, &[(-3, vec![21.0])]);
        assert_eq!(read_cell(&interp, 20, 0), 42.0);
    }

    #[test]
    fn order_breaking_splice_is_slotted() {
        // S6: v = load 21[0]; 21[0] <- 99; 20[0] <- v. Splicing v into the
        // last store would re-read AFTER the write; the order check must slot
        // v so it captures the pre-write value.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let v = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(21, 0),
            },
        );
        let c99 = mir.push_inst(Inst::ConstInt(99));
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: concrete_place(21, 0),
                value: c99,
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
        destruct_ssa(&mut mir).unwrap();
        let interp = run_mir(&mir, &[(21, vec![5.0])]);
        assert_eq!(read_cell(&interp, 20, 0), 5.0, "pre-write value captured");
        assert_eq!(read_cell(&interp, 21, 0), 99.0);
    }

    #[test]
    fn adjacent_single_use_still_splices_without_slotting() {
        // The S6 check must NOT fire on an order-preserving chain: no class
        // temps appear (the whole point of promotion).
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let v = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(-3, 0),
            },
        );
        let one = mir.push_inst(Inst::ConstInt(1));
        let add = sched(
            &mut mir,
            b0,
            Inst::Op {
                op: Op::Add,
                pure_node: true,
                args: vec![v, one],
            },
        );
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: concrete_place(20, 0),
                value: add,
            },
        );
        let temps_before = mir.temps.len();
        destruct_ssa(&mut mir).unwrap();
        assert_eq!(mir.temps.len(), temps_before, "no slotting needed");
        let interp = run_mir(&mir, &[(-3, vec![4.0])]);
        assert_eq!(read_cell(&interp, 20, 0), 5.0);
    }

    #[test]
    fn lazy_referenced_value_becomes_unscheduled_class_temp_load() {
        // S5: a scheduled value referenced from inside a lazy tree. The inner
        // reference must become an unscheduled class-temp load so the value
        // stays conditionally evaluated and the tree stays self-contained.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let v = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(-3, 1),
            },
        );
        let lhs = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(-3, 0),
            },
        );
        let one = mir.push_inst(Inst::ConstInt(1));
        // Lazy tree: Add(v, 1) referencing the SCHEDULED v.
        let lazy_add = mir.push_inst(Inst::Op {
            op: Op::Add,
            pure_node: true,
            args: vec![v, one],
        });
        let sc = sched(
            &mut mir,
            b0,
            Inst::ShortCircuit {
                op: Op::And,
                pure_node: true,
                lhs,
                rhs: lazy_add,
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
        destruct_ssa(&mut mir).unwrap();
        // The lazy Add's first operand is now an UNSCHEDULED load.
        let scheduled = mir.scheduled_mask();
        let Inst::Op { args, .. } = mir.inst(lazy_add) else {
            panic!()
        };
        let Inst::Load { place } = mir.inst(args[0]) else {
            panic!("lazy ref must be a load, got {:?}", mir.inst(args[0]));
        };
        assert!(matches!(place.block, BlockRef::Temp(_)));
        assert!(
            !scheduled[args[0] as usize],
            "the load is lazy (unscheduled)"
        );
        // Behavior: lhs = 0 -> short-circuit (And yields 0); lhs = 1 -> Add(v, 1).
        let interp = run_mir(&mir, &[(-3, vec![0.0, 6.0])]);
        assert_eq!(read_cell(&interp, 20, 0), 0.0);
        let interp = run_mir(&mir, &[(-3, vec![1.0, 6.0])]);
        assert_eq!(read_cell(&interp, 20, 0), 7.0);
    }

    #[test]
    fn scheduled_rhs_root_becomes_unscheduled_class_temp_load() {
        // S5b: a ShortCircuit whose rhs root is itself a scheduled value.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let v = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(-3, 1),
            },
        );
        let lhs = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(-3, 0),
            },
        );
        let sc = sched(
            &mut mir,
            b0,
            Inst::ShortCircuit {
                op: Op::Or,
                pure_node: true,
                lhs,
                rhs: v,
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
        destruct_ssa(&mut mir).unwrap();
        let scheduled = mir.scheduled_mask();
        let Inst::ShortCircuit { rhs, .. } = mir.inst(sc) else {
            panic!()
        };
        assert!(matches!(mir.inst(*rhs), Inst::Load { .. }));
        assert!(!scheduled[*rhs as usize], "rhs is lazy again");
        // Or(1, _) short-circuits to 1; Or(0, v) yields v.
        let interp = run_mir(&mir, &[(-3, vec![1.0, 6.0])]);
        assert_eq!(read_cell(&interp, 20, 0), 1.0);
        let interp = run_mir(&mir, &[(-3, vec![0.0, 6.0])]);
        assert_eq!(read_cell(&interp, 20, 0), 6.0);
    }

    #[test]
    fn cross_block_use_is_slotted_and_runs() {
        // S3 (pre-existing behavior, re-pinned): def in block 0, use in 1.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        mir.blocks[b0].terminator = Terminator::Jump(b1);
        let v = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(-3, 0),
            },
        );
        sched(
            &mut mir,
            b1,
            Inst::Store {
                place: concrete_place(20, 0),
                value: v,
            },
        );
        destruct_ssa(&mut mir).unwrap();
        let interp = run_mir(&mir, &[(-3, vec![9.0])]);
        assert_eq!(read_cell(&interp, 20, 0), 9.0);
    }

    #[test]
    fn spliced_test_value_stays_last_or_slots() {
        // A branch test defined before a later effect: the dispatcher
        // evaluates the test AFTER the statements, so the test value must be
        // slotted (captured at its def) — minimal-form MIR always computes
        // the test last, but value-SSA MIR may not.
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
        let c9 = mir.push_inst(Inst::ConstInt(9));
        sched(
            &mut mir,
            b0,
            Inst::Store {
                place: concrete_place(21, 0),
                value: c9,
            },
        );
        mir.blocks[b0].terminator = Terminator::Branch {
            test,
            cases: vec![(CaseCond::Int(0), b1)],
            default: Some(b2),
        };
        let c1 = mir.push_inst(Inst::ConstInt(1));
        sched(
            &mut mir,
            b1,
            Inst::Store {
                place: concrete_place(20, 0),
                value: c1,
            },
        );
        let c2 = mir.push_inst(Inst::ConstInt(2));
        sched(
            &mut mir,
            b2,
            Inst::Store {
                place: concrete_place(20, 0),
                value: c2,
            },
        );
        destruct_ssa(&mut mir).unwrap();
        // 21[0] starts 0: the ORIGINAL test value 0 must take the 0-case,
        // even though 21[0] is 9 by dispatch time.
        let interp = run_mir(&mir, &[(21, vec![0.0])]);
        assert_eq!(read_cell(&interp, 20, 0), 1.0, "pre-write test captured");
    }
}
