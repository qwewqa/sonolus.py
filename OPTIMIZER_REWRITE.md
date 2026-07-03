# Optimizer Rewrite Plan

> **Historical plan-of-record.** This is the pre-implementation design; it is kept
> as history. Some of it has been **superseded by M9 (2026-07-02): the per-callback
> thread pool was removed and builds are now serial** (the `nogil` annotations are
> retained only to document GIL-independence). Wherever this document presents
> multithreading / a thread pool as the plan, read it as superseded — see
> `M9_RESUME.md` and `REVIEWER_INFO.md` for the current design. The `CompilerPass`,
> `CompileCache`, and `hash_cfg` removals it describes did ship as planned.

Status: proposal (pre-implementation)
Scope: `sonolus/backend/optimize/*`, `sonolus/backend/finalize.py`, `sonolus/build/compile.py`,
`sonolus/build/engine.py` (thread-pool gate + `cache` parameter), `sonolus/build/dev_server.py` and
`sonolus/build/project.py` (cache plumbing removal), mechanical updates in
`sonolus/script/{project,debug}.py` and `sonolus/build/cli.py`, build system, tests.

## 1. Goals

1. **Maintainable**: fewer, stronger passes with clear contracts, replacing today's ~37-entry pipeline of
   interleaved repeated passes. One canonical IR form per pipeline stage instead of passes that must all
   tolerate both SSA and non-SSA input.
2. **Fast to compile**: the optimizer core is implemented in Cython over a flat, arena-allocated IR
   (C structs, integer ids), instead of Python object graphs with recursive tree hashing. The core is
   designed **multithreaded from the start**: passes run in `nogil` regions over per-callback-local
   arenas, so the existing per-callback thread pool gains parallelism on standard GIL builds (today it
   only pays off on free-threaded Python). Caveat kept honest by measurement: only the optimizer/emit
   segment parallelizes — frontend tracing (`callback_to_cfg`) stays Python under the GIL, and if it
   dominates wall time the pool gains little; the M0 baseline records the frontend/optimizer time split
   so this goal is measurable rather than assumed. Free-threaded (no-GIL) Python is explicitly *not*
   supported for now — `nogil` regions on regular builds provide the parallelism. The dev server's
   compiled-node cache (`CompileCache`/`hash_cfg`) is removed rather than ported — raw per-callback
   speed and parallelism replace it.
3. **Effective**: equal or better generated code, measured by emitted node count and dispatch count on the
   `pydori` regression project. Runtime-specific optimizations (n-ary associative ops, integer-switch
   dispatch, exit-block sharing, temp-memory packing) are retained and in some cases extended
   (new: if-conversion into runtime `If`-expression nodes). Behavior-preserving is the floor, not the
   ceiling: where the current optimizer has known coverage gaps, the rewrite closes them rather than
   porting the gap — e.g. constant folding today covers only 42 of the 84 foldable pure ops; §7.2.2
   extends it to all of them. Every such extension is gated by differential tests, and gaps noticed
   mid-implementation get the same treatment (fix + test, or record in §13 if deferred).
4. **Compatible at the boundary**: the frontend (`Context`/visitor) and the test oracle
   (`interpret.py`) are untouched. Python `BasicBlock`/IR objects remain the interchange format at the
   API boundary; the Cython core marshals in/out.

Non-goals:

- No changes to the frontend visitor, the IR the frontend emits, or `EngineNode`/output JSON format.
- No changes to language semantics (float behavior, `Random`, aliasing rules).
- Not attempting an e-graph/equality-saturation optimizer (see §12 Alternatives).

### 1.1 Decisions at a glance

Alternatives are documented in §12; this is the one concrete plan. Every load-bearing choice:

- **IR**: flat arena, **value-based SSA** mid-end, phi operands per incoming edge, pinned effect
  ordering; AoS instruction structs, interned consts/places; associative ops binary through the
  mid-end, n-ary from tree emission onward (§6.1, §7.4.3).
- **SSA construction**: Braun et al. on-the-fly with trivial-phi elimination; only size-1 temps
  promote; `UNDEF` → one shared never-written slot (§7.2.1).
- **Mid-end (standard)**: SCCP (const + ≤100-set lattice) → simplify/GVN → DCE → LICM (cost ≥ 4) →
  `rewrite_switch`; the SCCP/GVN/DCE core repeats at most once more, only if it (or cfg_cleanup)
  changed anything — LICM and `rewrite_switch` run once (§7.2).
- **If-conversion**: standard level only; strictly pure, must-fold arms; initial arm budget = folded
  arm-tree cost ≤ 8 by the §2 cost metric (§7.3).
- **Lowering**: split-all-critical-edges phi lowering + parallel-copy sequentialization + interference
  coalescing on the post-fold schedule (Boissinot deferred); treeify fold/duplicate/materialize by the
  §2 cost model; phi-free cleanup → RPO layout → `normalize_switch` → allocate → emit (§7.4–§7.6).
- **Allocation**: first-fit gap packing sorted by (−size, temp id); bump for `minimal`;
  try-bump-then-packing for `fast` (§7.5).
- **Emission**: behavior-preserving `finalize.py` port + idempotent re-flattening; arena hash-consing;
  `FunctionNode` at the boundary; `OutputNodeGenerator` stays Python (§7.6, §6.2).
- **Levels**: -O0 `minimal` = cfg_cleanup + bump; -O1 `fast` = one mid-end round + cheap alloc (dev
  default); -O2 `standard` = full pipeline (build default) (§7.0).
- **API**: opaque level sentinels; `run_passes` / `optimize_and_finalize` / `cfg_to_engine_node`;
  `CompilerPass` and `CompileCache`/`hash_cfg` removed (§5).
- **Threading**: per-callback thread pool on all builds; passes `nogil`; no free-threading support (§8).
- **Build**: setuptools ≥ 77 + Cython ≥ 3.1, minimal `setup.py`, classic `.pyx`, no pure-Python
  fallback; cibuildwheel wheels cp312–cp314 + compiling sdist (§9).
- **Numeric semantics**: fold kernels = runtime value semantics + the documented §7.2.2 exceptions;
  fold coverage = **all** pure data-independent ops (84 — double today's 42, adding `Sign`, `Trunc`,
  `Unlerp`/`UnlerpClamped`, `Judge`/`JudgeSimple`, 36 `Ease*`); `Op.Rem` = `math_impls`
  sign-of-dividend and `Op.Sign` zero/NaN behavior confirmed vs real runtime in M0; bitwise lattice
  equality; −0.0 preserved internally (§3, §7.2.2).
- **Testing**: dual-run suite at all levels, plus one extra full run at `standard` with
  `OptimizerConfig(mode=Mode.PLAY, callback="updateSequential")`; goldens regenerated once at M4;
  committed M0 baseline hard-gates effective node counts (§2), with compile times tracked against it
  as a report (§10).
- **Migration**: delete-first; M0 baseline/Rem fix → M1 delete+boundary+allocator → M2 mid-end (`fast`)
  → M3 `standard` → M4 integration+parity → M5 polish; no release tagged until M4 passes (§11).

## 2. Background: what the optimizer targets (runtime cost model)

The engine output is a flat, hash-consed node array (`{"value": n}` or
`{"func": op, "args": [indexes]}`). The **actual Sonolus runtime compiles this node tree to bytecode**:
a value node costs a push instruction; a function node costs an instruction plus non-trivial
per-instruction interpreter overhead. (`sonolus/backend/interpret.py` is a test-only tree-walking
oracle with the same semantics but not the same cost profile.) Key facts that shape the optimizer
(references: `sonolus/backend/interpret.py` for semantics, `sonolus/backend/finalize.py` for lowering):

- **Cost ≈ total bytecode instructions executed ≈ total nodes evaluated, operands included.**
  Per-instruction interpreter overhead is significant, so node count — function nodes *and* value/operand
  pushes — is the primary metric. Shared (deduped) nodes are *re-executed* per reference — hash-consing
  shrinks the artifact, it does not memoize execution. Avoiding recomputation requires materializing to a
  temp (`Set` once, `Get` at uses). The current `CSE`/`LICM` cost heuristic (const=1, get=3,
  instr=1+Σargs, threshold ≥ 4) already approximates this bytecode-instruction count and carries over
  largely unchanged, with the effective-cost refinement below.
- **The real runtime constant-folds runtime-constant expressions** during bytecode compilation: a pure
  function node whose leaves are constants and constant-index reads of `RUNTIME_CONSTANT_BLOCKS`
  (today `inlining.py`: RuntimeEnvironment, RuntimeUI, LevelData, LevelOption, EngineRom, ...) is
  evaluated once and becomes a plain constant — its **effective** executed cost is one push regardless
  of tree size. Materializing such an expression to a temp *defeats* this (a temp-memory read is not
  runtime-constant) and pays the write besides, so duplicating into every use is strictly better at
  runtime; hash-consing keeps duplicated trees from bloating the artifact. The test interpreter does
  **not** model this fold (it re-executes trees), so the effect is invisible to the dual-run suite,
  and raw node counts overstate the cost of runtime-constant trees. Wherever this plan says "the §2
  cost metric" (treeify, LICM threshold, if-conversion arm budget), cost means **effective cost**: a
  runtime-constant subtree counts as 1.
- **Control flow**: the whole callback is one `Block(JumpLoop(Execute_0, ..., Execute_n-1, 0))`.
  Each basic block is an `Execute` whose last argument evaluates to the next block index.
  **`JumpLoop` is the runtime's expected CFG representation**, and the runtime has dedicated fast paths
  for blocks whose terminator (the `Execute`'s last argument) is one of the recognized shapes:
  - a **constant** (unconditional jump),
  - an **`If` expression**: `If(test, t_idx, f_idx)`,
  - a **`Switch` variant**, where `SwitchInteger`/`SwitchIntegerWithDefault` (contiguous integer cases
    `0..k-1`) perform better than the other switch types (`SwitchWithDefault` is a linear scan).

  Emission must therefore keep terminators in exactly these recognized forms; the existing
  `finalize.py` lowering already does, and the rewrite preserves its behavior (§7.6).
- **Associative ops are n-ary**: `Add/Multiply/Mod/Rem` (and `And`/`Or`, short-circuit) fold their args
  left-to-right in a single operation. `Add(a,b,c,d)` is 1 function node; `Add(Add(Add(a,b),c),d)` is 3 —
  same operand pushes, two fewer instructions.
- **Memory**: numbered blocks with per-mode/per-callback `readable`/`writable` sets
  (`sonolus/backend/blocks.py`). Optimizer temps are packed into block 10000 (`TemporaryMemory`,
  4096 slots). NaN/±Inf constants are emitted as `EngineRom` reads by finalize.
- **Purity classes** (`sonolus/backend/ops.py`):
  - pure (`pure=True`): freely foldable/dedupable/reorderable, subject to FP-order rules below.
  - `Random`/`RandomInteger` (`pure=False, side_effects=False`): each evaluation is a fresh draw —
    never fold, dedup, hoist, or duplicate; **may** be deleted if the result is unused.
  - reads (`Get`/`GetShifted`/`GetPointed`, stack reads; `pure=False`): may not move across writes; may
    not be dedup'd across writes. Reads of blocks not writable in the current callback are effectively
    pure for that callback (this is how `InlineVars`/`CSE`/`LICM` justify motion today).
  - side-effecting (`Set*`, `Draw`, `Play`, `Spawn`, `ExportValue`, stack writes, ...): never delete,
    never reorder relative to each other or to reads they may alias.
  - Stream reads are pure (streams are immutable while readable).
- **FP ordering**: evaluation is strict left-to-right. Reassociation is only legal along the left spine
  (`(a+b)+c → Add(a,b,c)` preserves order; commuting `Add`/`Multiply` args does not). Only
  `Equal/NotEqual/Max/Min` are treated as commutative today (NaN caveats aside). The rewrite keeps
  exactly this policy.

## 3. Semantic contract (invariants the new optimizer must uphold)

Input (from `context_to_cfg`): a CFG of `BasicBlock`s; statements are shallow
`IRSet(BlockPlace, IRPureInstr|IRInstr|IRGet|IRConst)` three-address code over `TempBlock` virtual
registers, **plus bare side-effecting `IRInstr` statements** (`Op.Break` for return values,
`Op.StreamSet`, `Op.ExportValue`, ...), and `IRSet` values may themselves be effectful `IRInstr`s
(native calls wrap any non-pure op, e.g. `Random`, into `IRSet(place, IRInstr(...))`); `Op.Break`
additionally makes the block's fall-through unreachable (safe to ignore, optional to exploit);
`phis` empty; each block has `test: IRExpr` and outgoing edges
keyed by `cond: int|float|None` with shapes `{}` (exit), `{None}`, `{0, None}` (false/true),
`{c1..ck, None}`, or `{c1..ck}` **without a default edge** — generator state machines emit default-less
multi-way blocks, where a missing default means "jump to exit" (finalize implements this today;
`NormalizeSwitch` currently *asserts* a default exists for >2 cases, so the rewrite must handle the
shape explicitly rather than assert it away). Not SSA. `TempBlock`s come in size 0 (placeholders,
allocated to sentinel offset −1), size 1 (promotable to values), and size>1 (arrays accessed with
dynamic indexes, never promoted). `BlockPlace.block` may itself be a place/expression (pointer
dereference — finalize emits nested `Get`s), not just a static block id.

Output (consumed by finalize/emission): no SSA places or phis; all temps rewritten to
`BlockPlace(block=10000, offset=base+off)` within the 4096-slot cap (arrays contiguous); switch case
conds normalized (contiguous `0..k-1` where achievable); a single shared empty exit block; every block's
edge shape one of the legal forms above.

Correctness rules: the purity/aliasing/FP rules of §2; array (size>1) temps never SSA-promoted, never
CSE'd; array liveness follows today's rule exactly — a (single-slot) array write kills whole-array
liveness only when it is provably the **first write to that array on every path** (`is_array_init`),
and arrays are not live before any write; constant folding must match runtime numeric semantics
(round-half-to-even `Round`, `smath.frac` for `Frac`/`Mod`, division-by-zero → not-a-constant, NaN/Inf
preserved as values and lowered via ROM at emission), with the deliberate policy exceptions documented
in §7.2.2.

Known discrepancy to resolve in M0: **`Op.Rem` semantics**. `interpret.py` folds `Rem` with IEEE
`math.remainder` (round-half-even quotient) while the frontend's `math_impls._remainder` deliberately
uses sign-of-dividend semantics — and the symbol SCCP calls (`smath.remainder`) does not even exist, a
latent `AttributeError` proving that fold path has never fired. **Decision: the `math_impls`
sign-of-dividend definition is canonical**; M0 confirms it against the real runtime (revisit only if
the runtime disagrees), fixes `interpret.py` and the fold kernels to match, and adds the differential
test.

Two more oracle gaps to fix in M0 for the same reason (the oracle must be trustworthy before the
differential tests mean anything): (a) `interpret.py` evaluates `Op.Sign` as `math.copysign(1, x)`,
which returns ±1 for ±0 and +1 for NaN — if the real runtime matches JS `Math.sign` (0/−0/NaN map to
themselves), that is a second Rem-style discrepancy; confirm against the runtime and fix the oracle to
match. (b) `interpret.py` raises `NotImplementedError` for all 36 `Ease*` ops and `Judge`/`JudgeSimple`
— implement them (mirroring the Python reference bodies in `easing.py`/`bucket.py`) so the §10
fold-kernel differential tests can cover the ops newly folded by §7.2.2, and so dual-run tests using
easing/judgment work at all.

Determinism: all passes must be deterministic (the current code pays sorting costs to iterate sets
deterministically; the arena IR is index-ordered and deterministic by construction).

## 4. Runtime-specific optimizations retained (and their new homes)

| Today | Semantics to preserve | New home |
|---|---|---|
| `FlattenAssociativeOps` | Flatten left spines of `Add/Multiply/Mod/Rem` into n-ary nodes (only `args[0]`, preserving left-to-right FP order; `Mod`/`Rem` legal only because runtime folds left-to-right) | Tree emission (§7.4.3); binary through the mid-end, n-ary from tree emission onward. §7.6 emission also re-flattens (idempotent) so the export → `cfg_to_engine_node` path matches the fused path |
| `UnflattenAssociativeOps` | Existed only so tree-based CSE could see binary ops | Obsolete — IR is always binary until emission |
| `RemoveRedundantArguments` | Drop `+0`, `-0` (rhs), `*1`, `/1` (rhs); singleton collapse; `0-x → Negate(x)` | Algebraic rules in the simplify/GVN pass (binary form), re-checked during n-ary emission |
| `RewriteToSwitch` | `If (x == C)` chains → one multi-way block: move `C` onto the true edge (`cond=None → cond=C`, `cond=0 → None`), then splice chained same-test blocks | Dedicated `rewrite_switch` pass on SSA (§7.2.6) |
| `NormalizeSwitch` | Cases forming an arithmetic progression `a+i·b` → rewrite conds to `0..k-1` and test to `(test - a) / b` so finalize emits `SwitchIntegerWithDefault`; must additionally handle default-less blocks (§3), which today's assert rejects | `normalize_switch` in lowering (§7.4.5) |
| `CombineExitBlocks` | All empty exit blocks share one block (one shared `Execute(exit_index)` node) | `cfg_cleanup` canonicalization (§7.1/§7.4.5) |
| `NormalizeBlocks` | Coerce raw int block ids to the mode's `BlockData` so writability is known | Marshal-in resolves block ids against the mode's block table once (§6.2) |
| `Allocate` family | Interference-based packing into block 10000, larger-first, 4096 cap. (Today's overlap test never reuses gaps — it bumps past interfering neighbors, making `Allocate` ≈ `AllocateFast`.) Bump allocator for O0; O1 = try-bump, fall back to `AllocateFast` | `allocate` in lowering (§7.5), upgraded to true first-fit |
| `InlineVars` duplication rules | Today: *bare reads* with constant block+index duplicate freely regardless of writability (alias path); pure instruction trees over `RUNTIME_CONSTANT_BLOCKS` reads also duplicate; and aggressive-inline + CSE(cost ≥ 4) leaves cheap multi-use pure exprs duplicated | Treeify cost model (§7.4.1) — deliberately stricter on writable-block reads, deliberately more aggressive on runtime-constant trees (duplicate at any size; effective cost, §2) |
| SCCP set-of-constants lattice | Branch-correlated small constant sets (≤100) prune switch edges | Kept in the new SCCP (§7.2.2) |

## 5. New architecture overview

```
frontend (Python, unchanged)
  └─ BasicBlock CFG (Python objects)
        │  marshal in (Cython, GIL held)
        ▼
  Arena IR  ──(passes, nogil, per-callback-local)──►  optimized Arena IR
        │                                                   │
        │ export back to BasicBlocks                        │ lower + emit
        ▼ (tests, visualize_cfg, goldens)                   ▼
  Python BasicBlock CFG                            EngineNode tree (FunctionNode)
                                                            │
                                              OutputNodeGenerator (Python, unchanged)
```

- New compiled package: `sonolus/backend/_opt/` (Cython `.pyx`/`.pxd`).
- `sonolus/backend/optimize/` (all pass modules, `passes.py`, `optimize.py`) and the body of
  `finalize.py` are **deleted** and reimplemented. `flow.py` survives (trimmed) as the boundary:
  `BasicBlock`, `FlowEdge`, `cfg_to_text`, `cfg_to_mermaid` are used by the frontend, tests, and goldens.
  `interpret.py` survives untouched (test oracle).
- `compile_mode` moves to a Cython module keeping `thread_pool` and `validate_only`; the `cache`
  parameter, `CompileCache`, and `hash_cfg` are removed. The `no_gil()` gate in
  `sonolus/build/engine.py` is dropped: the thread pool is used on **all** builds, since the optimizer
  releases the GIL for the heavy work (marshal in → optimize (nogil) → emit). Shared sinks
  (`OutputNodeGenerator`, result dicts) stay lock-guarded as today.

### Public API (compatibility)

Pass *pipelines-as-values* are replaced by opaque optimization levels:

```python
# sonolus/backend/optimize/__init__.py (thin Python shim over _opt)
class OptimizationLevel: ...          # opaque sentinel
MINIMAL_PASSES = OptimizationLevel("minimal")   # -O0
FAST_PASSES = OptimizationLevel("fast")         # -O1
STANDARD_PASSES = OptimizationLevel("standard") # -O2

def run_passes(entry: BasicBlock, level, config: OptimizerConfig, *, allocate: bool = True) -> BasicBlock
def optimize_and_finalize(entry: BasicBlock, level, config) -> EngineNode   # fused fast path
def cfg_to_engine_node(entry: BasicBlock) -> EngineNode   # emit only, no passes (marshal-in → emit);
                                                          # conftest/goldens emit from an already-
                                                          # optimized CFG and must not re-run passes
```

Kept working with mechanical updates only:

- `tests/script/conftest.py` and `tests/regressions/test_project.py`: `run_passes(cfg, level, OptimizerConfig(...))` unchanged in shape; import paths update mechanically (today: `sonolus.backend.optimize.optimize`/`.passes`; `project.py` imports the `optimize` module object; `debug.py` imports `RenumberVars` from `.simplify`).
- `BuildConfig.MINIMAL_PASSES/FAST_PASSES/STANDARD_PASSES` and `BuildConfig.passes` (`sonolus/script/project.py`): now hold level sentinels.
- CLI `-O0/-O1/-O2` mapping (`sonolus/build/cli.py`): unchanged.
- `visualize_cfg` (`sonolus/script/debug.py`): currently slices `*PASSES[:-1]` *intending* to skip
  allocation (for `fast` the slice actually drops a trailing `CoalesceFlow` and keeps allocation) —
  replaced by `run_passes(..., allocate=False)` plus deterministic renumbering on export. Deliberate
  behavior change: `fast` visualizations now show pre-allocation temps.

Breaking (decided, called out in changelog): the `CompilerPass` subclassing API and user-supplied pass
sequences are removed — `BuildConfig.passes` accepts only level sentinels. Also removed:
`compile_mode`'s `cache` parameter, `CompileCache`, `hash_cfg`, and the dev server's compiled-node
caching (compilation is cache-free; parallelism and the fast core replace it).

## 6. The arena IR

One `Func` arena per callback, with no global mutable state (op metadata and similar tables are static
`const`) — per-callback threading is safe by construction, and lifetimes stay simple.

### 6.1 Data layout (Cython structs, integer ids everywhere)

```
Func:
  instrs:   Instr[]       # one array; order within block = schedule for effectful ops
  args:     u32[]         # operand pool: value ids, slices referenced by instrs
  blocks:   BlockInfo[]   # first/last instr, term kind, edge slice, RPO number, idom, ...
  edges:    Edge[]        # (src, dst, cond_kind, cond_value) — arrays, not Python sets
  consts:   f64[] + hash table (interned; canonical int/float unification like IRConst)
  places:   PlaceInfo[]   # interned: kind (temp scalar/array/size-0 | real block | dynamic),
                          # block = id, temp id, OR value id (pointer-deref places compute their
                          # block; finalize emits nested Gets), index value id, offset;
                          # writability bit resolved once against config.mode/callback at marshal-in
  temps:    TempInfo[]    # name id, size (for export naming + allocation)
  names:    Python str list (only touched at marshal boundaries)

Instr (AoS, ~24–32 bytes):
  op:        u16          # extended opcode space: all runtime Ops + PHI, CONST, GET, SET, PARAM...
  flags:     u8           # pure / side_effect / pinned / dead ...
  block:     i32
  arg_start: i32, nargs: i16
  aux:       i32          # const table idx, place idx, etc.
```

- **Value-based SSA** is the mid-end form — chosen on merit, not for continuity with today's
  `ToSSA`/`FromSSA` (there is no requirement to keep SSA or its current format at all; the workhorse
  passes are just materially simpler and faster on it: SCCP is *defined* sparsely over SSA edges,
  GVN reduces to hashing `(op, value-ids)`, DCE falls out of use counts, and the treeify cost decisions
  need exactly the def/use structure SSA maintains for free — see §12 for the non-SSA alternatives
  considered). The format freely diverges from today's: an instruction *is* its value (id = instr index);
  `IRGet`/`IRSet` on size-1 temps disappear into def/use edges (index on a size-1 temp is ignored,
  matching today's `ToSSA`). Phis are instructions at block heads with **operands keyed per incoming
  edge**, not per predecessor — parallel edges between the same block pair with different conds are
  legal, and SCCP executability and out-of-SSA splitting are per-edge. Verified invariant (checked by
  the debug-build `verify()`): parallel edges from the same predecessor carry **equal** phi operands —
  the value at a predecessor's exit is unique, and the Python `BasicBlock.phis` export format (keyed
  per pred block) can only represent that case, so export normalizes per-pred. Memory ops (real blocks, size>1
  temp arrays, `*Shifted`/`*Pointed`) remain explicit `GET`/`SET`-class instructions, "pinned" to
  program order along with all effectful ops.
- **`UNDEF` policy** (reads of never-written scalars — reachable in practice, e.g. provably-dead
  `VarArray[Num, 1]` accesses): a dedicated `UNDEF` value; Braun trivial-phi elimination treats
  `phi(UNDEF, v) = v`; a surviving live `UNDEF` lowers to one shared never-written temp slot, exactly
  like today's `SSAPlace("err", 0)` sentinel — deterministic and documented.
- **Op metadata** (`pure`/`side_effects` bitflags, arity class) is a generated static C table.
  A checked-in `_ops_gen.pxd` is produced by a small generator script from `ops.py`, with a unit test
  asserting the two are in sync (regenerate via `python tools/gen_ops.py`).
- Hashing an instruction for value numbering is O(1) over `(op, args...)` ints — this removes the single
  biggest compile-time hotspot of the current design (recursive Python tree `__hash__`/`__eq__`).

### 6.2 Marshalling

- **In** (GIL held): walk the Python `BasicBlock` graph once; intern constants/places; expand statement
  trees into instructions (the frontend emits shallow trees, so this is nearly 1:1; any n-ary
  associative instr in the input is defensively binarized — the IR stays binary until emission);
  resolve raw int block ids against `config.mode.blocks` and precompute per-place writability
  (subsumes `NormalizeBlocks`). Writability matches today's semantics exactly: a resolved `BlockData`
  place is writable iff `config.callback in block.writable` — so `callback=None` (the dual-run suite's
  `OptimizerConfig()` and `visualize_cfg`) treats every resolved block as **read-only**, preserving the
  aggressive read-motion coverage those paths exercise today; unresolvable raw-int blocks
  (`mode=None`) are conservatively writable, as today (they never become `BlockData`). Validate the §3
  input shapes (bare `IRInstr` statements, effectful `IRSet` values, default-less multi-way blocks);
  reject anything else with a clear error rather than miscompiling.
- **Out** (GIL held): two exporters —
  1. `to_basic_blocks(func)` → Python `BasicBlock`/IR objects (tests, goldens, `visualize_cfg`), with
     deterministic renumbered temp names (subsumes `RenumberVars` in role, not byte-for-byte: today's
     pass numbers from `v1` and renames only size-1 `IRSet`-target temps, leaving the `err` sentinel
     and arrays untouched — the new exporter renumbers uniformly; cosmetic, goldens regenerate).
  2. `to_engine_node(func)` → `FunctionNode` tree with intra-callback hash-consing done on the arena.
     Cross-callback dedup stays in the existing Python `OutputNodeGenerator` (cheap; node trees are tiny
     relative to the IR).
- Round-trip property (`to_basic_blocks(marshal_in(cfg)) ≡ cfg` modulo normalization) is a tested
  invariant (§10).

## 7. Pass pipeline

Design principles: each pass runs at a fixed place, once or twice (change-driven repeat of the cheap
mid-end round instead of today's hand-scheduled ping-pong); analyses (dominators, loops, liveness) are
computed where needed and explicitly invalidated — no `requires()/preserves()` framework, just function
calls in a fixed driver (the current framework's flexibility is unused in practice: nothing ever
preserves anything).

### 7.0 Levels

| Level | Pipeline |
|---|---|
| `minimal` (-O0) | cfg_cleanup → bump allocation. Exists for tests/debugging only. |
| `fast` (-O1, dev default) | cfg_cleanup → SSA → one mid-end round (SCCP + simplify/GVN + DCE) → lowering with cheap allocation (bump, fallback to interference packing on overflow). Rationale: much better code than today's `FAST_PASSES` at compile times expected to remain lower with the Cython core — verified against the M0 compile-time baseline rather than assumed (frontend tracing stays Python and may dominate); dev-server iteration speed is the priority. |
| `standard` (-O2, build default) | full pipeline below; the mid-end core runs a second round only if the first changed anything (§7.2.7); LICM + `rewrite_switch` + if-conversion enabled (`normalize_switch` runs at fast too, §7.4.5); interference allocation. |

### 7.1 CFG cleanup (`cfg_cleanup`, run at all levels, repeated after mid-end)

Worklist-based, subsumes `CoalesceFlow` + `UnreachableCodeElimination` + `CombineExitBlocks` +
`CoalesceSmallConditionalBlocks`:

- fold constant tests → retarget to the single live edge; drop unreachable blocks/edges (+ phi args);
- thread edges through empty blocks; dedup parallel edges; merge single-pred/single-succ chains;
- bounded tail-duplication of tiny blocks (≤1 statement) into predecessors to expose threading —
  **pre-SSA only** (duplicating defs on SSA would require phi repair; today's
  `CoalesceSmallConditionalBlocks` likewise refuses SSA). The mid-end repeat of cfg_cleanup runs the
  phi-safe subset only (constant-test folding, edge threading, empty-block removal, phi-aware merges);
- canonicalize: one shared empty exit block.

### 7.2 Mid-end (SSA)

1. **SSA construction** — Braun et al. (CC'13) on-the-fly construction with trivial-phi elimination
   (no dominance frontiers needed; simpler and cheaper than the current Cytron implementation, and the
   frontend's mostly-fresh temps make it near-linear). Only size-1 temps promote; arrays and real-block
   accesses stay as pinned memory ops. Undefined reads get an explicit `UNDEF` value (mirrors the
   `SSAPlace("err", 0)` sentinel today).
2. **SCCP** — Wegman–Zadeck sparse conditional constant propagation, keeping the current extension:
   lattice `⊤ → const → small-set(≤100) → ⊥`, per-edge executability, edge pruning for set-valued
   switch tests. Lattice equality is **bitwise** (canonical NaN) — today's `!=` comparison on floats
   makes a NaN flowing through a loop phi re-enqueue forever, a latent nontermination the C port must
   not copy. The const table preserves `-0.0` as distinct internally (today's `IRConst` interning
   collapses `-0.0` → `0` at construction; fold kernels must not introduce new collapses mid-fold).
   Constant folding uses one shared kernel reimplemented in C, following runtime value semantics with
   today's **documented policy exceptions** (deliberate, matching current behavior and the engine's
   FP tolerance): `Multiply` with a constant-0 arg folds to 0 even though other args could be NaN/Inf;
   `And`/`Or` short-circuit folds assume boolean (0/1) operands — the runtime returns the first
   falsy/truthy *value* (`And(2,3) = 3`), so these folds apply only where operands are known boolean,
   else fold strictly by value semantics. The differential tests encode these exceptions explicitly.
   (`Rem` kernel per the §3 decision.)
   **Fold coverage extends to all pure, data-independent ops** — today's `SUPPORTED_OPS`
   (`constant_evaluation.py`) covers 42 and silently treats the other 42 as not-a-constant: `Sign`,
   `Trunc`, `Unlerp`, `UnlerpClamped`, `Judge`/`JudgeSimple`, and the 36 `Ease*` ops. The new kernel
   table covers all 84, mirroring the existing Python reference implementations (`easing.py`,
   `bucket.py`, `math_impls`; `Sign` per the §3 confirmation). Pure but environment-dependent ops
   (`BeatTo*`/`TimeTo*`, `Has*`, stream reads) participate in GVN but are never constant-folded.
3. **Simplify + GVN** (the workhorse; subsumes `CSE`, `RemoveRedundantArguments`, most of `InlineVars`) —
   dominator-scoped hash-based value numbering with an integrated algebraic rewriter, RPO worklist:
   - identities: `x+0`, `x-0`, `x*1`, `x/1`, `0-x → neg x`, `not(not(b))→b` (bool-typed only), constant
     comparisons, `Min/Max` idempotence, etc. (binary form);
   - canonicalization: commutative arg ordering for `Equal/NotEqual/Max/Min` only (by value id — O(1),
     replacing today's stringified sort keys);
   - GVN via dominator-tree scoped table; **plain `Get`s with a direct static block id** that is
     non-writable-in-this-callback participate (with GVN'd index); `Random`, writable-block reads, and
     all `GetPointed`/`GetShifted`/dynamic-block reads never do (`GetPointed`'s place block only holds
     the pointer — the dereferenced target is dynamic and may be writable).
   Note: GVN *unifies* values; whether a multi-use value is worth a temp is decided later by treeify
   (§7.4), so unification is always safe here.
4. **DCE** — use-count/worklist mark from roots (block tests + side-effecting instrs); deletes unused
   pure defs and unused `Random`s; drops dead phis. Subsumes today's `DeadCodeElimination` on SSA;
   `AdvancedDeadCodeElimination`'s post-SSA role (self-copies and stores made dead by phi lowering and
   coalescing) is covered instead by the explicit cleanup inside lowering (§7.4.4).
5. **LICM** — loop forest from dominators + back edges; hoist pure, loop-invariant, guaranteed-to-execute
   (dominates all latches) values with cost ≥ 4 into preheaders (today's CSE/LICM threshold, now on
   **effective** cost per §2 — runtime-constant values have effective cost 1 and are never hoisted;
   today's LICM, unaware of runtime constants, hoists them into temps and blocks the runtime's own
   folding). Direct hoisting of the value — no more "insert a copy and hope CSE dedups it".
6. **`rewrite_switch`** — as today (§4): equality-chain → multi-way block, then splice same-test chains.
7. Repeat 2–4 (and the phi-safe cfg_cleanup subset) once if **SCCP/GVN/DCE/cfg_cleanup** made a change
   (fixed bound, no fixpoint pass scheduling). LICM and `rewrite_switch` deliberately run once — same
   single-shot property as today's pipeline.

### 7.3 If-conversion (new, standard level — runs on SSA, after the mid-end, while phis still exist)

- Convert diamonds/triangles into a single `If`/`Switch`
  *expression* feeding the phi (a select). The payoff is removing whole `Execute` blocks and `JumpLoop`
  round-trips for the ternaries/short-circuits the frontend lowers to CFG today. Legality is strict, and
  the restriction comes from IR representability, not from runtime `If` semantics (the runtime `If`
  does lazily evaluate only the taken arm — but our flat pinned schedule cannot express pinned/effectful
  ops *under* an expression, so anything merged into the joined block executes unconditionally):
  - arms may contain **strictly pure instructions only** — no side effects, no memory reads of any
    kind (effectively-pure reads of non-writable blocks are unpinned in this design, but a speculated
    guarded read like `arr[i] if i < n else 0` would still fault the oracle's bounds assert), and no
    `Random` (draw count must not change);
  - arm values must be **guaranteed to fold** into the select's arm trees: single-use, within the arm
    budget, and marked "must-fold" — an invariant treeify (§7.4) honors. Otherwise a materialized arm
    temp would evaluate on both paths (e.g. `a/b if b != 0 else 0` speculating the division faults the
    interpreter oracle even though the real runtime tolerates it);
  - matched shapes: `{0, None}` diamonds/triangles, **`{C, None}` two-way blocks** (which
    `rewrite_switch` has already produced from `Equal(x, C)` tests by this point — a primary motivating
    case, emitted as `If(Equal(test, C), ...)`), and small multi-way blocks → `Switch` selects.
  (This also naturally covers `And`/`Or` reconstruction.) Initial arm budget: folded arm-tree cost ≤ 8
  per arm by the §2 cost metric (effective cost — runtime-constant arms are nearly free) — tuned
  against pydori metrics at M4, but this is the starting value.

### 7.4 Out-of-SSA + treeify (subsumes `FromSSA`, `CopyCoalesce`, `InlineVars(aggressive)`, `FlattenAssociativeOps`)

1. **Scheduling decision per SSA value** (cost model, replaces `InlineVars`). Use positions are defined
   precisely: a block-`test` use sits at the **end** of its block (terminator position); a phi operand
   is a use at the **end of the corresponding predecessor edge**. Rules:
   - fold into the consumer tree: single-use pure values (cross-block only if not sinking into a deeper
     loop, mirroring `crosses_loop`); single-use pinned reads may fold only if there is no effectful
     instruction between def and use position **on any path** (for a `test` use that means to the end of
     the block), and never across a loop back edge (re-reading each iteration would cross the body's
     writes);
   - duplicate at every use, no temp: decided by cost comparison on **effective cost** (§2) —
     duplication wins when `dup_cost × uses < set_cost + get_cost × uses`. Runtime-constant trees
     (today's `is_runtime_constant`: pure ops over constants + constant-index reads of
     `RUNTIME_CONSTANT_BLOCKS`) have effective dup_cost 1, so they duplicate **regardless of tree
     size** — a temp would be a barrier to the runtime's own constant folding (§2). This improves on
     today: aggressive `InlineVars` duplicates these trees, but `CSE` and `LICM` are unaware of
     runtime constants and re-extract/hoist cost-≥ 4 trees back into temps, pessimizing the real
     runtime. Duplication also covers constants, bare constant-index reads of callback-read-only
     blocks, and cheap multi-use pure exprs (cost < 4 stays duplicated today because aggressive
     `InlineVars` duplicates everything and CSE only re-extracts cost ≥ 4);
   - otherwise: materialize to a temp.
   Deliberately stricter than today in one place: constant-index reads of *writable* blocks are no
   longer duplicated (duplication across effects can observe intervening writes); expect small
   node-count deltas both ways, checked against the M0 baseline.
2. **Phi elimination**: split critical edges (per-edge, handling parallel edges); lower phis to parallel
   copies; sequentialize with the standard temp-breaking for cycles. Values marked "must-fold" by
   if-conversion (§7.3) are honored here as an invariant. (Replaces `FromSSA`; simple correct version
   first — Boissinot-style out-of-SSA is a possible later refinement, §12.)
3. **Tree emission**: rebuild statement trees for each block per the step-1 decisions; flatten
   `Add/Multiply/Mod/Rem` left spines into n-ary instructions during emission and re-apply n-ary
   identity dropping (`RemoveRedundantArguments` semantics, including recursion into impure instrs'
   args — fixing the current bug where `IRInstr` args are never visited).
4. **Coalesce + cleanup on the final schedule**: compute liveness/interference **after** folding and
   tree emission — folding moves operand uses to the consumer's position, so interference computed on
   pre-fold SSA positions would let a coalesced phi-web temp clobber a folded operand (today this
   ordering hazard can't arise because `InlineVars` rewrites trees before `LivenessAnalysis`/
   `CopyCoalesce` run; the plan keeps that property explicit). Coalesce phi-copy webs and copy-related
   temps (replaces `CopyCoalesce`), then delete self-copies and dead stores exposed by coalescing
   (today's `AdvancedDeadCodeElimination` + `Allocate`'s built-in dead-store removal). The same
   final-schedule liveness feeds allocation (§7.5).
5. **Final CFG canonicalization**: edge splitting (step 2) and coalescing (step 4) leave empty
   jump-only blocks, so a phi-free `cfg_cleanup` runs here — mirroring today's
   `FromSSA → CoalesceFlow → CopyCoalesce → ADCE → CoalesceFlow` tail; skipping it would regress node
   counts on every loop back-edge and phi merge. Then block layout in RPO (stable emission order), then
   **`normalize_switch`** — arithmetic-progression case normalization (§4), handling default-less
   blocks (missing default = exit). `normalize_switch` runs at **both fast and standard** (it is cheap
   and multi-way blocks exist even without `rewrite_switch`, e.g. generator dispatch). It runs strictly
   **after all cleanup**: parallel-edge dedup after normalization could remove a case edge and break
   the contiguous `0..k-1` gate, silently downgrading `SwitchIntegerWithDefault` to a linear
   `SwitchWithDefault` (today's pass order has the same no-cleanup-after-normalize property).
   Allocation (§7.5) and emission (§7.6) follow.

### 7.5 Allocation (`allocate`)

Same algorithm family as today, in C: liveness via per-block bitsets over temp ids (backward dataflow
over the **final post-fold schedule**, shared with §7.4.4; the array-init forward pass preserved
exactly — a single-slot array write kills whole-array liveness only when it is provably the first write
on every path, and arrays are not live before any write); interference from live sets; packing sorted
by (−size, temp id) — deterministic without touching Python strings, so the pass stays nogil (goldens
regenerate anyway); arrays contiguous; size-0 temps get sentinel offset −1; 4096-slot cap with a clear
error. Today's packer never actually reuses gaps (its overlap test bumps past interfering neighbors,
making `Allocate` ≈ `AllocateFast`); the rewrite implements true first-fit gap packing — a likely
slot-count improvement, but baseline comparisons should expect different offsets. O0 uses the bump
allocator; O1 tries bump then falls back to interference packing. The interference builder drops
today's O(|live|²)-per-statement set updates for incremental bitset operations.

### 7.6 Emission

Deliberately a behavior-preserving reimplementation of `finalize.py`: RPO `Execute` nodes wrapped in
`Block(JumpLoop(...))`, terminators kept in the runtime-recognized shapes (constant index /
`If(test, t, f)` / `SwitchIntegerWithDefault` when cases are contiguous `0..k-1`, else
`SwitchWithDefault`), NaN/Inf via ROM reads, int demotion of integral floats. The runtime pattern-matches
these exact terminator forms for its CFG fast paths (§2), so emission is not a place for creativity —
implemented on the arena with integrated hash-consing, exporting `FunctionNode` at the boundary.

One deliberate addition over today's `finalize.py`: emission **re-flattens associative left spines**
(idempotent — already-n-ary trees pass through). This makes the two emit paths agree byte-for-byte:
the fused `optimize_and_finalize` path emits from n-ary trees, while the test/golden path
(`run_passes` → export → `cfg_to_engine_node`) round-trips through marshal-in, which binarizes (§6.2) —
without re-flattening, that path would emit `Add(Add(a,b),c)` where the real build emits `Add(a,b,c)`,
and the `_nodes` goldens would not reflect shipped output.

## 8. Cython implementation notes

- Modules (keep the count small; heavy `cimport` graphs slow builds):
  `_opt/ir.pyx` (arena, interning, marshal in/out), `_opt/analysis.pyx` (dominators — CHK iterative,
  loop forest, liveness bitsets, use counts), `_opt/midend.pyx` (SSA, SCCP, simplify/GVN, DCE, LICM,
  switch passes), `_opt/lower.pyx` (if-conversion, out-of-SSA, treeify, allocate, emission),
  `_opt/driver.pyx` (levels, `run_passes`, `optimize_and_finalize`, `compile_mode`).
- Directives: `boundscheck=False, wraparound=False, cdivision=True, language_level=3` in release;
  a debug build flag (env `SONOLUS_OPT_DEBUG_BUILD=1` at build time) keeps bounds checks + asserts.
- All arena memory via a simple growable buffer allocator owned by `Func`; no per-node allocations;
  everything freed in one place. No global mutable state → thread-safe by construction. Passes run in
  `nogil` regions (marshal in/out holds the GIL), which is what makes the per-callback thread pool
  effective on GIL builds. Free-threaded Python is out of scope for now (no ft wheels, no
  `freethreading_compatible` directive); the no-global-state design keeps that door open later.
- Numeric kernels: constant folding implemented against C doubles with explicit mirrors of
  `smath.frac`/`smath.remainder`/round-half-even; shared between SCCP and simplify.
- Debuggability: `SONOLUS_OPT_TRACE=1` dumps `cfg_to_text` of the exported CFG after each pass; each pass
  has an internal `verify(func)` (edge shape, def-before-use, phi arity) enabled in debug builds.

## 9. Build system and CI

Per current ecosystem practice (hatch-cython is unmaintained; meson/scikit-build are overkill for a few
self-contained `.pyx`):

- Switch `build-system` to `setuptools>=77` + `Cython>=3.1` with a minimal `setup.py` containing only
  `cythonize()`/ext_modules; all metadata stays in `pyproject.toml`. `[tool.hatch.*]` removed; its
  `packages = ["sonolus"]` is replaced by an explicit `[tool.setuptools.packages.find]`
  (`include = ["sonolus*"]`) — flat-layout auto-discovery with `tools/`, `rust/`, `docs/`, and
  `tests/` at the repo root is too fragile to rely on.
- **No pure-Python fallback** (recommendation): a single implementation is the maintainability win this
  rewrite is for, and the old Python optimizer is being deleted. Installation coverage comes from wheels:
  cibuildwheel (GitHub Actions matrix: linux x86_64/aarch64 via native ARM runners, windows, macOS
  arm64/x86_64) for cp312/cp313/cp314 (no free-threaded wheels for now), plus an sdist that cythonizes
  at build time (ship `.pyx`, not generated `.c`). Add cp315 wheels and a py315 tox env when Python
  3.15 releases (October 2026 — likely mid-rewrite; the sdist covers the gap, contingent on Cython
  supporting 3.15 by then). musllinux wheels stay enabled (cibuildwheel default, negligible cost);
  windows-arm64 wheels are skipped for now (sdist covers it). If an unsupported-platform need appears
  later, the aio-libs pattern (env-var opt-out + universal wheel) can be added.
- Dev workflow: `uv sync` builds the extension via PEP 517; add
  `[tool.uv] cache-keys = [{file="pyproject.toml"}, {file="setup.py"}, {file="sonolus/**/*.pyx"},
  {file="sonolus/**/*.pxd"}]` so uv rebuilds on `.pyx`/`.pxd` edits (the checked-in `_ops_gen.pxd`
  included). Document MSVC requirement for Windows contributors.
- CI: the repo has a single `publish.yaml` (`on: push`) whose `build` job runs tox **and** `uv build`,
  and whose tag-gated `publish` job downloads that same run's artifact — after the Cython switch,
  `uv build` would produce one linux-x86_64 wheel, i.e. a broken release for every other platform. So
  the release flow is rewired: the `build` job keeps running tests (compilation happens implicitly via
  `uv sync`); a cibuildwheel matrix + sdist job replaces `uv build` as the artifact producer on tags;
  the `publish` job consumes those artifacts (same trusted-publishing setup).

## 10. Testing and benchmarking

Existing safety nets (kept, minimally adapted):

- **Dual-run differential suite** (~830 tests, `tests/script/conftest.py`): Python-executed reference vs
  compiled+optimized+interpreted at all three levels, including debug-log parity and exception parity.
  This is the primary correctness gate; it needs only the level-sentinel import change. Coverage note:
  its `OptimizerConfig()` (no callback) treats resolved blocks as read-only (§6.2), which is what
  exercises the read-motion/GVN/duplication machinery — additionally run the full suite once more at
  `standard` with `OptimizerConfig(mode=Mode.PLAY, callback="updateSequential")` so the conservative
  writable path is exercised too.
- **Golden regressions** (`tests/regressions/`, pydori): `_cfg` files (pre-optimizer) must be
  byte-identical before/after the rewrite (frontend untouched — a strong no-regression check on the
  boundary). `_optimized_cfg`/`_nodes` files are deleted and regenerated once the new pipeline lands;
  the diff itself is review material.

New:

- **Baseline capture before deletion** (important since we delete first): a script records, for every
  pydori callback at fast/standard: emitted node counts (function nodes and value nodes separately —
  both cost bytecode instructions in the real runtime) **and effective node counts** (each
  runtime-constant subtree counted as 1, per §2 — the number that tracks what the runtime actually
  executes after its own constant folding), per-op counts, temp slots used, and
  wall-clock time split into frontend tracing vs optimize vs emit with the *current* optimizer; saved
  as JSON (e.g. `tests/regressions/data/baseline_v0.16_metrics.json`, committed). The new optimizer
  must meet or beat **effective** node counts — that is the hard M4 gate (raw counts are reported
  alongside; deliberate duplication of runtime-constant trees raises raw counts by design and must
  not read as a regression); compile times are tracked against the
  baseline as a report, expected to improve but not hard-gated in CI (machine variance, and the Python
  frontend dominates part of the time). A metrics test regenerates the same JSON for comparison, and
  the frontend/optimizer split keeps the parallelism goal (§1) honest.
- **Arena/pass unit tests**: a `_opt.debug` API to run individual named phases
  (`_opt.debug_run(cfg, phases=["sccp"]) -> BasicBlock`) so passes get focused tests (the current passes
  have none — only end-to-end coverage).
- **Marshal round-trip** property test (import→export identity modulo normalization).
- **Fold-kernel differential tests**: Hypothesis-generated operands per op, asserting the C constant
  folder matches `Interpreter.run` bit-for-bit (NaN/Inf/−0.0 included), covering **every foldable op**
  — including the 42 newly folded by §7.2.2 — with the §7.2.2 policy
  exceptions encoded explicitly, plus a test covering constant *interning* end-to-end (the −0.0
  collapse lives in `IRConst` interning, not the kernel — kernel-only tests would miss it).
  Prerequisite (M0): resolve the `Op.Rem` and `Op.Sign` discrepancies and add the missing
  `Ease*`/`Judge`/`JudgeSimple` oracle implementations (§3) so there is a single semantics to test.
- **Random CFG property tests**: Hypothesis generator for small CFGs over scalar/array temps with
  branches/loops; assert interpretation of unoptimized vs optimized (all levels) matches on outputs and
  memory (programs excluding `Random`, which legally changes draw counts under DCE).
- **Compile-time benchmark**: `tools/bench_compile.py` timing pydori full builds (fast/standard) —
  run manually and in CI as an informational job.

## 11. Implementation milestones

Each milestone ends with the full suite green (`pytest -n 32`).

- **M0 — Baseline + scaffolding**: metrics/baseline capture script + committed baseline JSON (node
  counts + frontend/optimize/emit time split + a **warm-cache dev-server rebuild timing**, the bar
  `CompileCache` removal must meet); resolve the `Op.Rem` and `Op.Sign` semantics discrepancies (§3) —
  confirm against the real runtime, fix `interpret.py`/`math_impls` accordingly, and implement the
  missing `Ease*`/`Judge`/`JudgeSimple` ops in `interpret.py` (pre-rewrite bugfixes so
  the oracle is trustworthy); build system switch (setuptools+Cython, uv cache-keys, CI build step,
  wheels workflow); trivial `_opt` extension proving the toolchain on all platforms.
- **M1 — Delete + boundary**: delete `sonolus/backend/optimize/` pass modules and `finalize.py` body;
  trim `flow.py` to the boundary types/printers; **remove `CompileCache`/`hash_cfg` and the cache
  plumbing now** (`compile.py`, `engine.py`, `dev_server.py`, `project.py`) — `package_engine`
  constructs a `CompileCache` by default and `compile_mode` calls `hash_cfg` whenever one is present,
  so deferring this to M4 would break every build the moment `flow.py` is trimmed; land arena IR +
  marshal in/out + `cfg_cleanup` + bump allocator (O0) **+ bitset liveness + interference allocation**
  + emission. Temporarily alias
  fast/standard to cfg_cleanup + interference allocation — bump alone is not enough: pydori exceeds the
  4096-slot cap without liveness-based allocation (the regression suite already excludes `minimal` for
  this reason, and `test_project_full_build_succeeds` is a build test, not a golden, so xfailing
  goldens alone wouldn't keep the suite green). Dual-run suite green; pydori goldens for fast/standard
  xfailed until M4. Release policy for the window: master carries weak codegen for fast/standard
  between M1 and M3 — **no release is tagged until M4 passes the metrics gate**.
- **M2 — Mid-end**: SSA construction, SCCP (+set lattice), simplify/GVN, DCE, out-of-SSA (simple
  correct version) + treeify + n-ary emission + final-schedule coalescing/cleanup + `normalize_switch`.
  Enable as `fast`.
- **M3 — Full standard**: LICM, `rewrite_switch`, if-conversion. Enable as `standard`.
- **M4 — Integration + parity**: Cython `compile_mode`; enable the thread pool on all builds (drop the
  `no_gil()` gate) with passes in nogil regions; `visualize_cfg`/debug helpers; regenerate goldens;
  metrics comparison vs M0 baseline (gate: effective node counts (§2) ≤ baseline on pydori, raw counts
  reported alongside; investigate any callback
  that regresses); compile-time benchmark report (single- and multi-threaded).
- **M5 — Polish**: docs/changelog, contributor build docs, wheel release dry-run, delete dead shims.

## 12. Alternatives considered

- **E-graphs / aegraphs (Cranelift-style)**: elegant unification of rewrites, but a large complexity and
  compile-time budget for a target whose main cost metric (node count on mostly-straight-line game
  callbacks) is well served by SCCP+GVN+treeify. Revisit only if rule-ordering problems materialize.
- **Sea of nodes**: overkill for this IR size; block-structured SSA keeps the mental model close to the
  existing textual CFG dumps and tests.
- **No SSA at all** (def-use chains over virtual registers, or a Binaryen-style tree-rewriting IR):
  genuinely considered, since the target is expression trees and the boundary format is non-SSA anyway.
  Rejected because the passes that produce most of the win degrade: SCCP loses its sparseness (per-edge
  constant sets are what prune switch edges), GVN needs availability bookkeeping that SSA gives for
  free, and multi-value merges reinvent phis under another name. The price of SSA is confined to one
  well-understood place — phi lowering + coalescing in §7.4 — and the tree-shaped backend still gets a
  tree IR at exactly the stage where tree rewrites are natural (§7.4.3). If implementation experience
  contradicts this, the fallback is a non-SSA local-value-numbering mid-end at `fast` level only,
  keeping SSA for `standard`.
- **Keeping the Python optimizer as a fallback**: rejected — doubles maintenance and contradicts the
  delete-first mandate; wheels + sdist cover distribution (§9).
- **Boissinot out-of-SSA**: better copy counts than split-all-edges + coalesce, but subtle; deferred to a
  measured follow-up (§7.4).
- **Rust core** (an experiment exists in untracked `rust/`): Cython chosen per project direction —
  simpler Python interop for the marshalling-heavy boundary and a single-language build.

## 13. Risks / open questions

- **FP-semantics drift** in the C fold kernels (highest correctness risk) — mitigated by the
  differential fold tests and the dual-run suite; keep kernels as literal transcriptions of
  `interpret.py`/`smath`.
- **Golden churn**: ~1200 regenerated reference files in one commit; mitigated by the `_cfg` invariance
  check and the metrics diff.
- **Temp-memory regressions**: allocator quality gate via baseline slot counts (pydori "minimal runs out
  of temp memory" today shows headroom matters).
- **Windows/macOS contributor friction** (compiler required): document; CI wheels keep users unaffected.
- **Dev-server rebuild latency**: removing `CompileCache` means every rebuild re-optimizes all
  callbacks. M0 records a **warm-cache** dev-server rebuild timing (the case the cache actually
  accelerates — unchanged callbacks skip optimization entirely today), and M4 verifies the new
  uncached rebuild against that bar, not just against uncached full-build splits.
- **Open** (both have concrete defaults; open only in the sense of pending confirmation/tuning):
  (a) if-conversion arm budget starts at cost ≤ 8, tuned against pydori metrics at M4;
  (b) `Op.Rem` is decided as `math_impls` sign-of-dividend semantics — confirm against the real
  runtime before M0 closes, revisit only if it disagrees.
