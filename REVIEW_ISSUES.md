# Remaining review issues

This manifest records every issue remaining after implementation commit `413f557` and review-manifest commit
`75b861f`, together with the accepted limitations and refuted leads retained from the full review session.

## Open compiler and runtime correctness issues

1. **High, validated: a runtime-skipped yield can make consecutive `next()` calls resume the wrong state.**
   When a runtime condition skips the first source `yield`, later consecutive direct `next()` calls can resume
   from the wrong source position. A generator that conditionally yields first, then yields callbacks producing
   2 and 3, returns 23 in Python when the condition is false but 22 after compilation; the true path returns 12
   as expected. The compiler indexes resume state by the compile-time `next()` call count rather than the yield
   actually reached at runtime. This occurs within one supported consumption sequence and is not iterator reuse.

## Decided not to fix, accepted policy, and refuted leads

2. **Custom `__getattribute__` implementations are unsupported.**
   Their behavior during compilation is not guaranteed. The compiler may reject an implementation when it can
   detect one without undue complexity, but rejection is best effort rather than part of the contract.

3. **`SonolusDescriptor.__get__` must not raise `AttributeError`.**
   Production descriptor getters were audited against this internal contract. Custom descriptor implementations
   must observe it so that attribute fallback remains well-defined during compilation.

4. **Invalid nonprogressing generator shapes are not required to compile.**
   A statically nonempty source combined with a compile-time-false filter cannot yield or advance. The recognized
   shape now calls `error()`, terminating instead of freezing and notifying when configured, but equivalent
   nested invalid shapes need not all be detected or assigned a meaningful completion path.

5. **Call diagnostics may eagerly inspect compile-time callable names.**
   Preparing keyword-error text can read `__qualname__` and `__name__` before arguments are visited. Compile-time
   callables are assumed not to attach meaningful side effects to those diagnostic attributes.

6. **Host `frozenset` acceptance remains undocumented.**
   Validation currently lets `set(frozenset(...))` compile, but `frozenset` is not a supported source type. The
   published stub and concepts guide intentionally continue to list only supported inputs.

7. **Terminating reads inside match subpatterns remain an undocumented restriction.**
    A property or `__len__` read that terminates every path while a pattern CFG is partially open is rejected with
    a meaningful compilation error. This niche restriction was deliberately omitted from the concepts guide.

8. **Unused or reordered runtime numeric faults need not be preserved.**
    The optimizer may delete an unused scalar fault, sink it past another operation, or move it into only a branch
    that consumes it. The Sonolus runtime does not expose these reference-interpreter faults as supported behavior;
    they are considered undefined, like invalid accesses, so eager fault timing is not an optimization constraint.

9. **Source recovery does not index lambdas inside annotations.**
    Python 3.14 can expose a callable lambda through a lazy annotation, but compiling a call to it may fail source
    recovery. Supporting this niche annotation path was explicitly declined.

10. **Nested generic functions do not bind their runtime type parameters.**
    A nested PEP 695 definition that reads its own type parameter can report the name as undefined during
    compilation. This is distinct from static generic bounds, but the runtime use is niche and was declined.

11. **Published signatures may expose internal value and runtime-check types.**
    Public inheritance and annotations currently render names such as `Value`, `GenericValue`, `TransientValue`,
    and `RuntimeChecks` without public reference pages. Hiding or replacing these leaks was explicitly deferred.

12. **The current changelog section is intentionally headed 0.19.0.**
    The prior 0.18.2 working heading was changed as requested while its prose was condensed. This is the intended
    next release heading, not a source-version mismatch.

13. **Constructor `Usage:` blocks on public constructible records are intentional.**
    Generated constructors do not render in the reference. Keep pseudo-signature `Usage:` blocks consistently on
    public records users construct, including `LifeInfo` and `Particle`; omit them from returned or engine-owned
    records. These blocks are not redundant with the visible class signature.

14. **Public debugging helpers remain undocumented and semi-public.**
    `visualize_cfg` and `simulation_context` intentionally have no public docstrings and do not render on the
    debug reference page. Their internal types and options are not a documentation gap by themselves.

15. **`SimulationContext` setup is not transactional.**
    Entry or import-hook substitution failures can leave partial testing state installed. Simulation context is a
    testing aid, and transactional rollback was explicitly judged not worth the complexity.

16. **Dictionary ordered lookup assumes consistent comparisons.**
    Fast lookup relies on keys and probes providing mutually consistent equality and total ordering. Partial or
    inconsistent user comparisons can make lookup size-dependent; this is a documented caller requirement.

17. **Extreme `round(..., ndigits)` values.**
    Very large positive or negative digit counts can overflow or underflow the scaling implementation instead of
    matching Python's limiting result. This uncommon range remains unsupported and is documented.

18. **`make_comparable_float` input-domain validation.**
    Its documented integer and range restrictions remain caller obligations. Additional guards were declined
    because the function is niche.

19. **Optimizer marshal validation for malformed numeric IR metadata.**
    Fractional block IDs, offsets, and temporary sizes can truncate, but normal frontend output does not produce
    them. Additional boundary checks were declined for performance reasons.

20. **Duplicate CFG labels in manually authored backend graphs.**
    Normal compilation stores successors in a keyed mapping and cannot create equal-label duplicates. Aggressive
    marshal validation for externally mutated internal graphs was declined.

21. **Public `BuildConfig` exposing the backend optimization type.**
    Generated signatures still show `optimize.OptimizationLevel`. Publishing or hiding the type was deferred.

22. **Useful standard-Python behavior remains in stub prose.**
    Behavioral context may remain when needed for a standalone reference. This does not justify repeating a
    signature or duplicating the same fact in a summary and `Returns:` section.

23. **Case-distinct collection names and Windows component limits.**
    Cross-platform casefold uniqueness and pre-validating the Windows 255-unit component limit were explicitly
    declined portability constraints.

24. **`sonolus-py check` remains a lightweight frontend check.**
    It does not guarantee that names, level data, configuration JSON, optimization, or packaging will succeed.

25. **Development-server reload, publication, and network limitations.**
    Stale bytecode, disabled address reuse, IPv4-only operation, in-place partial publication, retained endpoints,
    timestamp races, repository trust, and production hardening remain accepted for the manual dev server.

26. **Floating-point format, midpoint, and edge-value differences.**
    Users must not rely on f32-versus-f64 precision, exact midpoint agreement for `round`, NaN ordering,
    signed-zero operand identity, or non-finite switch-label behavior.

27. **Interpreter diagnostics differing from target invalid access.**
    Diagnostic sentinels and faults intentionally expose uninitialized optimizer reads. Optimizations need not
    preserve those interpreter-only invalid-access failures. The prior partial-array packing lead read an
    uninitialized element; no initialized valid-read counterexample was found.

28. **Deep recursive debug formatting and adversarial optimizer recursion.**
    Output at recursion-exhausting depth is not considered useful, and no practical compiler-generated subtree
    recursion failure was found.

29. **Empty closure cells referenced only by dead code.**
    Functions created before such a closure cell is initialized remain outside the intended compiled-code style.

30. **Dictionary hashes and heterogeneous unsortable constant keys.**
    Compiled lookup does not execute user hashes, and equal keys must satisfy Python's equal-hash contract.
    Heterogeneous unsortable constant keys remain unsupported.

31. **`min` and `max` over arbitrary mutable-reference iterables.**
    The compiler cannot preserve arbitrary reference semantics; supported array-like and numeric iterator paths
    remain the contract. The published `default` restriction is intentionally not narrowed to `ArrayLike`:
    runtime iterators and runtime-length array-like values both require numeric elements when emptiness is known
    only at runtime.

32. **Compile-time-only archetypes returned by name lookup.**
    Exposing these bases during compilation is accepted even though they are not shipped and have no runtime ID.

33. **`Project.resources` and `Project.converters` documentation mentions only `dev`.**
    Programmatic builds also consume them, but correcting this published description was explicitly declined.

34. **Source recovery assumes UTF-8.**
    Non-UTF-8 engine source remains outside the project convention even when Python can execute it via PEP 263.

35. **Compilation installs a process-global exception hook.**
    Preserving or chaining an application's existing hook remains an unresolved integration choice.

36. **Python 3.14 semantics apply on every supported host.**
    The compiler follows Python 3.14 behavior rather than adding version-dependent semantics for older hosts.
    Python 3.12 and 3.13 eager-annotation differences are therefore not bugs.

37. **Optimizer toolchain helper overflow.**
    Private test helper `nogil_sum` can overflow a Windows C `long`; it is not production code.

38. **Export directory writers are additive.**
    Optional files are not removed when a later write omits them; these APIs do not synchronize directories.

39. **Missing goldens are created by the regression helper.**
    This is the documented delete-and-rerun regeneration workflow.

40. **Mutable cached-hash backend place objects.**
    Post-construction mutation can corrupt sets or dictionaries, but the compiler performs no such mutation.

41. **Traceback cause restoration after traceback printing fails.**
    Defensive recovery was declined once diagnostic output itself is unavailable.

42. **Optimizer profiling across concurrent programmatic builds.**
    Profiling is process-global and internal; concurrent callers must coordinate reset, snapshot, and toggling.

43. **Impure value identity through optimizer place fusion.**
    No reachable counterexample was found because separate evaluations receive distinct value IDs.

44. **Backend helper `run_fuse_rmw` with a side-effecting right operand.**
    The direct test helper can reorder a hand-built malformed expression, but production treeification
    materializes the operand and all normal optimization pipelines preserve behavior.

45. **Generic bounds and constraints are static metadata.**
    Python permits runtime parameterization outside a PEP 695 bound or constraint. Sonolus enforces only concrete
    storage and layout requirements, leaving ordinary bounds to static type checkers.

46. **Private `_remainder` constant folding at a zero divisor.**
    Its native constant evaluator can raise `ZeroDivisionError`, but the helper has no public registration or
    production caller. This dormant path does not justify changing native folding policy by itself.

47. **Fixed lead-time guidance for scheduled effects.**
    The current public contract recommends scheduling the three scheduled sound-effect operations at least 0.5
    seconds ahead when possible. Removing that wording was rejected because the latest user decision restored it.

48. **Meta-function positional configuration.**
    Runtime `meta_fn(False)` remains invalid; the corrected overload exposes configuration only as a keyword and
    supports both direct and decorator-factory forms.

49. **Abstract contracts without `ABC`.**
    `SonolusDescriptor` and `BackingValue` deliberately avoid `ABC` because of metaclass interactions. Their
    abstract methods document the contract and raise `NotImplementedError`, but instantiation is not blocked.

50. **Internal array-iterator generic annotations.**
    Private iterator type variables describe the backing container rather than its element in a few annotations.
    Runtime behavior is correct and these underscore types are unpublished, so this was not escalated.

51. **Unused random draws may be optimized away.**
    SCCP and dead-code elimination can remove a random operand whose result cannot affect the program. Existing
    optimizer tests and the runtime-cost policy explicitly permit deleting such unused draws. Runtime numeric
    faults are governed by the broader accepted optimization policy in item 8.

52. **Normal build, verbose diagnostics, callback, export, and localization behavior was rechecked.**
    Module detection, `BuildConfig.verbose`, port defaults, callback lists, export requirements, and localization
    values match their definitions and documented contracts. The build audit found no additional issue beyond
    leads independently recorded above.

53. **Compiled range, random, builtin, and container behavior was rechecked.**
    Range arithmetic outside the fixed `index` shape, reversed-bound uniform, duplicate keys, generic cache
    isolation, and runtime `zip(strict=...)` match their supported contracts. The builtin and backend audits found
    no additional issue beyond leads independently recorded above.

54. **Generated documentation and release integrity checks.**
    Private-name leaks fixed in prior passes were not reintroduced; the remaining internal-type exposure is the
    accepted policy in item 11. Entity-data terminology is not contradictory, and strict MkDocs found no broken
    links or anchors. The intentionally forward `0.19.0` changelog heading is covered by item 12.

55. **Keyword-only defaults of `None` in nested functions.**
    Source `None` is wrapped as a compile-time constant and is not confused with a missing default.

56. **Flow-edge numeric annotation difference.**
    Annotating a condition as `float` still accepts `int` under the typing numeric tower; this is not an API bug.

57. **Invalid surplus arguments to `DebugPause`.**
    The operation is zero-argument in public and emitted code. Extra operands occur only in malformed internal IR.

58. **Regression prose and whitespace-only debt.**
    Non-semantic trailing spaces and already-covered mechanical prose are not separate correctness findings.

59. **Starting a second generator consumption sequence is unsupported.**
    A generator may participate in only one consumption sequence. Starting another loop or consumer, or mixing
    direct `next()` calls with another form of consumption, is unsupported even before exhaustion. A run of
    consecutive `next()` calls is one consumption sequence.

60. **Visitor evaluation order and ordinary argument binding were rechecked.**
    Mixed starred arguments and keywords, defaults for every parameter kind, mutable defaults, argument snapshots,
    decorator evaluation, assignment sequencing, match flow, and ordinary expression termination matched Python
    or their documented subset contracts. Fresh exceptions are recorded explicitly above.

61. **Scope scanners and established nested-function cases were rechecked.**
    Function lexical locals, dead-path `global` or `nonlocal` rejection, late closure lookup, keyword-only `None`
    defaults, eager outermost generator-expression iterables, preceding-target lookup, same-line lambda
    disambiguation, and definition-time write scanning behaved as intended. Conservatively tracking a bare
    annotation at a loop header caused only unnecessary bookkeeping, with no semantic divergence found.
