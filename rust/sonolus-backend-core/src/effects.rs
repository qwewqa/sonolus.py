//! Effect classification for MIR instructions (PORT.md T2.2): a shared,
//! documented answer to "what can this instruction observably do?", for
//! rewrite rules and future optimization passes.
//!
//! # Relationship to `analysis::inst_effect` (T2.1)
//!
//! The two are complementary, not duplicates: [`crate::analysis::inst_effect`]
//! computes *liveness facts* (which values/temps an instruction uses and
//! defines, for dataflow); this module classifies *effect kinds* (memory
//! reads/writes, RNG, control relevance, purity). Liveness never inspects op
//! flags — op-flag interpretation lives only here.
//!
//! # Classification
//!
//! Effects are flags, not a single enum, because instructions can combine them
//! (e.g. `IncrementPost` both reads and writes memory):
//!
//! - **`reads_memory`**: reads runtime block memory (`Load`, `Op::Get*`, the
//!   `Stack*` read family). Note that a memory read with a dynamic index may
//!   also *trap* (index asserts); whether dropping such a potential trap is
//!   acceptable follows the legacy folding policy and is each transform's
//!   responsibility, verified by differential testing (invariant §3.7).
//! - **`writes_memory`**: any externally observable effect — memory writes,
//!   drawing, audio, logging, exports, `Break` (mirrors `Op.side_effects`:
//!   "may not be removed"). Side-effecting ops are conservatively also marked
//!   `reads_memory` (many read-modify-write, e.g. `SetAdd`).
//! - **`rng`**: draws from the seeded RNG stream (`Random`, `RandomInteger`).
//!   Removing or duplicating an RNG draw shifts the stream and breaks
//!   differential testing even when the value is unused — never fold these.
//! - **`control`**: control-relevant. For MIR this means `Op::Break` (the
//!   frontend return), `ShortCircuit` (the D11 lazy boundary: its rhs subtree
//!   conditionally evaluates), and `Phi` (its value is control-dependent —
//!   code motion and naive value rewriting must treat it specially).
//!
//! An instruction with no flags set is **pure**: freely removable when unused,
//! duplicable, and foldable — subject only to the no-fold-on-Python-error rule
//! (ARCHITECTURE §6), which is about *result* fidelity, not effects.
//!
//! # Shallow vs deep
//!
//! [`inst_effects`] classifies the instruction *itself* (a `ShortCircuit` is
//! just a control-relevant pure fold; its operands — including the lazy rhs
//! root — are separate instructions). [`inst_effects_deep`] additionally
//! unions the effects of every instruction inside the owned lazy rhs tree of a
//! `ShortCircuit` (those instructions are scheduled nowhere; their effects
//! belong, conditionally, to the owner). Use the deep form when deciding
//! whether *evaluating* a `ShortCircuit` can have effects (DCE, LICM); use the
//! shallow form when the lazy tree's fate is handled separately (e.g. the
//! rewrite driver's replaceability guard: replacing `And(0, effectful-tree)`
//! with `0` is legal precisely because the tree never ran).

use crate::mir::{Inst, Mir, Value};
use crate::ops::Op;

/// Effect flags of one op or MIR instruction. See the module docs.
///
/// These are independent flags an instruction can combine (a read-modify-write
/// both reads and writes), so a bool-per-kind struct is the right shape rather
/// than an enum.
#[allow(clippy::struct_excessive_bools)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct Effects {
    /// Reads runtime block memory (may also trap on a bad dynamic index).
    pub reads_memory: bool,
    /// Has an externally observable effect (memory write, draw, log, ...).
    pub writes_memory: bool,
    /// Draws from the seeded RNG stream.
    pub rng: bool,
    /// Control-relevant (`Break`, the `ShortCircuit` lazy boundary, `Phi`).
    pub control: bool,
}

impl Effects {
    /// No effects at all.
    pub const PURE: Self = Self {
        reads_memory: false,
        writes_memory: false,
        rng: false,
        control: false,
    };

    /// True when no flag is set: removable when unused, duplicable, foldable
    /// (subject to the no-fold-on-Python-error rule).
    pub fn is_pure(self) -> bool {
        self == Self::PURE
    }

    /// Union of two effect sets.
    #[must_use]
    pub fn union(self, other: Self) -> Self {
        Self {
            reads_memory: self.reads_memory || other.reads_memory,
            writes_memory: self.writes_memory || other.writes_memory,
            rng: self.rng || other.rng,
            control: self.control || other.control,
        }
    }
}

/// The ops that are neither `pure` nor `side_effects` in `ops.py`, classified
/// explicitly. Everything else in that "neither" category is unknown and gets
/// the maximally conservative classification (see [`op_effects`]); the unit
/// test below pins this list against the generated op table so a new
/// unclassified op fails loudly instead of silently pessimizing.
const NEITHER_READS: [Op; 7] = [
    Op::Get,
    Op::GetPointed,
    Op::GetShifted,
    Op::StackGet,
    Op::StackGetFrame,
    Op::StackGetFramePointer,
    Op::StackGetPointer,
];
const NEITHER_RNG: [Op; 2] = [Op::Random, Op::RandomInteger];

/// Classifies an op from its generated flags (`pure` / `side_effects` /
/// `control_flow`) plus the explicit classification of the "neither" set.
pub fn op_effects(op: Op) -> Effects {
    let control = op.control_flow();
    if op.pure() {
        return Effects {
            control,
            ..Effects::PURE
        };
    }
    if op.side_effects() {
        // Conservatively also a reader: many side-effecting ops are
        // read-modify-write (SetAdd, IncrementPost, ...).
        return Effects {
            reads_memory: true,
            writes_memory: true,
            rng: false,
            control,
        };
    }
    if NEITHER_RNG.contains(&op) {
        return Effects {
            rng: true,
            control,
            ..Effects::PURE
        };
    }
    if NEITHER_READS.contains(&op) {
        return Effects {
            reads_memory: true,
            control,
            ..Effects::PURE
        };
    }
    // Unknown neither-pure-nor-side-effecting op (a future ops.py addition):
    // maximally conservative until classified above.
    Effects {
        reads_memory: true,
        writes_memory: true,
        rng: true,
        control,
    }
}

/// Classifies one instruction *itself* (shallow: a `ShortCircuit`'s lazy rhs
/// tree is not walked — see the module docs).
// `ShortCircuit` and `Phi` happen to share the control-only classification but
// are kept as distinct arms: each documents *why* it is control-relevant (the
// D11 lazy boundary vs. a control-dependent phi value), and a future refinement
// may diverge them.
#[allow(clippy::match_same_arms)]
pub fn inst_effects(mir: &Mir, v: Value) -> Effects {
    match mir.inst(v) {
        Inst::ConstInt(_) | Inst::ConstFloat(_) => Effects::PURE,
        Inst::Op { op, .. } => op_effects(*op),
        // The op is And/Or (control_flow in the op table); the fold itself is
        // pure, but the lazy boundary makes the instruction control-relevant.
        Inst::ShortCircuit { .. } => Effects {
            control: true,
            ..Effects::PURE
        },
        Inst::Load { .. } => Effects {
            reads_memory: true,
            ..Effects::PURE
        },
        Inst::Store { .. } => Effects {
            writes_memory: true,
            ..Effects::PURE
        },
        Inst::Phi { .. } => Effects {
            control: true,
            ..Effects::PURE
        },
    }
}

/// [`inst_effects`] unioned with the effects of every instruction inside the
/// owned lazy rhs tree of a `ShortCircuit` (iterative walk; nested
/// `ShortCircuit`s included). For non-`ShortCircuit` instructions this equals
/// the shallow form.
pub fn inst_effects_deep(mir: &Mir, v: Value) -> Effects {
    let mut effects = inst_effects(mir, v);
    let Inst::ShortCircuit { rhs, .. } = mir.inst(v) else {
        return effects;
    };
    let scheduled = mir.scheduled_mask();
    let mut stack = vec![*rhs];
    while let Some(lv) = stack.pop() {
        // Scheduled values are ordinary operands evaluated before the owner,
        // not part of the lazy tree (single-owner contract; defensive — the
        // builder never produces such references).
        if scheduled[lv as usize] {
            continue;
        }
        effects = effects.union(inst_effects(mir, lv));
        Mir::for_each_operand(mir.inst(lv), |o| stack.push(o));
    }
    effects
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::mir::{BlockRef, IndexRef, Place};

    #[test]
    fn every_neither_op_is_classified_explicitly() {
        // The "neither pure nor side_effects" set in ops.py must exactly equal
        // our explicit classification lists; a newly added unclassified op
        // makes this fail (instead of silently getting the conservative
        // fallback).
        let mut neither: Vec<Op> = Op::all()
            .filter(|op| !op.pure() && !op.side_effects())
            .collect();
        neither.sort();
        let mut classified: Vec<Op> = NEITHER_READS.iter().chain(&NEITHER_RNG).copied().collect();
        classified.sort();
        assert_eq!(neither, classified);
    }

    #[test]
    fn op_classification_samples() {
        assert!(op_effects(Op::Add).is_pure());
        assert!(op_effects(Op::Sin).is_pure());
        let get = op_effects(Op::Get);
        assert!(get.reads_memory && !get.writes_memory && !get.rng && !get.control);
        let set = op_effects(Op::Set);
        assert!(set.writes_memory && !set.rng);
        let log = op_effects(Op::DebugLog);
        assert!(log.writes_memory);
        let random = op_effects(Op::Random);
        assert!(random.rng && !random.reads_memory && !random.writes_memory);
        let brk = op_effects(Op::Break);
        assert!(brk.writes_memory && brk.control);
        // And/Or: pure fold, control-relevant flag from the op table.
        let and = op_effects(Op::And);
        assert!(and.control && !and.reads_memory && !and.writes_memory && !and.rng);
    }

    #[test]
    fn every_op_classifies_without_panicking() {
        for op in Op::all() {
            let e = op_effects(op);
            if op.pure() {
                assert!(
                    !e.reads_memory && !e.writes_memory && !e.rng,
                    "pure op {} must carry no memory/RNG effects",
                    op.name()
                );
            }
            if op.side_effects() {
                assert!(
                    e.writes_memory,
                    "side-effecting op {} must be a writer",
                    op.name()
                );
            }
        }
    }

    fn temp_place(t: usize) -> Place {
        Place {
            block: BlockRef::Temp(t),
            index: IndexRef::Const(0),
            offset: 0,
        }
    }

    #[test]
    fn inst_classification() {
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 1);
        let b0 = mir.push_block();
        let c = mir.push_inst(Inst::ConstInt(1));
        assert!(inst_effects(&mir, c).is_pure());
        let load = mir.push_inst(Inst::Load {
            place: temp_place(t),
        });
        mir.blocks[b0].insts.push(load);
        assert!(inst_effects(&mir, load).reads_memory);
        assert!(!inst_effects(&mir, load).writes_memory);
        let store = mir.push_inst(Inst::Store {
            place: temp_place(t),
            value: c,
        });
        mir.blocks[b0].insts.push(store);
        assert!(inst_effects(&mir, store).writes_memory);
        let phi = mir.push_inst(Inst::Phi { args: vec![] });
        assert!(inst_effects(&mir, phi).control);
        assert!(!inst_effects(&mir, phi).writes_memory);
    }

    #[test]
    fn deep_unions_lazy_tree_effects_shallow_does_not() {
        // sc = And(c, lazy DebugLog(7)): shallow = control only; deep adds the
        // log's write effect.
        let mut mir = Mir::new();
        let b0 = mir.push_block();
        let c = mir.push_inst(Inst::ConstInt(1));
        let seven = mir.push_inst(Inst::ConstInt(7));
        let log = mir.push_inst(Inst::Op {
            op: Op::DebugLog,
            pure_node: false,
            args: vec![seven],
        }); // unscheduled: lazy
        let sc = mir.push_inst(Inst::ShortCircuit {
            op: Op::And,
            pure_node: true,
            lhs: c,
            rhs: log,
        });
        mir.blocks[b0].insts.push(sc);
        let shallow = inst_effects(&mir, sc);
        assert!(shallow.control && !shallow.writes_memory);
        let deep = inst_effects_deep(&mir, sc);
        assert!(deep.control && deep.writes_memory);
    }

    #[test]
    fn deep_walks_nested_short_circuits_and_stops_at_scheduled_values() {
        // sc = And(a, lazy Or(load t, lazy rng)): deep sees reads + rng.
        // The eager lhs `a` (scheduled) is NOT part of sc's deep effects.
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 1);
        let b0 = mir.push_block();
        let a = mir.push_inst(Inst::Op {
            op: Op::Random,
            pure_node: false,
            args: vec![],
        });
        mir.blocks[b0].insts.push(a); // scheduled RNG draw used as eager lhs
        let lazy_load = mir.push_inst(Inst::Load {
            place: temp_place(t),
        });
        let zero = mir.push_inst(Inst::ConstInt(0));
        let lazy_rng = mir.push_inst(Inst::Op {
            op: Op::RandomInteger,
            pure_node: false,
            args: vec![zero, zero],
        });
        let inner = mir.push_inst(Inst::ShortCircuit {
            op: Op::Or,
            pure_node: true,
            lhs: lazy_load,
            rhs: lazy_rng,
        });
        let sc = mir.push_inst(Inst::ShortCircuit {
            op: Op::And,
            pure_node: true,
            lhs: a,
            rhs: inner,
        });
        mir.blocks[b0].insts.push(sc);
        let deep = inst_effects_deep(&mir, sc);
        assert!(deep.reads_memory, "lazy load found through the nested Or");
        assert!(deep.rng, "lazy RNG draw found");
        assert!(deep.control);
        // Shallow effects of the scheduled eager lhs do not change: the lhs is
        // a separate instruction, and deep() of sc only owns the rhs tree.
        let mut no_lhs_mir = mir.clone();
        no_lhs_mir.insts[a as usize] = Inst::ConstInt(0);
        let deep_without_lhs = inst_effects_deep(&no_lhs_mir, sc);
        assert_eq!(deep, deep_without_lhs);
    }
}
