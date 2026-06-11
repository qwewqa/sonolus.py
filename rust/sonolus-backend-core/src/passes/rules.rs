//! W1 rewrite rules (PORT.md T3.2): real constant folding through the T1.1
//! `py_*` kernels, plus the legacy-licensed algebraic identities and strength
//! reduction with **exact** signed-zero guards.
//!
//! # Constants are emission-normalized (`-0.0` does not exist at runtime)
//!
//! The emitter's numeric path (`emit::push_numeric`, the exact port of legacy
//! `_numeric_to_engine_node`) sends every *integral* constant through the
//! int-tagged path, which normalizes `-0.0` to `+0.0` (Python's `int(-0.0)`
//! is `0`). A `-0.0` constant therefore **cannot reach the runtime**: by the
//! time the program executes, that literal is `+0.0`. Two consequences for
//! every rule here:
//!
//! - The *runtime value* of a constant operand is [`runtime_const_value`]
//!   (integral floats normalized through `+ 0.0`), not its raw MIR bits —
//!   folding with the raw `-0.0` would diverge from what the minimal oracle
//!   actually computes (caught by the per-pass fuzzer: `Arctan2(0, -0.0)` is
//!   `0.0` at runtime, not `π`).
//! - A fold whose **result** is `-0.0` or NaN is refused: the engine constant
//!   format cannot represent either faithfully. `-0.0` would be flipped to
//!   `+0.0` by the integral path; a NaN constant emits as the `EngineRom`
//!   read `Get(3000, 0)`, whose value is the runtime's single positive quiet
//!   NaN — but a *computed* NaN can carry a set sign bit (e.g.
//!   `Frac(-inf) == -NaN` via `fmod`), and `Sign` exposes the NaN sign bit as
//!   `±1`. Both refusals keep the op so the runtime computes the exact value,
//!   matching the minimal oracle. (`±inf` results are fine: the ROM reads
//!   yield exactly `±inf`.)
//!
//! # Rule list and safety arguments
//!
//! - **`w1-const-fold`** — folds a pure op whose operands are all constants,
//!   for exactly the op set the legacy backend licensed
//!   (`constant_evaluation.py::SparseConditionalConstantPropagation
//!   .SUPPORTED_OPS`, minus `And`/`Or`, which are MIR `ShortCircuit`
//!   instructions and outside the rewrite domain per decision D11). Evaluation
//!   goes through the interpreter's own `py_*` kernels over the
//!   emission-normalized operand values, so the folded value is bit-identical
//!   to what the runtime computes — `NaN` and `±inf` results included
//!   (NaN/inf constants emit as `EngineRom` reads, exactly like the runtime
//!   provides them). A kernel error means **no fold** (the
//!   no-fold-on-Python-error rule, ARCHITECTURE §6): the trap is a behavior
//!   and must stay in the program. A `-0.0` result also means no fold (see
//!   above). Ops outside the legacy set (e.g. `Sign`, `Trunc`, `Unlerp`)
//!   deliberately do not fold, mirroring legacy.
//!
//!   Int/float **tag** policy (output-cosmetic only; the numeric value is
//!   always the exact runtime f64): comparisons and `Not` are int (Python
//!   `bool`); `Ceil`/`Floor`/`Round` are int when exactly representable
//!   (Python returns `int`); `Divide` (true division), `Rem`
//!   (`math.remainder`) and the transcendentals are float (Python returns
//!   `float`); everything else is int iff every operand was int-tagged and
//!   the result is an exactly-representable integer. Documented divergence
//!   from Python's exact result types in corners (e.g. `Max(2, 1.0)` is int 2
//!   in Python, float 2.0 here): tags only affect output formatting, never
//!   the runtime value — the same relaxation output dedup already ships
//!   (`output.rs`: 5 and 5.0 share one node), and integral float tags are
//!   re-normalized to int by `push_numeric` at emission anyway.
//!
//! - **`w1-mul-one`** — `Multiply(x, 1) -> x` and `Multiply(1, x) -> x`.
//!   Exact IEEE identity for every `x` (incl. `-0.0`, NaN, ±inf). Legacy
//!   precedent: `simplify.py::RemoveRedundantArguments` drops `== 1` constant
//!   factors.
//!
//! - **`w1-div-one`** — `Divide(x, 1) -> x`. Exact (`x / 1.0 == x` bit-for-bit;
//!   `py_div` cannot error for divisor 1). Legacy precedent: ditto for `== 1`
//!   divisors after the first argument.
//!
//! - **`w1-add-zero`** — `Add(x, c) -> x` (either operand position) where `c`
//!   is a zero constant (any of int `0`, `0.0`, `-0.0` — all `+0.0` at
//!   runtime), **guarded**: fires only when `x` provably cannot be `-0.0`
//!   ([`never_neg_zero`]) — because `-0.0 + 0.0 == +0.0`, which
//!   `Sign`/`Arctan2` can observe. Legacy dropped any `== 0` addend unguarded
//!   (`RemoveRedundantArguments`); the T2.3 differential net (fuzz generator
//!   with `Sign` and computed `-0.0` values) detectably falsifies the
//!   unguarded form, so this port ships the strictly-more-conservative exact
//!   variant.
//!
//! - **`w1-sub-zero`** — `Subtract(x, c) -> x` where `c` is a zero constant.
//!   Unconditionally exact: the subtrahend is `+0.0` at runtime and
//!   `x - 0.0 == x` bit-for-bit for every `x` (including `-0.0 - 0.0 ==
//!   -0.0`). Legacy precedent as `w1-add-zero`.
//!
//! - **`w1-negate`** (strength reduction) — `Subtract(c, x) -> Negate(x)` for
//!   a zero-constant minuend, **guarded** on `x` provably never being zero
//!   ([`never_zero`]): the runtime computation is `0.0 - x`, which equals
//!   `-x` except at `x = +0.0` (`0.0 - 0.0 == +0.0` but `Negate(+0.0) ==
//!   -0.0`). Legacy precedent: `RemoveRedundantArguments` rewrote
//!   `Subtract(0, x)` to `Negate(x)` unguarded; same conservatism rationale
//!   as `w1-add-zero`.
//!
//! All rules fire only on eager scheduled instructions; the driver enforces
//! the D11 lazy boundary (none of these rules opts into `enters_lazy`).

use crate::interpret::{
    py_acos, py_asin, py_ceil, py_cos, py_cosh, py_div, py_floor, py_log, py_max, py_min, py_mod,
    py_pow, py_remainder, py_round, py_sin, py_sinh, py_tan,
};
use crate::mir::{Inst, Value};
use crate::ops::Op;
use crate::rewrite::{Const, Rewrite, RewriteCtx, RewriteRule};

/// The complete W1 rule list, in driver order (first rule wins per value).
pub fn w1_rules() -> Vec<Box<dyn RewriteRule>> {
    vec![
        Box::new(FoldConsts),
        Box::new(MulOne),
        Box::new(DivOne),
        Box::new(AddZero),
        Box::new(SubZero),
        Box::new(SubFromZeroToNegate),
    ]
}

// ----------------------------------------------------------------------------------
// Constant folding
// ----------------------------------------------------------------------------------

/// The arity (in binary MIR form) of an op in the legacy-licensed fold set,
/// or `None` if the op is not folded. This is exactly
/// `constant_evaluation.py::SUPPORTED_OPS` minus `And`/`Or` (`ShortCircuit`
/// in MIR, excluded per D11). The reduce-style ops are binary in MIR
/// (invariant §3.3); the fixed-arity ops keep their frontend arity.
fn fold_arity(op: Op) -> Option<usize> {
    Some(match op {
        Op::Not
        | Op::Negate
        | Op::Log
        | Op::Ceil
        | Op::Floor
        | Op::Round
        | Op::Frac
        | Op::Sin
        | Op::Cos
        | Op::Tan
        | Op::Sinh
        | Op::Cosh
        | Op::Tanh
        | Op::Arcsin
        | Op::Arccos
        | Op::Arctan
        | Op::Abs
        | Op::Degree
        | Op::Radian => 1,
        Op::Equal
        | Op::NotEqual
        | Op::Greater
        | Op::GreaterOr
        | Op::Less
        | Op::LessOr
        | Op::Add
        | Op::Subtract
        | Op::Multiply
        | Op::Divide
        | Op::Power
        | Op::Mod
        | Op::Rem
        | Op::Arctan2
        | Op::Max
        | Op::Min => 2,
        Op::Clamp | Op::Lerp | Op::LerpClamped => 3,
        Op::Remap | Op::RemapClamped => 5,
        _ => return None,
    })
}

fn truth(b: bool) -> f64 {
    if b { 1.0 } else { 0.0 }
}

/// The value a constant has **at runtime**, mirroring the emitter's
/// `push_numeric` (`_numeric_to_engine_node`): integral values go through the
/// int-tagged path, normalizing `-0.0` to `+0.0` (the `+ 0.0` is a no-op for
/// every other value, including `±inf`); non-integral values (and NaN) keep
/// their bits (NaN/±inf become `EngineRom` reads with the same value). See
/// the module docs.
#[allow(clippy::float_cmp)] // exact integrality check, the ported emit semantics
pub(crate) fn runtime_const_value(c: Const) -> f64 {
    match c {
        #[allow(clippy::cast_precision_loss)] // the runtime is f64-only
        Const::Int(i) => i as f64,
        Const::Float(f) => {
            if f == f.trunc() {
                f + 0.0
            } else {
                f
            }
        }
    }
}

/// Python `max(0, min(1, s))` — the interpreter's `clamp01` (NaN clamps to 1).
fn clamp01(s: f64) -> f64 {
    py_max(0.0, py_min(1.0, s))
}

/// Evaluates one fold-set op exactly as the interpreter would (same kernels,
/// same composition). `None` means the kernel raised a Python error — no fold.
#[allow(clippy::float_cmp)] // exact comparisons are the ported Python semantics
fn eval_fold(op: Op, v: &[f64]) -> Option<f64> {
    let r = match op {
        Op::Equal => truth(v[0] == v[1]),
        Op::NotEqual => truth(v[0] != v[1]),
        Op::Greater => truth(v[0] > v[1]),
        Op::GreaterOr => truth(v[0] >= v[1]),
        Op::Less => truth(v[0] < v[1]),
        Op::LessOr => truth(v[0] <= v[1]),
        Op::Not => truth(v[0] == 0.0),
        Op::Negate => -v[0],
        Op::Add => v[0] + v[1],
        Op::Subtract => v[0] - v[1],
        Op::Multiply => v[0] * v[1],
        Op::Divide => py_div(v[0], v[1]).ok()?,
        Op::Power => py_pow(v[0], v[1]).ok()?,
        Op::Mod => py_mod(v[0], v[1]).ok()?,
        Op::Rem => py_remainder(v[0], v[1]).ok()?,
        Op::Log => py_log(v[0]).ok()?,
        Op::Ceil => py_ceil(v[0]).ok()?,
        Op::Floor => py_floor(v[0]).ok()?,
        Op::Round => py_round(v[0]).ok()?,
        Op::Frac => {
            // The interpreter's literal composition: x % 1, then the
            // `result if result >= 0 else result + 1` adjustment.
            let result = py_mod(v[0], 1.0).ok()?;
            if result >= 0.0 { result } else { result + 1.0 }
        }
        Op::Sin => py_sin(v[0]).ok()?,
        Op::Cos => py_cos(v[0]).ok()?,
        Op::Tan => py_tan(v[0]).ok()?,
        Op::Sinh => py_sinh(v[0]).ok()?,
        Op::Cosh => py_cosh(v[0]).ok()?,
        Op::Tanh => v[0].tanh(),
        Op::Arcsin => py_asin(v[0]).ok()?,
        Op::Arccos => py_acos(v[0]).ok()?,
        Op::Arctan => v[0].atan(),
        Op::Arctan2 => v[0].atan2(v[1]),
        Op::Max => py_max(v[0], v[1]),
        Op::Min => py_min(v[0], v[1]),
        Op::Abs => v[0].abs(),
        Op::Clamp => py_max(v[1], py_min(v[2], v[0])),
        Op::Degree => v[0].to_degrees(),
        Op::Radian => v[0].to_radians(),
        Op::Lerp => v[0] + (v[1] - v[0]) * v[2],
        Op::LerpClamped => v[0] + (v[1] - v[0]) * clamp01(v[2]),
        Op::Remap => v[2] + py_div((v[3] - v[2]) * (v[4] - v[0]), v[1] - v[0]).ok()?,
        Op::RemapClamped => v[2] + (v[3] - v[2]) * clamp01(py_div(v[4] - v[0], v[1] - v[0]).ok()?),
        _ => return None,
    };
    Some(r)
}

/// Whether `r` can carry the int tag: an exactly-representable integer.
/// (`-0.0` results never reach tagging — the fold is refused outright, module
/// docs.)
fn int_representable(r: f64) -> bool {
    r.fract() == 0.0 && r.abs() <= 9_007_199_254_740_992.0
}

/// The tag policy (module docs): chooses `ConstInt`/`ConstFloat` for a folded
/// result. The numeric value is `r` either way.
#[allow(clippy::cast_possible_truncation)] // guarded by int_representable
fn fold_rewrite(op: Op, operands: &[Const], r: f64) -> Rewrite {
    let int_tag = match op {
        // Python bool (an int).
        Op::Equal
        | Op::NotEqual
        | Op::Greater
        | Op::GreaterOr
        | Op::Less
        | Op::LessOr
        | Op::Not => true,
        // Python returns int (value integral by construction; the guard only
        // rejects magnitudes beyond exact representation).
        Op::Ceil | Op::Floor | Op::Round => int_representable(r),
        // Python returns float.
        Op::Divide
        | Op::Rem
        | Op::Log
        | Op::Sin
        | Op::Cos
        | Op::Tan
        | Op::Sinh
        | Op::Cosh
        | Op::Tanh
        | Op::Arcsin
        | Op::Arccos
        | Op::Arctan
        | Op::Arctan2
        | Op::Degree
        | Op::Radian => false,
        // Int-preserving arithmetic / selection ops.
        _ => operands.iter().all(|c| c.is_int()) && int_representable(r),
    };
    if int_tag {
        Rewrite::ConstInt(r as i64)
    } else {
        Rewrite::ConstFloat(r)
    }
}

/// `w1-const-fold` (module docs).
#[derive(Debug)]
pub struct FoldConsts;

impl RewriteRule for FoldConsts {
    fn name(&self) -> &'static str {
        "w1-const-fold"
    }

    fn rewrite(&self, ctx: &RewriteCtx<'_>, v: Value) -> Option<Rewrite> {
        let Inst::Op { op, args, .. } = ctx.inst(v) else {
            return None;
        };
        let op = *op;
        if args.len() != fold_arity(op)? || !ctx.effects(v).is_pure() {
            return None;
        }
        let operands: Vec<Const> = args
            .iter()
            .map(|&a| ctx.as_const(a))
            .collect::<Option<_>>()?;
        // Fold over the *runtime* values of the constants (module docs).
        let vals: Vec<f64> = operands.iter().map(|&c| runtime_const_value(c)).collect();
        let r = eval_fold(op, &vals)?; // None = Python error: no fold.
        if r.to_bits() == (-0.0f64).to_bits() || r.is_nan() {
            // Neither -0.0 nor a computed NaN can be represented faithfully
            // as an engine constant; keep the op (module docs).
            return None;
        }
        Some(fold_rewrite(op, &operands, r))
    }
}

// ----------------------------------------------------------------------------------
// Algebraic identities
// ----------------------------------------------------------------------------------

/// Whether this constant is zero **at runtime** (int `0`, float `0.0` or
/// `-0.0` — all `+0.0` post-emission, module docs).
#[allow(clippy::float_cmp)] // exact zero test
fn is_runtime_zero(c: Const) -> bool {
    runtime_const_value(c) == 0.0
}

#[allow(clippy::float_cmp)] // exactly 1 is the multiplicative identity
fn is_one(c: Const) -> bool {
    c.as_f64() == 1.0
}

/// Whether `x` provably cannot evaluate to `-0.0`: any constant (`-0.0`
/// literals are emission-normalized to `+0.0`, module docs), or the result of
/// an op whose kernel never produces `-0.0` — comparisons/`Not` (0 or 1),
/// `Sign` (±1), `Abs` (`|-0.0| == +0.0`), `Ceil`/`Floor`/`Round`/`Trunc` (the
/// `py_*` kernels normalize `-0.0` away, like Python's int returns), and
/// `Frac` (`py_mod(x, 1)` gives zero results the divisor's sign, `+0.0`).
/// Conservative `false` for everything else.
fn never_neg_zero(ctx: &RewriteCtx<'_>, x: Value) -> bool {
    match ctx.inst(x) {
        Inst::ConstInt(_) | Inst::ConstFloat(_) => true,
        Inst::Op { op, .. } => matches!(
            op,
            Op::Not
                | Op::Equal
                | Op::NotEqual
                | Op::Greater
                | Op::GreaterOr
                | Op::Less
                | Op::LessOr
                | Op::Sign
                | Op::Abs
                | Op::Ceil
                | Op::Floor
                | Op::Round
                | Op::Trunc
                | Op::Frac
        ),
        _ => false,
    }
}

/// Whether `x` provably can never evaluate to zero of either sign: `Sign`
/// yields exactly `±1` (even for NaN input — `copysign(1, NaN)`), `Cosh`
/// yields values `>= 1`, `+inf`, or NaN (never zero). Conservative `false`
/// for everything else (constants fold instead).
fn never_zero(ctx: &RewriteCtx<'_>, x: Value) -> bool {
    matches!(ctx.inst(x), Inst::Op { op, .. } if matches!(op, Op::Sign | Op::Cosh))
}

/// Matches a pure binary `Inst::Op` of the given op; returns its operand pair.
fn binary_pure(ctx: &RewriteCtx<'_>, v: Value, want: Op) -> Option<(Value, Value)> {
    let Inst::Op { op, args, .. } = ctx.inst(v) else {
        return None;
    };
    if *op != want || args.len() != 2 || !ctx.effects(v).is_pure() {
        return None;
    }
    Some((args[0], args[1]))
}

/// `w1-mul-one` (module docs).
#[derive(Debug)]
pub struct MulOne;

impl RewriteRule for MulOne {
    fn name(&self) -> &'static str {
        "w1-mul-one"
    }

    fn rewrite(&self, ctx: &RewriteCtx<'_>, v: Value) -> Option<Rewrite> {
        let (a, b) = binary_pure(ctx, v, Op::Multiply)?;
        if ctx.as_const(b).is_some_and(is_one) {
            return Some(Rewrite::Existing(a));
        }
        if ctx.as_const(a).is_some_and(is_one) {
            return Some(Rewrite::Existing(b));
        }
        None
    }
}

/// `w1-div-one` (module docs).
#[derive(Debug)]
pub struct DivOne;

impl RewriteRule for DivOne {
    fn name(&self) -> &'static str {
        "w1-div-one"
    }

    fn rewrite(&self, ctx: &RewriteCtx<'_>, v: Value) -> Option<Rewrite> {
        let (a, b) = binary_pure(ctx, v, Op::Divide)?;
        if ctx.as_const(b).is_some_and(is_one) {
            return Some(Rewrite::Existing(a));
        }
        None
    }
}

/// `w1-add-zero` (module docs: the zero addend is `+0.0` at runtime, so the
/// other operand must provably never be `-0.0`).
#[derive(Debug)]
pub struct AddZero;

impl RewriteRule for AddZero {
    fn name(&self) -> &'static str {
        "w1-add-zero"
    }

    fn rewrite(&self, ctx: &RewriteCtx<'_>, v: Value) -> Option<Rewrite> {
        let (a, b) = binary_pure(ctx, v, Op::Add)?;
        if ctx.as_const(b).is_some_and(is_runtime_zero) && never_neg_zero(ctx, a) {
            return Some(Rewrite::Existing(a));
        }
        if ctx.as_const(a).is_some_and(is_runtime_zero) && never_neg_zero(ctx, b) {
            return Some(Rewrite::Existing(b));
        }
        None
    }
}

/// `w1-sub-zero` (module docs: `x - 0.0 == x` exactly for every `x`).
#[derive(Debug)]
pub struct SubZero;

impl RewriteRule for SubZero {
    fn name(&self) -> &'static str {
        "w1-sub-zero"
    }

    fn rewrite(&self, ctx: &RewriteCtx<'_>, v: Value) -> Option<Rewrite> {
        let (a, b) = binary_pure(ctx, v, Op::Subtract)?;
        if ctx.as_const(b).is_some_and(is_runtime_zero) {
            return Some(Rewrite::Existing(a));
        }
        None
    }
}

/// `w1-negate` (module docs: `0.0 - x -> Negate(x)`, guarded on `x` never
/// being zero).
#[derive(Debug)]
pub struct SubFromZeroToNegate;

impl RewriteRule for SubFromZeroToNegate {
    fn name(&self) -> &'static str {
        "w1-negate"
    }

    fn rewrite(&self, ctx: &RewriteCtx<'_>, v: Value) -> Option<Rewrite> {
        let (a, b) = binary_pure(ctx, v, Op::Subtract)?;
        if !ctx.as_const(a).is_some_and(is_runtime_zero) || !never_zero(ctx, b) {
            return None;
        }
        let Inst::Op { pure_node, .. } = ctx.inst(v) else {
            return None;
        };
        Some(Rewrite::NewInst(Inst::Op {
            op: Op::Negate,
            pure_node: *pure_node,
            args: vec![b],
        }))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::mir::{BlockRef, IndexRef, Mir, Place, Terminator};
    use crate::rewrite::RewriteDriver;

    fn temp_place(t: usize) -> Place {
        Place {
            block: BlockRef::Temp(t),
            index: IndexRef::Const(0),
            offset: 0,
        }
    }

    /// One block storing `root` (built by the closure) to a temp; returns the
    /// MIR and the store value.
    fn store_mir(build: impl FnOnce(&mut Mir) -> Value) -> (Mir, Value) {
        let mut mir = Mir::new();
        let t = mir.push_temp("t", 1);
        let b0 = mir.push_block();
        let root = build(&mut mir);
        let store = mir.push_inst(Inst::Store {
            place: temp_place(t),
            value: root,
        });
        mir.blocks[b0].insts.push(store);
        mir.blocks[b0].terminator = Terminator::Exit;
        (mir, store)
    }

    fn sched(mir: &mut Mir, inst: Inst) -> Value {
        let v = mir.push_inst(inst);
        mir.blocks[0].insts.push(v);
        v
    }

    fn run_rules(mir: &mut Mir) -> crate::rewrite::RewriteReport {
        let rules = w1_rules();
        RewriteDriver::new(&rules).run(mir)
    }

    fn stored_value(mir: &Mir, store: Value) -> &Inst {
        let Inst::Store { value, .. } = mir.inst(store) else {
            panic!("expected store")
        };
        mir.inst(*value)
    }

    /// Folds one pure binary op over two constants and returns the stored
    /// replacement instruction (or the original op if nothing fired).
    fn fold_binary(op: Op, a: Inst, b: Inst) -> Inst {
        let (mut mir, store) = store_mir(|mir| {
            let ca = mir.push_inst(a);
            let cb = mir.push_inst(b);
            let inst = mir.push_inst(Inst::Op {
                op,
                pure_node: true,
                args: vec![ca, cb],
            });
            mir.blocks[0].insts.push(inst);
            inst
        });
        run_rules(&mut mir);
        stored_value(&mir, store).clone()
    }

    fn fold_unary(op: Op, a: Inst) -> Inst {
        let (mut mir, store) = store_mir(|mir| {
            let ca = mir.push_inst(a);
            let inst = mir.push_inst(Inst::Op {
                op,
                pure_node: true,
                args: vec![ca],
            });
            mir.blocks[0].insts.push(inst);
            inst
        });
        run_rules(&mut mir);
        stored_value(&mir, store).clone()
    }

    #[test]
    fn fold_int_arithmetic_keeps_int_tag() {
        assert_eq!(
            fold_binary(Op::Add, Inst::ConstInt(2), Inst::ConstInt(3)),
            Inst::ConstInt(5)
        );
        assert_eq!(
            fold_binary(Op::Multiply, Inst::ConstInt(-4), Inst::ConstInt(6)),
            Inst::ConstInt(-24)
        );
        // Floor-mod: sign follows the divisor.
        assert_eq!(
            fold_binary(Op::Mod, Inst::ConstInt(-7), Inst::ConstInt(3)),
            Inst::ConstInt(2)
        );
    }

    #[test]
    fn fold_mixed_operands_promote_to_float() {
        assert_eq!(
            fold_binary(Op::Add, Inst::ConstInt(2), Inst::ConstFloat(3.5)),
            Inst::ConstFloat(5.5)
        );
    }

    #[test]
    fn fold_divide_is_always_float_tagged() {
        // Python truediv returns float even for exact integer quotients.
        assert_eq!(
            fold_binary(Op::Divide, Inst::ConstInt(4), Inst::ConstInt(2)),
            Inst::ConstFloat(2.0)
        );
    }

    #[test]
    fn fold_comparisons_are_int_tagged() {
        assert_eq!(
            fold_binary(Op::Less, Inst::ConstFloat(1.5), Inst::ConstFloat(2.5)),
            Inst::ConstInt(1)
        );
        assert_eq!(
            fold_binary(
                Op::Equal,
                Inst::ConstFloat(f64::NAN),
                Inst::ConstFloat(f64::NAN)
            ),
            Inst::ConstInt(0),
            "NaN == NaN is false, and the comparison still folds"
        );
    }

    #[test]
    fn fold_round_is_bankers() {
        assert_eq!(
            fold_unary(Op::Round, Inst::ConstFloat(2.5)),
            Inst::ConstInt(2)
        );
        assert_eq!(
            fold_unary(Op::Round, Inst::ConstFloat(3.5)),
            Inst::ConstInt(4)
        );
    }

    #[test]
    fn no_fold_when_the_result_is_negative_zero() {
        // 0 * -1 is -0.0 in f64 (what the runtime computes), but the engine
        // constant format cannot represent -0.0 (emission normalizes it to
        // +0.0): the fold must be refused so the runtime keeps computing the
        // true -0.0 (observable via Sign).
        let folded = fold_binary(Op::Multiply, Inst::ConstInt(0), Inst::ConstInt(-1));
        assert!(
            matches!(
                folded,
                Inst::Op {
                    op: Op::Multiply,
                    ..
                }
            ),
            "a -0.0-producing fold must be refused, got {folded:?}"
        );
    }

    #[test]
    fn fold_uses_emission_normalized_constant_values() {
        // A -0.0 *literal* is +0.0 by the time the runtime sees it
        // (emit::push_numeric sends integral constants through the int path),
        // so Arctan2(0, -0.0) folds to atan2(0, +0.0) == 0.0 — NOT pi. This
        // pins the exact divergence the per-pass fuzzer caught.
        let folded = fold_binary(Op::Arctan2, Inst::ConstInt(0), Inst::ConstFloat(-0.0));
        assert_eq!(folded, Inst::ConstFloat(0.0));
        // Non-integral float constants keep their bits.
        let folded = fold_binary(Op::Arctan2, Inst::ConstInt(0), Inst::ConstFloat(-0.5));
        assert_eq!(folded, Inst::ConstFloat(0.0f64.atan2(-0.5)));
    }

    #[test]
    fn fold_power_negative_exponent() {
        assert_eq!(
            fold_binary(Op::Power, Inst::ConstInt(2), Inst::ConstInt(-1)),
            Inst::ConstFloat(0.5)
        );
    }

    #[test]
    fn fold_min_max_are_position_dependent_like_python() {
        // py_max(NaN, 1) keeps NaN (a NaN result: fold refused); py_max(1,
        // NaN) keeps 1 (folds). The position dependence is exactly Python's.
        let r = fold_binary(Op::Max, Inst::ConstFloat(f64::NAN), Inst::ConstInt(1));
        assert!(matches!(r, Inst::Op { op: Op::Max, .. }));
        let r = fold_binary(Op::Max, Inst::ConstInt(1), Inst::ConstFloat(f64::NAN));
        assert_eq!(r, Inst::ConstFloat(1.0), "mixed tags promote to float");
    }

    #[test]
    fn no_fold_on_python_error() {
        // Each must stay an op (the trap is behavior).
        for (op, a, b) in [
            (Op::Divide, Inst::ConstInt(1), Inst::ConstInt(0)),
            (Op::Mod, Inst::ConstInt(5), Inst::ConstInt(0)),
            (Op::Power, Inst::ConstInt(0), Inst::ConstInt(-1)),
            (Op::Rem, Inst::ConstInt(1), Inst::ConstInt(0)),
        ] {
            let folded = fold_binary(op, a, b);
            assert!(
                matches!(folded, Inst::Op { op: got, .. } if got == op),
                "{op:?} by zero/in error must not fold, got {folded:?}"
            );
        }
        for (op, a) in [
            (Op::Log, Inst::ConstInt(-1)),
            (Op::Arcsin, Inst::ConstInt(2)),
            (Op::Round, Inst::ConstFloat(f64::NAN)),
            (Op::Ceil, Inst::ConstFloat(f64::INFINITY)),
            (Op::Sin, Inst::ConstFloat(f64::INFINITY)),
        ] {
            let folded = fold_unary(op, a);
            assert!(
                matches!(folded, Inst::Op { op: got, .. } if got == op),
                "{op:?} on a Python-error input must not fold, got {folded:?}"
            );
        }
    }

    #[test]
    fn ops_outside_the_legacy_set_do_not_fold() {
        // Sign and Trunc are pure and trivially evaluable, but legacy SCCP
        // never folded them — neither do we.
        let folded = fold_unary(Op::Sign, Inst::ConstInt(7));
        assert!(matches!(folded, Inst::Op { op: Op::Sign, .. }));
        let folded = fold_unary(Op::Trunc, Inst::ConstFloat(2.9));
        assert!(matches!(folded, Inst::Op { op: Op::Trunc, .. }));
    }

    #[test]
    fn no_fold_when_the_result_is_nan() {
        // inf + -inf = NaN with no Python error, but a computed NaN cannot be
        // represented as an engine constant (NaN constants emit as the ROM's
        // single +NaN, and Sign exposes the NaN sign bit): no fold.
        let folded = fold_binary(
            Op::Add,
            Inst::ConstFloat(f64::INFINITY),
            Inst::ConstFloat(f64::NEG_INFINITY),
        );
        assert!(matches!(folded, Inst::Op { op: Op::Add, .. }));
        // Frac(-inf) computes fmod(-inf, 1) == -NaN at runtime; Sign of it is
        // -1, while Sign of a folded (ROM) NaN constant would be +1 — the
        // exact divergence the per-pass fuzzer caught. Must not fold.
        let folded = fold_unary(Op::Frac, Inst::ConstFloat(f64::NEG_INFINITY));
        assert!(matches!(folded, Inst::Op { op: Op::Frac, .. }));
    }

    #[test]
    fn fold_infinity_results_are_kept() {
        // ±inf results ARE representable (EngineRom reads yield exactly ±inf).
        let folded = fold_binary(Op::Add, Inst::ConstFloat(f64::INFINITY), Inst::ConstInt(1));
        assert!(matches!(folded, Inst::ConstFloat(f) if f == f64::INFINITY));
    }

    /// Builds `op(load t2, const)` (or reversed) and runs the rules; returns
    /// the stored instruction.
    fn identity_case(op: Op, c: Inst, const_first: bool) -> Inst {
        let (mut mir, store) = store_mir(|mir| {
            let t2 = mir.push_temp("x", 1);
            let load = sched(
                mir,
                Inst::Load {
                    place: temp_place(t2),
                },
            );
            let cv = mir.push_inst(c);
            let args = if const_first {
                vec![cv, load]
            } else {
                vec![load, cv]
            };
            let inst = mir.push_inst(Inst::Op {
                op,
                pure_node: true,
                args,
            });
            mir.blocks[0].insts.push(inst);
            inst
        });
        run_rules(&mut mir);
        stored_value(&mir, store).clone()
    }

    #[test]
    fn mul_one_fires_both_positions() {
        assert!(matches!(
            identity_case(Op::Multiply, Inst::ConstInt(1), false),
            Inst::Load { .. }
        ));
        assert!(matches!(
            identity_case(Op::Multiply, Inst::ConstFloat(1.0), true),
            Inst::Load { .. }
        ));
        // Guard: 1.0000001 is not a multiplicative identity.
        assert!(matches!(
            identity_case(Op::Multiply, Inst::ConstFloat(1.000_000_1), false),
            Inst::Op {
                op: Op::Multiply,
                ..
            }
        ));
        // Guard: -1 is not either.
        assert!(matches!(
            identity_case(Op::Multiply, Inst::ConstInt(-1), false),
            Inst::Op {
                op: Op::Multiply,
                ..
            }
        ));
    }

    #[test]
    fn div_one_fires_only_on_the_divisor() {
        assert!(matches!(
            identity_case(Op::Divide, Inst::ConstInt(1), false),
            Inst::Load { .. }
        ));
        // 1 / x is NOT x.
        assert!(matches!(
            identity_case(Op::Divide, Inst::ConstInt(1), true),
            Inst::Op { op: Op::Divide, .. }
        ));
        // x / 0 must stay (trap preservation) — and DivOne must not touch it.
        assert!(matches!(
            identity_case(Op::Divide, Inst::ConstInt(0), false),
            Inst::Op { op: Op::Divide, .. }
        ));
    }

    #[test]
    fn add_zero_signed_zero_guards() {
        // x + zero with x an opaque load: x could be -0.0 at runtime (then
        // x + 0.0 == +0.0, observable via Sign) — must NOT fire, for any
        // spelling of the zero constant (-0.0 literals are +0.0 at runtime).
        for zero in [
            Inst::ConstInt(0),
            Inst::ConstFloat(0.0),
            Inst::ConstFloat(-0.0),
        ] {
            for const_first in [false, true] {
                assert!(
                    matches!(
                        identity_case(Op::Add, zero.clone(), const_first),
                        Inst::Op { op: Op::Add, .. }
                    ),
                    "Add(load, {zero:?}) must not fire (load could be -0.0)"
                );
            }
        }
    }

    #[test]
    fn add_zero_fires_for_never_negative_zero_operands() {
        // Sign(load) + 0 -> Sign(load): Sign yields ±1, never -0.0.
        let (mut mir, store) = store_mir(|mir| {
            let t2 = mir.push_temp("x", 1);
            let load = sched(
                mir,
                Inst::Load {
                    place: temp_place(t2),
                },
            );
            let sign = sched(
                mir,
                Inst::Op {
                    op: Op::Sign,
                    pure_node: true,
                    args: vec![load],
                },
            );
            let zero = mir.push_inst(Inst::ConstInt(0));
            let add = mir.push_inst(Inst::Op {
                op: Op::Add,
                pure_node: true,
                args: vec![sign, zero],
            });
            mir.blocks[0].insts.push(add);
            add
        });
        run_rules(&mut mir);
        assert!(matches!(
            stored_value(&mir, store),
            Inst::Op { op: Op::Sign, .. }
        ));
    }

    #[test]
    fn sub_zero_fires_for_any_zero_spelling() {
        // x - zero -> x: exact for every x (the subtrahend is +0.0 at runtime
        // and -0.0 - 0.0 == -0.0); fires for all three zero spellings.
        for zero in [
            Inst::ConstInt(0),
            Inst::ConstFloat(0.0),
            Inst::ConstFloat(-0.0),
        ] {
            assert!(
                matches!(
                    identity_case(Op::Subtract, zero.clone(), false),
                    Inst::Load { .. }
                ),
                "Subtract(load, {zero:?}) must fire"
            );
        }
        // Guard: a nonzero subtrahend must not fire.
        assert!(matches!(
            identity_case(Op::Subtract, Inst::ConstFloat(0.5), false),
            Inst::Op {
                op: Op::Subtract,
                ..
            }
        ));
    }

    #[test]
    fn negate_strength_reduction_requires_never_zero_operand() {
        // 0 - load stays: the load could be +0.0 (0.0 - 0.0 == +0.0, but
        // Negate(+0.0) == -0.0, observable via Sign).
        for zero in [
            Inst::ConstInt(0),
            Inst::ConstFloat(0.0),
            Inst::ConstFloat(-0.0),
        ] {
            assert!(matches!(
                identity_case(Op::Subtract, zero, true),
                Inst::Op {
                    op: Op::Subtract,
                    ..
                }
            ));
        }
        // 0 - Sign(load) -> Negate(Sign(load)): Sign yields exactly ±1.
        let (mut mir, store) = store_mir(|mir| {
            let t2 = mir.push_temp("x", 1);
            let load = sched(
                mir,
                Inst::Load {
                    place: temp_place(t2),
                },
            );
            let sign = sched(
                mir,
                Inst::Op {
                    op: Op::Sign,
                    pure_node: true,
                    args: vec![load],
                },
            );
            let zero = mir.push_inst(Inst::ConstInt(0));
            let sub = mir.push_inst(Inst::Op {
                op: Op::Subtract,
                pure_node: true,
                args: vec![zero, sign],
            });
            mir.blocks[0].insts.push(sub);
            sub
        });
        run_rules(&mut mir);
        assert!(matches!(
            stored_value(&mir, store),
            Inst::Op { op: Op::Negate, .. }
        ));
    }

    #[test]
    fn chained_folds_reach_fixpoint() {
        // Add(Add(1, 2), Multiply(2, 2.5)) -> Add(3, 5.0) -> 8.0 (float: one
        // operand chain was float).
        let (mut mir, store) = store_mir(|mir| {
            let c1 = mir.push_inst(Inst::ConstInt(1));
            let c2 = mir.push_inst(Inst::ConstInt(2));
            let a = sched(
                mir,
                Inst::Op {
                    op: Op::Add,
                    pure_node: true,
                    args: vec![c1, c2],
                },
            );
            let c3 = mir.push_inst(Inst::ConstInt(2));
            let c4 = mir.push_inst(Inst::ConstFloat(2.5));
            let m = sched(
                mir,
                Inst::Op {
                    op: Op::Multiply,
                    pure_node: true,
                    args: vec![c3, c4],
                },
            );
            let top = mir.push_inst(Inst::Op {
                op: Op::Add,
                pure_node: true,
                args: vec![a, m],
            });
            mir.blocks[0].insts.push(top);
            top
        });
        let report = run_rules(&mut mir);
        assert!(!report.capped);
        assert_eq!(report.replaced.len(), 3, "all three ops fold");
        assert_eq!(stored_value(&mir, store), &Inst::ConstFloat(8.0));
    }
}
