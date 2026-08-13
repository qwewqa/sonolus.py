# Remaining review issues

This manifest records the accepted limitations, policy decisions, resolved documentation leads, and refuted leads
for the current implementation.

Unsupported behavior is not an open issue merely because the implementation can accept it in some forms or fail
differently in others. Best-effort diagnostics and runtime checks do not expand the supported contract.

## Open issues

No open issues remain from this review round.

## Decided not to fix, accepted policy, resolved documentation, and refuted leads

1. **`__getattribute__` behavior is unspecified.**
   Its behavior during compilation is not guaranteed. The compiler may reject an implementation when it can
   detect one without undue complexity, but rejection is best effort rather than part of the contract.

2. **Private callable-classification markers must not be synthesized.**
   A callable `Record` can use `__getattr__` to synthesize `_meta_fn_` and be treated as a compile-time meta
   function. `_meta_fn_` is a private compiler marker, and adversarially spoofing it is outside the supported
   contract, so additional defensive classification was declined.

3. **`SonolusDescriptor.__get__` must not raise `AttributeError`.**
   Production descriptor getters observe this internal contract. Custom descriptor implementations must observe
   it so that attribute fallback remains well-defined during compilation.

4. **Reference-valued generator closures may be rejected conservatively.**
   Scope merging can reject a capture even when all runtime paths would select one reference. The over-rejection
   appears in rare generator-expression, filter, branch, and completion shapes. Preserving safety is preferred to
   accepting every provably valid reference merge, so this behavior was explicitly retained.

5. **Invalid nonprogressing generator shapes are not required to compile.**
   A statically nonempty source combined with a compile-time-false filter cannot yield or advance. The recognized
   shape calls `error()`, terminating instead of freezing and notifying when configured, but equivalent nested
   invalid shapes need not all be detected or assigned a meaningful completion path.

6. **Using an iterator more than once is unsupported.**
   An iterator may be used by exactly one reached `for` loop, one reached `next()` call, or one other iterator
   consumer. A loop may advance its iterator repeatedly, but reaching another consumer, including the same
   `next()` expression again, is unsupported. The stale numeric and wrong reference-binding closure probes both
   require such a later advance; no divergence is known with one supported consumer.

   With runtime checks enabled, generated generators track a consumer owner and reject a later advance by another
   owner on a best-effort basis. This check does not make unsupported reuse valid when checks are disabled, and it
   does not promise equivalent detection for every iterator implementation.

7. **Iterator and nested-generator documentation now states the intended boundary.**
   Nested generators that capture changing variables from another generator are unsupported; invariant captures
   are no longer excluded. The iterator guide now states the one-use rule directly. The previous advice to copy
   yielded values is unnecessary because no supported one-consumer counterexample is known.

8. **Call diagnostics may eagerly inspect compile-time callable names.**
   Preparing keyword-error text can read `__qualname__` and `__name__` before arguments are visited. Compile-time
   callables are assumed not to attach meaningful side effects to those diagnostic attributes.

9. **Host `frozenset` acceptance remains undocumented.**
   Validation currently lets `set(frozenset(...))` compile, but `frozenset` is not a supported source type. The
   published stub and concepts guide intentionally continue to list only supported inputs.

10. **Terminating reads inside match subpatterns remain an undocumented restriction.**
    A property or `__len__` read that terminates every path while a pattern CFG is partially open is rejected with
    a meaningful compilation error. This niche restriction was deliberately omitted from the concepts guide.

11. **Lazy `Project` level-source failures are not sticky.**
    After a lazy level source fails, another access may return an empty result instead of retaining or repeating
    the failure. This unusual recovery path was explicitly judged not worth additional state and complexity.

12. **Unused or reordered runtime numeric faults need not be preserved.**
    The optimizer may delete an unused scalar fault, sink it past another operation, or move it into only a branch
    that consumes it. The Sonolus runtime does not expose these reference-interpreter faults as supported behavior;
    they are considered undefined, like invalid accesses, so eager fault timing is not an optimization constraint.

13. **Source recovery does not index lambdas inside annotations.**
    Python 3.14 can expose a callable lambda through a lazy annotation, but compiling a call to it may fail source
    recovery. Supporting this niche annotation path was explicitly declined.

14. **Nested generic functions do not bind their runtime type parameters.**
    A nested PEP 695 definition that reads its own type parameter can report the name as undefined during
    compilation. This is distinct from static generic bounds, but the runtime use is niche and was declined.

15. **Published signatures may expose internal value and runtime-check types.**
    Public inheritance and annotations currently render names such as `Value`, `GenericValue`, `TransientValue`,
    and `RuntimeChecks` without public reference pages. Hiding or replacing these leaks was explicitly deferred.

16. **The current changelog section is intentionally headed 0.19.0.**
    This is the intended next release heading, not a source-version mismatch.

17. **Constructor `Usage:` blocks on public constructible records are intentional.**
    Generated constructors do not render in the reference. Keep pseudo-signature `Usage:` blocks consistently on
    public records users construct, including `LifeInfo` and `Particle`; omit them from returned or engine-owned
    records. These blocks are not redundant with the visible class signature.

18. **Public debugging helpers remain undocumented and semi-public.**
    `visualize_cfg` and `simulation_context` intentionally have no public docstrings and do not render on the
    debug reference page. Their internal types and options are not a documentation gap by themselves.

19. **`SimulationContext` setup is not transactional.**
    Entry or import-hook substitution failures can leave partial testing state installed. Simulation context is a
    testing aid, and transactional rollback was explicitly judged not worth the complexity.

20. **Dictionary ordered lookup assumes consistent comparisons.**
    Fast lookup relies on keys and probes providing mutually consistent equality and total ordering. Partial or
    inconsistent user comparisons can make lookup size-dependent; this is a documented caller requirement.

21. **Extreme `round(..., ndigits)` values.**
    Very large positive or negative digit counts can overflow or underflow the scaling implementation instead of
    matching Python's limiting result. This uncommon range remains unsupported and is documented.

22. **`make_comparable_float` input-domain validation.**
    Its documented integer and range restrictions remain caller obligations. Additional guards were declined
    because the function is niche.

23. **Optimizer marshal validation for malformed numeric IR metadata.**
    Fractional block IDs, offsets, and temporary sizes can truncate, but normal frontend output does not produce
    them. Additional boundary checks were declined for performance reasons.

24. **Duplicate CFG labels in manually authored backend graphs.**
    Normal compilation stores successors in a keyed mapping and cannot create equal-label duplicates. Aggressive
    marshal validation for externally mutated internal graphs was declined.

25. **Public `BuildConfig` exposing the backend optimization type.**
    Generated signatures still show `optimize.OptimizationLevel`. Publishing or hiding the type was deferred.

26. **Useful standard-Python behavior remains in stub prose.**
    Behavioral context may remain when needed for a standalone reference. This does not justify repeating a
    signature or duplicating the same fact in a summary and `Returns:` section.

27. **Case-distinct collection names and Windows component limits.**
    Cross-platform casefold uniqueness and pre-validating the Windows 255-unit component limit were explicitly
    declined portability constraints.

28. **`sonolus-py check` remains a lightweight frontend check.**
    It does not guarantee that names, level data, configuration JSON, optimization, or packaging will succeed.

29. **Development-server reload, publication, and network limitations.**
    Stale bytecode, disabled address reuse, IPv4-only operation, in-place partial publication, retained endpoints,
    timestamp races, repository trust, and production hardening remain accepted for the manual dev server.

30. **Floating-point format, midpoint, and edge-value differences.**
    Users must not rely on f32-versus-f64 precision, exact midpoint agreement for `round`, NaN ordering,
    signed-zero operand identity, or non-finite switch-label behavior.

31. **Interpreter diagnostics differing from target invalid access.**
    Diagnostic sentinels and faults intentionally expose uninitialized optimizer reads. Optimizations need not
    preserve those interpreter-only invalid-access failures. The prior partial-array packing lead read an
    uninitialized element; no initialized valid-read counterexample was found.

32. **Deep recursive debug formatting and adversarial optimizer recursion.**
    Output at recursion-exhausting depth is not considered useful, and no practical compiler-generated subtree
    recursion failure was found.

33. **Empty closure cells referenced only by dead code.**
    Functions created before such a closure cell is initialized remain outside the intended compiled-code style.

34. **Dictionary hash contract and heterogeneous constant keys.**
    Compiled lookup does not execute user hashes, and equal keys must satisfy Python's equal-hash contract.
    Heterogeneous keys use linear lookup when the compiler cannot establish a shared ordering.

35. **`min` and `max` over arbitrary mutable-reference iterables.**
    The compiler cannot preserve arbitrary reference semantics; supported array-like and numeric iterator paths
    remain the contract. The published `default` restriction is intentionally not narrowed to `ArrayLike`:
    runtime iterators and runtime-length array-like values both require numeric elements when emptiness is known
    only at runtime.

36. **Compile-time-only archetypes returned by name lookup.**
    Exposing these bases during compilation is accepted even though they are not shipped and have no runtime ID.

37. **`Project.resources` and `Project.converters` documentation mentions only `dev`.**
    Programmatic builds also consume them, but correcting this published description was explicitly declined.

38. **Source recovery assumes UTF-8.**
    Non-UTF-8 engine source remains outside the project convention even when Python can execute it via PEP 263.

39. **Compilation installs a process-global exception hook.**
    Preserving or chaining an application's existing hook remains an unresolved integration choice.

40. **Python 3.14 semantics apply on every supported host.**
    The compiler follows Python 3.14 behavior rather than adding version-dependent semantics for older hosts.
    Python 3.12 and 3.13 eager-annotation differences are therefore not bugs.

41. **Optimizer toolchain helper overflow.**
    Private test helper `nogil_sum` can overflow a Windows C `long`; it is not production code.

42. **Export directory writers are additive.**
    Optional files are not removed when a later write omits them; these APIs do not synchronize directories.

43. **Missing goldens are created by the regression helper.**
    This is the documented delete-and-rerun regeneration workflow.

44. **Mutable cached-hash backend place objects.**
    Post-construction mutation can corrupt sets or dictionaries, but the compiler performs no such mutation.

45. **Traceback cause restoration after traceback printing fails.**
    Defensive recovery was declined once diagnostic output itself is unavailable.

46. **Optimizer profiling across concurrent programmatic builds.**
    Profiling is process-global and internal; concurrent callers must coordinate reset, snapshot, and toggling.

47. **Impure value identity through optimizer place fusion.**
    No reachable counterexample was found because separate evaluations receive distinct value IDs.

48. **Backend helper `run_fuse_rmw` with a side-effecting right operand.**
    The direct test helper can reorder a hand-built malformed expression, but production treeification
    materializes the operand and all normal optimization pipelines preserve behavior.

49. **Generic bounds and constraints are static metadata.**
    Python permits runtime parameterization outside a PEP 695 bound or constraint. Sonolus enforces only concrete
    storage and layout requirements, leaving ordinary bounds to static type checkers.

50. **Private `_remainder` constant folding at a zero divisor.**
    Its native constant evaluator can raise `ZeroDivisionError`, but the helper has no public registration or
    production caller. This dormant path does not justify changing native folding policy by itself.

51. **Fixed lead-time guidance for scheduled effects.**
    The current public contract recommends scheduling the three scheduled sound-effect operations at least 0.5
    seconds ahead when possible. The guidance is intentional and remains part of the public contract.

52. **Meta-function positional configuration.**
    Runtime `meta_fn(False)` remains invalid; the corrected overload exposes configuration only as a keyword and
    supports both direct and decorator-factory forms.

53. **Abstract contracts without `ABC`.**
    `SonolusDescriptor` and `BackingValue` deliberately avoid `ABC` because of metaclass interactions. Their
    abstract methods document the contract and raise `NotImplementedError`, but instantiation is not blocked.

54. **Internal array-iterator generic annotations.**
    Private iterator type variables describe the backing container rather than its element in a few annotations.
    Runtime behavior is correct and these underscore types are unpublished, so this was not escalated.

55. **Unused random draws may be optimized away.**
    SCCP and dead-code elimination can remove a random operand whose result cannot affect the program. Existing
    optimizer tests and the runtime-cost policy explicitly permit deleting such unused draws. Runtime numeric
    faults are governed by the broader accepted optimization policy above.

56. **Normal build, verbose diagnostics, callback, export, and localization behavior.**
    Module detection, `BuildConfig.verbose`, port defaults, callback lists, export requirements, and localization
    values match their definitions and documented contracts.

57. **Compiled range, random, builtin, and container behavior.**
    Range arithmetic outside the fixed `index` shape, reversed-bound uniform, duplicate keys, generic cache
    isolation, and runtime `zip(strict=...)` match their supported contracts.

58. **Generated documentation and release integrity.**
    The remaining internal-type exposure is an accepted policy above. Entity-data terminology is not contradictory,
    and the intentionally forward `0.19.0` changelog heading is retained.

59. **Keyword-only defaults of `None` in nested functions.**
    Source `None` is wrapped as a compile-time constant and is not confused with a missing default.

60. **Flow-edge numeric annotation difference.**
    Annotating a condition as `float` still accepts `int` under the typing numeric tower; this is not an API bug.

61. **Invalid surplus arguments to `DebugPause`.**
    The operation is zero-argument in public and emitted code. Extra operands occur only in malformed internal IR.

62. **Regression prose and whitespace-only debt.**
    Non-semantic trailing spaces and already-covered mechanical prose are not separate correctness findings.

63. **Visitor evaluation order and ordinary argument binding.**
    Mixed starred arguments and keywords, defaults for every parameter kind, mutable defaults, argument snapshots,
    decorator evaluation, assignment sequencing, match flow, and ordinary expression termination matched Python
    or their documented subset contracts.

64. **Scope scanners and established nested-function cases.**
    Function lexical locals, dead-path `global` or `nonlocal` rejection, late closure lookup, keyword-only `None`
    defaults, eager outermost generator-expression iterables, preceding-target lookup, same-line lambda
    disambiguation, and definition-time write scanning behaved as intended. Conservatively tracking a bare
    annotation at a loop header caused only unnecessary bookkeeping, with no semantic divergence found.

65. **Keyword forms for positional-only Python builtins remain Sonolus-specific extensions.**
    Compiled wrappers and published stubs accept keyword forms for several builtins that CPython marks
    positional-only, including the source parameter of `dict`. In particular,
    `dict(mapping_or_iterable=(("value", 1),))` treats the argument as the source rather than as a data key. The
    published signature exposes that call shape. Exact CPython positional-only enforcement is not the
    compiled-subset contract, so these outcomes are not correctness bugs. Signatures may be narrowed later as an
    API decision.

66. **Non-finite numeric prose describes an unsupported reliance, not universal input rejection.**
    Tests and emission intentionally accept some non-finite constants, while the numeric guide says users must not
    rely on values outside the runtime's supported numeric boundary. Accepted construction of an infinity does not
    promise Python behavior for arithmetic, ordering, labels, or serialization involving it. Item 30 remains the
    operative policy.

67. **Dual `NotImplemented` equality does not fall back to traced object identity.**
    When both same-type `__eq__` or `__ne__` methods return `NotImplemented`, compilation reports an error instead
    of applying Python's identity fallback. Traced object identity is not considered reliable for this purpose,
    and focused tests intentionally use `run_compiled` to pin the accepted divergence.

68. **Record-valued chained comparisons remain constrained by branch merging.**
    Chained non-membership comparisons can reject record-valued intermediate or final rich-comparison results.
    Supporting the raw short-circuit and final results would still leave the common runtime paths unusable because
    distinct reference values cannot merge. This niche extension was declined.

69. **Mode archetype lists are validated at construction rather than after every mutation.**
    Mutating a mode's public `archetypes` list after construction can insert the same class twice and bypass the
    constructor's duplicate check. Revalidating every later schema and packaging boundary was declined.

70. **Iterator tests may pin behavior beyond the public single-use contract.**
    Focused tests assert exact reuse behavior for specific builtin iterator implementations. These tests do not
    broaden the public contract, which continues to treat iterators as single use.
