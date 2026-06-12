//! Sparse conditional constant propagation (PORT.md T3.1, wave W1).
//!
//! Standard SCCP (Wegman–Zadeck) over MIR: a flat value lattice
//! (`Top` / `Const` / `Bottom`) solved with twin SSA/CFG-edge worklists and
//! executable-edge tracking, followed by a rewrite phase that materializes
//! constants, prunes constant branches, and removes unreachable blocks.
//!
//! # Lattice and folding policy
//!
//! - **Constants** hold one `f64` — the *runtime* value domain (every engine
//!   value is an `f64`). Identity is bit-level (NaN equals NaN by bits); the
//!   lattice never holds `-0.0` (see the `-0.0` rule below).
//! - **Folding** is strict and routed exclusively through the T1.1 `py_*`
//!   kernels (`crate::interpret`): an op folds only when every operand is a
//!   known constant *and* the kernel succeeds. **No fold where Python
//!   raises** (invariant §3.6): `Divide`/`Mod`/`Rem` by zero, `Power` domain
//!   errors (`0**negative`, negative base with fractional exponent) and
//!   overflow, `Log`/`Arcsin`/`Arccos` domain errors, `Round`/`Ceil`/`Floor`/
//!   `Trunc` of NaN/±inf, `Sin`/`Cos`/`Tan` of ±inf, `Sinh`/`Cosh` overflow —
//!   all stay `Bottom` and the instruction remains scheduled, so the runtime
//!   trap is preserved exactly. Successful ±inf *results* fold fine: inf
//!   constants are legal in MIR (emission reads them from ROM). NaN results
//!   follow the **canonical-NaN rule** below (W1-merge alignment with T3.2's
//!   GVN, which discovered the hazard by fuzz).
//!
//! # The canonical-NaN rule
//!
//! Every NaN *constant* is the ROM NaN at runtime (emission turns NaN consts
//! into `Get(3000, 0)`), whose bits are the canonical positive quiet NaN
//! (`f64::NAN`). But a freshly *generated* NaN (e.g. `inf * 0`) can carry a
//! different sign/payload at runtime (x86 gives `-NaN`), and `Sign`/bit
//! observables expose it. Therefore:
//!
//! - A `ConstFloat(NaN-with-any-payload)` seeds the lattice as the canonical
//!   `f64::NAN` (its true post-emission runtime value).
//! - A fold result that is NaN with **non-canonical bits** is refused
//!   (`Bottom`) — it is unmaterializable as a constant node. Canonical-NaN
//!   results (NaN propagated from a NaN constant through the same `py_*`
//!   kernels the interpreter runs) fold fine.
//! - The legacy pass's non-strict rules (`Multiply` with a zero operand → 0,
//!   `And`/`Or` over n-ary arg lists via Python truthiness) are deliberately
//!   **not** ported: `Multiply(NaN, 0)` is NaN at runtime, and MIR `And`/`Or`
//!   are `ShortCircuit` instructions with their own exact rule below. The
//!   legacy multi-value (frozenset) phi refinement is also dropped — the flat
//!   lattice keeps `Bottom` instead (a quality, not correctness, delta).
//! - **Effectful ops are `Bottom` by definition**: RNG (`Random` /
//!   `RandomInteger` — draw count/order is part of the optimizer contract),
//!   memory readers/writers, `DebugLog`, `Break`. `Load`s are `Bottom` (no
//!   memory tracking in W1). They are never folded and never reordered: this
//!   pass only ever *removes* pure instructions whose successful constant
//!   evaluation was proven, so the relative order of every remaining (
//!   effectful or trap-capable) evaluation is unchanged.
//!
//! # Int/float tags of materialized constants, and the `-0.0` rule
//!
//! A folded constant is int-tagged when its value is integral (mirroring the
//! emission-time collapse of integral floats to ints); integral values
//! outside the `i64` range stay float-tagged (not representable as
//! `ConstInt`). Negative zero needs care because emission collapses every
//! integral constant via `value + 0.0` (`emit.rs::push_numeric`, legacy
//! parity): **a constant `-0.0` is `+0.0` at runtime**. Therefore:
//!
//! - A `ConstFloat(-0.0)` instruction seeds the lattice as `Const(0.0)` (its
//!   true runtime value) — e.g. `Sign(-0.0_const)` correctly folds to `1`,
//!   matching minimal.
//! - A *computed* `-0.0` (e.g. `Negate(0)`, evaluated at runtime where it
//!   really is `-0.0`, observable through `Sign`/`Arctan2`) is
//!   unmaterializable as a constant node, so such fold results are refused
//!   (`Bottom`) and the computing instruction stays. Both rules were pinned
//!   by a fuzz counterexample (minimal logged `Sign(-0.0_const) = 1.0`).
//!
//! # `ShortCircuit` (decision D11)
//!
//! The lazy rhs tree is never evaluated speculatively. With
//! `lattice(lhs) = Const(c)` the runtime behavior is fully known:
//!
//! - **`c` short-circuits** (`And` with `c == 0`, `Or` with `c != 0`,
//!   NaN counting as truthy exactly like the interpreter): the result *is*
//!   `c` (`And`/`Or` return the last evaluated value) and the rhs tree never
//!   runs — the instruction folds to `Const(c)` and the lazy tree is dropped.
//! - **`c` passes through**: the result is exactly `eval(rhs)`. If the rhs
//!   root is also a known constant the whole instruction folds (every
//!   instruction on the rhs value spine was proven pure-and-successfully-
//!   folded, so dropping the tree is unobservable). Otherwise the rewrite
//!   phase **splices the rhs tree into the eager schedule at the
//!   `ShortCircuit`'s own program point** (documented choice): the tree's
//!   instructions are scheduled in exactly the evaluation order the lazy
//!   evaluator would have used (operands left to right, place block before
//!   index, nested `ShortCircuit`s keep their own lazy rhs), at the same
//!   point the `ShortCircuit` itself evaluated — so every effect/trap inside
//!   the tree happens at the identical position in the program's effect
//!   order. Uses of the `ShortCircuit` are redirected to the rhs root.
//!
//! Inside lazy trees, pure subtrees fold like everywhere else (operands of
//! lazy instructions are rewritten to constants); nothing effectful is ever
//! hoisted out of, or assumed evaluated in, a lazy side.
//!
//! # Phis
//!
//! Today's MIR has no phis before W2; they are handled anyway (W2 relies on
//! it): a phi's value is the meet of its arguments **over executable incoming
//! edges only**. Arguments from non-executable edges are pruned by the
//! rewrite phase, and phi arguments referencing removed blocks are remapped
//! with the block compaction.
//!
//! # Branch pruning
//!
//! A `Branch` whose test is a known constant is rewritten to a `Jump` to the
//! taken edge — matching by **runtime numeric equality on `f64`**
//! (`CaseCond::value()`, int/float-insensitive), which is exactly the
//! comparison the emitted dispatcher performs (`Equal` / `SwitchWithDefault`
//! pair scan in case order). With no matching case and no default edge the
//! terminator becomes `Exit`: the emitter uses the exit index as the implicit
//! default (`emit.rs`), so this is the runtime behavior minimal would have.
//! Never-executable blocks are then removed and block ids compacted.
//! (`build_mir`'s compile-time UCE uses exact Python `int == float` matching
//! for *frontend* constants; here the test value already lives in the `f64`
//! runtime domain, so `f64` equality is the faithful — and for the corpus'
//! small conds identical — choice.)
//!
//! # Lowering validity (`LowerError::MultiUse` note from T2.3)
//!
//! Every value whose uses were rewritten to constants has its (pure, proven
//! foldable) defining instruction swept from the schedule, with one
//! exception: a constant that feeds a memory-place component it cannot
//! legally become (non-integral / out-of-range index — converting it would
//! drop the runtime `ensure_int` trap) keeps its defining instruction
//! scheduled, computing the same constant at the same point. Integral
//! in-range constant components become `IndexRef::Const`/`BlockRef::Concrete`
//! instead. The pass therefore always leaves valid lowerable MIR: schedule
//! order equals evaluation order, every scheduled value is used at most once.
//!
//! # Pass contract
//!
//! Pure (no globals), deterministic (Vec worklists, fixed scan orders; hash
//! containers are used for membership only), iterative everywhere (explicit
//! work stacks, invariant §3.4). Reports `changed` precisely and invalidates
//! analyses per the T2.1 discipline (`invalidate_cfg` when terminators,
//! blocks, or phi placement changed; `invalidate_values` otherwise).

// Exact f64 comparisons are the ported Python/runtime semantics throughout
// this pass (same rationale as interpret.rs).
#![allow(clippy::float_cmp)]

use std::collections::{HashMap, HashSet};

use crate::analysis::Analyses;
use crate::effects::op_effects;
use crate::interpret::{
    clamp01, py_acos, py_asin, py_ceil, py_div, py_floor, py_log, py_max, py_min, py_mod,
    py_overflowing, py_pow, py_remainder, py_round, py_trig, py_trunc,
};
use crate::mir::{BlockId, BlockRef, CaseCond, IndexRef, Inst, Mir, Terminator, Value};
use crate::ops::Op;
use crate::passes::Pass;

/// The SCCP pass. See the module docs.
#[derive(Debug, Default, Clone, Copy)]
pub struct Sccp;

impl Pass for Sccp {
    fn name(&self) -> &'static str {
        "sccp"
    }

    fn run(&self, mir: &mut Mir, analyses: &mut Analyses) -> bool {
        if mir.blocks.is_empty() {
            return false;
        }
        let solution = Solver::solve(mir);
        let (values_changed, cfg_changed) = Rewriter::apply(mir, solution);
        if cfg_changed {
            analyses.invalidate_cfg();
            true
        } else if values_changed {
            analyses.invalidate_values();
            true
        } else {
            false
        }
    }
}

// ----------------------------------------------------------------------------------
// Lattice
// ----------------------------------------------------------------------------------

/// The flat SCCP value lattice. `Const` identity is bit-level (see module docs).
#[derive(Debug, Clone, Copy)]
enum Lattice {
    /// Optimistic "no information yet" (unvisited / undefined).
    Top,
    /// Proven runtime constant.
    Const(f64),
    /// Not a constant.
    Bottom,
}

impl Lattice {
    fn same(self, other: Self) -> bool {
        match (self, other) {
            (Self::Top, Self::Top) | (Self::Bottom, Self::Bottom) => true,
            (Self::Const(a), Self::Const(b)) => a.to_bits() == b.to_bits(),
            _ => false,
        }
    }
}

/// Meet for phi merging: `Top` is the identity, `Bottom` absorbs, constants
/// merge only when bit-identical.
fn meet(a: Lattice, b: Lattice) -> Lattice {
    match (a, b) {
        (Lattice::Top, x) | (x, Lattice::Top) => x,
        (Lattice::Bottom, _) | (_, Lattice::Bottom) => Lattice::Bottom,
        (Lattice::Const(x), Lattice::Const(y)) => {
            if x.to_bits() == y.to_bits() {
                Lattice::Const(x)
            } else {
                Lattice::Bottom
            }
        }
    }
}

/// Whether a `ShortCircuit` with constant lhs `c` stops (short-circuits) —
/// exactly the interpreter's rule: `And` stops on `c == 0.0`, `Or` stops on
/// `c != 0.0` (NaN is truthy).
fn sc_stops(op: Op, c: f64) -> bool {
    if op == Op::And { c == 0.0 } else { c != 0.0 }
}

/// `1.0` / `0.0` for comparison results, like the interpreter.
fn truth(b: bool) -> f64 {
    if b { 1.0 } else { 0.0 }
}

/// Runtime branch matching: the emitted dispatcher compares the test value
/// against the cond as `f64` (module docs).
fn case_matches(cond: CaseCond, value: f64) -> bool {
    cond.value() == value
}

/// The taken successor of a constant-test branch: first matching case in
/// (sorted) case order, else the default, else none (= runtime exit).
fn taken_target(
    cases: &[(CaseCond, BlockId)],
    default: Option<BlockId>,
    value: f64,
) -> Option<BlockId> {
    cases
        .iter()
        .find(|&&(cond, _)| case_matches(cond, value))
        .map(|&(_, target)| target)
        .or(default)
}

/// Folds one pure op over constant operand values via the interpreter's exact
/// kernels. `None` = no fold (Python raises, unsupported op, or bad arity).
#[allow(clippy::too_many_lines, clippy::many_single_char_names)]
fn fold_op(op: Op, v: &[f64]) -> Option<f64> {
    Some(match (op, v) {
        // Binarized reduce ops (build_mir emits exactly two operands).
        (Op::Add, [a, b]) => a + b,
        (Op::Subtract, [a, b]) => a - b,
        (Op::Multiply, [a, b]) => a * b,
        (Op::Divide, [a, b]) => py_div(*a, *b).ok()?,
        (Op::Mod, [a, b]) => py_mod(*a, *b).ok()?,
        (Op::Power, [a, b]) => py_pow(*a, *b).ok()?,
        (Op::Rem, [a, b]) => py_remainder(*a, *b).ok()?,
        // Comparisons and logic.
        (Op::Equal, [a, b]) => truth(a == b),
        (Op::NotEqual, [a, b]) => truth(a != b),
        (Op::Greater, [a, b]) => truth(a > b),
        (Op::GreaterOr, [a, b]) => truth(a >= b),
        (Op::Less, [a, b]) => truth(a < b),
        (Op::LessOr, [a, b]) => truth(a <= b),
        (Op::Not, [a]) => truth(*a == 0.0),
        // Unary math.
        (Op::Abs, [a]) => a.abs(),
        (Op::Negate, [a]) => -a,
        (Op::Sign, [a]) => 1.0f64.copysign(*a),
        (Op::Floor, [a]) => py_floor(*a).ok()?,
        (Op::Ceil, [a]) => py_ceil(*a).ok()?,
        (Op::Round, [a]) => py_round(*a).ok()?,
        (Op::Trunc, [a]) => py_trunc(*a).ok()?,
        (Op::Frac, [a]) => {
            // The interpreter's literal Frac: x % 1, adjusted into [0, 1).
            let m = py_mod(*a, 1.0).ok()?;
            if m >= 0.0 { m } else { m + 1.0 }
        }
        (Op::Log, [a]) => py_log(*a).ok()?,
        (Op::Sin, [a]) => py_trig(*a, f64::sin).ok()?,
        (Op::Cos, [a]) => py_trig(*a, f64::cos).ok()?,
        (Op::Tan, [a]) => py_trig(*a, f64::tan).ok()?,
        (Op::Sinh, [a]) => py_overflowing(*a, f64::sinh).ok()?,
        (Op::Cosh, [a]) => py_overflowing(*a, f64::cosh).ok()?,
        (Op::Tanh, [a]) => a.tanh(),
        (Op::Arcsin, [a]) => py_asin(*a).ok()?,
        (Op::Arccos, [a]) => py_acos(*a).ok()?,
        (Op::Arctan, [a]) => a.atan(),
        (Op::Arctan2, [a, b]) => a.atan2(*b),
        (Op::Degree, [a]) => a.to_degrees(),
        (Op::Radian, [a]) => a.to_radians(),
        // Binary/ternary/5-ary fused math (Python min/max ordering preserved).
        (Op::Min, [a, b]) => py_min(*a, *b),
        (Op::Max, [a, b]) => py_max(*a, *b),
        (Op::Clamp, [x, lo, hi]) => py_max(*lo, py_min(*hi, *x)),
        (Op::Lerp, [a, b, t]) => a + (b - a) * t,
        (Op::LerpClamped, [a, b, t]) => a + (b - a) * clamp01(*t),
        (Op::Unlerp, [a, b, x]) => py_div(x - a, b - a).ok()?,
        (Op::UnlerpClamped, [a, b, x]) => clamp01(py_div(x - a, b - a).ok()?),
        (Op::Remap, [fa, fb, ta, tb, x]) => ta + py_div((tb - ta) * (x - fa), fb - fa).ok()?,
        (Op::RemapClamped, [fa, fb, ta, tb, x]) => {
            ta + (tb - ta) * clamp01(py_div(x - fa, fb - fa).ok()?)
        }
        // Anything else (incl. pure ops without a kernel): no fold.
        _ => return None,
    })
}

/// The constant instruction for a folded value: int-tagged when integral;
/// values outside the `i64` range stay float-tagged (module docs). `-0.0`
/// never reaches here (the lattice never holds it — module docs); the
/// float-tagged fallback is defensive.
#[allow(clippy::cast_possible_truncation)]
fn const_inst_for(c: f64) -> Inst {
    const I64_MIN_F: f64 = -9_223_372_036_854_775_808.0; // -2^63 (exact)
    const I64_MAX_EXCL_F: f64 = 9_223_372_036_854_775_808.0; // 2^63 (exact)
    let integral = c.is_finite() && c.trunc() == c;
    let neg_zero = c == 0.0 && c.is_sign_negative();
    debug_assert!(!neg_zero, "the lattice never holds -0.0");
    if integral && !neg_zero && (I64_MIN_F..I64_MAX_EXCL_F).contains(&c) {
        Inst::ConstInt(c as i64)
    } else {
        Inst::ConstFloat(c)
    }
}

/// A constant place component conversion: `Some(i)` when the value can become
/// `IndexRef::Const`/`BlockRef::Concrete` with identical runtime behavior
/// (integral, comfortably inside `i64` so emitter offset arithmetic cannot
/// overflow). `None` = keep the dynamic component (preserves the runtime
/// `ensure_int`/range trap).
#[allow(clippy::cast_possible_truncation)]
fn to_place_i64(c: f64) -> Option<i64> {
    const BOUND: f64 = 281_474_976_710_656.0; // 2^48
    (c.is_finite() && c.trunc() == c && (-BOUND..=BOUND).contains(&c)).then_some(c as i64)
}

// ----------------------------------------------------------------------------------
// Solver
// ----------------------------------------------------------------------------------

struct Solution {
    lattice: Vec<Lattice>,
    block_executable: Vec<bool>,
    edge_executable: HashSet<(BlockId, BlockId)>,
}

struct Solver<'a> {
    mir: &'a Mir,
    lattice: Vec<Lattice>,
    /// value -> dependent instruction values (SSA def-use edges, including
    /// edges internal to lazy trees and phi arguments).
    users: Vec<Vec<Value>>,
    /// value -> blocks branching on it as the terminator test.
    test_blocks: Vec<Vec<BlockId>>,
    /// value -> owning block, for phis (`usize::MAX` = not a placed phi).
    phi_block: Vec<usize>,
    block_executable: Vec<bool>,
    edge_executable: HashSet<(BlockId, BlockId)>,
    flow_work: Vec<(BlockId, BlockId)>,
    ssa_work: Vec<Value>,
}

impl<'a> Solver<'a> {
    // The arena index fits `Value` (u32) by construction (`Mir::push_inst`).
    #[allow(clippy::cast_possible_truncation)]
    fn solve(mir: &'a Mir) -> Solution {
        let n = mir.insts.len();
        let mut s = Self {
            mir,
            lattice: vec![Lattice::Top; n],
            users: vec![Vec::new(); n],
            test_blocks: vec![Vec::new(); n],
            phi_block: vec![usize::MAX; n],
            block_executable: vec![false; mir.blocks.len()],
            edge_executable: HashSet::new(),
            flow_work: Vec::new(),
            ssa_work: Vec::new(),
        };
        for (i, inst) in mir.insts.iter().enumerate() {
            Mir::for_each_operand(inst, |o| s.users[o as usize].push(i as Value));
            if matches!(inst, Inst::ConstInt(_) | Inst::ConstFloat(_)) {
                s.lattice[i] = s.transfer(i as Value);
            }
        }
        for (b, block) in mir.blocks.iter().enumerate() {
            for &p in &block.phis {
                s.phi_block[p as usize] = b;
            }
            if let Terminator::Branch { test, .. } = &block.terminator {
                s.test_blocks[*test as usize].push(b);
            }
        }

        s.block_executable[0] = true;
        s.visit_block(0);
        s.drain();

        // Defensive normalization (relevant once phis exist, W2): a Branch in
        // an executable block whose test is still Top (e.g. a degenerate phi
        // cycle) would leave its successors unreachable while the terminator
        // still points at them. Force such tests to Bottom and re-drain so
        // every kept terminator's successors stay executable.
        loop {
            let mut forced = false;
            for b in 0..mir.blocks.len() {
                if !s.block_executable[b] {
                    continue;
                }
                if let Terminator::Branch { test, .. } = &mir.blocks[b].terminator
                    && matches!(s.lattice[*test as usize], Lattice::Top)
                {
                    s.lower_to(*test, Lattice::Bottom);
                    forced = true;
                }
            }
            if !forced {
                break;
            }
            s.drain();
        }

        Solution {
            lattice: s.lattice,
            block_executable: s.block_executable,
            edge_executable: s.edge_executable,
        }
    }

    fn drain(&mut self) {
        loop {
            if let Some((_src, dst)) = self.flow_work.pop() {
                if self.block_executable[dst] {
                    // Re-entered along a new edge: only phi meets can change.
                    for i in 0..self.mir.blocks[dst].phis.len() {
                        let p = self.mir.blocks[dst].phis[i];
                        self.visit_value(p);
                    }
                } else {
                    self.block_executable[dst] = true;
                    self.visit_block(dst);
                }
                continue;
            }
            if let Some(v) = self.ssa_work.pop() {
                self.visit_value(v);
                continue;
            }
            break;
        }
    }

    /// First visit of a newly executable block: phis, the schedule (lazy
    /// trees first, in dependency order), then the terminator.
    fn visit_block(&mut self, b: BlockId) {
        for i in 0..self.mir.blocks[b].phis.len() {
            let p = self.mir.blocks[b].phis[i];
            self.visit_value(p);
        }
        for i in 0..self.mir.blocks[b].insts.len() {
            let v = self.mir.blocks[b].insts[i];
            let mut roots: Vec<Value> = Vec::new();
            Mir::for_each_lazy_root(self.mir.inst(v), |root| roots.push(root));
            for root in roots {
                self.visit_lazy_tree(root);
            }
            self.visit_value(v);
        }
        self.process_terminator(b);
    }

    /// Visits every instruction of a lazy tree, operands before users
    /// (iterative post-order; cycle-guarded against malformed input).
    fn visit_lazy_tree(&mut self, root: Value) {
        enum W {
            Visit(Value),
            Eval(Value),
        }
        let mut seen: HashSet<Value> = HashSet::new();
        let mut stack = vec![W::Visit(root)];
        while let Some(item) = stack.pop() {
            match item {
                W::Visit(v) => {
                    if self.mir.is_const(v) || !seen.insert(v) {
                        continue;
                    }
                    stack.push(W::Eval(v));
                    Mir::for_each_operand(self.mir.inst(v), |o| stack.push(W::Visit(o)));
                }
                W::Eval(v) => self.visit_value(v),
            }
        }
    }

    fn visit_value(&mut self, v: Value) {
        let new = self.transfer(v);
        let old = self.lattice[v as usize];
        if old.same(new) {
            return;
        }
        debug_assert!(
            matches!(
                (old, new),
                (Lattice::Top, _) | (Lattice::Const(_), Lattice::Const(_) | Lattice::Bottom)
            ),
            "non-monotone lattice move for value {v}: {old:?} -> {new:?}"
        );
        self.lower_to(v, new);
    }

    /// Records a lattice lowering and propagates: SSA users are re-queued and
    /// terminators testing this value are re-processed.
    fn lower_to(&mut self, v: Value, new: Lattice) {
        self.lattice[v as usize] = new;
        self.ssa_work.extend_from_slice(&self.users[v as usize]);
        for i in 0..self.test_blocks[v as usize].len() {
            let b = self.test_blocks[v as usize][i];
            if self.block_executable[b] {
                self.process_terminator(b);
            }
        }
    }

    /// Marks newly executable outgoing edges per the test's lattice value.
    fn process_terminator(&mut self, b: BlockId) {
        match &self.mir.blocks[b].terminator {
            Terminator::Exit => {}
            Terminator::Jump(t) => self.mark_edge(b, *t),
            Terminator::Branch {
                test,
                cases,
                default,
            } => match self.lattice[*test as usize] {
                Lattice::Top => {}
                Lattice::Bottom => {
                    let succs: Vec<BlockId> = self.mir.blocks[b].terminator.successors().collect();
                    for t in succs {
                        self.mark_edge(b, t);
                    }
                }
                Lattice::Const(c) => {
                    if let Some(t) = taken_target(cases, *default, c) {
                        self.mark_edge(b, t);
                    }
                    // No match, no default: runtime exits (emit.rs uses the
                    // exit index as the implicit default) — no edge.
                }
            },
        }
    }

    fn mark_edge(&mut self, src: BlockId, dst: BlockId) {
        if self.edge_executable.insert((src, dst)) {
            self.flow_work.push((src, dst));
        }
    }

    /// The transfer function (module docs). Strict except the documented
    /// `ShortCircuit` refinement.
    #[allow(clippy::cast_precision_loss)]
    fn transfer(&self, v: Value) -> Lattice {
        match self.mir.inst(v) {
            // Constants seed with their *post-emission runtime* value:
            // emission collapses every integral constant to an int node via
            // `value + 0.0` (emit.rs::push_numeric), so a constant `-0.0` is
            // `+0.0` at runtime (legacy parity).
            Inst::ConstInt(c) => Lattice::Const(*c as f64),
            Inst::ConstFloat(c) => Lattice::Const(if *c == 0.0 {
                0.0
            } else if c.is_nan() {
                // Post-emission runtime value: NaN consts are ROM reads, and
                // the ROM NaN is the canonical positive quiet NaN — a literal
                // with a different payload/sign does not survive emission.
                f64::NAN
            } else {
                *c
            }),
            // No memory tracking in W1; stores produce no usable value.
            Inst::Load { .. } | Inst::Store { .. } => Lattice::Bottom,
            Inst::Phi { args } => {
                let b = self.phi_block[v as usize];
                if b == usize::MAX {
                    return Lattice::Bottom; // unplaced phi: out of contract
                }
                let mut acc = Lattice::Top;
                for &(pred, arg) in args {
                    if !self.edge_executable.contains(&(pred, b)) {
                        continue;
                    }
                    acc = meet(acc, self.lattice[arg as usize]);
                    if matches!(acc, Lattice::Bottom) {
                        break;
                    }
                }
                acc
            }
            Inst::ShortCircuit { op, lhs, rhs, .. } => match self.lattice[*lhs as usize] {
                Lattice::Top => Lattice::Top,
                Lattice::Bottom => Lattice::Bottom,
                Lattice::Const(c) => {
                    if sc_stops(*op, c) {
                        // The result is the last evaluated value: lhs itself.
                        Lattice::Const(c)
                    } else {
                        // Pass-through: the result is exactly eval(rhs).
                        self.lattice[*rhs as usize]
                    }
                }
            },
            // The W4 if-conversion product. SCCP runs before if-conversion in
            // the registry, so this is defensive coverage: with a known test
            // the result is exactly the taken arm's lattice value (the other
            // arm never runs); an unknown test is overdefined (no
            // phase-2 splicing refinement is implemented for `Select`).
            Inst::Select {
                test,
                then_root,
                else_root,
            } => match self.lattice[*test as usize] {
                Lattice::Top => Lattice::Top,
                Lattice::Bottom => Lattice::Bottom,
                Lattice::Const(c) => {
                    // `If` semantics: test != 0.0 takes the then arm (NaN is
                    // truthy).
                    if c == 0.0 {
                        self.lattice[*else_root as usize]
                    } else {
                        self.lattice[*then_root as usize]
                    }
                }
            },
            Inst::Op { op, args, .. } => {
                if !op_effects(*op).is_pure() {
                    return Lattice::Bottom;
                }
                let mut vals = [0.0f64; 5];
                if args.len() > vals.len() {
                    return Lattice::Bottom;
                }
                let mut any_top = false;
                for (slot, &a) in vals.iter_mut().zip(args) {
                    match self.lattice[a as usize] {
                        Lattice::Bottom => return Lattice::Bottom,
                        Lattice::Top => any_top = true,
                        Lattice::Const(c) => *slot = c,
                    }
                }
                if any_top {
                    return Lattice::Top;
                }
                match fold_op(*op, &vals[..args.len()]) {
                    // A computed `-0.0` is unmaterializable: every constant
                    // node emits as `+0.0` (push_numeric collapse), while the
                    // original instruction computes a true `-0.0` at runtime
                    // (observable through Sign/Arctan2). Refuse the fold.
                    Some(c) if c == 0.0 && c.is_sign_negative() => Lattice::Bottom,
                    // Canonical-NaN rule (module docs): a freshly generated
                    // NaN with non-canonical bits (e.g. x86 `inf * 0` =
                    // `-NaN`) is unmaterializable — NaN consts emit as the
                    // ROM's canonical +NaN and `Sign` exposes the difference.
                    Some(c) if c.is_nan() && c.to_bits() != f64::NAN.to_bits() => Lattice::Bottom,
                    Some(c) => Lattice::Const(c),
                    None => Lattice::Bottom,
                }
            }
        }
    }
}

// ----------------------------------------------------------------------------------
// Rewriter
// ----------------------------------------------------------------------------------

struct Rewriter<'a> {
    mir: &'a mut Mir,
    lattice: Vec<Lattice>,
    block_executable: Vec<bool>,
    edge_executable: HashSet<(BlockId, BlockId)>,
    /// Values with a remaining use that could not be rewritten away (an
    /// unconvertible constant place component): their defining instructions
    /// must stay scheduled.
    kept_use: Vec<bool>,
    /// Materialized constants by bit pattern (lookup only; arena insertion
    /// order is the deterministic scan order).
    const_cache: HashMap<u64, Value>,
    values_changed: bool,
    cfg_changed: bool,
}

impl<'a> Rewriter<'a> {
    fn apply(mir: &'a mut Mir, solution: Solution) -> (bool, bool) {
        let n = mir.insts.len();
        let mut r = Self {
            mir,
            lattice: solution.lattice,
            block_executable: solution.block_executable,
            edge_executable: solution.edge_executable,
            kept_use: vec![false; n],
            const_cache: HashMap::new(),
            values_changed: false,
            cfg_changed: false,
        };
        // Phase 1: rewrite operands/places/phi args/terminators in place
        // (discovers kept place-component uses).
        for b in 0..r.mir.blocks.len() {
            if r.block_executable[b] {
                r.rewrite_block_contents(b);
            }
        }
        // Phase 2: restructure schedules (drop folded defs, splice refined
        // ShortCircuits) and drop folded phis.
        for b in 0..r.mir.blocks.len() {
            if r.block_executable[b] {
                r.restructure_block(b);
            }
        }
        // Phase 3: remove never-executable blocks and compact ids.
        r.remove_unreachable();
        (r.values_changed, r.cfg_changed)
    }

    fn lattice_of(&self, v: Value) -> Lattice {
        // Values materialized during rewriting are constants and are never
        // queried; guard anyway.
        self.lattice
            .get(v as usize)
            .copied()
            .unwrap_or(Lattice::Bottom)
    }

    fn is_const_lattice(&self, v: Value) -> bool {
        matches!(self.lattice_of(v), Lattice::Const(_))
    }

    /// Materializes (or reuses) a constant instruction for a folded value.
    fn materialize(&mut self, c: f64) -> Value {
        if let Some(&v) = self.const_cache.get(&c.to_bits()) {
            return v;
        }
        let v = self.mir.push_inst(const_inst_for(c));
        self.values_changed = true; // arena growth is a MIR change
        self.const_cache.insert(c.to_bits(), v);
        v
    }

    /// Resolves an operand: folded values become materialized constants
    /// (existing constant instructions are reused as-is), and pass-through
    /// `ShortCircuit` chains (constant lhs that does not short-circuit) are
    /// skipped down to their rhs. Anything else is returned unchanged.
    fn resolve(&mut self, mut v: Value) -> Value {
        let mut budget = self.mir.insts.len() + 1;
        loop {
            if let Lattice::Const(c) = self.lattice_of(v) {
                if self.mir.is_const(v) {
                    return v;
                }
                return self.materialize(c);
            }
            if let Inst::ShortCircuit { op, lhs, rhs, .. } = self.mir.inst(v)
                && let Lattice::Const(c) = self.lattice_of(*lhs)
                && !sc_stops(*op, c)
            {
                v = *rhs;
                budget -= 1;
                if budget == 0 {
                    return v; // malformed cyclic input: stop following
                }
                continue;
            }
            return v;
        }
    }

    /// A scheduled `ShortCircuit` the rewrite phase splices: lhs is a known
    /// constant that passes through, and the overall value is not a constant
    /// (the rhs tree must actually run).
    fn is_refinable_sc(&self, v: Value) -> bool {
        if self.is_const_lattice(v) {
            return false;
        }
        if let Inst::ShortCircuit { op, lhs, .. } = self.mir.inst(v)
            && let Lattice::Const(c) = self.lattice_of(*lhs)
        {
            return !sc_stops(*op, c);
        }
        false
    }

    // ---------------- Phase 1 ----------------

    fn rewrite_block_contents(&mut self, b: BlockId) {
        // Phis: prune non-executable incoming edges, resolve argument values.
        for i in 0..self.mir.blocks[b].phis.len() {
            let p = self.mir.blocks[b].phis[i];
            let Inst::Phi { args } = self.mir.inst(p).clone() else {
                continue;
            };
            let mut new_args = Vec::with_capacity(args.len());
            for (pred, val) in args {
                if !self.edge_executable.contains(&(pred, b)) {
                    continue;
                }
                new_args.push((pred, self.resolve(val)));
            }
            if let Inst::Phi { args } = &self.mir.insts[p as usize]
                && *args != new_args
            {
                self.mir.insts[p as usize] = Inst::Phi { args: new_args };
                self.values_changed = true;
            }
        }
        // Schedule: rewrite operands (refinable ShortCircuits are handled
        // whole by the phase-2 splice).
        for i in 0..self.mir.blocks[b].insts.len() {
            let v = self.mir.blocks[b].insts[i];
            if self.is_refinable_sc(v) {
                continue;
            }
            self.rewrite_inst_operands(v);
        }
        // Terminator: fold constant tests, otherwise resolve the test value.
        if let Terminator::Branch {
            test,
            cases,
            default,
        } = self.mir.blocks[b].terminator.clone()
        {
            if let Lattice::Const(c) = self.lattice_of(test) {
                self.mir.blocks[b].terminator = match taken_target(&cases, default, c) {
                    Some(t) => Terminator::Jump(t),
                    None => Terminator::Exit,
                };
                self.cfg_changed = true;
            } else {
                let new_test = self.resolve(test);
                if new_test != test
                    && let Terminator::Branch { test, .. } = &mut self.mir.blocks[b].terminator
                {
                    *test = new_test;
                    self.values_changed = true;
                }
            }
        }
    }

    /// Rewrites the operands of one instruction in place: value operands are
    /// resolved; place components convert to constant form when legal, else
    /// the producing value is pinned via `kept_use`. For a (non-refinable)
    /// `ShortCircuit` the lazy rhs tree's instructions are rewritten too.
    fn rewrite_inst_operands(&mut self, v: Value) {
        match self.mir.inst(v).clone() {
            Inst::ConstInt(_) | Inst::ConstFloat(_) | Inst::Phi { .. } => {}
            Inst::Op { args, .. } => {
                let new_args: Vec<Value> = args.iter().map(|&a| self.resolve(a)).collect();
                if new_args != args
                    && let Inst::Op { args, .. } = &mut self.mir.insts[v as usize]
                {
                    *args = new_args;
                    self.values_changed = true;
                }
            }
            Inst::ShortCircuit { lhs, rhs, .. } => {
                let new_lhs = self.resolve(lhs);
                let new_rhs = self.resolve(rhs);
                if (new_lhs, new_rhs) != (lhs, rhs)
                    && let Inst::ShortCircuit { lhs, rhs, .. } = &mut self.mir.insts[v as usize]
                {
                    (*lhs, *rhs) = (new_lhs, new_rhs);
                    self.values_changed = true;
                }
                self.rewrite_lazy_tree(new_rhs);
            }
            Inst::Select {
                test,
                then_root,
                else_root,
            } => {
                let new_test = self.resolve(test);
                let new_then = self.resolve(then_root);
                let new_else = self.resolve(else_root);
                if (new_test, new_then, new_else) != (test, then_root, else_root)
                    && let Inst::Select {
                        test,
                        then_root,
                        else_root,
                    } = &mut self.mir.insts[v as usize]
                {
                    (*test, *then_root, *else_root) = (new_test, new_then, new_else);
                    self.values_changed = true;
                }
                self.rewrite_lazy_tree(new_then);
                self.rewrite_lazy_tree(new_else);
            }
            Inst::Load { mut place } => {
                if self.rewrite_place(&mut place) {
                    self.mir.insts[v as usize] = Inst::Load { place };
                    self.values_changed = true;
                }
            }
            Inst::Store { mut place, value } => {
                let place_changed = self.rewrite_place(&mut place);
                let new_value = self.resolve(value);
                if place_changed || new_value != value {
                    self.mir.insts[v as usize] = Inst::Store {
                        place,
                        value: new_value,
                    };
                    self.values_changed = true;
                }
            }
        }
    }

    /// Rewrites a place's dynamic components. Returns whether it changed.
    /// Unconvertible constant components pin their producing value.
    fn rewrite_place(&mut self, place: &mut crate::mir::Place) -> bool {
        let mut changed = false;
        if let BlockRef::Value(v) = place.block
            && let Lattice::Const(c) = self.lattice_of(v)
        {
            if let Some(i) = to_place_i64(c) {
                place.block = BlockRef::Concrete(i);
                changed = true;
            } else {
                self.keep_use(v);
            }
        }
        if let IndexRef::Value(v) = place.index
            && let Lattice::Const(c) = self.lattice_of(v)
        {
            if let Some(i) = to_place_i64(c) {
                place.index = IndexRef::Const(i);
                changed = true;
            } else {
                self.keep_use(v);
            }
        }
        self.values_changed |= changed;
        changed
    }

    fn keep_use(&mut self, v: Value) {
        if let Some(slot) = self.kept_use.get_mut(v as usize) {
            *slot = true;
        }
    }

    /// Rewrites operands of every instruction inside a (kept) lazy tree.
    /// Pure folded subtrees disappear by becoming constant operands of their
    /// parents; pass-through chains were already skipped by `resolve`.
    fn rewrite_lazy_tree(&mut self, root: Value) {
        let mut seen: HashSet<Value> = HashSet::new();
        let mut stack = vec![root];
        while let Some(v) = stack.pop() {
            if self.mir.is_const(v) || self.is_const_lattice(v) || !seen.insert(v) {
                continue;
            }
            match self.mir.inst(v).clone() {
                Inst::ConstInt(_) | Inst::ConstFloat(_) => {}
                Inst::Op { args, .. } => {
                    let new_args: Vec<Value> = args.iter().map(|&a| self.resolve(a)).collect();
                    for &a in &new_args {
                        stack.push(a);
                    }
                    if new_args != args
                        && let Inst::Op { args, .. } = &mut self.mir.insts[v as usize]
                    {
                        *args = new_args;
                        self.values_changed = true;
                    }
                }
                Inst::ShortCircuit { lhs, rhs, .. } => {
                    let new_lhs = self.resolve(lhs);
                    let new_rhs = self.resolve(rhs);
                    stack.push(new_lhs);
                    stack.push(new_rhs);
                    if (new_lhs, new_rhs) != (lhs, rhs)
                        && let Inst::ShortCircuit { lhs, rhs, .. } = &mut self.mir.insts[v as usize]
                    {
                        (*lhs, *rhs) = (new_lhs, new_rhs);
                        self.values_changed = true;
                    }
                }
                Inst::Select {
                    test,
                    then_root,
                    else_root,
                } => {
                    let new_test = self.resolve(test);
                    let new_then = self.resolve(then_root);
                    let new_else = self.resolve(else_root);
                    stack.push(new_test);
                    stack.push(new_then);
                    stack.push(new_else);
                    if (new_test, new_then, new_else) != (test, then_root, else_root)
                        && let Inst::Select {
                            test,
                            then_root,
                            else_root,
                        } = &mut self.mir.insts[v as usize]
                    {
                        (*test, *then_root, *else_root) = (new_test, new_then, new_else);
                        self.values_changed = true;
                    }
                }
                Inst::Load { mut place } => {
                    if self.rewrite_place(&mut place) {
                        self.mir.insts[v as usize] = Inst::Load { place };
                    }
                    if let BlockRef::Value(bv) = place.block {
                        stack.push(bv);
                    }
                    if let IndexRef::Value(iv) = place.index {
                        stack.push(iv);
                    }
                }
                Inst::Store { mut place, value } => {
                    // Legal inside W4 if-conversion arm trees (Execute-wrapped
                    // statements); rewrite like the eager Store case.
                    let place_changed = self.rewrite_place(&mut place);
                    let new_value = self.resolve(value);
                    if place_changed || new_value != value {
                        self.mir.insts[v as usize] = Inst::Store {
                            place,
                            value: new_value,
                        };
                        self.values_changed = true;
                    }
                    stack.push(new_value);
                    if let BlockRef::Value(bv) = place.block {
                        stack.push(bv);
                    }
                    if let IndexRef::Value(iv) = place.index {
                        stack.push(iv);
                    }
                }
                Inst::Phi { .. } => {
                    debug_assert!(false, "Phi inside a lazy tree (value {v})");
                }
            }
        }
    }

    // ---------------- Phase 2 ----------------

    fn restructure_block(&mut self, b: BlockId) {
        // Drop folded phis (their uses were rewritten to constants).
        let phis = std::mem::take(&mut self.mir.blocks[b].phis);
        let phi_count = phis.len();
        let kept_phis: Vec<Value> = phis
            .into_iter()
            .filter(|&p| !self.is_const_lattice(p) || self.kept_use[p as usize])
            .collect();
        if kept_phis.len() != phi_count {
            self.cfg_changed = true; // phi placement changed
        }
        self.mir.blocks[b].phis = kept_phis;

        // Rebuild the schedule: drop folded defs, splice refined ShortCircuits.
        let old = std::mem::take(&mut self.mir.blocks[b].insts);
        let mut new = Vec::with_capacity(old.len());
        for v in old {
            if self.is_refinable_sc(v) {
                self.splice_sc(v, &mut new);
                self.values_changed = true;
            } else if self.is_const_lattice(v) && !self.kept_use[v as usize] {
                // Folded: evaluation proven pure and successful; uses were
                // rewritten to the constant. Sweep it (LowerError::MultiUse
                // discipline).
                self.values_changed = true;
            } else {
                new.push(v);
            }
        }
        self.mir.blocks[b].insts = new;
    }

    /// Splices the remaining rhs tree of a refined `ShortCircuit` into the
    /// eager schedule at the instruction's own slot, in lazy-evaluation order
    /// (module docs). The `ShortCircuit` itself is dropped; its uses resolve
    /// to the rhs root.
    #[allow(clippy::too_many_lines)] // one work-stack state machine
    fn splice_sc(&mut self, v: Value, out: &mut Vec<Value>) {
        enum W {
            Visit(Value),
            Sched(Value),
        }
        let root = self.resolve(v);
        if self.mir.is_const(root) {
            return; // fully folded after all (defensive; uses resolve to it)
        }
        let mut seen: HashSet<Value> = HashSet::new();
        let mut stack = vec![W::Visit(root)];
        while let Some(item) = stack.pop() {
            match item {
                W::Sched(x) => out.push(x),
                W::Visit(x) => {
                    if self.mir.is_const(x) || !seen.insert(x) {
                        continue;
                    }
                    if self.is_const_lattice(x) {
                        // A folded value pinned by an unconvertible place
                        // component: schedule it (its operands fold to
                        // constants), do not descend.
                        self.rewrite_inst_operands(x);
                        stack.push(W::Sched(x));
                        continue;
                    }
                    match self.mir.inst(x).clone() {
                        Inst::ConstInt(_) | Inst::ConstFloat(_) => {}
                        Inst::Op { args, .. } => {
                            let new_args: Vec<Value> =
                                args.iter().map(|&a| self.resolve(a)).collect();
                            if new_args != args
                                && let Inst::Op { args, .. } = &mut self.mir.insts[x as usize]
                            {
                                args.clone_from(&new_args);
                            }
                            stack.push(W::Sched(x));
                            // Evaluation order: args left to right (LIFO).
                            for &a in new_args.iter().rev() {
                                stack.push(W::Visit(a));
                            }
                        }
                        Inst::ShortCircuit { lhs, rhs, .. } => {
                            // Becomes a scheduled ShortCircuit: lhs is eager,
                            // the rhs tree stays lazy (operand-rewritten only).
                            let new_lhs = self.resolve(lhs);
                            let new_rhs = self.resolve(rhs);
                            if (new_lhs, new_rhs) != (lhs, rhs)
                                && let Inst::ShortCircuit { lhs, rhs, .. } =
                                    &mut self.mir.insts[x as usize]
                            {
                                (*lhs, *rhs) = (new_lhs, new_rhs);
                            }
                            self.rewrite_lazy_tree(new_rhs);
                            stack.push(W::Sched(x));
                            stack.push(W::Visit(new_lhs));
                        }
                        Inst::Select {
                            test,
                            then_root,
                            else_root,
                        } => {
                            // Becomes a scheduled Select: the test is eager,
                            // both arm trees stay lazy (operand-rewritten
                            // only).
                            let new_test = self.resolve(test);
                            let new_then = self.resolve(then_root);
                            let new_else = self.resolve(else_root);
                            if (new_test, new_then, new_else) != (test, then_root, else_root)
                                && let Inst::Select {
                                    test,
                                    then_root,
                                    else_root,
                                } = &mut self.mir.insts[x as usize]
                            {
                                (*test, *then_root, *else_root) = (new_test, new_then, new_else);
                            }
                            self.rewrite_lazy_tree(new_then);
                            self.rewrite_lazy_tree(new_else);
                            stack.push(W::Sched(x));
                            stack.push(W::Visit(new_test));
                        }
                        Inst::Load { mut place } => {
                            if self.rewrite_place(&mut place) {
                                self.mir.insts[x as usize] = Inst::Load { place };
                            }
                            stack.push(W::Sched(x));
                            // Evaluation order: block then index (LIFO).
                            if let IndexRef::Value(iv) = place.index {
                                stack.push(W::Visit(iv));
                            }
                            if let BlockRef::Value(bv) = place.block {
                                stack.push(W::Visit(bv));
                            }
                        }
                        Inst::Store { mut place, value } => {
                            // Legal inside W4 if-conversion arm trees: splice
                            // back into the eager schedule like any member,
                            // evaluation order block, index, value (LIFO).
                            let place_changed = self.rewrite_place(&mut place);
                            let new_value = self.resolve(value);
                            if place_changed || new_value != value {
                                self.mir.insts[x as usize] = Inst::Store {
                                    place,
                                    value: new_value,
                                };
                            }
                            stack.push(W::Sched(x));
                            stack.push(W::Visit(new_value));
                            if let IndexRef::Value(iv) = place.index {
                                stack.push(W::Visit(iv));
                            }
                            if let BlockRef::Value(bv) = place.block {
                                stack.push(W::Visit(bv));
                            }
                        }
                        Inst::Phi { .. } => {
                            debug_assert!(false, "Phi inside a lazy tree (value {x})");
                            stack.push(W::Sched(x));
                        }
                    }
                }
            }
        }
    }

    // ---------------- Phase 3 ----------------

    fn remove_unreachable(&mut self) {
        if self.block_executable.iter().all(|&e| e) {
            return;
        }
        let mut new_id: Vec<Option<BlockId>> = vec![None; self.mir.blocks.len()];
        let mut next = 0usize;
        for (b, &exec) in self.block_executable.iter().enumerate() {
            if exec {
                new_id[b] = Some(next);
                next += 1;
            }
        }
        debug_assert_eq!(new_id[0], Some(0), "the entry block is always executable");

        let old_blocks = std::mem::take(&mut self.mir.blocks);
        let mut kept = Vec::with_capacity(next);
        for (b, block) in old_blocks.into_iter().enumerate() {
            if self.block_executable[b] {
                kept.push(block);
            }
        }
        let remap = |t: BlockId| new_id[t].expect("kept terminators only target executable blocks");
        for block in &mut kept {
            match &mut block.terminator {
                Terminator::Exit => {}
                Terminator::Jump(t) => *t = remap(*t),
                Terminator::Branch { cases, default, .. } => {
                    for (_, t) in cases.iter_mut() {
                        *t = remap(*t);
                    }
                    if let Some(d) = default {
                        *d = remap(*d);
                    }
                }
            }
            // Phi argument predecessor ids (args were pruned to executable
            // edges in phase 1, so every remaining pred is kept).
            for &p in &block.phis {
                if let Inst::Phi { args } = &mut self.mir.insts[p as usize] {
                    for (pred, _) in args.iter_mut() {
                        *pred =
                            new_id[*pred].expect("phi args were pruned to executable predecessors");
                    }
                }
            }
        }
        self.mir.blocks = kept;
        self.cfg_changed = true;
    }
}

#[cfg(test)]
mod tests {
    // Toolchain note: clippy 1.96 newly lints test code under --all-targets;
    // test constants are tiny; the casts cannot truncate/wrap in practice.
    #![allow(
        clippy::type_complexity,
        clippy::cast_possible_wrap,
        clippy::cast_precision_loss
    )]
    use super::*;
    use crate::cfg::{BasicBlock, BlockValue, Cfg, Edge, EdgeCond, IndexValue, Node, Place};
    use crate::diff::{DiffConfig, DiffOutcome, diff_with, run_with_memory};
    use crate::mir::{MirBlock, build_mir};
    use crate::passes::Pipeline;
    use crate::pipeline::{Level, compile_cfg, compile_cfg_with_pipeline};

    fn sccp_pipeline() -> Pipeline {
        Pipeline::new(vec![Box::new(Sccp)])
    }

    fn run_sccp(mir: &mut Mir) -> bool {
        // Run through a Pipeline so the debug changed-flag fingerprint guard
        // is active in every unit test.
        sccp_pipeline().run(mir, &mut Analyses::new())
    }

    /// Asserts minimal and minimal+[SCCP] behave identically on a frontend CFG
    /// (two memory seeds).
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
                |c| compile_cfg_with_pipeline(c, &sccp_pipeline()),
                &config,
            );
            assert_eq!(outcome, DiffOutcome::Match, "seed {seed:#x}");
        }
    }

    /// Tiny frontend-CFG builder (mirror of the one in diff.rs tests).
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
            self.cfg.places.push(Place {
                block: BlockValue::Int(block),
                index: IndexValue::Int(index),
                offset: 0,
            });
            self.cfg.places.len() - 1
        }
        fn int(&mut self, v: i64) -> usize {
            self.node(Node::ConstInt(v))
        }
        fn float(&mut self, v: f64) -> usize {
            self.node(Node::ConstFloat(v))
        }
        fn set(&mut self, place: usize, value: usize) -> usize {
            self.node(Node::Set { place, value })
        }
        fn pure(&mut self, op: Op, args: Vec<usize>) -> usize {
            self.node(Node::PureInstr { op, args })
        }
        fn instr(&mut self, op: Op, args: Vec<usize>) -> usize {
            self.node(Node::Instr { op, args })
        }
        fn block(&mut self, statements: Vec<usize>, test: usize, outgoing: Vec<Edge>) {
            self.cfg.blocks.push(BasicBlock {
                statements,
                test,
                outgoing,
            });
        }
        fn single_store(mut self, value: usize) -> Cfg {
            let p = self.place_int(20, 0);
            let s = self.set(p, value);
            let test = self.int(0);
            self.block(vec![s], test, vec![]);
            self.cfg
        }
    }

    /// All `Store` value instructions in schedule order across all blocks.
    fn store_values(mir: &Mir) -> Vec<Inst> {
        let mut out = Vec::new();
        for block in &mir.blocks {
            for &v in &block.insts {
                if let Inst::Store { value, .. } = mir.inst(v) {
                    out.push(mir.inst(*value).clone());
                }
            }
        }
        out
    }

    fn scheduled_op_count(mir: &Mir, op: Op) -> usize {
        mir.blocks
            .iter()
            .flat_map(|b| &b.insts)
            .filter(|&&v| matches!(mir.inst(v), Inst::Op { op: o, .. } if *o == op))
            .count()
    }

    // ------------------------- folding -------------------------

    #[test]
    fn folds_int_arith_to_int_tagged_const() {
        let mut b = B::default();
        let one = b.int(1);
        let two = b.int(2);
        let add = b.pure(Op::Add, vec![one, two]);
        let cfg = b.single_store(add);
        let mut mir = build_mir(&cfg).unwrap();
        assert!(run_sccp(&mut mir));
        assert_eq!(store_values(&mir), vec![Inst::ConstInt(3)]);
        assert_eq!(scheduled_op_count(&mir, Op::Add), 0, "folded Add is swept");
        assert_diff_match(&cfg);
    }

    #[test]
    fn folds_non_integral_result_float_tagged() {
        let mut b = B::default();
        let one = b.int(1);
        let two = b.int(2);
        let div = b.pure(Op::Divide, vec![one, two]);
        let cfg = b.single_store(div);
        let mut mir = build_mir(&cfg).unwrap();
        assert!(run_sccp(&mut mir));
        assert_eq!(store_values(&mir), vec![Inst::ConstFloat(0.5)]);
        assert_diff_match(&cfg);
    }

    #[test]
    fn integral_float_result_gets_int_tag() {
        let mut b = B::default();
        let h1 = b.float(0.5);
        let h2 = b.float(0.5);
        let add = b.pure(Op::Add, vec![h1, h2]);
        let cfg = b.single_store(add);
        let mut mir = build_mir(&cfg).unwrap();
        assert!(run_sccp(&mut mir));
        assert_eq!(store_values(&mir), vec![Inst::ConstInt(1)]);
    }

    #[test]
    fn computed_negative_zero_is_refused() {
        // Negate(0) computes a true -0.0 at runtime, but every constant node
        // emits as +0.0 (push_numeric collapse) — the fold must be refused so
        // the runtime value (observable through Sign) is preserved.
        let mut b = B::default();
        let zero = b.int(0);
        let neg = b.pure(Op::Negate, vec![zero]);
        let sign = b.pure(Op::Sign, vec![neg]);
        let cfg = b.single_store(sign);
        let mut mir = build_mir(&cfg).unwrap();
        assert!(!run_sccp(&mut mir), "nothing may fold");
        assert_eq!(scheduled_op_count(&mir, Op::Negate), 1);
        assert_eq!(scheduled_op_count(&mir, Op::Sign), 1);
        // Minimal computes Sign(-0.0) = -1 at runtime; so do we.
        assert_diff_match(&cfg);
    }

    #[test]
    fn negative_zero_constant_folds_as_positive_zero() {
        // Fuzz-found regression: a ConstFloat(-0.0) *instruction* is +0.0 at
        // runtime (emission collapses integral constants via `value + 0.0`),
        // so Sign(-0.0_const) folds to 1 — exactly what minimal computes.
        let mut b = B::default();
        let neg_zero = b.float(-0.0);
        let sign = b.pure(Op::Sign, vec![neg_zero]);
        let cfg = b.single_store(sign);
        let mut mir = build_mir(&cfg).unwrap();
        assert!(run_sccp(&mut mir));
        assert_eq!(store_values(&mir), vec![Inst::ConstInt(1)]);
        assert_diff_match(&cfg);
    }

    #[test]
    fn bankers_rounding_folds() {
        for (input, expected) in [(2.5, 2), (3.5, 4), (-2.5, -2), (0.5, 0)] {
            let mut b = B::default();
            let x = b.float(input);
            let round = b.pure(Op::Round, vec![x]);
            let cfg = b.single_store(round);
            let mut mir = build_mir(&cfg).unwrap();
            assert!(run_sccp(&mut mir));
            assert_eq!(
                store_values(&mir),
                vec![Inst::ConstInt(expected)],
                "round({input})"
            );
        }
    }

    #[test]
    fn floor_mod_sign_follows_divisor() {
        for (a, m, expected) in [(-7.0, 3.0, 2.0), (7.0, -3.0, -2.0), (7.5, -3.0, -1.5)] {
            let mut b = B::default();
            let x = b.float(a);
            let y = b.float(m);
            let modded = b.pure(Op::Mod, vec![x, y]);
            let cfg = b.single_store(modded);
            let mut mir = build_mir(&cfg).unwrap();
            assert!(run_sccp(&mut mir));
            let values = store_values(&mir);
            let got = match values[0] {
                Inst::ConstInt(i) => i as f64,
                Inst::ConstFloat(f) => f,
                ref other => panic!("expected const, got {other:?}"),
            };
            assert_eq!(got, expected, "Mod({a}, {m})");
        }
    }

    #[test]
    fn nan_and_inf_arithmetic_folds() {
        // inf + 1 = inf and NaN propagated from a NaN const (same py_* kernel
        // the interpreter runs ⇒ canonical bits) fold; see the canonical-NaN
        // rule for the generated-NaN cases below.
        let cases: Vec<(Op, f64, f64, fn(f64) -> bool)> = vec![
            (Op::Add, f64::INFINITY, 1.0, |c| c == f64::INFINITY),
            (Op::Subtract, f64::NAN, 1.0, f64::is_nan),
        ];
        for (op, x, y, check) in cases {
            let mut b = B::default();
            let a = b.float(x);
            let c = b.float(y);
            let node = b.pure(op, vec![a, c]);
            let cfg = b.single_store(node);
            let mut mir = build_mir(&cfg).unwrap();
            assert!(run_sccp(&mut mir), "{op:?}({x}, {y}) must fold");
            let values = store_values(&mir);
            let Inst::ConstFloat(folded) = values[0] else {
                panic!("{op:?}({x}, {y}): expected float const, got {values:?}");
            };
            assert!(check(folded), "{op:?}({x}, {y}) folded to {folded}");
        }
    }

    #[test]
    fn generated_nan_folds_follow_canonical_nan_rule() {
        // `inf * 0` and `inf + -inf` GENERATE a NaN whose runtime bits are
        // platform-defined (x86: -NaN), while a folded NaN const would emit
        // as the ROM's canonical +NaN — observable through Sign. Folding is
        // allowed only when the kernel's result is bit-canonical; otherwise
        // the instruction must stay scheduled (W1-merge alignment with GVN).
        for (op, x, y) in [
            (Op::Multiply, f64::INFINITY, 0.0),
            (Op::Add, f64::INFINITY, f64::NEG_INFINITY),
        ] {
            let kernel_result = match op {
                Op::Multiply => x * y,
                Op::Add => x + y,
                _ => unreachable!(),
            };
            let canonical = kernel_result.to_bits() == f64::NAN.to_bits();
            let mut b = B::default();
            let a = b.float(x);
            let c = b.float(y);
            let node = b.pure(op, vec![a, c]);
            let cfg = b.single_store(node);
            let mut mir = build_mir(&cfg).unwrap();
            run_sccp(&mut mir);
            let values = store_values(&mir);
            let folded_to_const = matches!(values[0], Inst::ConstFloat(_));
            assert_eq!(
                folded_to_const,
                canonical,
                "{op:?}({x}, {y}): fold iff kernel result is canonical NaN \
                 (result bits {:#x})",
                kernel_result.to_bits()
            );
        }
    }

    // ------------------------- fold refusal (Python raises) -------------------------

    #[test]
    fn no_fold_where_python_raises() {
        // (op, args): every case must stay scheduled and unfolded.
        let cases: Vec<(Op, Vec<f64>)> = vec![
            (Op::Mod, vec![1.0, 0.0]),                  // ZeroDivisionError
            (Op::Divide, vec![1.0, 0.0]),               // ZeroDivisionError
            (Op::Rem, vec![1.0, 0.0]),                  // ValueError (math domain)
            (Op::Rem, vec![f64::INFINITY, 2.0]),        // ValueError
            (Op::Log, vec![-1.0]),                      // ValueError
            (Op::Log, vec![0.0]),                       // ValueError
            (Op::Power, vec![0.0, -1.0]),               // 0**neg: ZeroDivisionError
            (Op::Power, vec![-2.0, 0.5]),               // complex: ValueError
            (Op::Power, vec![1e308, 2.0]),              // OverflowError
            (Op::Arcsin, vec![2.0]),                    // ValueError
            (Op::Arccos, vec![-2.0]),                   // ValueError
            (Op::Round, vec![f64::NAN]),                // ValueError
            (Op::Round, vec![f64::INFINITY]),           // OverflowError
            (Op::Ceil, vec![f64::INFINITY]),            // OverflowError
            (Op::Floor, vec![f64::NAN]),                // ValueError
            (Op::Trunc, vec![f64::NAN]),                // ValueError
            (Op::Sin, vec![f64::INFINITY]),             // ValueError
            (Op::Tan, vec![f64::NEG_INFINITY]),         // ValueError
            (Op::Sinh, vec![1e300]),                    // OverflowError
            (Op::Unlerp, vec![1.0, 1.0, 2.0]),          // div by zero
            (Op::Remap, vec![1.0, 1.0, 0.0, 1.0, 2.0]), // div by zero
        ];
        for (op, args) in cases {
            let mut b = B::default();
            let arg_nodes: Vec<usize> = args.iter().map(|&a| b.float(a)).collect();
            let node = b.pure(op, arg_nodes);
            let cfg = b.single_store(node);
            let mut mir = build_mir(&cfg).unwrap();
            let changed = run_sccp(&mut mir);
            assert!(!changed, "{op:?}({args:?}) must not change anything");
            assert_eq!(
                scheduled_op_count(&mir, op),
                1,
                "{op:?}({args:?}) must stay scheduled"
            );
            // Both sides trap identically at runtime.
            assert_diff_match(&cfg);
        }
    }

    // ------------------------- branch pruning -------------------------

    /// Entry branches on `test_node` over {0: store 10, 1: store 20,
    /// default: store 30}; each arm stores to a distinct cell so the
    /// surviving arm is observable.
    fn branch_cfg(mut b: B, test_node: usize) -> Cfg {
        let mut edges = Vec::new();
        for (i, cond) in [EdgeCond::Int(0), EdgeCond::Int(1), EdgeCond::None]
            .into_iter()
            .enumerate()
        {
            edges.push(Edge {
                cond,
                target: i + 1,
            });
        }
        b.block(vec![], test_node, edges);
        for (i, v) in [10, 20, 30].into_iter().enumerate() {
            let p = b.place_int(20, i as i64);
            let c = b.int(v);
            let s = b.set(p, c);
            let zt = b.int(0);
            b.block(vec![s], zt, vec![]);
        }
        b.cfg
    }

    #[test]
    fn const_test_takes_matching_edge_and_prunes_others() {
        // Add(0, 1) folds to 1: only the {1: store 20} arm survives.
        let mut b = B::default();
        let zero = b.int(0);
        let one = b.int(1);
        let add = b.pure(Op::Add, vec![zero, one]);
        let cfg = branch_cfg(b, add);
        let mut mir = build_mir(&cfg).unwrap();
        assert!(run_sccp(&mut mir));
        assert_eq!(mir.blocks.len(), 2, "two arms pruned");
        assert!(matches!(mir.blocks[0].terminator, Terminator::Jump(1)));
        assert_eq!(store_values(&mir), vec![Inst::ConstInt(20)]);
        assert_diff_match(&cfg);
    }

    #[test]
    fn float_const_test_matches_int_cond() {
        // Add(0.5, 0.5) = 1.0 (float) must take the Int(1) edge.
        let mut b = B::default();
        let h1 = b.float(0.5);
        let h2 = b.float(0.5);
        let add = b.pure(Op::Add, vec![h1, h2]);
        let cfg = branch_cfg(b, add);
        let mut mir = build_mir(&cfg).unwrap();
        assert!(run_sccp(&mut mir));
        assert_eq!(store_values(&mir), vec![Inst::ConstInt(20)]);
        assert_diff_match(&cfg);
    }

    #[test]
    fn int_const_test_matches_float_cond() {
        // test Add(1, 1) = 2 against a Float(2.0) cond.
        let mut b = B::default();
        let one1 = b.int(1);
        let one2 = b.int(1);
        let add = b.pure(Op::Add, vec![one1, one2]);
        b.block(
            vec![],
            add,
            vec![
                Edge {
                    cond: EdgeCond::Float(2.0),
                    target: 1,
                },
                Edge {
                    cond: EdgeCond::None,
                    target: 2,
                },
            ],
        );
        for v in [111, 222] {
            let p = b.place_int(20, 0);
            let c = b.int(v);
            let s = b.set(p, c);
            let zt = b.int(0);
            b.block(vec![s], zt, vec![]);
        }
        let cfg = b.cfg;
        let mut mir = build_mir(&cfg).unwrap();
        assert!(run_sccp(&mut mir));
        assert_eq!(store_values(&mir), vec![Inst::ConstInt(111)]);
        assert_diff_match(&cfg);
    }

    #[test]
    fn const_test_with_no_match_and_no_default_becomes_exit() {
        // Two cond edges, no default (a single cond edge would be coalesced
        // to an unconditional jump by build_mir before SCCP ever runs); the
        // folded test (2) matches neither, so the runtime exits (the emitter
        // uses the exit index as the implicit default).
        let mut b = B::default();
        let one1 = b.int(1);
        let one2 = b.int(1);
        let add = b.pure(Op::Add, vec![one1, one2]); // 2
        b.block(
            vec![],
            add,
            vec![
                Edge {
                    cond: EdgeCond::Int(5),
                    target: 1,
                },
                Edge {
                    cond: EdgeCond::Int(6),
                    target: 2,
                },
            ],
        );
        for v in [98, 99] {
            let p = b.place_int(20, 0);
            let c = b.int(v);
            let s = b.set(p, c);
            let zt = b.int(0);
            b.block(vec![s], zt, vec![]);
        }
        let cfg = b.cfg;
        let mut mir = build_mir(&cfg).unwrap();
        assert!(run_sccp(&mut mir));
        assert_eq!(mir.blocks.len(), 1);
        assert_eq!(mir.blocks[0].terminator, Terminator::Exit);
        assert!(
            store_values(&mir).is_empty(),
            "dead stores removed with their blocks"
        );
        assert_diff_match(&cfg);
    }

    #[test]
    fn nan_test_takes_default_edge() {
        let mut b = B::default();
        let nan = b.float(f64::NAN);
        let one = b.int(1);
        let add = b.pure(Op::Add, vec![nan, one]); // NaN (folds successfully)
        let cfg = branch_cfg(b, add);
        let mut mir = build_mir(&cfg).unwrap();
        assert!(run_sccp(&mut mir));
        assert_eq!(store_values(&mir), vec![Inst::ConstInt(30)]);
        assert_diff_match(&cfg);
    }

    #[test]
    fn non_const_test_keeps_branch_and_all_arms() {
        let mut b = B::default();
        let p = b.place_int(21, 0);
        let load = b.node(Node::Get(p));
        let cfg = branch_cfg(b, load);
        let mut mir = build_mir(&cfg).unwrap();
        let blocks_before = mir.blocks.len();
        assert!(!run_sccp(&mut mir), "nothing to do");
        assert_eq!(mir.blocks.len(), blocks_before);
        assert!(matches!(
            mir.blocks[0].terminator,
            Terminator::Branch { .. }
        ));
    }

    // ------------------------- phis (hand-built; W2 readiness) -------------------------

    /// Hand-built diamond: entry branches on `test`; b1/b2 jump to b3 which
    /// has one phi over (b1: v1, b2: v2) feeding a store to 20[0].
    fn phi_diamond(test: Inst, v1: Inst, v2: Inst) -> Mir {
        let mut mir = Mir::new();
        for _ in 0..4 {
            mir.push_block();
        }
        let t = mir.push_inst(test);
        // Schedule the test if it is not a constant (consts stay unscheduled).
        if !mir.is_const(t) {
            mir.blocks[0].insts.push(t);
        }
        mir.blocks[0].terminator = Terminator::Branch {
            test: t,
            cases: vec![(CaseCond::Int(0), 1)],
            default: Some(2),
        };
        mir.blocks[1].terminator = Terminator::Jump(3);
        mir.blocks[2].terminator = Terminator::Jump(3);
        let a1 = mir.push_inst(v1);
        let a2 = mir.push_inst(v2);
        let phi = mir.push_inst(Inst::Phi {
            args: vec![(1, a1), (2, a2)],
        });
        mir.blocks[3].phis.push(phi);
        let store = mir.push_inst(Inst::Store {
            place: crate::mir::Place {
                block: BlockRef::Concrete(20),
                index: IndexRef::Const(0),
                offset: 0,
            },
            value: phi,
        });
        mir.blocks[3].insts.push(store);
        mir.blocks[3].terminator = Terminator::Exit;
        mir
    }

    #[test]
    fn phi_merges_over_executable_edges_only() {
        // Const test 1 takes the default edge (b2): the phi sees only the
        // b2 argument and folds to 7; b1 is removed.
        let mut mir = phi_diamond(Inst::ConstInt(1), Inst::ConstInt(5), Inst::ConstInt(7));
        assert!(run_sccp(&mut mir));
        assert_eq!(mir.blocks.len(), 3, "b1 pruned");
        assert_eq!(store_values(&mir), vec![Inst::ConstInt(7)]);
        let no_phis = mir.blocks.iter().all(|b| b.phis.is_empty());
        assert!(no_phis, "folded phi is dropped");
    }

    #[test]
    fn phi_with_agreeing_const_args_folds() {
        // Bottom test (a load): both edges executable; both args are 5.
        let load = Inst::Load {
            place: crate::mir::Place {
                block: BlockRef::Concrete(21),
                index: IndexRef::Const(0),
                offset: 0,
            },
        };
        let mut mir = phi_diamond(load, Inst::ConstInt(5), Inst::ConstInt(5));
        assert!(run_sccp(&mut mir));
        assert_eq!(store_values(&mir), vec![Inst::ConstInt(5)]);
    }

    #[test]
    fn phi_with_conflicting_const_args_stays() {
        let load = Inst::Load {
            place: crate::mir::Place {
                block: BlockRef::Concrete(21),
                index: IndexRef::Const(0),
                offset: 0,
            },
        };
        let mut mir = phi_diamond(load, Inst::ConstInt(5), Inst::ConstInt(7));
        assert!(!run_sccp(&mut mir), "no change: phi stays Bottom");
        assert_eq!(mir.blocks[3].phis.len(), 1);
        let Inst::Phi { args } = mir.inst(mir.blocks[3].phis[0]) else {
            panic!("phi survives");
        };
        assert_eq!(args.len(), 2, "both executable args kept");
    }

    #[test]
    fn phi_over_pos_and_neg_zero_const_args_folds_to_zero() {
        // Constant instructions seed with their post-emission runtime value,
        // where -0.0 collapses to +0.0 — so these args agree and merge.
        let load = Inst::Load {
            place: crate::mir::Place {
                block: BlockRef::Concrete(21),
                index: IndexRef::Const(0),
                offset: 0,
            },
        };
        let mut mir = phi_diamond(load, Inst::ConstFloat(0.0), Inst::ConstFloat(-0.0));
        assert!(run_sccp(&mut mir));
        assert_eq!(store_values(&mir), vec![Inst::ConstInt(0)]);
    }

    #[test]
    fn pruned_phi_args_are_remapped_after_block_removal() {
        // Const test 0 takes the {0: b1} edge; the b2 arg is pruned and the
        // phi folds to the b1 value. Blocks compact (b2 removed).
        let mut mir = phi_diamond(Inst::ConstInt(0), Inst::ConstInt(5), Inst::ConstInt(7));
        assert!(run_sccp(&mut mir));
        assert_eq!(mir.blocks.len(), 3);
        assert_eq!(store_values(&mir), vec![Inst::ConstInt(5)]);
        // All terminator targets are in range after compaction.
        for block in &mir.blocks {
            for succ in block.terminator.successors() {
                assert!(succ < mir.blocks.len());
            }
        }
    }

    // ------------------------- ShortCircuit (D11) -------------------------

    /// `Set(20[0], op(lhs_expr, DebugLog(7)))` — the log is the lazy rhs.
    fn sc_cfg(op: Op, lhs_left: i64, lhs_right: i64) -> Cfg {
        let mut b = B::default();
        let l = b.int(lhs_left);
        let r = b.int(lhs_right);
        let lhs = b.pure(Op::Add, vec![l, r]);
        let seven = b.int(7);
        let log = b.instr(Op::DebugLog, vec![seven]);
        let sc = b.pure(op, vec![lhs, log]);
        b.single_store(sc)
    }

    #[test]
    fn and_with_const_zero_lhs_folds_and_drops_lazy_tree() {
        let cfg = sc_cfg(Op::And, 0, 0); // lhs Add(0,0) = 0: short-circuits
        let mut mir = build_mir(&cfg).unwrap();
        assert!(run_sccp(&mut mir));
        assert_eq!(store_values(&mir), vec![Inst::ConstInt(0)]);
        assert_eq!(
            scheduled_op_count(&mir, Op::DebugLog),
            0,
            "lazy log dropped"
        );
        // Behavior: minimal also never runs the log (lhs == 0).
        assert_diff_match(&cfg);
    }

    #[test]
    fn or_with_const_nonzero_lhs_folds_to_lhs_value() {
        let cfg = sc_cfg(Op::Or, 1, 1); // lhs = 2: Or stops, result 2
        let mut mir = build_mir(&cfg).unwrap();
        assert!(run_sccp(&mut mir));
        assert_eq!(store_values(&mir), vec![Inst::ConstInt(2)]);
        assert_eq!(scheduled_op_count(&mir, Op::DebugLog), 0);
        assert_diff_match(&cfg);
    }

    #[test]
    fn or_with_const_nan_lhs_folds_to_nan() {
        // NaN is truthy: Or stops and returns NaN itself.
        let mut b = B::default();
        let nan = b.float(f64::NAN);
        let one = b.int(1);
        let lhs = b.pure(Op::Add, vec![nan, one]); // NaN
        let seven = b.int(7);
        let log = b.instr(Op::DebugLog, vec![seven]);
        let sc = b.pure(Op::Or, vec![lhs, log]);
        let cfg = b.single_store(sc);
        let mut mir = build_mir(&cfg).unwrap();
        assert!(run_sccp(&mut mir));
        let values = store_values(&mir);
        let Inst::ConstFloat(c) = values[0] else {
            panic!("expected NaN const, got {values:?}");
        };
        assert!(c.is_nan());
        assert_diff_match(&cfg);
    }

    #[test]
    fn and_with_const_nonzero_lhs_splices_rhs_eagerly() {
        let cfg = sc_cfg(Op::And, 1, 1); // lhs = 2: passes through to rhs
        let mut mir = build_mir(&cfg).unwrap();
        assert!(run_sccp(&mut mir));
        // The log is now eagerly scheduled; the ShortCircuit is gone; the
        // store consumes the log's value directly.
        assert_eq!(scheduled_op_count(&mir, Op::DebugLog), 1);
        let sc_count = mir
            .blocks
            .iter()
            .flat_map(|b| &b.insts)
            .filter(|&&v| matches!(mir.inst(v), Inst::ShortCircuit { .. }))
            .count();
        assert_eq!(sc_count, 0, "refined ShortCircuit is unscheduled");
        let insts: Vec<&Inst> = mir.blocks[0].insts.iter().map(|&v| mir.inst(v)).collect();
        assert!(
            matches!(
                insts[0],
                Inst::Op {
                    op: Op::DebugLog,
                    ..
                }
            ),
            "log evaluates at the ShortCircuit's old position: {insts:?}"
        );
        let Inst::Store { value, .. } = insts[1] else {
            panic!("store follows");
        };
        assert!(matches!(
            mir.inst(*value),
            Inst::Op {
                op: Op::DebugLog,
                ..
            }
        ));
        // Behavior: minimal evaluates lhs (2, truthy) then the log; result is
        // the log's value 0.0. Identical effects, order, and result.
        assert_diff_match(&cfg);
    }

    #[test]
    fn nested_short_circuit_chain_splices_in_evaluation_order() {
        // And(2, Or(0, Execute-free tree)): both levels refine; the inner
        // tree (DebugLog(5) twice via Add) must run in original order.
        let mut b = B::default();
        let one1 = b.int(1);
        let one2 = b.int(1);
        let lhs = b.pure(Op::Add, vec![one1, one2]); // 2
        let zero = b.int(0);
        let five = b.int(5);
        let log1 = b.instr(Op::DebugLog, vec![five]);
        let six = b.int(6);
        let log2 = b.instr(Op::DebugLog, vec![six]);
        let sum = b.pure(Op::Add, vec![log1, log2]); // logs 5 then 6
        let inner = b.pure(Op::Or, vec![zero, sum]);
        let sc = b.pure(Op::And, vec![lhs, inner]);
        let cfg = b.single_store(sc);
        let mut mir = build_mir(&cfg).unwrap();
        assert!(run_sccp(&mut mir));
        assert_eq!(scheduled_op_count(&mir, Op::DebugLog), 2);
        // Behavioral equivalence covers ordering (log [5, 6] on both sides).
        assert_diff_match(&cfg);

        let nodes = compile_cfg_with_pipeline(&cfg, &sccp_pipeline()).unwrap();
        let mut interp = crate::interpret::Interpreter::new(0);
        interp.run(&nodes).unwrap();
        assert_eq!(interp.log(), &[5.0, 6.0]);
    }

    #[test]
    fn kept_short_circuit_with_bottom_lhs_is_untouched_but_lazy_consts_fold() {
        // And(load, Add(1, 2)): lhs unknown, so the ShortCircuit stays; the
        // lazy Add folds into a constant rhs.
        let mut b = B::default();
        let p = b.place_int(21, 0);
        let load = b.node(Node::Get(p));
        let one = b.int(1);
        let two = b.int(2);
        let add = b.pure(Op::Add, vec![one, two]);
        let sc = b.pure(Op::And, vec![load, add]);
        let cfg = b.single_store(sc);
        let mut mir = build_mir(&cfg).unwrap();
        assert!(run_sccp(&mut mir));
        let sc_value = mir
            .blocks
            .iter()
            .flat_map(|blk| &blk.insts)
            .find(|&&v| matches!(mir.inst(v), Inst::ShortCircuit { .. }))
            .copied()
            .expect("ShortCircuit kept");
        let Inst::ShortCircuit { rhs, .. } = mir.inst(sc_value) else {
            unreachable!()
        };
        assert_eq!(mir.inst(*rhs), &Inst::ConstInt(3), "lazy rhs folded");
        assert_diff_match(&cfg);
    }

    // ------------------------- effects / draws -------------------------

    #[test]
    fn random_is_never_folded_but_its_const_args_are() {
        let mut b = B::default();
        let one1 = b.int(1);
        let one2 = b.int(1);
        let lo = b.pure(Op::Add, vec![one1, one2]); // 2
        let two = b.int(2);
        let two2 = b.int(2);
        let hi = b.pure(Op::Add, vec![two, two2]); // 4
        let draw = b.instr(Op::Random, vec![lo, hi]);
        let cfg = b.single_store(draw);
        let mut mir = build_mir(&cfg).unwrap();
        assert!(run_sccp(&mut mir));
        assert_eq!(scheduled_op_count(&mir, Op::Random), 1, "draw preserved");
        let random = mir
            .blocks
            .iter()
            .flat_map(|blk| &blk.insts)
            .find(|&&v| matches!(mir.inst(v), Inst::Op { op: Op::Random, .. }))
            .copied()
            .unwrap();
        let Inst::Op { args, .. } = mir.inst(random) else {
            unreachable!()
        };
        assert_eq!(mir.inst(args[0]), &Inst::ConstInt(2));
        assert_eq!(mir.inst(args[1]), &Inst::ConstInt(4));
        assert_eq!(scheduled_op_count(&mir, Op::Add), 0, "args folded");
        // Draw count parity is asserted by the harness (RngDraws mismatch).
        assert_diff_match(&cfg);
    }

    #[test]
    fn debug_log_and_break_are_never_folded() {
        let mut b = B::default();
        let one = b.int(1);
        let two = b.int(2);
        let add = b.pure(Op::Add, vec![one, two]);
        let log = b.instr(Op::DebugLog, vec![add]);
        let brk_flag = b.int(1);
        let three = b.int(3);
        let brk = b.instr(Op::Break, vec![brk_flag, three]);
        let test = b.int(0);
        b.block(vec![log, brk], test, vec![]);
        let cfg = b.cfg;
        let mut mir = build_mir(&cfg).unwrap();
        assert!(run_sccp(&mut mir)); // the Add folds
        assert_eq!(scheduled_op_count(&mir, Op::DebugLog), 1);
        assert_eq!(scheduled_op_count(&mir, Op::Break), 1);
        assert_diff_match(&cfg);
    }

    // ------------------------- place components -------------------------

    #[test]
    fn integral_const_index_becomes_const_index() {
        // Hand-built: Load 21[Add(1, 2)] -> Load 21[3]; the Add is swept.
        let mut mir = Mir::new();
        let b = mir.push_block();
        let one = mir.push_inst(Inst::ConstInt(1));
        let two = mir.push_inst(Inst::ConstInt(2));
        let add = mir.push_inst(Inst::Op {
            op: Op::Add,
            pure_node: true,
            args: vec![one, two],
        });
        mir.blocks[b].insts.push(add);
        let load = mir.push_inst(Inst::Load {
            place: crate::mir::Place {
                block: BlockRef::Concrete(21),
                index: IndexRef::Value(add),
                offset: 0,
            },
        });
        mir.blocks[b].insts.push(load);
        let store = mir.push_inst(Inst::Store {
            place: crate::mir::Place {
                block: BlockRef::Concrete(20),
                index: IndexRef::Const(0),
                offset: 0,
            },
            value: load,
        });
        mir.blocks[b].insts.push(store);
        assert!(run_sccp(&mut mir));
        let Inst::Load { place } = mir.inst(load) else {
            unreachable!()
        };
        assert_eq!(place.index, IndexRef::Const(3));
        assert_eq!(scheduled_op_count(&mir, Op::Add), 0);
        // Still lowers cleanly (the MultiUse discipline).
        let alloc = crate::alloc::allocate_temps(&mir).unwrap();
        crate::lower::lower_mir(&mir, &alloc).expect("valid lowerable MIR");
    }

    #[test]
    fn non_integral_const_index_keeps_dynamic_form_and_trap() {
        // Load 21[Divide(5, 2)]: 2.5 cannot become IndexRef::Const — the
        // Divide stays scheduled (kept_use) so the runtime ensure_int trap
        // survives at its original evaluation point. (In contract-valid MIR
        // today dynamic components are always Loads, which are Bottom and
        // never folded — this exercises the W2-proofing path with a
        // hand-built component, which is deliberately outside the current
        // lowering grammar; the assertions are structural.)
        let mut mir = Mir::new();
        let b = mir.push_block();
        let five = mir.push_inst(Inst::ConstInt(5));
        let two = mir.push_inst(Inst::ConstInt(2));
        let div = mir.push_inst(Inst::Op {
            op: Op::Divide,
            pure_node: true,
            args: vec![five, two],
        });
        mir.blocks[b].insts.push(div);
        let load = mir.push_inst(Inst::Load {
            place: crate::mir::Place {
                block: BlockRef::Concrete(21),
                index: IndexRef::Value(div),
                offset: 0,
            },
        });
        mir.blocks[b].insts.push(load);
        let store = mir.push_inst(Inst::Store {
            place: crate::mir::Place {
                block: BlockRef::Concrete(20),
                index: IndexRef::Const(0),
                offset: 0,
            },
            value: load,
        });
        mir.blocks[b].insts.push(store);
        assert!(!run_sccp(&mut mir), "nothing legally rewritable");
        let Inst::Load { place } = mir.inst(load) else {
            unreachable!()
        };
        assert_eq!(place.index, IndexRef::Value(div));
        assert_eq!(scheduled_op_count(&mir, Op::Divide), 1, "kept for the trap");
    }

    // ------------------------- loops / no-op -------------------------

    #[test]
    fn unreachable_loop_is_removed() {
        // Entry: branch on const 0 -> {0: exit-store, default: loop pair}.
        let mut b = B::default();
        let zero1 = b.int(0);
        let zero2 = b.int(0);
        let test = b.pure(Op::Add, vec![zero1, zero2]); // 0
        b.block(
            vec![],
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
        let p = b.place_int(20, 0);
        let c = b.int(1);
        let s = b.set(p, c);
        let zt = b.int(0);
        b.block(vec![s], zt, vec![]);
        // Self-looping dead block.
        let p2 = b.place_int(20, 1);
        let c2 = b.int(2);
        let s2 = b.set(p2, c2);
        let zt2 = b.int(0);
        b.block(
            vec![s2],
            zt2,
            vec![Edge {
                cond: EdgeCond::None,
                target: 2,
            }],
        );
        let cfg = b.cfg;
        let mut mir = build_mir(&cfg).unwrap();
        assert!(run_sccp(&mut mir));
        assert_eq!(store_values(&mir), vec![Inst::ConstInt(1)]);
        assert_diff_match(&cfg);
    }

    #[test]
    fn reports_unchanged_when_there_is_nothing_to_do() {
        // Pure memory traffic, no constants to propagate.
        let mut b = B::default();
        let src = b.place_int(21, 0);
        let load = b.node(Node::Get(src));
        let cfg = b.single_store(load);
        let mut mir = build_mir(&cfg).unwrap();
        assert!(!run_sccp(&mut mir));
    }

    #[test]
    fn empty_mir_is_a_no_op() {
        let mut mir = Mir::new();
        assert!(!run_sccp(&mut mir));
        let mut mir = Mir::new();
        mir.blocks.push(MirBlock::default());
        assert!(!run_sccp(&mut mir));
    }

    // ------------------------- effectiveness -------------------------

    #[test]
    fn sccp_reduces_eval_count_and_static_nodes() {
        // Set(20[0], Add(Add(1, 2), 3)) plus a const-test branch: SCCP must
        // strictly reduce both node count and dynamic evals.
        let mut b = B::default();
        let one = b.int(1);
        let two = b.int(2);
        let add1 = b.pure(Op::Add, vec![one, two]);
        let three = b.int(3);
        let add2 = b.pure(Op::Add, vec![add1, three]);
        let cfg = branch_cfg(b, add2); // test = 6: no case matches, default arm
        let minimal_nodes = compile_cfg(&cfg, Level::Minimal).unwrap();
        let sccp_nodes = compile_cfg_with_pipeline(&cfg, &sccp_pipeline()).unwrap();
        assert!(
            sccp_nodes.arena.len() < minimal_nodes.arena.len(),
            "static nodes must drop: {} -> {}",
            minimal_nodes.arena.len(),
            sccp_nodes.arena.len()
        );
        let memory = crate::diff::build_memory(&cfg, 1);
        let base = run_with_memory(&minimal_nodes, &memory, 1, 100_000);
        let test = run_with_memory(&sccp_nodes, &memory, 1, 100_000);
        assert!(base.result.is_ok() && test.result.is_ok());
        assert!(
            test.eval_count < base.eval_count,
            "eval count must drop: {} -> {}",
            base.eval_count,
            test.eval_count
        );
        assert_diff_match(&cfg);
    }
}
