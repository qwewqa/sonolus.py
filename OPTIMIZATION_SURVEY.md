# Optimizer Codegen-Opportunity Survey (pydori, `standard`, M3 tip)

Analysis only — no source changes. Survey of the M3 optimizer's emitted output on the
`pydori` regression project, to find recurring wasteful patterns for the orchestrator to
prioritise before the M4 metrics gate.

- **Base commit**: `d81e99b` ("M3: LICM, rewrite_switch, and if-conversion wired into standard").
- **Method**: `tools/metrics.py` used as a library (task enumeration, node counting, runtime-constant
  classification) plus scratch analyzers. For every one of the **150 pydori callbacks** the survey built
  the raw CFG (`callback_to_cfg`), optimized at `standard` (`run_passes`), and emitted the `EngineNode`
  tree (`cfg_to_engine_node`). All frequencies are **per-reference over the expanded emitted tree**
  (shared/hash-consed nodes re-execute per reference, §2); all "effective" figures discount
  runtime-constant subtrees to cost 1 (`inlining.is_runtime_constant` semantics, §2). Every example below
  was read back from the actual dumps.

## 0. The value function, stated precisely (and its blind spot)

Per OPTIMIZER_REWRITE.md §2 the M4 gate is `effective_node_count`, which counts **function nodes and
value/operand pushes equally** (each = 1) after folding runtime-constant subtrees. Two consequences that
shape every estimate here:

1. Replacing a function node with a value node (e.g. `Get(b, Add(i, k))` → `GetShifted(b, k, i, 1)`) is
   **gate-neutral** — same node count — even though §2 says a function node carries "non-trivial
   per-instruction interpreter overhead" a plain push does not. Such changes are **real-runtime wins the
   gate cannot see**. This survey reports a `Gate Δ` column (what M4 credits) *and* a `Real effect` column
   (dispatch-mode / fn-instruction savings beyond the gate), and never conflates them.
2. Runtime-constant trees already fold to 1 at runtime, so "simplifying" them saves nothing. Estimates
   below exclude runtime-constant occurrences from the savings (measured, not assumed).

## 1. Aggregate shape of the emitted code (150 callbacks, `standard`)

| Metric | Value |
|---|---|
| function nodes (per-ref) | 89,072 |
| value nodes (per-ref) | 119,799 |
| **effective nodes (gate)** | **140,898** |
| blocks (Execute nodes) | 5,780 |
| distinct ops emitted | 59 of 191 |

Effective (non-runtime-const) function nodes by op — where the real cost lives:

```
Get 20760   Set 9688   Execute 5780   Add 5266   Multiply 3340   If 2719
Divide 1619  Subtract 1418  Less 587  LessOr 573  Greater 408  Remap 352
Equal 300  Not 246  SpawnParticleEffect 158  SwitchIntegerWithDefault 154
JumpLoop 150  Block 150  NotEqual 144  Draw 141  SwitchWithDefault 140
```

Terminator distribution (per block): `const` 3135, `If` 2351, `SwitchIntegerWithDefault` 154,
`SwitchWithDefault` 140. Of the 2351 `If` terminators, **2003 guard blocks with ≤1 statement**, but
almost all of those arms contain side effects (`Draw`/`Spawn`/`Set`/`StreamSet`), so they are *not*
if-convertible — multiway if-conversion cannot merge side-effecting arms under a select (§7.3). Dispatch
reduction beyond the current M3 if-conversion is therefore limited on pydori; the dispatch wins available
are switch-mode upgrades and exit-block elision, not more selects.

**Headroom is modest.** pydori's `standard` codegen is already tight. The clearly gate-positive
opportunities below total ~**1,240 effective nodes (~0.9%)**; their value is as much in real-runtime
dispatch/fn-instruction cost (invisible to the gate) as in the node count. No single transform is a
game-changer — this is a list of cheap, safe, incremental wins, ranked.

## 2. Ranked findings

> **Implementation status (2026-07, M3.5 tip 783b525, baseline standard effective = 138192).**
> Findings #1–#6 IMPLEMENTED; #7 DROPPED-AT-IMPL (no reproducible target at M3.5). Measured
> per-finding standard-effective deltas (attributable, in implementation order): #2+#6 −191,
> #1 −196, #3+#5 −46, #4 −148 → **total −581** (standard 138192→137611; fast 150301→149916).
> `SwitchWithDefault` 140→**0**; `SwitchIntegerWithDefault` 154→294; `Not` 453→57;
> `GetShifted` 0→1075, `SetShifted` 0→67; `Execute` 5780→5706 (74 shared exits elided). Full
> suite green (1759). See per-row Rec column for details.

| # | Opportunity | Freq | Gate Δ (eff) | Real effect (beyond gate) | Home / effort | Risk | Rec |
|---|---|---|---|---|---|---|---|
| 1 | `SwitchWithDefault` → `SwitchIntegerWithDefault` via **dense (gap-tolerant) normalize** | 140 blocks | **−476** | linear-scan → jump-table dispatch | `normalize_switch` (lower.pyx) / **S–M** | LOW | **IMPLEMENTED** (measured −196, not −476: the survey's estimate omitted the +2/block affine test-shift (`test−a`), and the 56 `(2,3,6)` blocks are net +1/block on the gate but still gain jump-table dispatch. All 140 convert; `SwitchWithDefault`→0. Gap-fill done emit-side (`_switch_node`) so it uniformly handles default & default-less without adding arena edges; density guard `span≤2k` keeps it gate-safe.) |
| 2 | `If(Not(x))` terminator → **swap edges, drop `Not`** | 394 (187 non-const) | **−187** | fewer branch fn-instrs | midend branch canon / **S** | LOW | **IMPLEMENTED** (`_canon_branch_not` in the shared mid-end pass; iterates `Not` chains, NaN-verified. `Not` 453→57. −191 combined with #6.) |
| 3 | Strided address → **`GetShifted`/`SetShifted`** (`Add(off, Mul(i,s))`) | 166 | **−332** | −2 fn-instrs each | emit `_place_components` / **S–M** | MED | **IMPLEMENTED** (emit `_shifted_components`; generalized to absorb *any* binary `Multiply` (runtime stride too, via the shifted `stride` operand). Also SetAddShifted via fuse_rmw interplay. −46 combined with #5.) |
| 4 | Empty **shared-exit-block elision** | 74 blocks | **~−148** | one fewer dispatch/callback | emit / **S–M** | LOW–MED | **IMPLEMENTED** (emit `_compute_block_map`, gated to ≥2-pred exits; **−148**, exactly 74 blocks. Emission-only; both emit paths agree byte-for-byte.) |
| 5 | Off-`k` address → `GetShifted`/`SetShifted` (`Add(i, const)`, stride 1) | 1284 | 0 | −1 fn-instr each (`Add`→push) | emit `_place_components` / **S** | MED | **IMPLEMENTED** (bundled with #3; gate-neutral by design. Skipped when the index is itself an `Add` — there the offset folds into the flattened `Add` spine for free, so shifting would ADD a node.) |
| 6 | `Subtract(x,Negate(y))`→`Add`, `Add(x,Negate(y))`→`Subtract` | ~92 | ~−80 | trivial | midend GVN / **S** | LOW | **IMPLEMENTED** (GVN identities, args[1] only, n==2; bit-exact vs the Interpreter. `Negate` 1749→1664.) |
| 7 | Treeify: **duplicate** runtime-const values instead of materialising | 8 temps | ~−16 | removes temp barrier to runtime folding | treeify (lower.pyx) / **S** | LOW | **DROPPED-AT-IMPL** (No reproducible target at M3.5: the fully-runtime-const temp stores in the current corpus are correct phi-copies (the rtc tree *is* duplicated into the copy body, not materialised) or dynamic-index reads (not rtc). The one path that could materialise an rtc value — undef pre-marking (ir.pxd, load-bearing for correctness + cycle-breaking) — never fires on a structurally-rtc value: a guarded exception was a measured **0-node** no-op. Not worth touching that machinery for zero gain.) |
| — | `Op.Copy` for bulk struct moves | 6 runs | 0 | — | — | — | **DROP** (sources are permutations) |
| — | `Clamp` from `Min`/`Max` nests | 0 | 0 | — | — | — | **DROP** (0 occurrences) |
| — | `JudgeSimple` from `Judge` | 0 | 0 | — | — | — | **DROP** (windows non-const) |
| — | `Lerp`/`Unlerp`/`Remap` reconstruction | ~0 | 0 | — | — | — | **DROP** (already emitted where used; `Add(a,Mul)` are FMA-shaped, not lerps) |
| — | `DoWhile`/`While` as expressions (loop re-roll) | n/a | — | — | L | HIGH | **DROP** (out of scope) |

---

## 3. Findings in detail

### 1. Dense-case `SwitchWithDefault` → `SwitchIntegerWithDefault`  — KEEP (best payoff/risk)

**What.** `normalize_switch` (lower.pyx `_normalize_switch`/`_offset_stride`) only rewrites cases that
form an **exact** arithmetic progression `a + i·b` with no gaps; any hole makes `_offset_stride` return
`None`, so the block stays a `SwitchWithDefault` (a linear scan, §2). All 140 surviving
`SwitchWithDefault` blocks are exactly these gapped generator-state dispatches:

- **84 blocks**: cases `(1,2,3,4,5,7)` — gap at 6.
- **56 blocks**: cases `(2,3,6)` — gaps at 4,5.

Both read a materialised state temp (`Get(10000, 0)` / `Get(10000, 22)`), e.g. in
`pydori_play_tap_update_sequential` and `pydori_play_tap_touch`. The two dispatch points share one state
variable whose full range is a contiguous `1..7`; each individual switch covers a subset with holes.

**Fix.** When `_offset_stride` fails, fall back to a **dense** table: `offset = min(cases)`, `stride = 1`,
`span = max−min+1`; rewrite the test to `test − offset`; emit `SwitchIntegerWithDefault` with `span`
target slots, routing the `span − k` holes to the default edge. Gate cap by a bounded span (e.g. reject if
`span > 2·k` or `span > 64`) so `{1, 1000000}` never explodes the table.

**Savings (measured).** Removes **672** case-label value-nodes, adds **196** hole-target value-nodes →
**net −476 effective**. On top of that, all 140 blocks upgrade from linear scan to O(1) jump-table
dispatch — a real-runtime win the gate does not price. `SwitchIntegerWithDefault` is already emitted 154×
today, so **runtime support is proven** (lowest risk of the emission-shape changes).

**Risk.** LOW. Pure per-block edge/test rewrite; each switch shifts its own test independently (no
cross-block coupling). Only hazard is an unbounded span → mitigated by the cap. Must run after all
cleanup (already the case, §7.4.5).

### 2. `If(Not(x))` terminator → swap true/false edges, drop the `Not` — KEEP (cheapest)

**What.** `_terminator_node` (emit.pyx) emits a two-way block as `If(test, true, false)` verbatim. The
mid-end GVN (`_gvn_instr`) canonicalises `Not(Not(b))→b` but has no rule for a *branch test* that is a
`Not` (the branch condition is `block.test_val` + edges, not a GVN'd instruction). Result: **394 `If`
terminators test `Not(...)`**:

```
inner op of Not:  Get 308,  Greater 50,  LessOr 28,  Less 7,  If 1
e.g. pydori_play_stage_touch:  If(Not(Get(10000,28)), t, f)
     pydori_play_tap_initialize: If(Not(Greater(EntityData[3],0)), t, f)
```

`If(Not(x), a, b) ≡ If(x, b, a)` — swapping the two target edges removes the `Not` entirely (this also
subsumes all 85 `Not(<cmp>)` terminators; no De Morgan needed).

**Fix.** In the mid-end, add a two-way-block canonicalization: if `test_val`'s op is `Not`, set
`test_val = inner` and swap the `cond=0` / `cond=None` edges. (Alternatively emit-local in
`_terminator_node`, but mid-end lets it compose with `rewrite_switch`/if-conversion and is unit-testable.)

**Savings (measured).** 394 `Not` terminators; **187 test non-runtime-const data** → **−187 effective**
(the other 207 wrap runtime-const reads that fold to cost 1 with or without the `Not`, so no gate credit —
still worth removing for artifact size / real dispatch). Effort **S**, risk **LOW** (pure edge relabel,
behaviour-preserving).

### 3. Strided address arithmetic → `GetShifted`/`SetShifted` — KEEP

**What.** `_place_components` (emit.pyx) already materialises a place's `offset` field as
`Add(index, offset)`, and the index can itself be `Multiply(i, stride)`. For the **strided** shape
`Get(block, Add(offset, Multiply(i, stride)))`, `GetShifted(block, offset, i, stride)` (interpret.py:562:
`get(block, offset + index*stride)`) computes the same address in **one** node, absorbing both the `Add`
and the `Multiply`:

```
pydori_play_stage_preprocess:  Get(2001, Add(Get(10000,8), ...))   [2001 = LevelData]
```

- `Get(block, Add(off, Mul(i,s)))` = 6 nodes + i-subtree (3 fn / 3 val)
- `GetShifted(block, off, i, s)`    = 4 nodes + i-subtree (1 fn / 3 val) → **−2 effective**

**Savings (measured).** 165 strided `Get` + 1 strided `Set` = 166 sites × −2 = **~−332 effective**.
GetShifted/SetShifted are currently **never emitted** (see §5), so this needs the place layer to carry a
stride and emit the shifted op — but the place already carries `(block, index_val, offset)`, so it is an
**emission-local** change plus a small recognition step (when `index_val` resolves to `Multiply(i, s)`
with constant `s`, emit shifted). Effort **S–M**.

**Risk.** MEDIUM. The oracle (`interpret.py`) implements `GetShifted`/`SetShifted`, so the dual-run suite
would pass — **but the oracle does not model bytecode cost**, so an M0-style check against the *real
runtime* is required to confirm (a) the runtime implements the `(block, offset, index, stride)` signature
and (b) a `GetShifted` instruction is not itself more expensive than `Get`+`Add` (it removes 2 fn nodes
for 1 heavier fn node — very likely a net win, but measure). This is exactly the "op the oracle supports
but the runtime cost is unmeasured" hazard §2/§13 flags.

### 4. Empty shared-exit-block elision — KEEP-low

**What.** Every callback with a return/exit path keeps one empty block (`0 statements, 0 outgoing`) that
emits `Execute(exit_index)`. **80 such blocks across 80 callbacks; 74 are shared (≥2 predecessors)** —
the `CombineExitBlocks` canonical exit. Concretely (`pydori_play_tap_initialize`):

```
0: goto 1 if (EntityData[4]>0) else 3
1: goto 2 if !(EntityData[3]>0) else 3
2: StreamSet(...); Spawn(...); goto exit
3:                       <-- empty; emits Execute(exit_index)
```

Predecessors could target `exit_index` (the `JumpLoop` halt sentinel) directly, dropping block 3's
`Execute`.

**Savings.** −1 `Execute` fn-node − its index operand ≈ **−2 effective per shared exit ≈ −148** across 74
callbacks, plus one fewer dispatch per callback execution. Effort **S–M** (emission must skip empty
no-outgoing blocks and redirect in-edges to `exit_index`, adjusting RPO indices). Risk **LOW–MED** (touches
the halt/sentinel convention — verify the `JumpLoop` trailing-`0` semantics still hold).

### 5. Stride-1 offset address → `GetShifted`/`SetShifted` — CONDITIONAL (real win, gate-blind)

**What.** The dominant address shape is `Get(block, Add(base, const))` (struct-field-in-array:
`arr[i].field`), **1198 `Get` + 86 `Set` = 1284 sites**, generated by `_place_components` from a place
with `offset ≠ 0`. `GetShifted(block, const, base, 1)` computes the same address.

**Savings.** **Gate Δ = 0** — `Get`+`Add` (4 nodes: 2 fn/2 val) and `GetShifted` (4 nodes: 1 fn/3 val)
have identical node count; the transform trades an `Add` **function** node for a `stride=1` **value**
push. That is a real bytecode win (one fewer function-instruction dispatch, per §2) on 1284 hot sites, but
**M4 will not credit it**. Effort **S** (same emit hook as #3). Recommendation: **only pursue if the
orchestrator values real dispatch cost beyond the gate, and only after the #3 runtime measurement
confirms `GetShifted` ≤ `Get`+`Add` cost** — otherwise it is a lateral move or a regression in disguise.

### 6. `Subtract(x,Negate(y))`→`Add`, `Add(…,Negate(y))`→`Subtract` — KEEP-low

**What.** GVN handles `0−x→Negate` and `Negate(Negate)` but not the recombination:

```
pydori_watch_watch_stage_preprocess:  Subtract(Get(1000,1), Negate(Get(1000,1)))   [= 2·x]
pydori_play_stage_preprocess:         Add(Negate(Get(1000,1)), 0.05, Multiply(...))
```

**Savings.** `Subtract(x,Negate(y))→Add(x,y)` (61) and `Add(x, Negate(y))→Subtract(x,y)` (31, valid only
when `Negate` is the trailing arg — reassociation of the `Add` spine is illegal under FP order, §2) each
drop one `Negate` → **~−80 effective**. Note `Subtract(x, Negate(x))` cases (block 1000 =
`RuntimeEnvironment`, runtime-const) fold to cost 1 anyway; savings counted on non-const only. Effort
**S**, risk **LOW**. Small; bundle with other GVN rules.

### 7. Treeify: duplicate runtime-const values, don't materialise — KEEP-low

**What.** §7.4.1 says runtime-constant trees must duplicate regardless of size (a temp is a barrier to the
runtime's own folding). **8 temps violate this**: `Set(10000, off, Multiply(6, Get(2001,12)))` in the
`*_touch` callbacks (block 2001 = `LevelData`, not writable in `touch` → runtime-const). The value is
materialised then read from a temp.

**Savings.** Each temp Get is effective-cost 1 whether folded-const or temp-read, so the win is only the
eliminated `Set` (+ its address nodes): **~−16 effective**, plus it stops defeating the runtime fold.
Effort **S**, risk **LOW**. Worth a look at why treeify keeps these 8 (likely the value is reused as a
dynamic array index base and a conservatism rule pins it). Broader materialisation audit found only **34
write-once/read-once temps** total (already near-optimal) and **813 constant-valued temp `Set`s**, but the
latter are overwhelmingly loop-induction / generator-state variables that are legitimately mutated, not
fold candidates — no systemic materialisation gap.

---

## 4. Candidates evaluated and dropped (with evidence)

- **`Op.Copy` for bulk struct moves** — the only runs of consecutive `Set(dst,k), Set(dst,k+1)…` (6 runs,
  length 8–9, in `*_stage_preprocess`) write a **permutation** of scrambled source temps
  (`dst+1←t0, dst+2←t7, dst+3←t6, …`). `Copy(src,src_i,dst,dst_i,count)` requires *contiguous* source and
  dest; sources here are not contiguous. **No applicability on pydori.**
- **`Clamp` from `Min`/`Max` nests** — `Min(Max(..))`/`Max(Min(..))` occur **0 times**; `Clamp` (257) and
  `Min`/`Max` (33/33) are emitted directly where the source uses them.
- **`JudgeSimple` from symmetric `Judge`** — all 14 `Judge` calls have `nargs=8` with **non-constant
  windows** (`const_args=0`), so the symmetric-window `JudgeSimple` form does not apply.
- **`Lerp`/`Unlerp`/`Remap` reconstruction** — `Remap` (502), `Lerp` (57), `Clamp` (257) already emitted.
  The `Add(a, Multiply(x,y))` shapes (602) are FMA-shaped, not lerps (`x` is not `Subtract(b,a)`), e.g.
  `Add(16, Multiply(LevelData[16], 9))`; the runtime has no FMA op, so nothing to fold into.
- **`DoWhile`/`While` as expressions** — would require loop re-rolling from the lowered CFG; large,
  high-risk, and out of the survey's incremental scope.
- **`Equal(x,const)` chains not caught by `rewrite_switch`** — 137 survivors, all in *statement position*
  (e.g. `Equal(Get(10000,0), 6)` assigned/used as a boolean value in `*_update_sequential`), not block
  tests. `rewrite_switch` only merges equality-chain *terminators*; these are genuine value computations,
  not missed switch material. No action.
- **`cmp(cmp(..))` (57)** — comparing a boolean result to a value (`NotEqual(Greater(..), Get(..))`); no
  general simplification.

## 5. Unused-op audit (132 of 191 ops never emitted)

Most unused ops are legitimately unreachable on pydori (36 `Ease*` variants where only `EaseOutQuad` is
used; the whole `Stack*` frame machinery; `Debug*`; `DrawCurved*`; scheduled-life ops). The ones with a
**profitable IR pattern that currently emits something longer**:

| Unused op | Pattern it could replace | Verdict |
|---|---|---|
| `GetShifted`/`SetShifted` | `Get/Set(b, Add(off, [Mul(i,s)]))` — 1450 sites | **Finding #3/#5** |
| `Copy` | contiguous bulk moves | DROP (permutations only) |
| `JudgeSimple` | symmetric `Judge` | DROP (windows non-const) |
| `*Pointed` (`GetPointed`, `SetAdd*Pointed`, …) | double-indirect `Get(Get(b,i), Get(b,i+1)+k)` — the `Get(_, Get(#,#))` shape appears 408× | Note: pointer-deref reads; the fused-RMW agent owns the `Set*` side. `GetPointed` folding of the observed `Get(deref_block, deref_index)` shape is a possible follow-up but semantically delicate (dynamic target block, aliasing) — **defer, out of this survey's low-risk scope**. |
| `Increment*/Decrement*`, `SetAdd*` | RMW — **owned by the separate fused-RMW agent; excluded**. |
| `Sign`, `Trunc`(28 used), `Round`, `Frac`, `Rem`, `Mod`(75 used) | already covered by SCCP fold coverage §7.2.2 | no codegen action |

## 6. Recommendation summary

Pick order by payoff/risk:

1. **#1 dense-`SwitchWithDefault`→`SwitchIntegerWithDefault`** — best all-around: −476 gate + real
   dispatch upgrade, LOW risk, op already proven in the runtime. Do first.
2. **#2 `If(Not)` edge-swap** — cheapest, −187 gate, LOW risk. Do alongside #1.
3. **#3 strided `GetShifted`/`SetShifted`** — −332 gate, but gate the whole GetShifted family behind an
   **M0-style real-runtime cost measurement** (the oracle can't validate cost). If the measurement is
   favourable, #5 (stride-1, 1284 sites) becomes worthwhile as a real-runtime-only win.
4. **#4 exit-block elision** (~−148) and **#6/#7** (~−96) — small, low-risk cleanups to bundle in.

Total gate headroom if #1–#4,#6,#7 all land: **~−1,240 effective (~0.9%)**, with disproportionately larger
real-runtime value from the switch-dispatch and fn-instruction reductions the gate does not price. pydori's
M3 codegen is already tight; there is no large single win, and the survey found **no correctness/coverage
regression** in the emitted output — the materialisation and runtime-constant-duplication machinery is
working as designed (effective 140,898 vs raw 208,871 confirms aggressive runtime-const duplication).

## Appendix: reproduction

Scratch analyzers (not committed) built each callback via `metrics.iter_callback_tasks` →
`callback_to_cfg` → `run_passes(STANDARD_PASSES, OptimizerConfig(mode, callback))` →
`cfg_to_engine_node`, then counted per-reference over `_post_order` with `ref_mult` for multiplicity and
`metrics._node_is_runtime_constant` for the runtime-const classification. Terminators read from each
`Block(JumpLoop(Execute…))`'s per-block last arg; algebraic/address patterns matched over the emitted DAG;
switch/exit/temp shapes read from both the emitted tree and the optimized `cfg_to_text`.
