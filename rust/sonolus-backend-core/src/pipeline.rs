//! Pipeline entry point (PORT.md T1.3): `compile_cfg(cfg, level)`.
//!
//! `Level::Minimal` is the Rust baseline pipeline, the equivalent of the legacy
//! `MINIMAL_PASSES` (`CoalesceFlow`, `UnreachableCodeElimination`,
//! `AllocateBasic`):
//!
//! ```text
//! decoded frontend Cfg
//!   -> build_mir        (cleanups + binarized flattening; mir.rs)
//!   -> allocate_temps   (liveness + interference + first-fit slots; alloc.rs)
//!   -> lower_mir        (statement regeneration, temp -> block 10000; lower.rs)
//!   -> cfg_to_engine_nodes (the T1.2 emitter; emit.rs)
//! ```
//!
//! No optimization happens at minimal; per decision D10 the SSA machinery
//! (`ssa.rs`) is not engaged. `Fast`/`Standard` return
//! [`CompileError::Unimplemented`] until T2.2 builds the level configuration.
//!
//! `compile_cfg` is a pure function: no globals, no caches, deterministic
//! output for identical inputs (insertion-order containers throughout).

use std::fmt;
use std::str::FromStr;

use crate::alloc::{TempLimitError, allocate_temps};
use crate::cfg::Cfg;
use crate::emit::{EmitError, cfg_to_engine_nodes};
use crate::lower::{LowerError, lower_mir};
use crate::mir::MirBuildError;
use crate::nodes::EngineNodes;

/// An optimization level (pipeline prefix).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Level {
    Minimal,
    Fast,
    Standard,
}

impl Level {
    pub fn name(self) -> &'static str {
        match self {
            Self::Minimal => "minimal",
            Self::Fast => "fast",
            Self::Standard => "standard",
        }
    }
}

impl FromStr for Level {
    type Err = UnknownLevel;

    fn from_str(s: &str) -> Result<Self, UnknownLevel> {
        match s {
            "minimal" => Ok(Self::Minimal),
            "fast" => Ok(Self::Fast),
            "standard" => Ok(Self::Standard),
            _ => Err(UnknownLevel(s.to_owned())),
        }
    }
}

/// An unrecognized level name.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UnknownLevel(pub String);

impl fmt::Display for UnknownLevel {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "unknown optimization level {:?} (expected minimal, fast, or standard)",
            self.0
        )
    }
}

impl std::error::Error for UnknownLevel {}

/// A pipeline failure.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CompileError {
    /// The level's pass pipeline is not implemented yet (T2.2+).
    Unimplemented(Level),
    /// IR build rejected the CFG (out-of-domain construct).
    Build(MirBuildError),
    /// Slot allocation exceeded the 4096-slot temporary memory budget.
    TempLimit(TempLimitError),
    /// Lowering rejected the MIR (cannot happen for pipeline-built MIR).
    Lower(LowerError),
    /// Emission rejected the lowered CFG (cannot happen post-allocation).
    Emit(EmitError),
}

impl fmt::Display for CompileError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Unimplemented(level) => {
                write!(
                    f,
                    "the {} level is not implemented yet (only minimal is available)",
                    level.name()
                )
            }
            Self::Build(e) => write!(f, "{e}"),
            Self::TempLimit(e) => write!(f, "{e}"),
            Self::Lower(e) => write!(f, "{e}"),
            Self::Emit(e) => write!(f, "{e}"),
        }
    }
}

impl std::error::Error for CompileError {}

impl From<MirBuildError> for CompileError {
    fn from(e: MirBuildError) -> Self {
        Self::Build(e)
    }
}

impl From<TempLimitError> for CompileError {
    fn from(e: TempLimitError) -> Self {
        Self::TempLimit(e)
    }
}

impl From<LowerError> for CompileError {
    fn from(e: LowerError) -> Self {
        Self::Lower(e)
    }
}

impl From<EmitError> for CompileError {
    fn from(e: EmitError) -> Self {
        Self::Emit(e)
    }
}

/// Pipeline statistics (for budget tests and metrics; T1.4/T2.4 consumers).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct CompileStats {
    /// Temp-memory high-water mark (`max(offset + size)`; legacy
    /// `AllocateBasic` would use the sum of unique temp sizes instead).
    pub temp_slots_used: u32,
    /// Temp-table entries that received an offset.
    pub temps_allocated: u32,
    /// MIR blocks after cleanups.
    pub mir_blocks: u32,
    /// MIR instructions (arena size; includes unscheduled constants).
    pub mir_insts: u32,
    /// Engine-node arena size of the emitted tree.
    pub node_count: u32,
}

/// Compiles a decoded frontend CFG to an engine-node tree at the given level.
///
/// # Errors
///
/// See [`CompileError`].
pub fn compile_cfg(cfg: &Cfg, level: Level) -> Result<EngineNodes, CompileError> {
    compile_cfg_stats(cfg, level).map(|(nodes, _)| nodes)
}

/// [`compile_cfg`] plus [`CompileStats`].
///
/// # Errors
///
/// See [`CompileError`].
pub fn compile_cfg_stats(
    cfg: &Cfg,
    level: Level,
) -> Result<(EngineNodes, CompileStats), CompileError> {
    if level != Level::Minimal {
        return Err(CompileError::Unimplemented(level));
    }
    let mir = crate::mir::build_mir(cfg)?;
    let alloc = allocate_temps(&mir)?;
    let lowered = lower_mir(&mir, &alloc)?;
    let nodes = cfg_to_engine_nodes(&lowered)?;
    let stats = CompileStats {
        temp_slots_used: alloc.slots_used,
        temps_allocated: u32::try_from(alloc.offsets.iter().filter(|o| o.is_some()).count())
            .expect("temp count fits u32"),
        mir_blocks: u32::try_from(mir.blocks.len()).expect("block count fits u32"),
        mir_insts: u32::try_from(mir.insts.len()).expect("inst count fits u32"),
        node_count: u32::try_from(nodes.arena.len()).expect("node count fits u32"),
    };
    Ok((nodes, stats))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::cfg::{
        BasicBlock, BlockValue, Edge, EdgeCond, IndexValue, Node, Place, TempBlockDef,
    };
    use crate::interpret::Interpreter;
    use crate::nodes::format_engine_node;
    use crate::ops::Op;

    /// Builds the frontend CFG for: t = And(in0, DebugLog(7)); out = t.
    /// Verifies end-to-end short-circuit behavior through the whole pipeline.
    fn and_log_cfg() -> Cfg {
        let mut cfg = Cfg::default();
        cfg.strings.push("t".to_owned());
        cfg.temp_blocks.push(TempBlockDef { name: 0, size: 1 });
        let node = |n: Node, cfg: &mut Cfg| {
            cfg.nodes.push(n);
            cfg.nodes.len() - 1
        };
        let place = |p: Place, cfg: &mut Cfg| {
            cfg.places.push(p);
            cfg.places.len() - 1
        };
        let in_place = place(
            Place {
                block: BlockValue::Int(-3),
                index: IndexValue::Int(0),
                offset: 0,
            },
            &mut cfg,
        );
        let t_place = place(
            Place {
                block: BlockValue::Temp(0),
                index: IndexValue::Int(0),
                offset: 0,
            },
            &mut cfg,
        );
        let out_place = place(
            Place {
                block: BlockValue::Int(20),
                index: IndexValue::Int(0),
                offset: 0,
            },
            &mut cfg,
        );
        let get_in = node(Node::Get(in_place), &mut cfg);
        let seven = node(Node::ConstInt(7), &mut cfg);
        let log = node(
            Node::Instr {
                op: Op::DebugLog,
                args: vec![seven],
            },
            &mut cfg,
        );
        let and = node(
            Node::PureInstr {
                op: Op::And,
                args: vec![get_in, log],
            },
            &mut cfg,
        );
        let set_t = node(
            Node::Set {
                place: t_place,
                value: and,
            },
            &mut cfg,
        );
        let get_t = node(Node::Get(t_place), &mut cfg);
        let set_out = node(
            Node::Set {
                place: out_place,
                value: get_t,
            },
            &mut cfg,
        );
        let zero = node(Node::ConstInt(0), &mut cfg);
        cfg.blocks.push(BasicBlock {
            statements: vec![set_t, set_out],
            test: zero,
            outgoing: vec![],
        });
        cfg
    }

    #[test]
    fn minimal_pipeline_preserves_short_circuit_semantics() {
        let cfg = and_log_cfg();
        let (nodes, stats) = compile_cfg_stats(&cfg, Level::Minimal).unwrap();
        assert_eq!(stats.temp_slots_used, 1);
        // in0 = 0: the DebugLog arm must not run.
        let mut interp = Interpreter::new(0);
        interp.set_block(-3, vec![0.0]);
        interp.run(&nodes).unwrap();
        assert!(interp.log().is_empty(), "short-circuit skipped the log");
        assert_eq!(interp.block(20).unwrap()[0], 0.0);
        // in0 = 1: the DebugLog arm runs; And yields DebugLog's 0.0.
        let mut interp = Interpreter::new(0);
        interp.set_block(-3, vec![1.0]);
        interp.run(&nodes).unwrap();
        assert_eq!(interp.log(), &[7.0]);
        assert_eq!(interp.block(20).unwrap()[0], 0.0);
    }

    #[test]
    fn nary_and_short_circuits_per_argument() {
        // t = And(in0, DebugLog(1), DebugLog(2)) — log prefixes depend on in0
        // exactly like the legacy n-ary interpreter case.
        let mut cfg = Cfg::default();
        cfg.strings.push("t".to_owned());
        cfg.temp_blocks.push(TempBlockDef { name: 0, size: 1 });
        let n = |cfg: &mut Cfg, node: Node| {
            cfg.nodes.push(node);
            cfg.nodes.len() - 1
        };
        cfg.places.push(Place {
            block: BlockValue::Int(-3),
            index: IndexValue::Int(0),
            offset: 0,
        });
        cfg.places.push(Place {
            block: BlockValue::Temp(0),
            index: IndexValue::Int(0),
            offset: 0,
        });
        let get_in = n(&mut cfg, Node::Get(0));
        let c1 = n(&mut cfg, Node::ConstInt(1));
        let log1 = n(
            &mut cfg,
            Node::Instr {
                op: Op::DebugLog,
                args: vec![c1],
            },
        );
        let c2 = n(&mut cfg, Node::ConstInt(2));
        let log2 = n(
            &mut cfg,
            Node::Instr {
                op: Op::DebugLog,
                args: vec![c2],
            },
        );
        // DebugLog returns 0.0, so make arm 2 truthy via Add(log1, 1) to
        // exercise the third arm: And(in0, Add(DebugLog(1), 1), DebugLog(2)).
        let one = n(&mut cfg, Node::ConstInt(1));
        let arm2 = n(
            &mut cfg,
            Node::PureInstr {
                op: Op::Add,
                args: vec![log1, one],
            },
        );
        let and = n(
            &mut cfg,
            Node::PureInstr {
                op: Op::And,
                args: vec![get_in, arm2, log2],
            },
        );
        let set_t = n(
            &mut cfg,
            Node::Set {
                place: 1,
                value: and,
            },
        );
        let zero = n(&mut cfg, Node::ConstInt(0));
        cfg.blocks.push(BasicBlock {
            statements: vec![set_t],
            test: zero,
            outgoing: vec![],
        });

        let nodes = compile_cfg(&cfg, Level::Minimal).unwrap();
        // in0 = 0: nothing logs.
        let mut interp = Interpreter::new(0);
        interp.set_block(-3, vec![0.0]);
        interp.run(&nodes).unwrap();
        assert_eq!(interp.log(), &[] as &[f64]);
        // in0 = 1: arm2 runs (logs 1, yields 1), then log2 runs (logs 2).
        let mut interp = Interpreter::new(0);
        interp.set_block(-3, vec![1.0]);
        interp.run(&nodes).unwrap();
        assert_eq!(interp.log(), &[1.0, 2.0]);
    }

    #[test]
    fn fast_and_standard_are_unimplemented() {
        let cfg = and_log_cfg();
        for level in [Level::Fast, Level::Standard] {
            let err = compile_cfg(&cfg, level).unwrap_err();
            assert_eq!(err, CompileError::Unimplemented(level));
            assert!(err.to_string().contains("not implemented"));
        }
    }

    #[test]
    fn temp_limit_error_message_matches_legacy() {
        // Two interfering 3000-slot temp blocks blow the 4096 budget.
        let mut cfg = Cfg::default();
        cfg.strings.push("a".to_owned());
        cfg.strings.push("b".to_owned());
        cfg.temp_blocks.push(TempBlockDef {
            name: 0,
            size: 3000,
        });
        cfg.temp_blocks.push(TempBlockDef {
            name: 1,
            size: 3000,
        });
        let mut stmts = Vec::new();
        for t in 0..2usize {
            cfg.places.push(Place {
                block: BlockValue::Temp(t),
                index: IndexValue::Int(0),
                offset: 0,
            });
            cfg.nodes.push(Node::ConstInt(1));
            let value = cfg.nodes.len() - 1;
            cfg.nodes.push(Node::Set {
                place: cfg.places.len() - 1,
                value,
            });
            stmts.push(cfg.nodes.len() - 1);
        }
        // Read both so they are live simultaneously.
        for t in 0..2usize {
            cfg.places.push(Place {
                block: BlockValue::Int(20),
                index: IndexValue::Int(t as i64),
                offset: 0,
            });
            let out_place = cfg.places.len() - 1;
            cfg.nodes.push(Node::Get(t)); // temp place ids are 0 and 1
            let get = cfg.nodes.len() - 1;
            cfg.nodes.push(Node::Set {
                place: out_place,
                value: get,
            });
            stmts.push(cfg.nodes.len() - 1);
        }
        cfg.nodes.push(Node::ConstInt(0));
        let test = cfg.nodes.len() - 1;
        cfg.blocks.push(BasicBlock {
            statements: stmts,
            test,
            outgoing: vec![],
        });
        let err = compile_cfg(&cfg, Level::Minimal).unwrap_err();
        assert_eq!(err.to_string(), "Temporary memory limit exceeded");
    }

    #[test]
    fn empty_cfg_compiles_to_empty_jump_loop() {
        let cfg = Cfg::default();
        let nodes = compile_cfg(&cfg, Level::Minimal).unwrap();
        assert_eq!(
            format_engine_node(&nodes.arena, nodes.root),
            "Block(JumpLoop(0))"
        );
    }

    #[test]
    fn deterministic_output_for_identical_input() {
        let cfg = and_log_cfg();
        let a = compile_cfg(&cfg, Level::Minimal).unwrap();
        let b = compile_cfg(&cfg, Level::Minimal).unwrap();
        assert_eq!(
            format_engine_node(&a.arena, a.root),
            format_engine_node(&b.arena, b.root)
        );
        let out_a = crate::output::generate_output_nodes(&a.arena, a.root).unwrap();
        let out_b = crate::output::generate_output_nodes(&b.arena, b.root).unwrap();
        assert_eq!(
            crate::output::output_node_dump(&out_a),
            crate::output::output_node_dump(&out_b)
        );
    }
}
