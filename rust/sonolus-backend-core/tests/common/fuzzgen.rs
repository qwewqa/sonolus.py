//! Proptest CFG fuzz generator (PORT.md T2.3).
//!
//! Generates **well-formed frontend-level** [`Cfg`]s as a small structured AST
//! ([`Program`]) built entirely from proptest strategies (so shrinking is
//! automatic), then lowers the AST to a `Cfg` with [`build_cfg`].
//!
//! # Coverage
//!
//! - Arithmetic expression trees over the full interpreter-supported pure-op
//!   set: every unary/binary/ternary/5-ary pure op plus the reduce-style
//!   variadic ops (`Add`/`Subtract`/`Multiply`/`Divide`/`Mod`/`Power`/`Rem`)
//!   with 1..=4 arguments.
//! - `Get`/`Set` on concrete blocks **and** sized `TempBlock`s, with constant
//!   and *dynamic* indices. A dynamic index is realized the way the frontend
//!   does it — the index expression is stored to a dedicated single-slot temp
//!   and the place's index is a nested place reading it — and is clamped
//!   into-range with `Floor(Mod(Mod(..)))` (see [`Builder::dyn_index`] for
//!   why both the double Mod and the Floor are load-bearing), so traps stay a
//!   minority (NaN/inf values still trap, deliberately, inside the `Floor`).
//! - Nested places in both positions: dynamic indices give
//!   `IndexValue::Place`; [`Expr::ReadNested`] gives `BlockValue::Place` (the
//!   block id is read from cell `21[31]`, initialized to a real block id in
//!   the entry block — and occasionally clobbered by the program's own writes,
//!   which is deterministic and identical on both sides of a diff).
//! - `NaN`/`±inf`/int-tagged/float-tagged constants (NaN/inf compile to
//!   `EngineRom` reads; the diff harness provides ROM like the runtime).
//! - `And`/`Or` with effectful arguments (`DebugLog` inside), plus
//!   `Random`/`RandomInteger` draws and `Break` (the frontend return) both as
//!   a final return and as a mid-program statement.
//! - Multi-block CFGs: diamonds (int- and float-cond), switches with int AND
//!   float conds and optional default, and **bounded** counter-based loops
//!   (init / increment / compare on a dedicated counter temp no other
//!   statement can touch — termination by construction; the interpreter eval
//!   budget is the backstop). Loops nest via `prop_recursive`.
//!
//! # Well-formedness invariants (what "well-formed" means here)
//!
//! - Block 0 is the entry; every edge targets an existing block; edges are
//!   strictly sorted by `(cond is None, cond)` with numerically distinct,
//!   non-NaN conds and at most one trailing unconditional edge.
//! - `Set` only in statement position; `PureInstr` only for pure ops; no
//!   control-flow ops in expression position (other than `And`/`Or`, and
//!   `Break` as a statement).
//! - Temp accesses are in-bounds by construction (constant indices are
//!   reduced mod the temp size; dynamic indices are
//!   `Floor(Mod(Mod(..)))`-clamped), and every temp-pool cell is initialized
//!   in the entry block, so no temp read ever observes allocator-placed
//!   (pipeline-specific) memory, and the index assert can never be
//!   allocation-dependent (the [`Builder::dyn_index`] docs explain both
//!   hazards).
//!
//! # No recursion over generated structures
//!
//! Generated values are *bounded* by the strategies (`prop_recursive` depth),
//! but [`build_cfg`] still lowers both expressions and shapes with explicit
//! work stacks, per invariant §3.4.

#![allow(dead_code)] // compiled per test binary; not every binary uses everything

use proptest::collection::vec;
use proptest::option;
use proptest::prelude::*;
use sonolus_backend_core::cfg::{
    BasicBlock, BlockValue, Cfg, Edge, EdgeCond, IndexValue, Node, Place, TempBlockDef,
};
use sonolus_backend_core::ops::Op;

/// Concrete blocks generated programs may read (includes a negative test-style
/// id and `EngineRom` 3000). Never 10000 (temp memory is pipeline-private).
pub const READ_BLOCKS: [i64; 5] = [-3, 20, 21, 1000, 3000];
/// Concrete blocks generated programs may write (no ROM, no temp memory).
pub const WRITE_BLOCKS: [i64; 3] = [20, 21, 1000];
/// The shared general-purpose temp pool: `(name, size)`. Sized > 1 so dynamic
/// indexing inside a temp block is meaningful.
pub const TEMP_POOL: [(&str, u64); 4] = [("g0", 1), ("g1", 4), ("g2", 8), ("g3", 16)];

/// Entry-block scaffolding node count: the nested-place block-id init plus one
/// `Set` of every temp-pool cell (2 nodes each: the value const and the Set).
/// Exposed so the canary shrink-size assertion can subtract the constant
/// scaffolding every generated CFG carries.
pub const SCAFFOLD_NODES: usize = 2 * (1 + (1 + 4 + 8 + 16));

/// The cell holding the block id used by [`Expr::ReadNested`] (initialized in
/// the entry block to `READ_BLOCKS[program.nested_target]`).
const BID_BLOCK: i64 = 21;
const BID_INDEX: i64 = 31;

/// Unary pure ops (interpreter-supported). Total-domain ops first — the
/// domain-restricted tail (`Log`/`Arcsin`/`Arccos`) is generated with lower
/// weight (and shrinking moves toward the front) so traps stay a minority.
pub const UNARY_OPS: [Op; 21] = [
    Op::Abs,
    Op::Negate,
    Op::Not,
    Op::Sign,
    Op::Floor,
    Op::Ceil,
    Op::Round,
    Op::Trunc,
    Op::Frac,
    Op::Sin,
    Op::Cos,
    Op::Tan,
    Op::Tanh,
    Op::Sinh,
    Op::Cosh,
    Op::Arctan,
    Op::Degree,
    Op::Radian,
    Op::Log,
    Op::Arcsin,
    Op::Arccos,
];

/// Reduce-style variadic pure ops (generated with 1..=4 args; the MIR
/// binarizes them). `Add` first: shrinking op choices converges on it. The
/// total ops (first three) are generated with higher weight than the
/// trap-capable tail (`Divide`/`Mod`/`Rem` by zero, `Power` domain/overflow)
/// so traps stay a minority.
pub const REDUCE_OPS: [Op; 7] = [
    Op::Add,
    Op::Multiply,
    Op::Subtract,
    Op::Divide,
    Op::Mod,
    Op::Rem,
    Op::Power,
];
/// How many leading `REDUCE_OPS` / `UNARY_OPS` entries are total (trap-free).
const REDUCE_TOTAL: usize = 3;
const UNARY_TOTAL: usize = 18;

/// Weighted op-index strategy: mostly total ops, occasionally trap-capable.
fn weighted_op_index(total: usize, len: usize) -> BoxedStrategy<usize> {
    prop_oneof![
        9 => (0..total).boxed(),
        1 => (total..len).boxed(),
    ]
    .boxed()
}

/// Fixed-arity pure ops: `(op, arity)`.
pub const FIXED_OPS: [(Op, usize); 16] = [
    (Op::Min, 2),
    (Op::Max, 2),
    (Op::Equal, 2),
    (Op::NotEqual, 2),
    (Op::Less, 2),
    (Op::LessOr, 2),
    (Op::Greater, 2),
    (Op::GreaterOr, 2),
    (Op::Arctan2, 2),
    (Op::Clamp, 3),
    (Op::Lerp, 3),
    (Op::LerpClamped, 3),
    (Op::Unlerp, 3),
    (Op::UnlerpClamped, 3),
    (Op::Remap, 5),
    (Op::RemapClamped, 5),
];

/// Int constant pool (index-shaped values first; includes 1 — load-bearing for
/// `Add(x, 1)`-style patterns — plus boundary-ish and huge-but-exact values).
pub const INT_POOL: [i64; 10] = [0, 1, 2, 3, -1, 7, 16, 100, 65535, 1 << 40];

/// Float constant pool: float-tagged values incl. NaN/±inf (which emit as
/// `EngineRom` reads), `-0.0`, integral floats (int/float tag coverage), and
/// magnitude extremes.
pub const FLOAT_POOL: [f64; 14] = [
    0.0,
    1.0,
    -1.0,
    0.5,
    -0.5,
    2.5,
    3.0,
    100.0,
    -0.0,
    1e-9,
    1e9,
    f64::NAN,
    f64::INFINITY,
    f64::NEG_INFINITY,
];

// ----------------------------------------------------------------------------------
// AST
// ----------------------------------------------------------------------------------

/// A place index: a constant, or a dynamic expression (Mod-clamped into range
/// and routed through a dedicated index temp at build time).
#[derive(Debug, Clone)]
pub enum Index {
    Const(u8),
    Dyn(Box<Expr>),
}

/// An expression. Const variants index the pools (compact `Debug`, shrinks
/// toward simple values); op variants index the op tables.
#[derive(Debug, Clone)]
pub enum Expr {
    /// `INT_POOL[i]` as an int-tagged constant.
    Int(usize),
    /// `FLOAT_POOL[i]` as a float-tagged constant.
    Float(usize),
    /// `Get` on `READ_BLOCKS[block]`.
    ReadConcrete {
        block: usize,
        index: Index,
        offset: u8,
    },
    /// `Get` on `TEMP_POOL[temp]` (constant indices reduced mod the size).
    ReadTemp { temp: usize, index: Index },
    /// `Get` through a nested block place: block id read from `21[31]`.
    ReadNested { index: u8, offset: u8 },
    /// `UNARY_OPS[op](arg)`.
    Unary { op: usize, arg: Box<Expr> },
    /// `REDUCE_OPS[op](args...)`, 1..=4 args (variadic at the frontend level).
    Reduce { op: usize, args: Vec<Expr> },
    /// `FIXED_OPS[op].0(args...)` with exactly `FIXED_OPS[op].1` args.
    Fixed { op: usize, args: Vec<Expr> },
    /// Short-circuit `And`/`Or` with 1..=4 args (args may be effectful).
    Sc { is_or: bool, args: Vec<Expr> },
    /// `DebugLog(arg)` (effectful; evaluates to 0.0).
    Log(Box<Expr>),
    /// `Random(lo, lo + width + 1)` with constant bounds.
    RandomUniform { lo: i8, width: u8 },
    /// `RandomInteger(0, max(hi, 1))` with constant bounds.
    RandomInt { hi: u8 },
}

/// A statement.
#[derive(Debug, Clone)]
pub enum Stmt {
    /// `Set` on `WRITE_BLOCKS[block]`.
    SetConcrete {
        block: usize,
        index: Index,
        value: Expr,
    },
    /// `Set` on `TEMP_POOL[temp]`.
    SetTemp {
        temp: usize,
        index: Index,
        value: Expr,
    },
    /// `DebugLog(expr)` as a statement.
    Log(Expr),
    /// `Break(1, expr)` — the frontend return; everything after is dead.
    Ret(Expr),
}

/// One switch case: cond value `cond` (0..=6), float-tagged `cond + 0.5` when
/// `cond_half` (covers int AND float conds in one switch).
#[derive(Debug, Clone)]
pub struct SwitchCase {
    pub cond_half: bool,
    pub cond: u8,
    pub stmts: Vec<Stmt>,
}

/// A control-flow shape. Shapes compose sequentially; loops nest shapes.
#[derive(Debug, Clone)]
pub enum Shape {
    /// Straight-line statements in the current block.
    Straight(Vec<Stmt>),
    /// Two-way branch on `cond` (`0`/`0.0` edge to else, default to then),
    /// merging afterwards.
    Diamond {
        cond: Expr,
        float_cond: bool,
        then_stmts: Vec<Stmt>,
        else_stmts: Vec<Stmt>,
    },
    /// Multi-way switch; cases deduped by numeric cond value and sorted.
    /// Without a default, an unmatched scrutinee exits the callback (the
    /// frontend-level semantics of cond-only edges).
    Switch {
        scrutinee: Expr,
        /// Wrap the scrutinee in `Mod(.., 8)` so cases actually get hit.
        wrap_mod: bool,
        cases: Vec<SwitchCase>,
        default: Option<Vec<Stmt>>,
    },
    /// Bounded counter loop: `for c in 0..trips { body }` on a dedicated
    /// counter temp that nothing else can write. Terminates by construction.
    Loop { trips: u8, body: Vec<Shape> },
}

/// A whole generated program.
#[derive(Debug, Clone)]
pub struct Program {
    /// Which `READ_BLOCKS` entry the nested-place block-id cell points at.
    pub nested_target: usize,
    pub shapes: Vec<Shape>,
    /// Optional final `Break(1, expr)` return value.
    pub ret: Option<Expr>,
}

// ----------------------------------------------------------------------------------
// Strategies
// ----------------------------------------------------------------------------------

fn const_index() -> impl Strategy<Value = Index> {
    (0..32u8).prop_map(Index::Const)
}

/// Index-shaped expressions for dynamic indices: usually-integral values
/// (memory reads of mostly-small-int fills, int consts, `RandomInteger`
/// draws, sums of those), so `Mod`-clamped dynamic indexing mostly works and
/// traps stay a minority. Arbitrary expressions are still mixed in by the
/// callers as a minority.
fn index_shaped() -> BoxedStrategy<Expr> {
    let atom = prop_oneof![
        3 => (0..READ_BLOCKS.len(), const_index(), 0..3u8)
            .prop_map(|(block, index, offset)| Expr::ReadConcrete { block, index, offset }),
        2 => (0..INT_POOL.len()).prop_map(Expr::Int),
        2 => (0..TEMP_POOL.len(), const_index())
            .prop_map(|(temp, index)| Expr::ReadTemp { temp, index }),
        1 => (1..100u8).prop_map(|hi| Expr::RandomInt { hi }),
    ]
    .boxed();
    prop_oneof![
        3 => atom.clone(),
        1 => vec(atom, 2..=3).prop_map(|args| Expr::Reduce { op: 0, args }), // Add
    ]
    .boxed()
}

fn leaf_expr() -> BoxedStrategy<Expr> {
    prop_oneof![
        4 => (0..INT_POOL.len()).prop_map(Expr::Int),
        3 => (0..FLOAT_POOL.len()).prop_map(Expr::Float),
        4 => (0..READ_BLOCKS.len(), const_index(), 0..3u8)
            .prop_map(|(block, index, offset)| Expr::ReadConcrete { block, index, offset }),
        3 => (0..TEMP_POOL.len(), const_index())
            .prop_map(|(temp, index)| Expr::ReadTemp { temp, index }),
        1 => (0..16u8, 0..3u8).prop_map(|(index, offset)| Expr::ReadNested { index, offset }),
        1 => (-5..5i8, 0..20u8).prop_map(|(lo, width)| Expr::RandomUniform { lo, width }),
        1 => (1..100u8).prop_map(|hi| Expr::RandomInt { hi }),
    ]
    .boxed()
}

/// The full expression strategy (recursive, bounded depth — shrinking is
/// proptest-automatic).
pub fn expr() -> BoxedStrategy<Expr> {
    leaf_expr()
        .prop_recursive(4, 24, 3, |inner| {
            let dyn_index = prop_oneof![
                4 => const_index().boxed(),
                3 => index_shaped().prop_map(|e| Index::Dyn(Box::new(e))).boxed(),
                1 => inner.clone().prop_map(|e| Index::Dyn(Box::new(e))).boxed(),
            ];
            let sc_arg = prop_oneof![
                3 => inner.clone(),
                1 => inner.clone().prop_map(|e| Expr::Log(Box::new(e))),
            ];
            let fixed = (0..FIXED_OPS.len()).prop_flat_map({
                let inner = inner.clone();
                move |op| {
                    vec(inner.clone(), FIXED_OPS[op].1)
                        .prop_map(move |args| Expr::Fixed { op, args })
                }
            });
            prop_oneof![
                3 => (weighted_op_index(UNARY_TOTAL, UNARY_OPS.len()), inner.clone())
                    .prop_map(|(op, arg)| Expr::Unary { op, arg: Box::new(arg) }),
                4 => (weighted_op_index(REDUCE_TOTAL, REDUCE_OPS.len()), vec(inner.clone(), 1..=4))
                    .prop_map(|(op, args)| Expr::Reduce { op, args }),
                3 => fixed,
                2 => (any::<bool>(), vec(sc_arg, 1..=4))
                    .prop_map(|(is_or, args)| Expr::Sc { is_or, args }),
                1 => inner.clone().prop_map(|e| Expr::Log(Box::new(e))),
                2 => (0..READ_BLOCKS.len(), dyn_index.clone(), 0..3u8)
                    .prop_map(|(block, index, offset)| Expr::ReadConcrete { block, index, offset }),
                2 => (0..TEMP_POOL.len(), dyn_index)
                    .prop_map(|(temp, index)| Expr::ReadTemp { temp, index }),
            ]
            .boxed()
        })
        .boxed()
}

fn stmt_index() -> impl Strategy<Value = Index> {
    prop_oneof![
        6 => const_index().boxed(),
        3 => index_shaped().prop_map(|e| Index::Dyn(Box::new(e))).boxed(),
        1 => expr().prop_map(|e| Index::Dyn(Box::new(e))).boxed(),
    ]
}

/// One statement.
pub fn stmt() -> BoxedStrategy<Stmt> {
    prop_oneof![
        4 => (0..WRITE_BLOCKS.len(), stmt_index(), expr())
            .prop_map(|(block, index, value)| Stmt::SetConcrete { block, index, value }),
        4 => (0..TEMP_POOL.len(), stmt_index(), expr())
            .prop_map(|(temp, index, value)| Stmt::SetTemp { temp, index, value }),
        2 => expr().prop_map(Stmt::Log),
        1 => expr().prop_map(Stmt::Ret),
    ]
    .boxed()
}

fn stmts() -> impl Strategy<Value = Vec<Stmt>> {
    vec(stmt(), 1..=4)
}

/// Loop-free shapes: straight-line, diamonds, switches.
pub fn shape_loop_free() -> BoxedStrategy<Shape> {
    let switch_case =
        (any::<bool>(), 0..=6u8, stmts()).prop_map(|(cond_half, cond, stmts)| SwitchCase {
            cond_half,
            cond,
            stmts,
        });
    prop_oneof![
        3 => stmts().prop_map(Shape::Straight),
        2 => (expr(), any::<bool>(), stmts(), stmts()).prop_map(
            |(cond, float_cond, then_stmts, else_stmts)| Shape::Diamond {
                cond,
                float_cond,
                then_stmts,
                else_stmts,
            }
        ),
        2 => (expr(), any::<bool>(), vec(switch_case, 1..=4), option::of(stmts())).prop_map(
            |(scrutinee, wrap_mod, cases, default)| Shape::Switch {
                scrutinee,
                wrap_mod,
                cases,
                default,
            }
        ),
    ]
    .boxed()
}

/// One control-flow shape (loops nest via `prop_recursive`).
pub fn shape() -> BoxedStrategy<Shape> {
    shape_loop_free()
        .prop_recursive(2, 6, 2, |inner| {
            (1..=5u8, vec(inner, 1..=2))
                .prop_map(|(trips, body)| Shape::Loop { trips, body })
                .boxed()
        })
        .boxed()
}

fn program_from_shapes(shapes: BoxedStrategy<Shape>) -> BoxedStrategy<Program> {
    (0..READ_BLOCKS.len(), vec(shapes, 1..=4), option::of(expr()))
        .prop_map(|(nested_target, shapes, ret)| Program {
            nested_target,
            shapes,
            ret,
        })
        .boxed()
}

/// A whole program.
pub fn program() -> BoxedStrategy<Program> {
    program_from_shapes(shape())
}

/// A whole program without loops. Used by the canary-shrinking test: the
/// canary breaks loop counter increments, so loop-bearing failures either
/// become budget-inconclusive or bottom out in local minima that must retain
/// whole-loop scaffolding — loop-free programs shrink to crisp straight-line
/// counterexamples instead. (Loop coverage for the canary lives in the corpus
/// canary test; loop coverage for the real fuzz net lives in [`program`].)
pub fn program_loop_free() -> BoxedStrategy<Program> {
    program_from_shapes(shape_loop_free())
}

// ----------------------------------------------------------------------------------
// AST -> Cfg lowering
// ----------------------------------------------------------------------------------

struct Builder {
    cfg: Cfg,
    /// The open block currently receiving statements.
    cur: usize,
    /// Fresh-temp counters (index temps `i{n}`, loop counters `c{n}`).
    idx_temps: usize,
    loop_temps: usize,
}

impl Builder {
    fn node(&mut self, n: Node) -> usize {
        self.cfg.nodes.push(n);
        self.cfg.nodes.len() - 1
    }

    fn place(&mut self, p: Place) -> usize {
        self.cfg.places.push(p);
        self.cfg.places.len() - 1
    }

    fn new_temp(&mut self, name: String, size: u64) -> usize {
        self.cfg.strings.push(name);
        self.cfg.temp_blocks.push(TempBlockDef {
            name: self.cfg.strings.len() - 1,
            size,
        });
        self.cfg.temp_blocks.len() - 1
    }

    fn new_block(&mut self) -> usize {
        self.cfg.blocks.push(BasicBlock::default());
        self.cfg.blocks.len() - 1
    }

    fn push_stmt(&mut self, node: usize) {
        let cur = self.cur;
        self.cfg.blocks[cur].statements.push(node);
    }

    /// `PureInstr` for pure ops, `Instr` otherwise (a decoder invariant).
    fn op_node(&mut self, op: Op, args: Vec<usize>) -> usize {
        if op.pure() {
            self.node(Node::PureInstr { op, args })
        } else {
            self.node(Node::Instr { op, args })
        }
    }

    fn const_int(&mut self, v: i64) -> usize {
        self.node(Node::ConstInt(v))
    }

    fn zero_test(&mut self) -> usize {
        self.const_int(0)
    }

    fn finalize(&mut self, block: usize, test: usize, outgoing: Vec<Edge>) {
        let b = &mut self.cfg.blocks[block];
        b.test = test;
        b.outgoing = outgoing;
    }

    fn temp_place(&mut self, temp: usize, index: IndexValue, offset: i64) -> usize {
        self.place(Place {
            block: BlockValue::Temp(temp),
            index,
            offset,
        })
    }

    /// Routes a dynamic index through a fresh single-slot temp:
    /// `i{n} <- Floor(Mod(Mod(child, range), range))`, then
    /// `IndexValue::Place(read of i{n})`. This combines two independently
    /// discovered, load-bearing fixes (W1 merge):
    ///
    /// - **Double Mod** (T3.1 SCCP fuzz): a single floor-mod of a tiny
    ///   negative value rounds to exactly `range` in f64 (`m + range` with
    ///   `m -> -0`), which would index one past the temp block — an
    ///   out-of-bounds temp access observes allocator-placed memory and
    ///   breaks minimal-vs-optimized differential soundness. The outer Mod of
    ///   a value in `[0, range]` is a pure positive `fmod` (exact): it maps
    ///   `range` to `0` and leaves everything else unchanged.
    /// - **Floor** (T3.3 DCE fuzz): temp-slot allocation is pipeline-specific
    ///   and the emitter adds the temp's allocated base offset to the index,
    ///   so a finite *non-integral* index makes the index assert
    ///   (`Value must be an integer`) allocation-dependent — `tiny + base`
    ///   can round to an exact integer for one pipeline's base and not
    ///   another's (caught when removing a dead store shifted every temp
    ///   offset). Floor of the exact in-range value is integral in
    ///   `[0, range-1]`.
    ///
    /// NaN/inf still trap (inside `Floor`, Python `math.floor` semantics),
    /// deliberately and allocation-independently: `NaN` survives both Mods.
    fn dyn_index(&mut self, child: usize, range: i64) -> IndexValue {
        let t = self.new_temp(format!("i{}", self.idx_temps), 1);
        self.idx_temps += 1;
        let range_c = self.const_int(range);
        let m1 = self.op_node(Op::Mod, vec![child, range_c]);
        let range_c2 = self.const_int(range);
        let m2 = self.op_node(Op::Mod, vec![m1, range_c2]);
        let clamped = self.op_node(Op::Floor, vec![m2]);
        let write = self.temp_place(t, IndexValue::Int(0), 0);
        let set = self.node(Node::Set {
            place: write,
            value: clamped,
        });
        self.push_stmt(set);
        let read = self.temp_place(t, IndexValue::Int(0), 0);
        IndexValue::Place(read)
    }

    /// Lowers one expression tree iteratively (explicit Visit/Build stack);
    /// returns the root node id. Dynamic indices emit their clamp-and-store
    /// prep statements into the open block as they complete.
    #[allow(clippy::too_many_lines)]
    fn emit_expr(&mut self, root: &Expr) -> usize {
        enum Work<'a> {
            Visit(&'a Expr),
            Build(&'a Expr),
        }
        let mut work: Vec<Work<'_>> = vec![Work::Visit(root)];
        let mut results: Vec<usize> = Vec::new();
        while let Some(item) = work.pop() {
            match item {
                Work::Visit(e) => {
                    work.push(Work::Build(e));
                    match e {
                        Expr::Unary { arg, .. } | Expr::Log(arg) => work.push(Work::Visit(arg)),
                        Expr::Reduce { args, .. }
                        | Expr::Fixed { args, .. }
                        | Expr::Sc { args, .. } => {
                            for a in args.iter().rev() {
                                work.push(Work::Visit(a));
                            }
                        }
                        Expr::ReadConcrete {
                            index: Index::Dyn(e),
                            ..
                        }
                        | Expr::ReadTemp {
                            index: Index::Dyn(e),
                            ..
                        } => work.push(Work::Visit(e)),
                        _ => {}
                    }
                }
                Work::Build(e) => {
                    let id = match e {
                        Expr::Int(i) => self.const_int(INT_POOL[*i]),
                        Expr::Float(i) => self.node(Node::ConstFloat(FLOAT_POOL[*i])),
                        Expr::ReadConcrete {
                            block,
                            index,
                            offset,
                        } => {
                            let index = match index {
                                Index::Const(c) => IndexValue::Int(i64::from(*c)),
                                Index::Dyn(_) => {
                                    let child = results.pop().expect("dyn index child");
                                    self.dyn_index(child, 32)
                                }
                            };
                            let p = self.place(Place {
                                block: BlockValue::Int(READ_BLOCKS[*block]),
                                index,
                                offset: i64::from(*offset),
                            });
                            self.node(Node::Get(p))
                        }
                        Expr::ReadTemp { temp, index } => {
                            #[allow(clippy::cast_possible_wrap)]
                            let size = TEMP_POOL[*temp].1 as i64;
                            let index = match index {
                                Index::Const(c) => IndexValue::Int(i64::from(*c) % size),
                                Index::Dyn(_) => {
                                    let child = results.pop().expect("dyn index child");
                                    self.dyn_index(child, size)
                                }
                            };
                            let p = self.temp_place(*temp, index, 0);
                            self.node(Node::Get(p))
                        }
                        Expr::ReadNested { index, offset } => {
                            let inner = self.place(Place {
                                block: BlockValue::Int(BID_BLOCK),
                                index: IndexValue::Int(BID_INDEX),
                                offset: 0,
                            });
                            let outer = self.place(Place {
                                block: BlockValue::Place(inner),
                                index: IndexValue::Int(i64::from(*index % 16)),
                                offset: i64::from(*offset),
                            });
                            self.node(Node::Get(outer))
                        }
                        Expr::Unary { op, .. } => {
                            let a = results.pop().expect("unary arg");
                            self.op_node(UNARY_OPS[*op], vec![a])
                        }
                        Expr::Reduce { op, args } => {
                            let xs = results.split_off(results.len() - args.len());
                            self.op_node(REDUCE_OPS[*op], xs)
                        }
                        Expr::Fixed { op, args } => {
                            let xs = results.split_off(results.len() - args.len());
                            self.op_node(FIXED_OPS[*op].0, xs)
                        }
                        Expr::Sc { is_or, args } => {
                            let xs = results.split_off(results.len() - args.len());
                            self.op_node(if *is_or { Op::Or } else { Op::And }, xs)
                        }
                        Expr::Log(_) => {
                            let a = results.pop().expect("log arg");
                            self.op_node(Op::DebugLog, vec![a])
                        }
                        Expr::RandomUniform { lo, width } => {
                            let l = self.const_int(i64::from(*lo));
                            let h = self.const_int(i64::from(*lo) + i64::from(*width) + 1);
                            self.op_node(Op::Random, vec![l, h])
                        }
                        Expr::RandomInt { hi } => {
                            let l = self.const_int(0);
                            let h = self.const_int(i64::from(*hi).max(1));
                            self.op_node(Op::RandomInteger, vec![l, h])
                        }
                    };
                    results.push(id);
                }
            }
        }
        results.pop().expect("expression produced a value")
    }

    /// Emits one statement into the open block.
    fn emit_stmt(&mut self, stmt: &Stmt) {
        match stmt {
            Stmt::SetConcrete {
                block,
                index,
                value,
            } => {
                let index = match index {
                    Index::Const(c) => IndexValue::Int(i64::from(*c)),
                    Index::Dyn(e) => {
                        let child = self.emit_expr(e);
                        self.dyn_index(child, 32)
                    }
                };
                let v = self.emit_expr(value);
                let p = self.place(Place {
                    block: BlockValue::Int(WRITE_BLOCKS[*block]),
                    index,
                    offset: 0,
                });
                let set = self.node(Node::Set { place: p, value: v });
                self.push_stmt(set);
            }
            Stmt::SetTemp { temp, index, value } => {
                #[allow(clippy::cast_possible_wrap)]
                let size = TEMP_POOL[*temp].1 as i64;
                let index = match index {
                    Index::Const(c) => IndexValue::Int(i64::from(*c) % size),
                    Index::Dyn(e) => {
                        let child = self.emit_expr(e);
                        self.dyn_index(child, size)
                    }
                };
                let v = self.emit_expr(value);
                let p = self.temp_place(*temp, index, 0);
                let set = self.node(Node::Set { place: p, value: v });
                self.push_stmt(set);
            }
            Stmt::Log(e) => {
                let v = self.emit_expr(e);
                let log = self.op_node(Op::DebugLog, vec![v]);
                self.push_stmt(log);
            }
            Stmt::Ret(e) => {
                let one = self.const_int(1);
                let v = self.emit_expr(e);
                let brk = self.op_node(Op::Break, vec![one, v]);
                self.push_stmt(brk);
            }
        }
    }

    fn emit_stmts(&mut self, stmts: &[Stmt]) {
        for s in stmts {
            self.emit_stmt(s);
        }
    }
}

/// Work items for the iterative shape-lowering stack in [`build_cfg`].
enum Work<'a> {
    Shape(&'a Shape),
    /// Close an open loop body: emit the counter increment, jump back to
    /// the header, and continue in the `after` block.
    CloseLoop {
        counter: usize,
        header: usize,
        after: usize,
    },
}

/// Lowers a [`Program`] to a well-formed frontend-level [`Cfg`]. Deterministic;
/// iterative over both shapes and expressions.
#[allow(clippy::too_many_lines)]
pub fn build_cfg(p: &Program) -> Cfg {
    let mut b = Builder {
        cfg: Cfg {
            version: sonolus_backend_core::decode::ENCODING_VERSION,
            op_count: Op::COUNT,
            ..Cfg::default()
        },
        cur: 0,
        idx_temps: 0,
        loop_temps: 0,
    };
    for (name, size) in TEMP_POOL {
        b.new_temp(name.to_owned(), size);
    }
    b.cur = b.new_block(); // entry = block 0

    // Initialize the nested-place block-id cell.
    let bid = b.const_int(READ_BLOCKS[p.nested_target]);
    let slot = b.place(Place {
        block: BlockValue::Int(BID_BLOCK),
        index: IndexValue::Int(BID_INDEX),
        offset: 0,
    });
    let init = b.node(Node::Set {
        place: slot,
        value: bid,
    });
    b.push_stmt(init);

    // Initialize every temp-pool cell with a small deterministic value.
    // Load-bearing for differential soundness: a read of a never-written temp
    // cell observes whatever the *slot allocator* placed there (block 10000
    // is shared and allocation is pipeline-specific by contract), so two
    // correct pipelines can legitimately disagree on such reads once an
    // optimization changes liveness/allocation. Generated programs must be
    // free of read-before-write temp accesses for minimal-vs-optimized
    // differential comparison to be meaningful. (Found by the T3.1 SCCP fuzz
    // run: a dropped never-evaluated lazy load shifted the allocation and a
    // read-only temp aliased a loop counter on one side only.)
    #[allow(clippy::cast_possible_wrap)]
    for (t, (_, size)) in TEMP_POOL.iter().enumerate() {
        for i in 0..*size {
            let v = b.const_int(((t as i64) * 7 + (i as i64) * 5) % 19 - 3);
            let place = b.temp_place(t, IndexValue::Int(i as i64), 0);
            let set = b.node(Node::Set { place, value: v });
            b.push_stmt(set);
        }
    }

    let mut work: Vec<Work<'_>> = p.shapes.iter().rev().map(Work::Shape).collect();
    while let Some(item) = work.pop() {
        match item {
            Work::Shape(Shape::Straight(stmts)) => b.emit_stmts(stmts),
            Work::Shape(Shape::Diamond {
                cond,
                float_cond,
                then_stmts,
                else_stmts,
            }) => {
                let test = b.emit_expr(cond);
                let then_b = b.new_block();
                let else_b = b.new_block();
                let merge = b.new_block();
                let zero_cond = if *float_cond {
                    EdgeCond::Float(0.0)
                } else {
                    EdgeCond::Int(0)
                };
                let entry = b.cur;
                b.finalize(
                    entry,
                    test,
                    vec![
                        Edge {
                            cond: zero_cond,
                            target: else_b,
                        },
                        Edge {
                            cond: EdgeCond::None,
                            target: then_b,
                        },
                    ],
                );
                for (block, stmts) in [(then_b, then_stmts), (else_b, else_stmts)] {
                    b.cur = block;
                    b.emit_stmts(stmts);
                    let zt = b.zero_test();
                    b.finalize(
                        block,
                        zt,
                        vec![Edge {
                            cond: EdgeCond::None,
                            target: merge,
                        }],
                    );
                }
                b.cur = merge;
            }
            Work::Shape(Shape::Switch {
                scrutinee,
                wrap_mod,
                cases,
                default,
            }) => {
                let mut test = b.emit_expr(scrutinee);
                if *wrap_mod {
                    let eight = b.const_int(8);
                    test = b.op_node(Op::Mod, vec![test, eight]);
                }
                // Dedupe by numeric cond value (first occurrence wins), then
                // sort ascending — the strict edge-order invariant.
                let mut kept: Vec<(f64, &SwitchCase)> = Vec::new();
                for case in cases {
                    let value = f64::from(case.cond) + if case.cond_half { 0.5 } else { 0.0 };
                    if !kept.iter().any(|&(v, _)| v.to_bits() == value.to_bits()) {
                        kept.push((value, case));
                    }
                }
                kept.sort_by(|a, b| a.0.total_cmp(&b.0));
                let merge = b.new_block();
                let mut edges: Vec<Edge> = Vec::new();
                let mut arms: Vec<(usize, &[Stmt])> = Vec::new();
                for (value, case) in kept {
                    let arm = b.new_block();
                    let cond = if case.cond_half {
                        EdgeCond::Float(value)
                    } else {
                        EdgeCond::Int(i64::from(case.cond))
                    };
                    edges.push(Edge { cond, target: arm });
                    arms.push((arm, &case.stmts));
                }
                if let Some(default_stmts) = default {
                    let arm = b.new_block();
                    edges.push(Edge {
                        cond: EdgeCond::None,
                        target: arm,
                    });
                    arms.push((arm, default_stmts));
                }
                let entry = b.cur;
                b.finalize(entry, test, edges);
                for (arm, stmts) in arms {
                    b.cur = arm;
                    b.emit_stmts(stmts);
                    let zt = b.zero_test();
                    b.finalize(
                        arm,
                        zt,
                        vec![Edge {
                            cond: EdgeCond::None,
                            target: merge,
                        }],
                    );
                }
                b.cur = merge;
            }
            Work::Shape(Shape::Loop { trips, body }) => {
                let counter = b.new_temp(format!("c{}", b.loop_temps), 1);
                b.loop_temps += 1;
                // init: counter <- 0 in the current block, then jump to header.
                let zero = b.const_int(0);
                let cw = b.temp_place(counter, IndexValue::Int(0), 0);
                let set = b.node(Node::Set {
                    place: cw,
                    value: zero,
                });
                b.push_stmt(set);
                let header = b.new_block();
                let body_b = b.new_block();
                let after = b.new_block();
                let entry = b.cur;
                let zt = b.zero_test();
                b.finalize(
                    entry,
                    zt,
                    vec![Edge {
                        cond: EdgeCond::None,
                        target: header,
                    }],
                );
                // header: test Less(counter, trips); 0 -> after, default -> body.
                let cr = b.temp_place(counter, IndexValue::Int(0), 0);
                let get = b.node(Node::Get(cr));
                let trips_c = b.const_int(i64::from(*trips));
                let less = b.op_node(Op::Less, vec![get, trips_c]);
                b.finalize(
                    header,
                    less,
                    vec![
                        Edge {
                            cond: EdgeCond::Int(0),
                            target: after,
                        },
                        Edge {
                            cond: EdgeCond::None,
                            target: body_b,
                        },
                    ],
                );
                b.cur = body_b;
                work.push(Work::CloseLoop {
                    counter,
                    header,
                    after,
                });
                for shape in body.iter().rev() {
                    work.push(Work::Shape(shape));
                }
            }
            Work::CloseLoop {
                counter,
                header,
                after,
            } => {
                // counter <- Add(counter, 1); jump back to the header.
                let cr = b.temp_place(counter, IndexValue::Int(0), 0);
                let get = b.node(Node::Get(cr));
                let one = b.const_int(1);
                let add = b.op_node(Op::Add, vec![get, one]);
                let cw = b.temp_place(counter, IndexValue::Int(0), 0);
                let set = b.node(Node::Set {
                    place: cw,
                    value: add,
                });
                b.push_stmt(set);
                let cur = b.cur;
                let zt = b.zero_test();
                b.finalize(
                    cur,
                    zt,
                    vec![Edge {
                        cond: EdgeCond::None,
                        target: header,
                    }],
                );
                b.cur = after;
            }
        }
    }

    if let Some(ret) = &p.ret {
        let one = b.const_int(1);
        let v = b.emit_expr(ret);
        let brk = b.op_node(Op::Break, vec![one, v]);
        b.push_stmt(brk);
    }
    let cur = b.cur;
    let zt = b.zero_test();
    b.finalize(cur, zt, vec![]);
    b.cfg
}
