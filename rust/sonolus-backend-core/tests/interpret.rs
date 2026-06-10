//! Behavioral tests for the engine-node interpreter.
//!
//! Expected values for numeric edge cases are pinned from CPython 3.14 (the legacy
//! `sonolus/backend/interpret.py` oracle); see the inline comments. The Python-side
//! counterpart (`tests/backend/test_interpreter.py`) re-checks a subset differentially
//! against the legacy interpreter through the FFI.

use sonolus_backend_core::interpret::{Interpreter, InterpreterError, InterpreterErrorKind};
use sonolus_backend_core::nodes::{NodeArena, NodeId};
use sonolus_backend_core::ops::Op;

/// Test-side node spec. Converted into arena form by `build` (recursive, but only
/// used for small hand-written trees; deep tests build arenas with loops).
enum N {
    /// Int-tagged constant.
    I(f64),
    /// Float-tagged constant.
    F(f64),
    Fun(Op, Vec<N>),
}

fn f(op: Op, args: Vec<N>) -> N {
    N::Fun(op, args)
}

fn c(value: f64) -> N {
    N::F(value)
}

fn build(arena: &mut NodeArena, n: &N) -> NodeId {
    match n {
        N::I(v) => arena.push_int(*v),
        N::F(v) => arena.push_float(*v),
        N::Fun(op, args) => {
            let ids: Vec<NodeId> = args.iter().map(|a| build(arena, a)).collect();
            arena.push_func(*op, &ids)
        }
    }
}

fn run_in(interpreter: &mut Interpreter, n: &N) -> Result<f64, InterpreterError> {
    let mut arena = NodeArena::new();
    let root = build(&mut arena, n);
    interpreter.run_node(&arena, root)
}

fn run(n: N) -> Result<f64, InterpreterError> {
    run_in(&mut Interpreter::new(0), &n)
}

fn run_ok(n: N) -> f64 {
    run(n).expect("expected successful evaluation")
}

#[track_caller]
fn assert_err(n: N, kind: InterpreterErrorKind, message: &str) {
    let err = run(n).expect_err("expected an interpreter error");
    assert_eq!(err.kind, kind, "kind mismatch; message: {}", err.message);
    assert_eq!(err.message, message);
}

#[track_caller]
fn assert_bits(actual: f64, expected: f64) {
    assert_eq!(
        actual.to_bits(),
        expected.to_bits(),
        "{actual:?} != {expected:?} (bitwise)"
    );
}

const NAN: f64 = f64::NAN;
const INF: f64 = f64::INFINITY;

// -----------------------------------------------------------------------------------
// Numeric semantics: Python-pinned edge tables
// -----------------------------------------------------------------------------------

#[test]
fn mod_is_python_floor_mod() {
    // Pinned from CPython: operator.mod.
    let cases = [
        (5.0, 3.0, 2.0),
        (-5.0, 3.0, 1.0),
        (5.0, -3.0, -1.0),
        (-5.0, -3.0, -2.0),
        (5.5, 2.0, 1.5),
        (-5.5, 2.0, 0.5),
        (5.5, -2.0, -0.5),
        (-5.5, -2.0, -1.5),
        (7.0, 0.5, 0.0),
        (1.0, INF, 1.0),
        (-1.0, INF, INF),
        (1.0, -INF, -INF),
    ];
    for (a, b, expected) in cases {
        assert_eq!(run_ok(f(Op::Mod, vec![c(a), c(b)])), expected, "{a} % {b}");
    }
    // Zero results take the divisor's sign.
    assert_bits(run_ok(f(Op::Mod, vec![c(-0.0), c(1.0)])), 0.0);
    assert_bits(run_ok(f(Op::Mod, vec![c(0.0), c(-1.0)])), -0.0);
    // NaN propagation.
    assert!(run_ok(f(Op::Mod, vec![c(NAN), c(1.0)])).is_nan());
    assert!(run_ok(f(Op::Mod, vec![c(INF), c(1.0)])).is_nan());
}

#[test]
fn mod_by_zero_is_an_error() {
    assert_err(
        f(Op::Mod, vec![c(1.0), c(0.0)]),
        InterpreterErrorKind::ZeroDivision,
        "division by zero",
    );
    // Python checks the divisor before NaN handling.
    assert_err(
        f(Op::Mod, vec![c(NAN), c(0.0)]),
        InterpreterErrorKind::ZeroDivision,
        "division by zero",
    );
}

#[test]
fn divide_by_zero_is_an_error_even_for_floats() {
    assert_err(
        f(Op::Divide, vec![c(1.0), c(0.0)]),
        InterpreterErrorKind::ZeroDivision,
        "division by zero",
    );
    assert_err(
        f(Op::Divide, vec![c(0.0), c(0.0)]),
        InterpreterErrorKind::ZeroDivision,
        "division by zero",
    );
}

#[test]
fn reduce_args_semantics() {
    // Empty -> 0.0; single argument returned unchanged; left fold otherwise.
    assert_eq!(run_ok(f(Op::Add, vec![])), 0.0);
    assert_eq!(run_ok(f(Op::Divide, vec![c(5.0)])), 5.0);
    assert!(run_ok(f(Op::Divide, vec![c(NAN)])).is_nan());
    assert_eq!(run_ok(f(Op::Add, vec![c(1.0), c(2.0), c(3.0)])), 6.0);
    assert_eq!(run_ok(f(Op::Subtract, vec![c(10.0), c(3.0), c(2.0)])), 5.0);
    assert_eq!(run_ok(f(Op::Multiply, vec![c(2.0), c(3.0), c(4.0)])), 24.0);
    assert_eq!(run_ok(f(Op::Divide, vec![c(8.0), c(2.0), c(2.0)])), 2.0);
    assert_eq!(run_ok(f(Op::Mod, vec![c(17.0), c(7.0), c(2.0)])), 1.0);
    // Power folds left: (2 ** 3) ** 2 = 64.
    assert_eq!(run_ok(f(Op::Power, vec![c(2.0), c(3.0), c(2.0)])), 64.0);
    // All operands are evaluated before the fold (legacy generator unpacking), so
    // side effects happen even when the fold errors.
    let mut interp = Interpreter::new(0);
    let result = run_in(
        &mut interp,
        &f(
            Op::Divide,
            vec![c(1.0), c(0.0), f(Op::DebugLog, vec![c(7.0)])],
        ),
    );
    assert_eq!(result.unwrap_err().kind, InterpreterErrorKind::ZeroDivision);
    assert_eq!(interp.log(), &[7.0]);
}

#[test]
fn round_is_bankers() {
    // Pinned from CPython round().
    let cases = [
        (0.5, 0.0),
        (1.5, 2.0),
        (2.5, 2.0),
        (-0.5, 0.0),
        (-1.5, -2.0),
        (-2.5, -2.0),
        (2.675, 3.0),
        (0.499_999_999_999_999_94, 0.0),
        (3.0, 3.0),
    ];
    for (x, expected) in cases {
        assert_eq!(run_ok(f(Op::Round, vec![c(x)])), expected, "round({x})");
    }
    // Python round() returns int: no negative zero.
    assert_bits(run_ok(f(Op::Round, vec![c(-0.5)])), 0.0);
    assert_err(
        f(Op::Round, vec![c(NAN)]),
        InterpreterErrorKind::Value,
        "cannot convert float NaN to integer",
    );
    assert_err(
        f(Op::Round, vec![c(INF)]),
        InterpreterErrorKind::Overflow,
        "cannot convert float infinity to integer",
    );
}

#[test]
fn rem_is_ieee_remainder() {
    // Pinned from CPython math.remainder.
    let cases = [
        (5.0, 3.0, -1.0),
        (-5.0, 3.0, 1.0),
        (5.0, -3.0, -1.0),
        (7.5, 2.0, -0.5),
        (2.5, 2.0, 0.5),
        (3.5, 2.0, -0.5),
        (2.0, INF, 2.0),
        // Ties round the quotient to even:
        (5.0, 4.0, 1.0),
        (-5.0, 4.0, -1.0),
        (6.0, 4.0, -2.0),
        (1.0, 2.0, 1.0),
        (3.0, 2.0, -1.0),
    ];
    for (a, b, expected) in cases {
        assert_eq!(
            run_ok(f(Op::Rem, vec![c(a), c(b)])),
            expected,
            "remainder({a}, {b})"
        );
    }
    assert_bits(run_ok(f(Op::Rem, vec![c(0.0), c(2.0)])), 0.0);
    assert_bits(run_ok(f(Op::Rem, vec![c(-0.0), c(2.0)])), -0.0);
    assert!(run_ok(f(Op::Rem, vec![c(NAN), c(2.0)])).is_nan());
    assert!(run_ok(f(Op::Rem, vec![c(2.0), c(NAN)])).is_nan());
    assert_err(
        f(Op::Rem, vec![c(1.0), c(0.0)]),
        InterpreterErrorKind::Value,
        "math domain error",
    );
    assert_err(
        f(Op::Rem, vec![c(INF), c(2.0)]),
        InterpreterErrorKind::Value,
        "math domain error",
    );
}

#[test]
fn sign_is_copysign_of_one() {
    assert_eq!(run_ok(f(Op::Sign, vec![c(3.5)])), 1.0);
    assert_eq!(run_ok(f(Op::Sign, vec![c(-3.5)])), -1.0);
    assert_eq!(run_ok(f(Op::Sign, vec![c(0.0)])), 1.0);
    assert_eq!(run_ok(f(Op::Sign, vec![c(-0.0)])), -1.0);
    assert_eq!(run_ok(f(Op::Sign, vec![c(NAN)])), 1.0);
    assert_eq!(run_ok(f(Op::Sign, vec![c(-NAN)])), -1.0);
    assert_eq!(run_ok(f(Op::Sign, vec![c(-INF)])), -1.0);
}

#[test]
fn frac_semantics() {
    // Pinned from the legacy interpreter: x % 1, then +1 if negative.
    let cases = [
        (2.25, 0.25),
        (-2.25, 0.75),
        (-0.5, 0.5),
        (0.0, 0.0),
        (-0.0, 0.0),
        (5.0, 0.0),
        (-5.0, 0.0),
    ];
    for (x, expected) in cases {
        assert_eq!(run_ok(f(Op::Frac, vec![c(x)])), expected, "frac({x})");
    }
    assert!(run_ok(f(Op::Frac, vec![c(INF)])).is_nan());
    assert!(run_ok(f(Op::Frac, vec![c(-INF)])).is_nan());
    assert!(run_ok(f(Op::Frac, vec![c(NAN)])).is_nan());
}

#[test]
fn power_special_cases() {
    // Pinned from CPython float.__pow__.
    assert_eq!(run_ok(f(Op::Power, vec![c(0.0), c(0.0)])), 1.0);
    assert_eq!(run_ok(f(Op::Power, vec![c(NAN), c(0.0)])), 1.0);
    assert_eq!(run_ok(f(Op::Power, vec![c(1.0), c(NAN)])), 1.0);
    assert!(run_ok(f(Op::Power, vec![c(-1.0), c(NAN)])).is_nan());
    assert_eq!(run_ok(f(Op::Power, vec![c(-1.0), c(INF)])), 1.0);
    assert_eq!(run_ok(f(Op::Power, vec![c(-2.0), c(INF)])), INF);
    assert_eq!(run_ok(f(Op::Power, vec![c(-2.0), c(-INF)])), 0.0);
    assert_eq!(run_ok(f(Op::Power, vec![c(-INF), c(3.0)])), -INF);
    assert_bits(run_ok(f(Op::Power, vec![c(-INF), c(-3.0)])), -0.0);
    assert_eq!(run_ok(f(Op::Power, vec![c(-INF), c(2.5)])), INF);
    assert_eq!(run_ok(f(Op::Power, vec![c(-2.0), c(3.0)])), -8.0);
    assert_eq!(run_ok(f(Op::Power, vec![c(-2.0), c(2.0)])), 4.0);
    assert_bits(run_ok(f(Op::Power, vec![c(-0.0), c(3.0)])), -0.0);
    assert_eq!(run_ok(f(Op::Power, vec![c(1e-308), c(2.0)])), 0.0);

    assert_err(
        f(Op::Power, vec![c(0.0), c(-1.0)]),
        InterpreterErrorKind::ZeroDivision,
        "zero to a negative power",
    );
    assert_err(
        f(Op::Power, vec![c(-0.0), c(-3.0)]),
        InterpreterErrorKind::ZeroDivision,
        "zero to a negative power",
    );
    // Python returns a complex number here; a documented divergence makes it an error.
    let err = run(f(Op::Power, vec![c(-2.0), c(0.5)])).unwrap_err();
    assert_eq!(err.kind, InterpreterErrorKind::Value);
    assert!(err.message.contains("fractional power"));
    // Finite overflow raises OverflowError in Python too (errno ERANGE).
    let err = run(f(Op::Power, vec![c(2.0), c(10_000.0)])).unwrap_err();
    assert_eq!(err.kind, InterpreterErrorKind::Overflow);
    assert_eq!(err.message, "math range error");
}

#[test]
fn math_domain_and_range_errors() {
    assert_err(
        f(Op::Log, vec![c(0.0)]),
        InterpreterErrorKind::Value,
        "expected a positive input, got 0.0",
    );
    assert_err(
        f(Op::Log, vec![c(-1.0)]),
        InterpreterErrorKind::Value,
        "expected a positive input, got -1.0",
    );
    assert!(run_ok(f(Op::Log, vec![c(NAN)])).is_nan());
    assert_eq!(run_ok(f(Op::Log, vec![c(INF)])), INF);
    assert_eq!(run_ok(f(Op::Log, vec![c(1.0)])), 0.0);

    assert_err(
        f(Op::Arccos, vec![c(2.0)]),
        InterpreterErrorKind::Value,
        "expected a number in range from -1 up to 1, got 2.0",
    );
    assert_err(
        f(Op::Arcsin, vec![c(-1.5)]),
        InterpreterErrorKind::Value,
        "expected a number in range from -1 up to 1, got -1.5",
    );
    assert!(run_ok(f(Op::Arccos, vec![c(NAN)])).is_nan());
    assert_eq!(run_ok(f(Op::Arccos, vec![c(1.0)])), 0.0);

    assert_err(
        f(Op::Cos, vec![c(INF)]),
        InterpreterErrorKind::Value,
        "expected a finite input, got inf",
    );
    assert_err(
        f(Op::Tan, vec![c(-INF)]),
        InterpreterErrorKind::Value,
        "expected a finite input, got -inf",
    );
    assert!(run_ok(f(Op::Sin, vec![c(NAN)])).is_nan());

    assert_err(
        f(Op::Cosh, vec![c(1000.0)]),
        InterpreterErrorKind::Overflow,
        "math range error",
    );
    assert_err(
        f(Op::Sinh, vec![c(-1000.0)]),
        InterpreterErrorKind::Overflow,
        "math range error",
    );
    assert_eq!(run_ok(f(Op::Cosh, vec![c(INF)])), INF);
}

#[test]
fn ceil_floor_trunc() {
    assert_eq!(run_ok(f(Op::Ceil, vec![c(2.1)])), 3.0);
    assert_eq!(run_ok(f(Op::Ceil, vec![c(-2.9)])), -2.0);
    assert_eq!(run_ok(f(Op::Floor, vec![c(2.9)])), 2.0);
    assert_eq!(run_ok(f(Op::Floor, vec![c(-2.1)])), -3.0);
    assert_eq!(run_ok(f(Op::Trunc, vec![c(2.9)])), 2.0);
    assert_eq!(run_ok(f(Op::Trunc, vec![c(-2.9)])), -2.0);
    // Python's math.ceil/floor/trunc return ints: no negative zero, NaN/inf error.
    assert_bits(run_ok(f(Op::Ceil, vec![c(-0.5)])), 0.0);
    assert_bits(run_ok(f(Op::Trunc, vec![c(-0.5)])), 0.0);
    assert_bits(run_ok(f(Op::Floor, vec![c(-0.0)])), 0.0);
    assert_err(
        f(Op::Ceil, vec![c(NAN)]),
        InterpreterErrorKind::Value,
        "cannot convert float NaN to integer",
    );
    assert_err(
        f(Op::Floor, vec![c(-INF)]),
        InterpreterErrorKind::Overflow,
        "cannot convert float infinity to integer",
    );
    assert_err(
        f(Op::Trunc, vec![c(NAN)]),
        InterpreterErrorKind::Value,
        "cannot convert float NaN to integer",
    );
}

#[test]
fn min_max_clamp_follow_python_argument_order() {
    // Python max/min keep the first argument on ties and NaN comparisons.
    assert!(run_ok(f(Op::Max, vec![c(NAN), c(1.0)])).is_nan());
    assert_eq!(run_ok(f(Op::Max, vec![c(1.0), c(NAN)])), 1.0);
    assert!(run_ok(f(Op::Min, vec![c(NAN), c(1.0)])).is_nan());
    assert_eq!(run_ok(f(Op::Min, vec![c(1.0), c(NAN)])), 1.0);
    assert_bits(run_ok(f(Op::Max, vec![c(-0.0), c(0.0)])), -0.0);
    assert_bits(run_ok(f(Op::Max, vec![c(0.0), c(-0.0)])), 0.0);
    assert_bits(run_ok(f(Op::Min, vec![c(0.0), c(-0.0)])), 0.0);
    assert_bits(run_ok(f(Op::Min, vec![c(-0.0), c(0.0)])), -0.0);
    assert_eq!(run_ok(f(Op::Max, vec![c(2.0), c(3.0)])), 3.0);
    assert_eq!(run_ok(f(Op::Min, vec![c(2.0), c(3.0)])), 2.0);

    // Clamp is literally max(a, min(b, x)); args are (x, a, b).
    assert_eq!(run_ok(f(Op::Clamp, vec![c(2.0), c(0.0), c(1.0)])), 1.0);
    assert_eq!(run_ok(f(Op::Clamp, vec![c(-1.0), c(0.0), c(1.0)])), 0.0);
    assert_eq!(run_ok(f(Op::Clamp, vec![c(0.5), c(0.0), c(1.0)])), 0.5);
    // NaN propagation order pinned from Python:
    assert_eq!(run_ok(f(Op::Clamp, vec![c(NAN), c(0.0), c(1.0)])), 1.0);
    assert!(run_ok(f(Op::Clamp, vec![c(0.5), c(NAN), c(1.0)])).is_nan());
    assert_eq!(run_ok(f(Op::Clamp, vec![c(0.5), c(0.0), c(NAN)])), 0.0);
}

#[test]
fn lerp_unlerp_remap() {
    assert_eq!(run_ok(f(Op::Lerp, vec![c(10.0), c(20.0), c(0.25)])), 12.5);
    assert_eq!(run_ok(f(Op::Lerp, vec![c(10.0), c(20.0), c(2.0)])), 30.0);
    assert_eq!(
        run_ok(f(Op::LerpClamped, vec![c(10.0), c(20.0), c(2.0)])),
        20.0
    );
    assert_eq!(
        run_ok(f(Op::LerpClamped, vec![c(10.0), c(20.0), c(-1.0)])),
        10.0
    );
    // NaN s clamps to 1 (Python max(0, min(1, nan)) == 1).
    assert_eq!(
        run_ok(f(Op::LerpClamped, vec![c(10.0), c(20.0), c(NAN)])),
        20.0
    );
    assert_eq!(run_ok(f(Op::Unlerp, vec![c(10.0), c(20.0), c(15.0)])), 0.5);
    assert_err(
        f(Op::Unlerp, vec![c(10.0), c(10.0), c(15.0)]),
        InterpreterErrorKind::ZeroDivision,
        "division by zero",
    );
    assert_eq!(
        run_ok(f(Op::UnlerpClamped, vec![c(10.0), c(20.0), c(25.0)])),
        1.0
    );
    assert_err(
        f(Op::UnlerpClamped, vec![c(10.0), c(10.0), c(15.0)]),
        InterpreterErrorKind::ZeroDivision,
        "division by zero",
    );
    // Remap args: (from_min, from_max, to_min, to_max, value).
    assert_eq!(
        run_ok(f(
            Op::Remap,
            vec![c(0.0), c(10.0), c(100.0), c(200.0), c(2.5)]
        )),
        125.0
    );
    assert_err(
        f(Op::Remap, vec![c(5.0), c(5.0), c(100.0), c(200.0), c(2.5)]),
        InterpreterErrorKind::ZeroDivision,
        "division by zero",
    );
    assert_eq!(
        run_ok(f(
            Op::RemapClamped,
            vec![c(0.0), c(10.0), c(100.0), c(200.0), c(25.0)]
        )),
        200.0
    );
}

#[test]
fn simple_unary_and_comparisons() {
    assert_eq!(run_ok(f(Op::Abs, vec![c(-2.5)])), 2.5);
    assert_eq!(run_ok(f(Op::Negate, vec![c(2.5)])), -2.5);
    assert_eq!(run_ok(f(Op::Not, vec![c(0.0)])), 1.0);
    assert_eq!(run_ok(f(Op::Not, vec![c(2.0)])), 0.0);
    assert_eq!(run_ok(f(Op::Not, vec![c(NAN)])), 0.0);
    assert_eq!(run_ok(f(Op::Equal, vec![c(2.0), c(2.0)])), 1.0);
    assert_eq!(run_ok(f(Op::Equal, vec![c(NAN), c(NAN)])), 0.0);
    assert_eq!(run_ok(f(Op::NotEqual, vec![c(NAN), c(NAN)])), 1.0);
    assert_eq!(run_ok(f(Op::Greater, vec![c(3.0), c(2.0)])), 1.0);
    assert_eq!(run_ok(f(Op::GreaterOr, vec![c(2.0), c(2.0)])), 1.0);
    assert_eq!(run_ok(f(Op::Less, vec![c(3.0), c(2.0)])), 0.0);
    assert_eq!(run_ok(f(Op::LessOr, vec![c(2.0), c(2.0)])), 1.0);
    assert_eq!(
        run_ok(f(Op::Arctan2, vec![c(0.0), c(-1.0)])),
        std::f64::consts::PI
    );
    assert_eq!(run_ok(f(Op::Degree, vec![c(std::f64::consts::PI)])), 180.0);
    assert_eq!(run_ok(f(Op::Radian, vec![c(180.0)])), std::f64::consts::PI);
}

// -----------------------------------------------------------------------------------
// Control flow
// -----------------------------------------------------------------------------------

#[test]
fn execute_returns_last_value() {
    assert_eq!(run_ok(f(Op::Execute, vec![])), 0.0);
    assert_eq!(run_ok(f(Op::Execute, vec![c(1.0), c(2.0), c(3.0)])), 3.0);
    assert_eq!(run_ok(f(Op::Execute0, vec![])), 0.0);
    assert_eq!(run_ok(f(Op::Execute0, vec![c(1.0), c(2.0)])), 0.0);
}

#[test]
fn if_branches_on_nonzero() {
    assert_eq!(run_ok(f(Op::If, vec![c(1.0), c(10.0), c(20.0)])), 10.0);
    assert_eq!(run_ok(f(Op::If, vec![c(0.0), c(10.0), c(20.0)])), 20.0);
    assert_eq!(run_ok(f(Op::If, vec![c(-3.0), c(10.0), c(20.0)])), 10.0);
    // NaN != 0.0 is true in Python.
    assert_eq!(run_ok(f(Op::If, vec![c(NAN), c(10.0), c(20.0)])), 10.0);
    // Only the taken branch is evaluated.
    let mut interp = Interpreter::new(0);
    let n = f(
        Op::If,
        vec![
            c(0.0),
            f(Op::DebugLog, vec![c(1.0)]),
            f(Op::DebugLog, vec![c(2.0)]),
        ],
    );
    assert_eq!(run_in(&mut interp, &n).unwrap(), 0.0);
    assert_eq!(interp.log(), &[2.0]);
}

#[test]
fn switch_matches_first_equal_case() {
    // Args: test, case, branch, case, branch, ...
    let n = |test: f64| {
        f(
            Op::Switch,
            vec![c(test), c(1.0), c(10.0), c(2.5), c(20.0), c(3.0), c(30.0)],
        )
    };
    assert_eq!(run_ok(n(1.0)), 10.0);
    assert_eq!(run_ok(n(2.5)), 20.0);
    assert_eq!(run_ok(n(3.0)), 30.0);
    assert_eq!(run_ok(n(4.0)), 0.0); // no match -> 0.0
    assert_eq!(run_ok(n(NAN)), 0.0);

    // Cases are evaluated in order until the first match; later cases and branches
    // are not evaluated.
    let mut interp = Interpreter::new(0);
    let logged_case =
        |id: f64, value: f64| f(Op::Execute, vec![f(Op::DebugLog, vec![c(id)]), c(value)]);
    let n = f(
        Op::Switch,
        vec![
            c(2.0),
            logged_case(1.0, 1.0),
            c(10.0),
            logged_case(2.0, 2.0),
            c(20.0),
            logged_case(3.0, 3.0),
            c(30.0),
        ],
    );
    assert_eq!(run_in(&mut interp, &n).unwrap(), 20.0);
    assert_eq!(interp.log(), &[1.0, 2.0]);
}

#[test]
fn switch_with_default() {
    let n = |test: f64| {
        f(
            Op::SwitchWithDefault,
            vec![c(test), c(1.0), c(10.0), c(2.0), c(20.0), c(99.0)],
        )
    };
    assert_eq!(run_ok(n(1.0)), 10.0);
    assert_eq!(run_ok(n(2.0)), 20.0);
    assert_eq!(run_ok(n(3.0)), 99.0);
    assert_eq!(run_ok(n(NAN)), 99.0);
}

#[test]
fn switch_odd_pair_list_is_index_error() {
    // Python: `branches[i + 1]` raises IndexError at the incomplete pair, before the
    // case value is compared.
    assert_err(
        f(Op::Switch, vec![c(9.0), c(1.0), c(10.0), c(2.0)]),
        InterpreterErrorKind::Index,
        "list index out of range",
    );
    assert_err(
        f(
            Op::SwitchWithDefault,
            vec![c(9.0), c(1.0), c(10.0), c(2.0), c(99.0)],
        ),
        InterpreterErrorKind::Index,
        "list index out of range",
    );
}

#[test]
fn switch_integer_indexes_branches() {
    let n = |test: N| f(Op::SwitchInteger, vec![test, c(10.0), c(20.0), c(30.0)]);
    assert_eq!(run_ok(n(N::I(0.0))), 10.0);
    assert_eq!(run_ok(n(N::I(2.0))), 30.0);
    // Divergence (documented): Python raises TypeError for float-typed integral
    // scrutinees; on f64 an integral value simply selects the branch.
    assert_eq!(run_ok(n(c(1.0))), 20.0);
    assert_eq!(run_ok(n(c(-0.0))), 10.0);
    // Out of range or non-integral -> 0.0 without evaluating any branch.
    assert_eq!(run_ok(n(c(-1.0))), 0.0);
    assert_eq!(run_ok(n(c(3.0))), 0.0); // == len(branches)
    assert_eq!(run_ok(n(c(1.5))), 0.0);
    assert_eq!(run_ok(n(c(NAN))), 0.0);
    assert_eq!(run_ok(n(c(INF))), 0.0);
}

#[test]
fn switch_integer_with_default() {
    let n = |test: f64| {
        f(
            Op::SwitchIntegerWithDefault,
            vec![c(test), c(10.0), c(20.0), c(30.0), c(99.0)],
        )
    };
    assert_eq!(run_ok(n(0.0)), 10.0);
    assert_eq!(run_ok(n(2.0)), 30.0);
    assert_eq!(run_ok(n(-1.0)), 99.0);
    assert_eq!(run_ok(n(3.0)), 99.0); // == len(branches)
    assert_eq!(run_ok(n(1.5)), 99.0);
    assert_eq!(run_ok(n(NAN)), 99.0);
    assert_eq!(run_ok(n(-INF)), 99.0);
}

#[test]
fn while_loop() {
    // while get(0, 0) < 5: set(0, 0, get(0, 0) + 1)
    let mut interp = Interpreter::new(0);
    interp.set_block(0, vec![0.0]);
    let n = f(
        Op::While,
        vec![
            f(Op::Less, vec![f(Op::Get, vec![c(0.0), c(0.0)]), c(5.0)]),
            f(
                Op::Set,
                vec![
                    c(0.0),
                    c(0.0),
                    f(Op::Add, vec![f(Op::Get, vec![c(0.0), c(0.0)]), c(1.0)]),
                ],
            ),
        ],
    );
    assert_eq!(run_in(&mut interp, &n).unwrap(), 0.0);
    assert_eq!(interp.block(0).unwrap(), &[5.0]);
    // Zero-iteration while: body never runs.
    let mut interp = Interpreter::new(0);
    let n = f(Op::While, vec![c(0.0), f(Op::DebugLog, vec![c(1.0)])]);
    assert_eq!(run_in(&mut interp, &n).unwrap(), 0.0);
    assert_eq!(interp.log(), &[] as &[f64]);
}

#[test]
fn do_while_runs_body_at_least_once() {
    let mut interp = Interpreter::new(0);
    let n = f(Op::DoWhile, vec![f(Op::DebugLog, vec![c(1.0)]), c(0.0)]);
    assert_eq!(run_in(&mut interp, &n).unwrap(), 0.0);
    assert_eq!(interp.log(), &[1.0]);

    // do { increment } while (get(0,0) < 3); memory starts unset (-1.0 fill).
    let mut interp = Interpreter::new(0);
    let n = f(
        Op::DoWhile,
        vec![
            f(Op::IncrementPost, vec![c(0.0), c(0.0)]),
            f(Op::Less, vec![f(Op::Get, vec![c(0.0), c(0.0)]), c(3.0)]),
        ],
    );
    assert_eq!(run_in(&mut interp, &n).unwrap(), 0.0);
    assert_eq!(interp.block(0).unwrap(), &[3.0]);
}

#[test]
fn and_or_short_circuit_and_return_last_value() {
    assert_eq!(run_ok(f(Op::And, vec![])), 0.0);
    assert_eq!(run_ok(f(Op::Or, vec![])), 0.0);
    assert_eq!(run_ok(f(Op::And, vec![c(2.0), c(3.0)])), 3.0);
    assert_eq!(run_ok(f(Op::And, vec![c(2.0), c(0.0), c(5.0)])), 0.0);
    assert_eq!(run_ok(f(Op::Or, vec![c(0.0), c(5.0), c(7.0)])), 5.0);
    assert_eq!(run_ok(f(Op::Or, vec![c(0.0), c(0.0)])), 0.0);
    assert!(run_ok(f(Op::And, vec![c(1.0), c(NAN)])).is_nan());

    // Short-circuit: later arguments are not evaluated.
    let mut interp = Interpreter::new(0);
    let n = f(Op::And, vec![c(0.0), f(Op::DebugLog, vec![c(1.0)])]);
    assert_eq!(run_in(&mut interp, &n).unwrap(), 0.0);
    assert_eq!(interp.log(), &[] as &[f64]);
    let mut interp = Interpreter::new(0);
    let n = f(Op::Or, vec![c(4.0), f(Op::DebugLog, vec![c(1.0)])]);
    assert_eq!(run_in(&mut interp, &n).unwrap(), 4.0);
    assert_eq!(interp.log(), &[] as &[f64]);
}

#[test]
fn jump_loop_walks_indices_and_returns_tail() {
    // args: [-> 1, -> 2, tail]; logs record visit order.
    let step = |id: f64, next: f64| f(Op::Execute, vec![f(Op::DebugLog, vec![c(id)]), c(next)]);
    let mut interp = Interpreter::new(0);
    let n = f(Op::JumpLoop, vec![step(0.0, 1.0), step(1.0, 2.0), c(42.0)]);
    assert_eq!(run_in(&mut interp, &n).unwrap(), 42.0);
    assert_eq!(interp.log(), &[0.0, 1.0]);
    assert_eq!(interp.dispatch_count(), 2); // two non-tail dispatches; tail uncounted

    // Negative or too-large indices exit the loop with 0.0.
    assert_eq!(run_ok(f(Op::JumpLoop, vec![c(-1.0), c(42.0)])), 0.0);
    assert_eq!(run_ok(f(Op::JumpLoop, vec![c(5.0), c(42.0)])), 0.0);
    // int() truncation toward zero: 1.9 -> 1 (the tail here).
    assert_eq!(run_ok(f(Op::JumpLoop, vec![c(1.9), c(42.0)])), 42.0);
    // -0.9 -> 0, NOT -1: the dispatcher at index 0 runs again. It returns -0.9 on
    // the first dispatch (IncrementPost yields the unset value -1) and 9.0 after,
    // so floor-style truncation would exit after one dispatch while Python's int()
    // dispatches twice.
    let mut interp = Interpreter::new(0);
    let n = f(
        Op::JumpLoop,
        vec![
            f(
                Op::If,
                vec![
                    f(
                        Op::Equal,
                        vec![f(Op::IncrementPost, vec![c(0.0), c(0.0)]), c(-1.0)],
                    ),
                    c(-0.9),
                    c(9.0),
                ],
            ),
            c(42.0),
        ],
    );
    assert_eq!(run_in(&mut interp, &n).unwrap(), 0.0); // 9 is out of range
    assert_eq!(interp.dispatch_count(), 2);
    assert_eq!(interp.block(0).unwrap(), &[1.0]); // dispatcher ran twice
    // Empty and single-arg (tail-only) forms.
    assert_eq!(run_ok(f(Op::JumpLoop, vec![])), 0.0);
    assert_eq!(run_ok(f(Op::JumpLoop, vec![c(7.0)])), 7.0);
    // NaN/inf dispatch values error like Python int().
    assert_err(
        f(Op::JumpLoop, vec![c(NAN), c(42.0)]),
        InterpreterErrorKind::Value,
        "cannot convert float NaN to integer",
    );
    assert_err(
        f(Op::JumpLoop, vec![c(INF), c(42.0)]),
        InterpreterErrorKind::Overflow,
        "cannot convert float infinity to integer",
    );
}

#[test]
fn block_break_unwinding() {
    // Simple catch.
    assert_eq!(
        run_ok(f(Op::Block, vec![f(Op::Break, vec![N::I(1.0), c(5.0)])])),
        5.0
    );
    // Break skips the rest of the enclosing computation.
    let mut interp = Interpreter::new(0);
    let n = f(
        Op::Block,
        vec![f(
            Op::Execute,
            vec![
                f(Op::DebugLog, vec![c(1.0)]),
                f(Op::Break, vec![N::I(1.0), c(3.0)]),
                f(Op::DebugLog, vec![c(2.0)]),
            ],
        )],
    );
    assert_eq!(run_in(&mut interp, &n).unwrap(), 3.0);
    assert_eq!(interp.log(), &[1.0]);
    // Multi-level: n=2 crosses one Block, caught by the second.
    assert_eq!(
        run_ok(f(
            Op::Block,
            vec![f(
                Op::Execute,
                vec![
                    f(Op::Block, vec![f(Op::Break, vec![N::I(2.0), c(42.0)])]),
                    c(99.0),
                ],
            )],
        )),
        42.0
    );
    // n=3 crosses two Blocks.
    assert_eq!(
        run_ok(f(
            Op::Block,
            vec![f(
                Op::Block,
                vec![f(Op::Block, vec![f(Op::Break, vec![N::I(3.0), c(7.0)])])],
            )],
        )),
        7.0
    );
    // n=0 and negative n are caught by the first Block (Python: `e.n > 1` is False).
    assert_eq!(
        run_ok(f(Op::Block, vec![f(Op::Break, vec![N::I(0.0), c(11.0)])])),
        11.0
    );
    assert_eq!(
        run_ok(f(Op::Block, vec![f(Op::Break, vec![N::I(-5.0), c(12.0)])])),
        12.0
    );
    // Block passes through the value when no Break occurs.
    assert_eq!(run_ok(f(Op::Block, vec![c(8.0)])), 8.0);
    // Uncaught Break is an error (divergence: Python lets BreakException escape).
    let err = run(f(Op::Break, vec![N::I(1.0), c(5.0)])).unwrap_err();
    assert_eq!(err.kind, InterpreterErrorKind::Runtime);
    let err = run(f(Op::Block, vec![f(Op::Break, vec![N::I(2.0), c(5.0)])])).unwrap_err();
    assert_eq!(err.kind, InterpreterErrorKind::Runtime);
    // Break level must be an integer (ensure_int).
    assert_err(
        f(Op::Block, vec![f(Op::Break, vec![c(1.5), c(0.0)])]),
        InterpreterErrorKind::Assertion,
        "Value must be an integer",
    );
}

#[test]
fn deep_execute_chain_is_iterative() {
    // 200_000-deep nested Execute chain: requires the explicit work stack.
    let mut arena = NodeArena::new();
    let mut node = arena.push_float(13.5);
    for _ in 0..200_000 {
        node = arena.push_func(Op::Execute, &[node]);
    }
    let mut interp = Interpreter::new(0);
    assert_eq!(interp.run_node(&arena, node).unwrap(), 13.5);
    assert_eq!(interp.eval_count(), 200_001);
}

#[test]
fn deep_block_break_unwinding_is_iterative() {
    // Break(200_000) unwinds through 200_000 nested Blocks iteratively.
    let depth = 200_000;
    let mut arena = NodeArena::new();
    #[allow(clippy::cast_precision_loss)]
    let n_const = arena.push_int(depth as f64);
    let value = arena.push_float(42.0);
    let mut node = arena.push_func(Op::Break, &[n_const, value]);
    for _ in 0..depth {
        node = arena.push_func(Op::Block, &[node]);
    }
    let mut interp = Interpreter::new(0);
    assert_eq!(interp.run_node(&arena, node).unwrap(), 42.0);
}

// -----------------------------------------------------------------------------------
// Memory
// -----------------------------------------------------------------------------------

#[test]
fn memory_extends_with_negative_one_fill() {
    let mut interp = Interpreter::new(0);
    assert_eq!(
        run_in(&mut interp, &f(Op::Get, vec![c(5.0), c(3.0)])).unwrap(),
        -1.0
    );
    assert_eq!(interp.block(5).unwrap(), &[-1.0, -1.0, -1.0, -1.0]);
    // Negative block ids work (used by the test harness).
    let n = f(Op::Set, vec![c(-2.0), c(1.0), c(9.0)]);
    assert_eq!(run_in(&mut interp, &n).unwrap(), 9.0);
    assert_eq!(interp.block(-2).unwrap(), &[-1.0, 9.0]);
    assert_eq!(interp.block_ids(), vec![-2, 5]);
    // The mutating legacy `get` is exposed directly.
    assert_eq!(interp.get(-2.0, 1.0).unwrap(), 9.0);
    assert_eq!(interp.get(7.0, 0.0).unwrap(), -1.0);
    assert_eq!(interp.block(7).unwrap(), &[-1.0]);
}

#[test]
fn memory_assert_messages_are_exact() {
    assert_err(
        f(Op::Get, vec![c(5.0), c(-1.0)]),
        InterpreterErrorKind::Assertion,
        "Index must be non-negative",
    );
    assert_err(
        f(Op::Get, vec![c(5.0), c(65536.0)]),
        InterpreterErrorKind::Assertion,
        "Index is too large",
    );
    assert_eq!(run_ok(f(Op::Get, vec![c(5.0), c(65535.0)])), -1.0);
    assert_err(
        f(Op::Get, vec![c(1.5), c(0.0)]),
        InterpreterErrorKind::Assertion,
        "Value must be an integer",
    );
    assert_err(
        f(Op::Set, vec![c(0.0), c(0.25), c(1.0)]),
        InterpreterErrorKind::Assertion,
        "Value must be an integer",
    );
    assert_err(
        f(Op::Copy, vec![c(0.0), c(0.0), c(1.0), c(0.0), c(-1.0)]),
        InterpreterErrorKind::Assertion,
        "Count must be non-negative",
    );
}

#[test]
fn get_interleaves_ensure_int_but_set_collects_first() {
    // Op.Get: ensure_int runs per argument as it is evaluated, so a bad first
    // argument stops evaluation before the second argument runs.
    let mut interp = Interpreter::new(0);
    let n = f(
        Op::Get,
        vec![
            c(1.5),
            f(Op::Execute, vec![f(Op::DebugLog, vec![c(7.0)]), c(0.0)]),
        ],
    );
    let err = run_in(&mut interp, &n).unwrap_err();
    assert_eq!(err.message, "Value must be an integer");
    assert_eq!(interp.log(), &[] as &[f64]);
    // Op.Set: all arguments are evaluated first; ensure_int runs afterwards.
    let mut interp = Interpreter::new(0);
    let n = f(
        Op::Set,
        vec![
            c(1.5),
            f(Op::Execute, vec![f(Op::DebugLog, vec![c(7.0)]), c(0.0)]),
            c(2.0),
        ],
    );
    let err = run_in(&mut interp, &n).unwrap_err();
    assert_eq!(err.message, "Value must be an integer");
    assert_eq!(interp.log(), &[7.0]);
}

#[test]
fn copy_reads_everything_before_writing() {
    let mut interp = Interpreter::new(0);
    interp.set_block(1, vec![1.0, 2.0, 3.0]);
    // Overlapping forward copy: [1, 2, 3] -> [1, 1, 2].
    let n = f(Op::Copy, vec![c(1.0), c(0.0), c(1.0), c(1.0), c(2.0)]);
    assert_eq!(run_in(&mut interp, &n).unwrap(), 0.0);
    assert_eq!(interp.block(1).unwrap(), &[1.0, 1.0, 2.0]);
    // Copy across blocks, reading unset cells as -1.0.
    let n = f(Op::Copy, vec![c(2.0), c(0.0), c(3.0), c(1.0), c(2.0)]);
    assert_eq!(run_in(&mut interp, &n).unwrap(), 0.0);
    assert_eq!(interp.block(3).unwrap(), &[-1.0, -1.0, -1.0]);
    // Zero count copies nothing.
    let n = f(Op::Copy, vec![c(10.0), c(0.0), c(11.0), c(0.0), c(0.0)]);
    assert_eq!(run_in(&mut interp, &n).unwrap(), 0.0);
    assert!(interp.block(11).is_none());
}

#[test]
fn pointed_and_shifted_accessors() {
    let mut interp = Interpreter::new(0);
    // Block 1 holds a (block, index) pointer pair: -> block 3, index 2.
    interp.set_block(1, vec![3.0, 2.0]);
    interp.set_block(3, vec![0.0, 0.0, 0.0, 9.0]);
    // GetPointed(block=1, index=0, offset=1): reads block 3 index 2+1.
    let n = f(Op::GetPointed, vec![c(1.0), c(0.0), c(1.0)]);
    assert_eq!(run_in(&mut interp, &n).unwrap(), 9.0);
    // SetPointed(block=1, index=0, offset=2, value=5): writes block 3 index 4.
    let n = f(Op::SetPointed, vec![c(1.0), c(0.0), c(2.0), c(5.0)]);
    assert_eq!(run_in(&mut interp, &n).unwrap(), 5.0);
    assert_eq!(interp.block(3).unwrap(), &[0.0, 0.0, 0.0, 9.0, 5.0]);
    // GetShifted(block=3, offset=1, index=1, stride=2): reads index 1 + 1*2 = 3.
    let n = f(Op::GetShifted, vec![c(3.0), c(1.0), c(1.0), c(2.0)]);
    assert_eq!(run_in(&mut interp, &n).unwrap(), 9.0);
    // SetShifted writes the same addressing.
    let n = f(Op::SetShifted, vec![c(3.0), c(1.0), c(1.0), c(2.0), c(8.5)]);
    assert_eq!(run_in(&mut interp, &n).unwrap(), 8.5);
    assert_eq!(interp.block(3).unwrap(), &[0.0, 0.0, 0.0, 8.5, 5.0]);
}

#[test]
fn increments() {
    let mut interp = Interpreter::new(0);
    interp.set_block(0, vec![5.0]);
    // Post returns the old value, pre returns the new one.
    let n = f(Op::IncrementPost, vec![c(0.0), c(0.0)]);
    assert_eq!(run_in(&mut interp, &n).unwrap(), 5.0);
    assert_eq!(interp.block(0).unwrap(), &[6.0]);
    let n = f(Op::IncrementPre, vec![c(0.0), c(0.0)]);
    assert_eq!(run_in(&mut interp, &n).unwrap(), 7.0);
    assert_eq!(interp.block(0).unwrap(), &[7.0]);
    // Pointed variants dereference a (block, index) pair plus offset.
    interp.set_block(1, vec![4.0, 1.0]);
    interp.set_block(4, vec![0.0, 10.0, 20.0]);
    let n = f(Op::IncrementPostPointed, vec![c(1.0), c(0.0), c(1.0)]);
    assert_eq!(run_in(&mut interp, &n).unwrap(), 20.0);
    assert_eq!(interp.block(4).unwrap(), &[0.0, 10.0, 21.0]);
    let n = f(Op::IncrementPrePointed, vec![c(1.0), c(0.0), c(0.0)]);
    assert_eq!(run_in(&mut interp, &n).unwrap(), 11.0);
    // Shifted variants use offset + index * stride.
    let n = f(
        Op::IncrementPostShifted,
        vec![c(4.0), c(0.0), c(1.0), c(2.0)],
    );
    assert_eq!(run_in(&mut interp, &n).unwrap(), 21.0);
    assert_eq!(interp.block(4).unwrap(), &[0.0, 11.0, 22.0]);
    let n = f(
        Op::IncrementPreShifted,
        vec![c(4.0), c(2.0), c(0.0), c(5.0)],
    );
    assert_eq!(run_in(&mut interp, &n).unwrap(), 23.0);
}

#[test]
fn debug_ops() {
    let mut interp = Interpreter::new(0);
    let n = f(
        Op::Execute,
        vec![
            f(Op::DebugLog, vec![c(1.5)]),
            f(Op::DebugLog, vec![c(-2.0)]),
            c(3.0),
        ],
    );
    assert_eq!(run_in(&mut interp, &n).unwrap(), 3.0);
    assert_eq!(interp.log(), &[1.5, -2.0]);
    // DebugPause returns 0.0 and does not evaluate its arguments.
    let n = f(Op::DebugPause, vec![f(Op::DebugLog, vec![c(9.0)])]);
    assert_eq!(run_in(&mut interp, &n).unwrap(), 0.0);
    assert_eq!(interp.log(), &[1.5, -2.0]);
}

#[test]
fn unsupported_op_message_matches_legacy_format() {
    // Op is a StrEnum in Python, so the legacy message interpolates the bare name.
    assert_err(
        f(Op::Draw, vec![]),
        InterpreterErrorKind::NotImplemented,
        "Unsupported operation: Draw",
    );
    assert_err(
        f(Op::SpawnParticleEffect, vec![]),
        InterpreterErrorKind::NotImplemented,
        "Unsupported operation: SpawnParticleEffect",
    );
}

// -----------------------------------------------------------------------------------
// Counters
// -----------------------------------------------------------------------------------

#[test]
fn eval_counter_counts_every_node_evaluation_including_consts() {
    let mut interp = Interpreter::new(0);
    run_in(&mut interp, &c(5.0)).unwrap();
    assert_eq!(interp.eval_count(), 1);
    run_in(&mut interp, &f(Op::Add, vec![c(1.0), c(2.0)])).unwrap();
    assert_eq!(interp.eval_count(), 4); // accumulates: 1 + 3
    let mut interp = Interpreter::new(0);
    // If evaluates the node, the test, and one branch: 3.
    run_in(&mut interp, &f(Op::If, vec![c(0.0), c(1.0), c(2.0)])).unwrap();
    assert_eq!(interp.eval_count(), 3);
    let mut interp = Interpreter::new(0);
    // And short-circuits: node + first arg.
    run_in(&mut interp, &f(Op::And, vec![c(0.0), c(1.0)])).unwrap();
    assert_eq!(interp.eval_count(), 2);
    let mut interp = Interpreter::new(0);
    // While with a false test: node + test.
    run_in(&mut interp, &f(Op::While, vec![c(0.0), c(1.0)])).unwrap();
    assert_eq!(interp.eval_count(), 2);
}

#[test]
fn dispatch_counter_counts_non_tail_jumploop_steps() {
    let mut interp = Interpreter::new(0);
    // 0 -> 1 -> tail: 2 dispatches; tail evaluation not counted.
    let n = f(Op::JumpLoop, vec![c(1.0), c(2.0), c(42.0)]);
    assert_eq!(run_in(&mut interp, &n).unwrap(), 42.0);
    assert_eq!(interp.dispatch_count(), 2);
    assert_eq!(interp.eval_count(), 4); // JumpLoop + two dispatchers + tail
    // Tail-only loop: no dispatches.
    let mut interp = Interpreter::new(0);
    run_in(&mut interp, &f(Op::JumpLoop, vec![c(7.0)])).unwrap();
    assert_eq!(interp.dispatch_count(), 0);
    // Jump out of range: one dispatch, no tail.
    let mut interp = Interpreter::new(0);
    run_in(&mut interp, &f(Op::JumpLoop, vec![c(-1.0), c(42.0)])).unwrap();
    assert_eq!(interp.dispatch_count(), 1);
    // Counters accumulate across runs.
    run_in(&mut interp, &f(Op::JumpLoop, vec![c(-1.0), c(42.0)])).unwrap();
    assert_eq!(interp.dispatch_count(), 2);
}

// -----------------------------------------------------------------------------------
// RNG
// -----------------------------------------------------------------------------------

#[test]
fn seeded_rng_is_deterministic() {
    let draw = |interp: &mut Interpreter| {
        (0..8)
            .map(|_| run_in(interp, &f(Op::Random, vec![c(0.0), c(1.0)])).unwrap())
            .collect::<Vec<f64>>()
    };
    let a = draw(&mut Interpreter::new(123));
    let b = draw(&mut Interpreter::new(123));
    let c_ = draw(&mut Interpreter::new(124));
    assert_eq!(a, b);
    assert_ne!(a, c_);
    for v in &a {
        assert!((0.0..1.0).contains(v));
    }
}

#[test]
fn random_uses_uniform_formula() {
    // uniform(lo, hi) = lo + (hi - lo) * random(); degenerate ranges collapse.
    let mut interp = Interpreter::new(7);
    for _ in 0..16 {
        let v = run_in(&mut interp, &f(Op::Random, vec![c(2.0), c(3.0)])).unwrap();
        assert!((2.0..3.0).contains(&v));
    }
    let v = run_in(&mut interp, &f(Op::Random, vec![c(5.0), c(5.0)])).unwrap();
    assert_eq!(v, 5.0);
    // Reversed bounds behave like Python uniform (hi < lo allowed).
    let v = run_in(&mut interp, &f(Op::Random, vec![c(3.0), c(2.0)])).unwrap();
    assert!((2.0..=3.0).contains(&v));
}

#[test]
fn random_integer_is_randrange() {
    let mut interp = Interpreter::new(99);
    let mut seen = std::collections::HashSet::new();
    for _ in 0..64 {
        let v = run_in(&mut interp, &f(Op::RandomInteger, vec![c(-3.0), c(-1.0)])).unwrap();
        assert!(v == -3.0 || v == -2.0, "randrange(-3, -1) produced {v}");
        seen.insert(v.to_bits());
    }
    assert_eq!(seen.len(), 2, "both values of the range should appear");
    // Bounds must be integral (ensure_int with the legacy message).
    assert_err(
        f(Op::RandomInteger, vec![c(1.5), c(3.0)]),
        InterpreterErrorKind::Assertion,
        "Value must be an integer",
    );
    // Empty ranges use the CPython message.
    assert_err(
        f(Op::RandomInteger, vec![c(5.0), c(5.0)]),
        InterpreterErrorKind::Value,
        "empty range in randrange(5, 5)",
    );
    assert_err(
        f(Op::RandomInteger, vec![c(5.0), c(4.0)]),
        InterpreterErrorKind::Value,
        "empty range in randrange(5, 4)",
    );
}

#[test]
fn rng_tape_mode_replays_and_errors_on_exhaustion() {
    let mut interp = Interpreter::with_tape(vec![0.25, 7.0]);
    // Tape values are returned verbatim, regardless of the requested range.
    let v = run_in(&mut interp, &f(Op::Random, vec![c(10.0), c(20.0)])).unwrap();
    assert_eq!(v, 0.25);
    let v = run_in(&mut interp, &f(Op::RandomInteger, vec![c(0.0), c(100.0)])).unwrap();
    assert_eq!(v, 7.0);
    let err = run_in(&mut interp, &f(Op::Random, vec![c(0.0), c(1.0)])).unwrap_err();
    assert_eq!(err.kind, InterpreterErrorKind::Runtime);
    assert_eq!(err.message, "RNG tape exhausted");
    // Argument validation still applies in tape mode.
    let mut interp = Interpreter::with_tape(vec![1.0]);
    let err = run_in(&mut interp, &f(Op::RandomInteger, vec![c(5.0), c(5.0)])).unwrap_err();
    assert_eq!(err.message, "empty range in randrange(5, 5)");
    // set_rng_tape switches an existing interpreter into tape mode.
    let mut interp = Interpreter::new(0);
    interp.set_rng_tape(vec![0.5]);
    let v = run_in(&mut interp, &f(Op::Random, vec![c(0.0), c(1.0)])).unwrap();
    assert_eq!(v, 0.5);
}

// -----------------------------------------------------------------------------------
// Numeric kernels used directly (shared with future constant folding)
// -----------------------------------------------------------------------------------

#[test]
fn ensure_int_kernel() {
    use sonolus_backend_core::interpret::ensure_int;
    assert_eq!(ensure_int(5.0).unwrap(), 5.0);
    assert_eq!(ensure_int(-3.0).unwrap(), -3.0);
    assert_eq!(ensure_int(0.0).unwrap(), 0.0);
    assert_eq!(ensure_int(-0.0).unwrap(), -0.0);
    assert_eq!(ensure_int(1e300).unwrap(), 1e300); // integral, like Python
    let err = ensure_int(1.5).unwrap_err();
    assert_eq!(err.kind, InterpreterErrorKind::Assertion);
    assert_eq!(err.message, "Value must be an integer");
    let err = ensure_int(f64::NAN).unwrap_err();
    assert_eq!(err.kind, InterpreterErrorKind::Value);
    assert_eq!(err.message, "cannot convert float NaN to integer");
    let err = ensure_int(f64::INFINITY).unwrap_err();
    assert_eq!(err.kind, InterpreterErrorKind::Overflow);
    assert_eq!(err.message, "cannot convert float infinity to integer");
}
