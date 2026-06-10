//! Emitter: decoded frontend-level CFG → `Block(JumpLoop(...))` engine-node tree.
//!
//! A faithful port of `sonolus/backend/finalize.py` (`cfg_to_engine_node` +
//! `ir_to_engine_node`), operating on the [`crate::cfg::Cfg`] arena instead of live
//! Python objects.
//!
//! # Input domain
//!
//! Inputs are **post-pass** CFGs: the output of the optimization pipeline, encoded
//! with `sonolus/backend/encode.py` just before emission. These contain only
//! concrete integer block ids and indices. The encoding can also represent
//! `TempBlock` places (pre-allocation CFGs); the legacy emitter crashes on those
//! (`TempBlock` has no `offset` attribute), so this port rejects them with
//! [`EmitError::TempBlockPlace`].
//!
//! # Emission rules (ported exactly)
//!
//! Blocks are emitted in their decoded order, which is the reverse postorder the
//! legacy `block_indexes` uses (the encoder numbers blocks with the identical
//! traversal). Each block becomes `Execute(stmt_nodes..., dispatcher)`; the final
//! `JumpLoop` gets one `Execute` per block plus an int `0` tail, wrapped in
//! `Block(...)`. The exit index equals the number of blocks (the tail's position).
//!
//! The dispatcher is selected from the block's `{cond: target}` map (edges sorted
//! by `(cond is None, cond)`, exactly the decoded order):
//!
//! - no edges → const exit index
//! - `{None: t}` → const target index
//! - `{0: f, None: t}` → `If(test, t, f)` — the `0` key matches *numerically*
//!   (Python dict keys: int `0`, float `0.0` and `-0.0` all hit `case {0: ...}`)
//! - `{None: default, c: branch}` (exactly one non-None cond, `c != 0`) →
//!   `If(Equal(test, const), branch, default)` where the cond constant goes through
//!   the `IRConst` numeric path (integral → int-tagged, non-integral → float-tagged,
//!   ±inf → ROM read)
//! - dense (all conds integral and exactly `0..n-1`) →
//!   `SwitchIntegerWithDefault(test, targets..., default)` with
//!   `default = targets[None]` if present else the exit index
//! - otherwise → `SwitchWithDefault(test, (cond, target)..., default)` with pairs in
//!   ascending cond order and the cond values appended **raw**: a float cond stays a
//!   float-tagged node value (even when integral, even `±inf`/`-0.0`); it does *not*
//!   go through the ROM/int-normalization path.
//!
//! # IR conversion (`ir_to_engine_node`)
//!
//! - Constants via the numeric path: `float(value).is_integer()` → int-tagged node
//!   (Python's `int()` kills `-0.0`); other finite → float-tagged; `±inf` →
//!   `Get(3000, 1|2)`; NaN → `Get(3000, 0)` (`EngineRom` reads).
//! - `IRGet(place)` → the place's node. A `BlockPlace` becomes
//!   `Get(block_node, index_node)` where the index is: the converted index if
//!   `offset == 0`; the bare offset int if `index == 0`; else
//!   `Add(converted_index, offset)` in that argument order.
//! - `IRSet(place, value)` → `Set(block_node, index_node, value_node)` (the place's
//!   `Get` arguments spliced in front of the value).
//! - `IRInstr`/`IRPureInstr` → `FunctionNode(op, converted_args)`.
//!
//! All conversion is iterative (explicit work stacks, invariant §3.4): real node
//! trees are deep enough to overflow a thread stack.
//!
//! # Graceful rejection of out-of-domain inputs
//!
//! Decoded CFGs produced by `sonolus/backend/encode.py` always have edges strictly
//! sorted by `(cond is None, cond)` with numerically distinct, non-NaN conds (the
//! encoder rejects violations because they make the legacy dict/sort semantics
//! nondeterministic). Hand-crafted byte streams can violate this; the emitter
//! returns an [`EmitError`] instead of silently picking an arbitrary dispatcher.
//!
//! # Documented precision divergences (unreachable from real pipelines)
//!
//! `i64` values (raw conds, place offsets, huge int constants) are converted to
//! `f64` node values, rounding beyond ±2^53 where the legacy emitter keeps exact
//! Python ints in a few raw paths (`SwitchWithDefault` conds, `Add` offsets).
//! Real conds/offsets are tiny; constants already round through `float(value)` in
//! the legacy `_numeric_to_engine_node`.

use std::cmp::Ordering;
use std::fmt;

use crate::cfg::{BasicBlock, BlockValue, Cfg, Edge, EdgeCond, IndexValue, Node};
use crate::nodes::{EngineNodes, NodeArena, NodeId};
use crate::ops::Op;

/// An emitter failure. All variants are out-of-domain inputs that the Python
/// encoder never produces (see the module docs).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum EmitError {
    /// A place references a `TempBlock`: the CFG was not run through allocation.
    TempBlockPlace,
    /// An edge cond is NaN (unsortable; rejected by the encoder).
    NanEdgeCond,
    /// A block's edges are not strictly sorted by `(cond is None, cond)` with
    /// distinct conds and at most one trailing unconditional edge.
    MalformedEdges,
}

impl fmt::Display for EmitError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::TempBlockPlace => write!(
                f,
                "CFG contains a TempBlock place; the emitter requires an allocated \
                 (post-pass) CFG with concrete block ids"
            ),
            Self::NanEdgeCond => write!(f, "NaN edge conds are not supported"),
            Self::MalformedEdges => write!(
                f,
                "block edges are not strictly sorted with distinct conds and at most \
                 one trailing unconditional edge"
            ),
        }
    }
}

impl std::error::Error for EmitError {}

/// Emits the engine-node tree for a decoded post-pass CFG.
///
/// # Errors
///
/// Returns an [`EmitError`] for out-of-domain inputs (`TempBlock` places, NaN conds,
/// or malformed edge lists); see the module docs.
pub fn cfg_to_engine_nodes(cfg: &Cfg) -> Result<EngineNodes, EmitError> {
    let mut arena = NodeArena::new();
    let exit_index = cfg.blocks.len();
    let mut jump_args: Vec<NodeId> = Vec::with_capacity(exit_index + 1);
    let mut stmts: Vec<NodeId> = Vec::new();
    for block in &cfg.blocks {
        stmts.clear();
        for &stmt in &block.statements {
            stmts.push(convert_node(cfg, &mut arena, stmt)?);
        }
        stmts.push(dispatcher(cfg, &mut arena, block, exit_index)?);
        jump_args.push(arena.push_func(Op::Execute, &stmts));
    }
    jump_args.push(arena.push_int(0.0));
    let jump = arena.push_func(Op::JumpLoop, &jump_args);
    let root = arena.push_func(Op::Block, &[jump]);
    Ok(EngineNodes { arena, root })
}

/// `_numeric_to_engine_node`: integral (per `float.is_integer`) → int-tagged node
/// with `-0.0` normalized to `0.0` (Python's `int(-0.0)` is `0`); other finite →
/// float-tagged; non-finite → `EngineRom` read (`Get(3000, 0|1|2)`).
#[allow(clippy::float_cmp)] // exact integrality check, ported Python semantics
fn push_numeric(arena: &mut NodeArena, value: f64) -> NodeId {
    if value.is_nan() {
        push_rom_read(arena, 0.0)
    } else if value.is_infinite() {
        push_rom_read(arena, if value > 0.0 { 1.0 } else { 2.0 })
    } else if value == value.trunc() {
        // `+ 0.0` turns -0.0 into +0.0 and is a no-op for every other integral value.
        arena.push_int(value + 0.0)
    } else {
        arena.push_float(value)
    }
}

fn push_rom_read(arena: &mut NodeArena, index: f64) -> NodeId {
    let block = arena.push_int(3000.0);
    let index = arena.push_int(index);
    arena.push_func(Op::Get, &[block, index])
}

#[allow(clippy::cast_precision_loss)]
fn push_block_index(arena: &mut NodeArena, index: usize) -> NodeId {
    arena.push_int(index as f64)
}

/// Work items for the iterative IR-to-node conversion. Completed items push one
/// arena node onto the shared result stack; `Finish*` items pop their operands
/// from it (operands arrive in argument order because children are pushed onto the
/// work stack in reverse).
enum Work {
    /// Convert a decoded IR node.
    Node(usize),
    /// Convert a place into its `Get(block, index)` node.
    Place(usize),
    /// Push a numeric constant (via [`push_numeric`]).
    Numeric(f64),
    /// Pop `argc` results and build a function node.
    FinishFunc { op: Op, argc: usize },
    /// Pop `[place_get, value]` and build `Set(place_get.args..., value)`.
    FinishSet,
    /// Pop `[block, index]` and build `Get(block, index)`.
    FinishGet,
    /// Pop `[index]` and build `Add(index, offset)`.
    FinishAdd { offset: i64 },
}

/// `ir_to_engine_node`, iterative.
#[allow(clippy::cast_precision_loss)]
fn convert_node(cfg: &Cfg, arena: &mut NodeArena, root: usize) -> Result<NodeId, EmitError> {
    let mut work: Vec<Work> = vec![Work::Node(root)];
    let mut results: Vec<NodeId> = Vec::new();
    while let Some(item) = work.pop() {
        match item {
            Work::Numeric(value) => {
                let id = push_numeric(arena, value);
                results.push(id);
            }
            Work::Node(id) => match &cfg.nodes[id] {
                Node::ConstInt(v) => {
                    let id = push_numeric(arena, *v as f64);
                    results.push(id);
                }
                Node::ConstFloat(v) => {
                    let id = push_numeric(arena, *v);
                    results.push(id);
                }
                Node::PureInstr { op, args } | Node::Instr { op, args } => {
                    work.push(Work::FinishFunc {
                        op: *op,
                        argc: args.len(),
                    });
                    for &arg in args.iter().rev() {
                        work.push(Work::Node(arg));
                    }
                }
                Node::Get(place) => work.push(Work::Place(*place)),
                Node::Set { place, value } => {
                    work.push(Work::FinishSet);
                    work.push(Work::Node(*value));
                    work.push(Work::Place(*place));
                }
            },
            Work::Place(id) => {
                let place = &cfg.places[id];
                work.push(Work::FinishGet);
                // Index work first (it runs second; the work stack is LIFO).
                if place.offset == 0 {
                    work.push(index_base_work(place.index));
                } else if place.index == IndexValue::Int(0) {
                    work.push(Work::Numeric(place.offset as f64));
                } else {
                    work.push(Work::FinishAdd {
                        offset: place.offset,
                    });
                    work.push(index_base_work(place.index));
                }
                match place.block {
                    BlockValue::Int(v) => work.push(Work::Numeric(v as f64)),
                    BlockValue::Temp(_) => return Err(EmitError::TempBlockPlace),
                    BlockValue::Place(p) => work.push(Work::Place(p)),
                }
            }
            Work::FinishFunc { op, argc } => {
                let start = results.len() - argc;
                let id = arena.push_func(op, &results[start..]);
                results.truncate(start);
                results.push(id);
            }
            Work::FinishGet => {
                let index = results.pop().expect("FinishGet missing index operand");
                let block = results.pop().expect("FinishGet missing block operand");
                results.push(arena.push_func(Op::Get, &[block, index]));
            }
            Work::FinishAdd { offset } => {
                let index = results.pop().expect("FinishAdd missing index operand");
                let offset = push_numeric(arena, offset as f64);
                results.push(arena.push_func(Op::Add, &[index, offset]));
            }
            Work::FinishSet => {
                let value = results.pop().expect("FinishSet missing value operand");
                let get = results.pop().expect("FinishSet missing place operand");
                // `Set(*place_get.args, value)`. The intermediate Get node stays in
                // the arena unreferenced; only reachability from the root matters.
                let mut args = arena.args_of(get).to_vec();
                args.push(value);
                results.push(arena.push_func(Op::Set, &args));
            }
        }
    }
    debug_assert_eq!(results.len(), 1, "conversion must produce exactly one node");
    Ok(results
        .pop()
        .expect("conversion always produces exactly one node"))
}

#[allow(clippy::cast_precision_loss)]
fn index_base_work(index: IndexValue) -> Work {
    match index {
        IndexValue::Int(v) => Work::Numeric(v as f64),
        IndexValue::Place(p) => Work::Place(p),
    }
}

/// The numeric value of a non-None cond (Python compares conds numerically).
#[allow(clippy::cast_precision_loss)]
fn cond_value(cond: EdgeCond) -> Result<f64, EmitError> {
    match cond {
        EdgeCond::Int(v) => Ok(v as f64),
        EdgeCond::Float(v) => {
            if v.is_nan() {
                Err(EmitError::NanEdgeCond)
            } else {
                Ok(v)
            }
        }
        EdgeCond::None => unreachable!("cond_value is only called for conditional edges"),
    }
}

/// Validates the decoded edge order and splits off the default (None) edge.
///
/// Conditional edges must be strictly ascending by numeric cond value with the
/// optional unconditional edge last — exactly the order the encoder guarantees.
/// Int-Int pairs compare exactly in `i64`; mixed/float pairs compare in `f64`.
#[allow(clippy::float_cmp, clippy::cast_precision_loss)]
fn split_edges(edges: &[Edge]) -> Result<(&[Edge], Option<usize>), EmitError> {
    let (conds, default) = match edges {
        [
            rest @ ..,
            Edge {
                cond: EdgeCond::None,
                target,
            },
        ] => (rest, Some(*target)),
        _ => (edges, None),
    };
    for pair in conds.windows(2) {
        let ordering = match (pair[0].cond, pair[1].cond) {
            (EdgeCond::None, _) | (_, EdgeCond::None) => return Err(EmitError::MalformedEdges),
            (EdgeCond::Int(a), EdgeCond::Int(b)) => a.cmp(&b),
            (a, b) => cond_value(a)?
                .partial_cmp(&cond_value(b)?)
                .ok_or(EmitError::NanEdgeCond)?,
        };
        if ordering != Ordering::Less {
            return Err(EmitError::MalformedEdges);
        }
    }
    // Validate the trailing pair / lone conds for NaN even when no comparison ran.
    for edge in conds {
        cond_value(edge.cond)?;
    }
    Ok((conds, default))
}

/// True if the cond compares numerically equal to zero (`0`, `0.0`, `-0.0` —
/// the keys that match Python's `case {0: ...}` mapping pattern).
#[allow(clippy::float_cmp)]
fn cond_is_zero(cond: EdgeCond) -> bool {
    match cond {
        EdgeCond::Int(v) => v == 0,
        EdgeCond::Float(v) => v == 0.0,
        EdgeCond::None => false,
    }
}

/// True if the sorted conds are exactly the dense integer set `0..len`.
#[allow(clippy::float_cmp, clippy::cast_precision_loss)]
fn is_dense(conds: &[Edge]) -> bool {
    let max = conds.len() - 1;
    let first_is_zero = cond_is_zero(conds[0].cond);
    let last_is_max = match conds[conds.len() - 1].cond {
        EdgeCond::Int(v) => i64::try_from(max).is_ok_and(|m| v == m),
        EdgeCond::Float(v) => v == max as f64,
        EdgeCond::None => false,
    };
    let all_integral = conds.iter().all(|e| match e.cond {
        EdgeCond::Int(_) => true,
        EdgeCond::Float(v) => v.is_finite() && v.trunc() == v,
        EdgeCond::None => false,
    });
    first_is_zero && last_is_max && all_integral
}

/// The raw cond node for `SwitchWithDefault` pairs: int conds become int-tagged
/// nodes, float conds stay float-tagged with their exact value (`finalize.py`
/// appends the cond without any normalization).
#[allow(clippy::cast_precision_loss)]
fn push_raw_cond(arena: &mut NodeArena, cond: EdgeCond) -> NodeId {
    match cond {
        EdgeCond::Int(v) => arena.push_int(v as f64),
        EdgeCond::Float(v) => arena.push_float(v),
        EdgeCond::None => unreachable!("raw cond nodes are only built for conditional edges"),
    }
}

/// Builds the dispatcher node for one block (the legacy `match outgoing` selection).
fn dispatcher(
    cfg: &Cfg,
    arena: &mut NodeArena,
    block: &BasicBlock,
    exit_index: usize,
) -> Result<NodeId, EmitError> {
    let (conds, default) = split_edges(&block.outgoing)?;
    Ok(match (conds, default) {
        ([], None) => push_block_index(arena, exit_index),
        ([], Some(target)) => push_block_index(arena, target),
        ([edge], Some(default_target)) if cond_is_zero(edge.cond) => {
            let test = convert_node(cfg, arena, block.test)?;
            let t_branch = push_block_index(arena, default_target);
            let f_branch = push_block_index(arena, edge.target);
            arena.push_func(Op::If, &[test, t_branch, f_branch])
        }
        ([edge], Some(default_target)) => {
            // `If(Equal(test, IRConst(cond)), branch, default)`: the cond goes
            // through the IRConst/numeric path (int-normalized, ROM reads for inf).
            let test = convert_node(cfg, arena, block.test)?;
            let cond = cond_value(edge.cond)?;
            let cond = push_numeric(arena, cond);
            let equal = arena.push_func(Op::Equal, &[test, cond]);
            let t_branch = push_block_index(arena, edge.target);
            let f_branch = push_block_index(arena, default_target);
            arena.push_func(Op::If, &[equal, t_branch, f_branch])
        }
        (conds, default) => {
            let test = convert_node(cfg, arena, block.test)?;
            let mut args = vec![test];
            if is_dense(conds) {
                // Sorted, distinct, integral, min 0, max n-1 ⇒ the k-th edge has
                // cond k, so iteration order equals Python's `range(len(conds))`.
                for edge in conds {
                    args.push(push_block_index(arena, edge.target));
                }
                args.push(push_block_index(arena, default.unwrap_or(exit_index)));
                arena.push_func(Op::SwitchIntegerWithDefault, &args)
            } else {
                for edge in conds {
                    args.push(push_raw_cond(arena, edge.cond));
                    args.push(push_block_index(arena, edge.target));
                }
                args.push(push_block_index(arena, default.unwrap_or(exit_index)));
                arena.push_func(Op::SwitchWithDefault, &args)
            }
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::cfg::{Place, TempBlockDef};
    use crate::nodes::format_engine_node;

    /// Builder for hand-made decoded CFGs in tests.
    #[derive(Default)]
    struct CfgBuilder {
        cfg: Cfg,
    }

    impl CfgBuilder {
        fn node(&mut self, node: Node) -> usize {
            self.cfg.nodes.push(node);
            self.cfg.nodes.len() - 1
        }

        fn place(&mut self, block: BlockValue, index: IndexValue, offset: i64) -> usize {
            self.cfg.places.push(Place {
                block,
                index,
                offset,
            });
            self.cfg.places.len() - 1
        }

        fn set_stmt(&mut self, block: i64, index: i64, value: i64) -> usize {
            let place = self.place(BlockValue::Int(block), IndexValue::Int(index), 0);
            let value = self.node(Node::ConstInt(value));
            self.node(Node::Set { place, value })
        }

        fn get_test(&mut self) -> usize {
            let place = self.place(BlockValue::Int(21), IndexValue::Int(3), 0);
            self.node(Node::Get(place))
        }

        fn block(&mut self, statements: Vec<usize>, test: usize, outgoing: Vec<Edge>) {
            self.cfg.blocks.push(BasicBlock {
                statements,
                test,
                outgoing,
            });
        }

        fn zero_test(&mut self) -> usize {
            self.node(Node::ConstInt(0))
        }
    }

    fn edge(cond: EdgeCond, target: usize) -> Edge {
        Edge { cond, target }
    }

    fn emit_str(cfg: &Cfg) -> String {
        let nodes = cfg_to_engine_nodes(cfg).expect("emit must succeed");
        format_engine_node(&nodes.arena, nodes.root)
    }

    fn convert_str(b: &mut CfgBuilder, node: usize) -> String {
        let mut arena = NodeArena::new();
        let id = convert_node(&b.cfg, &mut arena, node).expect("conversion must succeed");
        format_engine_node(&arena, id)
    }

    // All expected strings below are pinned from the frozen Python reference
    // (`cfg_to_engine_node` / `ir_to_engine_node` + `format_engine_node`).

    #[test]
    fn exit_only_block() {
        let mut b = CfgBuilder::default();
        let s = b.set_stmt(20, 0, 5);
        let t = b.zero_test();
        b.block(vec![s], t, vec![]);
        assert_eq!(
            emit_str(&b.cfg),
            "Block(JumpLoop(\n  Execute(\n    Set(\n      20\n      0\n      5\n    )\n    1\n  )\n  0\n))"
        );
    }

    #[test]
    fn single_unconditional_edge() {
        let mut b = CfgBuilder::default();
        let t0 = b.zero_test();
        b.block(vec![], t0, vec![edge(EdgeCond::None, 1)]);
        let t1 = b.zero_test();
        b.block(vec![], t1, vec![]);
        assert_eq!(
            emit_str(&b.cfg),
            "Block(JumpLoop(\n  Execute(1)\n  Execute(2)\n  0\n))"
        );
    }

    fn two_way_cfg(cond: EdgeCond) -> Cfg {
        // Entry tests Get(21, 3); cond -> block 2, None -> block 1; both fall to 3.
        let mut b = CfgBuilder::default();
        let test = b.get_test();
        b.block(vec![], test, vec![edge(cond, 2), edge(EdgeCond::None, 1)]);
        let s1 = b.set_stmt(20, 0, 1);
        let t1 = b.zero_test();
        b.block(vec![s1], t1, vec![edge(EdgeCond::None, 3)]);
        let s2 = b.set_stmt(20, 0, 2);
        let t2 = b.zero_test();
        b.block(vec![s2], t2, vec![edge(EdgeCond::None, 3)]);
        let t3 = b.zero_test();
        b.block(vec![], t3, vec![]);
        b.cfg
    }

    #[test]
    fn zero_none_becomes_if() {
        let expected = "Block(JumpLoop(\n  Execute(If(\n    Get(\n      21\n      3\n    )\n    1\n    2\n  ))\n  Execute(\n    Set(\n      20\n      0\n      1\n    )\n    3\n  )\n  Execute(\n    Set(\n      20\n      0\n      2\n    )\n    3\n  )\n  Execute(4)\n  0\n))";
        assert_eq!(emit_str(&two_way_cfg(EdgeCond::Int(0))), expected);
        // A float 0.0 (or -0.0) cond matches Python's `case {0: ...}` numerically.
        assert_eq!(emit_str(&two_way_cfg(EdgeCond::Float(0.0))), expected);
        assert_eq!(emit_str(&two_way_cfg(EdgeCond::Float(-0.0))), expected);
    }

    fn if_equal_cfg(cond: EdgeCond) -> Cfg {
        // Entry: cond -> block 1, None -> block 2 (exit-bound ordering from RPO).
        let mut b = CfgBuilder::default();
        let test = b.get_test();
        b.block(vec![], test, vec![edge(cond, 1), edge(EdgeCond::None, 2)]);
        let s1 = b.set_stmt(20, 0, 1);
        let t1 = b.zero_test();
        b.block(vec![s1], t1, vec![edge(EdgeCond::None, 2)]);
        let t2 = b.zero_test();
        b.block(vec![], t2, vec![]);
        b.cfg
    }

    #[test]
    fn single_nonzero_cond_with_default_becomes_if_equal() {
        let expected_int = "Block(JumpLoop(\n  Execute(If(\n    Equal(\n      Get(\n        21\n        3\n      )\n      3\n    )\n    1\n    2\n  ))\n  Execute(\n    Set(\n      20\n      0\n      1\n    )\n    2\n  )\n  Execute(3)\n  0\n))";
        assert_eq!(emit_str(&if_equal_cfg(EdgeCond::Int(3))), expected_int);
        // The cond goes through the IRConst path: an integral float is
        // int-normalized...
        assert_eq!(emit_str(&if_equal_cfg(EdgeCond::Float(3.0))), expected_int);
        // ...a non-integral float stays float...
        assert_eq!(
            emit_str(&if_equal_cfg(EdgeCond::Float(2.5))),
            expected_int.replace("      3\n    )", "      2.5\n    )")
        );
        // ...and an infinity becomes a ROM read.
        assert_eq!(
            emit_str(&if_equal_cfg(EdgeCond::Float(f64::INFINITY))),
            expected_int.replace(
                "      3\n    )",
                "      Get(\n        3000\n        1\n      )\n    )"
            )
        );
    }

    fn switch_cfg(conds: &[EdgeCond], with_default: bool) -> Cfg {
        // Entry block 0; targets are blocks 1..=k in *reverse* edge order (matching
        // the RPO numbering the encoder would produce); exit block is k+1.
        let mut b = CfgBuilder::default();
        let k = conds.len();
        let test = b.get_test();
        let mut edges: Vec<Edge> = conds
            .iter()
            .enumerate()
            .map(|(i, &cond)| edge(cond, k - i))
            .collect();
        if with_default {
            edges.push(edge(EdgeCond::None, k + 1));
        }
        b.block(vec![], test, edges);
        for i in 0..k {
            let s = b.set_stmt(20, 0, 10 + i64::try_from(k - 1 - i).unwrap());
            let t = b.zero_test();
            b.block(vec![s], t, vec![edge(EdgeCond::None, k + 1)]);
        }
        let t = b.zero_test();
        b.block(vec![], t, vec![]);
        b.cfg
    }

    #[test]
    fn dense_conds_become_switch_integer_with_default() {
        let cfg = switch_cfg(
            &[EdgeCond::Int(0), EdgeCond::Int(1), EdgeCond::Int(2)],
            true,
        );
        let expected = "Block(JumpLoop(\n  Execute(SwitchIntegerWithDefault(\n    Get(\n      21\n      3\n    )\n    3\n    2\n    1\n    4\n  ))\n  Execute(\n    Set(\n      20\n      0\n      12\n    )\n    4\n  )\n  Execute(\n    Set(\n      20\n      0\n      11\n    )\n    4\n  )\n  Execute(\n    Set(\n      20\n      0\n      10\n    )\n    4\n  )\n  Execute(5)\n  0\n))";
        assert_eq!(emit_str(&cfg), expected);
        // Mixed int/float keys still count as dense (numeric equality).
        let cfg = switch_cfg(
            &[EdgeCond::Int(0), EdgeCond::Float(1.0), EdgeCond::Int(2)],
            true,
        );
        assert_eq!(emit_str(&cfg), expected);
    }

    #[test]
    fn dense_without_default_uses_exit_index() {
        let cfg = switch_cfg(&[EdgeCond::Int(0), EdgeCond::Int(1)], false);
        assert_eq!(
            emit_str(&cfg),
            "Block(JumpLoop(\n  Execute(SwitchIntegerWithDefault(\n    Get(\n      21\n      3\n    )\n    2\n    1\n    4\n  ))\n  Execute(\n    Set(\n      20\n      0\n      11\n    )\n    3\n  )\n  Execute(\n    Set(\n      20\n      0\n      10\n    )\n    3\n  )\n  Execute(4)\n  0\n))"
        );
    }

    #[test]
    fn sparse_conds_become_switch_with_default_raw_conds() {
        let cfg = switch_cfg(
            &[EdgeCond::Int(-1), EdgeCond::Float(2.5), EdgeCond::Int(7)],
            true,
        );
        assert_eq!(
            emit_str(&cfg),
            "Block(JumpLoop(\n  Execute(SwitchWithDefault(\n    Get(\n      21\n      3\n    )\n    -1\n    3\n    2.5\n    2\n    7\n    1\n    4\n  ))\n  Execute(\n    Set(\n      20\n      0\n      12\n    )\n    4\n  )\n  Execute(\n    Set(\n      20\n      0\n      11\n    )\n    4\n  )\n  Execute(\n    Set(\n      20\n      0\n      10\n    )\n    4\n  )\n  Execute(5)\n  0\n))"
        );
    }

    #[test]
    fn sparse_integral_float_cond_stays_raw_float() {
        // {3.0: ..., 7: ..., None: ...}: the raw 3.0 cond must stay a float-tagged
        // node (it does NOT go through the int-normalization path).
        let cfg = switch_cfg(&[EdgeCond::Float(3.0), EdgeCond::Int(7)], true);
        assert_eq!(
            emit_str(&cfg),
            "Block(JumpLoop(\n  Execute(SwitchWithDefault(\n    Get(\n      21\n      3\n    )\n    3.0\n    2\n    7\n    1\n    3\n  ))\n  Execute(\n    Set(\n      20\n      0\n      11\n    )\n    3\n  )\n  Execute(\n    Set(\n      20\n      0\n      10\n    )\n    3\n  )\n  Execute(4)\n  0\n))"
        );
    }

    #[test]
    fn single_cond_without_default() {
        // {4: ...} with no default falls through to the dict() case: sparse.
        let cfg = switch_cfg(&[EdgeCond::Int(4)], false);
        assert_eq!(
            emit_str(&cfg),
            "Block(JumpLoop(\n  Execute(SwitchWithDefault(\n    Get(\n      21\n      3\n    )\n    4\n    1\n    3\n  ))\n  Execute(\n    Set(\n      20\n      0\n      10\n    )\n    2\n  )\n  Execute(3)\n  0\n))"
        );
        // {0: ...} with no default is dense.
        let cfg = switch_cfg(&[EdgeCond::Int(0)], false);
        assert_eq!(
            emit_str(&cfg),
            "Block(JumpLoop(\n  Execute(SwitchIntegerWithDefault(\n    Get(\n      21\n      3\n    )\n    1\n    3\n  ))\n  Execute(\n    Set(\n      20\n      0\n      10\n    )\n    2\n  )\n  Execute(3)\n  0\n))"
        );
    }

    #[test]
    fn numeric_constants() {
        let mut b = CfgBuilder::default();
        for (node, expected) in [
            (Node::ConstInt(5), "5"),
            (Node::ConstInt(-3), "-3"),
            (Node::ConstInt(0), "0"),
            (Node::ConstFloat(2.5), "2.5"),
            (Node::ConstFloat(-0.0), "0"),
            (Node::ConstFloat(7.0), "7"),
            (Node::ConstFloat(f64::INFINITY), "Get(\n  3000\n  1\n)"),
            (Node::ConstFloat(f64::NEG_INFINITY), "Get(\n  3000\n  2\n)"),
            (Node::ConstFloat(f64::NAN), "Get(\n  3000\n  0\n)"),
        ] {
            let id = b.node(node);
            assert_eq!(convert_str(&mut b, id), expected);
        }
    }

    #[test]
    fn place_offset_forms() {
        let mut b = CfgBuilder::default();
        // offset == 0: index converted directly.
        let p = b.place(BlockValue::Int(21), IndexValue::Int(3), 0);
        let g = b.node(Node::Get(p));
        assert_eq!(convert_str(&mut b, g), "Get(\n  21\n  3\n)");
        // index == 0, offset != 0: bare offset.
        let p = b.place(BlockValue::Int(21), IndexValue::Int(0), 4);
        let g = b.node(Node::Get(p));
        assert_eq!(convert_str(&mut b, g), "Get(\n  21\n  4\n)");
        // both non-zero: Add(index, offset).
        let p = b.place(BlockValue::Int(21), IndexValue::Int(3), 4);
        let g = b.node(Node::Get(p));
        assert_eq!(
            convert_str(&mut b, g),
            "Get(\n  21\n  Add(\n    3\n    4\n  )\n)"
        );
        // dynamic index with offset: Add(Get(...), offset).
        let inner = b.place(BlockValue::Int(22), IndexValue::Int(1), 0);
        let p = b.place(BlockValue::Int(21), IndexValue::Place(inner), 4);
        let g = b.node(Node::Get(p));
        assert_eq!(
            convert_str(&mut b, g),
            "Get(\n  21\n  Add(\n    Get(\n      22\n      1\n    )\n    4\n  )\n)"
        );
        // nested block place.
        let inner = b.place(BlockValue::Int(23), IndexValue::Int(2), 0);
        let p = b.place(BlockValue::Place(inner), IndexValue::Int(1), 0);
        let g = b.node(Node::Get(p));
        assert_eq!(
            convert_str(&mut b, g),
            "Get(\n  Get(\n    23\n    2\n  )\n  1\n)"
        );
    }

    #[test]
    fn set_splices_place_args() {
        let mut b = CfgBuilder::default();
        let p = b.place(BlockValue::Int(21), IndexValue::Int(3), 4);
        let v = b.node(Node::ConstInt(9));
        let s = b.node(Node::Set { place: p, value: v });
        assert_eq!(
            convert_str(&mut b, s),
            "Set(\n  21\n  Add(\n    3\n    4\n  )\n  9\n)"
        );
    }

    #[test]
    fn instr_conversion() {
        let mut b = CfgBuilder::default();
        let one = b.node(Node::ConstInt(1));
        let f = b.node(Node::ConstFloat(2.5));
        let add = b.node(Node::PureInstr {
            op: Op::Add,
            args: vec![one, f],
        });
        let log = b.node(Node::Instr {
            op: Op::DebugLog,
            args: vec![add],
        });
        assert_eq!(convert_str(&mut b, log), "DebugLog(Add(\n  1\n  2.5\n))");
    }

    #[test]
    fn deep_nesting_is_iterative() {
        // 200k-deep Negate chain: must not overflow the thread stack.
        let mut b = CfgBuilder::default();
        let mut node = b.node(Node::ConstInt(7));
        for _ in 0..200_000 {
            node = b.node(Node::PureInstr {
                op: Op::Negate,
                args: vec![node],
            });
        }
        let t = b.zero_test();
        b.block(vec![node], t, vec![]);
        let nodes = cfg_to_engine_nodes(&b.cfg).expect("emit must succeed");
        assert!(nodes.arena.len() > 200_000);
        // Deeply nested places too.
        let mut b = CfgBuilder::default();
        let mut place = b.place(BlockValue::Int(4000), IndexValue::Int(0), 0);
        for _ in 0..100_000 {
            place = b.place(BlockValue::Place(place), IndexValue::Int(0), 0);
        }
        let g = b.node(Node::Get(place));
        let t = b.zero_test();
        b.block(vec![g], t, vec![]);
        cfg_to_engine_nodes(&b.cfg).expect("emit must succeed");
    }

    #[test]
    fn temp_block_place_is_rejected() {
        let mut b = CfgBuilder::default();
        b.cfg.strings.push("tmp".to_owned());
        b.cfg.temp_blocks.push(TempBlockDef { name: 0, size: 1 });
        let p = b.place(BlockValue::Temp(0), IndexValue::Int(0), 0);
        let g = b.node(Node::Get(p));
        let t = b.zero_test();
        b.block(vec![g], t, vec![]);
        assert!(matches!(
            cfg_to_engine_nodes(&b.cfg),
            Err(EmitError::TempBlockPlace)
        ));
    }

    #[test]
    fn malformed_edges_are_rejected() {
        // Unsorted conds.
        let mut b = CfgBuilder::default();
        let test = b.get_test();
        b.block(
            vec![],
            test,
            vec![edge(EdgeCond::Int(2), 1), edge(EdgeCond::Int(1), 1)],
        );
        let t = b.zero_test();
        b.block(vec![], t, vec![]);
        assert!(matches!(
            cfg_to_engine_nodes(&b.cfg),
            Err(EmitError::MalformedEdges)
        ));
        // Numerically duplicate conds (int 1 vs float 1.0).
        let mut b = CfgBuilder::default();
        let test = b.get_test();
        b.block(
            vec![],
            test,
            vec![edge(EdgeCond::Int(1), 1), edge(EdgeCond::Float(1.0), 1)],
        );
        let t = b.zero_test();
        b.block(vec![], t, vec![]);
        assert!(matches!(
            cfg_to_engine_nodes(&b.cfg),
            Err(EmitError::MalformedEdges)
        ));
        // None edge not last.
        let mut b = CfgBuilder::default();
        let test = b.get_test();
        b.block(
            vec![],
            test,
            vec![edge(EdgeCond::None, 1), edge(EdgeCond::Int(1), 1)],
        );
        let t = b.zero_test();
        b.block(vec![], t, vec![]);
        assert!(matches!(
            cfg_to_engine_nodes(&b.cfg),
            Err(EmitError::MalformedEdges)
        ));
        // NaN cond.
        let mut b = CfgBuilder::default();
        let test = b.get_test();
        b.block(vec![], test, vec![edge(EdgeCond::Float(f64::NAN), 1)]);
        let t = b.zero_test();
        b.block(vec![], t, vec![]);
        assert!(matches!(
            cfg_to_engine_nodes(&b.cfg),
            Err(EmitError::NanEdgeCond)
        ));
    }
}
