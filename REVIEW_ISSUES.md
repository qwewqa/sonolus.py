# Remaining review issues

This manifest records issues remaining after commit `7f46456`, together with accepted limitations and refuted
leads from this review session. Fixed items are omitted.

Confidence labels:

- **Validated**: independently reproduced after the audit lead was reported.
- **Reproduced**: reproduced during the audit but not independently challenged.
- **Lead**: supported by source analysis and still needs focused validation.

## Open correctness issues

1. **High, validated: expression visitors continue after a child terminates compilation.**
   Many visitors evaluate later children after an earlier child makes the context dead. Confirmed paths include
   binary and conditional expressions, `or`, dict/set/tuple displays, the first comparison operand, calls,
   subscripts, and assignment-target sequencing. Unreachable names or meta functions can therefore cause false
   compilation failures or compile-time effects. Add liveness checks after each ordered child evaluation.

2. **Medium, validated: decorators and defaults continue after an earlier expression terminates.**
   Function decorators and positional or keyword-only defaults are collected without stopping when the context
   becomes dead; lambda defaults have the same problem. Python stops at the first terminating expression. Preserve
   that evaluation order for both functions and lambdas.

3. **Medium, validated: `SimulationContext` setup is not transactional.**
   If module substitution fails during `__enter__`, the active global context and earlier replacements can remain
   installed because `__exit__` is never called. A failure from the import hook can likewise leave a partially
   substituted module in `sys.modules`. Roll back context, hooks, and each module atomically on failure.

4. **Medium, validated: iterator consumers do not consistently require `Maybe` results.**
   Builtin `next`, `maybe_next`, numeric extrema, enumerate, zip, map, and filter adapters call or dereference a
   custom iterator's `next()` result without the explicit `Maybe` validation now used by loops and `yield from`.
   Invalid results cause incidental errors or can pass through structural lookalikes. Centralize the protocol
   check at every untrusted iterator boundary.

5. **Medium, validated: dictionary binary search assumes an unproven total order.**
   Ordered lookup is enabled after checking only the first two stored keys, then branches using the probe's `<`.
   Partial, inconsistent, or cross-type ordering can produce errors or false negatives, and behavior changes when
   the dictionary grows beyond the linear-search leaf size. Use linear equality lookup unless a safe order domain
   can be proven.

6. **Medium, validated: extreme finite `round(..., ndigits)` values fail.**
   Scaling by `10**ndigits` can overflow or underflow before rounding. Python returns the original finite value for
   a very large positive `ndigits` and zero for a very large negative one; the compiled implementation can raise or
   produce invalid arithmetic. Handle the extreme ranges without materializing an unusable scale.

7. **Medium, validated: duplicate project level names silently overwrite output.**
   Both direct builds and collection builds write levels by name, so a later same-name level replaces the earlier
   payload. Reject duplicate names in `Project.levels` before packaging or writing.

8. **Low, validated: `BasicBlock` ownership depends on whether supplied containers are empty.**
   The constructor uses `value or default` for phi, statements, incoming, and outgoing. Empty caller-owned
   containers are discarded while nonempty ones are retained by identity. Use explicit `None` checks and add
   identity and mutation tests.

9. **Low, validated: empty Engine and Level titles fall back to the name.**
   Constructors use `title or name`, although the public contract says fallback occurs only when title is unset.
   Empty strings and empty localization mappings are valid explicit values. Test `title is None` instead.

10. **Low, validated: tutorial instruction APIs expose an indirect private error.**
    `Instruction.show`, `show_instruction`, and `clear_instruction` are tutorial-only but do not document that
    restriction or guard it explicitly. Other modes fail through `_TutorialInstruction`, leaking a private name.
    Publish the restriction and provide stable public-facing diagnostics.

11. **Low, validated: `Collection.write` has a false output-path annotation.**
    It accepts `Asset`, which includes bytes and URL strings, but immediately passes the value to `Path`. Narrow
    the annotation, and the helper annotation, to filesystem path types.

12. **Low, validated: `meta_fn` overloads do not match its call forms.**
    The overload accepts positional `meta_fn(False)`, which crashes at runtime, and omits supported `meta_fn()`.
    Make the configuration parameter keyword-only in the decorator-factory overload.

13. **Low, validated: internal abstract contracts are not enforced.**
    `SonolusDescriptor` declares abstract methods without inheriting `ABC`, so incomplete subclasses instantiate
    and inherited pass bodies silently return. `BackingValue` has a similar weak contract. Apply the repository's
    abstract-method pattern with docstrings and `NotImplementedError` bodies.

14. **Low, validated: dormant archetype registration leaves derived state stale.**
    `register_archetype` invalidates one subclass cache but not the MRO ROM cache or other archetype-derived maps.
    The method and related helpers currently have no callers. Remove the dead API or redesign registration to
    update every derived structure atomically.

## Open documentation and prose issues

15. **Low, reproduced: stream and easing summaries still violate prose standards.**
    Two stream class summaries begin with `Represents`, several stream and easing summaries exceed 120 columns,
    and vector normalization documents assertion machinery instead of the nonzero-vector restriction.

16. **Low, reproduced: public container restrictions are vague or expose failure mechanics.**
    `VarArray.append_unchecked` and `ArrayPointer` warn about hard-to-debug behavior without stating a usable
    contract. `ArrayMap` deletion and pop describe callback termination for invalid input instead of stating that
    the key must be present.

17. **Low, reproduced: backend and test comments retain mechanical narration.**
    `place.py` narrates its hash assignment without explaining the size omission; flow ordering embeds a drifting
    corpus percentage; interpreter-oracle tests contain section banners and assertion paraphrases. Preserve only
    the semantic pins for operation meaning and addressing.

18. **Low, reproduced: frontend comments narrate visible control flow.**
    Visitor comments such as `This will never run`, `Unroll the loop`, and `Skip the else block` restate the code;
    two nearby future-proofing comments remain vague. Remove them unless a concrete invariant is supplied.

19. **Low, reproduced: one build diagnostic line exceeds 120 columns.**
    The project-import error construction in `sonolus/build/cli.py` remains over the prose limit.

## Decided not to fix or accepted policy

20. **`make_comparable_float` input-domain validation.**
    Its documented integer and range restrictions remain caller obligations. Additional guards were declined
    because the function is niche.

21. **Optimizer marshal validation for malformed numeric IR metadata.**
    Fractional block IDs, offsets, and temporary sizes can truncate, but normal frontend output does not produce
    them. Additional boundary checks were declined for performance reasons.

22. **Duplicate CFG labels in manually authored backend graphs.**
    Normal compilation stores successors in a keyed mapping and cannot create equal-label duplicates. Existing
    compiler and corpus tests cover that invariant; aggressive marshal validation was declined.

23. **Public `BuildConfig` exposing the backend optimization type.**
    Generated signatures still show `optimize.OptimizationLevel`. Publishing or hiding the type was deferred.

24. **Useful standard-Python behavior remains in stub prose.**
    Behavioral context should remain when removing it would make a standalone reference awkward to understand.
    This does not justify repeating a signature or duplicating the same fact in a summary and `Returns:` section.

25. **Case-distinct collection names and Windows component limits.**
    Cross-platform casefold uniqueness and pre-validating the Windows 255-unit component limit were explicitly
    declined portability constraints.

26. **`sonolus-py check` remains a lightweight frontend check.**
    It does not guarantee that names, level data, configuration JSON, optimization, or packaging will succeed.

27. **Development-server reload, publication, and network limitations.**
    Stale bytecode, disabled address reuse, IPv4-only operation, in-place partial publication, retained endpoints,
    timestamp races, repository trust, and production hardening remain accepted for the manual dev server.

28. **Floating-point format, midpoint, and edge-value differences.**
    Users must not rely on f32-versus-f64 precision, exact midpoint agreement for `round`, NaN ordering,
    signed-zero operand identity, or non-finite switch-label behavior. Extreme finite `ndigits` remains distinct
    and open above.

29. **Interpreter diagnostics differing from target invalid access.**
    Diagnostic sentinels and faults intentionally expose uninitialized optimizer reads. Optimizations need not
    preserve those interpreter-only invalid-access failures.

30. **Deep recursive debug formatting and adversarial optimizer recursion.**
    Output at recursion-exhausting depth is not considered useful, and no practical compiler-generated subtree
    recursion failure was found.

31. **Empty closure cells referenced only by dead code.**
    Functions created before such a closure cell is initialized remain outside the intended compiled-code style.

32. **Dictionary hashes and heterogeneous unsortable constant keys.**
    Compiled lookup does not execute user hashes, and equal keys must satisfy Python's equal-hash contract.
    Heterogeneous unsortable constant keys remain unsupported. The open binary-search ordering issue is separate.

33. **`min` and `max` over arbitrary mutable-reference iterables.**
    The compiler cannot preserve arbitrary reference semantics; supported array-like and numeric iterator paths
    remain the contract.

34. **Compile-time-only archetypes returned by name lookup.**
    Exposing these bases during compilation is accepted even though they are not shipped and have no runtime ID.

35. **`Project.resources` and `Project.converters` documentation mentions only `dev`.**
    Programmatic builds also consume them, but correcting this published description was explicitly declined.

36. **Source recovery assumes UTF-8.**
    Non-UTF-8 engine source remains outside the project convention even when Python can execute it via PEP 263.

37. **Compilation installs a process-global exception hook.**
    Preserving or chaining an application's existing hook remains an unresolved integration choice.

38. **Python 3.14 semantics apply on every supported host.**
    The compiler follows Python 3.14 behavior rather than adding version-dependent semantics for older hosts. The
    membership behavior fixed in `7f46456` is one explicit application of this policy.

39. **Optimizer toolchain helper overflow.**
    Private test helper `nogil_sum` can overflow a Windows C `long`; it is not production code.

40. **Export directory writers are additive.**
    Optional files are not removed when a later write omits them; these APIs do not synchronize directories.

41. **Missing goldens are created by the regression helper.**
    This is the documented delete-and-rerun regeneration workflow.

42. **Mutable cached-hash backend place objects.**
    Post-construction mutation can corrupt sets or dictionaries, but the compiler performs no such mutation.

43. **Traceback cause restoration after traceback printing fails.**
    Defensive recovery was declined once diagnostic output itself is unavailable.

44. **Optimizer profiling across concurrent programmatic builds.**
    Profiling is process-global and internal; concurrent callers must coordinate reset, snapshot, and toggling.

45. **Impure value identity through optimizer place fusion.**
    No reachable counterexample was found because separate evaluations receive distinct value IDs.

46. **`BuildConfig.verbose`, `check`, module detection, and port defaults.**
    These behaviors were checked and found intentional or correctly documented.

47. **Callback lists, export requirements, and localization.**
    The inspected build paths match their definitions and documented values; suspected inconsistencies were
    refuted.

48. **Compiled range, random, and container behavior checked in prior passes.**
    Range arithmetic outside the fixed `index` shape, reversed-bound uniform, union ordering, duplicate keys,
    generic cache isolation, and runtime `zip(strict=...)` match their supported contracts.

49. **Generated documentation and release integrity checks.**
    The site no longer leaks `_QuadLike`; entity-data terminology is not contradictory; unreleased version skew is
    normal; strict MkDocs found no broken links or anchors.

50. **Keyword-only defaults of `None` in nested functions.**
    Source `None` is wrapped as a compile-time constant and is not confused with a missing default.

51. **Flow-edge numeric annotation difference.**
    Annotating a condition as `float` still accepts `int` under the typing numeric tower; this is not an API bug.

52. **Invalid surplus arguments to `DebugPause`.**
    The operation is zero-argument in public and emitted code. Extra operands occur only in malformed internal IR.

53. **Regression prose and whitespace-only debt.**
    Non-semantic trailing spaces and already-covered mechanical prose are not separate correctness findings.
