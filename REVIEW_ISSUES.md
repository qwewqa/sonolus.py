# Remaining review issues

This manifest records issues remaining after commit `857b926`. It includes every remaining issue and every issue
explicitly rejected or accepted during this review session, so later reviews can distinguish open work from
settled policy.

Confidence labels:

- **Validated**: independently reproduced after the audit lead was reported.
- **Reproduced**: reproduced during the audit but not independently challenged.
- **Lead**: supported by source analysis and still needs focused validation.

## Open correctness issues

1. **Medium, validated: normal builds suppress output-cleanup failures.**
   `build_project` and `write_collection(clear=True)` ignore `shutil.rmtree` errors, then write into the surviving
   directory and report success. Removed levels or assets can remain in ordinary build output. Cleanup failures
   should abort the build; this is separate from the accepted incremental behavior of the development server.

2. **Medium, validated: repeated entity instances produce ambiguous level references.**
   `build_level_data` maps entities by object identity. Reusing one instance at multiple positions gives both
   serialized entries the later position's name, and every reference resolves to that later occurrence. Reject a
   repeated identity while continuing to allow distinct instances with equal field values.

3. **Medium, validated: same-stem source resources overwrite each other silently.**
   `Collection.load_from_source` keys resources by filename stem. Files such as `data.bin` and `data.json` resolve
   to the same field; the later file wins and the earlier repository object becomes unreachable. A resource stem
   can also overwrite an `item.json` field. Reject duplicate resolved keys before adding assets.

4. **Medium, validated: `make_comparable_float` does not enforce its input domain.**
   The public contract requires integer pairs satisfying `0 <= value < max_value` and a product of maxima below
   `2**31`. Negative, fractional, out-of-range, zero-maximum, and runtime-oversized inputs are accepted and can
   produce sentinel-like values. Use runtime assertions so invalid calls in dead runtime branches still compile.

5. **Medium, validated: sequence `index` bounds are not consistently Python-compatible.**
   `ArrayLike.index` and `TupleImpl.index` accept fractional `start` or `stop`; runtime-valued array bounds can
   reach unchecked fractional memory indices. Tuple bounds also accept keywords. Require integer-valued bounds,
   preserve dead-branch compilation, and make tuple bounds positional-only.

6. **Medium, validated: compiled `range.index` accepts unsupported arguments.**
   Python 3.14 accepts exactly one positional argument after `self`. The compiled replacement also accepts
   `start`, `stop`, and keywords, so code can compile successfully but fail in Python or debug execution. Narrow
   the signature to `index(self, value, /)`.

7. **Medium, validated: `yield from` does not validate the iterator result protocol.**
   Ordinary loops and generator expressions require `iterator.next()` to return `Maybe`, but `yield from`
   dereferences `_present` and `_value` directly. It therefore produces incidental attribute errors or accepts an
   unrelated record with similarly named fields. Apply the same explicit `Maybe` validation as other iteration.

8. **Low-medium, validated: membership still depends on the host Python version.**
   When `__contains__` returns `NotImplemented`, the compiler calls host `bool(NotImplemented)`. Python 3.14
   raises `TypeError`, while older hosts can treat it as true with a warning. The project policy is to follow 3.14
   on every host, so this path should raise the 3.14 error explicitly for both `in` and `not in`.

9. **Medium, validated: `run_and_validate` checks only one compiled exception leg.**
   When plain Python raises, the test helper re-raises inside its first execution iteration. It therefore checks
   only the non-ROM minimal-optimization run instead of all six closure and optimization combinations, and it
   skips exception-path log parity. Complete every compiled leg before re-raising the saved Python exception.

10. **Low-medium, validated: optimizer marshal-in silently truncates numeric IR metadata.**
    Fractional raw block IDs, offsets, and temporary sizes are cast to integers. Fractional temporary sizes can
    additionally select an array kind before truncating to a scalar-sized allocation. Require exact non-boolean
    integers with explicit ranges at the marshal boundary.

11. **Low, validated: duplicate CFG-label validation is bypassable.**
    `BasicBlock.connect_to` rejects duplicate labels, but callers can supply `outgoing=` or mutate the edge set
    directly. Marshal-in then keeps an arbitrary equal-label edge. Keep the early check and add authoritative
    validation at the optimizer boundary.

12. **Low, validated: an explicit `None` attribute does not mask an inherited descriptor.**
    Visitor and builtin `getattr` MRO searches use `dict.get(..., None)` as both the missing sentinel and a value.
    A subclass attribute set to `None` is skipped and an inherited property may run instead. Test membership in
    `cls.__dict__` or use a distinct sentinel.

13. **Low, validated: mixed-case HTTP schemes are treated as filesystem paths.**
    URI schemes are case-insensitive, but asset and resource loaders recognize only lowercase `http://` and
    `https://`. Preserve the original URL while comparing its scheme case-insensitively.

## Open documentation and API issues

14. **Medium, validated: scheduled-effect latency guidance is inconsistent.**
    `Effect.schedule`, `Effect.schedule_loop`, and `ScheduledLoopedEffectHandle.stop` should all explain that
    scheduling shortly before the target can cause unexpected latency. Do not prescribe a fixed lead time or
    restore the removed 0.5-second recommendation.

15. **Medium, validated: public `BuildConfig` signatures expose an unpublished backend type.**
    Generated reference pages render `optimize.OptimizationLevel` for optimization constants and `passes`.
    `sonolus.backend.optimize` has no public reference page, leaving users with an unlinked internal-looking type.
    Publish an appropriate type or hide the backend annotation from the public signature.

16. **Low, validated: public property summaries use function-style imperatives.**
    Published properties in containers, effects, maybe values, runtime UI, sprites, and vectors begin with verbs
    such as `Return` or `Check`. Property summaries should be noun phrases, and redundant `Returns:` blocks should
    be removed.

17. **Low, validated: published stubs repeat standard Python behavior.**
    Several builtin and random stubs repeat their summaries in `Returns:` and give argument descriptions that add
    no contract information. This is especially visible for `all`, `any`, `enumerate`, `filter`, `zip`, `random`,
    and `uniform`. Retain Sonolus-specific restrictions and remove restatement.

18. **Low, validated: optimizer profiling prose overstates its behavior.**
    Profiling is described as timing each pass with zero disabled cost, but it records coarse pipeline stages and
    still tests the enabled flag. Its displayed snapshot shape is also misleading, and concurrency rationale is
    scoped only to serial CLI builds. Rewrite the module and driver comments around observable stage timing.

19. **Low, reproduced: `DictImpl.get` requires an explicit default without documenting the restriction.**
    Ordinary `dict.get(key)` is valid, but the compiled implementation requires `get(key, default)`. Either add a
    supported one-argument form or publish the restriction and rationale.

20. **Low, reproduced: CLI `--verbose` help promises broader verbosity than it provides.**
    The flag controls full tracebacks for compilation errors; it does not generally make output verbose. Use the
    precise wording already present in the CLI concepts page.

21. **Low, validated: existing prose exceeds project clarity and line-length standards.**
    Long lines remain in concepts pages, public docstrings, and comments. Additional comments mechanically narrate
    container algorithms, archetype initialization, records, globals, and unchecked access without preserving a
    durable rationale. Treat this as careful prose debt: retain load-bearing invariants, remove narration, and keep
    changed prose within 120 columns.

## Decided not to fix or accepted policy

22. **Case-distinct collection names colliding on Windows.**
    Windows can map `pixel` and `PIXEL` to one file. Cross-platform casefold uniqueness was explicitly declined;
    this remains an accepted portability limitation of collection output.

23. **Windows component-length validation.**
    Names over 255 UTF-16 code units can fail during publication. Pre-validating the Windows component limit was
    explicitly declined.

24. **`sonolus-py check` validates frontend code rather than complete project data.**
    The command intentionally remains a lightweight callback/frontend check. It does not promise that names,
    level data, configuration JSON, optimization, or packaging will succeed.

25. **Development-server reload and publication limitations.**
    Stale timestamp bytecode, disabled address reuse, in-place partial publication, retained endpoints, resource
    timestamp races, repository trust, and production-grade network hardening were all declined for the manual
    development server. The normal-build cleanup failure is separate and remains open above.

26. **Development-server IPv6 support is deferred.**
    Address discovery filters IPv6 and the server uses `AF_INET`. IPv6-only development environments remain
    unsupported unless the development-network support policy changes.

27. **Floating-point format and edge-value differences.**
    Users must not rely on f32-versus-f64 precision, NaN ordering, signed-zero operand identity, or non-finite
    switch-label behavior. This covers SCCP folding and branch decisions near f32 boundaries, NaN edge ordering,
    and `min`/`max` tie identity.

28. **Interpreter diagnostics differing from target-runtime invalid access.**
    Interpreter `Get` sentinels and faults intentionally expose uninitialized or invalid optimizer reads even when
    the real runtime returns zero or does not trap. DCE and self-copy removal need not preserve those diagnostics.

29. **Deep recursive engine-node formatting.**
    The formatter is a debugging aid, and output at the depth that exhausts recursion is not considered useful.

30. **Empty closure cells referenced only by dead code.**
    Functions created before such a closure cell is initialized remain outside the intended compiled-code style.

31. **Dictionary hashes and heterogeneous unsortable constant keys.**
    Compiled lookup does not execute user hashes, and equal keys are expected to satisfy Python's equal-hash
    contract. Heterogeneous unsortable constant keys remain unsupported to avoid input-dependent lookup costs.

32. **`min` and `max` over arbitrary mutable-reference iterables.**
    The compiler cannot preserve arbitrary reference semantics; supported array-like and numeric iterator paths
    remain the contract.

33. **Compile-time-only archetypes returned by name lookup.**
    Exposing these bases during compilation is accepted even though they are not shipped and have no runtime ID.

34. **`Project.resources` and `Project.converters` documentation mentions only `dev`.**
    Programmatic builds also consume them, but correcting this published description was explicitly declined.

35. **Source recovery assumes UTF-8.**
    PEP 263 encoded source can execute in Python but fail compiler source recovery. Non-UTF-8 engine source remains
    outside the project convention.

36. **Compilation installs a process-global exception hook.**
    Preserving or chaining an application's existing `sys.excepthook` remains an unresolved integration choice.

37. **Python 3.14 semantics on every supported host.**
    The compiler deliberately follows 3.14 deferred-annotation behavior on Python 3.12 and 3.13 instead of adding
    version-specific annotation evaluation. Other discovered host-version branches should likewise be normalized
    to 3.14 semantics.

38. **Optimizer toolchain helper overflow.**
    Private test helper `nogil_sum` overflows a Windows C `long` at large inputs. It is not production code and was
    explicitly declined.

39. **Export directory writers are additive.**
    Rewriting an exported level or engine with an optional asset set to `None` does not remove a previous file.
    The methods promise to overwrite files they write, not synchronize or destructively clear a directory.

40. **Missing goldens are created by the regression helper.**
    This is the documented delete-and-rerun regeneration workflow, not a test failure policy.

41. **Mutable cached-hash backend place objects.**
    Mutating equality fields after constructing internal IR places can corrupt sets or dictionaries. The compiler
    performs no such mutation, so post-construction mutation remains unsupported internal misuse.

42. **Traceback cause restoration if traceback printing itself fails.**
    `print_simple_traceback` may not restore `__cause__` if the diagnostic stream fails while printing. Defensive
    hardening is not considered worthwhile after traceback output itself has become unavailable.

43. **Optimizer profiling across concurrent programmatic builds.**
    Profiling state is process-global and concurrent reset, snapshot, toggling, or compilation can mix samples.
    Profiling is internal and CLI builds are serial; concurrent callers must coordinate externally.

44. **Impure value identity through optimizer place fusion.**
    `_values_equal(v, v)` precedes an impurity guard, but marshal and lowering create distinct value IDs for each
    evaluation. No reachable place-fusion counterexample was found.

45. **Recursive optimizer subtree analysis.**
    Exhausting the C stack would require an adversarially enormous lowered RMW value graph. No practical failure
    threshold or compiler-generated reproducer was found.

46. **`BuildConfig.verbose` and `check` optimization behavior.**
    `BuildConfig.verbose` accurately describes compilation-error tracebacks, and `check` deliberately ignores
    optimization flags. Neither is a build correctness issue.

47. **Project module detection and port defaults.**
    CLI project-module detection behaved as documented. The programmatic server default of 8080 and CLI default
    of 8000 are intentional interfaces, not a mismatched shared default.

48. **Callback lists, export requirements, and localization.**
    Mode callback enumerations match their definitions, required engine export assets are enforced, and inspected
    localization paths preserve the documented values. The suspected build inconsistencies were refuted.

49. **Compiled range semantics outside `range.index`.**
    Range length, containment, equality, negative steps, zero steps, and integer-domain validation were checked and
    match the supported contract. Only the open `range.index` arity issue remains.

50. **Compiled random and container behavior.**
    Reversed-bound `uniform`, dict and set union ordering, duplicate-key behavior, generic cache isolation, and
    runtime `zip(strict=...)` were checked and match their supported contracts.

51. **Generated documentation and entity-data terminology.**
    The generated site no longer leaks `_QuadLike`. Calling entity data private to the engine means it is excluded
    from level input, not that the author API is hidden; the wording is not contradictory.

52. **Changelog versioning and reference integrity.**
    An unreleased changelog version newer than `pyproject.toml` is normal. Strict MkDocs found no broken links or
    anchors, and the new 0.18.2 bullets are concise user-observable changes rather than redundant release prose.

53. **Keyword-only defaults of `None` in nested functions.**
    The visitor wraps source `None` as a compile-time constant rather than confusing it with a missing default, so
    the suspected signature bug was refuted.
