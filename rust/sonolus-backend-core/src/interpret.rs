//! Engine-node interpreter: a faithful, iterative port of
//! `sonolus/backend/interpret.py`.
//!
//! # Porting contract
//!
//! The legacy Python interpreter is the semantic oracle. Every operation matches it
//! observably: evaluation order (including interleaved `ensure_int` checks), memory
//! mutation order, short-circuiting, `Block`/`Break` unwinding, and Python numeric
//! semantics (see ARCHITECTURE.md §6). All runtime values are `f64`; the legacy
//! interpreter's incidental Python `int` values (e.g. `math.floor` results) have no
//! observable effect except where noted under *Divergences*.
//!
//! # No recursion
//!
//! `run` is an explicit work-stack machine (invariant §3.4): a `Vec<Frame>` of
//! in-progress function nodes plus a shared value stack. Node trees are user-sized and
//! can be hundreds of thousands of levels deep.
//!
//! # Instrumentation (definitions replicated by T2.4 metrics and `tools/metrics.py`)
//!
//! - **`eval_count`**: incremented exactly once per engine-node evaluation — equivalent
//!   to one `Interpreter.run()` call in the legacy Python interpreter. Constant nodes
//!   count. Re-evaluations count each time (loop bodies, `JumpLoop` dispatchers, switch
//!   cases, ...). Accumulates across `run` calls.
//! - **`dispatch_count`**: incremented once per `JumpLoop` index-walk step — each
//!   evaluation of a *non-tail* `JumpLoop` argument as a dispatcher (each
//!   `index = int(run(args[index]))` round trip in the legacy interpreter). Evaluating
//!   the final argument as the loop result (the tail) does not count. Accumulates
//!   across `run` calls.
//! - **`rng_draw_count`**: incremented once per successful `Random`/`RandomInteger`
//!   draw (seeded or tape mode). Draw-order/count preservation is part of the
//!   optimizer contract (same seed must yield identical draws at every optimization
//!   level), so the differential harness (`crate::diff`, T2.3) compares it.
//!
//! # Eval budget (differential testing, T2.3)
//!
//! [`Interpreter::set_eval_budget`] installs an optional cap on the cumulative
//! `eval_count`; exceeding it stops evaluation with the *distinct*
//! [`InterpreterErrorKind::EvalBudgetExceeded`] outcome (the run was cut off — that is
//! not a behavioral fact about the program). Unset, the cost is a single untaken
//! branch per node evaluation.
//!
//! # Runtime-only op stubbing (metrics only)
//!
//! [`Interpreter::set_stub_runtime_ops`] (default OFF) replaces the
//! `"Unsupported operation: ..."` failure for runtime-only ops with a deterministic
//! stub so METRICS runs can measure past the first `Draw`/`BeatToTime`/... The full
//! rule is documented in one place, on the setter. The behavioral suite and the
//! differential/fuzz harnesses (`crate::diff`) never enable it; disabled, behavior is
//! identical to an interpreter without the feature.
//!
//! # RNG
//!
//! The legacy interpreter uses Python's global Mersenne Twister; matching it is
//! explicitly not required. This port uses `SplitMix64` (Steele et al.), seeded via
//! [`Interpreter::new`]: tiny, deterministic, and statistically fine for differential
//! testing. `Op::Random` draws `lo + (hi - lo) * next_f64()` (the exact
//! `random.uniform` formula); `Op::RandomInteger` implements `random.randrange(lo, hi)`
//! semantics with Lemire's debiased bounded sampling. Alternatively, a *tape* of
//! pre-recorded draw results can be installed ([`Interpreter::with_tape`] /
//! [`Interpreter::set_rng_tape`]); each `Random`/`RandomInteger` evaluation then
//! returns the next tape value verbatim (after argument validation), and exhausting
//! the tape is an error. The T0.5 corpus vectors store such tapes
//! (`rust/testdata/README.md`).
//!
//! # Errors
//!
//! All user-reachable failures are [`InterpreterError`] values (no panics), with a
//! [`kind`](InterpreterErrorKind) mapping onto the Python exception type the legacy
//! interpreter raises and a message matching `CPython` 3.14 where the legacy message is
//! part of the contract. The four legacy assert messages are exact:
//! `"Index must be non-negative"`, `"Index is too large"`, `"Value must be an
//! integer"`, `"Count must be non-negative"`.
//!
//! # Divergences from the Python reference (all documented decisions)
//!
//! - **Complex powers**: Python `float ** float` with a negative base and non-integral
//!   exponent returns a *complex* number; here it is a `ValueError`-kind error.
//! - **Power overflow**: finite `x ** y` overflowing raises `OverflowError` in both,
//!   but Python's message is errno-derived (`(34, 'Result too large')`); here it is
//!   `"math range error"`.
//! - **`SwitchInteger` float scrutinees**: Python indexes `branches[test_result]`
//!   directly, raising `TypeError` when the value is a Python `float` (even an
//!   integral one) and working when it is an `int`. The distinction does not exist on
//!   f64; an in-range integral value always selects the branch. The legacy emitter
//!   never produces bare `SwitchInteger` (only `If`/`SwitchWithDefault`/
//!   `SwitchIntegerWithDefault`, see `finalize.py`), so the `TypeError` path is
//!   unreachable from real engine nodes.
//! - **Huge integers**: Python ints are arbitrary-precision; block ids and
//!   `randrange` bounds outside the `i64` range are errors here, and address
//!   arithmetic (`offset + index * stride`) rounds in f64 where Python computes
//!   exactly. Unreachable for well-formed nodes (memory indices are capped at 65535).
//! - **Malformed arity**: Python raises lazy `IndexError`/unpack `ValueError` part-way
//!   through evaluation for malformed argument counts; fixed-arity operations here
//!   check arity up front (except `Block`/`Break`, which match Python's access order
//!   exactly). Real emitted nodes always have correct arity.
//! - **Uncaught `Break`**: Python lets the `BreakException` escape `run`; here it is a
//!   `RuntimeError`-kind error.
//! - **Error message float formatting** uses Rust's shortest-roundtrip formatting and
//!   `NaN`/`inf` spellings inside message text (decision D7).

// Strict f64 comparisons are the ported Python semantics (`==`/`!=` on floats is
// exactly what the legacy interpreter does); margin-of-error comparisons would be
// incorrect here.
#![allow(clippy::float_cmp)]

use std::collections::HashMap;
use std::fmt;

use crate::nodes::{EngineNodes, NodeArena, NodeId, NodeKind};
use crate::ops::Op;

/// Classification of an [`InterpreterError`], mirroring the Python exception type the
/// legacy interpreter raises for the same condition.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InterpreterErrorKind {
    /// Python `AssertionError` (the four legacy interpreter asserts).
    Assertion,
    /// Python `ZeroDivisionError`.
    ZeroDivision,
    /// Python `ValueError`.
    Value,
    /// Python `OverflowError`.
    Overflow,
    /// Python `IndexError`.
    Index,
    /// Python `NotImplementedError` (unsupported op).
    NotImplemented,
    /// Conditions with no clean legacy equivalent (uncaught `Break`, RNG tape
    /// exhaustion, malformed nodes).
    Runtime,
    /// The optional eval budget ([`Interpreter::set_eval_budget`]) was
    /// exceeded. **Not a normal error**: it means "this run was cut off", not
    /// "this program misbehaved". Differential testing treats it as an
    /// inconclusive outcome, never as a behavioral fact.
    EvalBudgetExceeded,
}

/// An interpreter failure. See the module docs: all user-reachable failures are
/// values of this type, never panics.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InterpreterError {
    pub kind: InterpreterErrorKind,
    pub message: String,
}

impl InterpreterError {
    fn new(kind: InterpreterErrorKind, message: impl Into<String>) -> Self {
        Self {
            kind,
            message: message.into(),
        }
    }
}

impl fmt::Display for InterpreterError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for InterpreterError {}

type Result<T> = std::result::Result<T, InterpreterError>;

fn assertion(message: &str) -> InterpreterError {
    InterpreterError::new(InterpreterErrorKind::Assertion, message)
}

fn zero_division(message: &str) -> InterpreterError {
    InterpreterError::new(InterpreterErrorKind::ZeroDivision, message)
}

fn value_error(message: impl Into<String>) -> InterpreterError {
    InterpreterError::new(InterpreterErrorKind::Value, message)
}

fn overflow_error(message: &str) -> InterpreterError {
    InterpreterError::new(InterpreterErrorKind::Overflow, message)
}

fn runtime_error(message: impl Into<String>) -> InterpreterError {
    InterpreterError::new(InterpreterErrorKind::Runtime, message)
}

/// Formats a float for error message text. Rust-native formatting (decision D7),
/// except infinities use Python's `inf` spelling for familiarity.
fn fmt_float(x: f64) -> String {
    if x.is_infinite() {
        if x > 0.0 { "inf".into() } else { "-inf".into() }
    } else {
        format!("{x:?}")
    }
}

// ---------------------------------------------------------------------------------
// Python numeric semantics kernels (shared with constant folding in later tasks).
// Messages match CPython 3.14.
// ---------------------------------------------------------------------------------

/// Python `int(x)` for floats: truncation toward zero; NaN/inf are errors.
/// No integrality requirement (used by `JumpLoop`). The `+ 0.0` normalizes `-0.0`
/// to `+0.0`: Python's `int()` returns an `int`, which has no negative zero.
pub fn py_int_trunc(x: f64) -> Result<f64> {
    if x.is_nan() {
        return Err(value_error("cannot convert float NaN to integer"));
    }
    if x.is_infinite() {
        return Err(overflow_error("cannot convert float infinity to integer"));
    }
    Ok(x.trunc() + 0.0)
}

/// The legacy `Interpreter.ensure_int`: `assert value == int(value)`. NaN/inf fail
/// inside `int()` (`ValueError`/`OverflowError`); finite non-integral values fail the
/// assert with the exact legacy message.
pub fn ensure_int(value: f64) -> Result<f64> {
    let truncated = py_int_trunc(value)?;
    if truncated != value {
        return Err(assertion("Value must be an integer"));
    }
    Ok(value)
}

/// Python `/` (`operator.truediv`): division by zero is an error even for floats.
pub fn py_div(a: f64, b: f64) -> Result<f64> {
    if b == 0.0 {
        return Err(zero_division("division by zero"));
    }
    Ok(a / b)
}

/// Python `%` (`operator.mod`) floor-mod on floats: sign follows the divisor, zero
/// results take the divisor's sign, mod-by-zero is an error. Ported from `CPython`
/// `float_rem`.
pub fn py_mod(a: f64, b: f64) -> Result<f64> {
    if b == 0.0 {
        return Err(zero_division("division by zero"));
    }
    let mut m = a % b;
    if m == 0.0 {
        m = 0.0f64.copysign(b);
    } else if (b < 0.0) != (m < 0.0) {
        // NaN reaches here (NaN != 0.0) and stays NaN, like CPython.
        m += b;
    }
    Ok(m)
}

fn is_odd_integer(x: f64) -> bool {
    (x.abs() % 2.0) == 1.0
}

/// Python `**` (`operator.pow`) on floats. Ported from `CPython` `float_pow`, including
/// every special case. Divergences (documented in the module docs): negative base with
/// non-integral exponent is an error instead of a complex number, and the overflow
/// message is `"math range error"`.
pub fn py_pow(x: f64, y: f64) -> Result<f64> {
    if y == 0.0 {
        return Ok(1.0); // x**0 is 1, even for NaN x.
    }
    if x.is_nan() {
        return Ok(x);
    }
    if y.is_nan() {
        return Ok(if x == 1.0 { 1.0 } else { y });
    }
    if y.is_infinite() {
        let ax = x.abs();
        return Ok(if ax == 1.0 {
            1.0
        } else if (y > 0.0) == (ax > 1.0) {
            f64::INFINITY
        } else {
            0.0
        });
    }
    if x.is_infinite() {
        let odd = is_odd_integer(y);
        return Ok(if y > 0.0 {
            if odd { x } else { x.abs() }
        } else if odd {
            0.0f64.copysign(x)
        } else {
            0.0
        });
    }
    if x == 0.0 {
        if y < 0.0 {
            return Err(zero_division("zero to a negative power"));
        }
        // Preserves -0.0 for odd integral exponents, like CPython.
        return Ok(if is_odd_integer(y) { x } else { 0.0 });
    }
    let mut base = x;
    let mut negate = false;
    if x < 0.0 {
        if y != y.floor() {
            return Err(value_error(
                "negative number cannot be raised to a fractional power (Python returns \
                 a complex number; complex results are unsupported)",
            ));
        }
        base = -x;
        negate = is_odd_integer(y);
    }
    if base == 1.0 {
        return Ok(if negate { -1.0 } else { 1.0 });
    }
    let result = base.powf(y);
    if result.is_infinite() {
        // Finite operands overflowed: CPython raises OverflowError (ERANGE).
        return Err(overflow_error("math range error"));
    }
    Ok(if negate { -result } else { result })
}

/// `math.remainder`: IEEE 754 remainder. Ported from `CPython` `m_remainder` (exact,
/// fmod-based). `remainder(x, 0)` and `remainder(inf, y)` are `ValueError`s.
#[allow(clippy::similar_names, clippy::many_single_char_names)] // names mirror CPython
pub fn py_remainder(x: f64, y: f64) -> Result<f64> {
    if x.is_finite() && y.is_finite() {
        if y == 0.0 {
            return Err(value_error("math domain error"));
        }
        let absx = x.abs();
        let absy = y.abs();
        let m = absx % absy;
        let c = absy - m;
        let r = if m < c {
            m
        } else if m > c {
            -c
        } else {
            // Tie: round the quotient to even. Computed exactly per CPython.
            m - 2.0 * ((0.5 * (absx - m)) % absy)
        };
        return Ok(1.0f64.copysign(x) * r);
    }
    if x.is_nan() {
        return Ok(x);
    }
    if y.is_nan() {
        return Ok(y);
    }
    if x.is_infinite() {
        return Err(value_error("math domain error"));
    }
    Ok(x) // x finite, y infinite.
}

/// Python `round(x)` (one argument): banker's rounding; NaN/inf error like `int()`.
/// `+ 0.0` normalizes `-0.0` (Python returns an `int`, observable via `Sign`).
pub fn py_round(x: f64) -> Result<f64> {
    py_int_trunc(x)?; // NaN/inf checks with the exact messages.
    Ok(x.round_ties_even() + 0.0)
}

/// `math.ceil` on floats: NaN/inf error and no `-0.0` (Python returns an `int`).
pub fn py_ceil(x: f64) -> Result<f64> {
    py_int_trunc(x)?;
    Ok(x.ceil() + 0.0)
}

/// `math.floor` on floats: NaN/inf error and no `-0.0` (Python returns an `int`).
pub fn py_floor(x: f64) -> Result<f64> {
    py_int_trunc(x)?;
    Ok(x.floor() + 0.0)
}

/// `math.trunc` on floats: NaN/inf error and no `-0.0` (Python returns an `int`).
pub fn py_trunc(x: f64) -> Result<f64> {
    py_int_trunc(x)
}

/// `math.log` (natural log): NaN passes through, non-positive inputs are errors.
pub fn py_log(x: f64) -> Result<f64> {
    if x.is_nan() {
        return Ok(x);
    }
    if x > 0.0 {
        return Ok(x.ln());
    }
    Err(value_error(format!(
        "expected a positive input, got {}",
        fmt_float(x)
    )))
}

/// `math.acos`: NaN passes through, out-of-range inputs are errors.
pub fn py_acos(x: f64) -> Result<f64> {
    if x.is_nan() {
        return Ok(x);
    }
    if (-1.0..=1.0).contains(&x) {
        return Ok(x.acos());
    }
    Err(value_error(format!(
        "expected a number in range from -1 up to 1, got {}",
        fmt_float(x)
    )))
}

/// `math.asin`: NaN passes through, out-of-range inputs are errors.
pub fn py_asin(x: f64) -> Result<f64> {
    if x.is_nan() {
        return Ok(x);
    }
    if (-1.0..=1.0).contains(&x) {
        return Ok(x.asin());
    }
    Err(value_error(format!(
        "expected a number in range from -1 up to 1, got {}",
        fmt_float(x)
    )))
}

/// `math.sin`/`cos`/`tan` share `CPython`'s domain rule: infinite inputs are errors,
/// NaN passes through. `pub(crate)`: shared with SCCP constant folding (T3.1).
pub(crate) fn py_trig(x: f64, f: fn(f64) -> f64) -> Result<f64> {
    if x.is_infinite() {
        return Err(value_error(format!(
            "expected a finite input, got {}",
            fmt_float(x)
        )));
    }
    Ok(f(x))
}

/// `math.sinh`/`cosh` overflow rule: a finite input producing an infinite result is an
/// `OverflowError` (`"math range error"`); infinite inputs pass through.
/// `pub(crate)`: shared with SCCP constant folding (T3.1).
pub(crate) fn py_overflowing(x: f64, f: fn(f64) -> f64) -> Result<f64> {
    let result = f(x);
    if result.is_infinite() && x.is_finite() {
        return Err(overflow_error("math range error"));
    }
    Ok(result)
}

/// `math.sin` (see [`py_trig`]'s domain rule): infinite inputs error, NaN passes.
/// `pub` for constant folding (T3.1/T3.2): folds must share the interpreter's
/// exact kernels.
pub fn py_sin(x: f64) -> Result<f64> {
    py_trig(x, f64::sin)
}

/// `math.cos` (see [`py_sin`]).
pub fn py_cos(x: f64) -> Result<f64> {
    py_trig(x, f64::cos)
}

/// `math.tan` (see [`py_sin`]).
pub fn py_tan(x: f64) -> Result<f64> {
    py_trig(x, f64::tan)
}

/// `math.sinh` (see [`py_overflowing`]'s overflow rule). `pub` for constant
/// folding.
pub fn py_sinh(x: f64) -> Result<f64> {
    py_overflowing(x, f64::sinh)
}

/// `math.cosh` (see [`py_sinh`]).
pub fn py_cosh(x: f64) -> Result<f64> {
    py_overflowing(x, f64::cosh)
}

/// Python `min(a, b)`: returns `a` unless `b < a`. NOT `f64::min` — NaN handling is
/// position-dependent (`min(nan, x) == nan`, `min(x, nan) == x`) and `-0.0`/`0.0`
/// keep the first value, exactly like Python.
pub fn py_min(a: f64, b: f64) -> f64 {
    if b < a { b } else { a }
}

/// Python `max(a, b)`: returns `a` unless `b > a`. See [`py_min`] for NaN/±0 notes.
pub fn py_max(a: f64, b: f64) -> f64 {
    if b > a { b } else { a }
}

/// Python `max(0, min(1, s))`, the clamping used by `LerpClamped`/`RemapClamped`/
/// `UnlerpClamped`. NaN clamps to 1.0 (Python: `min(1, nan)` keeps 1).
/// `pub(crate)`: shared with SCCP constant folding (T3.1).
pub(crate) fn clamp01(s: f64) -> f64 {
    py_max(0.0, py_min(1.0, s))
}

fn truth(b: bool) -> f64 {
    if b { 1.0 } else { 0.0 }
}

/// Converts an integral f64 to i64, erroring outside the representable range
/// (divergence: Python ints are arbitrary-precision; see module docs).
fn int_to_i64(x: f64, what: &str) -> Result<i64> {
    const MIN: f64 = -9_223_372_036_854_775_808.0; // -2^63
    const MAX_EXCL: f64 = 9_223_372_036_854_775_808.0; // 2^63
    if (MIN..MAX_EXCL).contains(&x) {
        #[allow(clippy::cast_possible_truncation)]
        Ok(x as i64)
    } else {
        Err(value_error(format!(
            "{what} {} is out of the supported integer range",
            fmt_float(x)
        )))
    }
}

// ---------------------------------------------------------------------------------
// RNG
// ---------------------------------------------------------------------------------

/// `SplitMix64` (Steele, Lea, Flood 2014). Deterministic test RNG; deliberately not
/// Python's Mersenne Twister (see module docs).
#[derive(Debug, Clone)]
struct SplitMix64 {
    state: u64,
}

impl SplitMix64 {
    fn new(seed: u64) -> Self {
        Self { state: seed }
    }

    fn next_u64(&mut self) -> u64 {
        self.state = self.state.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = self.state;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    }

    /// Uniform in [0, 1) with 53 random bits, like `CPython`'s `random_random`.
    #[allow(clippy::cast_precision_loss)]
    fn next_f64(&mut self) -> f64 {
        (self.next_u64() >> 11) as f64 * (1.0 / (1u64 << 53) as f64)
    }

    /// Uniform in [0, n) via Lemire's debiased multiply-shift.
    fn below(&mut self, n: u64) -> u64 {
        debug_assert!(n > 0);
        let mut m = u128::from(self.next_u64()) * u128::from(n);
        #[allow(clippy::cast_possible_truncation)]
        let mut lo = m as u64;
        if lo < n {
            let threshold = n.wrapping_neg() % n;
            while lo < threshold {
                m = u128::from(self.next_u64()) * u128::from(n);
                #[allow(clippy::cast_possible_truncation)]
                {
                    lo = m as u64;
                }
            }
        }
        (m >> 64) as u64
    }
}

#[derive(Debug, Clone)]
enum Rng {
    Seeded(SplitMix64),
    Tape { values: Vec<f64>, pos: usize },
}

// ---------------------------------------------------------------------------------
// Interpreter
// ---------------------------------------------------------------------------------

/// The engine-node interpreter. See the module docs for the full contract.
#[derive(Debug, Clone)]
pub struct Interpreter {
    blocks: HashMap<i64, Vec<f64>>,
    log: Vec<f64>,
    rng: Rng,
    eval_count: u64,
    dispatch_count: u64,
    /// RNG draw counter: one increment per successful `Random`/`RandomInteger`
    /// draw (seeded or tape). Draw-order/count preservation is part of the
    /// optimizer contract, so differential testing compares this.
    rng_draw_count: u64,
    /// Optional eval budget (`None` = unlimited). When `eval_count` exceeds
    /// it, evaluation stops with [`InterpreterErrorKind::EvalBudgetExceeded`].
    eval_budget: Option<u64>,
    /// Metrics-only runtime-op stubbing (default off). See
    /// [`set_stub_runtime_ops`](Self::set_stub_runtime_ops) for the rule.
    stub_runtime_ops: bool,
    /// Last-write-wins write log, when recording is enabled (mirrors the
    /// `RecordingInterpreter` used by the T0.5 corpus capture).
    writes: Option<HashMap<(i64, i64), f64>>,
}

impl Default for Interpreter {
    fn default() -> Self {
        Self::new(0)
    }
}

/// One in-progress function-node evaluation on the explicit work stack.
#[derive(Debug)]
struct Frame {
    op: Op,
    node: NodeId,
    /// Op-specific step counter (see `step`).
    state: u32,
    /// Op-specific index: argument position, switch pair index, ...
    idx: usize,
    /// Op-specific scratch value: `Execute`/`And`/`Or` result, switch scrutinee,
    /// `Break` level.
    acc: f64,
    /// Base of this frame's region of the shared value stack.
    base: usize,
}

/// What the machine does next.
#[derive(Debug)]
enum Action {
    /// Evaluate this node; its value will be delivered to the top frame.
    Eval(NodeId),
    /// A value is ready for the frame below (or is the final result).
    Return(f64),
    /// The top frame finished with this value: pop it, then `Return`.
    CompleteTop(f64),
    /// A `Break` is unwinding (`BreakException(n, value)`).
    Break { n: f64, value: f64 },
}

impl Interpreter {
    /// Creates an interpreter with a seeded RNG.
    pub fn new(seed: u64) -> Self {
        Self {
            blocks: HashMap::new(),
            log: Vec::new(),
            rng: Rng::Seeded(SplitMix64::new(seed)),
            eval_count: 0,
            dispatch_count: 0,
            rng_draw_count: 0,
            eval_budget: None,
            stub_runtime_ops: false,
            writes: None,
        }
    }

    /// Creates an interpreter in RNG tape mode (see the module docs).
    pub fn with_tape(values: Vec<f64>) -> Self {
        let mut interpreter = Self::new(0);
        interpreter.set_rng_tape(values);
        interpreter
    }

    /// Switches the RNG to tape mode: each subsequent `Random`/`RandomInteger`
    /// evaluation returns the next value; exhaustion is an error.
    pub fn set_rng_tape(&mut self, values: Vec<f64>) {
        self.rng = Rng::Tape { values, pos: 0 };
    }

    /// Replaces the contents of a block (legacy `interpreter.blocks[id] = values`).
    pub fn set_block(&mut self, id: i64, values: Vec<f64>) {
        self.blocks.insert(id, values);
    }

    /// The current contents of a block, if it exists.
    pub fn block(&self, id: i64) -> Option<&[f64]> {
        self.blocks.get(&id).map(Vec::as_slice)
    }

    /// Ids of all existing blocks, sorted (deterministic output).
    pub fn block_ids(&self) -> Vec<i64> {
        let mut ids: Vec<i64> = self.blocks.keys().copied().collect();
        ids.sort_unstable();
        ids
    }

    /// The debug log (`Op::DebugLog` values, in order).
    pub fn log(&self) -> &[f64] {
        &self.log
    }

    /// Node-evaluation counter. See the module docs for the exact definition.
    pub fn eval_count(&self) -> u64 {
        self.eval_count
    }

    /// `JumpLoop` dispatch counter. See the module docs for the exact definition.
    pub fn dispatch_count(&self) -> u64 {
        self.dispatch_count
    }

    /// RNG draw counter: one increment per successful `Random`/`RandomInteger`
    /// draw, in both seeded and tape modes. Accumulates across runs.
    pub fn rng_draw_count(&self) -> u64 {
        self.rng_draw_count
    }

    /// Sets (or clears, with `None`) the eval budget: once the cumulative
    /// [`eval_count`](Self::eval_count) exceeds `budget`, evaluation stops with
    /// an [`InterpreterErrorKind::EvalBudgetExceeded`] error — a distinct
    /// outcome, not a behavioral error (the program was cut off, it did not
    /// misbehave). The budget compares against the *cumulative* counter, which
    /// accumulates across `run` calls. Unset (the default) costs one untaken
    /// branch per evaluation.
    pub fn set_eval_budget(&mut self, budget: Option<u64>) {
        self.eval_budget = budget;
    }

    /// Enables (or disables) **runtime-only op stubbing** — an opt-in mode for
    /// METRICS runs only (PORT.md §8 decision; `tools/metrics.py
    /// --stub-runtime-ops`). Default OFF; the behavioral suite and the
    /// differential/fuzz harnesses never enable it, and disabled behavior is
    /// identical to an interpreter without the feature.
    ///
    /// **The stub rule** (this is the single place it is defined): the affected
    /// ops are *exactly* the set the interpreter otherwise rejects with the
    /// [`NotImplemented`](InterpreterErrorKind::NotImplemented)-kind error
    /// `"Unsupported operation: <name>"` — every op that is neither a
    /// control-flow form handled by `step`, a `reduce_args` op
    /// ([`reduce_fold`]), nor a fixed-arity table op ([`simple_arity`]). Those
    /// are the runtime-only ops the engine host implements (`Draw`, `Play`,
    /// `Spawn`, `BeatToTime`, `ExportValue`, the `Ease*` family, ...). With
    /// stubbing enabled, such an op instead:
    ///
    /// 1. evaluates **all** of its arguments, left to right, exactly like
    ///    `Execute` — argument side effects (memory writes, `DebugLog`, RNG
    ///    draws) and counter increments land normally, and an argument error
    ///    (including the eval budget) interrupts evaluation at the same point
    ///    it would anywhere else; then
    /// 2. produces the deterministic result `0.0`.
    ///
    /// `Random`/`RandomInteger` are NOT runtime-only — they are implemented
    /// with the seeded RNG and are unaffected by this mode.
    pub fn set_stub_runtime_ops(&mut self, enabled: bool) {
        self.stub_runtime_ops = enabled;
    }

    /// The legacy mutating `get`: validates `block`/`index`, extends the block with
    /// `-1.0` up to `index`, and returns the value. Assert messages are exact.
    pub fn get(&mut self, block: f64, index: f64) -> Result<f64> {
        let (block, index) = self.locate(block, index)?;
        Ok(self.blocks.entry(block).or_default()[index])
    }

    /// The legacy `set`: validates like `get`, writes, and returns `value`.
    pub fn set(&mut self, block: f64, index: f64, value: f64) -> Result<f64> {
        let (block, index) = self.locate(block, index)?;
        self.blocks.entry(block).or_default()[index] = value;
        if let Some(writes) = &mut self.writes {
            #[allow(clippy::cast_possible_wrap)]
            writes.insert((block, index as i64), value);
        }
        Ok(value)
    }

    /// Enables write recording: every successful `set` (from any op) is recorded
    /// last-write-wins per `(block, index)`, like the corpus capture's
    /// `RecordingInterpreter`. Off by default (zero overhead).
    pub fn record_writes(&mut self) {
        self.writes = Some(HashMap::new());
    }

    /// The recorded writes as a sorted `(block, index, value)` list (deterministic
    /// output), or `None` if recording was never enabled.
    pub fn recorded_writes(&self) -> Option<Vec<(i64, i64, f64)>> {
        let writes = self.writes.as_ref()?;
        let mut out: Vec<(i64, i64, f64)> = writes
            .iter()
            .map(|(&(block, index), &value)| (block, index, value))
            .collect();
        out.sort_unstable_by_key(|&(block, index, _)| (block, index));
        Some(out)
    }

    /// Shared `get`/`set` validation and `-1.0` extension. Check order matches the
    /// legacy interpreter: `ensure_int(block)`, `ensure_int(index)`, then the two
    /// index asserts.
    fn locate(&mut self, block: f64, index: f64) -> Result<(i64, usize)> {
        let block = ensure_int(block)?;
        let index = ensure_int(index)?;
        if index < 0.0 {
            return Err(assertion("Index must be non-negative"));
        }
        if index > 65535.0 {
            return Err(assertion("Index is too large"));
        }
        let block = int_to_i64(block, "block id")?;
        #[allow(clippy::cast_possible_truncation, clippy::cast_sign_loss)]
        let index = index as usize;
        let values = self.blocks.entry(block).or_default();
        if values.len() <= index {
            values.resize(index + 1, -1.0);
        }
        Ok((block, index))
    }

    fn draw_uniform(&mut self, lo: f64, hi: f64) -> Result<f64> {
        let value = match &mut self.rng {
            Rng::Seeded(rng) => lo + (hi - lo) * rng.next_f64(),
            Rng::Tape { values, pos } => Self::tape_next(values, pos)?,
        };
        self.rng_draw_count += 1;
        Ok(value)
    }

    /// `random.randrange(lo, hi)`: arguments already integral; empty ranges error
    /// with the `CPython` message.
    #[allow(clippy::cast_precision_loss, clippy::cast_possible_truncation)]
    fn draw_randrange(&mut self, lo: f64, hi: f64) -> Result<f64> {
        let lo_i = int_to_i64(lo, "randrange bound")?;
        let hi_i = int_to_i64(hi, "randrange bound")?;
        if hi_i <= lo_i {
            return Err(value_error(format!(
                "empty range in randrange({lo_i}, {hi_i})"
            )));
        }
        let value = match &mut self.rng {
            Rng::Seeded(rng) => {
                let width = u64::try_from(i128::from(hi_i) - i128::from(lo_i))
                    .expect("hi > lo, so the width is positive and fits u64");
                let offset = rng.below(width);
                (i128::from(lo_i) + i128::from(offset)) as f64
            }
            Rng::Tape { values, pos } => Self::tape_next(values, pos)?,
        };
        self.rng_draw_count += 1;
        Ok(value)
    }

    fn tape_next(values: &[f64], pos: &mut usize) -> Result<f64> {
        if *pos < values.len() {
            let v = values[*pos];
            *pos += 1;
            Ok(v)
        } else {
            Err(runtime_error("RNG tape exhausted"))
        }
    }

    /// Runs a complete node tree.
    pub fn run(&mut self, nodes: &EngineNodes) -> Result<f64> {
        self.run_node(&nodes.arena, nodes.root)
    }

    /// Runs `root`. Iterative work-stack machine; see the module docs.
    pub fn run_node(&mut self, arena: &NodeArena, root: NodeId) -> Result<f64> {
        let mut frames: Vec<Frame> = Vec::new();
        let mut vals: Vec<f64> = Vec::new();
        let mut action = Action::Eval(root);
        loop {
            action = match action {
                Action::Eval(id) => {
                    self.eval_count += 1;
                    if let Some(budget) = self.eval_budget
                        && self.eval_count > budget
                    {
                        return Err(InterpreterError::new(
                            InterpreterErrorKind::EvalBudgetExceeded,
                            format!("eval budget exceeded ({budget} node evaluations)"),
                        ));
                    }
                    match arena.kind(id) {
                        NodeKind::Const { value, .. } => Action::Return(value),
                        NodeKind::Func { op, .. } => {
                            frames.push(Frame {
                                op,
                                node: id,
                                state: 0,
                                idx: 0,
                                acc: 0.0,
                                base: vals.len(),
                            });
                            let top = frames.len() - 1;
                            self.step(arena, &mut frames[top], &mut vals, None)?
                        }
                    }
                }
                Action::Return(value) => {
                    let Some(top) = frames.len().checked_sub(1) else {
                        return Ok(value);
                    };
                    self.step(arena, &mut frames[top], &mut vals, Some(value))?
                }
                Action::CompleteTop(value) => {
                    let frame = frames.pop().expect("CompleteTop with no frame");
                    vals.truncate(frame.base);
                    Action::Return(value)
                }
                Action::Break { mut n, value } => loop {
                    let Some(frame) = frames.pop() else {
                        return Err(runtime_error(
                            "uncaught Break outside of any Block during engine node \
                             evaluation",
                        ));
                    };
                    vals.truncate(frame.base);
                    if frame.op == Op::Block {
                        if n > 1.0 {
                            n -= 1.0; // `e.n -= 1; raise` — keep unwinding.
                        } else {
                            break Action::Return(value);
                        }
                    }
                },
            };
        }
    }

    /// Advances the top frame: `incoming` is `None` on frame entry, otherwise the
    /// value of the child evaluation this frame most recently requested.
    ///
    /// Frame `state` conventions are per-op and commented inline. Every path either
    /// requests a child evaluation (`Eval`), finishes the frame (`CompleteTop`),
    /// starts Break unwinding, or errors.
    #[allow(clippy::too_many_lines, clippy::cast_precision_loss)]
    fn step(
        &mut self,
        arena: &NodeArena,
        frame: &mut Frame,
        vals: &mut Vec<f64>,
        incoming: Option<f64>,
    ) -> Result<Action> {
        let op = frame.op;
        let args = arena.args_of(frame.node);
        let action = match op {
            // result = 0.0; for arg in args: result = run(arg); return result
            // (Execute0 returns 0.0 instead).
            Op::Execute | Op::Execute0 => match incoming {
                None => {
                    if args.is_empty() {
                        Action::CompleteTop(0.0)
                    } else {
                        Action::Eval(args[0])
                    }
                }
                Some(value) => {
                    frame.acc = value;
                    frame.idx += 1;
                    if frame.idx == args.len() {
                        Action::CompleteTop(if op == Op::Execute { frame.acc } else { 0.0 })
                    } else {
                        Action::Eval(args[frame.idx])
                    }
                }
            },
            // test, t_branch, f_branch = args; run(t if run(test) != 0 else f)
            Op::If => match (frame.state, incoming) {
                (0, None) => {
                    check_exact_arity(op, args, 3)?;
                    frame.state = 1;
                    Action::Eval(args[0])
                }
                (1, Some(test)) => {
                    frame.state = 2;
                    // `run(test) != 0.0` selects the true branch (NaN is truthy).
                    Action::Eval(if test == 0.0 { args[2] } else { args[1] })
                }
                (_, Some(value)) => Action::CompleteTop(value),
                _ => unreachable!("If frame protocol violated"),
            },
            // test, *branches[, default]: evaluate cases in order, run the branch of
            // the first case equal to the scrutinee. Odd pair lists IndexError at the
            // incomplete pair, exactly like Python's `branches[i + 1]`.
            Op::Switch | Op::SwitchWithDefault => {
                let has_default = op == Op::SwitchWithDefault;
                check_min_arity(op, args, if has_default { 2 } else { 1 })?;
                let branches_len = args.len() - if has_default { 2 } else { 1 };
                match (frame.state, incoming) {
                    (0, None) => {
                        frame.state = 1;
                        Action::Eval(args[0])
                    }
                    // state 1: scrutinee arrived; state 2: case value arrived.
                    (1 | 2, Some(value)) => {
                        if frame.state == 1 {
                            frame.acc = value;
                            frame.state = 2;
                        } else if frame.acc == value {
                            // Case matched: run its branch.
                            frame.state = 3;
                            let pair = 2 * frame.idx;
                            return Ok(Action::Eval(args[1 + pair + 1]));
                        } else {
                            frame.idx += 1;
                        }
                        let pair = 2 * frame.idx;
                        if pair >= branches_len {
                            // Cases exhausted.
                            if has_default {
                                frame.state = 3;
                                Action::Eval(args[args.len() - 1])
                            } else {
                                Action::CompleteTop(0.0)
                            }
                        } else if pair + 1 >= branches_len {
                            return Err(InterpreterError::new(
                                InterpreterErrorKind::Index,
                                "list index out of range",
                            ));
                        } else {
                            Action::Eval(args[1 + pair])
                        }
                    }
                    (_, Some(value)) => Action::CompleteTop(value),
                    _ => unreachable!("Switch frame protocol violated"),
                }
            }
            // test, *branches[, default]: O(1) integral indexing. See the module docs
            // for the float-scrutinee divergence.
            Op::SwitchInteger | Op::SwitchIntegerWithDefault => {
                let has_default = op == Op::SwitchIntegerWithDefault;
                check_min_arity(op, args, if has_default { 2 } else { 1 })?;
                let branches_len = args.len() - if has_default { 2 } else { 1 };
                match (frame.state, incoming) {
                    (0, None) => {
                        frame.state = 1;
                        Action::Eval(args[0])
                    }
                    (1, Some(test)) => {
                        frame.state = 2;
                        // `0 <= test_result < len(branches) and int(test_result) ==
                        // test_result` — NaN/inf fail the range check first, so the
                        // truncation is safe.
                        if test >= 0.0 && test < branches_len as f64 && test.trunc() == test {
                            #[allow(clippy::cast_possible_truncation, clippy::cast_sign_loss)]
                            Action::Eval(args[1 + test as usize])
                        } else if has_default {
                            Action::Eval(args[args.len() - 1])
                        } else {
                            Action::CompleteTop(0.0)
                        }
                    }
                    (_, Some(value)) => Action::CompleteTop(value),
                    _ => unreachable!("SwitchInteger frame protocol violated"),
                }
            }
            // while run(test) != 0: run(body); return 0
            Op::While => match (frame.state, incoming) {
                (0, None) => {
                    check_exact_arity(op, args, 2)?;
                    frame.state = 1;
                    Action::Eval(args[0])
                }
                (1, Some(test)) => {
                    if test == 0.0 {
                        Action::CompleteTop(0.0)
                    } else {
                        frame.state = 2;
                        Action::Eval(args[1])
                    }
                }
                (2, Some(_)) => {
                    frame.state = 1;
                    Action::Eval(args[0])
                }
                _ => unreachable!("While frame protocol violated"),
            },
            // loop { run(body); if run(test) == 0 break }; return 0
            Op::DoWhile => match (frame.state, incoming) {
                (0, None) => {
                    check_exact_arity(op, args, 2)?;
                    frame.state = 1;
                    Action::Eval(args[0])
                }
                (1, Some(_)) => {
                    frame.state = 2;
                    Action::Eval(args[1])
                }
                (2, Some(test)) => {
                    if test == 0.0 {
                        Action::CompleteTop(0.0)
                    } else {
                        frame.state = 1;
                        Action::Eval(args[0])
                    }
                }
                _ => unreachable!("DoWhile frame protocol violated"),
            },
            // Short-circuit; returns the last evaluated value (0.0 when empty).
            Op::And | Op::Or => match incoming {
                None => {
                    if args.is_empty() {
                        Action::CompleteTop(0.0)
                    } else {
                        Action::Eval(args[0])
                    }
                }
                Some(value) => {
                    frame.acc = value;
                    let stop = if op == Op::And {
                        value == 0.0
                    } else {
                        value != 0.0
                    };
                    frame.idx += 1;
                    if stop || frame.idx == args.len() {
                        Action::CompleteTop(frame.acc)
                    } else {
                        Action::Eval(args[frame.idx])
                    }
                }
            },
            // index = 0; while 0 <= index < len(args): if tail -> return run(tail);
            // index = int(run(args[index])); return 0.0
            // dispatch_count: +1 per non-tail dispatcher evaluation (see module docs).
            Op::JumpLoop => match (frame.state, incoming) {
                (0, None) => {
                    if args.is_empty() {
                        Action::CompleteTop(0.0)
                    } else if args.len() == 1 {
                        frame.state = 2; // index 0 is already the tail
                        Action::Eval(args[0])
                    } else {
                        self.dispatch_count += 1;
                        frame.state = 1;
                        Action::Eval(args[0])
                    }
                }
                (1, Some(value)) => {
                    // int() truncation toward zero; NaN/inf error like Python int(),
                    // so `index` is never NaN below.
                    let index = py_int_trunc(value)?;
                    if index < 0.0 || index >= args.len() as f64 {
                        Action::CompleteTop(0.0)
                    } else {
                        #[allow(clippy::cast_possible_truncation, clippy::cast_sign_loss)]
                        let i = index as usize;
                        if i == args.len() - 1 {
                            frame.state = 2;
                        } else {
                            self.dispatch_count += 1;
                        }
                        Action::Eval(args[i])
                    }
                }
                (2, Some(value)) => Action::CompleteTop(value),
                _ => unreachable!("JumpLoop frame protocol violated"),
            },
            // try: return run(args[0]) except Break — unwinding is handled centrally
            // in `run_node`.
            Op::Block => match incoming {
                None => {
                    if args.is_empty() {
                        return Err(InterpreterError::new(
                            InterpreterErrorKind::Index,
                            "list index out of range",
                        ));
                    }
                    Action::Eval(args[0])
                }
                Some(value) => Action::CompleteTop(value),
            },
            // raise BreakException(ensure_int(run(args[0])), run(args[1]))
            // Python evaluates args[0], ensure_ints it, *then* accesses args[1].
            Op::Break => match (frame.state, incoming) {
                (0, None) => {
                    if args.is_empty() {
                        return Err(InterpreterError::new(
                            InterpreterErrorKind::Index,
                            "list index out of range",
                        ));
                    }
                    frame.state = 1;
                    Action::Eval(args[0])
                }
                (1, Some(value)) => {
                    frame.acc = ensure_int(value)?;
                    if args.len() < 2 {
                        return Err(InterpreterError::new(
                            InterpreterErrorKind::Index,
                            "list index out of range",
                        ));
                    }
                    frame.state = 2;
                    Action::Eval(args[1])
                }
                (2, Some(value)) => Action::Break {
                    n: frame.acc,
                    value,
                },
                _ => unreachable!("Break frame protocol violated"),
            },
            // DebugPause returns 0.0 without evaluating its arguments.
            Op::DebugPause => Action::CompleteTop(0.0),
            // reduce_args: evaluate every argument first (the legacy generator is
            // fully consumed by unpacking), then fold left. Empty -> 0.0; a single
            // argument is returned unchanged (no operator application).
            _ => {
                if let Some(fold) = reduce_fold(op) {
                    match incoming {
                        None => {
                            if args.is_empty() {
                                Action::CompleteTop(0.0)
                            } else {
                                Action::Eval(args[0])
                            }
                        }
                        Some(value) => {
                            vals.push(value);
                            frame.idx += 1;
                            if frame.idx < args.len() {
                                Action::Eval(args[frame.idx])
                            } else {
                                let operands = &vals[frame.base..];
                                let mut acc = operands[0];
                                for &x in &operands[1..] {
                                    acc = fold(acc, x)?;
                                }
                                Action::CompleteTop(acc)
                            }
                        }
                    }
                } else if let Some((arity, int_args)) = simple_arity(op) {
                    // Collect `arity` argument values in order (ensure_int-ing each as
                    // it arrives when the legacy generator interleaves ensure_int),
                    // then apply.
                    match incoming {
                        None => {
                            check_exact_arity(op, args, arity)?;
                            Action::Eval(args[0])
                        }
                        Some(value) => {
                            let value = if int_args { ensure_int(value)? } else { value };
                            vals.push(value);
                            frame.idx += 1;
                            if frame.idx < arity {
                                Action::Eval(args[frame.idx])
                            } else {
                                let result = {
                                    let operands = &vals[frame.base..];
                                    self.apply_simple(op, operands)?
                                };
                                Action::CompleteTop(result)
                            }
                        }
                    }
                } else if self.stub_runtime_ops {
                    // Runtime-only op stub (metrics only): evaluate every argument in
                    // order — like `Execute`, the incoming values unused — then
                    // produce 0.0. The full rule is documented on
                    // `set_stub_runtime_ops`.
                    if incoming.is_some() {
                        frame.idx += 1;
                    }
                    if frame.idx == args.len() {
                        Action::CompleteTop(0.0)
                    } else {
                        Action::Eval(args[frame.idx])
                    }
                } else {
                    return Err(InterpreterError::new(
                        InterpreterErrorKind::NotImplemented,
                        format!("Unsupported operation: {}", op.name()),
                    ));
                }
            }
        };
        Ok(action)
    }

    /// Applies a fixed-arity operation to its collected argument values. `v` holds
    /// exactly the op's arity; for `int_args` ops every value is already integral.
    #[allow(clippy::too_many_lines)]
    fn apply_simple(&mut self, op: Op, v: &[f64]) -> Result<f64> {
        Ok(match op {
            Op::Abs => v[0].abs(),
            Op::Arccos => py_acos(v[0])?,
            Op::Arcsin => py_asin(v[0])?,
            Op::Arctan => v[0].atan(),
            Op::Arctan2 => v[0].atan2(v[1]),
            Op::Ceil => py_ceil(v[0])?,
            // max(a, min(b, x)) with Python min/max ordering — NaN propagation is
            // position-dependent; ported literally.
            Op::Clamp => py_max(v[1], py_min(v[2], v[0])),
            Op::Copy => {
                let (src_id, src_index, dst_id, dst_index, count) = (v[0], v[1], v[2], v[3], v[4]);
                if count < 0.0 {
                    return Err(assertion("Count must be non-negative"));
                }
                // Read everything first, then write: overlapping copies see the
                // original values, exactly like the legacy interpreter.
                // `get` caps indices at 65535, so huge counts fail fast.
                let mut values = Vec::new();
                let mut i = 0.0;
                while i < count {
                    values.push(self.get(src_id, src_index + i)?);
                    i += 1.0;
                }
                let mut i = 0.0;
                for value in values {
                    self.set(dst_id, dst_index + i, value)?;
                    i += 1.0;
                }
                0.0
            }
            Op::Cos => py_trig(v[0], f64::cos)?,
            Op::Cosh => py_overflowing(v[0], f64::cosh)?,
            Op::DebugLog => {
                self.log.push(v[0]);
                0.0
            }
            Op::Degree => v[0].to_degrees(),
            Op::Equal => truth(v[0] == v[1]),
            Op::Floor => py_floor(v[0])?,
            Op::Frac => {
                // x % 1 (floor-mod by 1 never divides by zero), then the literal
                // `result if result >= 0 else result + 1` adjustment.
                let result = py_mod(v[0], 1.0)?;
                if result >= 0.0 { result } else { result + 1.0 }
            }
            Op::Get => self.get(v[0], v[1])?,
            Op::GetPointed => {
                let block = self.get(v[0], v[1])?;
                let index = self.get(v[0], v[1] + 1.0)? + v[2];
                self.get(block, index)?
            }
            Op::GetShifted => self.get(v[0], v[1] + v[2] * v[3])?,
            Op::Greater => truth(v[0] > v[1]),
            Op::GreaterOr => truth(v[0] >= v[1]),
            Op::IncrementPost => {
                let value = self.get(v[0], v[1])?;
                self.set(v[0], v[1], value + 1.0)?;
                value
            }
            Op::IncrementPostPointed => {
                let deref_block = self.get(v[0], v[1])?;
                let deref_index = self.get(v[0], v[1] + 1.0)? + v[2];
                let value = self.get(deref_block, deref_index)?;
                self.set(deref_block, deref_index, value + 1.0)?;
                value
            }
            Op::IncrementPostShifted => {
                let index = v[1] + v[2] * v[3];
                let value = self.get(v[0], index)?;
                self.set(v[0], index, value + 1.0)?;
                value
            }
            Op::IncrementPre => {
                let value = self.get(v[0], v[1])? + 1.0;
                self.set(v[0], v[1], value)?;
                value
            }
            Op::IncrementPrePointed => {
                let deref_block = self.get(v[0], v[1])?;
                let deref_index = self.get(v[0], v[1] + 1.0)? + v[2];
                let value = self.get(deref_block, deref_index)? + 1.0;
                self.set(deref_block, deref_index, value)?;
                value
            }
            Op::IncrementPreShifted => {
                let index = v[1] + v[2] * v[3];
                let value = self.get(v[0], index)? + 1.0;
                self.set(v[0], index, value)?;
                value
            }
            Op::Lerp => v[0] + (v[1] - v[0]) * v[2],
            Op::LerpClamped => v[0] + (v[1] - v[0]) * clamp01(v[2]),
            Op::Less => truth(v[0] < v[1]),
            Op::LessOr => truth(v[0] <= v[1]),
            Op::Log => py_log(v[0])?,
            Op::Max => py_max(v[0], v[1]),
            Op::Min => py_min(v[0], v[1]),
            Op::Negate => -v[0],
            Op::Not => truth(v[0] == 0.0),
            Op::NotEqual => truth(v[0] != v[1]),
            Op::Radian => v[0].to_radians(),
            Op::Random => self.draw_uniform(v[0], v[1])?,
            Op::RandomInteger => self.draw_randrange(v[0], v[1])?,
            // to_min + (to_max - to_min) * (value - from_min) / (from_max - from_min)
            // with Python's left-to-right `* /` and ZeroDivisionError.
            Op::Remap => v[2] + py_div((v[3] - v[2]) * (v[4] - v[0]), v[1] - v[0])?,
            Op::RemapClamped => v[2] + (v[3] - v[2]) * clamp01(py_div(v[4] - v[0], v[1] - v[0])?),
            Op::Round => py_round(v[0])?,
            Op::Set => {
                let block = ensure_int(v[0])?;
                let index = ensure_int(v[1])?;
                self.set(block, index, v[2])?
            }
            Op::SetPointed => {
                let block = ensure_int(v[0])?;
                let index = ensure_int(v[1])?;
                let offset = ensure_int(v[2])?;
                let deref_block = self.get(block, index)?;
                let deref_index = self.get(block, index + 1.0)? + offset;
                self.set(deref_block, deref_index, v[3])?
            }
            Op::SetShifted => {
                let block = ensure_int(v[0])?;
                let offset = ensure_int(v[1])?;
                let index = ensure_int(v[2])?;
                let stride = ensure_int(v[3])?;
                self.set(block, offset + index * stride, v[4])?
            }
            Op::Sign => 1.0f64.copysign(v[0]),
            Op::Sin => py_trig(v[0], f64::sin)?,
            Op::Sinh => py_overflowing(v[0], f64::sinh)?,
            Op::Tan => py_trig(v[0], f64::tan)?,
            Op::Tanh => v[0].tanh(),
            Op::Trunc => py_trunc(v[0])?,
            Op::Unlerp => py_div(v[2] - v[0], v[1] - v[0])?,
            Op::UnlerpClamped => clamp01(py_div(v[2] - v[0], v[1] - v[0])?),
            _ => unreachable!("apply_simple called for non-simple op {}", op.name()),
        })
    }
}

/// Fixed-arity ops evaluated by collecting their argument values: `(arity,
/// ensure_int-each-argument)`. `int_args` ops mirror the legacy
/// `(self.ensure_int(self.run(arg)) for arg in args)` generators, where `ensure_int`
/// failures interrupt evaluation *before* later arguments run; the rest mirror
/// `(self.run(arg) for arg in args)`, with any `ensure_int` applied afterwards in
/// `apply_simple`.
fn simple_arity(op: Op) -> Option<(usize, bool)> {
    Some(match op {
        Op::Abs
        | Op::Arccos
        | Op::Arcsin
        | Op::Arctan
        | Op::Ceil
        | Op::Cos
        | Op::Cosh
        | Op::DebugLog
        | Op::Degree
        | Op::Floor
        | Op::Frac
        | Op::Log
        | Op::Negate
        | Op::Not
        | Op::Radian
        | Op::Round
        | Op::Sign
        | Op::Sin
        | Op::Sinh
        | Op::Tan
        | Op::Tanh
        | Op::Trunc => (1, false),
        Op::Arctan2
        | Op::Equal
        | Op::Greater
        | Op::GreaterOr
        | Op::Less
        | Op::LessOr
        | Op::Max
        | Op::Min
        | Op::NotEqual
        | Op::Random => (2, false),
        Op::Get | Op::IncrementPost | Op::IncrementPre | Op::RandomInteger => (2, true),
        Op::GetPointed | Op::IncrementPostPointed | Op::IncrementPrePointed => (3, true),
        Op::GetShifted | Op::IncrementPostShifted | Op::IncrementPreShifted => (4, true),
        Op::Copy => (5, true),
        Op::Clamp | Op::Lerp | Op::LerpClamped | Op::Set | Op::Unlerp | Op::UnlerpClamped => {
            (3, false)
        }
        Op::SetPointed => (4, false),
        Op::Remap | Op::RemapClamped | Op::SetShifted => (5, false),
        _ => return None,
    })
}

/// The `reduce_args` ops and their Python fold operators.
fn reduce_fold(op: Op) -> Option<fn(f64, f64) -> Result<f64>> {
    Some(match op {
        Op::Add => |a, b| Ok(a + b),
        Op::Subtract => |a, b| Ok(a - b),
        Op::Multiply => |a, b| Ok(a * b),
        Op::Divide => py_div,
        Op::Mod => py_mod,
        Op::Power => py_pow,
        Op::Rem => py_remainder,
        _ => return None,
    })
}

/// Arity check for ops where the legacy interpreter unpacks or fully indexes `args`.
/// Message text is Rust-specific (divergence: only malformed nodes reach this).
fn check_exact_arity(op: Op, args: &[NodeId], arity: usize) -> Result<()> {
    if args.len() == arity {
        Ok(())
    } else {
        Err(value_error(format!(
            "{} expects {arity} arguments, got {}",
            op.name(),
            args.len()
        )))
    }
}

fn check_min_arity(op: Op, args: &[NodeId], min: usize) -> Result<()> {
    if args.len() >= min {
        Ok(())
    } else {
        Err(value_error(format!(
            "{} expects at least {min} arguments, got {}",
            op.name(),
            args.len()
        )))
    }
}
