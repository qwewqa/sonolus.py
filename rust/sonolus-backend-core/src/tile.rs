//! Emission-time fused-op tiling (PORT.md T3.12, wave W5).
//!
//! The target runtime's op set contains fused forms (`IncrementPre`,
//! `IncrementPost`, `GetShifted`, `SetShifted`, `Lerp`, `Clamp`, `Unlerp`,
//! ...) that evaluate a whole multi-node expression tree as ONE node. Tiling
//! replaces an emitted tree whose evaluation is *bit-identical and
//! observably identical* to a fused kernel with the single fused node,
//! directly removing dynamic node evaluations (the priority-one metric).
//!
//! # Placement
//!
//! Like [`crate::flatten`] (T3.10), this transform exists only in the
//! lowering→emission seam: it consumes the [`crate::emit`] output and runs
//! **before** `flatten_engine_nodes` (tiles match the pre-flattened binary
//! emitted form; GVN's consts-first canonical order means const-operand
//! patterns appear as `Op(c, expr)`). Enabled per-pipeline (`standard` only,
//! [`crate::passes::Pipeline::tile_at_emit`]): `minimal` stays the
//! trivially-correct differential baseline, `fast` the plain W1 prefix.
//! Mid-level MIR never sees fused forms (invariant §3.3 analogue).
//!
//! # Exactness contract (hard; PORT.md T3.12)
//!
//! A tile must produce **bit-identical results and identical observable
//! behavior** (results, logs, writes, RNG draw count, error identity and
//! trap point) to the tree it replaces, per the interpreter kernels in
//! [`crate::interpret`] (the semantic oracle, equal to the legacy Python
//! interpreter). Every rule below is justified against the kernel's exact
//! evaluation order, including `ensure_int` interleaving and the mutating
//! `get`/`set` bounds asserts. Structural identity between two subtrees is
//! **bit-strict recursive structural equality** (constants compare by f64
//! bits, so `-0.0` ≠ `0.0`; int/float tags are ignored — they are
//! output-only and never affect evaluation). This is sound for duplicate
//! elimination because a structurally identical pure tree re-evaluated with
//! no intervening writes/RNG recomputes the identical bits; node identity in
//! the arena is not required (the emitted arena is a strict tree anyway).
//!
//! Classification used by the rules, all defined over emitted trees:
//!
//! - **pure**: every node in the subtree is effect-free per [`Op::pure`] plus
//!   the effect-free-but-impure reads (`Get`/`GetShifted`/`GetPointed`):
//!   no writes, no logs, no RNG, no runtime-only ops. A pure tree evaluated
//!   twice with no intervening writes yields identical bits and identical
//!   trap behavior, and evaluating it is unobservable.
//! - **total**: pure AND every op is on the never-trapping whitelist
//!   ([`node_is_total`], mirroring DCE's `op_is_total`) AND constants are
//!   finite (NaN/inf literals cannot reach emission, but the check is
//!   defensive). Reordering a total tree's evaluation relative to other pure
//!   evaluations is unobservable.
//! - **provably integral** ([`node_is_integral`]): the value is statically
//!   known to satisfy `ensure_int` whenever evaluation completes
//!   (int-tagged integral constants; `Floor`/`Ceil`/`Round`/`Trunc`, which
//!   return integral values or trap; comparison ops and `Not`, which return
//!   `0.0`/`1.0`). Used where a fused kernel `ensure_int`s argument values
//!   that the original tree only checked in aggregate (or later).
//!
//! # The tile set (each measured on corpus + pydori before adoption;
//! PORT.md T3.12 worklog has the census table)
//!
//! - **`IncrementPre(B, I)`** ⇐ `Set(B, I, Add(1, Get(B, I)))` (either `Add`
//!   operand order; the const-first form is what GVN emits). Census: 719
//!   pydori / 159 corpus sites, mostly loop counters in hot bodies; each
//!   saves 5 evaluations per execution (`Set+B+I+Add+1+Get+B+I` → 8 nodes
//!   becomes `IncrementPre+B+I` → 3). Kernel
//!   (`(2, true)` interleaved `ensure_int`): eval B, `ensure_int`, eval I,
//!   `ensure_int`, `v = get(B,I) + 1`, `set(B,I,v)`, return `v`. The tree:
//!   eval B, I, then `1`, then the inner `Get` re-evaluates B, I (each
//!   `ensure_int`ed there), reads, folds `1 + v` (≡ `v + 1` bitwise — at
//!   most one operand can be NaN), then `Set` re-`ensure_int`s and writes,
//!   returning the written value. Both compute and return `get(B,I) + 1.0`
//!   and perform the identical single write. Conditions:
//!   - B and I **pure** (deduplicated: evaluated twice in the tree, once by
//!     the kernel; nothing between the duplicate evaluations writes or
//!     draws, so pure recomputation is bit-identical) and **RNG-free** (RNG
//!     is never moved/duplicated/eliminated — implied by pure).
//!   - B **provably integral** OR I **total**: the only evaluation-order
//!     difference is that the kernel runs `ensure_int(B)` *before*
//!     evaluating I, while the tree first traps on a non-integral B inside
//!     the inner `Get`, *after* I's evaluation. If B cannot fail the check,
//!     or I cannot trap (its evaluation is the only observable in between —
//!     pure already), the trap sequences coincide:
//!     tree `[B, I, eint B, eint I, bounds]` ≡ kernel
//!     `[B, eint B, I, eint I, bounds]`. Every later step is identical.
//!   - The `1` may be int- or float-tagged (value `1.0`); the tile drops it
//!     (constant evaluation is unobservable). No node changes tags.
//! - **`Set(T, J, IncrementPost(B, I))`** ⇐ the adjacent `Execute`/
//!   `Execute0` statement pair
//!   `Set(T, J, Get(B, I)); Set(B, I, Add(1, Get(B, I)))` (the lowered
//!   post-increment idiom: save the old value, then bump). Census: 15
//!   pydori / 11 corpus pairs; each saves 8 evaluations per execution
//!   (14 nodes → 6). Kernel
//!   `IncrementPost`: eval B, `ensure_int`, eval I, `ensure_int`, read old,
//!   write old+1, return old; the wrapping `Set` then writes old into
//!   `T[J]`. Conditions:
//!   - T, J, B, I all **pure**; B, I deduplicated as above.
//!   - T, J, B, I all **constants** with `(T,J) ≠ (B,I)` as an exact value
//!     pair: the two writes swap order under the tile, which is observable
//!     if they alias (final memory) — constant disjointness proves they
//!     cannot. (Census: every real occurrence is constant-place anyway.)
//!   - T integral, J integral and in `0..=65535`: the original form runs
//!     `ensure_int(T)`/`ensure_int(J)`/`J`-bounds *before its first write*,
//!     the tile runs them *between* the (swapped) writes — they must
//!     provably never fire, or a trap there would observe `B[I]` already
//!     bumped. The B/I checks need no such guard: their trap positions
//!     coincide (both forms run them before any write, after the same pure
//!     constant evaluations), so non-integral or out-of-range B/I traps
//!     identically on both sides.
//!   - The pair's *second* `Set` must sit in a **value-discarded position**
//!     (non-last argument of `Execute`, any argument of `Execute0`): the
//!     merged node returns `old` where the original second `Set` returned
//!     `old + 1`. (The first `Set`'s value was discarded by position
//!     already; the merged node occupies its slot.)
//!   - The second `Set`'s inner `Add` may also read the saved copy
//!     (`Add(1, Get(T, J))`): with the writes proven disjoint, `T[J]`
//!     holds exactly `old` at that point — same value, same one-read shape.
//!     Both spellings are accepted.
//! - **Constant index folding** ⇐ `Get(B, Add(c1, c2))` /
//!   `Set(B, Add(c1, c2), V)` where `c1`, `c2` are integral constants whose
//!   sum is exactly representable: the lowering of a constant-index place
//!   with a nonzero static offset emits the `Add` (the mid-level pipeline
//!   never sees it, so SCCP cannot fold it). Census: 6,650 pydori / 153
//!   corpus sites, 2 evaluations each. Folding to the literal sum is
//!   bit-exact (`Add` never traps, integral f64 addition within ±2^53 is
//!   exact) and drops two constant evaluations per execution. The folded
//!   constant is int-tagged iff both inputs were (matching SCCP's tag rule;
//!   in practice both are int-tagged place components). This is the
//!   degenerate `GetShifted` tile — the full `GetShifted`/`SetShifted`
//!   shapes (`index + i*stride`) measured ZERO occurrences (census), so
//!   only the constant fold is implemented.
//!
//! # Tiles measured and *refused* (census + exactness)
//!
//! - **`SetAdd`/`SetSubtract`/... (the read-modify-write `Set*` family)**:
//!   no interpreter kernel exists — the legacy interpreter (and ours,
//!   bit-for-bit) rejects them as runtime-only ops, so emitting them would
//!   make compiled output unverifiable by the behavioral/differential
//!   oracle. Census: 18,476 pydori / 1,985 corpus non-increment RMW shapes
//!   (`rmw_*` rows) stay untiled for this reason alone — by far the
//!   largest tile-shaped population, recorded as a follow-up candidate
//!   (PORT.md T3.12 worklog) since it would need a deliberate
//!   oracle-extension decision.
//! - **`GetShifted`/`SetShifted`** (`Get(B, Add(O, Mul(I, S)))`): zero
//!   full-shape occurrences on corpus + pydori. Also hard to make exact:
//!   the kernels `ensure_int` O, I, S *individually* where the tree checks
//!   only the sum (`offset 1.5 + index 0.5` passes the tree and traps the
//!   kernel), so O, I, S would each need to be provably integral.
//!   Offset-only/stride-only degenerate shapes (10,896/947 + 658/129)
//!   are eval-count and node-count ties (4+n both ways) — pointless. Not
//!   implemented, by measurement.
//! - **`GetPointed`/`SetPointed`/`IncrementP*Pointed`**: `Get(Get(B, I), ...)`
//!   occurs (368/64) but never with the paired `index`/`index + 1` cell
//!   reads the `*Pointed` kernels require (they dereference a two-cell
//!   block+index pointer; the emitted shapes are single computed-block
//!   reads). Not implemented, by measurement.
//! - **`Lerp`/`LerpClamped`/`Unlerp`/`UnlerpClamped`/`Remap`/
//!   `RemapClamped`/`Clamp`**: the frontend already emits these ops fused
//!   (`Op.Lerp` etc. exist natively in traced CFGs); the arithmetic
//!   spellings (`Add(A, Mul(Sub(B, A), T))`, `Max(L, Min(H, X))`,
//!   `Div(Sub(V, A), Sub(B, A))`) occur 0 times across corpus + pydori,
//!   except `Max(0, Min(1, X))` (8 pydori / 0 corpus — negligible; a
//!   `Clamp` tile would save 1 eval per execution at 8 sites). Not
//!   implemented, by measurement.
//! - **`Execute0`/`Execute` selection**: the emitter's choice is already
//!   optimal — `Execute` and `Execute0` cost identical evaluations (one
//!   node plus each argument), the `JumpLoop` block protocol *uses* every
//!   block-`Execute`'s value (the next dispatch index), and census found 0
//!   value-discarded `Execute` nodes whose last argument's value is
//!   load-bearing. Nothing to select; recorded by measurement.
//!
//! # What tiling does NOT do
//!
//! Tiles never match across a conditional boundary (`If`/`And`/`Or`/
//! `Switch*` arms are conditionally evaluated, D11): a tile is a contiguous
//! subtree (or statement-list window) and is rewritten in place, so a tile
//! rooted inside an arm stays inside that arm; the matched fragments share
//! one conditional context by construction. RNG (`Random`/`RandomInteger`)
//! and effectful/runtime-only ops are refused inside every matched operand
//! (the **pure** condition), so draws/effects are never moved, duplicated,
//! or eliminated. `Execute` scaffolding from T3.8 arms is refused inside
//! deduplicated operands by the same rule (`Execute` itself is never pure
//! here: its children may write — purity is computed over the whole
//! subtree, so a pure-throughout `Execute` is fine and an impure one is
//! refused).
//!
//! # Mechanics
//!
//! Greedy bottom-up rewrite in one iterative post-order walk (explicit work
//! stack, invariant §3.4) over the reachable tree, memoized per arena node:
//! children first, then the largest tile whose conditions hold at the node
//! (statement-pair tiles are matched on the argument lists of `Execute`/
//! `Execute0` nodes, left to right). Nodes are
//! materialized into the output arena lazily (only when referenced), so
//! matched-away interiors and emit-time orphans drop out. Deterministic:
//! same input arena → same output arena, byte for byte.

use std::collections::HashMap;

use crate::nodes::{EngineNodes, NodeArena, NodeId, NodeKind};
use crate::ops::Op;

/// Statistics from one tiling run (census of fired tiles; test/metrics aid).
/// Counts *fired matches* during the bottom-up rewrite: a match superseded by
/// a larger enclosing tile (e.g. the inner `Get`'s constant-index fold under
/// an enclosing increment tile) still counts once, even though its node is
/// orphaned by the larger tile.
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct TileStats {
    /// `Set(B,I,Add(1,Get(B,I)))` → `IncrementPre(B,I)` rewrites.
    pub increment_pre: u64,
    /// Statement-pair → `Set(T,J,IncrementPost(B,I))` rewrites.
    pub increment_post: u64,
    /// `Get/Set(B, Add(c,c))` constant index folds.
    pub const_index_folds: u64,
}

impl TileStats {
    pub fn total(&self) -> u64 {
        self.increment_pre + self.increment_post + self.const_index_folds
    }
}

/// Applies fused-op tiling to an emitted engine-node tree. See module docs.
pub fn tile_engine_nodes(nodes: &EngineNodes) -> EngineNodes {
    tile_engine_nodes_stats(nodes).0
}

/// [`tile_engine_nodes`] plus fired-tile statistics.
pub fn tile_engine_nodes_stats(nodes: &EngineNodes) -> (EngineNodes, TileStats) {
    let cx = Analysis::compute(&nodes.arena, nodes.root);
    let mut rw = Rewriter {
        src: &nodes.arena,
        cx: &cx,
        out: NodeArena::new(),
        memo: HashMap::new(),
        out_memo: HashMap::new(),
        stats: TileStats::default(),
    };
    let root = rw.rewrite(nodes.root);
    let root = rw.materialize(root);
    (
        EngineNodes {
            arena: rw.out,
            root,
        },
        rw.stats,
    )
}

// ---------------------------------------------------------------------------
// Subtree analysis (purity / totality / integrality / structural classes)
// ---------------------------------------------------------------------------

/// Per-node facts over the source arena, computed in one forward pass
/// (arena order is topological: `push_func` arguments precede the node).
struct Analysis {
    /// Structural class id (bit-strict: const f64 bits; func op + child
    /// classes). Tags ignored (output-only).
    class: Vec<u32>,
    /// Subtree contains only effect-free ops (no writes/logs/RNG/
    /// runtime-only ops). See module docs ("pure").
    pure: Vec<bool>,
    /// Pure AND never-trapping AND all constants finite ("total").
    total: Vec<bool>,
    /// Value provably satisfies `ensure_int` on completion.
    integral: Vec<bool>,
}

/// Structural-class interning key.
#[derive(PartialEq, Eq, Hash)]
enum ClassKey {
    Const(u64),
    Func(Op, Vec<u32>),
}

impl Analysis {
    // float_cmp: `trunc() == value` is the exact integrality test (§6).
    #[allow(clippy::float_cmp)]
    fn compute(arena: &NodeArena, _root: NodeId) -> Self {
        let n = arena.len();
        let mut class = vec![0u32; n];
        let mut pure = vec![false; n];
        let mut total = vec![false; n];
        let mut integral = vec![false; n];
        let mut intern: HashMap<ClassKey, u32> = HashMap::new();
        for i in 0..n {
            let id = arena.id_at(i);
            match arena.kind(id) {
                NodeKind::Const { value, is_int } => {
                    pure[i] = true;
                    total[i] = value.is_finite();
                    integral[i] = is_int && value.is_finite() && value.trunc() == value;
                    let key = ClassKey::Const(value.to_bits());
                    let next = u32::try_from(intern.len()).expect("class count fits u32");
                    class[i] = *intern.entry(key).or_insert(next);
                }
                NodeKind::Func { op, .. } => {
                    let args = arena.args_of(id);
                    let kids_pure = args.iter().all(|a| pure[a.index()]);
                    let kids_total = args.iter().all(|a| total[a.index()]);
                    pure[i] = kids_pure && op_is_effect_free(op);
                    total[i] = kids_total && pure[i] && op_is_total_node(op);
                    integral[i] = op_is_integral(op);
                    let key = ClassKey::Func(op, args.iter().map(|a| class[a.index()]).collect());
                    let next = u32::try_from(intern.len()).expect("class count fits u32");
                    class[i] = *intern.entry(key).or_insert(next);
                }
            }
        }
        Self {
            class,
            pure,
            total,
            integral,
        }
    }

    fn same(&self, a: NodeId, b: NodeId) -> bool {
        self.class[a.index()] == self.class[b.index()]
    }
}

/// Effect-free node ops: evaluating one (given effect-free children) cannot
/// write memory, log, or draw RNG. `Op::pure` plus the effect-free reads.
/// Runtime-only ops are deliberately NOT effect-free (the host implements
/// them; we cannot reason about their behavior).
fn op_is_effect_free(op: Op) -> bool {
    op.pure() || matches!(op, Op::Get | Op::GetShifted | Op::GetPointed)
}

/// Never-trapping value ops (node-level mirror of DCE's `op_is_total`;
/// `Get` is NOT total — bounds asserts). Control/sequencing forms (`If`,
/// `And`, `Or`, `Execute`, ...) are conservatively excluded even though
/// some are total given total children — the simple op table is enough for
/// the implemented tiles.
fn op_is_total_node(op: Op) -> bool {
    matches!(
        op,
        Op::Abs
            | Op::Add
            | Op::Arctan
            | Op::Arctan2
            | Op::Clamp
            | Op::Degree
            | Op::Equal
            | Op::Frac
            | Op::Greater
            | Op::GreaterOr
            | Op::Lerp
            | Op::LerpClamped
            | Op::Less
            | Op::LessOr
            | Op::Max
            | Op::Min
            | Op::Multiply
            | Op::Negate
            | Op::Not
            | Op::NotEqual
            | Op::Radian
            | Op::Sign
            | Op::Subtract
            | Op::Tanh
    )
}

/// Ops whose result provably satisfies `ensure_int` whenever they complete:
/// `py_floor`/`py_ceil`/`py_round`/`py_trunc` return integral f64s or trap;
/// comparisons and `Not` return `0.0`/`1.0`.
fn op_is_integral(op: Op) -> bool {
    matches!(
        op,
        Op::Floor
            | Op::Ceil
            | Op::Round
            | Op::Trunc
            | Op::Equal
            | Op::NotEqual
            | Op::Greater
            | Op::GreaterOr
            | Op::Less
            | Op::LessOr
            | Op::Not
    )
}

// ---------------------------------------------------------------------------
// Rewriter
// ---------------------------------------------------------------------------

/// A rewritten node: either a verbatim copy of a source node (lazy) or a
/// freshly manufactured tile node.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
enum RwNode {
    /// Source node, args possibly rewritten. Materialized on demand.
    Src(NodeId),
    /// Manufactured node, already in the output arena.
    Out(NodeId),
}

struct Rewriter<'a> {
    src: &'a NodeArena,
    cx: &'a Analysis,
    out: NodeArena,
    /// Post-order rewrite memo: source node -> rewritten arg list (for
    /// function nodes) keyed by source id. DAG-safe.
    memo: HashMap<NodeId, RwNode>,
    /// Materialization memo: source node -> output arena id.
    out_memo: HashMap<NodeId, NodeId>,
    stats: TileStats,
}

/// Work-stack jobs for the iterative post-order rewrite.
enum Job {
    Visit(NodeId),
    Build(NodeId),
}

impl Rewriter<'_> {
    /// Rewrites the subtree rooted at `id` (iterative post-order; memoized).
    fn rewrite(&mut self, root: NodeId) -> RwNode {
        let mut stack = vec![Job::Visit(root)];
        while let Some(job) = stack.pop() {
            match job {
                Job::Visit(id) => {
                    if self.memo.contains_key(&id) {
                        continue;
                    }
                    match self.src.kind(id) {
                        NodeKind::Const { .. } => {
                            self.memo.insert(id, RwNode::Src(id));
                        }
                        NodeKind::Func { .. } => {
                            stack.push(Job::Build(id));
                            for &arg in self.src.args_of(id) {
                                stack.push(Job::Visit(arg));
                            }
                        }
                    }
                }
                Job::Build(id) => {
                    if self.memo.contains_key(&id) {
                        continue;
                    }
                    let built = self.build(id);
                    self.memo.insert(id, built);
                }
            }
        }
        self.memo[&root]
    }

    /// Rebuilds one function node whose children are already rewritten,
    /// applying the largest matching tile at this root.
    fn build(&mut self, id: NodeId) -> RwNode {
        let NodeKind::Func { op, .. } = self.src.kind(id) else {
            unreachable!("build called on a constant");
        };
        // Tile matching happens on the ORIGINAL child structure (the
        // analysis facts are source-arena facts). Children are rewritten
        // independently afterwards; a matched tile consumes original
        // subtrees whose own rewrites are reused via the memo.
        if let Some(t) = self.try_increment_pre(id) {
            self.stats.increment_pre += 1;
            return t;
        }
        if let Some(t) = self.try_const_index_fold(id) {
            self.stats.const_index_folds += 1;
            return t;
        }
        if matches!(op, Op::Execute | Op::Execute0)
            && let Some(t) = self.try_statement_pairs(id)
        {
            return t;
        }
        // No tile: rebuild with rewritten children. If every child is
        // unchanged, keep the source node itself (lazy copy).
        let args = self.src.args_of(id).to_vec();
        let rewritten: Vec<RwNode> = args.iter().map(|a| self.memo[a]).collect();
        if rewritten
            .iter()
            .zip(&args)
            .all(|(rw, a)| matches!(rw, RwNode::Src(s) if s == a))
        {
            return RwNode::Src(id);
        }
        let out_args: Vec<NodeId> = rewritten
            .into_iter()
            .map(|rw| self.materialize(rw))
            .collect();
        RwNode::Out(self.out.push_func(op, &out_args))
    }

    /// `Set(B, I, Add(1, Get(B, I)))` → `IncrementPre(B, I)`. Module docs
    /// carry the full exactness argument.
    fn try_increment_pre(&mut self, id: NodeId) -> Option<RwNode> {
        let (op, args) = self.func(id)?;
        if op != Op::Set || args.len() != 3 {
            return None;
        }
        let (b, i, value) = (args[0], args[1], args[2]);
        let (add_op, add_args) = self.func(value)?;
        if add_op != Op::Add || add_args.len() != 2 {
            return None;
        }
        let get = self.match_inc_addend(add_args[0], add_args[1])?;
        let (get_op, get_args) = self.func(get)?;
        if get_op != Op::Get || get_args.len() != 2 {
            return None;
        }
        if !(self.cx.same(b, get_args[0]) && self.cx.same(i, get_args[1])) {
            return None;
        }
        // Dedup soundness: B and I evaluated once instead of twice.
        if !(self.is_pure(b) && self.is_pure(i)) {
            return None;
        }
        // Trap-order soundness: kernel ensure_ints B before evaluating I.
        if !(self.is_integral(b) || self.is_total(i)) {
            return None;
        }
        let nb = self.rewrite_child(b);
        let nb = self.materialize(nb);
        // The index may itself be a foldable constant `Add` (static place
        // offset): fold it here, since the fused node replaces the `Get`/
        // `Set` roots the standalone fold would have fired on.
        let ni = if let Some(folded) = self.fold_const_add(i) {
            self.stats.const_index_folds += 1;
            folded
        } else {
            let ni = self.rewrite_child(i);
            self.materialize(ni)
        };
        Some(RwNode::Out(self.out.push_func(Op::IncrementPre, &[nb, ni])))
    }

    /// Matches the `Add` operand pair of an increment: one side a constant
    /// `1.0` (either tag), the other the `Get`. Returns the `Get` side.
    // float_cmp: matching the exact constant 1.0 is the point.
    #[allow(clippy::float_cmp)]
    fn match_inc_addend(&self, a: NodeId, b: NodeId) -> Option<NodeId> {
        let one =
            |id: NodeId| matches!(self.src.kind(id), NodeKind::Const { value, .. } if value == 1.0);
        if one(a) {
            Some(b)
        } else if one(b) {
            Some(a)
        } else {
            None
        }
    }

    /// `Get(B, Add(c1, c2))` / `Set(B, Add(c1, c2), V)` constant index fold
    /// (the lowering's static place offset; module docs).
    fn try_const_index_fold(&mut self, id: NodeId) -> Option<RwNode> {
        let (op, args) = self.func(id)?;
        let index_pos = match op {
            Op::Get => {
                if args.len() != 2 {
                    return None;
                }
                1
            }
            Op::Set => {
                if args.len() != 3 {
                    return None;
                }
                1
            }
            _ => return None,
        };
        let folded = self.fold_const_add(args[index_pos])?;
        let mut out_args: Vec<NodeId> = Vec::with_capacity(args.len());
        for (pos, &a) in args.iter().enumerate() {
            if pos == index_pos {
                out_args.push(folded);
            } else {
                let rw = self.rewrite_child(a);
                out_args.push(self.materialize(rw));
            }
        }
        Some(RwNode::Out(self.out.push_func(op, &out_args)))
    }

    /// Folds `Add(c1, c2)` of two integral constants with an exactly
    /// representable sum into a fresh constant in the output arena.
    /// Int-tagged iff both inputs are. `Add` never traps and constant
    /// evaluation is unobservable, so the fold is exact; the |sum| ≤ 2^53
    /// guard keeps integer addition exact in f64.
    // float_cmp: `trunc() == value` is the exact integrality test (§6).
    #[allow(clippy::float_cmp)]
    fn fold_const_add(&mut self, id: NodeId) -> Option<NodeId> {
        const EXACT: f64 = 9_007_199_254_740_992.0; // 2^53
        let (op, args) = self.func(id)?;
        if op != Op::Add || args.len() != 2 {
            return None;
        }
        let int_const = |id: NodeId| match self.src.kind(id) {
            NodeKind::Const { value, is_int }
                if value.is_finite() && value.trunc() == value && value.abs() <= EXACT =>
            {
                Some((value, is_int))
            }
            _ => None,
        };
        let (v1, t1) = int_const(args[0])?;
        let (v2, t2) = int_const(args[1])?;
        let sum = v1 + v2;
        if sum.abs() > EXACT {
            return None;
        }
        Some(self.out.push_const(sum, t1 && t2))
    }

    /// Scans an `Execute`/`Execute0` argument list left to right for the
    /// post-increment statement pair (module docs) and merges each match.
    /// Returns the rebuilt node if anything fired.
    fn try_statement_pairs(&mut self, id: NodeId) -> Option<RwNode> {
        let (op, args) = self.func(id)?;
        if args.len() < 2 {
            return None;
        }
        // For Execute the last argument's value is the node's value; a pair
        // whose second statement lands there must not merge.
        let value_limit = match op {
            Op::Execute => args.len() - 1,
            Op::Execute0 => args.len(),
            _ => unreachable!("statement pairs only on Execute forms"),
        };
        let mut merged: Vec<RwNode> = Vec::with_capacity(args.len());
        let mut fired = 0u64;
        let mut k = 0usize;
        while k < args.len() {
            if k + 1 < value_limit
                && let Some(node) = self.try_increment_post_pair(args[k], args[k + 1])
            {
                merged.push(node);
                fired += 1;
                k += 2;
                continue;
            }
            merged.push(self.rewrite_child(args[k]));
            k += 1;
        }
        if fired == 0 {
            return None;
        }
        self.stats.increment_post += fired;
        let out_args: Vec<NodeId> = merged.into_iter().map(|rw| self.materialize(rw)).collect();
        Some(RwNode::Out(self.out.push_func(op, &out_args)))
    }

    /// Matches `Set(T, J, Get(B, I)); Set(B, I, Add(1, Get(B, I) | Get(T, J)))`
    /// → `Set(T, J, IncrementPost(B, I))`. Module docs carry the exactness
    /// argument (constant disjoint places, write-order swap proof).
    fn try_increment_post_pair(&mut self, first: NodeId, second: NodeId) -> Option<RwNode> {
        let (op1, args1) = self.func(first)?;
        if op1 != Op::Set || args1.len() != 3 {
            return None;
        }
        let (t, j, save) = (args1[0], args1[1], args1[2]);
        let (save_op, save_args) = self.func(save)?;
        if save_op != Op::Get || save_args.len() != 2 {
            return None;
        }
        let (b, i) = (save_args[0], save_args[1]);

        let (op2, args2) = self.func(second)?;
        if op2 != Op::Set || args2.len() != 3 {
            return None;
        }
        // Second statement writes the same place the first read.
        if !(self.cx.same(args2[0], b) && self.cx.same(args2[1], i)) {
            return None;
        }
        let (add_op, add_args) = self.func(args2[2])?;
        if add_op != Op::Add || add_args.len() != 2 {
            return None;
        }
        let get2 = self.match_inc_addend(add_args[0], add_args[1])?;
        let (get2_op, get2_args) = self.func(get2)?;
        if get2_op != Op::Get || get2_args.len() != 2 {
            return None;
        }
        // The bump may re-read B[I] or read the saved copy T[J].
        let reads_src = self.cx.same(get2_args[0], b) && self.cx.same(get2_args[1], i);
        let reads_copy = self.cx.same(get2_args[0], t) && self.cx.same(get2_args[1], j);
        if !(reads_src || reads_copy) {
            return None;
        }
        // Constant, provably disjoint places (module docs: write order swaps).
        let (tb, tj) = (self.const_value(t)?, self.const_value(j)?);
        let (bb, bi) = (self.const_value(b)?, self.const_value(i)?);
        if (tb.to_bits(), tj.to_bits()) == (bb.to_bits(), bi.to_bits()) {
            return None;
        }
        // The tile moves the `T[J]` write (and its `ensure_int`/bounds
        // checks) AFTER the `B[I]` read-modify-write; the original checked
        // and wrote `T[J]` first. Sound only when those checks provably
        // cannot fire: T integral, J integral and in the runtime index
        // range (`0..=65535`) — otherwise a trap between the two writes
        // would observe the swapped order.
        if !(self.is_integral(t) && self.is_integral(j) && (0.0..=65535.0).contains(&tj)) {
            return None;
        }
        let nt = self.rewrite_child(t);
        let nj = self.rewrite_child(j);
        let nb = self.rewrite_child(b);
        let ni = self.rewrite_child(i);
        let (nt, nj) = (self.materialize(nt), self.materialize(nj));
        let (nb, ni) = (self.materialize(nb), self.materialize(ni));
        let inc = self.out.push_func(Op::IncrementPost, &[nb, ni]);
        Some(RwNode::Out(self.out.push_func(Op::Set, &[nt, nj, inc])))
    }

    // -- small helpers ------------------------------------------------------

    fn func(&self, id: NodeId) -> Option<(Op, Vec<NodeId>)> {
        match self.src.kind(id) {
            NodeKind::Func { op, .. } => Some((op, self.src.args_of(id).to_vec())),
            NodeKind::Const { .. } => None,
        }
    }

    fn const_value(&self, id: NodeId) -> Option<f64> {
        match self.src.kind(id) {
            NodeKind::Const { value, .. } => Some(value),
            NodeKind::Func { .. } => None,
        }
    }

    fn is_pure(&self, id: NodeId) -> bool {
        self.cx.pure[id.index()]
    }

    fn is_total(&self, id: NodeId) -> bool {
        self.cx.total[id.index()]
    }

    fn is_integral(&self, id: NodeId) -> bool {
        self.cx.integral[id.index()]
    }

    /// The rewritten form of a child (must have been visited already —
    /// guaranteed by post-order).
    fn rewrite_child(&mut self, id: NodeId) -> RwNode {
        self.memo[&id]
    }

    /// Copies a rewritten node into the output arena (memoized for source
    /// nodes; iterative).
    fn materialize(&mut self, node: RwNode) -> NodeId {
        match node {
            RwNode::Out(id) => id,
            RwNode::Src(id) => self.materialize_src(id),
        }
    }

    fn materialize_src(&mut self, root: NodeId) -> NodeId {
        if let Some(&done) = self.out_memo.get(&root) {
            return done;
        }
        let mut stack = vec![Job::Visit(root)];
        while let Some(job) = stack.pop() {
            match job {
                Job::Visit(id) => {
                    if self.out_memo.contains_key(&id) {
                        continue;
                    }
                    match self.src.kind(id) {
                        NodeKind::Const { value, is_int } => {
                            let nid = self.out.push_const(value, is_int);
                            self.out_memo.insert(id, nid);
                        }
                        NodeKind::Func { .. } => {
                            stack.push(Job::Build(id));
                            // An unchanged (Src) node's children are
                            // necessarily unchanged too (build() only keeps
                            // a Src when every child rewrote to itself), so
                            // every child here is Src(child); the Out arm is
                            // defensive.
                            for &arg in self.src.args_of(id) {
                                match self.memo.get(&arg) {
                                    Some(RwNode::Src(s)) => stack.push(Job::Visit(*s)),
                                    Some(RwNode::Out(_)) | None => {}
                                }
                            }
                        }
                    }
                }
                Job::Build(id) => {
                    if self.out_memo.contains_key(&id) {
                        continue;
                    }
                    let NodeKind::Func { op, .. } = self.src.kind(id) else {
                        unreachable!("Build job on a constant");
                    };
                    let args: Vec<NodeId> = self
                        .src
                        .args_of(id)
                        .iter()
                        .map(|a| match self.memo.get(a) {
                            Some(RwNode::Out(o)) => *o,
                            Some(RwNode::Src(s)) => self.out_memo[s],
                            // Unvisited child: verbatim subtree (reachable
                            // only through unchanged parents).
                            None => {
                                unreachable!("materialize_src hit an unvisited child")
                            }
                        })
                        .collect();
                    let nid = self.out.push_func(op, &args);
                    self.out_memo.insert(id, nid);
                }
            }
        }
        self.out_memo[&root]
    }
}

// ---------------------------------------------------------------------------
// Pattern census (PORT.md T3.12 step 1: measure before implementing)
// ---------------------------------------------------------------------------

/// The census rows, in report order.
const CENSUS_ROWS: &[&str] = &[
    "inc_pre",
    "inc_post_pair",
    "rmw_add_other",
    "rmw_sub",
    "rmw_mul",
    "rmw_div",
    "rmw_mod",
    "rmw_pow",
    "rmw_rem",
    "const_index_add",
    "const_add_other",
    "get_shifted_full",
    "get_shifted_offset_only",
    "get_shifted_mul_only",
    "set_shifted_full",
    "set_shifted_offset_only",
    "set_shifted_mul_only",
    "get_pointed_ish",
    "set_pointed_ish",
    "lerp_shape",
    "lerp_clamped_shape",
    "clamp_shape",
    "clamp01_shape",
    "unlerp_shape",
    "remap_ish",
    "remap_shape",
    "execute_value_discarded",
];

/// Counts candidate fused-op shapes in an emitted tree (pre-exactness:
/// structural shape only). Drives the data-driven tile selection; rows with
/// zero/negligible occurrences are recorded as not-implemented-by-
/// measurement. Returned as `(row, count)` pairs in fixed order.
// One flat shape-matching walk; splitting it would hurt, and the exact 1.0
// match is the point of `is_one`.
#[allow(clippy::too_many_lines, clippy::float_cmp)]
pub fn census_engine_nodes(nodes: &EngineNodes) -> Vec<(&'static str, u64)> {
    let arena = &nodes.arena;
    let cx = Analysis::compute(arena, nodes.root);
    let mut counts: HashMap<&'static str, u64> = HashMap::new();
    let bump = |counts: &mut HashMap<&'static str, u64>, k: &'static str| {
        *counts.entry(k).or_insert(0) += 1;
    };

    let func = |id: NodeId| match arena.kind(id) {
        NodeKind::Func { op, .. } => Some((op, arena.args_of(id))),
        NodeKind::Const { .. } => None,
    };
    let const_val = |id: NodeId| match arena.kind(id) {
        NodeKind::Const { value, .. } => Some(value),
        NodeKind::Func { .. } => None,
    };
    let is_const = |id: NodeId| const_val(id).is_some();
    let is_one = |id: NodeId| const_val(id) == Some(1.0);
    let is_zero = |id: NodeId| const_val(id) == Some(0.0);

    // clamp01 shape: Max(0, Min(1, X)) (legacy interpreter's clamp01).
    let clamp01_arg = |id: NodeId| -> Option<NodeId> {
        let (op, args) = func(id)?;
        if op != Op::Max || args.len() != 2 || !is_zero(args[0]) {
            return None;
        }
        let (mop, margs) = func(args[1])?;
        if mop == Op::Min && margs.len() == 2 && is_one(margs[0]) {
            Some(margs[1])
        } else {
            None
        }
    };

    // Walk every reachable node once (the emitted arena is a strict tree;
    // memoized for safety on DAG-shaped arenas).
    let mut seen = vec![false; arena.len()];
    let mut stack = vec![nodes.root];
    while let Some(id) = stack.pop() {
        if seen[id.index()] {
            continue;
        }
        seen[id.index()] = true;
        for &a in arena.args_of(id) {
            stack.push(a);
        }
        let Some((op, args)) = func(id) else {
            continue;
        };
        match op {
            Op::Set if args.len() == 3 => {
                let (blk, idx, val) = (args[0], args[1], args[2]);
                // Constant-index fold candidate.
                if let Some((iop, iargs)) = func(idx) {
                    if iop == Op::Add && iargs.len() == 2 && iargs.iter().all(|&a| is_const(a)) {
                        bump(&mut counts, "const_index_add");
                    }
                    if iop == Op::Add
                        && iargs.len() == 2
                        && iargs.iter().any(|&a| {
                            func(a).is_some_and(|(o, aa)| o == Op::Multiply && aa.len() == 2)
                        })
                    {
                        bump(&mut counts, "set_shifted_full");
                    } else if iop == Op::Add && iargs.len() == 2 {
                        bump(&mut counts, "set_shifted_offset_only");
                    } else if iop == Op::Multiply && iargs.len() == 2 {
                        bump(&mut counts, "set_shifted_mul_only");
                    }
                }
                // RMW shapes: Set(B, I, OP(..., Get(B, I), ...)).
                if let Some((vop, vargs)) = func(val) {
                    let same_get = |g: NodeId| {
                        func(g).is_some_and(|(go, ga)| {
                            go == Op::Get
                                && ga.len() == 2
                                && cx.same(ga[0], blk)
                                && cx.same(ga[1], idx)
                        })
                    };
                    if vargs.len() == 2 {
                        let (lhs, rhs) = (vargs[0], vargs[1]);
                        let row: Option<&'static str> = match vop {
                            Op::Add
                                if (is_one(lhs) && same_get(rhs))
                                    || (is_one(rhs) && same_get(lhs)) =>
                            {
                                Some("inc_pre")
                            }
                            Op::Add if same_get(lhs) || same_get(rhs) => Some("rmw_add_other"),
                            Op::Subtract if same_get(lhs) => Some("rmw_sub"),
                            Op::Multiply if same_get(lhs) || same_get(rhs) => Some("rmw_mul"),
                            Op::Divide if same_get(lhs) => Some("rmw_div"),
                            Op::Mod if same_get(lhs) => Some("rmw_mod"),
                            Op::Power if same_get(lhs) => Some("rmw_pow"),
                            Op::Rem if same_get(lhs) => Some("rmw_rem"),
                            _ => None,
                        };
                        if let Some(row) = row {
                            bump(&mut counts, row);
                        }
                    }
                }
                if func(blk).is_some_and(|(o, _)| o == Op::Get) {
                    bump(&mut counts, "set_pointed_ish");
                }
            }
            Op::Get if args.len() == 2 => {
                let i = args[1];
                if let Some((iop, iargs)) = func(i) {
                    if iop == Op::Add && iargs.len() == 2 && iargs.iter().all(|&a| is_const(a)) {
                        bump(&mut counts, "const_index_add");
                    }
                    if iop == Op::Add
                        && iargs.len() == 2
                        && iargs.iter().any(|&a| {
                            func(a).is_some_and(|(o, aa)| o == Op::Multiply && aa.len() == 2)
                        })
                    {
                        bump(&mut counts, "get_shifted_full");
                    } else if iop == Op::Add && iargs.len() == 2 {
                        bump(&mut counts, "get_shifted_offset_only");
                    } else if iop == Op::Multiply && iargs.len() == 2 {
                        bump(&mut counts, "get_shifted_mul_only");
                    }
                }
                if func(args[0]).is_some_and(|(o, _)| o == Op::Get) {
                    bump(&mut counts, "get_pointed_ish");
                }
            }
            Op::Add if args.len() == 2 => {
                if args.iter().all(|&a| is_const(a)) {
                    bump(&mut counts, "const_add_other");
                }
                // Lerp shape: Add(A, Mul(Sub(B, A), T)) any arrangement.
                for (a, m) in [(args[0], args[1]), (args[1], args[0])] {
                    let Some((mop, margs)) = func(m) else {
                        continue;
                    };
                    if mop != Op::Multiply || margs.len() != 2 {
                        continue;
                    }
                    for (s, _t) in [(margs[0], margs[1]), (margs[1], margs[0])] {
                        let Some((sop, sargs)) = func(s) else {
                            continue;
                        };
                        if sop == Op::Subtract && sargs.len() == 2 && cx.same(sargs[1], a) {
                            bump(&mut counts, "lerp_shape");
                        }
                        if sop == Op::Divide && sargs.len() == 2 {
                            bump(&mut counts, "remap_ish");
                        }
                    }
                    // Remap shape: Add(C, Div(Mul(Sub, Sub), Sub)).
                    if let Some((dop, dargs)) = func(m)
                        && dop == Op::Divide
                        && dargs.len() == 2
                        && func(dargs[0]).is_some_and(|(o, aa)| o == Op::Multiply && aa.len() == 2)
                        && func(dargs[1]).is_some_and(|(o, aa)| o == Op::Subtract && aa.len() == 2)
                    {
                        bump(&mut counts, "remap_shape");
                    }
                }
            }
            Op::Max => {
                if clamp01_arg(id).is_some() {
                    bump(&mut counts, "clamp01_shape");
                } else if args.len() == 2
                    && func(args[1]).is_some_and(|(o, aa)| o == Op::Min && aa.len() == 2)
                {
                    bump(&mut counts, "clamp_shape");
                }
            }
            Op::Divide if args.len() == 2 => {
                let num_sub =
                    func(args[0]).is_some_and(|(o, aa)| o == Op::Subtract && aa.len() == 2);
                let den_sub =
                    func(args[1]).is_some_and(|(o, aa)| o == Op::Subtract && aa.len() == 2);
                if num_sub && den_sub {
                    bump(&mut counts, "unlerp_shape");
                }
            }
            Op::Lerp if args.len() == 3 => {
                if clamp01_arg(args[2]).is_some() {
                    bump(&mut counts, "lerp_clamped_shape");
                }
            }
            Op::Execute | Op::Execute0 => {
                // Post-increment statement pair (see try_increment_post_pair;
                // here shape-only: Set(T,J,Get(B,I)) ; Set(B,I,Add(1, Get)).
                for w in args.windows(2) {
                    let (Some((o1, a1)), Some((o2, a2))) = (func(w[0]), func(w[1])) else {
                        continue;
                    };
                    if o1 != Op::Set || a1.len() != 3 || o2 != Op::Set || a2.len() != 3 {
                        continue;
                    }
                    let Some((go, ga)) = func(a1[2]) else {
                        continue;
                    };
                    if go != Op::Get
                        || ga.len() != 2
                        || !(cx.same(a2[0], ga[0]) && cx.same(a2[1], ga[1]))
                    {
                        continue;
                    }
                    let Some((ao, aa)) = func(a2[2]) else {
                        continue;
                    };
                    if ao == Op::Add
                        && aa.len() == 2
                        && (is_one(aa[0]) || is_one(aa[1]))
                        && aa
                            .iter()
                            .any(|&x| func(x).is_some_and(|(o, _)| o == Op::Get))
                    {
                        bump(&mut counts, "inc_post_pair");
                    }
                }
                // Execute0-selection relevance: an Execute child in a
                // value-discarded position (non-last Execute arg / any
                // Execute0 arg).
                let limit = if op == Op::Execute {
                    args.len().saturating_sub(1)
                } else {
                    args.len()
                };
                for &a in &args[..limit] {
                    if func(a).is_some_and(|(o, _)| o == Op::Execute) {
                        bump(&mut counts, "execute_value_discarded");
                    }
                }
            }
            _ => {}
        }
    }

    CENSUS_ROWS
        .iter()
        .map(|&row| (row, counts.get(row).copied().unwrap_or(0)))
        .collect()
}

#[cfg(test)]
mod tests {
    #![allow(clippy::float_cmp)] // exact f64 equality is the assertion contract (§6)

    use super::*;
    use crate::interpret::{Interpreter, InterpreterError};
    use crate::nodes::{EngineNodes, format_engine_node, tree_node_count};

    /// Builds `EngineNodes` from a tiny S-expression-ish closure-based DSL.
    fn nodes(build: impl FnOnce(&mut NodeArena) -> NodeId) -> EngineNodes {
        let mut arena = NodeArena::new();
        let root = build(&mut arena);
        EngineNodes { arena, root }
    }

    fn fmt(n: &EngineNodes) -> String {
        format_engine_node(&n.arena, n.root)
    }

    /// Runs a tree on a fresh interpreter with `block 20 = values`, returning
    /// `(result-or-error, block20-after, log, eval_count)`.
    fn run_with(
        n: &EngineNodes,
        init: &[f64],
    ) -> (Result<f64, InterpreterError>, Vec<f64>, Vec<f64>, u64) {
        let mut interp = Interpreter::new(0);
        interp.set_block(20, init.to_vec());
        let result = interp.run(n);
        let block = interp.block(20).unwrap_or(&[]).to_vec();
        let log = interp.log().to_vec();
        let evals = interp.eval_count();
        (result, block, log, evals)
    }

    /// Asserts tiled and untiled trees behave identically on `init` (result
    /// bits / error identity, written memory, debug log). Returns the eval
    /// counts `(before, after)`.
    fn assert_equivalent(src: &EngineNodes, init: &[f64]) -> (u64, u64) {
        let (tiled, _) = tile_engine_nodes_stats(src);
        let (r1, b1, l1, e1) = run_with(src, init);
        let (r2, b2, l2, e2) = run_with(&tiled, init);
        match (&r1, &r2) {
            (Ok(a), Ok(b)) => assert_eq!(a.to_bits(), b.to_bits(), "result bits"),
            (Err(a), Err(b)) => {
                assert_eq!(a.kind, b.kind, "error kind");
                assert_eq!(a.message, b.message, "error message");
            }
            _ => panic!("outcome mismatch: {r1:?} vs {r2:?}"),
        }
        let bits = |v: &[f64]| v.iter().map(|x| x.to_bits()).collect::<Vec<_>>();
        assert_eq!(bits(&b1), bits(&b2), "written memory (bitwise)");
        assert_eq!(bits(&l1), bits(&l2), "debug log (bitwise)");
        (e1, e2)
    }

    /// [`assert_equivalent`] plus an exact saved-eval-count assertion.
    fn assert_equivalent_and_saves(src: &EngineNodes, init: &[f64], saved: u64) {
        let (e1, e2) = assert_equivalent(src, init);
        assert_eq!(e1 - e2, saved, "saved evals (before {e1}, after {e2})");
    }

    /// `Set(20, 0, Add(1, Get(20, 0)))` — the GVN const-first increment.
    fn inc_pre_tree(addend_first: bool, addend: f64, addend_int: bool) -> EngineNodes {
        nodes(|a| {
            let b1 = a.push_int(20.0);
            let i1 = a.push_int(0.0);
            let b2 = a.push_int(20.0);
            let i2 = a.push_int(0.0);
            let get = a.push_func(Op::Get, &[b2, i2]);
            let one = a.push_const(addend, addend_int);
            let add = if addend_first {
                a.push_func(Op::Add, &[one, get])
            } else {
                a.push_func(Op::Add, &[get, one])
            };
            a.push_func(Op::Set, &[b1, i1, add])
        })
    }

    #[test]
    fn increment_pre_fires_both_addend_orders_and_tags() {
        for addend_first in [true, false] {
            for addend_int in [true, false] {
                let src = inc_pre_tree(addend_first, 1.0, addend_int);
                let (tiled, stats) = tile_engine_nodes_stats(&src);
                assert_eq!(stats.increment_pre, 1, "addend_first={addend_first}");
                assert_eq!(fmt(&tiled), "IncrementPre(\n  20\n  0\n)");
                // 8 tree nodes -> 3: saves 5 evals; value 7+1=8 written+returned.
                assert_equivalent_and_saves(&src, &[7.0], 5);
                let (r, b, _, _) = run_with(&tiled, &[7.0]);
                assert_eq!(r.unwrap(), 8.0);
                assert_eq!(b, vec![8.0]);
            }
        }
    }

    #[test]
    fn increment_pre_preserves_nan_payload_semantics() {
        // NaN in memory: both forms write/return the propagated quiet NaN.
        let src = inc_pre_tree(true, 1.0, true);
        assert_equivalent_and_saves(&src, &[f64::NAN], 5);
    }

    #[test]
    fn increment_pre_refuses_non_one_addend() {
        // Add(2, Get) is the SetAdd shape — no interpreter kernel; refused.
        let src = inc_pre_tree(true, 2.0, true);
        let (tiled, stats) = tile_engine_nodes_stats(&src);
        assert_eq!(stats.total(), 0);
        assert_eq!(fmt(&tiled), fmt(&src));
    }

    #[test]
    fn increment_pre_refuses_mismatched_place() {
        // Set(20, 0, Add(1, Get(20, 1))): not a self-increment.
        let src = nodes(|a| {
            let b1 = a.push_int(20.0);
            let i1 = a.push_int(0.0);
            let b2 = a.push_int(20.0);
            let i2 = a.push_int(1.0);
            let get = a.push_func(Op::Get, &[b2, i2]);
            let one = a.push_int(1.0);
            let add = a.push_func(Op::Add, &[one, get]);
            a.push_func(Op::Set, &[b1, i1, add])
        });
        let (_, stats) = tile_engine_nodes_stats(&src);
        assert_eq!(stats.total(), 0);
    }

    #[test]
    fn increment_pre_refuses_effectful_place_operand() {
        // The index is `Execute(DebugLog(5), 0)`: structurally identical on
        // both occurrences but EFFECTFUL — deduplicating it would drop a log.
        let src = nodes(|a| {
            let mk_index = |a: &mut NodeArena| {
                let five = a.push_int(5.0);
                let log = a.push_func(Op::DebugLog, &[five]);
                let zero = a.push_int(0.0);
                a.push_func(Op::Execute, &[log, zero])
            };
            let b1 = a.push_int(20.0);
            let i1 = mk_index(a);
            let b2 = a.push_int(20.0);
            let i2 = mk_index(a);
            let get = a.push_func(Op::Get, &[b2, i2]);
            let one = a.push_int(1.0);
            let add = a.push_func(Op::Add, &[one, get]);
            a.push_func(Op::Set, &[b1, i1, add])
        });
        let (tiled, stats) = tile_engine_nodes_stats(&src);
        assert_eq!(stats.total(), 0, "effectful dedup must be refused");
        // Both logs survive.
        let (_, _, log, _) = run_with(&tiled, &[7.0]);
        assert_eq!(log, vec![5.0, 5.0]);
    }

    #[test]
    fn increment_pre_refuses_rng_place_operand() {
        // RandomInteger(0, 2) as the index: RNG is never deduplicated.
        let src = nodes(|a| {
            let mk_index = |a: &mut NodeArena| {
                let lo = a.push_int(0.0);
                let hi = a.push_int(2.0);
                a.push_func(Op::RandomInteger, &[lo, hi])
            };
            let b1 = a.push_int(20.0);
            let i1 = mk_index(a);
            let b2 = a.push_int(20.0);
            let i2 = mk_index(a);
            let get = a.push_func(Op::Get, &[b2, i2]);
            let one = a.push_int(1.0);
            let add = a.push_func(Op::Add, &[one, get]);
            a.push_func(Op::Set, &[b1, i1, add])
        });
        let (_, stats) = tile_engine_nodes_stats(&src);
        assert_eq!(stats.total(), 0, "RNG dedup must be refused");
    }

    #[test]
    fn increment_pre_trap_order_guard() {
        // B = 0.5 (non-integral const), I = Get(20, 0) (pure but trap-capable):
        // the kernel would trap on ensure_int(B) BEFORE evaluating I, the tree
        // traps after — refused.
        let src = nodes(|a| {
            let mk_i = |a: &mut NodeArena| {
                let b = a.push_int(20.0);
                let i = a.push_int(0.0);
                a.push_func(Op::Get, &[b, i])
            };
            let b1 = a.push_float(0.5);
            let i1 = mk_i(a);
            let b2 = a.push_float(0.5);
            let i2 = mk_i(a);
            let get = a.push_func(Op::Get, &[b2, i2]);
            let one = a.push_int(1.0);
            let add = a.push_func(Op::Add, &[one, get]);
            a.push_func(Op::Set, &[b1, i1, add])
        });
        let (_, stats) = tile_engine_nodes_stats(&src);
        assert_eq!(stats.total(), 0, "non-integral B with trap-capable I");

        // Same shape but I total (a constant): fires, and both forms trap
        // identically on the non-integral block id ("Value must be an
        // integer", pure const evals in between).
        let src = nodes(|a| {
            let b1 = a.push_float(0.5);
            let i1 = a.push_int(0.0);
            let b2 = a.push_float(0.5);
            let i2 = a.push_int(0.0);
            let get = a.push_func(Op::Get, &[b2, i2]);
            let one = a.push_int(1.0);
            let add = a.push_func(Op::Add, &[one, get]);
            a.push_func(Op::Set, &[b1, i1, add])
        });
        let (_, stats) = tile_engine_nodes_stats(&src);
        assert_eq!(stats.increment_pre, 1, "total I lifts the guard");
        assert_equivalent(&src, &[7.0]); // both trap with the identical error
    }

    #[test]
    fn increment_pre_fires_inside_if_arm() {
        // If(Get(20,1), Set(20,0,Add(1,Get(20,0))), 0): the tile sits wholly
        // inside one arm — conditionality is preserved.
        let build = |a: &mut NodeArena| {
            let cb = a.push_int(20.0);
            let ci = a.push_int(1.0);
            let cond = a.push_func(Op::Get, &[cb, ci]);
            let b1 = a.push_int(20.0);
            let i1 = a.push_int(0.0);
            let b2 = a.push_int(20.0);
            let i2 = a.push_int(0.0);
            let get = a.push_func(Op::Get, &[b2, i2]);
            let one = a.push_int(1.0);
            let add = a.push_func(Op::Add, &[one, get]);
            let set = a.push_func(Op::Set, &[b1, i1, add]);
            let zero = a.push_int(0.0);
            a.push_func(Op::If, &[cond, set, zero])
        };
        let src = nodes(build);
        let (tiled, stats) = tile_engine_nodes_stats(&src);
        assert_eq!(stats.increment_pre, 1);
        assert!(fmt(&tiled).contains("IncrementPre"));
        // Arm taken: increments. Arm not taken: nothing happens.
        assert_equivalent_and_saves(&src, &[7.0, 1.0], 5);
        assert_equivalent_and_saves(&src, &[7.0, 0.0], 0);
    }

    /// `Execute(Set(20,1,Get(20,0)), Set(20,0,Add(1,Get(20,0))), <last>)`.
    fn inc_post_tree(t: f64, j: f64, b: f64, i: f64, bump_reads_copy: bool) -> EngineNodes {
        nodes(|a| {
            let tb = a.push_int(t);
            let tj = a.push_int(j);
            let gb = a.push_int(b);
            let gi = a.push_int(i);
            let get = a.push_func(Op::Get, &[gb, gi]);
            let save = a.push_func(Op::Set, &[tb, tj, get]);
            let bb = a.push_int(b);
            let bi = a.push_int(i);
            let (rb, ri) = if bump_reads_copy { (t, j) } else { (b, i) };
            let rb = a.push_int(rb);
            let ri = a.push_int(ri);
            let get2 = a.push_func(Op::Get, &[rb, ri]);
            let one = a.push_int(1.0);
            let add = a.push_func(Op::Add, &[one, get2]);
            let bump = a.push_func(Op::Set, &[bb, bi, add]);
            let last = a.push_int(0.0);
            a.push_func(Op::Execute, &[save, bump, last])
        })
    }

    #[test]
    fn increment_post_pair_fires_both_read_spellings() {
        for reads_copy in [false, true] {
            let src = inc_post_tree(20.0, 1.0, 20.0, 0.0, reads_copy);
            let (tiled, stats) = tile_engine_nodes_stats(&src);
            assert_eq!(stats.increment_post, 1, "reads_copy={reads_copy}");
            assert_eq!(
                fmt(&tiled),
                "Execute(\n  Set(\n    20\n    1\n    IncrementPost(\n      20\n      0\n    )\n  )\n  0\n)"
            );
            // 14+1 nodes -> 6+1: saves 8 evals; 20[1]=old, 20[0]=old+1.
            assert_equivalent_and_saves(&src, &[7.0, 0.0], 8);
            let (_, b, _, _) = run_with(&tiled, &[7.0, 0.0]);
            assert_eq!(b, vec![8.0, 7.0]);
        }
    }

    #[test]
    fn increment_post_pair_refuses_aliasing_places() {
        // (T,J) == (B,I): the save and the bump hit the same cell; merging
        // would swap the write order and change the final value.
        let src = inc_post_tree(20.0, 0.0, 20.0, 0.0, false);
        let (_, stats) = tile_engine_nodes_stats(&src);
        assert_eq!(stats.increment_post, 0);
        // The inner bump IS still a valid self-increment.
        assert_eq!(stats.increment_pre, 1);
    }

    #[test]
    fn increment_post_pair_refuses_unprovable_save_place() {
        // J = 70000 (out of the runtime index range): the tile would move the
        // J bounds check after the B[I] write. Refused (the bump still tiles
        // as a plain self-increment).
        let src = inc_post_tree(20.0, 70000.0, 20.0, 0.0, false);
        let (_, stats) = tile_engine_nodes_stats(&src);
        assert_eq!(stats.increment_post, 0);
        assert_eq!(stats.increment_pre, 1);
        // Both trap on the save's J-bounds before the bump runs.
        assert_equivalent_and_saves(&src, &[7.0], 0);

        // J = 0.5 (non-integral): same refusal, and the whole pair must
        // behave identically (trap inside the save's ensure_int).
        let src = inc_post_tree(20.0, 0.5, 20.0, 0.0, false);
        let (_, stats) = tile_engine_nodes_stats(&src);
        assert_eq!(stats.increment_post, 0);
    }

    #[test]
    fn increment_post_pair_refuses_value_position() {
        // The bump as the LAST Execute argument: its value (old+1) is the
        // Execute's value; the merged form would return old. Refused.
        let src = nodes(|a| {
            let tb = a.push_int(20.0);
            let tj = a.push_int(1.0);
            let gb = a.push_int(20.0);
            let gi = a.push_int(0.0);
            let get = a.push_func(Op::Get, &[gb, gi]);
            let save = a.push_func(Op::Set, &[tb, tj, get]);
            let bb = a.push_int(20.0);
            let bi = a.push_int(0.0);
            let rb = a.push_int(20.0);
            let ri = a.push_int(0.0);
            let get2 = a.push_func(Op::Get, &[rb, ri]);
            let one = a.push_int(1.0);
            let add = a.push_func(Op::Add, &[one, get2]);
            let bump = a.push_func(Op::Set, &[bb, bi, add]);
            a.push_func(Op::Execute, &[save, bump])
        });
        let (tiled, stats) = tile_engine_nodes_stats(&src);
        assert_eq!(stats.increment_post, 0);
        // The bump still tiles standalone (IncrementPre returns the same
        // value Set returned), so the Execute value is preserved.
        assert_eq!(stats.increment_pre, 1);
        let (r, _, _, _) = run_with(&tiled, &[7.0]);
        assert_eq!(r.unwrap(), 8.0);
    }

    #[test]
    fn increment_post_pair_fires_in_execute0_last_position() {
        // Execute0 discards every argument value; the pair may sit at the end.
        let src = nodes(|a| {
            let tb = a.push_int(20.0);
            let tj = a.push_int(1.0);
            let gb = a.push_int(20.0);
            let gi = a.push_int(0.0);
            let get = a.push_func(Op::Get, &[gb, gi]);
            let save = a.push_func(Op::Set, &[tb, tj, get]);
            let bb = a.push_int(20.0);
            let bi = a.push_int(0.0);
            let rb = a.push_int(20.0);
            let ri = a.push_int(0.0);
            let get2 = a.push_func(Op::Get, &[rb, ri]);
            let one = a.push_int(1.0);
            let add = a.push_func(Op::Add, &[one, get2]);
            let bump = a.push_func(Op::Set, &[bb, bi, add]);
            a.push_func(Op::Execute0, &[save, bump])
        });
        let (_, stats) = tile_engine_nodes_stats(&src);
        assert_eq!(stats.increment_post, 1);
        assert_equivalent_and_saves(&src, &[7.0, 0.0], 8);
    }

    #[test]
    fn const_index_fold_fires_and_preserves_tags() {
        // Get(20, Add(2, 1)) -> Get(20, 3) (int-tagged).
        let src = nodes(|a| {
            let b = a.push_int(20.0);
            let c1 = a.push_int(2.0);
            let c2 = a.push_int(1.0);
            let add = a.push_func(Op::Add, &[c1, c2]);
            a.push_func(Op::Get, &[b, add])
        });
        let (tiled, stats) = tile_engine_nodes_stats(&src);
        assert_eq!(stats.const_index_folds, 1);
        assert_eq!(fmt(&tiled), "Get(\n  20\n  3\n)");
        assert_equivalent_and_saves(&src, &[1.0, 2.0, 3.0, 4.0], 2);

        // Float-tagged operand keeps the fold float-tagged ("3.0" not "3").
        let src = nodes(|a| {
            let b = a.push_int(20.0);
            let c1 = a.push_float(2.0);
            let c2 = a.push_int(1.0);
            let add = a.push_func(Op::Add, &[c1, c2]);
            a.push_func(Op::Get, &[b, add])
        });
        let (tiled, stats) = tile_engine_nodes_stats(&src);
        assert_eq!(stats.const_index_folds, 1);
        assert_eq!(fmt(&tiled), "Get(\n  20\n  3.0\n)");

        // Set position too: Set(20, Add(1, 1), 9.5) -> Set(20, 2, 9.5).
        let src = nodes(|a| {
            let b = a.push_int(20.0);
            let c1 = a.push_int(1.0);
            let c2 = a.push_int(1.0);
            let add = a.push_func(Op::Add, &[c1, c2]);
            let v = a.push_float(9.5);
            a.push_func(Op::Set, &[b, add, v])
        });
        let (tiled, stats) = tile_engine_nodes_stats(&src);
        assert_eq!(stats.const_index_folds, 1);
        assert_eq!(fmt(&tiled), "Set(\n  20\n  2\n  9.5\n)");
        assert_equivalent_and_saves(&src, &[0.0, 0.0, 0.0], 2);
    }

    #[test]
    fn const_index_fold_refuses_non_integral_and_huge() {
        // Non-integral constant: refused (kept clearly inside integral index
        // arithmetic; the shape never occurs with floats anyway).
        let src = nodes(|a| {
            let b = a.push_int(20.0);
            let c1 = a.push_float(0.5);
            let c2 = a.push_int(1.0);
            let add = a.push_func(Op::Add, &[c1, c2]);
            a.push_func(Op::Get, &[b, add])
        });
        let (_, stats) = tile_engine_nodes_stats(&src);
        assert_eq!(stats.const_index_folds, 0);

        // Beyond-2^53 sums: refused.
        let src = nodes(|a| {
            let b = a.push_int(20.0);
            let c1 = a.push_int(9_007_199_254_740_992.0);
            let c2 = a.push_int(9_007_199_254_740_992.0);
            let add = a.push_func(Op::Add, &[c1, c2]);
            a.push_func(Op::Get, &[b, add])
        });
        let (_, stats) = tile_engine_nodes_stats(&src);
        assert_eq!(stats.const_index_folds, 0);
    }

    #[test]
    fn increment_pre_folds_its_own_const_index() {
        // Set(20, Add(2,1), Add(1, Get(20, Add(2,1)))) -> IncrementPre(20, 3).
        let src = nodes(|a| {
            let mk_index = |a: &mut NodeArena| {
                let c1 = a.push_int(2.0);
                let c2 = a.push_int(1.0);
                a.push_func(Op::Add, &[c1, c2])
            };
            let b1 = a.push_int(20.0);
            let i1 = mk_index(a);
            let b2 = a.push_int(20.0);
            let i2 = mk_index(a);
            let get = a.push_func(Op::Get, &[b2, i2]);
            let one = a.push_int(1.0);
            let add = a.push_func(Op::Add, &[one, get]);
            a.push_func(Op::Set, &[b1, i1, add])
        });
        let (tiled, stats) = tile_engine_nodes_stats(&src);
        assert_eq!(stats.increment_pre, 1);
        // 2 fold firings: the inner Get's fold fires in post-order before
        // the enclosing increment tile supersedes it (stats count fired
        // matches, not surviving nodes; the superseded fold is orphaned and
        // unreachable).
        assert_eq!(stats.const_index_folds, 2);
        assert_eq!(fmt(&tiled), "IncrementPre(\n  20\n  3\n)");
        // 12 tree nodes -> 3: saves 9 evals.
        assert_equivalent_and_saves(&src, &[0.0, 0.0, 0.0, 7.0], 9);
    }

    #[test]
    fn untouched_trees_round_trip_unchanged() {
        // A node mix none of the tiles match must come out structurally
        // identical (and orphan-free).
        let src = nodes(|a| {
            let lhs = a.push_float(2.5);
            let rhs = a.push_int(3.0);
            let mul = a.push_func(Op::Multiply, &[lhs, rhs]);
            let blk = a.push_int(20.0);
            let idx = a.push_int(0.0);
            let get = a.push_func(Op::Get, &[blk, idx]);
            let add = a.push_func(Op::Add, &[mul, get]);
            let log = a.push_func(Op::DebugLog, &[add]);
            let zero = a.push_int(0.0);
            a.push_func(Op::Execute, &[log, zero])
        });
        let (tiled, stats) = tile_engine_nodes_stats(&src);
        assert_eq!(stats.total(), 0);
        assert_eq!(fmt(&tiled), fmt(&src));
        assert_eq!(
            tree_node_count(&tiled.arena, tiled.root),
            tree_node_count(&src.arena, src.root)
        );
    }

    #[test]
    fn deep_tree_is_iterative() {
        // 200k-deep Negate chain over an increment: must not overflow the
        // thread stack, and the tile at the bottom still fires.
        let src = nodes(|a| {
            let b1 = a.push_int(20.0);
            let i1 = a.push_int(0.0);
            let b2 = a.push_int(20.0);
            let i2 = a.push_int(0.0);
            let get = a.push_func(Op::Get, &[b2, i2]);
            let one = a.push_int(1.0);
            let add = a.push_func(Op::Add, &[one, get]);
            let mut node = a.push_func(Op::Set, &[b1, i1, add]);
            for _ in 0..200_000 {
                node = a.push_func(Op::Negate, &[node]);
            }
            node
        });
        let (tiled, stats) = tile_engine_nodes_stats(&src);
        assert_eq!(stats.increment_pre, 1);
        assert_eq!(
            tree_node_count(&tiled.arena, tiled.root),
            tree_node_count(&src.arena, src.root) - 5
        );
    }

    #[test]
    fn deterministic_output() {
        let src = inc_post_tree(20.0, 1.0, 20.0, 0.0, false);
        let (a, _) = tile_engine_nodes_stats(&src);
        let (b, _) = tile_engine_nodes_stats(&src);
        assert_eq!(fmt(&a), fmt(&b));
    }

    #[test]
    fn e2e_hot_loop_eval_reduction() {
        // The PreviewStage.render counter idiom, hand-built at node level:
        //   Block(JumpLoop(
        //     Execute(Set(20,0,0), 1),                       // i = 0
        //     Execute(Set(20,0,Add(1,Get(20,0))),            // i += 1  <- tile
        //             If(Less(Get(20,0), 5), 1, 2)),         // loop while i<5
        //     0))
        let src = nodes(|a| {
            let b = a.push_int(20.0);
            let i = a.push_int(0.0);
            let z = a.push_int(0.0);
            let init = a.push_func(Op::Set, &[b, i, z]);
            let one_b = a.push_int(1.0);
            let blk0 = a.push_func(Op::Execute, &[init, one_b]);

            let b1 = a.push_int(20.0);
            let i1 = a.push_int(0.0);
            let b2 = a.push_int(20.0);
            let i2 = a.push_int(0.0);
            let get = a.push_func(Op::Get, &[b2, i2]);
            let one = a.push_int(1.0);
            let add = a.push_func(Op::Add, &[one, get]);
            let bump = a.push_func(Op::Set, &[b1, i1, add]);
            let b3 = a.push_int(20.0);
            let i3 = a.push_int(0.0);
            let get3 = a.push_func(Op::Get, &[b3, i3]);
            let five = a.push_int(5.0);
            let less = a.push_func(Op::Less, &[get3, five]);
            let t1 = a.push_int(1.0);
            let t2 = a.push_int(2.0);
            let iff = a.push_func(Op::If, &[less, t1, t2]);
            let blk1 = a.push_func(Op::Execute, &[bump, iff]);

            let exit = a.push_int(0.0);
            let jl = a.push_func(Op::JumpLoop, &[blk0, blk1, exit]);
            a.push_func(Op::Block, &[jl])
        });
        let (tiled, stats) = tile_engine_nodes_stats(&src);
        assert_eq!(stats.increment_pre, 1);
        // 5 loop iterations x 5 saved evals per iteration.
        assert_equivalent_and_saves(&src, &[0.0], 25);
        let (_, block, _, _) = run_with(&tiled, &[0.0]);
        assert_eq!(block, vec![5.0]);
    }
}
