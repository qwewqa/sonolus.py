//! Rewrite-rule framework (PORT.md T2.2): the infrastructure T3.2's GVN +
//! algebraic rules drive.
//!
//! A [`RewriteRule`] inspects one MIR instruction and its operands — including
//! constant detection through the arena — and optionally produces a
//! [`Rewrite`]: reuse an existing value, materialize a new instruction, or fold
//! to a constant. The [`RewriteDriver`] applies a *list* of rules to fixpoint
//! over a function's scheduled values with a hard iteration cap, deterministically.
//!
//! # Value replacement model
//!
//! The MIR arena is SSA-shaped: every value has a single definition. A rewrite
//! of value `v` to replacement `r` means *all current operand references to `v`
//! become references to `r`* — every instruction operand, phi argument, place
//! block/index, terminator test, and `ShortCircuit` lhs/rhs that pointed at `v`
//! now points at `r`. The defining instruction of `v` is left in the arena
//! (it becomes dead; DCE is a separate pass) but nothing references it any more,
//! so the program semantics are exactly those of `r`. Rules therefore never
//! rewrite *in place*; they describe the value that should stand in for the one
//! they were asked about.
//!
//! New instructions a rule introduces ([`Rewrite::NewInst`]) are appended to
//! the arena and, when the original value was scheduled, spliced into the
//! schedule immediately before the original instruction's slot, preserving
//! evaluation order. A rule must only build new instructions whose operands are
//! values that are already available at the rewrite point (constants, or values
//! the original instruction already depended on).
//!
//! # Lazy boundaries (decision D11)
//!
//! `ShortCircuit` owns an unscheduled lazy rhs expression tree (mir.rs). The
//! driver makes the lazy boundary **explicit**: by default it visits only
//! *scheduled* values, so a rule is never even asked about an instruction inside
//! a lazy tree, and when a rule inspects a `ShortCircuit`'s operands the rhs is
//! reported as a [`Operand::Lazy`] it cannot fold through. A rule that genuinely
//! wants to transform inside lazy trees must set
//! [`RewriteRule::enters_lazy`] to `true`; then the driver also visits lazy-tree
//! values and a rule may opt to read their definitions. The default — every
//! toy/real rule below — leaves laziness intact: nothing is hoisted out of, or
//! assumed evaluated in, the lazy side.
//!
//! # Determinism and termination
//!
//! Values are visited in ascending arena order; rules are tried in list order;
//! the first rule that fires wins for a given value, and the value is re-queued
//! so a follow-on rule can fire on the replacement. The driver runs rounds until
//! a full round makes no change (fixpoint) or the iteration cap is hit. The cap
//! guards against a rule pair that ping-pongs forever (`A -> B`, `B -> A`); when
//! it trips, debug builds panic (a rule set that does not converge is a bug) and
//! release builds stop and log via the returned [`RewriteReport`].

use crate::effects::{Effects, inst_effects};
use crate::mir::{Inst, Mir, Value};

/// What a rule replaces a value with.
#[derive(Debug, Clone, PartialEq)]
pub enum Rewrite {
    /// Replace every use of the value with this existing value (e.g.
    /// `Multiply(x, 1) -> x`). Must already be defined/available at the point.
    Existing(Value),
    /// Fold to an int-tagged constant.
    ConstInt(i64),
    /// Fold to a float-tagged constant.
    ConstFloat(f64),
    /// Replace with a freshly built instruction (the driver appends it to the
    /// arena and, if the original was scheduled, splices it into the schedule).
    /// Operands must be values already available at the rewrite point.
    NewInst(Inst),
}

/// An operand of the instruction under inspection, as a rule sees it. The key
/// distinction is [`Operand::Lazy`]: an operand the rule must treat as opaque
/// (a `ShortCircuit` rhs lazy-tree root) unless it opted into entering lazy
/// trees.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Operand {
    /// An int constant operand (folded through the arena for the rule).
    ConstInt(i64),
    /// A float constant operand.
    ConstFloat(f64),
    /// A non-constant operand value the rule may inspect further via the
    /// [`RewriteCtx`].
    Value(Value),
    /// An opaque operand inside a lazy `ShortCircuit` rhs tree: never folded or
    /// looked through unless the rule set [`RewriteRule::enters_lazy`].
    Lazy(Value),
}

/// Read-only inspection context handed to a rule: the MIR plus constant
/// detection helpers. Does not allow mutation — a rule's only output is the
/// returned [`Rewrite`].
#[derive(Debug)]
pub struct RewriteCtx<'a> {
    mir: &'a Mir,
    /// Which values are scheduled (computed once per round): a `ShortCircuit`
    /// rhs that is *not* scheduled is the lazy boundary.
    scheduled: &'a [bool],
}

impl RewriteCtx<'_> {
    /// The underlying MIR (read-only).
    pub fn mir(&self) -> &Mir {
        self.mir
    }

    /// The instruction defining `v`.
    pub fn inst(&self, v: Value) -> &Inst {
        self.mir.inst(v)
    }

    /// The constant value of `v` if it is a constant instruction.
    pub fn as_const(&self, v: Value) -> Option<Const> {
        match self.mir.inst(v) {
            Inst::ConstInt(i) => Some(Const::Int(*i)),
            Inst::ConstFloat(f) => Some(Const::Float(*f)),
            _ => None,
        }
    }

    /// The effect classification of `v`'s instruction (shallow; see
    /// [`crate::effects`]). Rules use this to refuse folds that would drop a
    /// side effect or an RNG draw.
    pub fn effects(&self, v: Value) -> Effects {
        inst_effects(self.mir, v)
    }

    /// Classifies the `i`th operand of `inst` as the rule should see it: a
    /// constant is surfaced as `ConstInt`/`ConstFloat`; a `ShortCircuit` rhs
    /// that is not scheduled is [`Operand::Lazy`]; everything else is
    /// [`Operand::Value`].
    fn classify_operand(&self, raw: Value, is_lazy: bool) -> Operand {
        if is_lazy {
            return Operand::Lazy(raw);
        }
        match self.mir.inst(raw) {
            Inst::ConstInt(i) => Operand::ConstInt(*i),
            Inst::ConstFloat(f) => Operand::ConstFloat(*f),
            _ => Operand::Value(raw),
        }
    }

    /// The classified operands of the instruction defining `v`, in operand
    /// order. For a `ShortCircuit`, the lhs is eager and the rhs is reported as
    /// [`Operand::Lazy`] when it heads an unscheduled lazy tree (the normal
    /// case).
    pub fn operands(&self, v: Value) -> Vec<Operand> {
        match self.mir.inst(v) {
            Inst::Op { args, .. } => args
                .iter()
                .map(|&a| self.classify_operand(a, false))
                .collect(),
            Inst::ShortCircuit { lhs, rhs, .. } => {
                let rhs_lazy = !self.scheduled[*rhs as usize];
                vec![
                    self.classify_operand(*lhs, false),
                    self.classify_operand(*rhs, rhs_lazy),
                ]
            }
            Inst::Select {
                test,
                then_root,
                else_root,
            } => {
                // Same model as `ShortCircuit`: the test is eager; each arm
                // root is opaque ([`Operand::Lazy`]) when it heads an
                // unscheduled lazy tree.
                let then_lazy = !self.scheduled[*then_root as usize];
                let else_lazy = !self.scheduled[*else_root as usize];
                vec![
                    self.classify_operand(*test, false),
                    self.classify_operand(*then_root, then_lazy),
                    self.classify_operand(*else_root, else_lazy),
                ]
            }
            Inst::Store { value, .. } => vec![self.classify_operand(*value, false)],
            Inst::Phi { args } => args
                .iter()
                .map(|&(_, a)| self.classify_operand(a, false))
                .collect(),
            Inst::ConstInt(_) | Inst::ConstFloat(_) | Inst::Load { .. } => Vec::new(),
        }
    }
}

/// A constant value seen by a rule.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Const {
    Int(i64),
    Float(f64),
}

impl Const {
    /// The numeric value as `f64` (for arithmetic folds).
    #[allow(clippy::cast_precision_loss)]
    pub fn as_f64(self) -> f64 {
        match self {
            Self::Int(v) => v as f64,
            Self::Float(v) => v,
        }
    }

    /// Whether this constant is int-tagged (the tag is load-bearing for output).
    pub fn is_int(self) -> bool {
        matches!(self, Self::Int(_))
    }
}

/// One rewrite rule over MIR values.
pub trait RewriteRule {
    /// A stable name (for the report / debugging).
    fn name(&self) -> &'static str;

    /// Whether this rule wants the driver to visit values inside lazy
    /// `ShortCircuit` rhs trees (and to look through `Operand::Lazy`). Defaults
    /// to `false`: laziness is respected (decision D11). Almost every rule
    /// should leave this `false`.
    fn enters_lazy(&self) -> bool {
        false
    }

    /// Inspect the value `v` (its instruction and operands via `ctx`) and
    /// optionally return a [`Rewrite`]. Returning `None` means "no opinion".
    ///
    /// A rule must not return a [`Rewrite`] that drops an observable effect:
    /// e.g. folding `Multiply(x, 1) -> x` is only valid because `Multiply` is
    /// pure. Use [`RewriteCtx::effects`] to check.
    fn rewrite(&self, ctx: &RewriteCtx<'_>, v: Value) -> Option<Rewrite>;
}

/// Outcome of a [`RewriteDriver::run`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RewriteReport {
    /// Total number of rewrites applied.
    pub rewrites: u32,
    /// Number of fixpoint rounds executed.
    pub rounds: u32,
    /// `true` iff the iteration cap was hit before reaching fixpoint (release
    /// builds stop and log; debug builds panic instead of returning this).
    pub capped: bool,
    /// The values whose uses were redirected, in rewrite order. Their defining
    /// instructions are dead but still scheduled — the pass driving the
    /// rewrites must sweep them out of the schedule before lowering
    /// (`LowerError::MultiUse` otherwise; see the T3.2 GVN pass's sweep).
    pub replaced: Vec<Value>,
}

/// A deterministic worklist driver applying a rule list to fixpoint with a hard
/// iteration cap. See the module docs.
pub struct RewriteDriver<'r> {
    rules: &'r [Box<dyn RewriteRule>],
    /// Hard cap on total rewrites (ping-pong guard). Exceeding it is a debug
    /// panic / release stop+log.
    cap: u32,
    /// Whether any rule wants lazy-tree visitation (cached from the rule list).
    any_enters_lazy: bool,
}

impl std::fmt::Debug for RewriteDriver<'_> {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("RewriteDriver")
            .field(
                "rules",
                &self.rules.iter().map(|r| r.name()).collect::<Vec<_>>(),
            )
            .field("cap", &self.cap)
            .field("any_enters_lazy", &self.any_enters_lazy)
            .finish()
    }
}

/// The default rewrite cap: generous for real rule sets, small enough that a
/// deliberate ping-pong trips it quickly in tests.
pub const DEFAULT_CAP: u32 = 10_000;

impl<'r> RewriteDriver<'r> {
    /// A driver over the given rules with [`DEFAULT_CAP`].
    pub fn new(rules: &'r [Box<dyn RewriteRule>]) -> Self {
        Self::with_cap(rules, DEFAULT_CAP)
    }

    /// A driver with an explicit cap (tests use a tiny cap to trip ping-pong
    /// quickly).
    pub fn with_cap(rules: &'r [Box<dyn RewriteRule>], cap: u32) -> Self {
        let any_enters_lazy = rules.iter().any(|r| r.enters_lazy());
        Self {
            rules,
            cap,
            any_enters_lazy,
        }
    }

    /// Applies the rules to fixpoint over `mir`. Returns a [`RewriteReport`].
    ///
    /// # Panics
    ///
    /// In debug builds, panics if the iteration cap is exceeded (a non-converging
    /// rule set is a bug). Release builds instead return a report with
    /// `capped == true` and stop.
    pub fn run(&self, mir: &mut Mir) -> RewriteReport {
        let mut rewrites = 0u32;
        let mut rounds = 0u32;
        // A value whose uses have been redirected to its replacement: its
        // defining instruction is now dead, must never be a rewrite target
        // again (its operands are unchanged, so a rule would re-fire on it
        // forever). Grows with the arena across rounds.
        let mut replaced = vec![false; mir.insts.len()];
        // The same set in rewrite order, surfaced in the report so the driving
        // pass can sweep the dead defining instructions.
        let mut replaced_order: Vec<Value> = Vec::new();
        loop {
            rounds += 1;
            let scheduled = mir.scheduled_mask();
            // Snapshot the set of values to visit this round in ascending arena
            // order (determinism). New values created by NewInst rewrites this
            // round are picked up next round.
            let visit: Vec<Value> = self.visit_order(mir, &scheduled, &replaced);

            let mut changed_this_round = false;
            for &v in &visit {
                if replaced[v as usize] {
                    continue; // replaced earlier this round (chained rewrite)
                }
                let Some(rewrite) = self.try_rewrite(mir, &scheduled, v) else {
                    continue;
                };
                apply_rewrite(mir, v, rewrite);
                replaced.resize(mir.insts.len(), false);
                replaced[v as usize] = true;
                replaced_order.push(v);
                rewrites += 1;
                changed_this_round = true;
                if rewrites >= self.cap {
                    return self.capped_report(rewrites, rounds, replaced_order);
                }
            }

            if !changed_this_round {
                return RewriteReport {
                    rewrites,
                    rounds,
                    capped: false,
                    replaced: replaced_order,
                };
            }
            if rewrites >= self.cap {
                return self.capped_report(rewrites, rounds, replaced_order);
            }
        }
    }

    /// The values to offer to rules this round, in ascending arena order: all
    /// scheduled (non-replaced, non-constant) values, plus — only if a rule
    /// entered lazy mode — lazy-tree values.
    fn visit_order(&self, mir: &Mir, scheduled: &[bool], replaced: &[bool]) -> Vec<Value> {
        // The arena length fits u32 (push_inst caps it; same contract as the
        // Value type).
        let len = Value::try_from(mir.insts.len()).expect("MIR arena fits u32");
        let live = |v: Value| {
            // `replaced` is shorter than the arena only for values created this
            // round (never replaced yet), so a missing entry is "not replaced".
            !mir.is_const(v) && !replaced.get(v as usize).copied().unwrap_or(false)
        };
        if self.any_enters_lazy {
            // Every non-constant, non-replaced arena value (scheduled + lazy).
            (0..len).filter(|&v| live(v)).collect()
        } else {
            (0..len)
                .filter(|&v| scheduled[v as usize] && live(v))
                .collect()
        }
    }

    fn try_rewrite(&self, mir: &Mir, scheduled: &[bool], v: Value) -> Option<Rewrite> {
        let ctx = RewriteCtx { mir, scheduled };
        for rule in self.rules {
            if let Some(rw) = rule.rewrite(&ctx, v) {
                return Some(rw);
            }
        }
        None
    }

    #[cfg_attr(
        debug_assertions,
        allow(unused_variables, clippy::needless_pass_by_value)
    )]
    fn capped_report(&self, rewrites: u32, rounds: u32, replaced: Vec<Value>) -> RewriteReport {
        #[cfg(debug_assertions)]
        panic!(
            "rewrite driver exceeded its iteration cap of {} ({rewrites} rewrites over \
             {rounds} rounds): the rule set does not converge (likely a ping-pong pair). \
             Rules: {:?}",
            self.cap,
            self.rules.iter().map(|r| r.name()).collect::<Vec<_>>()
        );
        #[cfg(not(debug_assertions))]
        RewriteReport {
            rewrites,
            rounds,
            capped: true,
            replaced,
        }
    }
}

/// Materializes a [`Rewrite`] for value `v`: builds the replacement value and
/// redirects every operand reference to `v` onto it. `pub(crate)` so the T3.2
/// GVN pass applies its merges through the exact same machinery as the driver.
pub(crate) fn apply_rewrite(mir: &mut Mir, v: Value, rewrite: Rewrite) {
    let replacement = match rewrite {
        Rewrite::Existing(r) => r,
        Rewrite::ConstInt(i) => mir.push_inst(Inst::ConstInt(i)),
        Rewrite::ConstFloat(f) => mir.push_inst(Inst::ConstFloat(f)),
        Rewrite::NewInst(inst) => {
            let new_v = mir.push_inst(inst);
            splice_into_schedule(mir, v, new_v);
            new_v
        }
    };
    replace_all_uses(mir, v, replacement);
}

/// If `original` is scheduled, inserts `new_v` into the same block's schedule
/// immediately before `original` (so the new instruction is evaluated at the
/// right point). No-op if `original` is unscheduled (a constant or lazy-tree
/// value — the replacement is then referenced lazily like its predecessor).
fn splice_into_schedule(mir: &mut Mir, original: Value, new_v: Value) {
    for block in &mut mir.blocks {
        if let Some(pos) = block.insts.iter().position(|&x| x == original) {
            block.insts.insert(pos, new_v);
            return;
        }
    }
}

/// Redirects every operand reference to `from` onto `to`, across all
/// instructions (operands, places, phi args, `ShortCircuit` lhs/rhs) and all
/// terminators. The defining instruction of `from` is untouched (it becomes
/// dead).
fn replace_all_uses(mir: &mut Mir, from: Value, to: Value) {
    for inst in &mut mir.insts {
        Mir::for_each_operand_mut(inst, |o| {
            if *o == from {
                *o = to;
            }
        });
    }
    for block in &mut mir.blocks {
        if let crate::mir::Terminator::Branch { test, .. } = &mut block.terminator
            && *test == from
        {
            *test = to;
        }
    }
}

// ----------------------------------------------------------------------------------
// Toy rules (tests only — NOT registered in any level pipeline; the real W1 rules
// are T3.1/T3.2's job and need differential coverage first, invariant §3.7).
// ----------------------------------------------------------------------------------

/// Toy: fold `Add(c1, c2)` of two int constants via the T1.1 `py_*`-adjacent
/// kernels (here plain integer addition, the int case of `Op::Add`). Proves the
/// constant-detection-through-the-arena machinery; it only fires on two
/// int-constant operands and only when `Add` is pure (it always is).
pub mod toy {
    use super::{Const, Operand, Rewrite, RewriteCtx, RewriteRule};
    use crate::mir::{Inst, Value};
    use crate::ops::Op;

    /// `Add(int c1, int c2) -> int (c1 + c2)`.
    #[derive(Debug)]
    pub struct FoldIntAdd;
    impl RewriteRule for FoldIntAdd {
        fn name(&self) -> &'static str {
            "toy-fold-int-add"
        }
        fn rewrite(&self, ctx: &RewriteCtx<'_>, v: Value) -> Option<Rewrite> {
            let Inst::Op { op: Op::Add, .. } = ctx.inst(v) else {
                return None;
            };
            // Pure check: never fold away an effect (Add is always pure, but
            // demonstrate the guard the real rules use).
            if !ctx.effects(v).is_pure() {
                return None;
            }
            match ctx.operands(v).as_slice() {
                [Operand::ConstInt(a), Operand::ConstInt(b)] => {
                    Some(Rewrite::ConstInt(a.wrapping_add(*b)))
                }
                _ => None,
            }
        }
    }

    /// `Multiply(x, 1) -> x` (identity element). Fires for an int-1 or float-1.0
    /// second operand; the first operand is returned unchanged. Pure-only.
    #[derive(Debug)]
    pub struct MulIdentity;
    impl RewriteRule for MulIdentity {
        fn name(&self) -> &'static str {
            "toy-mul-identity"
        }
        fn rewrite(&self, ctx: &RewriteCtx<'_>, v: Value) -> Option<Rewrite> {
            let Inst::Op {
                op: Op::Multiply,
                args,
                ..
            } = ctx.inst(v)
            else {
                return None;
            };
            if args.len() != 2 || !ctx.effects(v).is_pure() {
                return None;
            }
            let lhs = args[0];
            // x * 1 (or 1.0) -> x. The float identity must be *exactly* 1.0 —
            // an approximate value is not a multiplicative identity — so the
            // strict compare is correct here.
            #[allow(clippy::float_cmp)]
            let rhs_is_one = match ctx.operands(v).as_slice() {
                [_, Operand::ConstInt(1)] => true,
                [_, Operand::ConstFloat(f)] => *f == 1.0,
                _ => false,
            };
            rhs_is_one.then_some(Rewrite::Existing(lhs))
        }
    }

    /// Generic constant binary-op fold over `f64`, demonstrating tag-preserving
    /// folding: `Add`/`Subtract`/`Multiply` of two constants. Int+Int stays
    /// int; any float operand yields float (mirrors the interpreter's tagging,
    /// where an int-only computation keeps the int tag). Pure-only.
    #[derive(Debug)]
    pub struct FoldConstArith;
    impl RewriteRule for FoldConstArith {
        fn name(&self) -> &'static str {
            "toy-fold-const-arith"
        }
        fn rewrite(&self, ctx: &RewriteCtx<'_>, v: Value) -> Option<Rewrite> {
            let Inst::Op { op, args, .. } = ctx.inst(v) else {
                return None;
            };
            if args.len() != 2 || !ctx.effects(v).is_pure() {
                return None;
            }
            let folded = |a: Const, b: Const| -> Option<Rewrite> {
                let both_int = a.is_int() && b.is_int();
                let (x, y) = (a.as_f64(), b.as_f64());
                let r = match op {
                    Op::Add => x + y,
                    Op::Subtract => x - y,
                    Op::Multiply => x * y,
                    _ => return None,
                };
                Some(if both_int {
                    // Int-tag preservation: the result of int arithmetic is an
                    // int (in range). The toy rule keeps it simple and only
                    // re-tags when the f64 is exactly an in-range integer.
                    #[allow(clippy::cast_possible_truncation)]
                    if r.fract() == 0.0 && r.abs() < 9.007_199_254_740_992e15 {
                        Rewrite::ConstInt(r as i64)
                    } else {
                        Rewrite::ConstFloat(r)
                    }
                } else {
                    Rewrite::ConstFloat(r)
                })
            };
            let a = const_of(args[0], ctx)?;
            let b = const_of(args[1], ctx)?;
            folded(a, b)
        }
    }

    fn const_of(v: Value, ctx: &RewriteCtx<'_>) -> Option<Const> {
        ctx.as_const(v)
    }
}

#[cfg(test)]
mod tests {
    use super::toy::{FoldConstArith, FoldIntAdd, MulIdentity};
    use super::*;
    use crate::mir::{BlockRef, IndexRef, Inst, Place, Terminator};
    use crate::ops::Op;

    fn temp_place(t: usize) -> Place {
        Place {
            block: BlockRef::Temp(t),
            index: IndexRef::Const(0),
            offset: 0,
        }
    }

    /// A MIR with one block: `store t <- <root>`, where `root` is built by the
    /// caller. Returns (mir, the store value).
    fn store_mir(build: impl FnOnce(&mut Mir, usize) -> Value) -> (Mir, Value) {
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 1);
        let b0 = mir.push_block();
        let root = build(&mut mir, t);
        let store = mir.push_inst(Inst::Store {
            place: temp_place(t),
            value: root,
        });
        mir.blocks[b0].insts.push(store);
        mir.blocks[b0].terminator = Terminator::Exit;
        (mir, store)
    }

    fn rules(list: Vec<Box<dyn RewriteRule>>) -> Vec<Box<dyn RewriteRule>> {
        list
    }

    #[test]
    fn fold_int_add_reaches_fixpoint() {
        // store t <- Add(Add(1,2), Add(3,4)) -> all fold to the constant 10.
        let (mut mir, store) = store_mir(|mir, _t| {
            let c1 = mir.push_inst(Inst::ConstInt(1));
            let c2 = mir.push_inst(Inst::ConstInt(2));
            let c3 = mir.push_inst(Inst::ConstInt(3));
            let c4 = mir.push_inst(Inst::ConstInt(4));
            let a = mir.push_inst(Inst::Op {
                op: Op::Add,
                pure_node: true,
                args: vec![c1, c2],
            });
            let b = mir.push_inst(Inst::Op {
                op: Op::Add,
                pure_node: true,
                args: vec![c3, c4],
            });
            let top = mir.push_inst(Inst::Op {
                op: Op::Add,
                pure_node: true,
                args: vec![a, b],
            });
            mir.blocks[0].insts.push(a);
            mir.blocks[0].insts.push(b);
            mir.blocks[0].insts.push(top);
            top
        });
        let r = rules(vec![Box::new(FoldIntAdd)]);
        let report = RewriteDriver::new(&r).run(&mut mir);
        assert!(!report.capped);
        assert!(report.rewrites >= 3, "three Adds fold");
        // The store's value must now be a constant 10.
        let Inst::Store { value, .. } = mir.inst(store) else {
            panic!()
        };
        assert_eq!(mir.inst(*value), &Inst::ConstInt(10));
    }

    #[test]
    fn mul_identity_replaces_with_existing() {
        // store t <- Multiply(load t2, 1) -> store t <- load t2.
        let (mut mir, store) = store_mir(|mir, _t| {
            let t2 = mir.push_temp("t2", 1);
            let load = mir.push_inst(Inst::Load {
                place: temp_place(t2),
            });
            mir.blocks[0].insts.push(load);
            let one = mir.push_inst(Inst::ConstInt(1));
            let mul = mir.push_inst(Inst::Op {
                op: Op::Multiply,
                pure_node: true,
                args: vec![load, one],
            });
            mir.blocks[0].insts.push(mul);
            mul
        });
        let r = rules(vec![Box::new(MulIdentity)]);
        let report = RewriteDriver::new(&r).run(&mut mir);
        assert!(!report.capped);
        assert_eq!(report.rewrites, 1);
        let Inst::Store { value, .. } = mir.inst(store) else {
            panic!()
        };
        assert!(
            matches!(mir.inst(*value), Inst::Load { .. }),
            "Multiply(x,1) folded to x"
        );
    }

    #[test]
    fn const_arith_preserves_int_tag_and_promotes_to_float() {
        // Add(2,3) -> int 5; Add(2, 3.5) -> float 5.5.
        let (mut mir, store) = store_mir(|mir, _t| {
            let c2 = mir.push_inst(Inst::ConstInt(2));
            let c3 = mir.push_inst(Inst::ConstInt(3));
            let add = mir.push_inst(Inst::Op {
                op: Op::Add,
                pure_node: true,
                args: vec![c2, c3],
            });
            mir.blocks[0].insts.push(add);
            add
        });
        let r = rules(vec![Box::new(FoldConstArith)]);
        RewriteDriver::new(&r).run(&mut mir);
        let Inst::Store { value, .. } = mir.inst(store) else {
            panic!()
        };
        assert_eq!(mir.inst(*value), &Inst::ConstInt(5));

        let (mut mir, store) = store_mir(|mir, _t| {
            let c2 = mir.push_inst(Inst::ConstInt(2));
            let c3 = mir.push_inst(Inst::ConstFloat(3.5));
            let add = mir.push_inst(Inst::Op {
                op: Op::Add,
                pure_node: true,
                args: vec![c2, c3],
            });
            mir.blocks[0].insts.push(add);
            add
        });
        RewriteDriver::new(&r).run(&mut mir);
        let Inst::Store { value, .. } = mir.inst(store) else {
            panic!()
        };
        assert_eq!(mir.inst(*value), &Inst::ConstFloat(5.5));
    }

    #[test]
    fn determinism_same_input_same_report() {
        let build = |mir: &mut Mir, _t: usize| {
            let c1 = mir.push_inst(Inst::ConstInt(10));
            let c2 = mir.push_inst(Inst::ConstInt(20));
            let add = mir.push_inst(Inst::Op {
                op: Op::Add,
                pure_node: true,
                args: vec![c1, c2],
            });
            mir.blocks[0].insts.push(add);
            add
        };
        let (mut a, _) = store_mir(build);
        let (mut b, _) = store_mir(build);
        let r = rules(vec![Box::new(FoldIntAdd)]);
        let ra = RewriteDriver::new(&r).run(&mut a);
        let rb = RewriteDriver::new(&r).run(&mut b);
        assert_eq!(ra, rb);
    }

    /// A pair of rules that ping-pong: A rewrites `Negate(x)` -> `Abs(x)`,
    /// B rewrites `Abs(x)` -> `Negate(x)`; together they never converge.
    struct PingA;
    impl RewriteRule for PingA {
        fn name(&self) -> &'static str {
            "ping-a"
        }
        fn rewrite(&self, ctx: &RewriteCtx<'_>, v: Value) -> Option<Rewrite> {
            let Inst::Op {
                op: Op::Negate,
                args,
                pure_node,
            } = ctx.inst(v)
            else {
                return None;
            };
            Some(Rewrite::NewInst(Inst::Op {
                op: Op::Abs,
                pure_node: *pure_node,
                args: args.clone(),
            }))
        }
    }
    struct PongB;
    impl RewriteRule for PongB {
        fn name(&self) -> &'static str {
            "pong-b"
        }
        fn rewrite(&self, ctx: &RewriteCtx<'_>, v: Value) -> Option<Rewrite> {
            let Inst::Op {
                op: Op::Abs,
                args,
                pure_node,
            } = ctx.inst(v)
            else {
                return None;
            };
            Some(Rewrite::NewInst(Inst::Op {
                op: Op::Negate,
                pure_node: *pure_node,
                args: args.clone(),
            }))
        }
    }

    #[cfg(not(debug_assertions))]
    #[test]
    fn ping_pong_release_stops_at_cap() {
        let (mut mir, _) = store_mir(|mir, t| {
            let load = mir.push_inst(Inst::Load {
                place: temp_place(t),
            });
            mir.blocks[0].insts.push(load);
            let neg = mir.push_inst(Inst::Op {
                op: Op::Negate,
                pure_node: true,
                args: vec![load],
            });
            mir.blocks[0].insts.push(neg);
            neg
        });
        let r = rules(vec![Box::new(PingA), Box::new(PongB)]);
        let report = RewriteDriver::with_cap(&r, 50).run(&mut mir);
        assert!(report.capped, "ping-pong must trip the cap");
    }

    #[cfg(debug_assertions)]
    #[test]
    #[should_panic(expected = "does not converge")]
    fn ping_pong_debug_panics_at_cap() {
        let (mut mir, _) = store_mir(|mir, t| {
            let load = mir.push_inst(Inst::Load {
                place: temp_place(t),
            });
            mir.blocks[0].insts.push(load);
            let neg = mir.push_inst(Inst::Op {
                op: Op::Negate,
                pure_node: true,
                args: vec![load],
            });
            mir.blocks[0].insts.push(neg);
            neg
        });
        let r = rules(vec![Box::new(PingA), Box::new(PongB)]);
        let _ = RewriteDriver::with_cap(&r, 50).run(&mut mir);
    }

    /// A rule that fires only on an `Add` whose operands it sees as two int
    /// constants. Used to prove a lazy `Add(c1,c2)` inside a `ShortCircuit` rhs
    /// is NOT folded by a default (non-lazy) rule.
    #[test]
    fn default_rule_does_not_fire_inside_lazy_tree() {
        // store t <- And(load a, Add(1, 2))  — the Add is lazy (rhs of And).
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 1);
        let ta = mir.push_temp("a", 1);
        let b0 = mir.push_block();
        let load_a = mir.push_inst(Inst::Load {
            place: temp_place(ta),
        });
        mir.blocks[b0].insts.push(load_a);
        let c1 = mir.push_inst(Inst::ConstInt(1));
        let c2 = mir.push_inst(Inst::ConstInt(2));
        // Unscheduled lazy Add.
        let lazy_add = mir.push_inst(Inst::Op {
            op: Op::Add,
            pure_node: true,
            args: vec![c1, c2],
        });
        let sc = mir.push_inst(Inst::ShortCircuit {
            op: Op::And,
            pure_node: true,
            lhs: load_a,
            rhs: lazy_add,
        });
        mir.blocks[b0].insts.push(sc);
        let store = mir.push_inst(Inst::Store {
            place: temp_place(t),
            value: sc,
        });
        mir.blocks[b0].insts.push(store);
        mir.blocks[b0].terminator = Terminator::Exit;

        let r = rules(vec![Box::new(FoldIntAdd)]);
        let report = RewriteDriver::new(&r).run(&mut mir);
        assert_eq!(report.rewrites, 0, "the lazy Add must not be folded");
        // The lazy Add is untouched.
        assert_eq!(
            mir.inst(lazy_add),
            &Inst::Op {
                op: Op::Add,
                pure_node: true,
                args: vec![c1, c2]
            }
        );
    }

    /// A rule with `enters_lazy() == true` is allowed to fold inside the lazy
    /// tree; the same Add now folds.
    struct LazyFoldIntAdd;
    impl RewriteRule for LazyFoldIntAdd {
        fn name(&self) -> &'static str {
            "lazy-fold-int-add"
        }
        fn enters_lazy(&self) -> bool {
            true
        }
        fn rewrite(&self, ctx: &RewriteCtx<'_>, v: Value) -> Option<Rewrite> {
            let Inst::Op { op: Op::Add, .. } = ctx.inst(v) else {
                return None;
            };
            // In lazy mode operands of a *scheduled* Add are constants; here we
            // just read the raw instruction operands since this rule visits the
            // lazy Add directly (its own operands are constants).
            let Inst::Op { args, .. } = ctx.inst(v) else {
                return None;
            };
            let a = ctx.as_const(args[0])?;
            let b = ctx.as_const(args[1])?;
            if let (Const::Int(x), Const::Int(y)) = (a, b) {
                Some(Rewrite::ConstInt(x.wrapping_add(y)))
            } else {
                None
            }
        }
    }

    #[test]
    fn opt_in_rule_folds_inside_lazy_tree() {
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 1);
        let ta = mir.push_temp("a", 1);
        let b0 = mir.push_block();
        let load_a = mir.push_inst(Inst::Load {
            place: temp_place(ta),
        });
        mir.blocks[b0].insts.push(load_a);
        let c1 = mir.push_inst(Inst::ConstInt(1));
        let c2 = mir.push_inst(Inst::ConstInt(2));
        let lazy_add = mir.push_inst(Inst::Op {
            op: Op::Add,
            pure_node: true,
            args: vec![c1, c2],
        });
        let sc = mir.push_inst(Inst::ShortCircuit {
            op: Op::And,
            pure_node: true,
            lhs: load_a,
            rhs: lazy_add,
        });
        mir.blocks[b0].insts.push(sc);
        let store = mir.push_inst(Inst::Store {
            place: temp_place(t),
            value: sc,
        });
        mir.blocks[b0].insts.push(store);
        mir.blocks[b0].terminator = Terminator::Exit;

        let r = rules(vec![Box::new(LazyFoldIntAdd)]);
        let report = RewriteDriver::new(&r).run(&mut mir);
        assert_eq!(report.rewrites, 1, "opt-in rule folds the lazy Add");
        // The ShortCircuit's rhs now points at a constant 3.
        let Inst::ShortCircuit { rhs, .. } = mir.inst(sc) else {
            panic!()
        };
        assert_eq!(mir.inst(*rhs), &Inst::ConstInt(3));
    }
}
