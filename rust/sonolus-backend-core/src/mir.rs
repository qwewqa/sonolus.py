//! Mid-level IR (MIR): the arena-based SSA-capable IR the optimization pipeline
//! runs on (PORT.md T1.3), plus the IR builder that turns a decoded frontend
//! [`Cfg`] into MIR.
//!
//! # Shape
//!
//! A [`Mir`] is an instruction arena ([`Inst`] addressed by [`Value`]), a temp
//! block table copied from the decoded CFG (table indices are the stable temp
//! identity — never name strings), and a list of basic blocks. Each block has
//! phi instructions (a `Vec<Value>` of [`Inst::Phi`] — we use *phi instructions*
//! rather than block parameters because the legacy pipeline, the Braun
//! construction algorithm, and Boissinot out-of-SSA are all phrased in phis),
//! an **eager schedule** (`insts`: the instructions executed in order when the
//! block runs), and a [`Terminator`] (jump / branch with sorted cases incl.
//! optional default / exit).
//!
//! # Scheduled vs. lazy instructions
//!
//! Most instructions are *scheduled*: they appear in exactly one block's `insts`
//! list and execute at that point. Three kinds are *unscheduled*:
//!
//! - **Constants** are never scheduled; they materialize at each use.
//! - **Phis** live in `block.phis`, not the schedule.
//! - **`ShortCircuit` right operands** (see below) and **`Select` arms**
//!   (the W4 if-conversion product; same lazy-tree contract — see
//!   [`Inst::Select`] and `passes/if_convert.rs`).
//!
//! # Binarization (invariant §3.3: the mid-level IR is strictly binary)
//!
//! The frontend's variadic ops are binarized at IR build:
//!
//! - **Reduce ops** (`Add`, `Subtract`, `Multiply`, `Divide`, `Mod`, `Power`,
//!   `Rem`, `Execute`) fold **left-associatively**, matching the legacy
//!   interpreter's `reduce_args`: `Add(a, b, c)` becomes `Add(Add(a, b), c)`.
//!   Empty arg lists become `ConstInt(0)` (`reduce_args` returns `0.0`); a single
//!   arg becomes the value itself with no op applied (also `reduce_args`).
//!   `reduce_args` evaluates *all* arguments first and then folds, whereas the
//!   binarized form interleaves the fold operations with argument evaluation;
//!   the fold operations are pure value computations (no memory or RNG access),
//!   so the interleaving is observable only through *which* arguments have been
//!   evaluated when a fold step raises (e.g. `Divide` by zero) — a documented
//!   divergence on error paths, which the legacy capture corpus never contains
//!   (vectors record successful runs only).
//!
//! - **`And`/`Or` short-circuit** in the legacy interpreter: arguments evaluate
//!   left to right, evaluation stops at the first zero (`And`) / non-zero (`Or`)
//!   value, and the result is the last value evaluated. They are binarized
//!   **right-associatively** into [`Inst::ShortCircuit`]: `And(a, b, c)` becomes
//!   `And(a, And(b, c))`. Equivalence proof (induction on arity, `And` case;
//!   `Or` is symmetric with the stop condition negated):
//!   - n = 1: the binarized form is the value of `a₁` itself. Legacy `And(a₁)`
//!     evaluates `a₁` and returns it whether or not it is zero. Same effects,
//!     same value.
//!   - n > 1: binary `And(a₁, R)` with `R = And(a₂, …, aₙ)` evaluates `a₁` to
//!     `v₁`. If `v₁ = 0` it returns `v₁` without evaluating `R`; legacy stops at
//!     `a₁` and returns `v₁`, never evaluating `a₂…aₙ`. If `v₁ ≠ 0` the binary
//!     form returns the evaluation of `R`, which by induction equals legacy
//!     `And(a₂, …, aₙ)`; legacy continues its scan from `a₂` in the identical
//!     state. Same effects in the same order, same value.
//!
//!   The crux is that the *second* operand must not be evaluated eagerly: at the
//!   frontend level `And`/`Or` arguments are loads (`IRGet`) or constants
//!   (`Num.and_`/`or_` emit `IRPureInstr(op, [a.ir(), b.ir()])`), and a load may
//!   trap (index asserts) — flattening it into the eager schedule would evaluate
//!   it on paths where the legacy interpreter short-circuits past it. So
//!   `ShortCircuit` is the one place instruction operands are not eagerly
//!   evaluated values: `lhs` is an ordinary (scheduled, in eager context) value;
//!   `rhs` is the root of an **unscheduled expression tree** owned by this
//!   instruction and evaluated only when `lhs` does not short-circuit. The
//!   entire right-nested chain produced by binarizing an n-ary `And`/`Or` lives
//!   in lazy land except the outermost instruction. Instructions inside a lazy
//!   tree may reference only constants and other instructions of the same tree
//!   (single-owner; enforced by the builder, assumed by liveness and lowering).
//!
//! - All other ops keep their encoded arity verbatim (they are fixed-arity at
//!   the frontend; the binary invariant targets the variadic forms). In the
//!   checked-in corpus every variadic op is already binary, so binarization is
//!   an identity transform on real frontend output.
//!
//! Canonical operand ordering for commutative ops is a W1 (GVN) concern; the
//! builder never reorders operands — `minimal` stays a faithful baseline.
//!
//! # Evaluation-order fidelity
//!
//! Flattening preserves the legacy emitter/interpreter evaluation order exactly:
//! statements in order; for `IRSet` the place's nested block place, then nested
//! index place, then the value (matching `Set(block, index, value)`); for
//! `IRGet` block before index; instruction arguments left to right. The test
//! expression of a branching block is flattened after the statements (the legacy
//! emitter evaluates the dispatcher last); unconditional/exit blocks never
//! flatten their test at all (the legacy emitter ignores it, so its side
//! effects — if any — are dropped exactly like legacy).
//!
//! # CFG cleanups (the legacy minimal pipeline's `CoalesceFlow` +
//! `UnreachableCodeElimination`)
//!
//! `build_mir` first runs two cleanups on a lightweight skeleton of the decoded
//! CFG, mirroring the two graph passes the legacy `MINIMAL_PASSES` runs before
//! allocation (in the same order):
//!
//! - **Coalesce** (legacy `CoalesceFlow`): forwards edges through empty
//!   single-successor blocks, removes conditional edges that duplicate the
//!   default edge's target, merges single-successor/single-predecessor chains,
//!   and forwards the incoming edges of empty single-successor blocks.
//!   Like the legacy pass, a block with exactly one outgoing edge is treated as
//!   unconditional *even if that edge carries a cond* (real frontend CFGs
//!   contain empty `{0: target}` blocks that legacy `CoalesceFlow` skips through,
//!   discarding the test; the captured behavioral vectors bake this in, so the
//!   port must reproduce it). Divergence: a cycle of empty blocks would make the
//!   legacy skip loop hang; here a bounded skip count breaks out.
//! - **Unreachable-code elimination** (legacy `UnreachableCodeElimination`):
//!   from the entry, blocks with a constant test take the numerically matching
//!   edge (or the default), dropping the others; unreached blocks are removed.
//!   Divergence: a constant test matching no edge with no default on a block
//!   *with* outgoing edges is a legacy `assert` (compile-time crash); here the
//!   block becomes an exit, which is what the emitted `SwitchWithDefault` would
//!   do at runtime.
//!
//! Both passes only rewire the graph and concatenate statement lists; they never
//! rewrite expressions.
//!
//! # No recursion
//!
//! Every traversal is iterative (explicit work stacks, invariant §3.4):
//! expression trees and nested places are user-sized.

use std::fmt;

use crate::cfg::{BlockValue, Cfg, EdgeCond, IndexValue, Node};
use crate::ops::Op;

/// Index into [`Mir::insts`]; the result value of that instruction.
pub type Value = u32;
/// Index into [`Mir::blocks`]; block 0 is the entry.
pub type BlockId = usize;
/// Index into [`Mir::temps`] — the stable identity of a temp block.
pub type TempId = usize;

/// A temp block definition (name + size), copied from the decoded CFG's table.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TempDef {
    pub name: String,
    pub size: u64,
}

/// The `block` field of a memory place.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum BlockRef {
    /// A concrete runtime block id.
    Concrete(i64),
    /// A temp block (rewritten to block 10000 + offset by allocation/lowering).
    Temp(TempId),
    /// A dynamically computed block id (the value of a `Load` at the frontend
    /// level: nested places decode to chained loads).
    Value(Value),
}

/// The `index` field of a memory place.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum IndexRef {
    /// A constant index.
    Const(i64),
    /// A dynamically computed index.
    Value(Value),
}

/// A memory place: `{block, index, offset}` exactly like the frontend
/// `BlockPlace` (offset stays separate; the emitter folds it).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Place {
    pub block: BlockRef,
    pub index: IndexRef,
    pub offset: i64,
}

/// One instruction. See the module docs for scheduling and binarization rules.
#[derive(Debug, Clone, PartialEq)]
pub enum Inst {
    /// An int-tagged constant (the int/float tag is load-bearing for output).
    ConstInt(i64),
    /// A float-tagged constant.
    ConstFloat(f64),
    /// A fixed-arity op or one binary step of a binarized reduce op.
    /// `pure_node` records whether the frontend node was `IRPureInstr` (vs
    /// `IRInstr`), so lowering regenerates the identical node kind.
    Op {
        op: Op,
        pure_node: bool,
        args: Vec<Value>,
    },
    /// Binary `And`/`Or`. `lhs` is eager; `rhs` is the root of an unscheduled
    /// lazy expression tree owned by this instruction (module docs).
    ShortCircuit {
        op: Op,
        pure_node: bool,
        lhs: Value,
        rhs: Value,
    },
    /// The runtime `If` op as a value (W4 if-conversion, T3.8): evaluates
    /// `test`, then **only** the taken arm (`test != 0.0` — NaN is truthy —
    /// selects `then_root`, else `else_root`), and yields the taken arm's
    /// value. `test` is an ordinary eager operand. Each arm root follows the
    /// `ShortCircuit` rhs contract (the second species of the D11 lazy
    /// boundary): a constant, a scheduled value (legalized by `destruct_ssa`
    /// S5 into an unscheduled class-temp load), or the root of an unscheduled
    /// lazy expression tree owned by this instruction and evaluated iff its
    /// side is taken. The frontend never produces `Select`; only the
    /// if-conversion pass creates it, so `minimal` MIR never contains one.
    Select {
        test: Value,
        then_root: Value,
        else_root: Value,
    },
    /// `IRGet`: read one cell.
    Load { place: Place },
    /// `IRSet`: write one cell. Produces no usable value (statement-only, like
    /// the frontend's `IRSet`).
    Store { place: Place, value: Value },
    /// SSA phi (one entry per predecessor block). Only present between Braun
    /// construction and out-of-SSA; the minimal pipeline never creates one.
    Phi { args: Vec<(BlockId, Value)> },
}

/// A non-default branch condition (the int/float tag of the edge cond is
/// preserved end to end).
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum CaseCond {
    Int(i64),
    Float(f64),
}

impl CaseCond {
    /// The numeric value (for sorting/matching; conds are never NaN — the
    /// decoder enforces it).
    #[allow(clippy::cast_precision_loss)]
    pub fn value(self) -> f64 {
        match self {
            Self::Int(v) => v as f64,
            Self::Float(v) => v,
        }
    }

    pub fn to_edge_cond(self) -> EdgeCond {
        match self {
            Self::Int(v) => EdgeCond::Int(v),
            Self::Float(v) => EdgeCond::Float(v),
        }
    }
}

/// A block terminator.
#[derive(Debug, Clone, PartialEq)]
pub enum Terminator {
    /// Unconditional jump.
    Jump(BlockId),
    /// Multi-way branch on `test`: cases sorted ascending by cond value (the
    /// decoded edge order), then the optional default.
    Branch {
        test: Value,
        cases: Vec<(CaseCond, BlockId)>,
        default: Option<BlockId>,
    },
    /// No successors (falls out of the callback).
    Exit,
}

impl Terminator {
    /// Successor block ids in edge order (cases ascending, then default).
    pub fn successors(&self) -> impl Iterator<Item = BlockId> + '_ {
        let (cases, default): (&[(CaseCond, BlockId)], Option<BlockId>) = match self {
            Self::Jump(t) => (&[], Some(*t)),
            Self::Branch { cases, default, .. } => (cases.as_slice(), *default),
            Self::Exit => (&[], None),
        };
        cases.iter().map(|(_, t)| *t).chain(default)
    }
}

/// A basic block: phis, the eager instruction schedule, and the terminator.
#[derive(Debug, Clone, PartialEq)]
pub struct MirBlock {
    pub phis: Vec<Value>,
    pub insts: Vec<Value>,
    pub terminator: Terminator,
}

impl Default for MirBlock {
    fn default() -> Self {
        Self {
            phis: Vec::new(),
            insts: Vec::new(),
            terminator: Terminator::Exit,
        }
    }
}

/// A whole function in mid-level IR. Block 0 is the entry.
#[derive(Debug, Clone, Default)]
pub struct Mir {
    pub temps: Vec<TempDef>,
    pub insts: Vec<Inst>,
    pub blocks: Vec<MirBlock>,
}

impl Mir {
    pub fn new() -> Self {
        Self::default()
    }

    /// Adds an instruction to the arena (unscheduled) and returns its value.
    pub fn push_inst(&mut self, inst: Inst) -> Value {
        let id = u32::try_from(self.insts.len()).expect("MIR instruction arena overflow");
        self.insts.push(inst);
        id
    }

    pub fn inst(&self, value: Value) -> &Inst {
        &self.insts[value as usize]
    }

    /// Adds a new empty block (exit terminator) and returns its id.
    pub fn push_block(&mut self) -> BlockId {
        self.blocks.push(MirBlock::default());
        self.blocks.len() - 1
    }

    /// Adds a temp block definition and returns its id.
    pub fn push_temp(&mut self, name: impl Into<String>, size: u64) -> TempId {
        self.temps.push(TempDef {
            name: name.into(),
            size,
        });
        self.temps.len() - 1
    }

    /// Predecessor lists (in `(block index, edge order)` order — deterministic).
    pub fn predecessors(&self) -> Vec<Vec<BlockId>> {
        let mut preds: Vec<Vec<BlockId>> = vec![Vec::new(); self.blocks.len()];
        for (id, block) in self.blocks.iter().enumerate() {
            for succ in block.terminator.successors() {
                // One entry per predecessor *block* (like legacy phis, which are
                // keyed by source block): skip duplicates from parallel edges.
                if preds[succ].last() != Some(&id) && !preds[succ].contains(&id) {
                    preds[succ].push(id);
                }
            }
        }
        preds
    }

    /// Which values are scheduled (appear in some block's `insts` or `phis`).
    pub fn scheduled_mask(&self) -> Vec<bool> {
        let mut mask = vec![false; self.insts.len()];
        for block in &self.blocks {
            for &v in block.insts.iter().chain(&block.phis) {
                mask[v as usize] = true;
            }
        }
        mask
    }

    /// Calls `f` for every *immediate* operand value of an instruction
    /// (including the lazy roots — `ShortCircuit` rhs, `Select` arms — and
    /// place block/index values, but not descending into lazy trees).
    pub fn for_each_operand(inst: &Inst, mut f: impl FnMut(Value)) {
        match inst {
            Inst::ConstInt(_) | Inst::ConstFloat(_) => {}
            Inst::Op { args, .. } => {
                for &a in args {
                    f(a);
                }
            }
            Inst::ShortCircuit { lhs, rhs, .. } => {
                f(*lhs);
                f(*rhs);
            }
            Inst::Select {
                test,
                then_root,
                else_root,
            } => {
                f(*test);
                f(*then_root);
                f(*else_root);
            }
            Inst::Load { place } => for_place_operand(place, &mut f),
            Inst::Store { place, value } => {
                for_place_operand(place, &mut f);
                f(*value);
            }
            Inst::Phi { args } => {
                for &(_, a) in args {
                    f(a);
                }
            }
        }
    }

    /// Mutable operand visitor (same coverage as [`Self::for_each_operand`]).
    pub fn for_each_operand_mut(inst: &mut Inst, mut f: impl FnMut(&mut Value)) {
        match inst {
            Inst::ConstInt(_) | Inst::ConstFloat(_) => {}
            Inst::Op { args, .. } => {
                for a in args {
                    f(a);
                }
            }
            Inst::ShortCircuit { lhs, rhs, .. } => {
                f(lhs);
                f(rhs);
            }
            Inst::Select {
                test,
                then_root,
                else_root,
            } => {
                f(test);
                f(then_root);
                f(else_root);
            }
            Inst::Load { place } => for_place_operand_mut(place, &mut f),
            Inst::Store { place, value } => {
                for_place_operand_mut(place, &mut f);
                f(value);
            }
            Inst::Phi { args } => {
                for (_, a) in args {
                    f(a);
                }
            }
        }
    }

    /// Calls `f` for every **lazy root** an instruction owns: the
    /// `ShortCircuit` rhs and both `Select` arms. Nothing for every other
    /// instruction. The shared entry point for the "walk the owned lazy
    /// trees" pattern (lower/coalesce/GVN/DCE use counting, liveness, …);
    /// callers stop their walk at constants and scheduled values exactly as
    /// for a `ShortCircuit` rhs (a scheduled root is an ordinary use the
    /// `destruct_ssa` S5 rule legalizes).
    pub fn for_each_lazy_root(inst: &Inst, mut f: impl FnMut(Value)) {
        match inst {
            Inst::ShortCircuit { rhs, .. } => f(*rhs),
            Inst::Select {
                then_root,
                else_root,
                ..
            } => {
                f(*then_root);
                f(*else_root);
            }
            _ => {}
        }
    }

    /// True if the instruction is a constant.
    pub fn is_const(&self, value: Value) -> bool {
        matches!(self.inst(value), Inst::ConstInt(_) | Inst::ConstFloat(_))
    }

    /// Reverse postorder over reachable blocks from the entry, successors
    /// visited in edge-sorted order (mirrors `encode.py::_reverse_postorder`
    /// and `flow.py`'s traversal). Iterative. Empty for an empty MIR.
    pub fn reverse_postorder(&self) -> Vec<BlockId> {
        if self.blocks.is_empty() {
            return Vec::new();
        }
        let mut visited = vec![false; self.blocks.len()];
        visited[0] = true;
        let mut postorder: Vec<BlockId> = Vec::new();
        // (block, next successor index); successors() is already edge-sorted.
        let mut stack: Vec<(BlockId, usize)> = vec![(0, 0)];
        while let Some(&mut (block, ref mut next)) = stack.last_mut() {
            let succ = self.blocks[block].terminator.successors().nth(*next);
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
}

fn for_place_operand(place: &Place, f: &mut impl FnMut(Value)) {
    if let BlockRef::Value(v) = place.block {
        f(v);
    }
    if let IndexRef::Value(v) = place.index {
        f(v);
    }
}

fn for_place_operand_mut(place: &mut Place, f: &mut impl FnMut(&mut Value)) {
    if let BlockRef::Value(v) = &mut place.block {
        f(v);
    }
    if let IndexRef::Value(v) = &mut place.index {
        f(v);
    }
}

/// An IR-build failure: out-of-domain input the frontend never produces.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MirBuildError {
    /// A control-flow op (`If`, `Switch*`, `While`, `DoWhile`, `Block`,
    /// `JumpLoop`) appeared in an expression. The frontend never emits these
    /// (they are introduced by the legacy emitter / late lowering); carrying
    /// them as eager instructions would mis-evaluate their lazy arguments, so
    /// they are rejected.
    UnsupportedOp(Op),
    /// An `IRSet` appeared in expression position (the decoder already rejects
    /// this; defensive).
    SetInExpression,
}

impl fmt::Display for MirBuildError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::UnsupportedOp(op) => write!(
                f,
                "op {} is not supported in expression position at the frontend level",
                op.name()
            ),
            Self::SetInExpression => write!(f, "IRSet is only valid as a top-level statement"),
        }
    }
}

impl std::error::Error for MirBuildError {}

/// The reduce-style variadic ops (left-fold binarization; module docs).
fn is_reduce_op(op: Op) -> bool {
    matches!(
        op,
        Op::Add
            | Op::Subtract
            | Op::Multiply
            | Op::Divide
            | Op::Mod
            | Op::Power
            | Op::Rem
            | Op::Execute
    )
}

/// Control-flow ops that cannot appear in frontend expressions: everything
/// flagged `control_flow` except `And`/`Or` (binarized lazily) and `Break`
/// (the frontend's return statement; it evaluates both arguments eagerly).
/// `Execute`/`Execute0` are not `control_flow` in the op table; `Execute` is
/// binarized as a reduce op and `Execute0` (never emitted by the frontend)
/// carries through with its encoded arity like any other op.
fn is_rejected_op(op: Op) -> bool {
    op.control_flow() && !matches!(op, Op::And | Op::Or | Op::Break)
}

// ----------------------------------------------------------------------------------
// CFG skeleton + cleanups
// ----------------------------------------------------------------------------------

#[derive(Debug, Clone, Copy)]
struct SkelEdge {
    src: usize,
    dst: usize,
    cond: EdgeCond,
}

#[derive(Debug, Clone, Default)]
struct SkelBlock {
    stmts: Vec<usize>,
    test: usize,
    /// Outgoing edge ids in decoded (sorted) order.
    out: Vec<usize>,
    /// Incoming edge ids (order irrelevant).
    inc: Vec<usize>,
}

#[derive(Debug, Default)]
struct Skeleton {
    blocks: Vec<SkelBlock>,
    edges: Vec<SkelEdge>,
    entry: usize,
}

impl Skeleton {
    fn from_cfg(cfg: &Cfg) -> Self {
        let mut skel = Self {
            blocks: cfg
                .blocks
                .iter()
                .map(|b| SkelBlock {
                    stmts: b.statements.clone(),
                    test: b.test,
                    out: Vec::new(),
                    inc: Vec::new(),
                })
                .collect(),
            edges: Vec::new(),
            entry: 0,
        };
        for (src, block) in cfg.blocks.iter().enumerate() {
            for edge in &block.outgoing {
                let id = skel.edges.len();
                skel.edges.push(SkelEdge {
                    src,
                    dst: edge.target,
                    cond: edge.cond,
                });
                skel.blocks[src].out.push(id);
                skel.blocks[edge.target].inc.push(id);
            }
        }
        skel
    }

    fn retarget(&mut self, edge: usize, new_dst: usize) {
        let old_dst = self.edges[edge].dst;
        self.blocks[old_dst].inc.retain(|&e| e != edge);
        self.edges[edge].dst = new_dst;
        self.blocks[new_dst].inc.push(edge);
    }

    fn remove_edge(&mut self, edge: usize) {
        let SkelEdge { src, dst, .. } = self.edges[edge];
        self.blocks[src].out.retain(|&e| e != edge);
        self.blocks[dst].inc.retain(|&e| e != edge);
    }
}

/// The legacy `CoalesceFlow` pass, ported onto the skeleton (see module docs,
/// including the single-outgoing-edge-is-unconditional fidelity note).
#[allow(clippy::too_many_lines)]
fn coalesce_flow(skel: &mut Skeleton) {
    let mut queue: Vec<usize> = vec![skel.entry];
    let mut processed = vec![false; skel.blocks.len()];
    // Termination: merges strictly reduce the number of live blocks, and only a
    // merge re-queues a processed block, so the outer loop is bounded.
    while let Some(block) = queue.pop() {
        if processed[block] {
            continue;
        }
        processed[block] = true;
        // 1. Skip edges through empty single-successor blocks.
        let out_snapshot = skel.blocks[block].out.clone();
        for edge in out_snapshot {
            // Legacy hangs on a cycle of empty blocks; bound the walk instead.
            let mut budget = skel.blocks.len() + 1;
            loop {
                budget -= 1;
                if budget == 0 {
                    break;
                }
                let dst = skel.edges[edge].dst;
                if !skel.blocks[dst].stmts.is_empty()
                    || skel.blocks[dst].out.len() != 1
                    || dst == block
                    || dst == skel.entry
                {
                    break;
                }
                let next_dst = skel.edges[skel.blocks[dst].out[0]].dst;
                skel.blocks[dst].inc.retain(|&e| e != edge);
                if skel.blocks[dst].inc.is_empty() {
                    // dst became unreachable; unlink its outgoing edges.
                    for &dst_edge in &skel.blocks[dst].out.clone() {
                        let t = skel.edges[dst_edge].dst;
                        skel.blocks[t].inc.retain(|&e| e != dst_edge);
                    }
                    processed[dst] = true;
                }
                skel.edges[edge].dst = next_dst;
                skel.blocks[next_dst].inc.push(edge);
                if dst == next_dst {
                    break;
                }
            }
        }
        // 2. Remove conditional edges whose target equals the default edge's.
        let default_edge = skel.blocks[block]
            .out
            .iter()
            .copied()
            .find(|&e| skel.edges[e].cond == EdgeCond::None);
        if let Some(default_edge) = default_edge {
            let default_dst = skel.edges[default_edge].dst;
            for edge in skel.blocks[block].out.clone() {
                if edge != default_edge && skel.edges[edge].dst == default_dst {
                    self_remove(skel, edge);
                }
            }
        }
        // 3. Merge / forward.
        if skel.blocks[block].out.len() != 1 {
            for &e in &skel.blocks[block].out {
                queue.push(skel.edges[e].dst);
            }
            continue;
        }
        let next = skel.edges[skel.blocks[block].out[0]].dst;
        if next == block || next == skel.entry {
            continue;
        }
        if skel.blocks[next].inc.len() != 1 {
            queue.push(next);
            if skel.blocks[block].stmts.is_empty() {
                // Forward this empty block's incoming edges straight to next.
                for edge in skel.blocks[block].inc.clone() {
                    skel.retarget(edge, next);
                }
                skel.blocks[block].inc.clear();
                let out_edge = skel.blocks[block].out[0];
                skel.blocks[next].inc.retain(|&e| e != out_edge);
                if block == skel.entry {
                    skel.entry = next;
                }
            }
            continue;
        }
        // Merge `next` into `block` (the single connecting edge's cond is
        // discarded — legacy fidelity, see module docs).
        let next_stmts = std::mem::take(&mut skel.blocks[next].stmts);
        skel.blocks[block].stmts.extend(next_stmts);
        skel.blocks[block].test = skel.blocks[next].test;
        let next_out = std::mem::take(&mut skel.blocks[next].out);
        for &e in &next_out {
            skel.edges[e].src = block;
        }
        skel.blocks[block].out = next_out;
        skel.blocks[next].inc.clear();
        processed[next] = true;
        for &e in &skel.blocks[block].out {
            queue.push(skel.edges[e].dst);
        }
        processed[block] = false;
        queue.push(block);
    }
}

fn self_remove(skel: &mut Skeleton, edge: usize) {
    skel.remove_edge(edge);
}

/// The numeric value of a constant test node, if the test is a constant.
fn const_test_value(cfg: &Cfg, test: usize) -> Option<ConstVal> {
    match cfg.nodes[test] {
        Node::ConstInt(v) => Some(ConstVal::Int(v)),
        Node::ConstFloat(v) => Some(ConstVal::Float(v)),
        _ => None,
    }
}

#[derive(Debug, Clone, Copy)]
enum ConstVal {
    Int(i64),
    Float(f64),
}

/// Python `==` between an edge cond and a constant value (exact; no f64
/// rounding of large ints).
#[allow(clippy::float_cmp, clippy::cast_possible_truncation)]
fn cond_matches(cond: EdgeCond, value: ConstVal) -> bool {
    fn int_eq_float(i: i64, f: f64) -> bool {
        // Exact Python int == float: f must be integral, in i64 range, and
        // round-trip to the same i64. (Conds/consts in real CFGs are tiny.)
        f.is_finite() && f == f.trunc() && f >= -9_223_372_036_854_775_808.0 && {
            // 2^63 as f64 is exactly 9223372036854775808.0; any f64 below it fits.
            f < 9_223_372_036_854_775_808.0 && (f as i64) == i
        }
    }
    match (cond, value) {
        (EdgeCond::None, _) => false,
        (EdgeCond::Int(c), ConstVal::Int(v)) => c == v,
        (EdgeCond::Int(c), ConstVal::Float(v)) => int_eq_float(c, v),
        (EdgeCond::Float(c), ConstVal::Int(v)) => int_eq_float(v, c),
        (EdgeCond::Float(c), ConstVal::Float(v)) => c == v,
    }
}

/// The legacy `UnreachableCodeElimination`: constant tests take their matching
/// edge; unreached blocks die. Returns the per-block reachability mask.
fn eliminate_unreachable(skel: &mut Skeleton, cfg: &Cfg) -> Vec<bool> {
    let mut visited = vec![false; skel.blocks.len()];
    let mut worklist = vec![skel.entry];
    while let Some(block) = worklist.pop() {
        if visited[block] {
            continue;
        }
        visited[block] = true;
        if let Some(value) = const_test_value(cfg, skel.blocks[block].test) {
            let taken = skel.blocks[block]
                .out
                .iter()
                .copied()
                .find(|&e| cond_matches(skel.edges[e].cond, value))
                .or_else(|| {
                    skel.blocks[block]
                        .out
                        .iter()
                        .copied()
                        .find(|&e| skel.edges[e].cond == EdgeCond::None)
                });
            // Legacy asserts when edges exist but none is taken; here the block
            // becomes an exit (the runtime default of the emitted switch).
            for edge in skel.blocks[block].out.clone() {
                if Some(edge) != taken {
                    skel.remove_edge(edge);
                }
            }
            if let Some(edge) = taken {
                skel.edges[edge].cond = EdgeCond::None;
                worklist.push(skel.edges[edge].dst);
            }
        } else {
            for &e in &skel.blocks[block].out {
                worklist.push(skel.edges[e].dst);
            }
        }
    }
    visited
}

// ----------------------------------------------------------------------------------
// Expression flattening
// ----------------------------------------------------------------------------------

struct Builder<'a> {
    cfg: &'a Cfg,
    mir: Mir,
    /// The current block's schedule under construction.
    sched: Vec<Value>,
    /// > 0 while flattening a `ShortCircuit` rhs (instructions not scheduled).
    lazy_depth: usize,
}

enum Work {
    Node(usize),
    /// Flatten a place's dynamic components and produce a `Load` value.
    PlaceLoad(usize),
    EnterLazy,
    ExitLazy,
    FinishReduce {
        op: Op,
        pure_node: bool,
        argc: usize,
    },
    FinishShortCircuit {
        op: Op,
        pure_node: bool,
        argc: usize,
    },
    FinishOp {
        op: Op,
        pure_node: bool,
        argc: usize,
    },
    /// Pop the dynamic components of this place and build the `Load`.
    FinishLoad {
        place: usize,
    },
    /// Pop place components and the value; build the `Store` (statement-only).
    FinishStore {
        place: usize,
    },
}

impl Builder<'_> {
    fn push(&mut self, inst: Inst) -> Value {
        let v = self.mir.push_inst(inst);
        if self.lazy_depth == 0 {
            self.sched.push(v);
        }
        v
    }

    fn push_unscheduled(&mut self, inst: Inst) -> Value {
        self.mir.push_inst(inst)
    }

    /// Pops a place's dynamic block/index values from the result stack (pushed
    /// in block-then-index order) and builds the MIR place.
    fn finish_place(&mut self, place: usize, results: &mut Vec<Value>) -> Place {
        let p = &self.cfg.places[place];
        let index = match p.index {
            IndexValue::Int(v) => IndexRef::Const(v),
            IndexValue::Place(_) => IndexRef::Value(results.pop().expect("index value")),
        };
        let block = match p.block {
            BlockValue::Int(v) => BlockRef::Concrete(v),
            BlockValue::Temp(t) => BlockRef::Temp(t),
            BlockValue::Place(_) => BlockRef::Value(results.pop().expect("block value")),
        };
        Place {
            block,
            index,
            offset: p.offset,
        }
    }

    /// Pushes work items that evaluate a place's dynamic components in legacy
    /// order (block first, then index). LIFO stack: push index work first.
    fn push_place_work(&mut self, place: usize, work: &mut Vec<Work>) {
        let p = &self.cfg.places[place];
        if let IndexValue::Place(ip) = p.index {
            work.push(Work::PlaceLoad(ip));
        }
        if let BlockValue::Place(bp) = p.block {
            work.push(Work::PlaceLoad(bp));
        }
    }

    /// Flattens one statement or test expression tree; returns its value.
    /// `IRSet` produces a `Store` and returns its (unusable) value.
    #[allow(clippy::too_many_lines)] // one work-stack state machine
    fn flatten(&mut self, root: usize, allow_set: bool) -> Result<Value, MirBuildError> {
        let mut work: Vec<Work> = vec![Work::Node(root)];
        let mut results: Vec<Value> = Vec::new();
        let mut first = true;
        while let Some(item) = work.pop() {
            let at_root = std::mem::take(&mut first);
            match item {
                Work::EnterLazy => self.lazy_depth += 1,
                Work::ExitLazy => self.lazy_depth -= 1,
                Work::Node(id) => match &self.cfg.nodes[id] {
                    Node::ConstInt(v) => results.push(self.push_unscheduled(Inst::ConstInt(*v))),
                    Node::ConstFloat(v) => {
                        results.push(self.push_unscheduled(Inst::ConstFloat(*v)));
                    }
                    Node::PureInstr { op, args } | Node::Instr { op, args } => {
                        let op = *op;
                        if is_rejected_op(op) {
                            return Err(MirBuildError::UnsupportedOp(op));
                        }
                        let pure_node = matches!(&self.cfg.nodes[id], Node::PureInstr { .. });
                        let argc = args.len();
                        if matches!(op, Op::And | Op::Or) {
                            work.push(Work::FinishShortCircuit {
                                op,
                                pure_node,
                                argc,
                            });
                            // First arg evaluates in the current context; the
                            // rest are lazy. LIFO: push in reverse.
                            for (i, &arg) in args.iter().enumerate().rev() {
                                if i > 0 {
                                    work.push(Work::ExitLazy);
                                    work.push(Work::Node(arg));
                                    work.push(Work::EnterLazy);
                                } else {
                                    work.push(Work::Node(arg));
                                }
                            }
                        } else if is_reduce_op(op) {
                            work.push(Work::FinishReduce {
                                op,
                                pure_node,
                                argc,
                            });
                            for &arg in args.iter().rev() {
                                work.push(Work::Node(arg));
                            }
                        } else {
                            work.push(Work::FinishOp {
                                op,
                                pure_node,
                                argc,
                            });
                            for &arg in args.iter().rev() {
                                work.push(Work::Node(arg));
                            }
                        }
                    }
                    Node::Get(place) => work.push(Work::PlaceLoad(*place)),
                    Node::Set { place, value } => {
                        if !(allow_set && at_root) {
                            return Err(MirBuildError::SetInExpression);
                        }
                        // Legacy Set evaluation order: block, index, value.
                        work.push(Work::FinishStore { place: *place });
                        work.push(Work::Node(*value));
                        self.push_place_work(*place, &mut work);
                    }
                },
                Work::PlaceLoad(place) => {
                    work.push(Work::FinishLoad { place });
                    self.push_place_work(place, &mut work);
                }
                Work::FinishLoad { place } => {
                    let place = self.finish_place(place, &mut results);
                    let v = self.push(Inst::Load { place });
                    results.push(v);
                }
                Work::FinishStore { place } => {
                    let value = results.pop().expect("store value");
                    let place = self.finish_place(place, &mut results);
                    let v = self.push(Inst::Store { place, value });
                    results.push(v);
                }
                Work::FinishOp {
                    op,
                    pure_node,
                    argc,
                } => {
                    let start = results.len() - argc;
                    let args = results.split_off(start);
                    let v = self.push(Inst::Op {
                        op,
                        pure_node,
                        args,
                    });
                    results.push(v);
                }
                Work::FinishReduce {
                    op,
                    pure_node,
                    argc,
                } => {
                    let start = results.len() - argc;
                    let args = results.split_off(start);
                    let v = match args.as_slice() {
                        [] => self.push_unscheduled(Inst::ConstInt(0)),
                        [single] => *single,
                        [first_arg, rest @ ..] => {
                            let mut acc = *first_arg;
                            for &arg in rest {
                                acc = self.push(Inst::Op {
                                    op,
                                    pure_node,
                                    args: vec![acc, arg],
                                });
                            }
                            acc
                        }
                    };
                    results.push(v);
                }
                Work::FinishShortCircuit {
                    op,
                    pure_node,
                    argc,
                } => {
                    let start = results.len() - argc;
                    let args = results.split_off(start);
                    let v = match args.as_slice() {
                        [] => self.push_unscheduled(Inst::ConstInt(0)),
                        [single] => *single,
                        [first_arg, rest @ ..] => {
                            // Right-fold: inner chain instructions are lazy
                            // (owned by the next level out); only the outermost
                            // is created in the current context.
                            let mut rhs = *rest.last().expect("rest is non-empty");
                            for &arg in rest[..rest.len() - 1].iter().rev() {
                                rhs = self.push_unscheduled(Inst::ShortCircuit {
                                    op,
                                    pure_node,
                                    lhs: arg,
                                    rhs,
                                });
                            }
                            self.push(Inst::ShortCircuit {
                                op,
                                pure_node,
                                lhs: *first_arg,
                                rhs,
                            })
                        }
                    };
                    results.push(v);
                }
            }
        }
        debug_assert_eq!(results.len(), 1, "flattening yields exactly one value");
        Ok(results.pop().expect("flattening yields exactly one value"))
    }
}

/// Builds MIR from a decoded frontend CFG: cleanups (`CoalesceFlow` + UCE
/// equivalents), then statement-by-statement flattening with binarization.
pub fn build_mir(cfg: &Cfg) -> Result<Mir, MirBuildError> {
    let mut skel = Skeleton::from_cfg(cfg);
    if !skel.blocks.is_empty() {
        coalesce_flow(&mut skel);
    }
    let alive = if skel.blocks.is_empty() {
        Vec::new()
    } else {
        eliminate_unreachable(&mut skel, cfg)
    };

    // Map alive skeleton blocks to MIR ids: entry first, the rest in decoded
    // order (deterministic; final ordering is re-derived as RPO at lowering).
    let mut block_map: Vec<Option<BlockId>> = vec![None; skel.blocks.len()];
    let mut order: Vec<usize> = Vec::new();
    if !skel.blocks.is_empty() {
        order.push(skel.entry);
        for (i, &is_alive) in alive.iter().enumerate() {
            if is_alive && i != skel.entry {
                order.push(i);
            }
        }
    }
    let mut builder = Builder {
        cfg,
        mir: Mir {
            temps: cfg
                .temp_blocks
                .iter()
                .map(|t| TempDef {
                    name: cfg.strings[t.name].clone(),
                    size: t.size,
                })
                .collect(),
            insts: Vec::new(),
            blocks: Vec::new(),
        },
        sched: Vec::new(),
        lazy_depth: 0,
    };
    for (mir_id, &skel_id) in order.iter().enumerate() {
        block_map[skel_id] = Some(mir_id);
        builder.mir.blocks.push(MirBlock::default());
    }
    for &skel_id in &order {
        let mir_id = block_map[skel_id].expect("alive block was mapped");
        builder.sched = Vec::new();
        debug_assert_eq!(builder.lazy_depth, 0);
        for stmt in skel.blocks[skel_id].stmts.clone() {
            builder.flatten(stmt, true)?;
        }
        // Terminator (and the test, only when something branches on it).
        let out = skel.blocks[skel_id].out.clone();
        let target = |e: usize| block_map[skel.edges[e].dst].expect("edge target is alive");
        let terminator = match out.as_slice() {
            [] => Terminator::Exit,
            [e] if skel.edges[*e].cond == EdgeCond::None => Terminator::Jump(target(*e)),
            edges => {
                let test = builder.flatten(skel.blocks[skel_id].test, false)?;
                let mut cases = Vec::new();
                let mut default = None;
                for &e in edges {
                    match skel.edges[e].cond {
                        EdgeCond::None => default = Some(target(e)),
                        EdgeCond::Int(v) => cases.push((CaseCond::Int(v), target(e))),
                        EdgeCond::Float(v) => cases.push((CaseCond::Float(v), target(e))),
                    }
                }
                Terminator::Branch {
                    test,
                    cases,
                    default,
                }
            }
        };
        let block = &mut builder.mir.blocks[mir_id];
        block.insts = std::mem::take(&mut builder.sched);
        block.terminator = terminator;
    }
    Ok(builder.mir)
}

#[cfg(test)]
mod tests {
    // Toolchain note: clippy 1.96 newly lints test code under --all-targets;
    // terse local names are the test-builder convention in this module.
    // test constants are tiny; the casts cannot truncate/wrap in practice.
    #![allow(
        clippy::many_single_char_names,
        clippy::cast_possible_truncation,
        clippy::cast_sign_loss
    )]
    use super::*;
    use crate::cfg::{BasicBlock, Edge, Place as CfgPlace, TempBlockDef};

    /// Hand-built decoded-CFG builder (mirrors the one in emit.rs tests).
    #[derive(Default)]
    pub(crate) struct CfgBuilder {
        pub cfg: Cfg,
    }

    impl CfgBuilder {
        pub fn node(&mut self, node: Node) -> usize {
            self.cfg.nodes.push(node);
            self.cfg.nodes.len() - 1
        }

        pub fn temp(&mut self, name: &str, size: u64) -> usize {
            self.cfg.strings.push(name.to_owned());
            self.cfg.temp_blocks.push(TempBlockDef {
                name: self.cfg.strings.len() - 1,
                size,
            });
            self.cfg.temp_blocks.len() - 1
        }

        pub fn place(&mut self, block: BlockValue, index: IndexValue, offset: i64) -> usize {
            self.cfg.places.push(CfgPlace {
                block,
                index,
                offset,
            });
            self.cfg.places.len() - 1
        }

        pub fn temp_place(&mut self, temp: usize) -> usize {
            self.place(BlockValue::Temp(temp), IndexValue::Int(0), 0)
        }

        pub fn get(&mut self, place: usize) -> usize {
            self.node(Node::Get(place))
        }

        pub fn set(&mut self, place: usize, value: usize) -> usize {
            self.node(Node::Set { place, value })
        }

        pub fn const_int(&mut self, v: i64) -> usize {
            self.node(Node::ConstInt(v))
        }

        pub fn pure_instr(&mut self, op: Op, args: Vec<usize>) -> usize {
            self.node(Node::PureInstr { op, args })
        }

        pub fn instr(&mut self, op: Op, args: Vec<usize>) -> usize {
            self.node(Node::Instr { op, args })
        }

        pub fn block(&mut self, statements: Vec<usize>, test: usize, outgoing: Vec<Edge>) {
            self.cfg.blocks.push(BasicBlock {
                statements,
                test,
                outgoing,
            });
        }

        pub fn zero_test(&mut self) -> usize {
            self.const_int(0)
        }
    }

    pub(crate) fn edge(cond: EdgeCond, target: usize) -> Edge {
        Edge { cond, target }
    }

    fn sched_insts(mir: &Mir, block: BlockId) -> Vec<&Inst> {
        mir.blocks[block]
            .insts
            .iter()
            .map(|&v| mir.inst(v))
            .collect()
    }

    #[test]
    fn reduce_ops_binarize_left_associatively() {
        // Set(t0, Add(1, 2, 3, 4)) -> Add(Add(Add(1,2),3),4), then the store.
        let mut b = CfgBuilder::default();
        let t = b.temp("t0", 1);
        let p = b.temp_place(t);
        let args: Vec<usize> = (1..=4).map(|i| b.const_int(i)).collect();
        let add = b.pure_instr(Op::Add, args);
        let s = b.set(p, add);
        let zt = b.zero_test();
        b.block(vec![s], zt, vec![]);
        let mir = build_mir(&b.cfg).unwrap();
        let insts = sched_insts(&mir, 0);
        // Scheduled: three binary Adds then the Store (consts are unscheduled).
        assert_eq!(insts.len(), 4);
        for inst in &insts[..3] {
            let Inst::Op {
                op: Op::Add, args, ..
            } = inst
            else {
                panic!("expected binary Add, got {inst:?}");
            };
            assert_eq!(args.len(), 2);
        }
        // Left-fold: each Add's lhs is the previous Add (except the first).
        let adds: Vec<Value> = mir.blocks[0].insts[..3].to_vec();
        for (i, &v) in adds.iter().enumerate().skip(1) {
            let Inst::Op { args, .. } = mir.inst(v) else {
                unreachable!()
            };
            assert_eq!(args[0], adds[i - 1], "left-fold chains through arg 0");
        }
        assert!(matches!(insts[3], Inst::Store { .. }));
    }

    #[test]
    fn reduce_empty_and_single_arg_forms() {
        // Set(t0, Add()) -> store of const 0; Set(t1, Mul(x)) -> store of x itself.
        let mut b = CfgBuilder::default();
        let t0 = b.temp("t0", 1);
        let t1 = b.temp("t1", 1);
        let p0 = b.temp_place(t0);
        let p1 = b.temp_place(t1);
        let empty_add = b.pure_instr(Op::Add, vec![]);
        let s0 = b.set(p0, empty_add);
        let x = b.get(p0);
        let single_mul = b.pure_instr(Op::Multiply, vec![x]);
        let s1 = b.set(p1, single_mul);
        let zt = b.zero_test();
        b.block(vec![s0, s1], zt, vec![]);
        let mir = build_mir(&b.cfg).unwrap();
        let insts = sched_insts(&mir, 0);
        // store(const 0); load t0; store t1 <- that load. No Add/Multiply insts.
        assert_eq!(insts.len(), 3);
        let Inst::Store { value, .. } = insts[0] else {
            panic!("expected store");
        };
        assert_eq!(mir.inst(*value), &Inst::ConstInt(0));
        assert!(matches!(insts[1], Inst::Load { .. }));
        let Inst::Store { value, .. } = insts[2] else {
            panic!("expected store");
        };
        assert!(matches!(mir.inst(*value), Inst::Load { .. }));
    }

    #[test]
    fn and_binarizes_right_associatively_with_lazy_tail() {
        // Set(t, And(a, b, c)): only the outermost And is scheduled; the inner
        // And and the b/c loads are lazy.
        let mut b = CfgBuilder::default();
        let t = b.temp("t", 1);
        let ta = b.temp("a", 1);
        let tb = b.temp("b", 1);
        let tc = b.temp("c", 1);
        let pt = b.temp_place(t);
        let pa = b.temp_place(ta);
        let pb = b.temp_place(tb);
        let pc = b.temp_place(tc);
        let (ga, gb, gc) = (b.get(pa), b.get(pb), b.get(pc));
        let and = b.pure_instr(Op::And, vec![ga, gb, gc]);
        let s = b.set(pt, and);
        let zt = b.zero_test();
        b.block(vec![s], zt, vec![]);
        let mir = build_mir(&b.cfg).unwrap();
        let insts = sched_insts(&mir, 0);
        // Scheduled: load a, outer And, store. Loads of b/c and the inner And
        // are unscheduled (lazy).
        assert_eq!(insts.len(), 3);
        assert!(matches!(insts[0], Inst::Load { .. }));
        let Inst::ShortCircuit {
            op: Op::And,
            lhs,
            rhs,
            ..
        } = insts[1]
        else {
            panic!("expected scheduled ShortCircuit, got {:?}", insts[1]);
        };
        assert_eq!(*lhs, mir.blocks[0].insts[0]);
        let Inst::ShortCircuit {
            op: Op::And,
            lhs: inner_lhs,
            rhs: inner_rhs,
            ..
        } = mir.inst(*rhs)
        else {
            panic!("rhs must be the inner And");
        };
        assert!(matches!(mir.inst(*inner_lhs), Inst::Load { .. }));
        assert!(matches!(mir.inst(*inner_rhs), Inst::Load { .. }));
        // The lazy instructions are not scheduled anywhere.
        let mask = mir.scheduled_mask();
        assert!(!mask[*rhs as usize]);
        assert!(!mask[*inner_lhs as usize]);
        assert!(!mask[*inner_rhs as usize]);
        assert!(matches!(insts[2], Inst::Store { .. }));
    }

    #[test]
    fn nested_places_flatten_block_then_index() {
        // Get(BlockPlace(block=BlockPlace(20,1), index=BlockPlace(21,2), offset=3))
        // must schedule: load block place, load index place, outer load.
        let mut b = CfgBuilder::default();
        let bp = b.place(BlockValue::Int(20), IndexValue::Int(1), 0);
        let ip = b.place(BlockValue::Int(21), IndexValue::Int(2), 0);
        let outer = b.place(BlockValue::Place(bp), IndexValue::Place(ip), 3);
        let g = b.get(outer);
        let zt = b.zero_test();
        b.block(vec![g], zt, vec![]);
        let mir = build_mir(&b.cfg).unwrap();
        let insts = sched_insts(&mir, 0);
        assert_eq!(insts.len(), 3);
        let Inst::Load { place: p0 } = insts[0] else {
            panic!()
        };
        assert_eq!(p0.block, BlockRef::Concrete(20));
        let Inst::Load { place: p1 } = insts[1] else {
            panic!()
        };
        assert_eq!(p1.block, BlockRef::Concrete(21));
        let Inst::Load { place: p2 } = insts[2] else {
            panic!()
        };
        assert_eq!(
            (p2.block, p2.index, p2.offset),
            (
                BlockRef::Value(mir.blocks[0].insts[0]),
                IndexRef::Value(mir.blocks[0].insts[1]),
                3
            )
        );
    }

    #[test]
    fn store_evaluates_place_components_before_value() {
        // Set(place(block=nested, index=nested), value=Get(...)): order is
        // block load, index load, value load, store.
        let mut b = CfgBuilder::default();
        let bp = b.place(BlockValue::Int(20), IndexValue::Int(0), 0);
        let ip = b.place(BlockValue::Int(21), IndexValue::Int(0), 0);
        let vp = b.place(BlockValue::Int(22), IndexValue::Int(0), 0);
        let target = b.place(BlockValue::Place(bp), IndexValue::Place(ip), 0);
        let value = b.get(vp);
        let s = b.set(target, value);
        let zt = b.zero_test();
        b.block(vec![s], zt, vec![]);
        let mir = build_mir(&b.cfg).unwrap();
        let insts = sched_insts(&mir, 0);
        assert_eq!(insts.len(), 4);
        let blocks: Vec<i64> = insts[..3]
            .iter()
            .map(|i| {
                let Inst::Load { place } = i else { panic!() };
                let BlockRef::Concrete(c) = place.block else {
                    panic!()
                };
                c
            })
            .collect();
        assert_eq!(blocks, vec![20, 21, 22]);
        assert!(matches!(insts[3], Inst::Store { .. }));
    }

    #[test]
    fn control_flow_ops_in_expressions_are_rejected() {
        for op in [
            Op::If,
            Op::Switch,
            Op::SwitchInteger,
            Op::SwitchIntegerWithDefault,
            Op::SwitchWithDefault,
            Op::While,
            Op::DoWhile,
            Op::Block,
            Op::JumpLoop,
        ] {
            let mut b = CfgBuilder::default();
            let c = b.const_int(1);
            let i = b.instr(op, vec![c]);
            let zt = b.zero_test();
            b.block(vec![i], zt, vec![]);
            assert_eq!(
                build_mir(&b.cfg).err(),
                Some(MirBuildError::UnsupportedOp(op))
            );
        }
        // Break is allowed (the frontend's return statement).
        let mut b = CfgBuilder::default();
        let one = b.const_int(1);
        let val = b.const_int(7);
        let brk = b.instr(Op::Break, vec![one, val]);
        let zt = b.zero_test();
        b.block(vec![brk], zt, vec![]);
        assert!(build_mir(&b.cfg).is_ok());
    }

    #[test]
    fn unconditional_blocks_do_not_flatten_their_test() {
        // A side-effect-free Get test on a single-edge block is dropped, like
        // the legacy emitter which never evaluates it.
        let mut b = CfgBuilder::default();
        let tp = b.place(BlockValue::Int(21), IndexValue::Int(0), 0);
        let test0 = b.get(tp);
        b.block(vec![], test0, vec![edge(EdgeCond::None, 1)]);
        let zt = b.zero_test();
        b.block(vec![], zt, vec![]);
        let mir = build_mir(&b.cfg).unwrap();
        // CoalesceFlow forwards the entry to the exit block... entry has no
        // statements and one outgoing edge to a block with one incoming, so
        // they merge into a single exit block with no instructions.
        assert_eq!(mir.blocks.len(), 1);
        assert!(mir.blocks[0].insts.is_empty());
        assert_eq!(mir.blocks[0].terminator, Terminator::Exit);
    }

    #[test]
    fn const_test_branches_fold() {
        // Entry: test = 1, edges {0: b1, 1: b2, None: b3} -> only b2 survives.
        let mut b = CfgBuilder::default();
        let t = b.temp("x", 1);
        let p = b.temp_place(t);
        let one = b.const_int(1);
        b.block(
            vec![],
            one,
            vec![
                edge(EdgeCond::Int(0), 1),
                edge(EdgeCond::Int(1), 2),
                edge(EdgeCond::None, 3),
            ],
        );
        for v in [10, 20, 30] {
            let c = b.const_int(v);
            let s = b.set(p, c);
            let zt = b.zero_test();
            b.block(vec![s], zt, vec![]);
        }
        let mir = build_mir(&b.cfg).unwrap();
        // Entry jumps (merged or not) to exactly the v=20 store; others dead.
        let mut store_values = Vec::new();
        for block in &mir.blocks {
            for &v in &block.insts {
                if let Inst::Store { value, .. } = mir.inst(v) {
                    store_values.push(mir.inst(*value).clone());
                }
            }
        }
        assert_eq!(store_values, vec![Inst::ConstInt(20)]);
    }

    #[test]
    fn float_const_test_matches_int_cond() {
        // test = 1.0 must take the {1: ...} edge (Python numeric equality).
        let mut b = CfgBuilder::default();
        let t = b.temp("x", 1);
        let p = b.temp_place(t);
        let test = b.node(Node::ConstFloat(1.0));
        b.block(
            vec![],
            test,
            vec![edge(EdgeCond::Int(1), 1), edge(EdgeCond::None, 2)],
        );
        let c1 = b.const_int(111);
        let s1 = b.set(p, c1);
        let zt1 = b.zero_test();
        b.block(vec![s1], zt1, vec![]);
        let c2 = b.const_int(222);
        let s2 = b.set(p, c2);
        let zt2 = b.zero_test();
        b.block(vec![s2], zt2, vec![]);
        let mir = build_mir(&b.cfg).unwrap();
        let mut store_values = Vec::new();
        for block in &mir.blocks {
            for &v in &block.insts {
                if let Inst::Store { value, .. } = mir.inst(v) {
                    store_values.push(mir.inst(*value).clone());
                }
            }
        }
        assert_eq!(store_values, vec![Inst::ConstInt(111)]);
    }

    #[test]
    fn empty_single_cond_edge_blocks_are_skipped_like_legacy() {
        // Entry branches to an empty block with a single {0: target} edge;
        // legacy CoalesceFlow forwards straight through it, discarding the
        // test. The result reaches `target` unconditionally.
        let mut b = CfgBuilder::default();
        let t = b.temp("x", 1);
        let scrutinee = b.temp("s", 1);
        let p = b.temp_place(t);
        let sp = b.temp_place(scrutinee);
        let test0 = b.get(sp);
        b.block(
            vec![],
            test0,
            vec![edge(EdgeCond::Int(0), 1), edge(EdgeCond::None, 2)],
        );
        // Block 1: empty, single conditional edge {0: 3}.
        let test1 = b.get(sp);
        b.block(vec![], test1, vec![edge(EdgeCond::Int(0), 3)]);
        // Block 2: a store, then exit via block 3.
        let c2 = b.const_int(2);
        let s2 = b.set(p, c2);
        let zt2 = b.zero_test();
        b.block(vec![s2], zt2, vec![edge(EdgeCond::None, 3)]);
        // Block 3: exit with a store.
        let c3 = b.const_int(3);
        let s3 = b.set(p, c3);
        let zt3 = b.zero_test();
        b.block(vec![s3], zt3, vec![]);
        let mir = build_mir(&b.cfg).unwrap();
        // The entry's {0: ...} edge must point at the block holding store 3
        // (skipping block 1 entirely); no block has block 1's branch-on-s shape
        // with a single case.
        let Terminator::Branch { cases, default, .. } = &mir.blocks[0].terminator else {
            panic!("entry must still branch");
        };
        assert_eq!(cases.len(), 1);
        assert!(default.is_some());
        let zero_target = cases[0].1;
        let has_store3 = mir.blocks[zero_target].insts.iter().any(|&v| {
            matches!(mir.inst(v), Inst::Store { value, .. }
                if mir.inst(*value) == &Inst::ConstInt(3))
        });
        assert!(has_store3, "the {{0: ...}} edge skips to the store-3 block");
    }

    #[test]
    fn duplicate_default_cond_edges_are_removed() {
        // Edges {0: t, None: t} collapse to an unconditional edge; the test is
        // never flattened (its side effects drop, like legacy).
        let mut b = CfgBuilder::default();
        let sp = b.place(BlockValue::Int(21), IndexValue::Int(0), 0);
        let test = b.get(sp);
        b.block(
            vec![],
            test,
            vec![edge(EdgeCond::Int(0), 1), edge(EdgeCond::None, 1)],
        );
        let t = b.temp("x", 1);
        let p = b.temp_place(t);
        let c = b.const_int(5);
        let s = b.set(p, c);
        let zt = b.zero_test();
        b.block(vec![s], zt, vec![]);
        let mir = build_mir(&b.cfg).unwrap();
        // After cond-dedup the entry has one edge and merges with the store
        // block: a single block, no Load of the test place.
        assert_eq!(mir.blocks.len(), 1);
        assert!(
            mir.blocks[0]
                .insts
                .iter()
                .all(|&v| !matches!(mir.inst(v), Inst::Load { .. })),
            "the dropped test must not be flattened"
        );
    }

    #[test]
    fn straight_line_chains_merge() {
        let mut b = CfgBuilder::default();
        let t = b.temp("x", 1);
        let p = b.temp_place(t);
        for i in 0..3 {
            let c = b.const_int(i);
            let s = b.set(p, c);
            let zt = b.zero_test();
            let edges = if i < 2 {
                vec![edge(EdgeCond::None, (i + 1) as usize)]
            } else {
                vec![]
            };
            b.block(vec![s], zt, edges);
        }
        let mir = build_mir(&b.cfg).unwrap();
        assert_eq!(mir.blocks.len(), 1);
        assert_eq!(mir.blocks[0].insts.len(), 3);
        assert_eq!(mir.blocks[0].terminator, Terminator::Exit);
    }

    #[test]
    fn deep_expression_nesting_is_iterative() {
        // 150k-deep Negate chain through build_mir: no thread-stack recursion.
        let mut b = CfgBuilder::default();
        let t = b.temp("x", 1);
        let p = b.temp_place(t);
        let mut node = b.const_int(7);
        for _ in 0..150_000 {
            node = b.pure_instr(Op::Negate, vec![node]);
        }
        let s = b.set(p, node);
        let zt = b.zero_test();
        b.block(vec![s], zt, vec![]);
        let mir = build_mir(&b.cfg).unwrap();
        assert_eq!(mir.blocks[0].insts.len(), 150_001);
    }

    #[test]
    fn deep_and_nesting_is_iterative() {
        // 100k-deep right-leaning And tree (each rhs is the next And): the lazy
        // flattening must also be iterative.
        let mut b = CfgBuilder::default();
        let t = b.temp("x", 1);
        let p = b.temp_place(t);
        let mut node = b.const_int(1);
        for _ in 0..100_000 {
            let one = b.const_int(1);
            node = b.pure_instr(Op::And, vec![one, node]);
        }
        let s = b.set(p, node);
        let zt = b.zero_test();
        b.block(vec![s], zt, vec![]);
        let mir = build_mir(&b.cfg).unwrap();
        // Only the outermost And and the store are scheduled.
        assert_eq!(mir.blocks[0].insts.len(), 2);
    }
}
