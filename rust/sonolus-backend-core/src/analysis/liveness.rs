//! Liveness analysis over MIR values **and** temp blocks (PORT.md T2.1).
//!
//! Backward worklist dataflow with per-block gen/kill bitsets, in one fixpoint
//! over two tracked domains:
//!
//! - **Values** (arena ids, [`Value`]): each scheduled instruction defines its
//!   own value once (the arena is SSA-shaped — a value has exactly one
//!   definition point), so per-block gen/kill composition is exact.
//! - **Temp blocks** ([`TempId`]): same model as `alloc.rs` — a `Load` from a
//!   temp is a use; a `Store` to a **size-1** temp kills (any in-bounds write
//!   to a one-cell block is a full definition, the legacy `get_defs` rule);
//!   stores to larger temps define without killing and are not uses. No
//!   `array_defs`/dead-store refinement (deliberately conservative, like
//!   `alloc.rs`; live ranges only grow).
//!
//! # Phi semantics
//!
//! A phi argument `(p, a)` is a use of `a` at the **end of predecessor `p`**
//! (it is in `value_out(p)`, not in the phi block's `value_in`); a phi defines
//! its value at its block's head, so phi destinations are excluded from that
//! block's `value_in`. This matches `ssa.rs`'s out-of-SSA liveness.
//!
//! # Lazy trees (`ShortCircuit` rhs, `Select` arms — decision D11)
//!
//! Uses inside an instruction's unscheduled lazy tree (a `ShortCircuit` rhs,
//! either `Select` arm) are *conditional* uses that belong to the owning
//! instruction's program point: every value and temp read anywhere in the
//! lazy tree counts as used by the owner itself (may-use ⊆ live). Nothing
//! inside a lazy tree ever kills.
//!
//! # Terminator uses
//!
//! A `Branch` test value is used at the end of its block, after every
//! scheduled instruction (the lowered dispatcher evaluates it last).
//!
//! # Program points
//!
//! Per-block live-in/live-out sets are stored; intra-block points are walked
//! on demand with [`Liveness::cursor_at_end`], a backward cursor that applies
//! the same per-instruction transfer (cheap: one bitset clone per cursor).
//!
//! Unreachable blocks participate in the fixpoint like any other block (their
//! sets are sound and deterministic); consumers that care should filter by
//! [`DomTree::is_reachable`](crate::analysis::DomTree::is_reachable).

use crate::analysis::BitSet;
use crate::mir::{BlockId, BlockRef, IndexRef, Inst, Mir, Place, TempId, Terminator, Value};

/// The liveness-relevant effect of one scheduled instruction.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct InstEffect {
    /// Non-constant values used at this point (lazy-tree uses included).
    pub value_uses: Vec<Value>,
    /// Temps read at this point (eager loads plus loads anywhere inside a
    /// lazy tree; conditional uses count — may-use ⊆ live).
    pub temp_uses: Vec<TempId>,
    /// Eager store to a temp: `(temp, kills)` with `kills` iff size == 1.
    pub temp_def: Option<(TempId, bool)>,
}

fn place_value_uses(mir: &Mir, place: &Place, eff: &mut InstEffect) {
    if let BlockRef::Value(v) = place.block
        && !mir.is_const(v)
    {
        eff.value_uses.push(v);
    }
    if let IndexRef::Value(v) = place.index
        && !mir.is_const(v)
    {
        eff.value_uses.push(v);
    }
}

/// Computes the [`InstEffect`] of the scheduled instruction `v`.
/// `scheduled` is [`Mir::scheduled_mask`] (lazy-tree walks stop at scheduled
/// values and surface them as plain value uses).
pub fn inst_effect(mir: &Mir, scheduled: &[bool], v: Value) -> InstEffect {
    let mut eff = InstEffect::default();
    match mir.inst(v) {
        Inst::ConstInt(_) | Inst::ConstFloat(_) | Inst::Phi { .. } => {}
        Inst::Op { args, .. } => {
            for &a in args {
                if !mir.is_const(a) {
                    eff.value_uses.push(a);
                }
            }
        }
        inst @ (Inst::ShortCircuit { .. } | Inst::Select { .. }) => {
            let eager = match inst {
                Inst::ShortCircuit { lhs, .. } => *lhs,
                Inst::Select { test, .. } => *test,
                _ => unreachable!(),
            };
            if !mir.is_const(eager) {
                eff.value_uses.push(eager);
            }
            // Iterative walk of the owned lazy tree(s) (single-owner; module
            // docs). `Select` owns two roots, both conditional uses.
            let mut stack: Vec<Value> = Vec::new();
            Mir::for_each_lazy_root(inst, |root| stack.push(root));
            while let Some(lv) = stack.pop() {
                if mir.is_const(lv) {
                    continue;
                }
                if scheduled[lv as usize] {
                    eff.value_uses.push(lv);
                    continue;
                }
                match mir.inst(lv) {
                    Inst::ConstInt(_) | Inst::ConstFloat(_) | Inst::Phi { .. } => {}
                    inst @ (Inst::Op { .. } | Inst::ShortCircuit { .. } | Inst::Select { .. }) => {
                        Mir::for_each_operand(inst, |o| stack.push(o));
                    }
                    Inst::Load { place } | Inst::Store { place, .. } => {
                        // A store inside a lazy tree (a W4 if-conversion arm
                        // statement) is a conditional write: treat its temp
                        // as a conditional use, like `alloc.rs` (no kill).
                        if let BlockRef::Temp(t) = place.block {
                            eff.temp_uses.push(t);
                        }
                        if let BlockRef::Value(b) = place.block {
                            stack.push(b);
                        }
                        if let IndexRef::Value(i) = place.index {
                            stack.push(i);
                        }
                        if let Inst::Store { value, .. } = mir.inst(lv) {
                            stack.push(*value);
                        }
                    }
                }
            }
        }
        Inst::Load { place } => {
            if let BlockRef::Temp(t) = place.block {
                eff.temp_uses.push(t);
            }
            place_value_uses(mir, place, &mut eff);
        }
        Inst::Store { place, value } => {
            if let BlockRef::Temp(t) = place.block {
                eff.temp_def = Some((t, mir.temps[t].size == 1));
            }
            place_value_uses(mir, place, &mut eff);
            if !mir.is_const(*value) {
                eff.value_uses.push(*value);
            }
        }
    }
    eff
}

/// Per-block liveness over values and temps. See the module docs.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Liveness {
    /// Values live at block entry (phi destinations excluded — phis define at
    /// the head).
    value_in: Vec<BitSet>,
    /// Values live at block exit, **including** phi-argument uses on the
    /// block's outgoing edges, **excluding** the block's own terminator-test
    /// use (the test evaluates inside the block, not after it).
    value_out: Vec<BitSet>,
    /// Temps live at block entry.
    temp_in: Vec<BitSet>,
    /// Temps live at block exit.
    temp_out: Vec<BitSet>,
    /// [`Mir::scheduled_mask`] snapshot, for cursors.
    scheduled: Vec<bool>,
}

impl Liveness {
    /// Computes liveness for `mir`.
    pub fn compute(mir: &Mir) -> Self {
        let n_blocks = mir.blocks.len();
        let n_values = mir.insts.len();
        let n_temps = mir.temps.len();
        let scheduled = mir.scheduled_mask();

        // Phi-argument uses, attributed to the predecessor block's end.
        let mut phi_edge_uses: Vec<BitSet> = (0..n_blocks).map(|_| BitSet::new(n_values)).collect();
        for block in &mir.blocks {
            for &phi in &block.phis {
                let Inst::Phi { args } = mir.inst(phi) else {
                    continue;
                };
                for &(p, a) in args {
                    // Args keyed to out-of-range blocks (out of contract) are
                    // skipped rather than panicking.
                    if p < n_blocks && !mir.is_const(a) {
                        phi_edge_uses[p].insert(a as usize);
                    }
                }
            }
        }

        // Per-block gen/kill, accumulated in reverse execution order:
        // terminator test, then instructions last-to-first, then phi defs.
        let mut value_gen: Vec<BitSet> = Vec::with_capacity(n_blocks);
        let mut value_kill: Vec<BitSet> = Vec::with_capacity(n_blocks);
        let mut temp_gen: Vec<BitSet> = Vec::with_capacity(n_blocks);
        let mut temp_kill: Vec<BitSet> = Vec::with_capacity(n_blocks);
        for block in &mir.blocks {
            let mut vg = BitSet::new(n_values);
            let mut vk = BitSet::new(n_values);
            let mut tg = BitSet::new(n_temps);
            let mut tk = BitSet::new(n_temps);
            if let Terminator::Branch { test, .. } = &block.terminator
                && !mir.is_const(*test)
            {
                vg.insert(*test as usize);
            }
            for &v in block.insts.iter().rev() {
                let eff = inst_effect(mir, &scheduled, v);
                // The instruction defines its own value.
                vg.remove(v as usize);
                vk.insert(v as usize);
                for &u in &eff.value_uses {
                    vg.insert(u as usize);
                    vk.remove(u as usize);
                }
                if let Some((t, kills)) = eff.temp_def
                    && kills
                {
                    tg.remove(t);
                    tk.insert(t);
                }
                for &t in &eff.temp_uses {
                    tg.insert(t);
                    tk.remove(t);
                }
            }
            for &phi in &block.phis {
                vg.remove(phi as usize);
                vk.insert(phi as usize);
            }
            value_gen.push(vg);
            value_kill.push(vk);
            temp_gen.push(tg);
            temp_kill.push(tk);
        }

        // Backward worklist fixpoint over both domains.
        let preds = mir.predecessors();
        let mut value_in: Vec<BitSet> = (0..n_blocks).map(|_| BitSet::new(n_values)).collect();
        let mut value_out: Vec<BitSet> = (0..n_blocks).map(|_| BitSet::new(n_values)).collect();
        let mut temp_in: Vec<BitSet> = (0..n_blocks).map(|_| BitSet::new(n_temps)).collect();
        let mut temp_out: Vec<BitSet> = (0..n_blocks).map(|_| BitSet::new(n_temps)).collect();
        let mut worklist: Vec<BlockId> = (0..n_blocks).rev().collect();
        let mut queued = vec![true; n_blocks];
        while let Some(b) = worklist.pop() {
            queued[b] = false;
            let mut vo = phi_edge_uses[b].clone();
            let mut to = BitSet::new(n_temps);
            for succ in mir.blocks[b].terminator.successors() {
                vo.union_with(&value_in[succ]);
                to.union_with(&temp_in[succ]);
            }
            let mut vi = vo.clone();
            vi.subtract(&value_kill[b]);
            vi.union_with(&value_gen[b]);
            let mut ti = to.clone();
            ti.subtract(&temp_kill[b]);
            ti.union_with(&temp_gen[b]);
            value_out[b] = vo;
            temp_out[b] = to;
            if vi != value_in[b] || ti != temp_in[b] {
                value_in[b] = vi;
                temp_in[b] = ti;
                for &p in &preds[b] {
                    if !queued[p] {
                        queued[p] = true;
                        worklist.push(p);
                    }
                }
            }
        }

        Self {
            value_in,
            value_out,
            temp_in,
            temp_out,
            scheduled,
        }
    }

    pub fn value_in(&self, b: BlockId) -> &BitSet {
        &self.value_in[b]
    }

    pub fn value_out(&self, b: BlockId) -> &BitSet {
        &self.value_out[b]
    }

    pub fn temp_in(&self, b: BlockId) -> &BitSet {
        &self.temp_in[b]
    }

    pub fn temp_out(&self, b: BlockId) -> &BitSet {
        &self.temp_out[b]
    }

    /// A backward cursor positioned at the end of `block` (just before the
    /// terminator-test evaluation: the live sets include the test use).
    pub fn cursor_at_end<'a>(&'a self, mir: &'a Mir, block: BlockId) -> LivenessCursor<'a> {
        let mut values = self.value_out[block].clone();
        if let Terminator::Branch { test, .. } = &mir.blocks[block].terminator
            && !mir.is_const(*test)
        {
            values.insert(*test as usize);
        }
        LivenessCursor {
            mir,
            scheduled: &self.scheduled,
            block,
            pos: mir.blocks[block].insts.len(),
            values,
            temps: self.temp_out[block].clone(),
        }
    }
}

/// A backward walk over the program points of one block, maintaining the live
/// sets. Created by [`Liveness::cursor_at_end`]; each [`step_back`] call
/// un-applies one scheduled instruction. At position 0 the live sets are the
/// block's live-in **plus** live phi destinations (phis define above position
/// 0; `Liveness::value_in` excludes them).
///
/// [`step_back`]: LivenessCursor::step_back
#[derive(Debug, Clone)]
pub struct LivenessCursor<'a> {
    mir: &'a Mir,
    scheduled: &'a [bool],
    block: BlockId,
    /// Current point: just before the instruction at `pos` (== `insts.len()`
    /// means after the last instruction).
    pos: usize,
    values: BitSet,
    temps: BitSet,
}

impl LivenessCursor<'_> {
    pub fn pos(&self) -> usize {
        self.pos
    }

    pub fn block(&self) -> BlockId {
        self.block
    }

    /// Steps backward over one instruction, returning it; `None` at the block
    /// head. After the call the live sets describe the point just *before*
    /// the returned instruction.
    pub fn step_back(&mut self) -> Option<Value> {
        if self.pos == 0 {
            return None;
        }
        self.pos -= 1;
        let v = self.mir.blocks[self.block].insts[self.pos];
        let eff = inst_effect(self.mir, self.scheduled, v);
        self.values.remove(v as usize);
        if let Some((t, kills)) = eff.temp_def
            && kills
        {
            self.temps.remove(t);
        }
        for &u in &eff.value_uses {
            self.values.insert(u as usize);
        }
        for &t in &eff.temp_uses {
            self.temps.insert(t);
        }
        Some(v)
    }

    pub fn value_live(&self, v: Value) -> bool {
        self.values.contains(v as usize)
    }

    pub fn temp_live(&self, t: TempId) -> bool {
        self.temps.contains(t)
    }

    /// The live value set at the current point.
    pub fn live_values(&self) -> &BitSet {
        &self.values
    }

    /// The live temp set at the current point.
    pub fn live_temps(&self) -> &BitSet {
        &self.temps
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::mir::CaseCond;
    use crate::ops::Op;

    fn temp_place(t: TempId) -> Place {
        Place {
            block: BlockRef::Temp(t),
            index: IndexRef::Const(0),
            offset: 0,
        }
    }

    fn concrete_place(block: i64) -> Place {
        Place {
            block: BlockRef::Concrete(block),
            index: IndexRef::Const(0),
            offset: 0,
        }
    }

    fn sched(mir: &mut Mir, block: usize, inst: Inst) -> Value {
        let v = mir.push_inst(inst);
        mir.blocks[block].insts.push(v);
        v
    }

    fn load_temp(mir: &mut Mir, block: usize, t: TempId) -> Value {
        sched(
            mir,
            block,
            Inst::Load {
                place: temp_place(t),
            },
        )
    }

    fn store_temp(mir: &mut Mir, block: usize, t: TempId, value: Value) -> Value {
        sched(
            mir,
            block,
            Inst::Store {
                place: temp_place(t),
                value,
            },
        )
    }

    fn store_out(mir: &mut Mir, block: usize, value: Value) {
        sched(
            mir,
            block,
            Inst::Store {
                place: concrete_place(20),
                value,
            },
        );
    }

    #[test]
    fn straight_line_value_and_temp_ranges() {
        // b0: t = 1; v = load t; out = v.
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 1);
        let b0 = mir.push_block();
        let one = mir.push_inst(Inst::ConstInt(1));
        store_temp(&mut mir, b0, t, one);
        let v = load_temp(&mut mir, b0, t);
        store_out(&mut mir, b0, v);
        let live = Liveness::compute(&mir);
        assert!(live.value_in(b0).is_empty());
        assert!(live.value_out(b0).is_empty());
        assert!(
            !live.temp_in(b0).contains(t),
            "t is killed before its first use"
        );
        assert!(live.temp_out(b0).is_empty());
        // Cursor: walking back over the final store, v is live before it;
        // before the load, v is dead and t is live; before the store to t,
        // t is dead.
        let mut cur = live.cursor_at_end(&mir, b0);
        assert!(!cur.value_live(v));
        cur.step_back(); // out store
        assert!(cur.value_live(v));
        assert!(!cur.temp_live(t));
        cur.step_back(); // load
        assert!(!cur.value_live(v));
        assert!(cur.temp_live(t));
        cur.step_back(); // store to t
        assert!(!cur.temp_live(t));
        assert_eq!(cur.pos(), 0);
        assert!(cur.step_back().is_none());
    }

    #[test]
    fn read_before_write_temp_is_live_in() {
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 1);
        let b0 = mir.push_block();
        let v = load_temp(&mut mir, b0, t);
        store_out(&mut mir, b0, v);
        let live = Liveness::compute(&mir);
        assert!(live.temp_in(b0).contains(t));
    }

    #[test]
    fn array_store_does_not_kill() {
        // arr (size 3) is stored then loaded: a store to an array does not
        // kill, so the array is NOT killed above its store; but it is also
        // not a use, so live-in only contains it via the later load... the
        // load is after the store, and the store does not kill, so the load's
        // use propagates to live-in.
        let mut mir = Mir::new();
        let arr = mir.push_temp("arr", 3);
        let b0 = mir.push_block();
        let one = mir.push_inst(Inst::ConstInt(1));
        store_temp(&mut mir, b0, arr, one);
        let v = load_temp(&mut mir, b0, arr);
        store_out(&mut mir, b0, v);
        let live = Liveness::compute(&mir);
        assert!(
            live.temp_in(b0).contains(arr),
            "array stores do not kill (conservative, same as alloc.rs)"
        );
    }

    #[test]
    fn temp_live_around_loop_back_edge() {
        // b0: t = 1 -> b1: out = load t; branch {0: b1, default: b2}.
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 1);
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let b2 = mir.push_block();
        let one = mir.push_inst(Inst::ConstInt(1));
        store_temp(&mut mir, b0, t, one);
        mir.blocks[b0].terminator = Terminator::Jump(b1);
        let v = load_temp(&mut mir, b1, t);
        store_out(&mut mir, b1, v);
        let test = mir.push_inst(Inst::ConstInt(0));
        mir.blocks[b1].terminator = Terminator::Branch {
            test,
            cases: vec![(CaseCond::Int(0), b1)],
            default: Some(b2),
        };
        let live = Liveness::compute(&mir);
        assert!(live.temp_in(b1).contains(t));
        assert!(
            live.temp_out(b1).contains(t),
            "live around the back edge into b1"
        );
        assert!(live.temp_out(b0).contains(t));
        assert!(!live.temp_in(b0).contains(t));
        assert!(live.temp_in(b2).is_empty());
        assert!(live.temp_out(b2).is_empty());
    }

    #[test]
    fn phi_args_live_out_of_preds_and_dest_killed_at_head() {
        // Diamond: b0 -> {b1, b2} -> b3 with phi(b1: x1, b2: x2); out = phi.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let b2 = mir.push_block();
        let b3 = mir.push_block();
        let test = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(-3),
            },
        );
        mir.blocks[b0].terminator = Terminator::Branch {
            test,
            cases: vec![(CaseCond::Int(0), b1)],
            default: Some(b2),
        };
        let x1 = sched(
            &mut mir,
            b1,
            Inst::Load {
                place: concrete_place(21),
            },
        );
        mir.blocks[b1].terminator = Terminator::Jump(b3);
        let x2 = sched(
            &mut mir,
            b2,
            Inst::Load {
                place: concrete_place(22),
            },
        );
        mir.blocks[b2].terminator = Terminator::Jump(b3);
        let phi = mir.push_inst(Inst::Phi {
            args: vec![(b1, x1), (b2, x2)],
        });
        mir.blocks[b3].phis.push(phi);
        store_out(&mut mir, b3, phi);
        let live = Liveness::compute(&mir);
        assert!(live.value_out(b1).contains(x1 as usize));
        assert!(!live.value_out(b1).contains(x2 as usize));
        assert!(live.value_out(b2).contains(x2 as usize));
        assert!(
            !live.value_in(b3).contains(phi as usize),
            "phi defines at the head of its block"
        );
        assert!(
            !live.value_in(b1).contains(x1 as usize),
            "x1 is defined inside b1"
        );
        // The phi-arg uses do not leak into the phi block's live-in.
        assert!(!live.value_in(b3).contains(x1 as usize));
        assert!(!live.value_in(b3).contains(x2 as usize));
        // Cursor at position 0 of b3: the phi (defined above pos 0) is live.
        let mut cur = live.cursor_at_end(&mir, b3);
        while cur.step_back().is_some() {}
        assert!(cur.value_live(phi));
    }

    #[test]
    fn self_loop_phi_arg_is_live_out_of_its_own_block() {
        // b0 -> b1; b1: phi(b0: c, b1: phi) -> {b1, b2}.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let b2 = mir.push_block();
        mir.blocks[b0].terminator = Terminator::Jump(b1);
        let c = mir.push_inst(Inst::ConstInt(7));
        let phi = mir.push_inst(Inst::Phi { args: Vec::new() });
        let Inst::Phi { args } = &mut mir.insts[phi as usize] else {
            unreachable!()
        };
        *args = vec![(b0, c), (b1, phi)];
        mir.blocks[b1].phis.push(phi);
        store_out(&mut mir, b1, phi);
        let test = mir.push_inst(Inst::ConstInt(0));
        mir.blocks[b1].terminator = Terminator::Branch {
            test,
            cases: vec![(CaseCond::Int(0), b1)],
            default: Some(b2),
        };
        let live = Liveness::compute(&mir);
        assert!(
            live.value_out(b1).contains(phi as usize),
            "the self-arg keeps the phi live out of its own block"
        );
        assert!(
            !live.value_in(b1).contains(phi as usize),
            "the head def still kills it on entry"
        );
    }

    #[test]
    fn lazy_short_circuit_uses_count_at_owner_point() {
        // c = load tc; sc = And(c, lazy load tx); out = sc.
        // tx's only read is inside the lazy tree: it must be live at the
        // ShortCircuit's point (and thus interfere with anything stored
        // between).
        let mut mir = Mir::new();
        let tc = mir.push_temp("c", 1);
        let tx = mir.push_temp("x", 1);
        let b0 = mir.push_block();
        let one = mir.push_inst(Inst::ConstInt(1));
        store_temp(&mut mir, b0, tc, one);
        store_temp(&mut mir, b0, tx, one);
        let c = load_temp(&mut mir, b0, tc);
        let lazy_load = mir.push_inst(Inst::Load {
            place: temp_place(tx),
        }); // unscheduled
        let sc = sched(
            &mut mir,
            b0,
            Inst::ShortCircuit {
                op: Op::And,
                pure_node: true,
                lhs: c,
                rhs: lazy_load,
            },
        );
        store_out(&mut mir, b0, sc);
        let live = Liveness::compute(&mir);
        let mut cur = live.cursor_at_end(&mir, b0);
        cur.step_back(); // out store
        assert!(cur.value_live(sc));
        cur.step_back(); // the ShortCircuit
        assert!(cur.value_live(c), "eager lhs is a use");
        assert!(cur.temp_live(tx), "lazy load counts at the owner's point");
        assert!(!cur.temp_live(tc), "tc's last use was the load before");
        // The lazy load's value is NOT live as a value (it is not scheduled;
        // it belongs to the owning instruction).
        assert!(!cur.value_live(lazy_load));
        cur.step_back(); // the load of tc
        assert!(cur.temp_live(tc));
    }

    #[test]
    fn terminator_test_is_used_at_block_end() {
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let b2 = mir.push_block();
        let test = sched(
            &mut mir,
            b0,
            Inst::Load {
                place: concrete_place(-3),
            },
        );
        mir.blocks[b0].terminator = Terminator::Branch {
            test,
            cases: vec![(CaseCond::Int(0), b1)],
            default: Some(b2),
        };
        let live = Liveness::compute(&mir);
        // The test is defined and used inside b0: not live-in, not live-out.
        assert!(!live.value_in(b0).contains(test as usize));
        assert!(!live.value_out(b0).contains(test as usize));
        // But the cursor at block end sees it live (before the dispatcher).
        let cur = live.cursor_at_end(&mir, b0);
        assert!(cur.value_live(test));
    }

    #[test]
    fn exit_blocks_have_empty_live_out() {
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 1);
        let b0 = mir.push_block();
        let v = load_temp(&mut mir, b0, t);
        store_out(&mut mir, b0, v);
        let live = Liveness::compute(&mir);
        assert!(live.value_out(b0).is_empty());
        assert!(live.temp_out(b0).is_empty());
    }

    #[test]
    fn unreachable_blocks_participate_without_panic() {
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 1);
        let b0 = mir.push_block();
        let b1 = mir.push_block(); // unreachable, reads t, jumps into b0
        let v0 = load_temp(&mut mir, b0, t);
        store_out(&mut mir, b0, v0);
        let v1 = load_temp(&mut mir, b1, t);
        store_out(&mut mir, b1, v1);
        mir.blocks[b1].terminator = Terminator::Jump(b0);
        let live = Liveness::compute(&mir);
        assert!(live.temp_in(b0).contains(t));
        assert!(live.temp_in(b1).contains(t));
        assert!(live.temp_out(b1).contains(t), "b1's succ b0 reads t");
    }

    #[test]
    fn empty_mir_and_empty_blocks() {
        let live = Liveness::compute(&Mir::new());
        assert_eq!(live, Liveness::compute(&Mir::new()));
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let live = Liveness::compute(&mir);
        assert!(live.value_in(b0).is_empty());
        let mut cur = live.cursor_at_end(&mir, b0);
        assert!(cur.step_back().is_none());
    }

    #[test]
    fn deterministic() {
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 1);
        let b0 = mir.push_block();
        let b1 = mir.push_block();
        let one = mir.push_inst(Inst::ConstInt(1));
        store_temp(&mut mir, b0, t, one);
        mir.blocks[b0].terminator = Terminator::Jump(b1);
        let v = load_temp(&mut mir, b1, t);
        store_out(&mut mir, b1, v);
        assert_eq!(Liveness::compute(&mir), Liveness::compute(&mir));
    }
}
