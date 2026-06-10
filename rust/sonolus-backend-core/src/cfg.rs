//! Arena-based representation of decoded frontend-level CFGs, plus the canonical
//! structural dump (round-trip validation) and a human-readable debug dump.
//!
//! See `rust/ENCODING.md` for the binary format and the canonical dump grammar.
//! All traversals here are iterative (explicit work stacks): expression trees and
//! nested places are user-sized and can be deep.

use std::fmt::Write as _;

use crate::ops::Op;

/// Index into [`Cfg::nodes`].
pub type NodeId = usize;
/// Index into [`Cfg::places`].
pub type PlaceId = usize;
/// Index into [`Cfg::temp_blocks`].
pub type TempBlockId = usize;
/// Index into [`Cfg::strings`].
pub type StrId = usize;
/// Block number (reverse postorder; 0 is the entry block).
pub type BlockId = usize;

/// An IR node (statement or expression).
#[derive(Debug, Clone, PartialEq)]
pub enum Node {
    /// `IRConst` with an int value (the int/float tag is load-bearing).
    ConstInt(i64),
    /// `IRConst` with a float value.
    ConstFloat(f64),
    /// `IRPureInstr` (n-ary at the frontend level).
    PureInstr { op: Op, args: Vec<NodeId> },
    /// `IRInstr` (n-ary at the frontend level).
    Instr { op: Op, args: Vec<NodeId> },
    /// `IRGet`.
    Get(PlaceId),
    /// `IRSet`; only valid as a top-level statement.
    Set { place: PlaceId, value: NodeId },
}

/// The `block` field of a `BlockPlace`.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum BlockValue {
    /// A runtime block id (block enum members are plain ints here) or raw int.
    Int(i64),
    /// A `TempBlock` reference.
    Temp(TempBlockId),
    /// A nested dynamic place.
    Place(PlaceId),
}

/// The `index` field of a `BlockPlace`.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum IndexValue {
    /// A constant index.
    Int(i64),
    /// A nested dynamic place.
    Place(PlaceId),
}

/// A `BlockPlace`.
#[derive(Debug, Clone, PartialEq)]
pub struct Place {
    pub block: BlockValue,
    pub index: IndexValue,
    pub offset: i64,
}

/// A `TempBlock` definition.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TempBlockDef {
    pub name: StrId,
    pub size: u64,
}

/// An outgoing edge condition.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum EdgeCond {
    /// Unconditional / default edge.
    None,
    /// Integer condition (tag preserved from Python).
    Int(i64),
    /// Float condition.
    Float(f64),
}

/// An outgoing flow edge.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Edge {
    pub cond: EdgeCond,
    pub target: BlockId,
}

/// A basic block.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct BasicBlock {
    pub statements: Vec<NodeId>,
    /// The test expression (referenced by conditional edges).
    pub test: NodeId,
    /// Outgoing edges in encoded order (sorted by `(cond is none, cond)`).
    pub outgoing: Vec<Edge>,
}

/// A decoded frontend-level CFG.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Cfg {
    /// Format version from the header.
    pub version: u16,
    /// Op-table size from the header (validated against [`Op::COUNT`]).
    pub op_count: u16,
    /// String table (currently only `TempBlock` names).
    pub strings: Vec<String>,
    /// Temp block table.
    pub temp_blocks: Vec<TempBlockDef>,
    /// Node arena.
    pub nodes: Vec<Node>,
    /// Place arena.
    pub places: Vec<Place>,
    /// Basic blocks in reverse postorder; block 0 is the entry.
    pub blocks: Vec<BasicBlock>,
}

/// Escapes a string for the canonical dump: byte-wise over UTF-8 with `\"`, `\\`,
/// and `\xNN` for bytes outside printable ASCII. Must match the Python side exactly.
fn escape_canonical(s: &str, out: &mut String) {
    for &byte in s.as_bytes() {
        match byte {
            0x22 => out.push_str("\\\""),
            0x5c => out.push_str("\\\\"),
            0x20..=0x7e => out.push(byte as char),
            _ => {
                let _ = write!(out, "\\x{byte:02x}");
            }
        }
    }
}

/// Work items for the iterative renderers.
enum Item {
    Node(NodeId),
    Place(PlaceId),
    Lit(&'static str),
    Owned(String),
}

/// Renders one node tree in canonical form (see `ENCODING.md` §5), iteratively.
fn push_canonical_node(cfg: &Cfg, root: NodeId, out: &mut String) {
    let mut stack = vec![Item::Node(root)];
    while let Some(item) = stack.pop() {
        match item {
            Item::Lit(s) => out.push_str(s),
            Item::Owned(s) => out.push_str(&s),
            Item::Node(id) => match &cfg.nodes[id] {
                Node::ConstInt(v) => {
                    let _ = write!(out, "(const i:{v})");
                }
                Node::ConstFloat(v) => {
                    let _ = write!(out, "(const f:0x{:016x})", v.to_bits());
                }
                Node::PureInstr { op, args } | Node::Instr { op, args } => {
                    let label = if matches!(&cfg.nodes[id], Node::PureInstr { .. }) {
                        "pure"
                    } else {
                        "instr"
                    };
                    let _ = write!(out, "({label} {}", op.id());
                    stack.push(Item::Lit(")"));
                    for &arg in args.iter().rev() {
                        stack.push(Item::Node(arg));
                        stack.push(Item::Lit(" "));
                    }
                }
                Node::Get(place) => {
                    out.push_str("(get ");
                    stack.push(Item::Lit(")"));
                    stack.push(Item::Place(*place));
                }
                Node::Set { place, value } => {
                    out.push_str("(set ");
                    stack.push(Item::Lit(")"));
                    stack.push(Item::Node(*value));
                    stack.push(Item::Lit(" "));
                    stack.push(Item::Place(*place));
                }
            },
            Item::Place(id) => {
                let place = &cfg.places[id];
                out.push_str("(place b=");
                stack.push(Item::Owned(format!(" o={})", place.offset)));
                match place.index {
                    IndexValue::Int(v) => stack.push(Item::Owned(format!("i:{v}"))),
                    IndexValue::Place(p) => stack.push(Item::Place(p)),
                }
                stack.push(Item::Lit(" i="));
                match place.block {
                    BlockValue::Int(v) => stack.push(Item::Owned(format!("i:{v}"))),
                    BlockValue::Temp(t) => stack.push(Item::Owned(format!("t:{t}"))),
                    BlockValue::Place(p) => stack.push(Item::Place(p)),
                }
            }
        }
    }
}

/// Renders the canonical structural dump of a decoded CFG.
///
/// Byte-identical to the Python side's `sonolus.backend.encode.cfg_canonical_dump`
/// of the CFG that was encoded (the round-trip validation contract). Floats are
/// rendered as raw IEEE-754 bits so the comparison is bit-exact, including NaN
/// payloads.
pub fn canonical_dump(cfg: &Cfg) -> String {
    let mut out = String::new();
    let _ = writeln!(out, "cfg-canonical v{}", cfg.version);
    let _ = writeln!(out, "ops {}", cfg.op_count);
    let _ = writeln!(out, "strings {}", cfg.strings.len());
    for (i, s) in cfg.strings.iter().enumerate() {
        let _ = write!(out, "  string {i} \"");
        escape_canonical(s, &mut out);
        out.push_str("\"\n");
    }
    let _ = writeln!(out, "temps {}", cfg.temp_blocks.len());
    for (i, t) in cfg.temp_blocks.iter().enumerate() {
        let _ = writeln!(out, "  temp {i} name={} size={}", t.name, t.size);
    }
    let _ = writeln!(out, "blocks {}", cfg.blocks.len());
    for (i, block) in cfg.blocks.iter().enumerate() {
        let _ = writeln!(out, "block {i}");
        let _ = writeln!(out, "  stmts {}", block.statements.len());
        for &stmt in &block.statements {
            out.push_str("    ");
            push_canonical_node(cfg, stmt, &mut out);
            out.push('\n');
        }
        out.push_str("  test ");
        push_canonical_node(cfg, block.test, &mut out);
        out.push('\n');
        let _ = writeln!(out, "  edges {}", block.outgoing.len());
        for edge in &block.outgoing {
            out.push_str("    edge ");
            match edge.cond {
                EdgeCond::None => out.push_str("none"),
                EdgeCond::Int(v) => {
                    let _ = write!(out, "i:{v}");
                }
                EdgeCond::Float(v) => {
                    let _ = write!(out, "f:0x{:016x}", v.to_bits());
                }
            }
            let _ = writeln!(out, " -> {}", edge.target);
        }
    }
    out
}

/// Renders one place in debug form, iteratively (mirrors Python `BlockPlace.__str__`
/// shape but with Rust formatting per decision D7).
fn push_debug_place(cfg: &Cfg, root: PlaceId, out: &mut String) {
    let mut stack = vec![Item::Place(root)];
    while let Some(item) = stack.pop() {
        match item {
            Item::Lit(s) => out.push_str(s),
            Item::Owned(s) => out.push_str(&s),
            Item::Place(id) => {
                let place = &cfg.places[id];
                // Single-slot temp block shorthand: just the name.
                if let (BlockValue::Temp(t), IndexValue::Int(0), 0) =
                    (place.block, place.index, place.offset)
                    && cfg.temp_blocks[t].size == 1
                {
                    out.push_str(&cfg.strings[cfg.temp_blocks[t].name]);
                    continue;
                }
                match place.index {
                    IndexValue::Int(i) => {
                        stack.push(Item::Owned(format!("[{}]", i.wrapping_add(place.offset))));
                    }
                    IndexValue::Place(p) => {
                        if place.offset == 0 {
                            stack.push(Item::Lit("]"));
                        } else {
                            stack.push(Item::Owned(format!(" + {}]", place.offset)));
                        }
                        stack.push(Item::Place(p));
                        stack.push(Item::Lit("["));
                    }
                }
                match place.block {
                    BlockValue::Int(v) => stack.push(Item::Owned(format!("{v}"))),
                    BlockValue::Temp(t) => {
                        stack.push(Item::Owned(cfg.strings[cfg.temp_blocks[t].name].clone()));
                    }
                    BlockValue::Place(p) => stack.push(Item::Place(p)),
                }
            }
            Item::Node(_) => unreachable!("places never contain expressions at the frontend level"),
        }
    }
}

/// Renders one node tree in debug form, iteratively.
fn push_debug_node(cfg: &Cfg, root: NodeId, out: &mut String) {
    let mut stack = vec![Item::Node(root)];
    while let Some(item) = stack.pop() {
        match item {
            Item::Lit(s) => out.push_str(s),
            Item::Owned(s) => out.push_str(&s),
            Item::Place(id) => push_debug_place(cfg, id, out),
            Item::Node(id) => match &cfg.nodes[id] {
                Node::ConstInt(v) => {
                    let _ = write!(out, "{v}");
                }
                Node::ConstFloat(v) => {
                    let _ = write!(out, "{v}");
                }
                Node::PureInstr { op, args } | Node::Instr { op, args } => {
                    let _ = write!(out, "{}(", op.name());
                    stack.push(Item::Lit(")"));
                    for (idx, &arg) in args.iter().enumerate().rev() {
                        stack.push(Item::Node(arg));
                        if idx > 0 {
                            stack.push(Item::Lit(", "));
                        }
                    }
                }
                Node::Get(place) => stack.push(Item::Place(*place)),
                Node::Set { place, value } => {
                    stack.push(Item::Node(*value));
                    stack.push(Item::Lit(" <- "));
                    stack.push(Item::Place(*place));
                }
            },
        }
    }
}

/// Returns true if the cond compares numerically equal to zero (mirrors the Python
/// `case {0: ...}` mapping-pattern match, where `0`, `0.0`, and `-0.0` keys all hit).
fn cond_is_zero(cond: EdgeCond) -> bool {
    match cond {
        EdgeCond::Int(v) => v == 0,
        EdgeCond::Float(v) => v == 0.0,
        EdgeCond::None => false,
    }
}

/// Renders a human-readable debug dump of a decoded CFG.
///
/// Mirrors the general shape of the Python `cfg_to_text` (block labels and `goto`
/// forms) but uses Rust's native float formatting and uniform `OpName(args)`
/// expression rendering (decision D7). Not a compatibility surface.
pub fn cfg_to_text(cfg: &Cfg) -> String {
    let mut out = String::new();
    for (i, block) in cfg.blocks.iter().enumerate() {
        let _ = writeln!(out, "{i}:");
        for &stmt in &block.statements {
            out.push_str("  ");
            push_debug_node(cfg, stmt, &mut out);
            out.push('\n');
        }
        match block.outgoing.as_slice() {
            [] => out.push_str("  goto exit\n"),
            [
                Edge {
                    cond: EdgeCond::None,
                    target,
                },
            ] => {
                let _ = writeln!(out, "  goto {target}");
            }
            [f_edge, t_edge] if cond_is_zero(f_edge.cond) && t_edge.cond == EdgeCond::None => {
                out.push_str("  goto ");
                let _ = write!(out, "{} if ", t_edge.target);
                push_debug_node(cfg, block.test, &mut out);
                let _ = writeln!(out, " else {}", f_edge.target);
            }
            edges => {
                out.push_str("  goto when ");
                push_debug_node(cfg, block.test, &mut out);
                out.push('\n');
                for edge in edges {
                    out.push_str("    ");
                    match edge.cond {
                        EdgeCond::None => out.push_str("default"),
                        EdgeCond::Int(v) => {
                            let _ = write!(out, "{v}");
                        }
                        EdgeCond::Float(v) => {
                            let _ = write!(out, "{v}");
                        }
                    }
                    let _ = writeln!(out, " -> {}", edge.target);
                }
            }
        }
    }
    out
}
