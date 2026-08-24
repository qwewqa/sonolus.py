---
name: runtime-semantics
description: "Read when a change depends on what the real Sonolus runtime does rather than on what this repo does: adding or changing an Op's semantics, writing or auditing a fold kernel, reasoning about a numeric divergence between the compiled and interpreted paths, judging whether an optimization is safe, or estimating the runtime cost of emitted nodes. Covers the runtime's 32-bit floats and why the f64 oracle cannot catch an f32 miscompile, the exact semantics pinned for Rem, Mod, Round, Sign, Judge, Power, and the easings, and the node cost model behind effective_node_count."
---

# What the real runtime does

The Sonolus runtime is Unity and C#. When a question cannot be answered from this repo, that is the reference.
`sonolus.js` is a separate implementation and is **not** authoritative: do not resolve a semantic question by
reading it.

Inside the repo the chain of authority is explicit:

1. `sonolus/backend/interpret.py` is the oracle. Its `Interpreter` defines what every op it models means, memory
   reads and writes included. The host-side ops it does not model (`Draw*`, `Play*`, `Spawn`, `Print`, the particle
   ops) raise `NotImplementedError`.
2. The reference bodies it mirrors are the definitions users see: `internal/math_impls.py` for `Rem` and `Frac`,
   `easing.py` for the 36 `Ease*`, `bucket.py` for `Judge` and `JudgeSimple`.
3. `_opt/kernels.pyx` must be a literal transcription of 1 and 2, enforced differentially by
   `tests/backend/test_fold_kernels.py` and `tests/backend/test_interpret_oracle.py`.

So a semantic change touches `interpret.py`, the reference body if the op has one, and `kernels.pyx`. The
differential tests will tell you if you missed one.

One of the pinned semantics below is described in terms of JavaScript, because that is what the runtime
implements for that op. That is a statement about the JS *language*, not a licence to consult the `sonolus.js`
implementation.

## The runtime computes in 32-bit floats

Every value in a Sonolus engine is an f32. The oracle, the fold kernels, and the whole Python side are f64.

**The oracle cannot catch an f32-specific miscompile.** A transformation that is exact in f64 and lossy in f32
passes the entire test suite. This is an accepted, structural limitation, and the reason the guards below exist as
explicit range checks rather than as tests.

Guards currently in place:

- `kernels.pyx`'s `_f32_exact(v)`: `If` and the four `Switch*` selects fold only when the test and each examined
  key survives an f32 roundtrip, so compile-time f64 selection agrees exactly with 32-bit dispatch. NaN folds
  through, because truthiness and `==` key-miss agree in both widths.
- `lower.pyx`'s `_CASE_MAG_LIMIT` (2^24) and `_CASE_SPAN_LIMIT` (2^24): switch case normalization must keep both
  each case magnitude and the `test - offset` span inside the f32 exact-integer range `[-2^24, 2^24]`. Beyond it an
  int64 case rewrite would disagree with the f32 dispatch.

To check a guard, model the runtime with `numpy.float32`, as in
`np.float32(np.float32(case) - np.float32(off)) / np.float32(stride)`. Do not reach for the oracle: it evaluates
the same expression in f64, so it agrees with the optimizer even where the real runtime mis-dispatches. Declining
to transform is always f32-safe, so returning nothing is the right answer whenever a guard cannot be established.

If you add a transformation that turns one arithmetic form into another, ask whether it is exact in f32, not just
in f64. `numtools.py` has a `2**24` check of the same kind on the frontend side.

## Pinned op semantics

The ones that surprise people, all verified against the oracle:

- **`Rem`** is a truncated remainder taking the sign of the **dividend**: `copysign(abs(a) % abs(b), a)`. It is not
  `math.remainder`, and it yields `-0.0` for a negative dividend with a zero remainder. Python's `%` is a
  different op.
- **`Mod`** is Python's `%`, taking the sign of the **divisor**, with a zero remainder becoming
  `copysign(0.0, b)`. `Rem` and `Mod` are both real ops and are not interchangeable.
- **`Round`** is Python's `round`, so half-to-even (banker's rounding), not half-away-from-zero.
- **`Sign`** is JS `Math.sign`: `0`, `-0`, and NaN map to themselves, everything else to `+/-1`.
- **`Power`** is `float(base) ** float(exponent)`, deliberately going through the float machine rather than
  Python's exact integers. A negative base with a fractional exponent would be complex, so it does not fold.
- **`Judge`** takes `diff = source - target` and returns 1, 2, 3, or 0 for perfect, great, good, or miss, testing
  the three windows in that order with inclusive bounds. **`JudgeSimple`** is exactly `Judge` with symmetric
  windows: `(-max_perfect, max_perfect, -max_great, max_great, -max_good, max_good)`.
- **The 36 `Ease*` ops** each clamp their input to `[0, 1]` first, then follow the corresponding curve from
  easings.net. `easing.py` holds the definitions and `interpret.py` transcribes them.
- **`Remap`/`Unlerp`** do not clamp; `RemapClamped`/`UnlerpClamped` clamp the interpolant to `[0, 1]`, and NaN
  clamps to 1.0 by the `max(0, min(1, x))` ordering.

Domain errors do not fold: if the oracle would raise `ZeroDivisionError`, a `ValueError` from `Log`/`Arcsin`, an
`OverflowError` from `sinh`, or a `ceil`/`round` of inf or NaN, `fold_op` returns `FOLD_NOT_CONSTANT` instead of
trapping or inventing a value.

## The node cost model

What the runtime charges for, which is what `tools/metrics.py` and the metrics gate measure:

- The runtime compiles **block structure to bytecode** and folds only the expressions within. So the outer `Block`,
  the `JumpLoop`, each per-block `Execute`, and the terminators are skeleton nodes that never fold.
- Nodes are counted **per reference**, not per unique node. The emitter hash-conses, so the emitted tree is a DAG,
  but the real runtime re-executes a shared subtree once per reference. Deduplication in the emitted data is not a
  saving at runtime.
- `effective_node_count` counts every **maximal runtime-constant subtree as 1**, modelling the runtime's own
  constant folding. Runtime-constant means a constant-index read of a `RUNTIME_CONSTANT_BLOCKS` block that is not
  writable in the current callback. This is the number the gate compares, so deliberately duplicating a
  runtime-constant tree (which raises the raw count by design) does not read as a regression.

- `JumpLoop` is the CFG shape the runtime expects, and it fast-paths a block whose terminator is a constant, an
  `If` expression, or a `Switch` variant, with `SwitchInteger` and `SwitchIntegerWithDefault` the cheapest of the
  switches. Emitted terminators have to stay in those recognized forms to keep the fast path.

The practical consequence: an optimization that reduces unique nodes while increasing references is a regression,
and one that duplicates a runtime-constant expression costs nothing the gate measures, though it still grows the
emitted engine data. Materializing a runtime-constant subtree into a temp is worse on this cost model at any size:
a temp read is not runtime-constant, so the temp is both a barrier to the runtime's own folding and a wasted
write. Lowering duplicates by default for that reason, with one deliberate exception. Past `_RTC_DUP_BUDGET`
(`lower.pyx`) a tree materializes anyway, buying a bounded arena and bounded compile time at exactly the
effective-node cost above; trees whose duplicated size is no larger than a temp read's are exempt from that
budget, because there the temp saves nothing to trade.
