# Rust Backend Port — Execution Ledger

> **This file is the single source of truth for the port.** Every work session starts by
> reading this file and [ARCHITECTURE.md](ARCHITECTURE.md), and ends by updating the task
> table and appending to the worklog. Design rationale lives in ARCHITECTURE.md; this file
> holds rules, tasks, state, and decisions. Maintainer notes (setup, end-of-run review)
> live in [EXECUTION.md](EXECUTION.md).

**Status:** in progress — S0 complete; next task: T1.1
**Last updated:** 2026-06-10

## 0. Entry point — if you were pointed at this file, start here

You are the port orchestrator, running the entire Rust backend port autonomously in one
continuous run. Read [ARCHITECTURE.md](ARCHITECTURE.md), then, starting from the ledger's
current state (task table, §7), execute every stage S0–S7 in order following the §2
protocol: work on the `rust-port` branch, dispatch subagents, verify everything yourself,
and apply the §4 policies when blocked — never wait for the user. If stale git worktrees
exist from an interrupted run, reconcile them first (a worktree branch with commits is an
unverified subagent result: rebase, verify, then mark accordingly). Re-read this file
after any context compaction. Finish by appending the completion report (§2.2) to this
file.

## 1. Mission and end state

Replace the Python compiler backend with a redesigned Rust backend in a single release.
Done means: Python backend deleted, Rust is the only backend, full CI (tests + wheel
matrix incl. free-threaded builds) and local dev support, behavioral test suite passing,
quality metrics ≥ legacy Python `standard` passes, `sonolus-py build`/`dev` working from a
wheel on a machine with no Rust toolchain, dev defaulting to standard optimization.

## 2. Execution protocol

The port runs as one continuous autonomous session: an **orchestrator** executes every
stage S0–S7 in order, dispatching implementation work to subagents and verifying it
itself, persisting across context compaction. All work lands on the `rust-port` branch
(never `master`); one task ≈ one commit; push the branch periodically for CI signal
(publishing is tag-gated, so branch pushes are release-safe). The single human trust
decision is the final merge review.

### 2.1 Per-task loop (subagent contract)

Each dispatched subagent:

1. Reads this file and ARCHITECTURE.md, then implements exactly its assigned task,
   obeying every invariant in §3.
2. Runs the task's DoD commands plus the standing commands (§5). All must pass.
3. Reports back: task ID, what was done, DoD command results verbatim, files touched,
   surprises/follow-ups. **Subagents never edit PORT.md** — the orchestrator owns the
   ledger.

### 2.2 Orchestrator rules

- **Own the ledger.** Update the task table and append a worklog entry after *every*
  task, so an interrupted run loses at most one task of state. Re-read PORT.md after any
  context compaction and before every dispatch decision.
- **Trust nothing unverified.** Before marking a task `done`, re-run the standing
  commands (§5) and the task's DoD yourself after the work is merged. A subagent's claim
  that tests pass is input, not evidence.
- **Dispatch**: sequential tasks one at a time in the main checkout. S3 wave tasks in
  parallel (2–3 subagents, worktree isolation); rebase results, resolve the pass-registry
  merge point, verify once on the combined tree. On verification failure, continue the
  same subagent with the failure details rather than spawning fresh.
- **Gates (G3.x, G-P1/G-P2) are never delegated** — run them yourself. A failed gate
  becomes a fix task, or a §4 policy application once its budget is spent.
- **Budgets**: at most 3 implementation/fix cycles per task, 3 fix-task cycles per gate.
  Budget exhausted → apply the §4 policy, record in the Deviation log, move on. Never
  silently retry forever.
- **Stuck ≠ abort**: if the critical path is blocked, work any available parallel track
  (e.g., S5) before stopping. Full abort only on the §4 hard-abort conditions.
- **Out of scope, always**: pushing/merging to `master`, tagging, flipping the publish
  workflow live. T7.2 prepares and CI-verifies the workflows; the maintainer flips/tags.
- **Finish** by appending the completion report to this file: outcome summary, final
  metrics tables, the full Deviation log with severity assessment, and the explicit list
  of remaining human actions (review, merge, tag, publish flip).

An interrupted run needs no special recovery: start a fresh orchestrator from the
ledger's current state (kickoff prompt in EXECUTION.md). A human may also execute any
single task directly by following §2.1 plus the ledger-update duty.

## 3. Invariants (hard rules — no exceptions without a §8 decision)

1. **Frontend CFG goldens are read-only forever** (`tests/regressions/data/*_cfg` produced
   pre-optimization). Optimizer/node goldens are regenerated exactly once, in T6.2, as a
   dedicated reviewed PR. No other golden writes, ever.
2. **The Python backend is frozen** (read-only) until deleted in S7, except corpus-capture
   instrumentation behind an env flag. Found a bug in it? Escalate; do not fix.
3. **Mid-level IR is binary** with canonical operand ordering. Variadic forms exist only
   between lowering and emission. No mid-level pass may rely on flattened form.
4. **No recursion over user-sized structures** in Rust — explicit work stacks.
5. **Determinism**: insertion-order containers anywhere ordering can reach output; same
   input → same output bytes, including under rayon.
6. **Numeric semantics** per ARCHITECTURE.md §6 (Python floor-mod, banker's round, IEEE
   remainder, copysign Sign, no-fold-on-Python-error, int/float tags, ROM NaN/inf).
7. **Every IR transform** must be covered by differential interpretation + fuzzing before
   its task closes.
8. `build_engine` stays a **pure function** — no state crosses FFI calls. No caches with
   cross-call lifetime.
9. No `unsafe` in `sonolus-backend-core` without a ledger decision.

## 4. Escalation triggers and autonomous policies

The run never waits on a human. When a trigger fires: apply the policy, record the event
in the Deviation log (§8), and continue — except where a hard abort is stated.

| Trigger | Autonomous policy (one-shot mode) |
|---|---|
| Temptation to modify goldens / frozen Python backend outside sanctioned tasks | **No fallback — never do it.** Route around or log-and-skip the task. |
| Frontend CFG goldens change under T6.2's regeneration | **Hard abort of that path**: this is a real bug, not a policy matter. Fix or stop the run. |
| Suspected bug in the Python backend (oracle) | The behavioral tests are the contract. Match observed behavior, do not "fix" the oracle; log the suspicion with a minimal repro for the maintainer. |
| Wave-gate metrics ratchet fails after budget (incl. G3.3) | Proceed flagged. The run lives on a branch; a quality miss ships nothing — the maintainer adjudicates at merge review with the metrics in hand. |
| Perf gates G-P1/G-P2 fail after budget | Apply the recorded fallback: keep `fast` as the dev default (one-line revert of T4.4's flip), file the offending pass as a follow-up, proceed. |
| Behavioral suite cannot be made green at some level after budget | **Hard abort condition** if it is the critical path (minimal/standard); log-and-skip only for non-default levels. |
| Anything touching publishing/credentials/tagging/`master` | Out of autonomous scope, categorically. Prepare and CI-verify only. |
| A DoD that cannot be made machine-checkable | Define the closest machine-checkable proxy, use it, log the substitution. |

**Hard-abort conditions** (stop the run entirely, leave the ledger consistent): frontend
goldens cannot be kept byte-identical; the behavioral suite cannot pass at `minimal` or
`standard` after budget; repository state corrupted beyond git recovery.

## 5. Standing commands (every task, in addition to its own DoD)

```
uv run pytest tests -n 32                 # from repo root (behavioral + build + regressions)
cargo test --workspace                    # from rust/
cargo clippy --workspace -- -D warnings   # from rust/
cargo fmt --check                         # from rust/
```

## 6. Stage map

```
S0 Foundations ─► S1 Interpreter + baseline backend ─► S2 Optimizer infra
   ─► S3 Optimizer waves W1..W5 (parallel fan-out; ratchet gate at W3)
   ─► S4 Single-call build ─► S5 Collection ─► S6 Test rewiring + golden regen
   ─► S7 Switchover & deletion
```

S5 depends only on S0 and may overlap late S3/S4 if an extra agent is available.

## 7. Task table

Status values: `todo` / `in-progress` / `blocked` / `done`.

### S0 — Foundations

| ID | Status | Task | DoD (beyond standing commands) |
|----|--------|------|-------------------------------|
| T0.1 | done | Cargo workspace `rust/` with `sonolus-backend-core` + `sonolus-backend-py` (PyO3); `maturin develop` flow into the uv venv; main `pyproject.toml` untouched (stays hatchling until S7). | `maturin develop` + `python -c "import sonolus_backend"` works locally (Windows) |
| T0.2 | done | Op codegen: Rust `Op` enum (name, pure, side_effects, control_flow) generated from `sonolus/backend/ops.py`; checked-in generated file + sync test. | sync test passes; deliberately desynced op fails it |
| T0.3 | done | CI stage A: `.github/workflows/rust.yml` — fmt, clippy `-D warnings`, `cargo test`, maturin develop + import smoke. Path-filtered. `publish.yaml` untouched. | green run on a PR |
| T0.4 | done | CFG encoding: `rust/ENCODING.md` spec (versioned; frontend-level constructs only — no SSA/phis), Python encoder `sonolus/backend/encode.py`, Rust decoder, Rust debug `cfg_to_text` (Rust float fmt). Round-trip validation is structural/bit-exact (e.g., hex-float canonical dumps on both sides), not repr-matching (decision D7). | round-trip test green over the mini-corpus and a full corpus capture run (corpus half completed under T0.5) |
| T0.5 | done | Corpus infra: `SONOLUS_CAPTURE_CORPUS=<dir>` pytest hook capturing frontend CFGs + behavioral I/O vectors; `tools/gen_corpus.py`; curated deterministic mini-corpus checked into `rust/testdata/` (~5MB budget, no hypothesis-derived cases). | capture run produces corpus; mini-corpus loads in `cargo test`; negative test (perturbed CFG) caught by round-trip check |

### S1 — Interpreter + baseline backend

| ID | Status | Task | DoD |
|----|--------|------|-----|
| T1.1 | todo | Interpreter port: seeded RNG, eval + JumpLoop-dispatch counters, `HashMap<i64,Vec<f64>>` blocks (negative ids), default `-1.0` fill, exact legacy assert messages, Block/Break unwinding, all switch forms. PyO3: `Interpreter(seed=)`, `set_block`, `get`, `run`, `log`, counters. | unit tests incl. numeric-semantics edge table (floor-mod, banker's round, remainder, Sign(±0)) |
| T1.2 | todo | Emitter: CFG → `Block(JumpLoop(...))` node tree (dense-switch selection per legacy `finalize.py` rules), node DAG dedup with canonical insertion order, int/float-tagged values, ROM NaN/inf, `format_engine_node`. | corpus check: Python-passes → encode → Rust emit runs the behavioral vectors correctly on the Rust interpreter |
| T1.3 | todo | Baseline pipeline (= `minimal` level): Braun SSA construction → Boissinot out-of-SSA with coalescing → chordal-coloring slot allocation (≤4096 slots) → emit. No optimization. | pydori callbacks all compile within temp-memory budget at minimal (improvement over legacy `AllocateBasic`) |
| T1.4 | todo | Dual-lane conftest: `SONOLUS_BACKEND=rust` routes `tests/script/conftest.py` through encode → Rust pipeline (levels available so far) + Rust interpreter, seed drawn once per invocation. CI stage B: rust-lane pytest job (Ubuntu, 3.14). | full behavioral suite green in **both** lanes, locally and in CI |

### S2 — Optimizer infrastructure

| ID | Status | Task | DoD |
|----|--------|------|-----|
| T2.1 | todo | Analyses: dominator tree, loop forest, liveness; on-demand caching with explicit invalidation. | cargo unit tests on hand-built and corpus CFGs |
| T2.2 | todo | Rewrite-rule framework + pipeline/level configuration (`minimal`/`fast`/`standard` as prefixes). | identity pipeline preserves baseline behavior on full corpus |
| T2.3 | todo | Differential-interpretation harness (minimal-vs-optimized, randomized memory + seeds, compares results/logs/writes) + CFG fuzz generator (proptest/arbitrary, shrinking). | seeded miscompile in a deliberately broken transform is caught and shrunk by both harness and fuzzer |
| T2.4 | todo | Metrics: Rust collectors (static nodes, DAG size, dyn eval count, dispatch count, wall time) + `tools/metrics.py` for the Python backend; capture `rust/baselines/python-standard.json` and `python-fast` timings (for G-P1) from the frozen oracle over mini-corpus + pydori. | baseline files committed; metrics report runs in one command |

### S3 — Optimizer waves (fan-out allowed within a wave; gates are single-agent)

Wave gate template (each `G3.x`): behavioral suite green at all levels in the rust lane;
differential interpretation clean on full corpus; fuzz budget clean (≥30 min or 1M cases);
metrics ratchet not regressed; worklog entry with metric movement.

| ID | Status | Task | DoD |
|----|--------|------|-----|
| T3.1 | todo | W1: SCCP (Python folding semantics, unreachable-edge pruning). | per-transform differential + fuzz |
| T3.2 | todo | W1: GVN + rewrite rules (commutative canonicalization, algebraic simplification, strength reduction). | per-transform differential + fuzz |
| T3.3 | todo | W1: ADCE + branch simplification + jump threading. | per-transform differential + fuzz |
| G3.1 | todo | W1 gate. Target: ≈ legacy `fast` quality. | wave gate template |
| T3.4 | todo | W2: Mem2Reg/SROA for TempBlocks (constant-index → scalars; dynamic-index arrays stay memory). **Top-risk transform — extra fuzz emphasis on dynamic indexing.** | per-transform differential + fuzz |
| T3.5 | todo | W2: copy-coalescing and allocation quality improvements. | per-transform differential + fuzz; temp-slot metrics |
| G3.2 | todo | W2 gate. | wave gate template |
| T3.6 | todo | W3: switch formation (RewriteToSwitch successor; recognizes post-GVN comparison trees on an integer scrutinee). | per-transform differential + fuzz |
| T3.7 | todo | W3: LICM; optional cost-modeled micro-unroll of tiny constant-trip loops. | per-transform differential + fuzz |
| G3.3 | todo | **W3 gate = switchover ratchet**: aggregate ≥ parity with `rust/baselines/python-standard.json`; no callback >10% worse on dyn eval count. | wave gate template + ratchet |
| T3.8 | todo | W4: expression-level if-conversion (small diamonds/triangles → `If`/`And`/`Or` value nodes, cost-modeled). | per-transform differential + fuzz; dispatch-count metric drop |
| T3.9 | todo | W4: block merging, exit combining, tiny-block duplication into predecessors. | per-transform differential + fuzz |
| G3.4 | todo | W4 gate. | wave gate template |
| T3.10 | todo | W5: emission-time FlattenAssociativeOps (sharing-aware vs node DAG dedup). | per-transform differential + fuzz; node-count metric |
| T3.11 | todo | W5: NormalizeSwitch (dense 0-based case manufacture) + dense-form selection in the emitter. | per-transform differential + fuzz |
| T3.12 | todo | W5: fused-op tiling (`Lerp`/`Remap`/`Clamp`/`*Shifted`/`*Pointed`/`Set*`/`Increment*`), Execute0/Execute selection. Rules added data-driven from metrics hot spots. | per-transform differential + fuzz; eval-count metric |
| G3.5 | todo | W5 gate; final S3 metrics report committed to the worklog. | wave gate template |

### S4 — Single-call build

| ID | Status | Task | DoD |
|----|--------|------|-----|
| T4.1 | todo | Payload schema + Python assembly: per-mode work units (callback, archetype idx, order, encoded CFG) + metadata JSON + ROM + level selection. | schema doc + assembly unit tests |
| T4.2 | todo | Rust `build_engine`: stateless intra-call dedup (hash of encoded CFG), rayon per callback with GIL released, canonical node ordering (archetype order then callback order), mode JSON (`preserve_order`), gzip mtime=0, ROM packing. Pure function. | A/B vs Python on pydori: decompressed structural equality of mode data + per-callback differential interpretation |
| T4.3 | todo | Wire `package_engine`; `validate_engine` stays pure-Python trace-only. **Remove** `CompileCache`, `hash_cfg`, the `cache` parameter threading (~8 signatures), and dev-server cache lifecycle (`reset_accessed`/`prune_unaccessed`). | `tests/build/test_dev_server.py` + full suite green; grep audit: no `CompileCache`/`hash_cfg` references |
| T4.4 | todo | Flip dev default to `standard` in `cli.py` (keep `-O0/-O1/-O2`); perf gates **G-P1** and **G-P2** measured on pydori and recorded below in §8-metrics. | gates pass; CLI help updated |

### S5 — Collection + level packaging

| ID | Status | Task | DoD |
|----|--------|------|-----|
| T5.1 | todo | Collection core in Rust: scp/zip load, SHA1 repository, resource gzip, localization, level/engine linking, site-tree write (skip-if-hash-exists). | cargo tests on fixture scp/source trees |
| T5.2 | todo | PyO3 `Collection` preserving the current Python API; Python keeps orchestration, user converter callbacks, URL fetching. | `tests/build` + regressions green |
| T5.3 | todo | A/B pydori collection both backends: structural site-tree equality (decompressed content; gzip bytes may differ); CLI smoke `sonolus-py build` on `test_projects/pydori`. | `tools/ab_collection.py` zero structural diffs |

### S6 — Test rewiring + golden regeneration

| ID | Status | Task | DoD |
|----|--------|------|-----|
| T6.1 | todo | `tests/script/conftest.py` Rust-only: remove `SONOLUS_BACKEND` lanes and the `random.setstate` dance (explicit seeds); all three levels via Rust. | full suite green `-n 32` |
| T6.2 | todo | **The one sanctioned golden regeneration** (dedicated reviewed PR): regressions produce optimized-CFG and node dumps via Rust; regenerate those goldens. Frontend CFG goldens must be byte-identical before/after — that diff being empty is the review check. | regression suite green; frontend-golden diff empty |
| T6.3 | todo | Freeze differential snapshots into `rust/testdata/`; retire Python-oracle tooling (`tools/metrics.py` Python side becomes historical). | `cargo test` self-contained offline |

### S7 — Switchover and deletion

| ID | Status | Task | DoD |
|----|--------|------|-----|
| T7.1 | todo | Delete Python backend: `backend/optimize/` (trim `flow.py` to `BasicBlock`/`FlowEdge`/traversal for frontend + encoder), `finalize.py`, `interpret.py`, `build/node.py`; reduce `compile.py`/`engine.py`/`collection.py` to wrappers. Keep `blocks.py`, `mode.py`, `ops.py`, `ir.py`, `place.py`, `excepthook.py`, `utils.py`. | full suite green; grep audit: no imports of deleted modules anywhere (incl. docs/doc_stubs) |
| T7.2 | todo | Packaging flip: `pyproject.toml` → maturin; wheels workflow (maturin-action): manylinux x86_64 + aarch64, macOS universal2, Windows x86_64 × CPython 3.12–3.14 + 3.13t/3.14t; sdist; per-OS install-test of wheels (run test suite against installed wheel); tox updated. Publish flip itself is maintainer-gated. | all matrix jobs green; artifact inspection dry-run |
| T7.3 | todo | Local dev support: README/CONTRIBUTING section (uv + rustup + `maturin develop`, Windows notes); pre-commit gains rustfmt/clippy; agent guidance (CLAUDE.md) updated for the new layout. | documented commands verified verbatim |
| T7.4 | todo | Changelog (incl. dev-default change to standard, before/after benchmark + metrics tables), version bump, final ledger close-out summary. | release checklist complete; maintainer tags |

## 8. Decision log

- **D1** Single-release switchover; no period maintaining both backends (experimental project).
- **D2** Optimizer is redesigned, not ported. Correctness oracle = behavioral suite +
  differential interpretation + fuzzing. Frontend goldens stay byte-exact; optimizer/node
  goldens regenerated once (T6.2).
- **D3** `JumpLoop` is the native CFG encoding of the target runtime and is kept. No
  structured control-flow reconstruction; W4 is expression-level if-conversion and block
  shaping instead.
- **D4** Runtime-specific passes retained by design: RewriteToSwitch (mid-level, post-GVN),
  NormalizeSwitch (lowering, paired with dense-switch selection), FlattenAssociativeOps
  (emission-time only). Mid-level IR is strictly binary (invariant 3).
- **D5** Dev defaults to `standard` passes (G-P1/G-P2 make "fast enough" a measured gate).
  `fast` demoted to triage; `minimal` is the differential baseline + debug level.
- **D6** `CompileCache` and `hash_cfg` are removed, not ported. `build_engine` is a pure
  function; intra-call stateless dedup only. Remedy for any future perf gap lives inside
  the single call, never as cross-call state.
- **D7** No Python-`repr` float-formatting compatibility in Rust. Encoder validation is
  structural/bit-exact; Rust dumps use Rust formatting; regenerated goldens are
  Rust-formatted snapshots.
- **D8** E-graph extraction (egg) is a post-port experiment behind a flag; promoted only if
  it beats the greedy rewriter on the metrics dashboard.
- **D9** `-O1`/`fast` stays in the CLI as a lightly-documented triage level; the regression
  suite keeps exercising it so pipeline prefixes remain valid stopping points.

### Blocked / decisions needed

(none)

### Deviation log

Every application of a §4 autonomous policy gets an entry: date, trigger, task/gate,
what was done instead, severity (`info` / `review-before-merge` / `blocks-merge`), and
pointers (failing commands, metric numbers, repro). Empty deviation log = clean run.

- 2026-06-10 — T0.3 DoD proxy (§4: non-machine-checkable DoD). DoD says "green run on a
  PR"; no `gh` CLI is installed so a PR cannot be opened autonomously. Proxy used: green
  push-triggered run of the identical workflow on `rust-port`
  (https://github.com/qwewqa/sonolus.py/actions/runs/27299008404, conclusion: success;
  the workflow also declares the same path-filtered `pull_request` trigger). Severity:
  info. Maintainer gets the true PR-triggered run for free when opening the merge-review
  PR.

### Recorded metrics

(populated by T2.4, G3.x, T4.4)

## 9. Worklog (append-only; newest first)

- 2026-06-10 — **T0.5 done; S0 complete.** Capture: `tests/conftest.py` monkeypatches
  `callback_to_cfg` (catches every frontend CFG suite-wide); `tests/script/conftest.py`
  records I/O vectors at the first opt level via `RecordingInterpreter` + RNG
  call-window tape (`random.uniform`/`randrange` delegated, behavior unchanged); all
  gated on `SONOLUS_CAPTURE_CORPUS` — env unset ⇒ `_CAPTURE is None`, original paths.
  Content-addressed sha256 names + atomic writes ⇒ xdist-safe. Python canonical dump is
  stored at capture time (both sides compare against the single stored artifact).
  `tools/gen_corpus.py`: verifies whole capture round-trips, then deterministically
  curates `rust/testdata/` (141 CFGs, 87 vectors on 72 entries, 4,899,682 B data; all
  19 mode/callback pairs; hypothesis-only CFGs excluded). Orchestrator verification:
  fresh capture run → 1096 green, **24,740 unique CFGs all round-trip clean, zero encode
  rejects** (completes T0.4's full-corpus DoD); regen from the original capture is
  byte-identical to checked-in testdata (355 files; README is hand-written); cargo
  19+4+4 green incl. corpus byte-flip negative test; clippy/fmt clean; pytest 1096 green
  env-unset. Notes: vectors store an RNG *tape*, not a seed (legacy interp uses global
  `random`); T1.2 replays the tape, T2.3 uses its own seeded RNG. Float-cond CFGs are
  nearly hypothesis-only → T2.3 fuzz generator must cover float conds explicitly.
  Subagent's reported pass count (1103) didn't reproduce; current tree is consistently
  1096 with and without capture — no test loss, count verified directly.
- 2026-06-10 — **T0.4 done.** Encoding v1 (`rust/ENCODING.md`): magic `SCFG`, u16 version,
  u16 op_count=191 desync guard; LEB128/zigzag varints, raw-bits f64; string + temp-block
  tables (first-encounter order); RPO blocks, edges sorted `(cond is None, cond)`, tagged
  conds none/int/float. `sonolus/backend/encode.py` (new file; frozen backend untouched)
  is fully iterative incl. its own iterative RPO equal to flow.py's ordering. Rust:
  `cfg.rs` arena + `decode.rs` (`Result`, work stacks, alloc caps, no panics on corrupt
  input — verified by truncation-at-every-prefix and byte-flip-sweep tests), canonical
  dump (floats as `f:0x%016x` raw bits — NaN payloads/−0.0 bit-exact) + Rust-fmt debug
  dump; PyO3 handles `decode_cfg_canonical_dump`/`decode_cfg_debug_dump`. Tests: 15 Rust
  (incl. cross-pinned literal byte stream + 200k-deep iterativeness), 43 backend pytest
  (7 real traced callbacks + edge cases; `importorskip` keeps tox lane green). rust.yml
  python-smoke now runs `pytest tests/backend`. Notes: IRConst can hold >i64 bignums —
  v1 rejects with clear error (version bump path documented); real CFGs do emit float
  edge conds (float dict keys). DoD's full-corpus half lands with T0.5. Verified: cargo
  19+4 green, clippy/fmt exit 0, pytest 880 green, frozen-tree audit clean.
- 2026-06-10 — **T0.3 done.** `.github/workflows/rust.yml`: `checks` job (fmt → clippy
  `-D warnings` → `cargo test --workspace` with setup-python 3.14 + `LD_LIBRARY_PATH` for
  the pyo3-linked test binary) and `python-smoke` job (uv sync → `gen_ops.py --check` →
  maturin develop → import smoke). Path-filtered to `rust/**`, `tools/**`,
  `pyproject.toml`, `uv.lock`, the workflow itself; triggers: push (master, rust-port) +
  pull_request. publish.yaml untouched. Authored directly by the orchestrator (single
  YAML; DoD is a CI gate, which is never delegated). Branch `rust-port` pushed; first run
  green: actions/runs/27299008404. DoD "green on a PR" satisfied via push-run proxy — see
  Deviation log (info).
- 2026-06-10 — **T0.2 done.** `tools/gen_ops.py` (pure `generate()`, `--check` flag) emits
  `rust/sonolus-backend-core/src/ops.rs`: `#[repr(u16)] Op` enum, **191 ops** (exact
  count), ids = 0-based definition order in ops.py (Abs=0 … While=190) — these ids are
  the T0.4 encoding contract (append-only; reorder ⇒ version bump). `const fn` accessors,
  `from_id`/`from_name`, `COUNT`, `all()`. `.gitattributes` pins ops.rs to LF so the
  byte-exact pytest sync test (tests/backend/test_ops_sync.py) survives checkout. Rust
  sanity tests (count, known flags, round-trips). Note for optimizer work: `Random`,
  `RandomInteger`, `Get`, `Stack*` family are neither pure nor side-effecting in ops.py.
  Verified: desync→fail→regen→pass cycle myself; cargo test/clippy/fmt clean; pytest 841
  (837+4) green.
- 2026-06-10 — **T0.1 done.** Cargo workspace `rust/` (resolver 3, edition 2024, workspace
  lints with `unsafe_code = "forbid"`): `sonolus-backend-core` (pure Rust stub) +
  `sonolus-backend-py` (PyO3 0.28.3, abi3-py312, `gil_used = false`, module
  `sonolus_backend`). `pyo3/extension-module` enabled via the crate pyproject's
  `[tool.maturin] features` (not Cargo.toml) so `cargo test --workspace` links on Linux.
  Canonical dev command: `uv run maturin develop -m rust/sonolus-backend-py/Cargo.toml`
  from repo root (running uv inside the crate dir would spawn a second venv). Main
  pyproject: only `maturin>=1.9` added to dev group; build-system untouched. Note for
  T7.3/T0.3 docs: plain `uv sync` uninstalls the maturin-develop artifact (not in uv.lock)
  — re-run maturin develop after sync, or use `uv sync --inexact`. Verified: cargo
  test/clippy/fmt clean; import smoke OK; pytest 837 passed.
- 2026-06-10 — Ledger and architecture documents created. Port not yet started.
