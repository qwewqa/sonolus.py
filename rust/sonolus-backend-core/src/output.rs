//! Output-node generation: engine-node tree → deduplicated flat node list.
//!
//! A faithful port of `sonolus/build/node.py` (`OutputNodeGenerator`), which turns
//! the emitter's `Block(JumpLoop(...))` tree into the flat node array shipped in
//! engine data: each output node is either a numeric value or a function with
//! argument indices into the same array.
//!
//! # Insertion order
//!
//! Nodes are appended in the order the recursive Python `_add` first encounters
//! them: a post-order walk (arguments before their parent, left to right), with
//! already-known nodes skipped. The walk here is iterative (explicit stack,
//! invariant §3.4) but produces the identical order: for a duplicate subtree every
//! descendant is also a duplicate, so re-walking it (which Python's early return
//! skips) inserts nothing and cannot perturb the order.
//!
//! # Dedup equality (must match Python dict-key semantics)
//!
//! Python deduplicates via `dict[EngineNode, int]`, so equality/hashing is
//! Python's:
//!
//! - **Constants compare numerically**: `5 == 5.0` and `0 == -0.0`, so an
//!   int-tagged and a float-tagged constant with the same numeric value are THE
//!   SAME node — the first one encountered wins and its tag (and exact value,
//!   e.g. `-0.0`) is what gets stored. The Rust key is the raw bits of the value
//!   with `-0.0` normalized to `0.0`; the stored node keeps the original
//!   value/tag.
//! - **Function nodes compare structurally** (same op, pairwise-equal args). With
//!   children always deduplicated before their parent, structural equality is
//!   exactly equality of `(op, child output indices)`, which is the Rust key.
//!
//! NaN constants cannot occur in output nodes: the emitter turns NaN constants
//! into `EngineRom` reads and the CFG encoding rejects NaN edge conds. They would
//! break dict-key dedup (`nan != nan`), so encountering one is an
//! [`OutputError::NanConstant`].
//!
//! # Int/float tag
//!
//! The `is_int` tag on value nodes is preserved: the engine-data serializer (a
//! later task) must emit int-tagged values as JSON integers (`5`) and float-tagged
//! values as JSON floats (`5.5`), matching the legacy backend where node values
//! are Python `int`/`float` objects.

use std::collections::HashMap;
use std::fmt;
use std::fmt::Write as _;

use crate::nodes::{NodeArena, NodeId, NodeKind};
use crate::ops::Op;

/// One output node: a tagged numeric value or a function application whose
/// arguments are indices into the output-node list.
#[derive(Debug, Clone, PartialEq)]
pub enum OutputNode {
    /// `{"value": ...}` — see the module docs for the int/float tag.
    Value { value: f64, is_int: bool },
    /// `{"func": op, "args": [...]}`.
    Func { op: Op, args: Vec<u32> },
}

/// The deduplicated output-node list for one engine-node tree.
#[derive(Debug, Clone, PartialEq)]
pub struct OutputNodes {
    /// Nodes in first-encounter post-order; arguments precede their parents.
    pub nodes: Vec<OutputNode>,
    /// Index of the root node (always the last node: the root strictly contains
    /// every other node, so it can never dedup onto an earlier one).
    pub root: u32,
}

/// An output-node generation failure.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OutputError {
    /// A NaN constant reached output-node generation (see the module docs).
    NanConstant,
}

impl fmt::Display for OutputError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::NanConstant => write!(
                f,
                "NaN constants cannot appear in output nodes (the emitter reads \
                 them from EngineRom instead)"
            ),
        }
    }
}

impl std::error::Error for OutputError {}

/// Dedup key with Python dict-key equality (see the module docs).
#[derive(PartialEq, Eq, Hash)]
enum Key {
    /// Raw bits of the value with `-0.0` normalized to `0.0` (so `0 == -0.0`).
    /// Tags are not part of the key (so `5 == 5.0`).
    Const(u64),
    /// Op plus child output indices (equivalent to structural equality).
    Func(Op, Vec<u32>),
}

/// Generates the deduplicated output-node list for a tree, in the legacy
/// generator's insertion order.
///
/// # Errors
///
/// Returns [`OutputError::NanConstant`] if a NaN constant node is reachable
/// (impossible for emitter-produced trees).
pub fn generate_output_nodes(arena: &NodeArena, root: NodeId) -> Result<OutputNodes, OutputError> {
    let mut keys: HashMap<Key, u32> = HashMap::new();
    let mut memo: Vec<Option<u32>> = vec![None; arena.len()];
    let mut nodes: Vec<OutputNode> = Vec::new();
    // (node, expanded): expanded means all children have been processed.
    let mut stack: Vec<(NodeId, bool)> = vec![(root, false)];
    while let Some((id, expanded)) = stack.pop() {
        if memo[id.index()].is_some() {
            continue;
        }
        match arena.kind(id) {
            NodeKind::Const { value, is_int } => {
                if value.is_nan() {
                    return Err(OutputError::NanConstant);
                }
                let key = Key::Const(normalize_zero(value).to_bits());
                let index = *keys.entry(key).or_insert_with(|| {
                    nodes.push(OutputNode::Value { value, is_int });
                    last_index(&nodes)
                });
                memo[id.index()] = Some(index);
            }
            NodeKind::Func { op, .. } => {
                let args = arena.args_of(id);
                if expanded {
                    let arg_indices: Vec<u32> = args
                        .iter()
                        .map(|arg| {
                            memo[arg.index()].expect("children are processed before parents")
                        })
                        .collect();
                    let key = Key::Func(op, arg_indices.clone());
                    let index = *keys.entry(key).or_insert_with(|| {
                        nodes.push(OutputNode::Func {
                            op,
                            args: arg_indices,
                        });
                        last_index(&nodes)
                    });
                    memo[id.index()] = Some(index);
                } else {
                    stack.push((id, true));
                    for &arg in args.iter().rev() {
                        stack.push((arg, false));
                    }
                }
            }
        }
    }
    let root = memo[root.index()].expect("the root was processed");
    debug_assert_eq!(root, last_index(&nodes), "the root must be the last node");
    Ok(OutputNodes { nodes, root })
}

fn normalize_zero(value: f64) -> f64 {
    if value == 0.0 { 0.0 } else { value }
}

fn last_index(nodes: &[OutputNode]) -> u32 {
    u32::try_from(nodes.len() - 1).expect("output node count exceeds u32")
}

/// Renders the canonical output-node dump: one line per node, in order.
///
/// - value nodes: `v i 0x<16 hex digits>` (int-tagged) or `v f 0x...`
///   (float-tagged), the hex digits being the raw IEEE-754 bits of the value
///   (bit-exact, including `-0.0` and infinities)
/// - func nodes: `f <OpName> <arg index>...` (space-separated)
///
/// Every line ends with `\n`. The root is the last node. The Python side of the
/// T1.2 A/B test renders the legacy `OutputNodeGenerator` result in the identical
/// format; the dumps must be byte-identical.
pub fn output_node_dump(output: &OutputNodes) -> String {
    let mut out = String::new();
    for node in &output.nodes {
        match node {
            OutputNode::Value { value, is_int } => {
                let tag = if *is_int { 'i' } else { 'f' };
                let _ = writeln!(out, "v {tag} 0x{:016x}", value.to_bits());
            }
            OutputNode::Func { op, args } => {
                let _ = write!(out, "f {}", op.name());
                for arg in args {
                    let _ = write!(out, " {arg}");
                }
                out.push('\n');
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dedup_matches_python_dict_semantics() {
        // Pinned from the Python reference:
        // OutputNodeGenerator().add(FunctionNode(Op.Add,
        //   (5, 5.0, FunctionNode(Op.Multiply, (5, -0.0)),
        //    FunctionNode(Op.Multiply, (5.0, 0)), 2.5)))
        // -> [{'value': 5}, {'value': -0.0}, {'func': 'Multiply', 'args': [0, 1]},
        //     {'value': 2.5}, {'func': 'Add', 'args': [0, 0, 2, 2, 3]}], root 4.
        let mut arena = NodeArena::new();
        let i5 = arena.push_int(5.0);
        let f5 = arena.push_float(5.0);
        let nz = arena.push_float(-0.0);
        let m1 = arena.push_func(Op::Multiply, &[i5, nz]);
        let i0 = arena.push_int(0.0);
        let f5b = arena.push_float(5.0);
        let m2 = arena.push_func(Op::Multiply, &[f5b, i0]);
        let f25 = arena.push_float(2.5);
        let root = arena.push_func(Op::Add, &[i5, f5, m1, m2, f25]);
        let out = generate_output_nodes(&arena, root).unwrap();
        assert_eq!(out.root, 4);
        assert_eq!(
            out.nodes,
            vec![
                OutputNode::Value {
                    value: 5.0,
                    is_int: true, // int 5 first: its tag wins over the later 5.0
                },
                OutputNode::Value {
                    value: -0.0,
                    is_int: false, // -0.0 first: stored raw; the later int 0 dedups onto it
                },
                OutputNode::Func {
                    op: Op::Multiply,
                    args: vec![0, 1],
                },
                OutputNode::Value {
                    value: 2.5,
                    is_int: false,
                },
                OutputNode::Func {
                    op: Op::Add,
                    args: vec![0, 0, 2, 2, 3],
                },
            ]
        );
        // The stored -0.0 keeps its sign bit in the dump.
        assert_eq!(
            output_node_dump(&out),
            "v i 0x4014000000000000\n\
             v f 0x8000000000000000\n\
             f Multiply 0 1\n\
             v f 0x4004000000000000\n\
             f Add 0 0 2 2 3\n"
        );
    }

    #[test]
    fn first_encounter_tag_wins_float_first() {
        // Pinned: Add(5.0, 5) -> [{'value': 5.0}, {'func': 'Add', 'args': [0, 0]}].
        let mut arena = NodeArena::new();
        let f5 = arena.push_float(5.0);
        let i5 = arena.push_int(5.0);
        let root = arena.push_func(Op::Add, &[f5, i5]);
        let out = generate_output_nodes(&arena, root).unwrap();
        assert_eq!(
            out.nodes,
            vec![
                OutputNode::Value {
                    value: 5.0,
                    is_int: false,
                },
                OutputNode::Func {
                    op: Op::Add,
                    args: vec![0, 0],
                },
            ]
        );
    }

    #[test]
    fn structural_function_dedup() {
        // Two structurally identical subtrees collapse; a different op does not.
        let mut arena = NodeArena::new();
        let a1 = arena.push_int(1.0);
        let b1 = arena.push_int(2.0);
        let add1 = arena.push_func(Op::Add, &[a1, b1]);
        let a2 = arena.push_int(1.0);
        let b2 = arena.push_int(2.0);
        let add2 = arena.push_func(Op::Add, &[a2, b2]);
        let mul = arena.push_func(Op::Multiply, &[a2, b2]);
        let root = arena.push_func(Op::Execute, &[add1, add2, mul]);
        let out = generate_output_nodes(&arena, root).unwrap();
        assert_eq!(
            out.nodes,
            vec![
                OutputNode::Value {
                    value: 1.0,
                    is_int: true,
                },
                OutputNode::Value {
                    value: 2.0,
                    is_int: true,
                },
                OutputNode::Func {
                    op: Op::Add,
                    args: vec![0, 1],
                },
                OutputNode::Func {
                    op: Op::Multiply,
                    args: vec![0, 1],
                },
                OutputNode::Func {
                    op: Op::Execute,
                    args: vec![2, 2, 3],
                },
            ]
        );
    }

    #[test]
    fn nan_constant_is_rejected() {
        let mut arena = NodeArena::new();
        let nan = arena.push_float(f64::NAN);
        let root = arena.push_func(Op::Execute, &[nan]);
        assert_eq!(
            generate_output_nodes(&arena, root),
            Err(OutputError::NanConstant)
        );
    }

    #[test]
    fn deep_chain_is_iterative() {
        let mut arena = NodeArena::new();
        let mut node = arena.push_int(7.0);
        for _ in 0..200_000 {
            node = arena.push_func(Op::Negate, &[node]);
        }
        let out = generate_output_nodes(&arena, node).unwrap();
        assert_eq!(out.nodes.len(), 200_001);
        assert_eq!(out.root, 200_000);
    }

    #[test]
    fn infinity_value_nodes_survive_raw() {
        // Raw SwitchWithDefault conds can be ±inf; they stay literal value nodes.
        let mut arena = NodeArena::new();
        let inf = arena.push_float(f64::INFINITY);
        let one = arena.push_int(1.0);
        let root = arena.push_func(Op::Execute, &[inf, one]);
        let out = generate_output_nodes(&arena, root).unwrap();
        assert_eq!(
            output_node_dump(&out),
            "v f 0x7ff0000000000000\nv i 0x3ff0000000000000\nf Execute 0 1\n"
        );
    }
}
