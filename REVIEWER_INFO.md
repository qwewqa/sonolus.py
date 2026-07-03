# Reviewer's guide to the Cython optimizer

This is an orientation for reviewing `sonolus/backend/_opt/` — the compiled Cython
optimizer core that replaced the old pure-Python `sonolus/backend/optimize/` passes.
It explains how to approach the code and, more importantly, the invariants and
hazards that are easy to break. `OPTIMIZER_REWRITE.md` is the original design plan;
`sonolus/backend/_opt/ir.pxd` is the load-bearing data-layout contract — read that
first, then this.

## The big picture

One `Func` **arena** per callback holds the whole IR as flat C arrays of integer
ids (instructions, edges, blocks, places, consts, temps). Passes operate on those
arrays; there is no per-node Python object and no global mutable state. Python is
touched only at the two marshal boundaries (in/out) and in emit, always with the
GIL held. Builds are **serial** (the old per-callback thread pool was removed in
M9); the `nogil` annotations on the passes are kept only to document that the C
code is GIL-independent.

## Module map (`sonolus/backend/_opt/`)

- **ir** — the arena `Func`, marshal in/out (Python `BasicBlock` ⇄ arena), `verify()`,
  const/place/temp interning, export. The boundary layer.
- **analysis** — dominators, liveness, the shared bit-set helpers.
- **midend** — `cfg_cleanup` (CFG simplification), SSA construction (`build_ssa`,
  Braun et al.), SCCP, GVN, DCE, LICM, `rewrite_switch`, and out-of-SSA.
- **lower** — `lower_from_ssa` (out-of-SSA + treeify + coalesce + `normalize_switch`),
  `if_convert`, `allocate_func` (bump / try-bump / packing), `fuse_rmw`.
- **emit** — arena `Func` → `EngineNode` tree (Python objects; stays GIL-bound).
- **driver** — level dispatch, the `_pipeline`, the per-mode `compile_mode` work loop,
  the debug phase registry.
- **kernels** — the pure constant-fold kernels (one per runtime op).

The Python shim `sonolus/backend/optimize/__init__.py` exposes `run_passes` /
`optimize_and_finalize` / `cfg_to_engine_node`; `sonolus/build/` drives whole-engine
builds (`engine.py`, `compile.py`, `cli.py`).

## The pipeline (per optimization level)

```
marshal_in → cfg_cleanup → build_ssa → midend → [if_convert] → lower_from_ssa → allocate → emit
```

- **minimal (-O0):** `cfg_cleanup → bump allocation` (mid-end bypassed).
- **fast (-O1):** one `midend_round` (SCCP → GVN/simplify → DCE), no LICM/switch/if-conv,
  try-bump allocation.
- **standard (-O2):** `midend_standard` (core + LICM + `rewrite_switch`) → `if_convert`
  (over SSA, while phis exist) → `lower_from_ssa` → packing allocation → `fuse_rmw`.

Each stage returns a fresh arena. `SONOLUS_OPT_TRACE=1` dumps the CFG after each pass.

## Invariants a reviewer must check

These are the things most likely to be silently broken:

- **Runtime is 32-bit float.** Any pass that *synthesizes* runtime arithmetic (chiefly
  switch normalization, which emits `(test-off)/stride`) must keep synthesized
  integers and spans inside the f32 exact-integer range `[-2^24, 2^24]` — *not* the
  f64 `2^53`. The differential oracle (`interpret.py`) computes in f64 and **cannot**
  see an f32 miscompile; validate these guards with `numpy.float32`, not the oracle.
- **Op semantics match the Sonolus runtime, not Python/JS defaults.** `Rem` takes the
  sign of the dividend; `Sign` is JS `Math.sign`; `IncrementPre/Post` return the
  old/new value; `Set*`/`Increment*` fused ops are emitted for their side effect only.
- **Cost model: every node counts.** The metrics gate measures *effective node count*
  (`tests/regressions/data/baseline_v0.16_metrics.json`); a "simplification" that adds
  nodes is a regression. Never edit the baseline or thresholds to pass.
- **LLP64: C `long` is 32-bit here.** Use `int32_t`/`int64_t`/`size_t` explicitly.
  Index/count products that can exceed 2^31 must widen to 64-bit before multiplying.
  A `double → int` cast of NaN/inf/out-of-range is UB — range-check first.
- **Passes never raise on degenerate arithmetic** (div/mod by zero, inf/NaN): fold to
  the IEEE result or decline; do not throw.
- **`verify()` mirrors emit's equality** (−0.0 ≠ +0.0 by bit pattern; NaN canonicalized).
  Parallel edges between a block pair are legal.
- **phi operand ↔ edge order.** A phi's operand *k* corresponds to the *k*-th incoming
  edge in ascending global edge-index order. `build_ssa`, `_export_phis`, and
  `out_of_ssa` all rely on exactly this order.
- **Determinism.** Output must be byte-identical across runs. `unordered_map`/khash/set
  iteration order is **unspecified** — use them for lookups only; anywhere output order
  depends on a container, use a `vector` or sort. `test_project_build_is_deterministic`
  and the byte-exact `_cfg` goldens catch violations.

## How to build, test, and validate

- Build (dev, Python 3.14): `uv sync --reinstall-package sonolus.py`. Cross-version
  (mirrors CI): `uv run tox` (py312/py313/py314) — run before pushing build changes.
- Debug build: `SONOLUS_OPT_DEBUG_BUILD=1 uv sync` keeps Cython bounds checks and C
  `assert`s so the internal `verify()` invariants fire.
- Fast suite: `uv run pytest tests -n 32 -q`. Backend only: `tests/backend`.
- **The safety nets, by strength:**
  - `tests/regressions/test_project.py` — full pydori build, **byte-exact `_cfg`
    goldens**, determinism, and the metrics gate. The strongest signal for any codegen
    or CFG-shape change. Regenerate goldens only for *intentional* changes and report them.
  - `tests/backend/test_random_cfg.py` — randomized differential CFGs against the oracle.
    The best net for CFG-transform correctness (e.g. cfg_cleanup).
  - `tests/backend/test_interpret_oracle.py` + `interpret.py` — the reference interpreter
    the differential tests run against (f64; see the f32 caveat above).
- Measurement: `SONOLUS_OPT_PROFILE=1` (or CLI `--profile` / `--profile-json`) breaks a
  build down per stage; `tools/bench_compile.py` times full builds; `tools/metrics.py`
  reports effective node counts.

## M9 notes (what recently changed)

- **Serial builds.** The thread pool is gone; `compile_mode` traces, optimizes, and
  emits each callback in one fixed order (deterministic by construction).
- **khash for hot int-keyed maps.** Vendored `khash.h` (MIT) + `khash_shim.h` +
  `_khash.pxd`. Const interning (`Func._const_intern`) is a `kh_i64i32_t*` keyed on the
  f64 bit pattern. khash tables are lookup-only (bucket order is unspecified).
- **cfg_cleanup uses per-head edge adjacency.** `_Cleaner` keeps append-only out/in
  adjacency lists so each transform iterates a head's own edges instead of rescanning
  all `n_e` edges — the old scans were O(nb·ne) and quadratic on the large CFGs
  marshal-in emits. Entries are never removed; consumers re-check `e_alive` and
  `e_src`/`e_dst == h` to skip stale entries left by kills/redirects.
- **Profiling shows the frontend now dominates.** For a pydori standard build, Python
  AST tracing (`callback_to_cfg`) is ~75% of wall time; the optimizer core is a small
  fraction. Compile-time work should be prioritized with real `SONOLUS_OPT_PROFILE`
  numbers, not assumptions about which pass is hot.
