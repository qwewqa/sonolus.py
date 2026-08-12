# Bug hunt final summary

Seven rounds of multi-agent auditing over sonolus.py, run 2026-08-09 through 2026-08-11 on top of commit
`87bfd38`. Each round swept the codebase with dimensional finders, reproduced or refuted every lead under
investigation, attacked the survivors with adversarial validators, then implemented what the maintainer approved.
Round N+1 always included regression finders walking round N's diff, which is where a third of the later findings
came from. `bug_manifest.md` is the full ledger; this document is the closing state of every numbered finding in
it.

Every ID carries exactly one of three states:

- **FIXED**: implemented and verified by the closing audit. `(unverified repro)` marks an item that is
  implemented but whose repro the audit could not re-run.
- **REJECTED**: refuted, overturned, adjudicated not-a-bug, or working as intended. No code change.
- **DEFERRED**: skipped, won't-fix, or held by maintainer adjudication. Real behavior, no change wanted now.

## Campaign statistics

| Metric | Value |
| --- | --- |
| Rounds | 7 |
| Leads raised / investigated | 216 / 212 |
| Numbered findings | 181 |
| Fixed / Rejected / Deferred | 157 / 10 / 14 |
| Tests before (recorded at `87bfd38`) | 2749 |
| Tests after (measured today) | 3876 |
| Gate progression by round | 3012, 3130, 3417, 3518, 3547, 3602, 3644 (round 7 batch 1), 3876 |

Final gate, all four commands run today on the closing tree:

| Command | Result |
| --- | --- |
| `pytest -n 32` | 3876 passed in 107 s |
| `ruff check` | All checks passed |
| `ruff format --check` | 268 files already formatted |
| `mkdocs build --strict` | built clean in 3.3 s |

Leads and IDs are not 1:1 and were never meant to reconcile: validation merged duplicate leads, refuted others,
and round 1 alone gained 7 findings discovered during investigation rather than during the sweep.

## Round 1: initial three-phase audit (43 IDs)

8 dimensional finders raised 48 leads, 44 went to investigation, 22 adversarial validators judged the confirmed
set. 35 findings filed, 8 adjudicated away.

| ID | State | Finding |
| --- | --- | --- |
| C1 | REJECTED | SCCP folds f64 comparisons, so -O1/-O2 can delete a branch arm f32 would take: out of contract |
| C2 | FIXED | Non-finite switch case labels shipped as bare Infinity/NaN, making the packaged engine JSON invalid |
| C3 | FIXED | `zip()` and multi-iterable `map()` advanced every arm past the first exhausted one |
| C4 | FIXED | `native_function(const_eval=True)` turned a domain error into a CompilationError on a dead branch |
| C5 | REJECTED | `Range.__len__` disagrees with `RangeIterator` on non-integral args, which are out of contract |
| C6 | FIXED | A failed dev-server rebuild left the failed build's debug-message mapping installed |
| C7 | REJECTED | One stalled TCP connection wedges the single-threaded dev server: restartable dev tooling |
| C8 | FIXED | A nested decorated `def` evaluated defaults before decorator expressions, unlike CPython |
| C9 | DEFERRED | `Num.__floordiv__` folds with CPython `//` but emits `Floor(Divide(...))`: won't fix |
| C10 | FIXED | Integral-operand folds overflowed into CompilationError where CPython and the runtime give inf |
| C11 | FIXED | WatchBlock `readable` sets for blocks 4000-4004 omitted `updateSpawn` |
| C12 | FIXED | `package_rom` aborted with a bare `struct.error` on an out-of-f32-range ROM value |
| C13 | REJECTED | Dev-server rebuilds rewrite served JSON non-atomically: same dev-tooling call as C7 |
| C14 | REJECTED | `apply_converter_to_collection` unguarded dereferences: skipped on C7/C13 dev-tooling grounds |
| C15 | REJECTED | Silent filename-stem collisions in collection loading: same dev-tooling grounds |
| C16 | REJECTED | `Collection.link` unguarded dereferences: same dev-tooling grounds |
| C17 | FIXED | `SimulationContext` set its re-entrancy guard in `__init__` instead of `__enter__` |
| C18 | FIXED | A Record with a `Final` field could not be stored in an Array: construction hit the assignment guard |
| C19 | FIXED | `Range.__setitem__` reported "Raise statements are not supported" instead of naming Range |
| P1 | FIXED | `lower_from_ssa` duplicated runtime-constant trees with no size bound: exponential compiles |
| P2 | FIXED | `ArchetypeScore` added to `RUNTIME_CONSTANT_BLOCKS` on permission-table parity with `ArchetypeLife` |
| P3 | FIXED | The `{Get,Set}Shifted` rewrite grew effective node count on runtime-constant indexes |
| P4 | FIXED | `cfg_cleanup` allocated a dense nb^2-byte matrix per callback (22.6 MB for pydori's largest) |
| P5 | FIXED | Test helpers re-traced each callback once per optimization level |
| T1 | FIXED | `sort_linked_entities` and `_EntityNodeRef` unexecuted by the whole suite |
| T2 | FIXED | The `static_assert` family in `sonolus.script.debug` had zero tests |
| T3 | FIXED | 22 reachable unsupported-construct guards untested; the `match=` patterns pinned nothing |
| T4 | FIXED | Non-Num `lerp` arm unguarded; `lerp_clamped` pinned only by golden bytes |
| T5 | FIXED | `Interval.shrink/expand/clamp/zero` unexecuted: the suite passed with shrink and expand swapped |
| T6 | FIXED | `Pair` ordering operators unexecuted: the suite passed with the order reversed |
| T7 | FIXED | Uncovered interpreter ops: the three named groups closed, the 15-op enumeration not re-derived |
| T8 | FIXED | `package_data` never rejected non-finite values and no test asserted conformant JSON |
| T9 | FIXED | The score-multiplier API (4 descriptors) had no coverage |
| T10 | FIXED | `ArrayMap` missing-key, `clear`, and `is_full` paths unguarded or golden-only |
| T11 | FIXED | `ArrayLike.last_index`/`swap`, `VarArray.append_unchecked`, and the eq/ne branches untested |
| T12 | FIXED | `SimulationContext` had zero tests and its conftest hook was dead |
| T13 | FIXED | `_OptionField` write arms and outside-context raises untested |
| D1 | FIXED | Level-memory docs gave one flat callback list: false for preview, incomplete for tutorial |
| D2 | FIXED | `types.md` truthiness rule omitted `__len__` and the Record-truthy default |
| D3 | FIXED | Unsupported host objects errored with a raw repr carrying a memory address |
| D4 | FIXED | Tuple-unpack arity error was generic where CPython counts both directions |
| D5 | FIXED | `Final` Record fields documented only in the changelog |
| D6 | FIXED | `random.pyi` omitted `shuffle`'s array-like restriction |

Round 1 also produced unnumbered outcomes with no ID: 6 refuted leads, 3 likely-settled ones, and 6 duplicates
merged into C1, C3, C5, C13, P1, and P4 during triage. They are recorded in the manifest and not tabulated here.

Two round-1 rows carry a residual worth keeping in the record. P2 landed on permission-table parity with
`ArchetypeLife` because the official wiki documents no member list, so the premise that the real runtime folds
block 5001 reads is still unverified. T7 closed the three op groups its entry names (the 11 environment queries,
`AddLifeScheduled`, and the six `DrawCurved*` ops) without re-deriving the 15-op enumeration behind the original
count, and its `DrawCurved*` pins are read off `sprite.py` rather than validated against the real runtime.

## Round 2: post-implementation hunt (27 IDs)

4 regression finders over the round-1 diff plus 4 fresh-area finders, 35 leads, 14 investigation groups.

| ID | State | Finding |
| --- | --- | --- |
| R1 | REJECTED | The tests/script oracle optimizes under `callback=None`: tests need not model a callback |
| R2 | DEFERRED | `max()`/`min()` with `key=` call the key 2*(n-1) times, right operand first: won't fix for now |
| R3 | FIXED | `construct_genexpr` did not stop the clause chain on a compile-time-false `if` filter |
| R4 | FIXED | `_BaseArchetype.__init__` used `or` where it meant presence, zeroing a falsy level-data argument |
| R5 | FIXED | Two imported fields resolving to one import key silently dropped an entry |
| R6 | FIXED | Two exported fields resolving to one export key desynchronized the export indexes |
| R7 | FIXED | One archetype class listed twice in a mode desynchronized ids, keys, and the mro ROM table |
| R8 | FIXED | An archetype field named `despawn`, `index`, `result`, and five more replaced the inherited property |
| R9 | FIXED | Two distinct classes resolving to one archetype name in a mode now warn; prefix stripping stays |
| R10 | DEFERRED | A reserved archetype field name in annotation-only form is silently dropped: not worth fixing |
| R11 | DEFERRED | `SimulationContext.__enter__` is not transactional: testing-only surface |
| R12 | FIXED | `visualize_cfg` dropped the callback it traced with |
| R13 | FIXED | `cfg_to_engine_node` re-marshaled with mode/callback `None`, so goldens measured an unshipped tree |
| R14 | FIXED | `run_and_validate`'s closure-to-ROM leg raised a bare unlocated ValueError from the C12 guard |
| R15 | FIXED | A starred unpack target got D4's counted-arity message, shadowing the accurate rejection |
| R16 | FIXED | The P1 duplication budget materialized flat runtime-constant leaves, de-classifying the tree |
| R17 | FIXED | The zip fix's justifying comment misstated the subset; two `noqa: RUF005` were unnecessary |
| R18 | FIXED | The C12 error interpolated the widened int, printing a `1e39` literal as 39 digits |
| R19 | FIXED | No descriptor diagnostic could fire for a class-level attribute write |
| R20 | FIXED | A cross-archetype class-level field read reported an internal repr depending on compile order |
| R21 | FIXED | The mode-unavailable global error still printed a default repr with a heap address |
| R22 | FIXED | `ConstantValue._get_parameterized` baked a memory address into the class `__name__` |
| R23 | FIXED | No test wrote through a zipped loop variable of reference type |
| R24 | FIXED | `Quad.scale_centered/from_quad`, `Rect.scale_centered/shrink`, and `Vec2.orthogonal` unasserted |
| R25 | FIXED | `InvertibleTransform2d`'s quad, perspective-x, and compose_before paths unexercised |
| R26 | FIXED | `pad_z_indexes`' four tuple arms and `Sprite.draw`'s z2/z3/z4 slots had no assertion |
| R27 | FIXED | `visualize_cfg`/`cfg_to_mermaid` had zero coverage; the phi sort key lacked a dead-block default |

## Round 3: final pass over rounds 1 and 2 (25 IDs)

3 regression finders plus 3 fresh-area finders, 26 leads, 8 investigation groups. Zero golden movement and
exact-zero metrics movement on implementation, as predicted.

| ID | State | Finding |
| --- | --- | --- |
| S1 | FIXED | `add_life_scheduled()` in preview/tutorial silently truncated the callback from the call site |
| S2 | FIXED | The shadow check matched only `property`, so a field named `ref`/`spawn`/`at` replaced a method |
| S3 | FIXED | `Generator._validate_bindings` walked every enclosing scope, so an outer shadow aborted a valid genexpr |
| S4 | REJECTED | `sonolus-py check` validates the frontend only: the -O short-circuit is what makes check fast |
| S5 | FIXED | A bare `str` where a name sequence is expected expanded per character into shipped resource tables |
| S6 | FIXED | Mode constructors stored `archetypes` unmaterialized, so a one-shot iterable shipped zero archetypes |
| S7 | FIXED | `nonlocal`/`global` were rejected only when reached, so a dead declaration changed live scoping |
| S8 | FIXED | `print_number`'s preview-only restriction was unenforced; `InstructionIcon.paint` had the same gap |
| S9 | FIXED | Effect and Particle APIs had no mode guard, compiling Play/SpawnParticleEffect into preview payloads |
| S10 | FIXED | The tutorial payload shipped an `"archetypes": []` key `EngineTutorialData` has no field for |
| S11 | FIXED | A `default=` of mismatched flattened arity aborted with a bare `zip()` ValueError and no user frame |
| S12 | FIXED | `_BaseArchetype.__init_subclass__` never chained to `super()`, skipping a mixin's hook |
| S13 | REJECTED | `visualize_cfg`'s `callback=""` default renders an all-read-only CFG: the default is intended |
| S14 | FIXED | A place whose block id the passes folded lost its derived flags, so P3's decline missed when shipped |
| S15 | FIXED | `test_lower.py`'s budget-exemption rationale stated an arena saving as if it were a shipped one |
| S16 | FIXED | Eight archetype mode-guard messages printed `Mode.value`, a raw `(Block,)` tuple |
| S17 | FIXED | Star-free destructuring of an Array/VarArray/range/Record/Pair was rejected as a starred expression |
| S18 | FIXED | `ConstantValue` defined no `__repr__`, so instances still printed a heap address after R22 |
| S19 | FIXED | `test_genexpr_filters.py` misstated which arm `range()` takes |
| S20 | FIXED | `cfg_to_mermaid`'s `"<dead>"` placeholder is parsed as markup by a real render and disappears |
| S21 | FIXED | `RuntimeUi`'s 21 properties dereferenced `ctx()` unguarded, raising a raw AttributeError |
| S22 | FIXED | Builtin argument-binding errors named the internal impl (`_abs`, `_sin`, `Range.frozen`) |
| S23 | FIXED | 21 `StandardText` docstrings contradicted the published sources and 31 ids were missing |
| S24 | FIXED | R8's rejection was pinned for PlayArchetype only, leaving Watch and Preview open |
| S25 | FIXED | `test_tuple.py`'s version-split comment missed the Enum-class unpack divergence on every version |

## Round 4: post-round-3 hunt (17 IDs)

3 regression finders plus a fresh-remainder finder and a cross-round interaction finder, 18 leads.

| ID | State | Finding |
| --- | --- | --- |
| F1 | FIXED | Two callables on one source line silently compiled the leftmost one's body (latent since 2025-07-27) |
| F2 | FIXED | A genexpr's outermost iterable was evaluated in the genexpr's own scope, so a shadowing target won |
| F3 | FIXED | `Maybe.tuple`'s absent-value second element is now documented as unspecified, not zero (docs-only) |
| F4 | FIXED | A failed `_init_fields` left bookkeeping on the class, so every retry reported a misleading error |
| F5 | DEFERRED | `Record.__init_subclass__` swallows class kwargs where archetypes reject them: not important |
| F6 | FIXED | `add_life_scheduled` enforced the mode half of its spec clause but not the preprocess-only half |
| F7 | FIXED | Constant indexes now bake into `PLACE_REAL_BLOCK` offsets in range; out-of-int32 divergence won't fix |
| F8 | FIXED | `@streams` `_init_` installed descriptors with no rollback, so the retry reported an unrelated error |
| F9 | FIXED | `imported(default=)` checked flattened arity but not type, shipping values under the wrong keys |
| F10 | DEFERRED | `imported(default=)` ships an out-of-f32-range constant unchecked: not worth fixing |
| F11 | DEFERRED | `_shadowed_member`'s `case _` arm let an archetype field replace a mixin constant: skipped |
| F12 | FIXED | The S22 name rewrite left seven builtins reporting argument counts off by one |
| F13 | FIXED | `native_function`'s two anonymous raise sites named no function |
| F14 | FIXED | No published text said preview playback needs a compile-time mode predicate |
| F15 | FIXED | Thirteen user-facing error sites still interpolated a raw instance and printed a heap address |
| F16 | FIXED | `Signature.bind` reported an unexpected keyword as a missing earlier parameter, naming no callee |
| F17 | FIXED | Every `@streams` rejection test asserted from `_init_()` alone, blind to the real build path (F8) |

## Round 5: full-breadth sweep (27 IDs)

Eleven finders at full effort, including two optimizer extra-scrutiny finders and a differential campaign, 35
leads, 13 investigation groups.

| ID | State | Finding |
| --- | --- | --- |
| G1 | FIXED | Match-pattern captures leaked into non-matching paths, leaving a stale binding a later arm could read |
| G2 | DEFERRED | Set-literal iteration order diverges between the legs: order is guaranteed nowhere, no action |
| G3 | FIXED | F7's normalization was gated on `PLACE_REAL_BLOCK`, so dynamic-block and temp-array places missed |
| G4 | FIXED | Watch `spawn_time`/`despawn_time` rejected `@callback(order=...)` although the spec gives them an order |
| G5 | FIXED | `@streams` data fields, read and write including the +/-0.5 sentinel, had zero coverage |
| G6 | FIXED | The stream ascending-query API had ten unreached members and five golden-only ones |
| G7 | FIXED | 18 runtime.py environment and global accessors uncovered, two with hand-transcribed index maps |
| G8 | FIXED | `entity_memory()` is unusable in every preview callback but was documented as universally available |
| G9 | DEFERRED | Level data may name an archetype the engine never declares: sometimes desired, no action |
| G10 | FIXED | Record in-place operators raised on `NotImplemented` instead of declining to the reflected path |
| G11 | FIXED | A keyword argument supplied twice was merged last-write-wins where CPython raises TypeError |
| G12 | FIXED | Item-access and `in` errors did not mirror CPython; not-iterable was worded two ways |
| G13 | FIXED | Self-copy stores `X <- X` reached emission at fast and standard: 636 effective nodes in pydori |
| G14 | FIXED | A call whose every traced path terminates handed its caller the `Const[None]` seed |
| G15 | FIXED | `JudgmentWindow.__add__`, `.start`, and `.end` unreached and structurally unpinnable by the oracle |
| G16 | FIXED | `build_ssa` dropped an entry-block loop-header phi operand; `lower_from_ssa` then crashed the process |
| G17 | FIXED | `FindFunction` pushed no scope for ClassDef/AsyncFunctionDef, mis-stamping the enclosing function |
| G18 | FIXED (unverified repro) | Watch `RuntimeUIConfiguration` slot count: the declaration wins, not the size table |
| G19 | FIXED | `math.tan`/`atan`/`cosh`/`tanh` had no frontend test, leaving the `MATH_BUILTIN_IMPLS` wiring unpinned |
| G20 | FIXED | Four unreached one-offs: `ArrayLike.shuffle`, `get_archetype_by_name`, `EntityRef.__bool__`, Maybe |
| G21 | FIXED | `constructs.md`'s "destructuring only for tuples" was false in both readings |
| G22 | FIXED | The corpus measured 150 derived-archetype callbacks where the build compiles 60 base ones |
| G23 | FIXED | Bucket sprite ids were never checked against the declared skin |
| G24 | DEFERRED | Callbacks can emit ids outside their mode's tables: ids can be runtime-computed, so unfixable |
| G25 | FIXED | A dangling `EntityRef` aborted the build with a bare unlocated KeyError `check` could not pre-empt |
| G26 | FIXED | `load_resource` resolved a str asset against `project.resources` but a PathLike against the CWD |
| G27 | FIXED | The `optimize_and_finalize` equivalence docstring was false for the G3 shapes |

## Round 6: post-G14/G16/G25 hunt (18 IDs)

Six finders including two batch-regression hunters, a second differential campaign, and a systematic
error-quality sweep, 25 leads, 9 investigation groups.

| ID | State | Finding |
| --- | --- | --- |
| H1 | FIXED | A plain subclass of a `derive()`d archetype shipped the base's body: the shape is now banned |
| H2 | FIXED | The G14 sentinel still leaked through a later generator and `yield from`: replaced by TerminatedCall |
| H3 | FIXED | The sentinel was validated instead of absorbed at 12 result positions: subsumed by the H2 redesign |
| H4 | FIXED | `test_terminated_call.py` tracked the G14 patch, not the iterate-then-validate surface |
| H5 | FIXED | The -O1/-O2 rejection of a non-terminating callback now gives a located message; levels unchanged |
| H6 | FIXED | The optimizer-failure CompilationError named callback and mode but not the archetype |
| H7 | DEFERRED | `allocate_func` over pre-existing block-10000 references: direct temp-memory references are UB |
| H8 | FIXED | `UiMetric` omitted `time`, added in Sonolus 1.1.3 |
| H9 | DEFERRED | Preview archetypes ship a `hasInput` key the preview data class does not declare: not worth fixing |
| H10 | FIXED | `@callback(order=...)` on a method that is not a callback in that mode was silently inert |
| H11 | FIXED | `Num._accept_` printed a heap address for tuple/dict/Maybe/generator operands and named no type |
| H12 | FIXED | `array.py` interpolated a Python set of type objects, reordering the message from process to process |
| H13 | FIXED | Six of nine declaration decorators left the offending field anonymous |
| H14 | FIXED | One mode-guard message still printed the raw Mode enum after S16 converted the other eight |
| H15 | FIXED | `get_archetype_by_name`'s KeyError was re-quoted whole by `CompilationError(str(cause))` |
| H16 | FIXED | A set with a runtime element reported the internal Num hash failure, not the documented rule |
| H17 | FIXED | Subscripting a non-generic Value reported "already parameterized"; the message was widened |
| H18 | FIXED | F16's `bind_arguments` reached 2 of 7 binding sites |

## Round 7: post-redesign hunt (24 IDs)

Nine finders led by TerminatedCall regression priority, an optimizer audit, a third differential campaign, and
six full sweeps: 29 leads, 10 investigation groups.

| ID | State | Finding |
| --- | --- | --- |
| J1 | FIXED | 16 TerminatedCall catch/raise sites unexecuted; a missed catch at a branch-opening site miscompiles |
| J2 | FIXED | `:=` inside a genexpr bound in the genexpr's own scope, so the containing binding kept its old value |
| J3 | FIXED | `visit_YieldFrom` lacked the statically-absent guard its two siblings have |
| J4 | FIXED | H10's stray-marker scan tested the function object, not the name binding (re-fixed 2026-08-11) |
| J5 | FIXED | A `@classmethod`/`@staticmethod` callback died inside inspect: now rejected at registration |
| J6 | FIXED | `_RTC_DEPTH_LIMIT` unpinned: the `_MAX_FOLD_DEPTH` relation is pinned instead |
| J7 | DEFERRED | Fused-RMW export drops the place on plain REAL and DYNAMIC blocks: test rigor only, held |
| J8 | FIXED | `in`/`not in` dispatched only to `__contains__`, with no `__iter__` fallback |
| J9 | FIXED | `visit_AugAssign`'s identity guard rejected a hand-written `__iadd__` returning a new object |
| J10 | FIXED | Entity info/data/input/shared-memory accessors documented unconditional; Spawn voids all four blocks |
| J11 | FIXED | `Archetype.spawn()` had no mode guard, shipping Op.Spawn into a mode with no spawning system |
| J12 | FIXED | `range().index()` inherited ArrayLike's -1-on-miss: now CPython behavior with an assertion failure |
| J13 | FIXED | The three scheduled-audio APIs omitted the runtime's 0.5-second lead-time requirement |
| J14 | FIXED | `UiMetric.TIME` (H8) shipped with no changelog entry |
| J15 | FIXED | `@options` was the only field-declaration decorator whose annotation rejection escaped anonymous |
| J16 | FIXED | H13's rewritten messages were unexecuted and unasserted at twelve raise sites |
| J17 | FIXED | The genexpr/for iterable-rejection guards and the wrong-mode archetype check were unexecuted |
| J18 | FIXED | `ConstantValue._parameterized_` grows about 1167 entries per build and survives cache clearing |
| J19 | DEFERRED | Store-to-load forwarding (-96 effective nodes): needs aliasing care for a low gain, held |
| J20 | FIXED | `Engine.export()` emitted null skin/background/effect/particle, the only underivable required fields |
| J21 | FIXED | `cli.md`'s -v row and `BuildConfig.verbose` promised a split optimizer-stage failures did not honor |
| J22 | FIXED | Three json sites still inherited `allow_nan=True`, laundering Infinity/NaN into dev-served files |
| J23 | FIXED | `build_project` pre-validated level names but never the engine name |
| J24 | FIXED | `project.resources` and `project.converters` are inert under `sonolus-py build`, undocumented |

Adjacent work delivered with J12 and carrying no ID of its own: a `TupleImpl.index` method, unified with
`Range.index`'s contract, and a docs statement of exactly which operations tuples and ranges support.

## Deferred

Fourteen items, every one a real behavior the maintainer chose not to change now.

| ID | Reason |
| --- | --- |
| C9 | `Num.__floordiv__` fold/emit divergence is real; maintainer will not fix it |
| R2 | `max`/`min` key-call count and order: fix deferred, no date |
| R10 | Annotation-only reserved field names silently dropped: not important enough to fix |
| R11 | Non-transactional `SimulationContext.__enter__`: testing-only surface |
| F5 | Records swallow class kwargs: users should not be doing anything exotic with subclasses |
| F10 | Out-of-f32-range `imported(default=)` constant unchecked: not worth fixing |
| F11 | Archetype field replacing a mixin's class-level constant: skipped |
| G2 | Set iteration order is guaranteed nowhere, so no program may depend on it in either leg |
| G9 | Level data naming an undeclared archetype is sometimes desired |
| G24 | Ids can be runtime-computed, so build-time table validation cannot cover them |
| H7 | Direct references to temporary memory in input code are undefined behavior already |
| H9 | Extra `hasInput` key on preview archetypes: probably not worth fixing |
| J7 | Fused-RMW export place loss affects measurement rigor only: held |
| J19 | Store-to-load forwarding needs aliasing care for a relatively low gain: held |

## Notable design decisions

- **The TerminatedCall unwind (H2, absorbing H3 and H4, pinned by J1).** The G14 sentinel value was removed
  entirely rather than patched at each door. `Visitor.run` now raises a private control-flow exception when every
  traced path of a callee terminates, restoring alloc state before the raise, and exactly the boundaries that must
  resume with a dead context catch it: the statement loop, the generator per-iteration body, and the construct
  heads that bail on a dead iterable. That deletes roughly 20 scattered guards. The correction of record is that
  a missed catch does not fail loudly: at a site that has already opened a branch the unwind leaves the live
  sibling arm with no continuation and the emitter turns it into a silent fall-through, so J1's per-site tests are
  the structural guard. Accepted delta: truncation moved from statement to expression granularity.
- **F7 and G3 place normalization.** Compile-time-constant indexes are baked into `PLACE_REAL_BLOCK` offsets
  during the passes, in SCCP's rewrite phase so GVN and aliasing see normalized places, plus the existing lowering
  fold site. G3 then extended it past the `PLACE_REAL_BLOCK` gate to dynamic-block and allocator temp-array
  places. Deliberately unfixed: the out-of-int32 accept/reject divergence between marshal-in and the fused path,
  which reaches only invalid code. An emit-time fold was considered and judged unnecessary once both emitter
  entrances deliver folded places.
- **The `derive()` subclass ban (H1).** A plain subclass of a `derive()`d archetype was shipping the base's
  callback body. Rather than making the subclass trace its own body, the shape is rejected with a located error:
  the shipped behavior was never meaningful, and banning it costs no valid program.
- **The G22 corpus restructure.** The regression corpus, goldens, and metrics gate measured 150 derived-archetype
  callbacks where the build compiles 60 base ones, so 67 percent of the hard aggregate scored callbacks that never
  ship. The corpus was restructured to measure what the build emits, which is what makes the node-count gate mean
  what it claims.
- **The G18 authorities-conflict flag.** Watch `RuntimeUIConfiguration` declares 14 slots against a repo size
  figure of 12. The maintainer reframed the finding rather than accepting it as scouted: `progress_graph` works at
  offsets 12 and 13, so the declaration is authoritative and the size figure is the wrong side of the conflict.
  The reframe is the disposition of record: no size-table change is present in the closing tree, and
  `BLOCK_MEMORY_SIZES` carries no entry for that block at all. The repo holds no oracle for real block sizes,
  which is why the closing audit could not re-run the repro and the item carries an unverified-repro tag.
  Sibling watch block sizes are worth the same scrutiny.

## Count verification

| Round | IDs | Fixed | Rejected | Deferred |
| --- | --- | --- | --- | --- |
| 1 (C, P, T, D) | 43 | 35 | 7 | 1 |
| 2 (R) | 27 | 23 | 1 | 3 |
| 3 (S) | 25 | 23 | 2 | 0 |
| 4 (F) | 17 | 14 | 0 | 3 |
| 5 (G) | 27 | 24 | 0 | 3 |
| 6 (H) | 18 | 16 | 0 | 2 |
| 7 (J) | 24 | 22 | 0 | 2 |
| **Total** | **181** | **157** | **10** | **14** |

Completeness checks: C1 through C19, P1 through P5, T1 through T13, D1 through D6, R1 through R27, S1 through
S25, F1 through F17, G1 through G27, H1 through H18, and J1 through J24 are each present exactly once, with no
gaps in any numbering. 157 + 10 + 14 = 181, and the per-round rows sum to the same totals column by column. The
Deferred recap lists 14 rows, matching the deferred column. Exactly one item, G18, carries the unverified-repro
tag.
