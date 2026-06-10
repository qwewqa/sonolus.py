# Rust Backend Architecture

Reference design for the Rust backend replacing `sonolus/backend/` and the heavy parts of
`sonolus/build/`. This document is stable design context; live execution state lives in
[PORT.md](PORT.md). Read both before working on any task.

## 1. Boundary and data flow

The frontend (`sonolus/script/`) stays in Python: it traces user callbacks into CFGs of IR
statements via `Context` / `context_to_cfg`, and gathers metadata (archetype tables, ROM,
buckets/skin/effects/particles, configuration). Everything downstream moves to Rust.

```
Python (frontend)                          Rust (backend)
─────────────────                          ──────────────
trace callbacks → CFG + metadata           build_engine(payload) -> PackagedEngine bytes
  (parallel under free-threaded 3.13+)       ├─ decode CFGs into arena IR
encode each CFG to flat binary               ├─ stateless dedup of identical CFGs
gather mode metadata as JSON strings         ├─ optimize each callback (rayon, GIL released)
ROM values as f64 array                      ├─ lower + emit JumpLoop node tree
        │                                    ├─ dedup node DAG (canonical order)
        └── ONE FFI call ─────────────►      ├─ assemble mode JSON, gzip (mtime=0)
                                             └─ return six byte blobs

tests: frontend CFG ──► Rust pipeline + Rust Interpreter (seeded)
collection: Python orchestrates (user converter callbacks, URL fetch);
            Rust does zip/sha1/gzip/JSON/file-tree work
```

`build_engine` is a **pure function**: no state survives the call. There is no compile
cache (removed by design — see PORT.md decision D6); identical CFGs within one call are
deduplicated statelessly by hashing their encoded bytes.

### Crates

- `sonolus-backend-core` — pure Rust, no PyO3. IR, pipeline, emitter, interpreter,
  packaging, collection. All differential/fuzz/corpus tests live here and run offline.
- `sonolus-backend-py` — thin PyO3 bindings: the coarse `build_engine` call, collection
  primitives, and fine-grained test handles (`run_pipeline`, `cfg_to_engine_node`,
  `Interpreter`, debug dumps).

## 2. FFI payload and CFG encoding

IR crosses the boundary in a versioned flat binary format (spec: `ENCODING.md`, written in
task T0.4). It only needs to represent **frontend-level** constructs — the frontend never
emits SSA, so `SSAPlace`/phis exist purely inside Rust and are not encoded:

- string table (TempBlock names), constants with an **int/float tag** (the distinction is
  load-bearing in output JSON and dumps: `5` vs `5.0`)
- blocks: statement stream, test expr, outgoing edges `(cond: tagged number | none, target)`
- expressions prefix-encoded with op ids; tagged unions for places:
  `BlockPlace{block: enum-int | int | TempBlock | nested expr, index: int | expr, offset: int}`,
  `TempBlock{name, size}`

The `Op` enum (~190 entries with `pure`/`side_effects`/`control_flow` flags) is
code-generated from `sonolus/backend/ops.py` with a sync test.

Mode metadata (archetype names/imports/exports/hasInput, callback names/orders, buckets,
skin/effects/particles/instructions, engine configuration) crosses as JSON strings — it is
small; only IR gets the binary format. ROM crosses as an f64 array.

## 3. IR design and contracts

Arena-based SSA mid-level IR built from the decoded CFG via Braun et al. on-the-fly SSA
construction. Analyses (dominator tree, loop forest, liveness) are computed on demand,
cached, and explicitly invalidated. The pass manager is a plain ordered pipeline — no
requires/preserves constraint solving.

**Hard IR contracts** (also listed as invariants in PORT.md):

1. **Mid-level IR is binary** with canonical operand ordering for commutative ops.
   Variadic (n-ary) forms exist only between the lowering stage and emission. No mid-level
   pass may depend on flattened form.
2. No recursion over user-sized structures — explicit work stacks everywhere (the Python
   pipeline needed `setrecursionlimit(10_000)`).
3. Insertion-order containers (`IndexMap`/`IndexSet`/`Vec`) anywhere ordering can reach
   output. Same input must always produce the same bytes, including under rayon.

## 4. Optimization pipeline

The target is a tree-walking interpreter over f64-only nodes with flat block memory.
Optimization goals, in priority order: **dynamic node-evaluation count** (every node has
interpretive overhead; the interpreter's eval counters are the metric), **static node
count / DAG size** (shipped engine data), **temporary-memory pressure** (4096 slots).

Runtime-specific facts that shape the design:

- `Block(JumpLoop(...))` is the **native CFG encoding** — computed dispatch on the block
  index. We keep it; there is no structured control-flow reconstruction. Wins come from
  reducing dispatcher round-trips, not eliminating the dispatcher.
- `SwitchIntegerWithDefault` / `SwitchInteger` are O(1) indexed dispatch; `If`-chains and
  `SwitchWithDefault` are linear. Manufacturing dense 0-based integer case sets is worth a
  dedicated pass.
- The op set has many fused forms (`Lerp`, `Remap`, `Clamp`, `GetShifted`, `SetPointed`,
  `SetAdd`, `IncrementPost`, ...) — instruction selection for them directly removes node
  evaluations.
- Variadic `Add`/`Multiply`/`And`/`Or`/`Execute` nodes are cheaper than nested binary
  trees — hence flattening, but only at emission.
- FP reassociation is licensed: the legacy backend already reassociated float math.

### Pipeline stages

```
decode → SSA construction (Braun)
  W1: SCCP (Python numeric semantics for folding)
      GVN + rewrite rules (canonicalization, algebraic simplification, strength reduction)
      ADCE + branch simplification
  W2: Mem2Reg/SROA for TempBlocks (constant-index → SSA scalars; dynamic-index stays memory)
      copy coalescing, allocation quality
  W3: switch formation (RewriteToSwitch successor, post-GVN, recognizes comparison trees)
      LICM; optional cost-modeled micro-unroll of tiny constant-trip loops
  W4: JumpLoop-aware CFG shaping: expression-level if-conversion of small diamonds into
      If/And/Or value-producing nodes; block merging; exit combining; tiny-block duplication
out-of-SSA: Boissinot phi elimination with coalescing;
            slot allocation by greedy coloring of the SSA interference graph (chordal ⇒ optimal)
  W5 (lowering, post-allocation):
      FlattenAssociativeOps (emission-time only; sharing-aware — may keep a nested form
        when the subtree is shared in the node DAG)
      NormalizeSwitch (rebase/scale scrutinee to dense 0-based cases) + dense-form selection
      fused-op tiling (greedy bottom-up tile matching); Execute0/Execute selection
emit: JumpLoop node tree → DAG dedup (canonical insertion order) → output nodes
```

**Optimization levels** are pipeline prefixes:
- `minimal` (-O0): SSA round-trip + allocation + emission only. Permanent role: the
  semantic baseline oracle for differential testing, and compiler debugging.
- `fast` (-O1): + W1. Kept as a triage/bisection tool; not a default anywhere.
- `standard` (-O2): everything. **Default for both `build` and `dev`.**

E-graph equality saturation (egg) over pure expression DAGs is an explicitly post-port
experiment behind a flag; it must beat the greedy rewriter on the metrics dashboard to be
promoted.

## 5. Interpreter

Rust port of `sonolus/backend/interpret.py`, exposed via PyO3 for the behavioral suite and
used internally as the differential-testing executor and profiler.

- `blocks: HashMap<i64, Vec<f64>>` (negative block ids are used by tests), default fill
  `-1.0`, index asserts `0..=65535` with the exact legacy messages.
- Seedable RNG (`Interpreter(seed=...)`); `Op.Random` → uniform, `Op.RandomInteger` →
  randrange. Tests draw the seed from Python's RNG once per invocation so equality checks
  across optimization levels hold.
- Instrumented: node-evaluation counter and JumpLoop-dispatch counter (the quality metrics).
- `Block`/`Break` unwinding, short-circuit `And`/`Or` returning the last evaluated value,
  all four switch forms, JumpLoop index-walk semantics — per the Python reference.

## 6. Numeric semantics (mandatory, everywhere)

Constant folding and interpretation must match Python/legacy semantics, not Rust defaults:

| Concern | Required behavior |
|---|---|
| `Mod` | Python floor-mod: sign follows divisor |
| `Round` | banker's rounding (half-to-even) |
| `Rem` | IEEE 754 remainder (`math.remainder`) |
| `Sign` | `copysign(1, x)` — `Sign(0.0) == 1.0`, `Sign(-0.0) == -1.0` |
| `Frac` | `x % 1`, adjusted into `[0, 1)` per the Python reference |
| folding errors | do not fold where Python raises `ValueError`/`ZeroDivisionError`/`OverflowError` |
| NaN/±inf constants | emitted as ROM reads: `EngineRom[0]`=NaN, `[1]`=+inf, `[2]`=−inf |
| int vs float | tag preserved end-to-end; integral floats emit as ints in node values |

Float formatting in Rust debug dumps uses Rust's shortest-roundtrip formatting (decision
D7): frontend CFG dumps remain Python-produced, and post-port optimizer/node goldens are
Rust-generated snapshots, so Python-`repr` compatibility is not required anywhere.

## 7. Packaging and collection

- Mode data: `serde_json` with `preserve_order`; gzip with mtime=0 (reproducible). Gzip
  bytes are not required to match Python's zlib output — content is the contract.
- ROM: little-endian f32 packing, gzip.
- Collection (`sonolus-backend-core::collection`): scp/zip loading, SHA1 content-addressed
  repository, resource gzip, item localization, level/engine linking, site-tree writing
  (skip-if-hash-exists preserved). Exposed to Python as a `Collection` class preserving the
  current API. Python keeps: orchestration, user level-converter callbacks, URL fetching
  (bytes handed to Rust).
- Level data JSON is built in Python (entity introspection); Rust packages it.

## 8. Verification strategy

The correctness contract (decision D2):

1. **Behavioral suite** (`tests/script/`) is the oracle — must pass at every optimization
   level. `uv run pytest tests -n 32`.
2. **Differential interpretation**: for every corpus CFG, `minimal` output vs optimized
   output on the Rust interpreter with identical seeds and randomized initial block memory;
   results, debug logs, and written memory must agree. Python-independent; runs in
   `cargo test` against `rust/testdata/`.
3. **CFG fuzzing**: proptest/arbitrary generator of well-formed CFGs (arithmetic, memory
   ops incl. dynamic indexing, loops, switches), shrinking on failure, checked
   minimal-vs-optimized. Time-boxed in PR CI; continuous nightly.
4. **Metrics ratchet**: per-callback static node count, DAG size, dynamic eval count,
   dispatch count, wall time — compared against the frozen Python baseline
   (`rust/baselines/`). Switchover requires aggregate ≥ parity with Python `standard` and
   no callback regressing >10% (tunable, ledger-tracked).

Goldens policy: frontend CFG dumps in `tests/regressions/data/` stay byte-exact and
read-only forever. Optimizer/node goldens are regenerated **exactly once** (task T6.2) as
Rust-backend snapshots in a dedicated reviewed PR, then become read-only change-detection.

## 9. Performance gates

Measured on `test_projects/pydori`, recorded in the ledger:

- **G-P1 (cold build)**: Rust backend wall time (decode + optimize `standard` + emit +
  package, excluding Python tracing) ≤ the legacy Python `FAST_PASSES` backend time it
  replaces as the dev default.
- **G-P2 (dev rebuild)**: total Rust backend wall time per rebuild ≤ Python frontend trace
  time for the same rebuild — the backend is provably not the bottleneck doing full
  re-optimization with no cache.

If G-P2 ever fails on a future larger project, the recorded remedy is stateless dedup /
caching *inside* the single call — never a resurrected cross-rebuild cache.

## 10. End state

Python backend deleted (`backend/optimize/`, `finalize.py`, `interpret.py`,
`build/node.py`; `flow.py` trimmed to what the frontend and encoder need; `compile.py` /
`engine.py` / `collection.py` reduced to trace-encode-call wrappers). Kept in Python:
`blocks.py`, `mode.py`, `ops.py`, `ir.py`, `place.py`, `excepthook.py`, `utils.py`.
maturin build backend; wheels for Windows/macOS/Linux × CPython 3.12–3.14 plus
free-threaded 3.13t/3.14t; sdist with Rust toolchain. `sonolus-py build`/`dev` work from a
wheel with no Rust toolchain installed. Dev defaults to standard passes. One release ships
the switch.
