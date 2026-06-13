//! Emission-time `FlattenAssociativeOps` (PORT.md T3.10, wave W5).
//!
//! Rewrites an emitted engine-node tree so that nested same-op chains of the
//! runtime's variadic ops become single variadic nodes:
//! `Add(Add(a, b), c)` → `Add(a, b, c)`, `Execute(s1, Execute(s2, v))` →
//! `Execute(s1, s2, v)`. Each splice removes one interior node, which the
//! tree-walking runtime would otherwise re-evaluate on every execution — a
//! guaranteed dynamic-eval and static-node win.
//!
//! # Placement (invariant §3.3, decision D4)
//!
//! This transform exists **only** in the lowering→emission seam: it consumes
//! the [`crate::emit`] output ([`EngineNodes`]) and runs immediately before
//! output-node generation ([`crate::output`]). Mid-level MIR stays strictly
//! binary; no mid-level pass may rely on (or ever see) flattened form. It is
//! enabled per-pipeline (`standard` only — see [`crate::pipeline`]): `minimal`
//! must stay the trivially-correct differential baseline and `fast` is the
//! W1-prefix triage level, mirroring the legacy lineup where only
//! `STANDARD_PASSES` flattened.
//!
//! # Op set and exactness (byte-equal observables)
//!
//! The differential contract (results, logs, writes, RNG draws — see
//! [`crate::diff`]) requires every splice to be *observably exact*, not merely
//! algebraically licensed. Per op (interpreter semantics from
//! [`crate::interpret`], the semantic oracle):
//!
//! - **`Add` / `Multiply`** — `reduce_args` ops: evaluate *all* arguments left
//!   to right, then fold left. Splicing a same-op child **in argument position
//!   0** preserves the argument-evaluation sequence and the exact fold
//!   sequence `((a ⊕ b) ⊕ c) …`, and the `+`/`*` folds can never raise, so
//!   moving them after later argument evaluations is unobservable. This is
//!   bitwise equality — no FP reassociation happens (reassociation is licensed
//!   per ARCHITECTURE §4, but position-0 splicing does not even need the
//!   license). Splicing at positions ≥ 1 *would* reassociate
//!   (`a + (b + c)` → `(a + b) + c`) and change result bits, which the
//!   differential harness rejects — refused, with one proven-exact exception:
//!
//!   **Const-sibling rotation.** GVN's canonical commutative operand order
//!   (constants first, then ascending definition) flips real chains to
//!   `Add(c, D)` with `c` a literal constant and `D` the nested chain — the
//!   `x + y + 5` shape — where position-0 splicing never fires. Rewriting it
//!   as `Add(D's args…, c)` is bitwise-exact, not merely licensed:
//!   the argument-evaluation sequence changes only by *when the literal
//!   constant evaluates* (no trap, no effect, no RNG draw — unobservable);
//!   the folds compute `fold(D) ⊕ c` instead of `c ⊕ fold(D)`, and IEEE-754
//!   `+`/`*` are bitwise commutative when at most one operand is NaN — `c` is
//!   a literal node and literal NaNs cannot reach emission (NaN constants are
//!   ROM reads; a defensive guard refuses NaN anyway). Applied only to the
//!   exact GVN shape (binary parent, constant in position 0, same-op child in
//!   position 1) and only for `Add`/`Multiply` (commutative, non-raising
//!   folds). This deliberately deviates from "never reorder" — decision D13;
//!   measured on pydori it is the difference between the transform firing in
//!   loop bodies and missing most chains there (PORT.md T3.10 worklog).
//! - **`Mod` / `Rem` — deliberately dropped from the legacy op set.** Legacy
//!   `FlattenAssociativeOps` flattened `{Add, Multiply, Mod, Rem}`, but the
//!   `%`/`remainder` folds can raise (`ZeroDivisionError`/`ValueError`), and
//!   because `reduce_args` collects all arguments before folding, splicing
//!   `Mod(Mod(a, 0), c)` into `Mod(a, 0, c)` moves the raise *after* `c`'s
//!   evaluation — `c`'s side effects (writes, logs, RNG draws) or its own
//!   error would land where the nested form never reaches. The legacy
//!   pipeline shipped that reorder unchecked; under the differential contract
//!   it is a mismatch. Measured on the mini-corpus and all 300 pydori
//!   callbacks: **zero** `Mod`/`Rem` splice opportunities exist (real chains
//!   are `x % 1`-style binary forms), so the guard a sound splice would need
//!   (pure-and-total proof for every later argument) buys nothing — dropped
//!   (decision D13; see the PORT.md T3.10 worklog entry).
//! - **`Subtract`** (not flattened by legacy) — **added** (decision D13): the
//!   `-` fold never raises, so position-0 splicing is exact by the same
//!   argument as `Add`, and it measures real opportunities (18 pydori / 5
//!   corpus deduplicated pairs). The runtime supports variadic `Subtract`
//!   (legacy `RemoveRedundantArguments` emitted such forms; `reduce_args`
//!   folds them). No rotation — `Subtract` is not commutative.
//! - **`Divide`/`Power`** (not flattened by legacy): their folds raise; same
//!   hazard as `Mod`, and measured zero opportunities — refused.
//! - **`And` / `Or`** — variadic short-circuit, left to right, returning the
//!   last evaluated value. Splicing a same-op child at **any** position is
//!   exact: the flat form evaluates the same arguments in the same order,
//!   stops at the same first falsy (`And`) / truthy (`Or`) value, and returns
//!   the same value (the child's verdict value is exactly the value at which
//!   evaluation stopped inside it). Legacy never *needed* this case — its IR
//!   kept the frontend's n-ary `And`/`Or` natively — whereas this backend
//!   binarizes them at MIR build (decision D11) and W4 if-conversion
//!   manufactures more; flattening reconstructs the legacy shape.
//! - **`Execute`** — evaluates every argument, returns the last value.
//!   Splicing a same-op child at any position is exact: a non-last child's
//!   value was discarded anyway, and a last child's value *is* its own last
//!   argument's value. Legacy emitted blocks as single n-ary `Execute`s and
//!   never nested them; T3.8's converted arms wrap statements in binary
//!   `Execute` chains, which this splice collapses.
//! - **Zero-argument children are never spliced** (any op): `Execute()` /
//!   `And()` evaluate to `0.0` — a *value* that splicing would delete (e.g.
//!   `And(x, And(), y)` stops at the falsy `0.0`; `Execute(x, Execute())`
//!   returns `0.0`, not `x`). Unreachable from real emitted trees, but the
//!   transform is total over arbitrary arenas.
//! - **Mixed-op boundaries are never spliced** (`Add` into `Multiply`,
//!   `Execute` into `Execute0`, ...): only `child.op == parent.op` qualifies.
//!
//! # Sharing-awareness vs the node DAG dedup
//!
//! The emitted arena is a strict tree; sharing materializes only when
//! [`crate::output`] deduplicates structurally identical subtrees. Flattening
//! a subtree that is structurally shared inlines its argument list into each
//! (structurally distinct) parent, which can duplicate *argument references*
//! in the shipped node array. The cost model, against the tracked metrics:
//!
//! - **`eval_count`**: every splice saves exactly one node evaluation per
//!   execution — the interpreter re-evaluates shared DAG nodes per reference,
//!   so sharing never amortizes dynamic cost. Flattening always wins.
//! - **`static_nodes`** (tree expansion): every splice removes one interior
//!   node per occurrence. Flattening always wins.
//! - **`dag_size`** (deduplicated node *count*): flattening can never increase
//!   it. The flattened form of a subtree is a deterministic function of its
//!   original structure, so originally-identical subtrees stay identical
//!   (they collapse exactly as before) and every surviving node's class maps
//!   1:1 from an original class; spliced-away interior nodes only *remove*
//!   classes. (Distinct originals can even merge: `Execute(Execute(a,b),c)`
//!   and `Execute(a,Execute(b,c))` both flatten to `Execute(a,b,c)`.)
//! - **Shipped bytes** (untracked): the per-parent duplication of a shared
//!   chain's argument indices grows the serialized `args` arrays — the one
//!   real cost, bounded by (refs − 1) × chain length per shared chain.
//!
//! [`SharingPolicy`] makes the trade-off explicit and measurable:
//! [`SharingPolicy::Always`] (the default — see [`DEFAULT_POLICY`]) flattens
//! unconditionally; `UnsharedOnly` refuses to splice structurally shared
//! children; `SharedUpTo(k)` splices shared children only when the inlined
//! argument list is small (≤ k). Measured on corpus + pydori (PORT.md T3.10
//! worklog table), `Always` dominates or ties every tracked metric, with
//! argument-duplication growth in the per-mill range — hence the default.
//!
//! # Mechanics
//!
//! One iterative post-order walk (explicit work stack, invariant §3.4) over
//! the reachable tree, memoized per arena node (correct for DAG-shaped arenas
//! too). For every function node it computes the flattened argument list;
//! nodes are materialized into the output arena lazily, only when referenced
//! whole (a spliced child never materializes), so the result arena contains
//! exactly the reachable flattened nodes — emit-time orphans (e.g. the
//! `FinishSet` intermediate `Get`s) are dropped in passing. Deterministic:
//! same input arena + policy → same output arena, byte for byte.

use std::collections::HashMap;

use crate::nodes::{EngineNodes, NodeArena, NodeId, NodeKind};
use crate::ops::Op;

/// How to treat splice candidates whose subtree is structurally shared
/// (would-be DAG nodes with more than one reference). See the module docs'
/// cost model.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SharingPolicy {
    /// Flatten unconditionally (sharing only ever costs untracked serialized
    /// argument duplication; every tracked metric wins or ties).
    Always,
    /// Never splice a child whose structural class has more than one
    /// reference in the deduplicated DAG of the *unflattened* tree.
    UnsharedOnly,
    /// Splice shared children only when the inlined argument list is small:
    /// flattened arity ≤ the bound. `SharedUpTo(0)` ≡ `UnsharedOnly`.
    SharedUpTo(u32),
}

/// The product policy (chosen by measurement, PORT.md T3.10 worklog table).
pub const DEFAULT_POLICY: SharingPolicy = SharingPolicy::Always;

/// Argument positions of a parent op at which a same-op child may be spliced.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum SplicePositions {
    /// Argument 0 only (the left-fold spine of a non-commutative
    /// `reduce_args` op with a never-raising fold: `Subtract`).
    First,
    /// Argument 0, plus the const-sibling rotation of `op(c, child)`
    /// (commutative never-raising folds: `Add`/`Multiply`) — module docs.
    FirstOrConstRotate,
    /// Any argument position (order-insensitive sequencing ops).
    Any,
}

/// The flattenable op set (module docs: exactness proofs + what legacy did).
fn splice_positions(op: Op) -> Option<SplicePositions> {
    match op {
        Op::Add | Op::Multiply => Some(SplicePositions::FirstOrConstRotate),
        Op::Subtract => Some(SplicePositions::First),
        Op::And | Op::Or | Op::Execute => Some(SplicePositions::Any),
        _ => None,
    }
}

/// Structural-class key, matching the output dedup's Python-dict equality
/// (constants numeric with `-0.0` ≡ `0.0`, tags ignored; functions by op +
/// child classes). Used only for the sharing analysis — NaN constants (which
/// [`crate::output`] rejects later) just key on their raw bits here.
#[derive(Debug, PartialEq, Eq, Hash)]
enum ClassKey {
    Const(u64),
    Func(Op, Vec<u32>),
}

/// Reference counts per structural class of the unflattened tree (DAG
/// in-degrees after a would-be dedup). Only computed for the sharing-aware
/// policies; `Always` never asks.
struct SharingAnalysis {
    /// Class id per arena node (reachable nodes only).
    class_of: Vec<u32>,
    /// Number of references to each class from (deduplicated) parents.
    refs: Vec<u32>,
}

impl SharingAnalysis {
    fn compute(arena: &NodeArena, root: NodeId) -> Self {
        let mut class_of: Vec<u32> = vec![u32::MAX; arena.len()];
        let mut keys: HashMap<ClassKey, u32> = HashMap::new();
        let mut refs: Vec<u32> = Vec::new();
        let mut stack: Vec<(NodeId, bool)> = vec![(root, false)];
        while let Some((id, expanded)) = stack.pop() {
            if class_of[id.index()] != u32::MAX {
                continue;
            }
            match arena.kind(id) {
                NodeKind::Const { value, .. } => {
                    let bits = if value == 0.0 { 0.0f64 } else { value }.to_bits();
                    let next = u32::try_from(refs.len()).expect("class count fits u32");
                    let class = *keys.entry(ClassKey::Const(bits)).or_insert_with(|| {
                        refs.push(0);
                        next
                    });
                    class_of[id.index()] = class;
                }
                NodeKind::Func { op, .. } => {
                    if expanded {
                        let child_classes: Vec<u32> = arena
                            .args_of(id)
                            .iter()
                            .map(|arg| class_of[arg.index()])
                            .collect();
                        let next = u32::try_from(refs.len()).expect("class count fits u32");
                        let mut inserted = false;
                        let class = *keys
                            .entry(ClassKey::Func(op, child_classes.clone()))
                            .or_insert_with(|| {
                                inserted = true;
                                refs.push(0);
                                next
                            });
                        if inserted {
                            // A NEW deduplicated parent: its argument list is
                            // this class's one set of outgoing references.
                            // Re-encountering the same class later must not
                            // recount (the dedup collapses those parents).
                            for &c in &child_classes {
                                refs[c as usize] += 1;
                            }
                        }
                        class_of[id.index()] = class;
                    } else {
                        stack.push((id, true));
                        for &arg in arena.args_of(id).iter().rev() {
                            if class_of[arg.index()] == u32::MAX {
                                stack.push((arg, false));
                            }
                        }
                    }
                }
            }
        }
        Self { class_of, refs }
    }

    fn is_shared(&self, id: NodeId) -> bool {
        self.refs[self.class_of[id.index()] as usize] > 1
    }
}

/// Per-source-node rewrite state: the flattened form, materialized lazily.
#[derive(Debug, Default, Clone)]
struct FlatNode {
    /// The node's id in the output arena, once some parent needed it whole
    /// (constants materialize eagerly).
    materialized: Option<NodeId>,
    /// `Some((op, flattened args))` for function nodes; `None` for constants.
    func: Option<(Op, Vec<NodeId>)>,
}

/// Materializes a processed node into the output arena, memoized — a child
/// both spliced into one parent and referenced whole by another materializes
/// exactly once.
fn materialize(flat: &mut [Option<FlatNode>], out: &mut NodeArena, id: NodeId) -> NodeId {
    let entry = flat[id.index()].as_ref().expect("node was processed");
    if let Some(m) = entry.materialized {
        return m;
    }
    let (op, args) = entry
        .func
        .as_ref()
        .expect("unmaterialized nodes are functions");
    let materialized = out.push_func(*op, &args.clone());
    flat[id.index()]
        .as_mut()
        .expect("entry exists")
        .materialized = Some(materialized);
    materialized
}

/// Whether the sharing policy permits splicing `child` (whose flattened
/// argument list has `flat_arity` entries).
fn policy_allows(
    policy: SharingPolicy,
    sharing: Option<&SharingAnalysis>,
    child: NodeId,
    flat_arity: usize,
) -> bool {
    let shared = || {
        sharing
            .expect("sharing analysis computed for sharing-aware policies")
            .is_shared(child)
    };
    match policy {
        SharingPolicy::Always => true,
        SharingPolicy::UnsharedOnly => !shared(),
        SharingPolicy::SharedUpTo(bound) => !shared() || flat_arity <= bound as usize,
    }
}

/// Flattens nested same-op variadic chains in an emitted engine-node tree.
/// See the module docs for the op set, exactness proofs, and the sharing
/// policy. Pure and deterministic; the input is not modified.
pub fn flatten_engine_nodes(nodes: &EngineNodes, policy: SharingPolicy) -> EngineNodes {
    let arena = &nodes.arena;
    let sharing = match policy {
        SharingPolicy::Always => None,
        SharingPolicy::UnsharedOnly | SharingPolicy::SharedUpTo(_) => {
            Some(SharingAnalysis::compute(arena, nodes.root))
        }
    };

    let mut out = NodeArena::new();
    // `None` = not yet processed (memo per source node; DAG-safe).
    let mut flat: Vec<Option<FlatNode>> = vec![None; arena.len()];
    let mut stack: Vec<(NodeId, bool)> = vec![(nodes.root, false)];
    while let Some((id, expanded)) = stack.pop() {
        if flat[id.index()].is_some() {
            continue;
        }
        match arena.kind(id) {
            NodeKind::Const { value, is_int } => {
                flat[id.index()] = Some(FlatNode {
                    materialized: Some(out.push_const(value, is_int)),
                    func: None,
                });
            }
            NodeKind::Func { op, .. } => {
                let args = arena.args_of(id);
                if !expanded {
                    stack.push((id, true));
                    for &arg in args.iter().rev() {
                        if flat[arg.index()].is_none() {
                            stack.push((arg, false));
                        }
                    }
                    continue;
                }
                let positions = splice_positions(op);
                let mut flat_args: Vec<NodeId> = Vec::with_capacity(args.len());
                for (i, &child) in args.iter().enumerate() {
                    // `rotate`: the proven-exact const-sibling case
                    // `op(c, child)` → `op(child's args…, c)` (module docs).
                    // Everything accumulated so far (exactly one entry, by
                    // the binary-parent guard) must be a non-NaN literal.
                    let mut rotate = false;
                    let position_ok = match positions {
                        Some(SplicePositions::Any) => true,
                        Some(SplicePositions::First) => i == 0,
                        Some(SplicePositions::FirstOrConstRotate) => {
                            rotate = i == 1
                                && args.len() == 2
                                && flat_args.len() == 1
                                && matches!(
                                    out.kind(flat_args[0]),
                                    NodeKind::Const { value, .. } if !value.is_nan()
                                );
                            i == 0 || rotate
                        }
                        None => false,
                    };
                    let child_flat = flat[child.index()]
                        .as_ref()
                        .expect("children are processed before parents");
                    if position_ok
                        && let Some((child_op, child_args)) = &child_flat.func
                        && *child_op == op
                        && !child_args.is_empty()
                        && policy_allows(policy, sharing.as_ref(), child, child_args.len())
                    {
                        if rotate {
                            // [c] -> [child args…, c].
                            let c = flat_args[0];
                            flat_args.clear();
                            flat_args.extend_from_slice(child_args);
                            flat_args.push(c);
                        } else {
                            flat_args.extend_from_slice(child_args);
                        }
                        continue;
                    }
                    let materialized = materialize(&mut flat, &mut out, child);
                    flat_args.push(materialized);
                }
                flat[id.index()] = Some(FlatNode {
                    materialized: None,
                    func: Some((op, flat_args)),
                });
            }
        }
    }

    // Materialize the root (it is never spliced into anything).
    let root = materialize(&mut flat, &mut out, nodes.root);
    EngineNodes { arena: out, root }
}

#[cfg(test)]
mod tests {
    // Exact f64 assertions are the ported semantics contract (ARCHITECTURE §6).
    #![allow(clippy::float_cmp)]

    use super::*;
    use crate::interpret::Interpreter;
    use crate::nodes::{format_engine_node, tree_node_count};

    fn fmt(nodes: &EngineNodes) -> String {
        format_engine_node(&nodes.arena, nodes.root)
    }

    fn flat(nodes: &EngineNodes) -> EngineNodes {
        flatten_engine_nodes(nodes, SharingPolicy::Always)
    }

    /// Builds `op(op(op(a, b), c), d)` — a left-nested chain.
    fn left_chain(op: Op) -> EngineNodes {
        let mut arena = NodeArena::new();
        let a = arena.push_int(1.0);
        let b = arena.push_int(2.0);
        let c = arena.push_int(3.0);
        let d = arena.push_int(4.0);
        let n1 = arena.push_func(op, &[a, b]);
        let n2 = arena.push_func(op, &[n1, c]);
        let root = arena.push_func(op, &[n2, d]);
        EngineNodes { arena, root }
    }

    /// Builds `op(a, op(b, op(c, d)))` — a right-nested chain.
    fn right_chain(op: Op) -> EngineNodes {
        let mut arena = NodeArena::new();
        let a = arena.push_int(1.0);
        let b = arena.push_int(2.0);
        let c = arena.push_int(3.0);
        let d = arena.push_int(4.0);
        let n1 = arena.push_func(op, &[c, d]);
        let n2 = arena.push_func(op, &[b, n1]);
        let root = arena.push_func(op, &[a, n2]);
        EngineNodes { arena, root }
    }

    #[test]
    fn add_left_spine_flattens_fully() {
        let flatd = flat(&left_chain(Op::Add));
        assert_eq!(fmt(&flatd), "Add(\n  1\n  2\n  3\n  4\n)");
        assert_eq!(tree_node_count(&flatd.arena, flatd.root), 5);
    }

    #[test]
    fn multiply_left_spine_flattens_fully() {
        let flatd = flat(&left_chain(Op::Multiply));
        assert_eq!(fmt(&flatd), "Multiply(\n  1\n  2\n  3\n  4\n)");
    }

    #[test]
    fn subtract_left_spine_flattens_but_never_rotates() {
        // Sub(Sub(Sub(1,2),3),4) → Sub(1,2,3,4): exact left fold.
        let flatd = flat(&left_chain(Op::Subtract));
        assert_eq!(fmt(&flatd), "Subtract(\n  1\n  2\n  3\n  4\n)");
        let mut interp = Interpreter::new(0);
        assert_eq!(interp.run(&flatd).unwrap(), -8.0);
        // Sub(1, Sub(2, Sub(3,4))): non-commutative — the const-sibling
        // rotation must NOT apply (1 - (2 - x) is not (2 - x) - 1).
        let nodes = right_chain(Op::Subtract);
        let flatd = flat(&nodes);
        assert_eq!(fmt(&flatd), fmt(&nodes));
    }

    #[test]
    fn add_right_spine_is_not_flattened_for_non_const_siblings() {
        // Add(load, Add(load, load)): splicing at position 1 would
        // reassociate `a + (b + c)` — refused (the rotation applies only to
        // a literal-const sibling).
        let mut arena = NodeArena::new();
        let load = |arena: &mut NodeArena, i: f64| {
            let blk = arena.push_int(-3.0);
            let idx = arena.push_int(i);
            arena.push_func(Op::Get, &[blk, idx])
        };
        let a = load(&mut arena, 0.0);
        let b = load(&mut arena, 1.0);
        let c = load(&mut arena, 2.0);
        let inner = arena.push_func(Op::Add, &[b, c]);
        let root = arena.push_func(Op::Add, &[a, inner]);
        let nodes = EngineNodes { arena, root };
        let flatd = flat(&nodes);
        assert_eq!(fmt(&flatd), fmt(&nodes));
    }

    #[test]
    fn const_seeded_right_chains_rotate_recursively() {
        // Add(1, Add(2, Add(3, 4))): every level is the const-sibling shape,
        // so rotation flattens the whole chain (each step bit-exact by
        // commutativity; values here are exact integers regardless).
        let flatd = flat(&right_chain(Op::Add));
        assert_eq!(fmt(&flatd), "Add(\n  3\n  4\n  2\n  1\n)");
        let mut interp = Interpreter::new(0);
        assert_eq!(interp.run(&flatd).unwrap(), 10.0);
    }

    #[test]
    fn mod_and_rem_are_not_flattened() {
        // Deliberately dropped from the legacy op set: the % fold can raise,
        // and reduce_args collects all arguments before folding, so splicing
        // would move the raise past later arguments' side effects (module
        // docs). Pinned behaviorally below.
        for op in [Op::Mod, Op::Rem] {
            let nodes = left_chain(op);
            let flatd = flat(&nodes);
            assert_eq!(fmt(&flatd), fmt(&nodes), "{op:?} must stay nested");
        }
    }

    #[test]
    fn mod_raise_point_motivates_the_refusal() {
        // Mod(Mod(7, 0), DebugLog(5)): the nested form raises ZeroDivision
        // BEFORE evaluating (and logging) the third argument. A flattened
        // Mod(7, 0, DebugLog(5)) would log first and raise after — observable
        // via the log. The transform must leave the tree alone.
        let mut arena = NodeArena::new();
        let seven = arena.push_int(7.0);
        let zero = arena.push_int(0.0);
        let inner = arena.push_func(Op::Mod, &[seven, zero]);
        let five = arena.push_int(5.0);
        let log = arena.push_func(Op::DebugLog, &[five]);
        let root = arena.push_func(Op::Mod, &[inner, log]);
        let nodes = EngineNodes { arena, root };
        let flatd = flat(&nodes);
        assert_eq!(fmt(&flatd), fmt(&nodes));
        let mut interp = Interpreter::new(0);
        let err = interp.run(&flatd).expect_err("Mod by zero raises");
        assert!(
            err.to_string().contains("division by zero"),
            "unexpected: {err}"
        );
        assert!(
            interp.log().is_empty(),
            "the raise must precede the third argument"
        );
    }

    #[test]
    fn const_sibling_rotation_flattens_the_gvn_shape() {
        // Add(5, Add(Add(a, b), c)) — the canonical post-GVN `a + b + c + 5`
        // shape — rotates to Add(a, b, c, 5). Bit-exactness pinned on values
        // where any *other* reassociation would change the result.
        let mut arena = NodeArena::new();
        let five = arena.push_int(5.0);
        let tenth = arena.push_float(0.1);
        let big = arena.push_float(1e16);
        let inner1 = arena.push_func(Op::Add, &[tenth, big]);
        let neg_big = arena.push_float(-1e16);
        let inner2 = arena.push_func(Op::Add, &[inner1, neg_big]);
        let root = arena.push_func(Op::Add, &[five, inner2]);
        let nodes = EngineNodes { arena, root };
        let flatd = flat(&nodes);
        assert_eq!(fmt(&flatd), "Add(\n  0.1\n  1e16\n  -1e16\n  5\n)");
        let mut nested_interp = Interpreter::new(0);
        let nested_result = nested_interp.run(&nodes).unwrap();
        let mut flat_interp = Interpreter::new(0);
        let flat_result = flat_interp.run(&flatd).unwrap();
        assert_eq!(
            nested_result.to_bits(),
            flat_result.to_bits(),
            "rotation must be bit-exact"
        );
        assert!(flat_interp.eval_count() < nested_interp.eval_count());
    }

    #[test]
    fn rotation_requires_a_literal_const_sibling() {
        // Add(Get(...), Add(a, b)): position-1 splice with a non-const
        // sibling would reassociate — must stay nested.
        let mut arena = NodeArena::new();
        let blk = arena.push_int(-3.0);
        let idx = arena.push_int(0.0);
        let load = arena.push_func(Op::Get, &[blk, idx]);
        let one = arena.push_int(1.0);
        let two = arena.push_int(2.0);
        let inner = arena.push_func(Op::Add, &[one, two]);
        let root = arena.push_func(Op::Add, &[load, inner]);
        let nodes = EngineNodes { arena, root };
        let flatd = flat(&nodes);
        assert_eq!(fmt(&flatd), fmt(&nodes));
        // A NaN const sibling (unreachable post-emit; defensive) also refuses.
        let mut arena = NodeArena::new();
        let nan = arena.push_float(f64::NAN);
        let one = arena.push_int(1.0);
        let two = arena.push_int(2.0);
        let inner = arena.push_func(Op::Add, &[one, two]);
        let root = arena.push_func(Op::Add, &[nan, inner]);
        let nodes = EngineNodes { arena, root };
        let flatd = flat(&nodes);
        assert_eq!(fmt(&flatd), fmt(&nodes));
    }

    #[test]
    fn and_or_flatten_at_any_position() {
        for op in [Op::And, Op::Or] {
            let left = flat(&left_chain(op));
            let right = flat(&right_chain(op));
            let expected = format!("{}(\n  1\n  2\n  3\n  4\n)", op.name());
            assert_eq!(fmt(&left), expected, "{op:?} left spine");
            assert_eq!(fmt(&right), expected, "{op:?} right spine");
        }
    }

    #[test]
    fn execute_flattens_at_any_position_including_interior() {
        // Execute(Execute(a, b), c, Execute(d, e)) → Execute(a, b, c, d, e).
        let mut arena = NodeArena::new();
        let one = arena.push_int(1.0);
        let two = arena.push_int(2.0);
        let three = arena.push_int(3.0);
        let four = arena.push_int(4.0);
        let five = arena.push_int(5.0);
        let first = arena.push_func(Op::Execute, &[one, two]);
        let last = arena.push_func(Op::Execute, &[four, five]);
        let root = arena.push_func(Op::Execute, &[first, three, last]);
        let nodes = EngineNodes { arena, root };
        let flatd = flat(&nodes);
        assert_eq!(fmt(&flatd), "Execute(\n  1\n  2\n  3\n  4\n  5\n)");
        // Value = the last argument of the last (spliced) child.
        let mut interp = Interpreter::new(0);
        assert_eq!(interp.run(&flatd).unwrap(), 5.0);
    }

    #[test]
    fn mixed_op_boundaries_are_not_flattened() {
        // Add child of Multiply, Execute child of Execute0, And child of Or:
        // only child.op == parent.op splices.
        let mut arena = NodeArena::new();
        let a = arena.push_int(2.0);
        let b = arena.push_int(3.0);
        let add = arena.push_func(Op::Add, &[a, b]);
        let c = arena.push_int(4.0);
        let mul = arena.push_func(Op::Multiply, &[add, c]);
        let exec = arena.push_func(Op::Execute, &[mul]);
        let root = arena.push_func(Op::Execute0, &[exec]);
        let nodes = EngineNodes { arena, root };
        let flatd = flat(&nodes);
        assert_eq!(fmt(&flatd), fmt(&nodes));
    }

    #[test]
    fn empty_children_are_never_spliced() {
        // Execute(7, Execute()) evaluates to 0.0 (the empty child's value);
        // splicing would change the result to 7.
        let mut arena = NodeArena::new();
        let seven = arena.push_int(7.0);
        let empty = arena.push_func(Op::Execute, &[]);
        let root = arena.push_func(Op::Execute, &[seven, empty]);
        let nodes = EngineNodes { arena, root };
        let flatd = flat(&nodes);
        assert_eq!(fmt(&flatd), fmt(&nodes));
        let mut interp = Interpreter::new(0);
        assert_eq!(interp.run(&flatd).unwrap(), 0.0);
    }

    #[test]
    fn single_argument_children_splice_exactly() {
        // Add(Add(a), b): the inner single-argument reduce returns `a`
        // unchanged, so Add(a, b) is exact (never emitted, but total).
        let mut arena = NodeArena::new();
        let a = arena.push_int(5.0);
        let inner = arena.push_func(Op::Add, &[a]);
        let b = arena.push_int(3.0);
        let root = arena.push_func(Op::Add, &[inner, b]);
        let nodes = EngineNodes { arena, root };
        let flatd = flat(&nodes);
        assert_eq!(fmt(&flatd), "Add(\n  5\n  3\n)");
        let mut interp = Interpreter::new(0);
        assert_eq!(interp.run(&flatd).unwrap(), 8.0);
    }

    #[test]
    fn const_tags_and_values_are_preserved() {
        // Int/float tags and -0.0 survive verbatim (first-encounter tag
        // semantics in the output dedup depend on them).
        let mut arena = NodeArena::new();
        let i5 = arena.push_int(5.0);
        let f5 = arena.push_float(5.0);
        let nz = arena.push_float(-0.0);
        let inner = arena.push_func(Op::Add, &[i5, f5]);
        let root = arena.push_func(Op::Add, &[inner, nz]);
        let nodes = EngineNodes { arena, root };
        let flatd = flat(&nodes);
        assert_eq!(fmt(&flatd), "Add(\n  5\n  5.0\n  -0.0\n)");
        let dump_before = crate::output::output_node_dump(
            &crate::output::generate_output_nodes(&nodes.arena, nodes.root).unwrap(),
        );
        let dump_after = crate::output::output_node_dump(
            &crate::output::generate_output_nodes(&flatd.arena, flatd.root).unwrap(),
        );
        // The interior Add disappears; the constants' bits/tags are unchanged.
        assert!(dump_before.contains("v i 0x4014000000000000"));
        assert!(dump_after.contains("v i 0x4014000000000000"));
        assert!(dump_after.contains("v f 0x8000000000000000"));
        assert_eq!(dump_after.matches("f Add").count(), 1);
    }

    #[test]
    fn and_short_circuit_behavior_is_identical() {
        // And(in0, And(Add(DebugLog(1), 1), DebugLog(2))): flattened and
        // nested forms must produce identical logs and results for inputs
        // that stop at every possible position.
        fn build() -> EngineNodes {
            let mut arena = NodeArena::new();
            let blk = arena.push_int(-3.0);
            let idx = arena.push_int(0.0);
            let in0 = arena.push_func(Op::Get, &[blk, idx]);
            let c1 = arena.push_int(1.0);
            let log1 = arena.push_func(Op::DebugLog, &[c1]);
            let one = arena.push_int(1.0);
            let arm1 = arena.push_func(Op::Add, &[log1, one]);
            let c2 = arena.push_int(2.0);
            let log2 = arena.push_func(Op::DebugLog, &[c2]);
            let inner = arena.push_func(Op::And, &[arm1, log2]);
            let root = arena.push_func(Op::And, &[in0, inner]);
            EngineNodes { arena, root }
        }
        let nodes = build();
        let flatd = flat(&nodes);
        assert_eq!(
            fmt(&flatd),
            "And(\n  Get(\n    -3\n    0\n  )\n  Add(\n    DebugLog(1)\n    1\n  )\n  DebugLog(2)\n)"
        );
        for input in [0.0, 1.0] {
            let mut a = Interpreter::new(0);
            a.set_block(-3, vec![input]);
            let ra = a.run(&nodes).unwrap();
            let mut b = Interpreter::new(0);
            b.set_block(-3, vec![input]);
            let rb = b.run(&flatd).unwrap();
            assert_eq!(ra, rb, "input {input}");
            assert_eq!(a.log(), b.log(), "input {input}");
            if input == 0.0 {
                // Short-circuits at position 0: the spliced inner And was
                // never evaluated in the nested form either.
                assert_eq!(b.eval_count(), a.eval_count(), "input {input}");
            } else {
                assert!(
                    b.eval_count() < a.eval_count(),
                    "input {input}: flattening must save evaluations"
                );
            }
        }
    }

    #[test]
    fn shared_subtree_policies() {
        // S = Add(a, b) is structurally shared by two distinct flattenable
        // parents: Add(S, c) and Add(S, d), both under one Execute.
        fn build() -> EngineNodes {
            let mut arena = NodeArena::new();
            let a1 = arena.push_int(1.0);
            let b1 = arena.push_int(2.0);
            let s1 = arena.push_func(Op::Add, &[a1, b1]);
            let c = arena.push_int(3.0);
            let p1 = arena.push_func(Op::Add, &[s1, c]);
            let a2 = arena.push_int(1.0);
            let b2 = arena.push_int(2.0);
            let s2 = arena.push_func(Op::Add, &[a2, b2]);
            let d = arena.push_int(4.0);
            let p2 = arena.push_func(Op::Add, &[s2, d]);
            let root = arena.push_func(Op::Execute, &[p1, p2]);
            EngineNodes { arena, root }
        }
        let nodes = build();
        // Always: both parents inline the shared chain.
        let always = flatten_engine_nodes(&nodes, SharingPolicy::Always);
        assert_eq!(
            fmt(&always),
            "Execute(\n  Add(\n    1\n    2\n    3\n  )\n  Add(\n    1\n    2\n    4\n  )\n)"
        );
        // UnsharedOnly: S has two deduplicated references — kept nested.
        let unshared = flatten_engine_nodes(&nodes, SharingPolicy::UnsharedOnly);
        assert_eq!(fmt(&unshared), fmt(&nodes));
        // SharedUpTo(2): the duplication (2 inlined args) is within bounds.
        let bounded = flatten_engine_nodes(&nodes, SharingPolicy::SharedUpTo(2));
        assert_eq!(fmt(&bounded), fmt(&always));
        // SharedUpTo(1): too large — kept nested.
        let tight = flatten_engine_nodes(&nodes, SharingPolicy::SharedUpTo(1));
        assert_eq!(fmt(&tight), fmt(&nodes));
        // Structurally identical *whole chains* still collapse under Always:
        // dag size shrinks (never grows) for every policy.
        for policy in [
            SharingPolicy::Always,
            SharingPolicy::UnsharedOnly,
            SharingPolicy::SharedUpTo(2),
        ] {
            let flatd = flatten_engine_nodes(&nodes, policy);
            let before = crate::output::generate_output_nodes(&nodes.arena, nodes.root)
                .unwrap()
                .nodes
                .len();
            let after = crate::output::generate_output_nodes(&flatd.arena, flatd.root)
                .unwrap()
                .nodes
                .len();
            assert!(after <= before, "{policy:?}: dag must not grow");
        }
    }

    #[test]
    fn unshared_only_still_flattens_unshared_chains() {
        let flatd = flatten_engine_nodes(&left_chain(Op::Add), SharingPolicy::UnsharedOnly);
        assert_eq!(fmt(&flatd), "Add(\n  1\n  2\n  3\n  4\n)");
    }

    #[test]
    fn identical_chains_collapse_after_flattening() {
        // The same chain twice: flattening keeps them structurally identical,
        // so the output dedup still collapses them onto one node.
        let mut arena = NodeArena::new();
        let mk = |arena: &mut NodeArena| {
            let a = arena.push_int(1.0);
            let b = arena.push_int(2.0);
            let inner = arena.push_func(Op::Add, &[a, b]);
            let c = arena.push_int(3.0);
            arena.push_func(Op::Add, &[inner, c])
        };
        let x = mk(&mut arena);
        let y = mk(&mut arena);
        let root = arena.push_func(Op::Execute, &[x, y]);
        let nodes = EngineNodes { arena, root };
        let flatd = flat(&nodes);
        let out = crate::output::generate_output_nodes(&flatd.arena, flatd.root).unwrap();
        // 1, 2, 3, Add(1,2,3) shared, Execute — five nodes.
        assert_eq!(out.nodes.len(), 5);
    }

    #[test]
    fn emit_orphans_are_dropped_and_dag_subtrees_handled() {
        // An arena with an unreachable node (emit's FinishSet leaves these)
        // and a genuinely DAG-shaped reachable region (one node referenced by
        // two parents *by id*).
        let mut arena = NodeArena::new();
        let dead = arena.push_int(9.0);
        let _orphan = arena.push_func(Op::Abs, &[dead]);
        let a = arena.push_int(1.0);
        let b = arena.push_int(2.0);
        let shared = arena.push_func(Op::Add, &[a, b]);
        let c = arena.push_int(3.0);
        let p1 = arena.push_func(Op::Add, &[shared, c]);
        let p2 = arena.push_func(Op::Multiply, &[shared, c]);
        let root = arena.push_func(Op::Execute, &[p1, p2]);
        let nodes = EngineNodes { arena, root };
        let flatd = flat(&nodes);
        // p1 splices the shared Add (position 0, same op); p2 keeps it whole
        // (Multiply boundary). The shared node materializes exactly once.
        assert_eq!(
            fmt(&flatd),
            "Execute(\n  Add(\n    1\n    2\n    3\n  )\n  Multiply(\n    Add(\n      1\n      2\n    )\n    3\n  )\n)"
        );
        // Orphans are gone: every node in the output arena is reachable
        // (1, 2, 3, the whole Add(1,2) for p2, Add(1,2,3), Multiply, Execute).
        assert_eq!(flatd.arena.len(), 7);
    }

    #[test]
    fn deep_chain_is_iterative_and_linear() {
        // 200k-deep left-nested Add chain: must not overflow the thread stack
        // and must produce one wide node.
        let mut arena = NodeArena::new();
        let mut node = arena.push_int(0.0);
        let depth = 200_000usize;
        for _ in 0..depth {
            let one = arena.push_int(1.0);
            node = arena.push_func(Op::Add, &[node, one]);
        }
        let nodes = EngineNodes { arena, root: node };
        let flatd = flat(&nodes);
        assert_eq!(flatd.arena.args_of(flatd.root).len(), depth + 1);
        assert_eq!(tree_node_count(&flatd.arena, flatd.root), depth as u64 + 2);
    }

    #[test]
    fn deterministic_output() {
        let nodes = left_chain(Op::Add);
        let a = flat(&nodes);
        let b = flat(&nodes);
        assert_eq!(fmt(&a), fmt(&b));
        let out_a = crate::output::generate_output_nodes(&a.arena, a.root).unwrap();
        let out_b = crate::output::generate_output_nodes(&b.arena, b.root).unwrap();
        assert_eq!(
            crate::output::output_node_dump(&out_a),
            crate::output::output_node_dump(&out_b)
        );
    }

    // ===== Emission e2e (pipeline wiring; PORT.md T3.10 DoD) =====

    use crate::cfg::{BasicBlock, BlockValue, Cfg, IndexValue, Node, Place};
    use crate::passes::{Pipeline, passes_for_level};
    use crate::pipeline::{Level, compile_cfg, compile_cfg_with_pipeline};

    /// Frontend CFG shaped like a real trace:
    /// `20[0] <- Add(Add(Add(Add(in[0], in[1]), in[2]), in[3]), 5)` built in
    /// Python evaluation order (binary nested adds with a constant tail — the
    /// shape GVN canonicalizes to `Add(5, chain)`, exercising both the
    /// position-0 splice and the const-sibling rotation) and
    /// `20[1] <- And(in[0], in[1], in[2])` (n-ary short-circuit, binarized
    /// right-associatively at MIR build).
    fn chain_cfg() -> Cfg {
        let mut cfg = Cfg::default();
        let node = |cfg: &mut Cfg, n: Node| {
            cfg.nodes.push(n);
            cfg.nodes.len() - 1
        };
        let get_in = |cfg: &mut Cfg, i: i64| {
            cfg.places.push(Place {
                block: BlockValue::Int(-3),
                index: IndexValue::Int(i),
                offset: 0,
            });
            let p = cfg.places.len() - 1;
            cfg.nodes.push(Node::Get(p));
            cfg.nodes.len() - 1
        };
        // Binary left-nested chain in trace order (load, then fold).
        let mut acc = get_in(&mut cfg, 0);
        for i in 1..4 {
            let next = get_in(&mut cfg, i);
            acc = node(
                &mut cfg,
                Node::PureInstr {
                    op: Op::Add,
                    args: vec![acc, next],
                },
            );
        }
        let five = node(&mut cfg, Node::ConstInt(5));
        let add = node(
            &mut cfg,
            Node::PureInstr {
                op: Op::Add,
                args: vec![acc, five],
            },
        );
        cfg.places.push(Place {
            block: BlockValue::Int(20),
            index: IndexValue::Int(0),
            offset: 0,
        });
        let out0 = cfg.places.len() - 1;
        let set0 = node(
            &mut cfg,
            Node::Set {
                place: out0,
                value: add,
            },
        );
        let ands: Vec<usize> = (0..3).map(|i| get_in(&mut cfg, i)).collect();
        let and = node(
            &mut cfg,
            Node::PureInstr {
                op: Op::And,
                args: ands,
            },
        );
        cfg.places.push(Place {
            block: BlockValue::Int(20),
            index: IndexValue::Int(1),
            offset: 0,
        });
        let out1 = cfg.places.len() - 1;
        let set1 = node(
            &mut cfg,
            Node::Set {
                place: out1,
                value: and,
            },
        );
        let zero = node(&mut cfg, Node::ConstInt(0));
        cfg.blocks.push(BasicBlock {
            statements: vec![set0, set1],
            test: zero,
            outgoing: vec![],
        });
        cfg
    }

    fn run_metrics(nodes: &EngineNodes, inputs: &[f64]) -> (f64, Vec<f64>, u64) {
        let mut interp = Interpreter::new(0);
        interp.set_block(-3, inputs.to_vec());
        let result = interp.run(nodes).expect("runs clean");
        (
            result,
            interp.block(20).unwrap().to_vec(),
            interp.eval_count(),
        )
    }

    #[test]
    fn standard_pipeline_flattens_and_drops_node_and_eval_counts() {
        let cfg = chain_cfg();
        let standard_passes = || Pipeline::new(passes_for_level(Level::Standard));
        let unflattened =
            compile_cfg_with_pipeline(&cfg, &standard_passes()).expect("compiles unflattened");
        let flattened = compile_cfg_with_pipeline(&cfg, &standard_passes().with_flatten(true))
            .expect("compiles flattened");

        // compile_cfg(Standard) IS the flattened variant, byte for byte.
        let level = compile_cfg(&cfg, Level::Standard).expect("standard compiles");
        assert_eq!(fmt(&level), fmt(&flattened));
        assert_ne!(fmt(&level), fmt(&unflattened));

        // The whole Add chain (incl. the GVN-rotated constant tail) collapsed
        // onto a single wide node, and the And chain onto one And.
        assert_eq!(fmt(&flattened).matches("Add(").count(), 1);
        assert!(fmt(&unflattened).matches("Add(").count() >= 4);
        assert_eq!(fmt(&flattened).matches("And(").count(), 1);

        // Static node count and dag size drop (3 Add splices + 2 And splices
        // worth of interior nodes, whatever the optimizer leaves of them).
        let static_before = tree_node_count(&unflattened.arena, unflattened.root);
        let static_after = tree_node_count(&flattened.arena, flattened.root);
        assert!(
            static_after < static_before,
            "static nodes must drop: {static_before} -> {static_after}"
        );
        let dag_before = crate::output::generate_output_nodes(&unflattened.arena, unflattened.root)
            .unwrap()
            .nodes
            .len();
        let dag_after = crate::output::generate_output_nodes(&flattened.arena, flattened.root)
            .unwrap()
            .nodes
            .len();
        assert!(
            dag_after < dag_before,
            "dag size must drop: {dag_before} -> {dag_after}"
        );

        // Behavior identical, dynamic evals drop.
        // Behavior identical (incl. the rotated fold being bit-exact on
        // non-associative-friendly floats), dynamic evals drop everywhere —
        // the Add chain executes unconditionally.
        for inputs in [
            [1.0, 2.0, 3.0, 4.0],
            [0.0, 2.0, 3.0, 4.0],
            [1.0, 0.0, 3.0, 4.0],
            [0.1, 1e16, -1e16, 0.2],
        ] {
            let (r_before, w_before, e_before) = run_metrics(&unflattened, &inputs);
            let (r_after, w_after, e_after) = run_metrics(&flattened, &inputs);
            assert_eq!(r_before, r_after, "{inputs:?}");
            assert_eq!(w_before, w_after, "{inputs:?}");
            assert!(
                e_after < e_before,
                "{inputs:?}: eval count must drop ({e_before} -> {e_after})"
            );
        }
    }

    #[test]
    fn minimal_and_fast_do_not_flatten() {
        let cfg = chain_cfg();
        // minimal: identical to the explicit empty pipeline (which never
        // flattens) — the differential baseline stays untouched.
        let minimal = compile_cfg(&cfg, Level::Minimal).expect("minimal compiles");
        let empty = compile_cfg_with_pipeline(&cfg, &Pipeline::new(vec![])).expect("compiles");
        assert_eq!(fmt(&minimal), fmt(&empty));
        // The binary chains are still nested at minimal (4 Adds, 2 Ands)...
        assert_eq!(fmt(&minimal).matches("Add(").count(), 4);
        assert_eq!(fmt(&minimal).matches("And(").count(), 2);
        // ...and at fast (the W1 prefix, no flattening).
        let fast = compile_cfg(&cfg, Level::Fast).expect("fast compiles");
        let fast_explicit =
            compile_cfg_with_pipeline(&cfg, &Pipeline::new(passes_for_level(Level::Fast)))
                .expect("compiles");
        assert_eq!(fmt(&fast), fmt(&fast_explicit));
        assert!(fmt(&fast).matches("Add(").count() >= 4);
    }
}
