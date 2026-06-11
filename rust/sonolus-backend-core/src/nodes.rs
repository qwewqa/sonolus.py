//! Engine-node arena, mirroring `sonolus/backend/node.py`.
//!
//! Python's `EngineNode` is `int | float | FunctionNode(func, args)`. Here nodes live in
//! a [`NodeArena`]: a flat `Vec` of [`NodeKind`] values addressed by [`NodeId`], with
//! argument lists stored contiguously in a shared side table. This removes all pointer
//! chasing and — together with the explicit work stacks used by the interpreter and
//! formatter — upholds the no-recursion invariant for user-sized node trees.
//!
//! # Int/float tag
//!
//! At runtime every node value is an `f64`; the interpreter ignores the tag entirely.
//! The `is_int` tag on [`NodeKind::Const`] records whether the constant was a Python
//! `int` (vs `float`), which is load-bearing for *output only*: the engine-data emitter
//! (task T1.2) must serialize int-tagged constants as JSON integers (`5`, not `5.0`),
//! matching the legacy backend where integral constants are Python `int` objects.
//! Constants are stored as `f64`, so int-tagged values beyond 2^53 lose precision
//! (Python's arbitrary-precision ints cannot produce such values through the legacy
//! emitter, which round-trips through `float`).

use std::fmt::Write as _;

use crate::ops::Op;

/// Index of a node in a [`NodeArena`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct NodeId(u32);

impl NodeId {
    /// The index of this node in its arena.
    pub fn index(self) -> usize {
        self.0 as usize
    }
}

/// A single engine node: a tagged constant or a function application.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum NodeKind {
    /// A numeric constant. See the module docs for the meaning of `is_int`.
    Const { value: f64, is_int: bool },
    /// A function node; its arguments are `arena.args_of(id)`.
    Func {
        op: Op,
        args_start: u32,
        args_len: u32,
    },
}

/// Arena holding a forest of engine nodes.
#[derive(Debug, Default, Clone)]
pub struct NodeArena {
    nodes: Vec<NodeKind>,
    args: Vec<NodeId>,
}

impl NodeArena {
    pub fn new() -> Self {
        Self::default()
    }

    /// Number of nodes in the arena.
    pub fn len(&self) -> usize {
        self.nodes.len()
    }

    pub fn is_empty(&self) -> bool {
        self.nodes.is_empty()
    }

    /// Adds a constant node with an explicit int/float tag.
    pub fn push_const(&mut self, value: f64, is_int: bool) -> NodeId {
        self.push_kind(NodeKind::Const { value, is_int })
    }

    /// Adds an int-tagged constant (Python `int`).
    pub fn push_int(&mut self, value: f64) -> NodeId {
        self.push_const(value, true)
    }

    /// Adds a float-tagged constant (Python `float`).
    pub fn push_float(&mut self, value: f64) -> NodeId {
        self.push_const(value, false)
    }

    /// Adds a function node. The argument list is copied into the arena.
    pub fn push_func(&mut self, op: Op, args: &[NodeId]) -> NodeId {
        let args_start = u32::try_from(self.args.len()).expect("node arena argument overflow");
        let args_len = u32::try_from(args.len()).expect("node argument list too long");
        self.args.extend_from_slice(args);
        self.push_kind(NodeKind::Func {
            op,
            args_start,
            args_len,
        })
    }

    fn push_kind(&mut self, kind: NodeKind) -> NodeId {
        let id = u32::try_from(self.nodes.len()).expect("node arena overflow");
        self.nodes.push(kind);
        NodeId(id)
    }

    /// The kind of a node.
    pub fn kind(&self, id: NodeId) -> NodeKind {
        self.nodes[id.index()]
    }

    /// The argument list of a node (empty for constants).
    pub fn args_of(&self, id: NodeId) -> &[NodeId] {
        match self.kind(id) {
            NodeKind::Const { .. } => &[],
            NodeKind::Func {
                args_start,
                args_len,
                ..
            } => &self.args[args_start as usize..(args_start + args_len) as usize],
        }
    }
}

/// A complete engine-node tree: an arena plus its root node.
///
/// This is what the emitter (T1.2) produces and what the interpreter consumes.
#[derive(Debug, Clone)]
pub struct EngineNodes {
    pub arena: NodeArena,
    pub root: NodeId,
}

/// Total node count of the **tree** rooted at `root`, counting a shared arena
/// node once per occurrence (the `static_nodes` metric of PORT.md T2.4 —
/// pre-DAG-dedup; the legacy backend materializes the same count as a strict
/// tree of Python objects). Memoized per arena node and saturating, so a
/// DAG-shaped arena is counted in linear time even when its tree expansion
/// would overflow. Iterative (explicit work stack); arenas are acyclic by
/// construction (`push_func` arguments always precede the node).
pub fn tree_node_count(arena: &NodeArena, root: NodeId) -> u64 {
    // 0 = not yet computed (every real count is >= 1).
    let mut counts: Vec<u64> = vec![0; arena.len()];
    let mut stack: Vec<(NodeId, bool)> = vec![(root, false)];
    while let Some((id, ready)) = stack.pop() {
        if counts[id.index()] != 0 {
            continue;
        }
        if ready {
            let mut total: u64 = 1;
            for &arg in arena.args_of(id) {
                total = total.saturating_add(counts[arg.index()]);
            }
            counts[id.index()] = total;
        } else {
            stack.push((id, true));
            for &arg in arena.args_of(id) {
                if counts[arg.index()] == 0 {
                    stack.push((arg, false));
                }
            }
        }
    }
    counts[root.index()]
}

/// Formats an engine node like `sonolus.backend.node.format_engine_node`.
///
/// Layout matches the Python reference exactly: zero-arg functions render as
/// `Name()`, single-arg functions render inline, and multi-arg functions render one
/// argument per line with two-space indentation. Constant formatting follows decision
/// D7 (Rust-native float formatting, no Python-`repr` compatibility): int-tagged
/// integral constants render without a fractional part (`5`), float constants use
/// Rust's shortest-roundtrip form (`5.0`, `1.5`; `NaN`/`inf` differ from Python's
/// `nan`/`inf` spelling).
///
/// Iterative (explicit work stack): node trees are user-sized and may be deeper than
/// any thread stack.
pub fn format_engine_node(arena: &NodeArena, root: NodeId) -> String {
    enum Job {
        Node(NodeId, u32),
        Str(&'static str),
        /// Newline followed by `2 * depth` spaces.
        Newline(u32),
    }

    let mut out = String::new();
    let mut stack = vec![Job::Node(root, 0)];
    while let Some(job) = stack.pop() {
        match job {
            Job::Str(s) => out.push_str(s),
            Job::Newline(depth) => {
                out.push('\n');
                for _ in 0..depth * 2 {
                    out.push(' ');
                }
            }
            Job::Node(id, depth) => match arena.kind(id) {
                NodeKind::Const { value, is_int } => write_const(&mut out, value, is_int),
                NodeKind::Func { op, .. } => {
                    let args = arena.args_of(id);
                    out.push_str(op.name());
                    match args.len() {
                        0 => out.push_str("()"),
                        1 => {
                            out.push('(');
                            stack.push(Job::Str(")"));
                            stack.push(Job::Node(args[0], depth));
                        }
                        _ => {
                            out.push('(');
                            stack.push(Job::Str(")"));
                            stack.push(Job::Newline(depth));
                            for &arg in args.iter().rev() {
                                stack.push(Job::Node(arg, depth + 1));
                                stack.push(Job::Newline(depth + 1));
                            }
                        }
                    }
                }
            },
        }
    }
    out
}

// float_cmp: exact integrality check, deliberate.
#[allow(clippy::cast_possible_truncation, clippy::float_cmp)]
fn write_const(out: &mut String, value: f64, is_int: bool) {
    const I64_MIN_F: f64 = -9_223_372_036_854_775_808.0; // -2^63
    const I64_MAX_EXCL_F: f64 = 9_223_372_036_854_775_808.0; // 2^63
    if is_int
        && value.is_finite()
        && value == value.trunc()
        && (I64_MIN_F..I64_MAX_EXCL_F).contains(&value)
    {
        write!(out, "{}", value as i64).expect("writing to String cannot fail");
    } else {
        write!(out, "{value:?}").expect("writing to String cannot fail");
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn arena_roundtrip() {
        let mut arena = NodeArena::new();
        let a = arena.push_int(1.0);
        let b = arena.push_float(2.5);
        let add = arena.push_func(Op::Add, &[a, b]);
        assert_eq!(arena.len(), 3);
        assert_eq!(
            arena.kind(a),
            NodeKind::Const {
                value: 1.0,
                is_int: true
            }
        );
        assert_eq!(
            arena.kind(b),
            NodeKind::Const {
                value: 2.5,
                is_int: false
            }
        );
        assert_eq!(arena.args_of(add), &[a, b]);
        assert_eq!(arena.args_of(a), &[] as &[NodeId]);
    }

    /// Expected strings pinned from the Python reference:
    /// `format_engine_node(FunctionNode(Op.Add, (1, FunctionNode(Op.Multiply, (2.5, 3)),
    /// FunctionNode(Op.Abs, (FunctionNode(Op.Negate, (4,)),)))))`.
    #[test]
    fn format_matches_python_reference() {
        let mut arena = NodeArena::new();
        let one = arena.push_int(1.0);
        let f2_5 = arena.push_float(2.5);
        let three = arena.push_int(3.0);
        let mul = arena.push_func(Op::Multiply, &[f2_5, three]);
        let four = arena.push_int(4.0);
        let neg = arena.push_func(Op::Negate, &[four]);
        let abs = arena.push_func(Op::Abs, &[neg]);
        let add = arena.push_func(Op::Add, &[one, mul, abs]);
        assert_eq!(
            format_engine_node(&arena, add),
            "Add(\n  1\n  Multiply(\n    2.5\n    3\n  )\n  Abs(Negate(4))\n)"
        );
    }

    #[test]
    fn format_zero_arg_and_const() {
        let mut arena = NodeArena::new();
        let exec = arena.push_func(Op::Execute, &[]);
        assert_eq!(format_engine_node(&arena, exec), "Execute()");
        let c = arena.push_float(5.0);
        assert_eq!(format_engine_node(&arena, c), "5.0");
        let i = arena.push_int(5.0);
        assert_eq!(format_engine_node(&arena, i), "5");
        let neg_zero = arena.push_float(-0.0);
        assert_eq!(format_engine_node(&arena, neg_zero), "-0.0");
    }

    #[test]
    fn format_deep_chain_is_iterative() {
        // 200_000-deep single-arg chain; would overflow the thread stack if recursive,
        // and must not be quadratic (single-line inline rendering appends in place).
        let mut arena = NodeArena::new();
        let mut node = arena.push_int(7.0);
        let depth = 200_000;
        for _ in 0..depth {
            node = arena.push_func(Op::Negate, &[node]);
        }
        let text = format_engine_node(&arena, node);
        assert_eq!(text.len(), "Negate(".len() * depth + 1 + depth);
        assert!(text.starts_with("Negate(Negate("));
        assert!(text.contains("Negate(7)"));
        assert!(text.ends_with(")))"));
    }

    #[test]
    fn tree_node_count_simple_tree() {
        let mut arena = NodeArena::new();
        let c = arena.push_int(1.0);
        assert_eq!(tree_node_count(&arena, c), 1);
        let f = arena.push_float(2.5);
        let add = arena.push_func(Op::Add, &[c, f]);
        assert_eq!(tree_node_count(&arena, add), 3);
        let exec = arena.push_func(Op::Execute, &[]);
        assert_eq!(tree_node_count(&arena, exec), 1);
    }

    #[test]
    fn tree_node_count_counts_shared_nodes_per_occurrence() {
        // Add(x, x) with x = Negate(7): tree expansion has 5 nodes even though
        // the arena holds only 3 reachable nodes.
        let mut arena = NodeArena::new();
        let seven = arena.push_int(7.0);
        let x = arena.push_func(Op::Negate, &[seven]);
        let add = arena.push_func(Op::Add, &[x, x]);
        assert_eq!(arena.len(), 3);
        assert_eq!(tree_node_count(&arena, add), 5);
    }

    #[test]
    fn tree_node_count_ignores_unreachable_nodes() {
        // The emitter's FinishSet leaves dead intermediate Get nodes in the
        // arena; they must not count.
        let mut arena = NodeArena::new();
        let dead = arena.push_int(9.0);
        let _dead_func = arena.push_func(Op::Abs, &[dead]);
        let one = arena.push_int(1.0);
        let root = arena.push_func(Op::Negate, &[one]);
        assert_eq!(arena.len(), 4);
        assert_eq!(tree_node_count(&arena, root), 2);
    }

    #[test]
    fn tree_node_count_deep_chain_is_iterative() {
        let mut arena = NodeArena::new();
        let mut node = arena.push_int(7.0);
        let depth: u64 = 200_000;
        for _ in 0..depth {
            node = arena.push_func(Op::Negate, &[node]);
        }
        assert_eq!(tree_node_count(&arena, node), depth + 1);
    }

    #[test]
    fn tree_node_count_saturates_on_exponential_sharing() {
        // 70 levels of Add(x, x) doubles the tree expansion per level: the
        // true tree size (2^71 - 1) overflows nothing thanks to saturation.
        let mut arena = NodeArena::new();
        let mut node = arena.push_int(1.0);
        for _ in 0..70 {
            node = arena.push_func(Op::Add, &[node, node]);
        }
        assert_eq!(tree_node_count(&arena, node), u64::MAX);
    }

    #[test]
    fn format_deep_multiline_nesting() {
        // Deep multi-arg nesting exercises the indentation path iteratively. Output
        // size is quadratic in depth (indentation widens per level, exactly like the
        // Python reference), so keep the depth moderate.
        let mut arena = NodeArena::new();
        let mut node = arena.push_int(1.0);
        for _ in 0..1_000 {
            let c = arena.push_int(2.0);
            node = arena.push_func(Op::Add, &[c, node]);
        }
        let text = format_engine_node(&arena, node);
        assert!(text.starts_with("Add(\n  2\n  Add(\n    2\n"));
    }
}
