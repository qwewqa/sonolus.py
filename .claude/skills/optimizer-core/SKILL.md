---
name: optimizer-core
description: Read before editing anything under sonolus/backend/_opt (the Cython optimizer core), sonolus/backend/optimize, sonolus/backend/ops.py, or tools/gen_ops.py and tools/metrics.py. Covers the arena IR and its index-addressed invariants, what each optimization level runs, what the SONOLUS_OPT_DEBUG_BUILD build does and does not enable, the SONOLUS_OPT_TRACE and SONOLUS_OPT_PROFILE environment switches, what verify() checks and when it fires, running one named pass through debug_run, why the shim imports _opt lazily, regenerating the checked-in op tables, and the memory-management conventions in the nogil passes.
---

# The optimizer core

`sonolus/backend/_opt/` is the optimizer, written in Cython and compiled as C++. It publishes no docs, so its
docstrings and comments can be as internal as they need to be, and the `.pxd` files carry the load-bearing
contracts. Read the header docstring of the file you are changing first: `ir.pxd` has the IR design summary,
`midend.pyx` has the mid-end pass descriptions, `driver.pyx` has the pipeline, `kernels.pyx` has a contract block
you must read before touching a fold kernel.

## Shape of the thing

| File | Contents |
| --- | --- |
| `ir.pyx` / `ir.pxd` | The arena `Func`, marshal in and out, `verify()`, `debug_run` |
| `analysis.pyx` | Dominators, liveness, and the analyses the passes share |
| `midend.pyx` | `cfg_cleanup`, `build_ssa`, SCCP, GVN, DCE, LICM, `rewrite_switch`, `out_of_ssa` |
| `lower.pyx` | `lower_from_ssa`, if-conversion, the three allocators, `fuse_rmw` |
| `kernels.pyx` | Constant-fold kernels: C-double evaluation of every foldable op |
| `emit.pyx` | `EngineNode` emission |
| `driver.pyx` | Level dispatch, the pipeline, the debug phase registry |
| `_ops_gen.pxd` / `_ops_gen.h` | Generated static op-metadata table (do not hand-edit) |
| `khash.h` / `_khash.pxd` | Vendored klib khash, used for the int-keyed hot maps |

`sonolus/backend/optimize/__init__.py` is a thin Python shim exposing `run_passes`, `optimize_and_finalize`,
`cfg_to_engine_node`, and the three level sentinels. It imports the compiled modules **inside** the functions, not
at module top: `_opt.ir` imports `optimize.flow`, so a top-level import would form a cycle. Keep it that way.

Everything in the arena is index-addressed: a value *is* an instruction and its value-id is its index into
`instrs`; a block, place, const, temp, and edge are likewise indices. There is no global mutable state, and the
`nogil` annotations document GIL-independence rather than actual threading: builds are serial.

## The levels

From `driver.pyx`'s `_pipeline`, which is the authoritative description:

- **minimal (-O0)**: `cfg_cleanup` -> bump allocation. The mid-end is bypassed entirely.
- **fast (-O1)**: `cfg_cleanup` -> `build_ssa` -> `midend_round(allow_repeat=False)` -> `lower_from_ssa` ->
  try-bump allocation -> `fuse_rmw`. No LICM, `rewrite_switch`, or if-conversion: iteration speed over codegen
  quality.
- **standard (-O2)**: `cfg_cleanup` -> `build_ssa` -> `midend_standard` -> `if_convert` -> `lower_from_ssa` ->
  packing allocation -> `fuse_rmw`.

The test oracle runs all three, so a pass that is only correct at one level fails `tests/script/` broadly rather
than in one place.

## Rebuilding and the debug build

A plain `uv sync` recompiles after a `.pyx`, `.pxd`, or `.h` edit: see the Checks section of `CLAUDE.md`.

```
SONOLUS_OPT_DEBUG_BUILD=1 uv sync                     # bash: set for this command only
$env:SONOLUS_OPT_DEBUG_BUILD='1'; uv sync             # PowerShell: set for the whole session
$env:SONOLUS_OPT_DEBUG_BUILD=$null; uv sync           # PowerShell: back to release
```

What a debug build actually gives you is Cython's `boundscheck` and `wraparound`, so an out-of-range index inside a
pass traps instead of reading past an arena array. It also leaves `NDEBUG` undefined so C-level `assert()` fires,
but no `.pyx` source or vendored header currently uses one, so that half is dormant: bounds checking is the reason
to build debug today.

Every `.pyd` comes out 10 to 15 percent larger, which is the quick way to tell which build you have. The env var is
part of `[tool.uv] cache-keys`, so toggling it invalidates the cached extension by itself, and **unsetting it and
re-syncing is what gets you back to a release build**: leaving it set keeps you on the slower one silently.

`verify()` is *not* what the debug build buys you. Its checks are Python `assert` statements, which Cython compiles
to a runtime `__pyx_assertions_enabled()` test, so they fire in a release build too and go away only under
`python -O`. Build debug to catch out-of-range indexing inside a pass, not to turn `verify()` on.

`verify()` checks the arena invariants: block and arg slices in range, def-before-use in the linear stream,
dominance for SSA operands, phis only in SSA form, aux ranges per opcode, purity flags matching the static op
table, edge `src` consistency. It is wired into the debug entry points only: `debug_run`, the `run_*` phase
wrappers, and `analysis.pyx`'s `*_debug` helpers. The production pipeline never calls it, so a pass that corrupts
the arena surfaces as a wrong engine rather than as a failed assert.

## Watching a pass run

- `SONOLUS_OPT_TRACE=1` exports and prints the CFG after each pipeline stage, so you can watch the IR evolve.
- `SONOLUS_OPT_PROFILE=1` (or the CLI `--profile` flag) accumulates wall time per named stage: frontend tracing,
  marshal-in, each pass, emit. Call `profiling.reset()` before a build to measure just that build.
- `SONOLUS_VISIT_STATS=1` enables the frontend's per-function visit statistics.
- `ir.debug_run(cfg, phases=[...])` marshals in, runs the named phases in order with a `verify()` between each,
  and exports back. `driver.pyx` registers `cfg_cleanup`, `ssa`, `unssa`, `ifconv`, `lower`, `dominators`, and the
  allocators `bump`, `packing`, `try_bump`; `midend.pyx` adds `sccp`, `gvn`, `dce`, `licm`, `rewrite_switch`,
  `midend`, and `midend_standard`. All sixteen are always reachable, whichever module is imported first. This is
  how to isolate a single pass in a test.

## The op tables are generated

`sonolus/backend/ops.py` is the source of truth for the `Op` enum. `tools/gen_ops.py` reads it and regenerates two
checked-in files: `_ops_gen.pxd` (the `cdef` interface and a C enum giving every op a stable integer id in
declaration order, then the synthetic `OPX_*` opcodes) and `_ops_gen.h` (the actual `static const` arrays, which
are GIL-free to read from `nogil` code).

After adding or reordering an op:

```
python tools/gen_ops.py
uv sync
pytest tests/backend/test_ops_sync.py
```

`test_ops_sync.py` guards both directions: the checked-in sources against `ops.py`, and the *compiled* static
table against the enum. It keeps an independent copy of the expected foldable-op set so it cross-checks the
generator rather than merely re-running it.

Adding or reordering an op changes the emitted node trees, so expect it to invalidate goldens under
`tests/regressions/data/` and to move the metrics gate. Finish with the golden regeneration recipe and the gate,
both in the writing-tests skill.

## Fold kernels

`kernels.pyx` owns the numeric semantics SCCP folds with, and its rule is strict: each kernel is a **literal
transcription** of the oracle in `sonolus/backend/interpret.py` (and of `math_impls` for `Rem`/`Frac`, `easing.py`
for the 36 `Ease*`, `bucket.py` for `Judge`). `fold_op` must return `FOLD_NOT_CONSTANT` exactly when the oracle
would raise or produce a complex result, and otherwise reproduce the oracle's double bit for bit.
`tests/backend/test_fold_kernels.py` asserts that differentially over Hypothesis-generated operands. Read the
contract block at the top of the file before touching a kernel, and see the runtime-semantics skill for the f32
guards.

## Node counts

`tools/metrics.py` compiles every `pydori` callback and reports per-callback node counts, per-op counts, and a
timing split. `tests/regressions/test_metrics_gate.py` gates the aggregate `effective_node_count` against a
committed baseline and hard-fails on a regression: run it after any change on a compiled hot path. The details are
in the writing-tests and runtime-semantics skills.

`tools/bench_compile.py` is the compile-time benchmark. It discards a warmup build, then reports min, median, and
the spread of the timed ones, so the noise floor sits next to the number: a difference smaller than the spread is
nothing. `--warmup` and `--repeat` control both.

Operation counts settle what the stopwatch cannot. Instrument the paths you touched, count how often each runs on
a full pydori build, and multiply by a microbenchmark of the added work. That is the only way to size a change
below the noise floor, and the only way to say anything at all about code this corpus never reaches: a guard for a
shape `pydori` does not contain executes zero times, so a green benchmark says nothing about its cost. Give the
bound for the case where it does run rather than implying the measurement covered it.

## Memory management

Prefer RAII over manual memory when the performance is comparable: `libcpp` containers or the vendored khash over
hand-rolled `malloc`/`free`, because the maintenance cost of manual lifetime management in a pass file this size is
real. khash is the established choice for the int-keyed hot maps (the const-intern map, for example) and is a
validated pattern here. The extension is built as C++, so `libcpp` containers are available too, though no current
map uses one.

This is a preference, not a mandate to go converting. Leave a manual buffer that is already performant alone;
`_Cleaner`'s edge arrays in particular grow inside `nogil` regions, where `vector::push_back` does not fit because
it can throw. Rework a buffer when you are already changing it, not on a sweep.

khash bucket-iteration order is unspecified, so use a khash table for lookups only. Never produce output by
iterating one: collect into a vector and sort it. Byte-identical output is what the goldens and the metrics gate
compare, and hash order will not reproduce across runs. `_khash.pxd`'s module docstring carries the rest of the
contract: the iterator convention, the `ret` codes, and why the error-code API is what keeps the allocating paths
`noexcept`. Read it before adding a second table.
