# Rust Backend Port — Execution Ledger

> **This file is the single source of truth for the port.** Every work session starts by
> reading this file and [ARCHITECTURE.md](ARCHITECTURE.md), and ends by updating the task
> table and appending to the worklog. Design rationale lives in ARCHITECTURE.md; this file
> holds rules, tasks, state, and decisions. Maintainer notes (setup, end-of-run review)
> live in [EXECUTION.md](EXECUTION.md).

**Status:** paused (maintainer request) — **G3.2 passed** (W2 gate, one fix cycle) and
T5.2 done and verified; **first item at resume: T5.2 CI fix** — rust-lane CI on d4243c3
(Ubuntu) fails 2/12 collection parity tests (`test_source_tree_parity`,
`test_load_resources_files_to_collection_lane_parity`: repository insertion order;
local Windows green — looks like the documented name-sorted source iteration divergence
surfacing on Linux readdir order). Then **W3** (T3.6 switch formation + T3.7 LICM,
parallel fan-out per §2.2), then gate G3.3 (the switchover ratchet).
**Last updated:** 2026-06-12

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
| T1.1 | done | Interpreter port: seeded RNG, eval + JumpLoop-dispatch counters, `HashMap<i64,Vec<f64>>` blocks (negative ids), default `-1.0` fill, exact legacy assert messages, Block/Break unwinding, all switch forms. PyO3: `Interpreter(seed=)`, `set_block`, `get`, `run`, `log`, counters. | unit tests incl. numeric-semantics edge table (floor-mod, banker's round, remainder, Sign(±0)) |
| T1.2 | done | Emitter: CFG → `Block(JumpLoop(...))` node tree (dense-switch selection per legacy `finalize.py` rules), node DAG dedup with canonical insertion order, int/float-tagged values, ROM NaN/inf, `format_engine_node`. | corpus check: Python-passes → encode → Rust emit runs the behavioral vectors correctly on the Rust interpreter |
| T1.3 | done | Baseline pipeline (= `minimal` level): Braun SSA construction → Boissinot out-of-SSA with coalescing → chordal-coloring slot allocation (≤4096 slots) → emit. No optimization. | pydori callbacks all compile within temp-memory budget at minimal (improvement over legacy `AllocateBasic`) |
| T1.4 | done | Dual-lane conftest: `SONOLUS_BACKEND=rust` routes `tests/script/conftest.py` through encode → Rust pipeline (levels available so far) + Rust interpreter, seed drawn once per invocation. CI stage B: rust-lane pytest job (Ubuntu, 3.14). | full behavioral suite green in **both** lanes, locally and in CI |

### S2 — Optimizer infrastructure

| ID | Status | Task | DoD |
|----|--------|------|-----|
| T2.1 | done | Analyses: dominator tree, loop forest, liveness; on-demand caching with explicit invalidation. | cargo unit tests on hand-built and corpus CFGs |
| T2.2 | done | Rewrite-rule framework + pipeline/level configuration (`minimal`/`fast`/`standard` as prefixes). | identity pipeline preserves baseline behavior on full corpus |
| T2.3 | done | Differential-interpretation harness (minimal-vs-optimized, randomized memory + seeds, compares results/logs/writes) + CFG fuzz generator (proptest/arbitrary, shrinking). | seeded miscompile in a deliberately broken transform is caught and shrunk by both harness and fuzzer |
| T2.4 | done | Metrics: Rust collectors (static nodes, DAG size, dyn eval count, dispatch count, wall time) + `tools/metrics.py` for the Python backend; capture `rust/baselines/python-standard.json` and `python-fast` timings (for G-P1) from the frozen oracle over mini-corpus + pydori. | baseline files committed; metrics report runs in one command |

### S3 — Optimizer waves (fan-out allowed within a wave; gates are single-agent)

Wave gate template (each `G3.x`): behavioral suite green at all levels in the rust lane;
differential interpretation clean on full corpus; fuzz budget clean (≥30 min or 1M cases);
metrics ratchet not regressed; worklog entry with metric movement.

| ID | Status | Task | DoD |
|----|--------|------|-----|
| T3.1 | done | W1: SCCP (Python folding semantics, unreachable-edge pruning). | per-transform differential + fuzz |
| T3.2 | done | W1: GVN + rewrite rules (commutative canonicalization, algebraic simplification, strength reduction). | per-transform differential + fuzz |
| T3.3 | done | W1: ADCE + branch simplification + jump threading. | per-transform differential + fuzz |
| G3.1 | done | W1 gate. Target: ≈ legacy `fast` quality. | wave gate template |
| T3.4 | done | W2: Mem2Reg/SROA for TempBlocks (constant-index → scalars; dynamic-index arrays stay memory). **Top-risk transform — extra fuzz emphasis on dynamic indexing.** | per-transform differential + fuzz |
| T3.5 | done | W2: copy-coalescing and allocation quality improvements. | per-transform differential + fuzz; temp-slot metrics |
| G3.2 | done | W2 gate. | wave gate template |
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
| T5.1 | done | Collection core in Rust: scp/zip load, SHA1 repository, resource gzip, localization, level/engine linking, site-tree write (skip-if-hash-exists). | cargo tests on fixture scp/source trees |
| T5.2 | done | PyO3 `Collection` preserving the current Python API; Python keeps orchestration, user converter callbacks, URL fetching. | `tests/build` + regressions green |
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
- **D10** (T1.3) The `minimal` level does **not** promote TempBlocks to SSA values: all
  TempBlock accesses stay memory ops, keeping the differential baseline trivially
  correct (no transform without differential coverage; the harness arrives in T2.3).
  Braun construction + Boissinot out-of-SSA ship in T1.3 as tested infrastructure
  (unit tests incl. hand-built phi-ful IR) and become load-bearing at W2 Mem2Reg
  (T3.4), which owns constant-index→SSA promotion per the architecture pipeline. The
  T1.3 allocator colors live ranges at TempBlock granularity (sized units, ≤4096),
  which is what beats legacy `AllocateBasic` (no reuse) on pydori; scalar-slot coloring
  of out-of-SSA values activates with W2.
- **D11** (T1.3) And/Or binarization: MIR `ShortCircuit{lhs, rhs}` is binary (invariant 3
  holds) but `rhs` is the root of an **unscheduled lazy expression tree** owned by the
  instruction — the one exception to eager value operands, required because frontend
  And/Or args may trap (loads with asserts). Right-associative binarization with an
  induction proof of equivalence to legacy n-ary short-circuit (in mir.rs). Every
  optimizer pass MUST respect laziness: nothing may be hoisted out of, or assumed
  evaluated in, the lazy side. Corpus fact: real frontend And/Or are all already binary.

### Blocked / decisions needed

- Before G3.3: decide whether to add an opt-in deterministic stub mode for
  runtime-only ops (Draw/BeatToTime/ExportValue/…) to the interpreter for METRICS runs
  only — 126/300 pydori baseline rows currently trap at the first runtime-only op
  (identical trap point both backends, so comparisons stay valid, but draw-heavy
  callbacks only measure their executable prologue). Adopting it requires a baseline
  regen (deterministic, documented). Revisit at G3.1 with real wave metrics in hand.

### Deviation log

Every application of a §4 autonomous policy gets an entry: date, trigger, task/gate,
what was done instead, severity (`info` / `review-before-merge` / `blocks-merge`), and
pointers (failing commands, metric numbers, repro). Empty deviation log = clean run.

- 2026-06-10 — Suspected oracle quirk (§4: suspected bug in the Python backend — logged,
  not fixed). `Allocate.get_mapping`'s overlap condition
  (`offset + size > other_offset or other_offset + other.size > offset`,
  sonolus/backend/optimize/allocate.py:124) is a tautology for positive sizes, so legacy
  standard `Allocate` degenerates to max-end placement (≡ `AllocateFast`). Quality-only
  (allocation stays correct, just non-reusing); behavioral tests unaffected. Repro: any
  two non-interfering temp blocks still get disjoint offsets. Also: the "minimal won't
  work" comment in tests/regressions/test_project.py is stale — legacy MINIMAL_PASSES
  fits pydori today (max callback usage 1631 < 4096, verified by running it). Severity:
  info.
- 2026-06-10 — T0.3 DoD proxy (§4: non-machine-checkable DoD). DoD says "green run on a
  PR"; no `gh` CLI is installed so a PR cannot be opened autonomously. Proxy used: green
  push-triggered run of the identical workflow on `rust-port`
  (https://github.com/qwewqa/sonolus.py/actions/runs/27299008404, conclusion: success;
  the workflow also declares the same path-filtered `pull_request` trigger). Severity:
  info. Maintainer gets the true PR-triggered run for free when opening the merge-review
  PR.

### Recorded metrics

- **T2.4 baselines** (2026-06-10, Zen 5 / Win11 / CPython 3.14.3, 300 pydori callbacks,
  dynamics on the Rust interpreter under documented seeded fill, 0 dynamic:null rows):
  - `python-standard`: static_nodes 458,996; dag_size 105,772; eval_count 50,245;
    dispatch_count 1,333; compile wall total 25,306 ms.
  - `python-fast`: compile wall total **1,841.9 ms** (G-P1 reference).
  - `rust-corpus` (Rust-side, 70 entries / 86 vectors): eval 38,880; dispatch 2,049;
    static 23,553; dag 6,934.
  - Rust today (pre-W1, standard ≡ minimal) vs python-standard: static 2.742×, dag
    2.157×, eval 2.342×, dispatch 1.401×; 193/300 callbacks >10% worse on eval.
  - Caveat: 126/300 rows trap at the first runtime-only op (Draw/BeatToTime/…) —
    identical trap point both sides; see Blocked/decisions.

## 9. Worklog (append-only; newest first)

- 2026-06-12 — **T5.2 done (PyO3 Collection), merged and verified; run wrapped at
  maintainer request — resume at W3 (T3.6 + T3.7).** Subagent ran in a worktree during
  the G3.2 fuzz window (S5 overlap per §6). `sonolus_backend.Collection` (PyO3,
  `rust/sonolus-backend-py/src/collection.rs`): persistent SHA1 repo (IndexMap),
  scp/zip load, resource gzip, level/engine link, pyjson serialization, site-tree
  write; categories cross as JSON; errors map to legacy KeyError/OSError/ValueError
  messages. Python `RustCollection(Collection)` drop-in (`sonolus/build/
  rust_collection.py`): inherits the live-categories dict-mutation pattern, user
  converters, URL fetch; `write` links in Rust and adopts the post-link snapshot
  (preserves legacy dev-rebuild aliasing incl. the stale-engine quirk — A/B equality
  prioritized, documented). Selection: `make_collection()` honors `SONOLUS_BACKEND`
  (same value set as the test lane); both classes stay directly constructible for
  T5.3's A/B. 12 parity tests (`tests/build/test_collection.py`); gzip byte-identical
  in all of them (zlib-rs L9) — T5.3 zero-byte-diff plausible. **Suite-coverage find:
  pytest's default `norecursedirs` contains `build`, so `tests/build/` had been
  silently skipped in every full-suite/CI run ever** (§5's "behavioral + build +
  regressions" was aspirational; T4.3's dev-server DoD would have been vacuous) —
  fixed via `[tool.pytest.ini_options] norecursedirs` in pyproject.toml; suite 1196→
  1235 passed. Divergences documented in the class docstring (garbage-input error
  types, name-sorted source iteration, JSON-round-trippable item values). Follow-ups
  noted: GIL released during `write` as a dev-server micro-improvement; T5.3 needs a
  real pydori `resources/` tree. Orchestrator verified on the merged tree (49430c3):
  cargo 363 lib + all suites, clippy/fmt clean, both lanes 1235+4, explicit
  build+regressions 43 passed.
- 2026-06-12 — **G3.2 passed (W2 gate), one fix cycle.** Template items, all run by the
  orchestrator: behavioral green both lanes at all 3 levels (1196+4 each, re-verified
  post-fix); corpus differential clean in-suite; **the first 1M-case release fuzz run
  CAUGHT a real W2 miscompile** — GVN's rules-phase sweep cascade-deleted trap-capable
  orphans: a rewrite rule firing on an already-zero-use root (the dead-store value tree
  Mem2Reg correctly leaves scheduled for trap preservation) put it in `replaced`, and
  `sweep()`'s purity-only transitive-orphan check then deleted an `Arcsin` whose trap
  minimal observes. Shrunk shape: dead `temp[0] <- Sub(Arcsin(Add(uniform[0,6))), t1[19])`
  with only never-written `temp[1]` read; minimal-vs-fast PASSED on the same input —
  W1-only is clean, the hole needed the post-promotion W1 re-run (w1-sub-zero firing on
  the dead tree). Fix (2cf7927, cycle 1/3): `sweep()` takes an `OrphanPolicy` — rules
  sweep requires transitive orphans to be on the 24-op never-trapping whitelist (= DCE's
  `op_is_total`; seeds stay fully removable — each rule/fold proves its own root);
  GVN-sweep orphans unchanged (dominating same-class leader computes the same values
  earlier and traps first with the same error). 2 GVN unit tests + a hand-built-CFG
  end-to-end regression test, all verified failing pre-fix; fuzz seed persisted
  (c44c938). Re-run: **1M cases, 3/3 ok in 3,284s, clean**. Ratchet regen
  (`corpus --update`): eval 36,673→22,837, static 20,609→13,651, dispatch 2,039→2,016,
  dag 6,634→5,452; the fix cost ZERO on the corpus; 0 vectors >10% worse. The in-cargo
  corpus test now enforces non-strict ratchet semantics (`corpus_ratchet_*` — the old
  strict-improvement assert read the live ratchet file and broke at the first gate regen
  by construction; c44c938). pydori vs python-standard (unchanged by the fix): eval
  1.308×, static 1.701×, dag 1.286×, dispatch 1.433× — G3.3 parity needs W3+ as planned.
  Hygiene: 2 unused test-cfg imports fixed (invisible to standing clippy — no
  `--all-targets`).
- 2026-06-10 — **T3.5 done; run paused at maintainer request — resume at G3.2.**
  `coalesce.rs`: second Boissinot phase at temp granularity (union-find over the
  allocator's own interference graph; catches residual parallel copies, gvnN
  double-stores, m2rN reroutes; self-copies deleted); copy-only split blocks threaded
  out (T3.4's +1.3% dispatch regression fully clawed back, now below the stored
  ratchet). `alloc.rs`: unified on analysis::BitSet; legacy array_defs/is_array_init
  liveness parity; scalar coloring = MCS-order greedy **portfolioed against table
  order** (ties prefer table order — slot layout is a DAG-dedup input: pure MCS cost
  +5% dag with zero slot benefit; never worse than T1.3 by construction). pydori
  slots: standard total 2,705→2,622, max 41 = proven structural floor (two unpromoted
  dynamic-index arrays + pressure-optimal 17 scalar colors); further reduction needs
  array SROA (W3+ fodder). Corpus standard: eval 23,853→22,837, static 14,015→13,651,
  dispatch 2,065→2,016. pydori vs python-standard: eval 1.322×→1.308×, static
  1.773×→1.701×, dispatch 1.566×→1.433×. Subagent ran 50k release fuzz (level +
  dynamic-heavy) clean; scalar interference post-coalescing is NOT chordal (hence
  portfolio). Orchestrator verified: cargo all suites green, clippy/fmt clean, both
  pytest lanes 1196+4 green. **G3.2 remains**: 1M fuzz budget run, rust-corpus.json
  ratchet regen (`tools/metrics.py corpus --update`), gate worklog, CI push.
- 2026-06-10 — **T3.4 done (top-risk Mem2Reg) + T5.1 done (collection core), both
  merged and verified.** T3.4: whole-temp promotion via Braun (escapes: dynamic index,
  OOB const; refusal for statically-possible read-before-write paths — 160/8,992
  corpus temps ≈ legacy ToSSA err-place cases; lazy-tree loads rerouted through m2r
  temps, zero lazy refusals); `destruct_ssa` wired unconditionally into compile_cfg
  (extended: per-occurrence multi-use loads — fixed latent Op(v,v) shared-load bug;
  lazy-referenced scheduled values; order-breaking-splice fixpoint); W1 re-run
  registered post-promotion (registry now [W1×3, mem2reg, W1×3 again]). **Two GVN
  fixes**: phi-arg uses uncounted in sweep (real bug, caught by 50k fuzz, seed
  persisted) + singleton-class CSE enabled (the main post-promotion CSE case).
  Promotion: 97.7% of corpus temps; corpus standard metrics: **eval 36,673→23,853
  (−35%), static 20,609→14,015 (−32%)**; dispatch +1.3% (phi-copy split blocks —
  T3.5/W4 threading fodder). Fuzz: per-pass 50k + NEW dynamic-heavy profile 50k +
  level 50k, all clean, release. T5.1: `collection/` (+`pyjson` CPython-3.14-exact
  serializer incl. repr(float) and \uXXXX rules); A/B vs frozen Python on fixture scp:
  22/22 site-tree files byte-identical, zlib-rs L9 gzip byte-matches Python gzip
  mtime=0 (T5.3 may see zero byte diffs); skip-if-hash-exists via WriteStats;
  divergences documented (error values vs exceptions, name-sorted source iteration);
  deps: serde_json+preserve_order promoted, indexmap, sha1, flate2, zip(zlib-rs).
  3.14 PurePath.stem/suffix semantics pinned. Combined tree verified: cargo 348 lib +
  all suites green, clippy/fmt clean, both pytest lanes 1196+4 (rust lane 168s — W2
  makes it FASTER). Next: T3.5 then gate G3.2 (incl. rust-corpus ratchet regen).
- 2026-06-10 — **G3.1 passed (W1 gate).** All template items, run by the orchestrator:
  behavioral suite green at all 3 levels in the rust lane (1196+4 locally; CI run on
  28aa508 green incl. the rust-lane job) and default lane green; corpus differential
  clean (in-suite, minimal-vs-fast + minimal-vs-standard, 0 mismatches); **fuzz budget
  clean: 1M release cases, 3/3 tests ok in 1,324s, 0 mismatches**; metrics ratchet
  not regressed — improved across the board (pydori vs python-standard: eval
  2.342×→2.177×, static 2.742×→2.619×, dag 2.157×→2.053×, dispatch flat; corpus:
  eval 38,880→36,673, 0 vectors >10% worse; rust-corpus.json ratcheted up). Target
  "≈ legacy fast quality" comfortably met (legacy FAST_PASSES does no optimization —
  Rust fast additionally beats it on allocation ~12×). W1 quality is load-gated on
  memory promotion as designed; W2 next.
- 2026-06-10 — **T3.1 + T3.2 + T3.3 done (W1 wave, parallel worktrees); G3.1 underway.**
  SCCP (`sccp.rs`: Wegman-Zadeck, py_* kernel folding with the full
  no-fold-on-Python-error table, executable-edge pruning, ShortCircuit refinement with
  evaluation-order-preserving rhs splice, phi-ready for W2; 34 unit tests). GVN+rules
  (`gvn.rs`/`rules.rs`: canonical commutative operand order spec — consts first by
  total_cmp/tag/arena idx; Min/Max deliberately NOT commutative under NaN/±0; dominator
  GVN sharing via fresh single-slot temps to respect the single-use lowering contract;
  6 legacy-derived rules each with safety guards — add-zero guarded on never_neg_zero,
  unguarded legacy forms provably falsified by the T2.3 harness). DCE (`dce.rs`:
  mark-live with trap-conservative 24-op never-raising whitelist, RNG never removed,
  per-point temp DSE, branch simplification, empty-block jump threading; corpus static
  nodes −13.5% standalone). Each: corpus differential 270 cases 0 mismatches +
  per-pass 20k release fuzz clean. **Cross-task findings reconciled at merge by the
  orchestrator**: (1) canonical-NaN rule — generated NaN (x86 inf*0 = −NaN) is
  unmaterializable since NaN consts emit as ROM +NaN and Sign exposes the sign bit;
  GVN discovered by fuzz, orchestrator applied the same rule to SCCP (lattice seeds
  normalize NaN payloads; non-canonical NaN folds refused; new unit test) — T3.1's
  fuzz had missed it. (2) fuzzgen dyn_index = `Floor(Mod(Mod(x,s),s))` combining
  T3.1's boundary-rounding fix with T3.3's allocation-dependent-integrality fix (both
  load-bearing, docs explain). (3) identity_levels relaxed to behavioral-replay-only.
  Registry order SCCP→GVN→DCE. Combined tree: cargo 294 lib + all integration green,
  clippy/fmt clean, both pytest lanes green (rust lane at all 3 levels, 1196+4).
  Metrics vs python-standard: eval 2.342×→**2.177×**, static 2.742×→**2.619×**, dag
  2.157×→**2.053×**, dispatch flat 1.401× (W1 does no CFG shaping); corpus ratchet
  all-improved (eval 38,880→36,673, 0 vectors >10% worse), rust-corpus.json moved up.
  Modest by design: loads are opaque until W2 Mem2Reg (both subagents confirmed real
  callbacks' values flow through memory). W2 dispatch note: rerun-SCCP/GVN-after-
  Mem2Reg question goes to T3.4's design. G3.1 remaining: 1M-case fuzz budget run
  (in progress), CI green, gate worklog close-out.
- 2026-06-10 — **T2.4 done; S2 complete.** `tools/metrics.py` baseline/report/corpus
  (one-command report incl. `--ratchet` = the G3.3 gate path, exits 1 today as
  expected). Both backends' dynamics measured on the RUST interpreter (legacy
  FunctionNode→EngineNodes converter, validated against Rust counters); pydori-only
  baseline (Python can't decode .scfg — corpus ratchet half is Rust-vs-Rust via
  rust-corpus.json). Seeded fill = bit-exact Python mirror of diff.rs::build_memory
  (pinned by test, 4 seeds); PyO3 gained `set_eval_budget`/`seeded_memory`;
  `run_pipeline_stats` returns static_nodes/dag_size. Baselines committed (schema v1,
  metadata: CPU/commit/seeds); determinism check PASSED both files (volatile fields
  stripped). Headline numbers in §8-metrics. Notes: legacy CFGs can't deepcopy
  (IRConst.__new__) — timings re-trace per run; `OptimizerConfig(mode=, callback=)` is
  load-bearing for cse/inlining/licm. Verified: report run end-to-end + determinism
  check by orchestrator; cargo 259 green; clippy/fmt clean; both lanes 1196+4 green.
- 2026-06-10 — **T2.3 done.** `diff.rs`: `diff_levels`/`diff_with` (closure compile sides
  via new `compile_cfg_with_pipeline` — the T3.x per-transform injection point),
  `DiffOutcome::{Match, Inconclusive, Mismatch{Compile,Result,Error,Log,Writes,
  RngDraws}}`, seeded memory mix (ROM 3000 NaN/±inf; block 10000 never filled/compared),
  budget-exceeded ⇒ inconclusive; interpreter gained `set_eval_budget` (distinct
  `EvalBudgetExceeded`) + `rng_draw_count` (draw-order preservation is part of the
  optimizer contract). Corpus differential: 270 cases/level, 0 mismatches. Fuzz:
  proptest 1.11 `fuzzgen.rs` (full pure-op set, dynamic temp indices Mod-clamped, float
  conds, And/Or with DebugLog args, bounded counter loops ≤5 trips; 53% trap-free),
  default 256 cases in CI (~3s); persistence MUST use `FileFailurePersistence::Direct`
  (proptest default silently broken in integration tests). Canary `Add(x,c)→x`: corpus 4
  mismatches; fuzzer catch ~20ms, shrunk to 1 block/9 nodes. **Gate fuzz recipe:
  `SONOLUS_FUZZ_CASES=1000000 cargo test --release -p sonolus-backend-core --test fuzz`
  (~13-24 min)**; orchestrator spot-ran 20k release cases green in 17s. T3.2/T3.3
  dispatch note: RewriteDriver leaves replaced defining insts scheduled — real rewrite
  passes MUST pair with a DCE/schedule sweep before lowering (`LowerError::MultiUse`
  otherwise); prefer wide-footprint canaries. Verified: cargo 249 green, clippy/fmt
  clean, pytest 1191+4 green.
- 2026-06-10 — **T2.2 done.** `passes/mod.rs`: `Pass` trait + plain ordered `Pipeline`
  runner; debug builds re-fingerprint MIR after each pass (lying changed-flag or missed
  invalidation panics — #[should_panic] pinned); `Hooks` for per-pass wall times (T2.4
  ready), lean release path. Level config: single ordered `registry()` with `Stage`
  (W1–W4) tags; `passes_for_level` takes the prefix; registry empty today so all three
  levels are callable and identity-equal — `tests/identity_levels.rs` pins fast/standard
  node trees byte-identical to minimal (one-constant relaxation for wave tasks) + 86
  vectors replay at all levels. `rewrite.rs`: `RewriteRule`/`RewriteCtx`/`Rewrite` +
  deterministic worklist driver (ascending arena order, first-rule-wins, `replaced`
  mask — a fold leaves a dead defining inst behind and would re-fire forever without
  it), hard cap (debug panic / release stop+log), D11 lazy boundary explicit
  (`Operand::Lazy` opaque unless `enters_lazy()`); toy rules in `pub mod toy` only.
  `effects.rs` (salvaged from the interrupted first attempt — kept, wired, 3 clippy
  fixes): effect kinds (pure/reads/writes/RNG/control) vs T2.1's liveness facts,
  pinned against the generated op table. NOTE for T3.1/T3.2: toy `FoldConstArith` uses
  plain f64 — real W1 folds must route through T1.1 `py_*` kernels (no-fold-on-error).
  Two 529-outage dispatch failures; completed on a retry with model override. Verified:
  cargo 227 green, clippy/fmt clean, both pytest lanes 1191+4 green.
- 2026-06-10 — **T2.1 done; T1.4 CI confirmed green** (rust-lane + python lane on
  82a3a7f — S1 fully closed). `analysis/` module: `Analyses` manager (lazy compute;
  `invalidate_cfg`/`invalidate_values`/`invalidate_all`; debug-build MIR fingerprinting
  panics loudly if a cached result is served after un-invalidated mutation — pinned by
  #[should_panic] tests). `dom.rs`: Cooper-Harvey-Kennedy idoms + frontiers (CHK
  entry-sentinel quirk documented: looping entry never in its own frontier — W2 phi
  placement note; low risk since Braun doesn't use DF). `loops.rs`: natural loops only;
  irreducible regions produce no loop (contract documented). `liveness.rs`: one worklist
  fixpoint over values AND temps; phi/lazy-ShortCircuit/terminator uses handled per D11;
  backward per-point cursor API. Corpus: 135 CFGs / 5,514 MIR blocks / 269 natural loops
  (max depth 3); randomized 1000-CFG dominator check vs naive O(n²) reference. Cleanup
  candidate noted: alloc.rs private bitset vs analysis::BitSet (when W2 touches
  alloc.rs). Verified: cargo 204 green, clippy/fmt clean, pytest 1191+4 green.
- 2026-06-10 — **T1.4 done.** Rust lane: `SONOLUS_BACKEND=rust` routes
  run_and_validate/run_compiled through encode → run_pipeline → Rust Interpreter.
  `RUST_OPTIMIZATION_LEVELS = ("minimal",)` — S2/S3 just append. RNG: tape recorded from
  the direct Python execution (uniform/randrange call-window) + seed `getrandbits(63)`
  drawn once per invocation (tape when non-empty, seed otherwise); tape exhaustion AND
  underconsumption both fail loudly (probe-draw check); randrange caveat documented
  (start=0/step=1 alignment — only direct form used; others would fail loudly). Guards:
  unknown backend value, missing extension (mandatory in lane), capture×rust mutual
  exclusion. Default lane token-identical. CI stage B: `rust-lane` job (Ubuntu/3.14,
  SONOLUS_BACKEND=rust, full pytest); `tests/**` added to rust.yml path filters
  (load-bearing — lane changes wouldn't trigger CI otherwise). Both lanes 1191 passed +
  4 skips locally (807 tests exercise the rust path); zero behavioral differences; rust
  lane is setstate-free (T6.1 direction confirmed viable). Follow-up noted:
  `rng_tape_remaining` getter on PyO3 Interpreter would replace the probe-draw trick.
  Verified: both lanes run by orchestrator; cargo/clippy/fmt clean; CI pending push.
- 2026-06-10 — **T1.3 done.** Minimal pipeline: decode → `mir.rs` (arena SSA-capable IR,
  phi instructions, eager schedule + lazy ShortCircuit per D11, strict binarization,
  faithful CoalesceFlow/UCE ports incl. the legacy single-edge-with-cond quirk — 46
  corpus instances bake it in) → `alloc.rs` (bitset liveness at TempBlock granularity;
  interference cliques at stores incl. dead stores — minimal does no DCE; deterministic
  first-fit by (−size, table idx); budget error text "Temporary memory limit exceeded")
  → `lower.rs` → T1.2 emit. `ssa.rs`: Braun construction + Boissinot out-of-SSA
  (edge-splitting from any Branch pred; union-find coalescing; parallel-copy
  sequentialization w/ scratch; 2000-case randomized property test vs parallel-semantics
  oracle) shipped as tested infra, engaged at W2 per D10. PyO3 `run_pipeline`/
  `run_pipeline_stats`. pydori budget: 300 callbacks, Rust max 147 slots vs legacy
  AllocateBasic-equivalent 1631 (totals 4,078 vs 50,220, ~12×); Rust ≤ legacy on every
  callback (structural: first-fit ≤ sum-of-sizes). Corpus: 86/86 vectors replay from
  frontend CFGs through compile_cfg(minimal). W2 contract noted: Mem2Reg must keep
  load-directly-before-user form for the lowering splice. Verified: cargo 158 green
  across targets, clippy/fmt clean, pytest 1191+4 skips green.
- 2026-06-10 — **T1.2 done.** `emit.rs`: exact finalize.py port (all five dispatcher
  forms; If(Equal) cond via IRConst path vs SwitchWithDefault conds RAW; iterative).
  `output.rs`: OutputNodeGenerator port — dedup key = Python dict equality (consts by
  numeric value, `5 == 5.0`, key-normalized `-0.0`→`0.0`, first encounter wins its tag;
  funcs by (op, child indices)); insertion order proven identical to recursive `_add`.
  Corpus schema v2: vectors link content-addressed POST-pass CFGs (captured between
  run_passes and the destructive cfg_to_engine_node); testdata now 135 CFGs / 86 vectors
  / 69 post CFGs / 4,899,901 B. Cargo replay test: all 86 vectors decode→emit→run with
  RNG tape; result/log/writes match (excl. temp block 10000). Live A/B pytest: 7
  callbacks × 3 levels, output dumps byte-identical on all 19 runnable combos; 4 skips
  pinned to a documented gap — **standard-level legacy passes (InlineVars) can put
  IRExpr in BlockPlace.index, outside encoding v1's domain**; product path only encodes
  frontend CFGs so this is corpus-replay-only; T2.x differential infra must use the Rust
  pipeline's own CFGs. Replay equality is Python `==` NaN-aware (`+0.0 == -0.0` allowed;
  3/3074 smoke vectors hit int-vs-f64 `-0.0` log divergence; no amplified e.g. Sign
  divergence anywhere). Verified: fresh capture 24,844+18,760 CFGs zero rejects all
  round-trip clean (subagent) + testdata regen byte-identical from capture, 410 files
  (orchestrator); cargo 98 green; clippy/fmt clean; pytest 1183 passed + 4 documented
  skips.
- 2026-06-10 — **T1.1 done.** `nodes.rs` (arena: `Const{value, is_int}` 16B nodes,
  contiguous args; `EngineNodes{arena, root}` is T1.2's product; iterative
  `format_engine_node`) + `interpret.rs` (explicit Frame/Action stack machine; central
  Break unwinding; evaluation-order fidelity incl. interleaved vs collect-then-check
  ensure_int, proved by side-effect-ordering tests). Counters documented: eval_count ≡
  legacy run() calls incl. consts; dispatch_count = non-tail JumpLoop round trips (tail
  not counted) — T2.4's Python metrics must replicate exactly. RNG: SplitMix64 seeded +
  tape mode (`RuntimeError("RNG tape exhausted")`). Errors are values in core; PyO3 maps
  to exact CPython-3.14 exception types/messages (verified differentially against the
  frozen interpreter; 3.14 changed several messages vs older versions). Documented
  divergences: complex pow → ValueError; pow overflow message; bare SwitchInteger float
  scrutinee (unreachable — finalize.py only emits If/SwitchWithDefault/
  SwitchIntegerWithDefault + Block(JumpLoop(…, 0-tail))); −0.0 normalization where
  Python's int-returning round/ceil/floor/trunc kill −0.0. `py_*` numeric kernels are
  pub for T3.1 SCCP reuse (fold-exactly-where-Python-folds). Verified: cargo 75 green
  (24 lib + 4 corpus + 43 interp + 4 ops), clippy/fmt clean, pytest 1153 green (+57).
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
