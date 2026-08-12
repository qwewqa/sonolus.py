# Remaining review issues

This manifest records every issue remaining after commit `e1c5855`, together with the accepted limitations and
refuted leads retained from the full review session. Issues fixed by that commit are omitted.

Confidence labels:

- **Validated**: independently reproduced after the audit lead was reported.
- **Reproduced**: reproduced during the audit but not independently challenged.

## Open compiler and runtime correctness issues

1. **High, validated: lambda assignment targets are not predeclared as lexical locals.**
   The visitor imports `declared_locals` for function definitions but not lambdas. A lambda such as
   `lambda: x + (x := 1)` therefore reads an outer `x` and returns a value, where Python raises
   `UnboundLocalError` because `x` is local throughout the lambda.

2. **High, validated: transitive generator closure captures evade stale-binding validation.**
   A generator records an enclosing binding only when its own visitor reads it. If a nested lambda reads `x`,
   changing `x` after the generator is created is not detected, and the generator can use the captured old value.
   This violates the documented current-value closure behavior and the generator single-live-definition rule.

3. **High, validated: generator expressions do not predeclare every comprehension target.**
   Only the outer target is treated as local throughout a generator expression. In
   `sum(y for x in Array(1) for y in Array(y, y + 1))`, the inner iterable incorrectly reads an outer `y`;
   Python raises `UnboundLocalError` because the later target makes `y` local to the generator expression.

4. **Medium, validated: statically nonempty custom iterators retain phantom exhaustion paths.**
   A `next()` method that always returns `Some` still gives `for` and `yield from` an impossible exhaustion
   continuation. The compiler then visits unreachable loop `else` code or later yields and can falsely reject
   conflicting returns or yielded values. This is not the documented restriction on reusing an iterator.

5. **Medium, validated: merge bookkeeping falsely counts an internal loop-binding read.**
   `Scope.apply_merge()` obtains its own copied `ValueBinding` through `get_value()`, incrementing `read_count`.
   A later loop merge can then reject the binding as though user code had read it. Use the already-known binding
   value without recording a source read; genuine source reads must continue to trigger the conflict.

6. **Medium, validated: membership does not truth-convert a custom `__contains__` result.**
   `in` and `not in` require the result to already be numeric instead of applying the documented general
   truth-testing protocol. A bare `Record` returned by `__contains__` is truthy in Python but fails compilation.
   Preserve the separate Python 3.14 policy for a result of `NotImplemented`.

7. **Medium, reproduced: compiled binary-operator negotiation is incomplete.**
   Mixed union operations are asymmetric: `Num | int` produces `NotImplemented`, while `int | Num` produces
   `Num`. The compile-time shortcut returns the left result without reflected fallback, and the builtin `int`
   wrapper lacks the corresponding reverse union. Custom compile-time types also skip strict-subclass priority
   and `NotImplemented` fallback. Implement the full compile-time protocol and symmetric builtin-alias shims.

8. **Medium, reproduced: `getattr(obj, name, default)` ignores getter-raised `AttributeError`.**
   The compiled builtin distinguishes a missing descriptor from one whose getter raises `AttributeError`, then
   propagates the latter. Python first gives `__getattr__` an opportunity, then returns the supplied default if
   the complete lookup still raises `AttributeError`, as the published `getattr` contract implies. Direct access,
   `hasattr`, and class patterns share adjacent parts of this lookup path and need consistent treatment.

9. **Medium, reproduced: attribute-error detection can swallow an unrelated outer exception.**
   `caused_by_attribute_error()` follows an exception's explicit cause without checking the outer exception type.
   `hasattr` and class patterns can therefore treat an unrelated natural exception raised from `AttributeError`
   as a missing attribute, instead of propagating the outer exception as Python does.

10. **Medium, validated: optimizer sinking and DCE do not preserve eager numeric faults.**
    A pure scalar expression such as division by an initialized runtime zero can be deleted when unused, sunk
    past `DebugLog`, or moved into only the branch that consumes it. Optimization levels then differ in whether
    and when the reference interpreter raises. This is ordinary eager evaluation, not item 35's policy for
    invalid or uninitialized reads; LICM already treats oracle domain faults as ordering constraints.

11. **Low, validated: source recovery does not index lambdas inside annotations.**
    `FindFunction.visit_AnnAssign` skips the annotation expression. On Python 3.14, a lambda retrieved from a lazy
    annotation is a real callable, but compiling a call to it fails because `get_function` cannot find its AST.
    Visiting the annotation is consistent with the project's Python 3.14 semantic baseline.

12. **Low, reproduced: nested generic functions do not bind their type parameters.**
    A nested PEP 695 definition such as `def inner[T](): return T` works in Python but compilation reports that
    `T` is undefined. Generic bounds remain static metadata under item 53; this issue is the missing runtime
    binding of the type parameter itself.

13. **Low, reproduced: matrix multiplication can leak an internal `KeyError`.**
    `@` and its reflected form are present in the operator tables but absent from a compile-time dispatch path.
    An otherwise unsupported matrix multiplication therefore indexes a missing entry instead of reporting the
    natural public-facing operator error.

14. **Low, reproduced: a property with no getter reports the wrong failure.**
    Compiled access to a `property` whose `fget` is `None` attempts to compile or call `None`, producing an
    incidental callable error instead of Python's unreadable-attribute `AttributeError`.

15. **Low, reproduced: expanded call-keyword failures use the wrong diagnostics.**
    A non-mapping `**value` or a non-string expanded key raises `ValueError` rather than Python's `TypeError`.
    Duplicate expanded keywords also omit the callee name that other argument-binding diagnostics include.

16. **Low, reproduced: class patterns reject excess positional subpatterns with the wrong error.**
    When a pattern supplies more positional subpatterns than `__match_args__` allows, compilation raises a
    `ValueError` with non-Python wording rather than Python's `TypeError` naming the matched class and limit.

## Open documentation and prose issues

17. **Low, validated: published signatures expose internal value and runtime-check types.**
    Public inheritance and annotations render `Value`, `GenericValue`, `TransientValue`, and `RuntimeChecks`,
    although those names live under `sonolus.script.internal` and have no public reference. Replace or hide the
    leaks without changing the intentionally deferred `OptimizationLevel` decision in item 29.

18. **Low, validated: public schema result shapes are not fully documented.**
    `Project.schema()` publishes `ProjectSchema`, but its `archetypes` member has no attribute documentation.
    `Archetype.schema()` returns the public `ArchetypeSchema`, whose `name`, `fields`, and `exports` members have
    no published type documentation at all.

19. **Low, validated: entity-info result records are undocumented.**
    `entity_info_at()` publicly returns `PlayEntityInfo`, `WatchEntityInfo`, or `PreviewEntityInfo`, but those
    public record classes and their fields have no published descriptions. Document the returned shapes without
    adding constructor `Usage:` blocks because these records are engine-owned results, not user constructions.

20. **Low, reproduced: the current changelog contains implementation and diagnostic transcripts.**
    Many 0.18.2 bullets reproduce complete error text, Python-identical mechanics, or implementation alternatives.
    Condense them to the user-observable change, while retaining information needed to distinguish separate fixes.

## Decided not to fix, accepted policy, and refuted leads

21. **Constructor `Usage:` blocks on public constructible records are intentional.**
    Generated constructors do not render in the reference. Keep pseudo-signature `Usage:` blocks consistently on
    public records users construct, including `LifeInfo` and `Particle`; omit them from returned or engine-owned
    records. These blocks are not redundant with the visible class signature.

22. **Public debugging helpers remain undocumented and semi-public.**
    `visualize_cfg` and `simulation_context` intentionally have no public docstrings and do not render on the
    debug reference page. Their internal types and options are not a documentation gap by themselves.

23. **`SimulationContext` setup is not transactional.**
    Entry or import-hook substitution failures can leave partial testing state installed. Simulation context is a
    testing aid, and transactional rollback was explicitly judged not worth the complexity.

24. **Dictionary ordered lookup assumes consistent comparisons.**
    Fast lookup relies on keys and probes providing mutually consistent equality and total ordering. Partial or
    inconsistent user comparisons can make lookup size-dependent; this is a documented caller requirement.

25. **Extreme finite `round(..., ndigits)` values.**
    Very large positive or negative digit counts can overflow or underflow the scaling implementation instead of
    matching Python's limiting result. This uncommon range remains unsupported and is documented.

26. **`make_comparable_float` input-domain validation.**
    Its documented integer and range restrictions remain caller obligations. Additional guards were declined
    because the function is niche.

27. **Optimizer marshal validation for malformed numeric IR metadata.**
    Fractional block IDs, offsets, and temporary sizes can truncate, but normal frontend output does not produce
    them. Additional boundary checks were declined for performance reasons.

28. **Duplicate CFG labels in manually authored backend graphs.**
    Normal compilation stores successors in a keyed mapping and cannot create equal-label duplicates. Aggressive
    marshal validation for externally mutated internal graphs was declined.

29. **Public `BuildConfig` exposing the backend optimization type.**
    Generated signatures still show `optimize.OptimizationLevel`. Publishing or hiding the type was deferred.

30. **Useful standard-Python behavior remains in stub prose.**
    Behavioral context may remain when needed for a standalone reference. This does not justify repeating a
    signature or duplicating the same fact in a summary and `Returns:` section.

31. **Case-distinct collection names and Windows component limits.**
    Cross-platform casefold uniqueness and pre-validating the Windows 255-unit component limit were explicitly
    declined portability constraints.

32. **`sonolus-py check` remains a lightweight frontend check.**
    It does not guarantee that names, level data, configuration JSON, optimization, or packaging will succeed.

33. **Development-server reload, publication, and network limitations.**
    Stale bytecode, disabled address reuse, IPv4-only operation, in-place partial publication, retained endpoints,
    timestamp races, repository trust, and production hardening remain accepted for the manual dev server.

34. **Floating-point format, midpoint, and edge-value differences.**
    Users must not rely on f32-versus-f64 precision, exact midpoint agreement for `round`, NaN ordering,
    signed-zero operand identity, or non-finite switch-label behavior.

35. **Interpreter diagnostics differing from target invalid access.**
    Diagnostic sentinels and faults intentionally expose uninitialized optimizer reads. Optimizations need not
    preserve those interpreter-only invalid-access failures. The fresh partial-array packing lead read an
    uninitialized element; no initialized-read counterexample was found, so it remains covered here.

36. **Deep recursive debug formatting and adversarial optimizer recursion.**
    Output at recursion-exhausting depth is not considered useful, and no practical compiler-generated subtree
    recursion failure was found.

37. **Empty closure cells referenced only by dead code.**
    Functions created before such a closure cell is initialized remain outside the intended compiled-code style.

38. **Dictionary hashes and heterogeneous unsortable constant keys.**
    Compiled lookup does not execute user hashes, and equal keys must satisfy Python's equal-hash contract.
    Heterogeneous unsortable constant keys remain unsupported.

39. **`min` and `max` over arbitrary mutable-reference iterables.**
    The compiler cannot preserve arbitrary reference semantics; supported array-like and numeric iterator paths
    remain the contract. The published `default` restriction is intentionally not narrowed to `ArrayLike`:
    runtime iterators and runtime-length array-like values both require numeric elements when emptiness is known
    only at runtime.

40. **Compile-time-only archetypes returned by name lookup.**
    Exposing these bases during compilation is accepted even though they are not shipped and have no runtime ID.

41. **`Project.resources` and `Project.converters` documentation mentions only `dev`.**
    Programmatic builds also consume them, but correcting this published description was explicitly declined.

42. **Source recovery assumes UTF-8.**
    Non-UTF-8 engine source remains outside the project convention even when Python can execute it via PEP 263.

43. **Compilation installs a process-global exception hook.**
    Preserving or chaining an application's existing hook remains an unresolved integration choice.

44. **Python 3.14 semantics apply on every supported host.**
    The compiler follows Python 3.14 behavior rather than adding version-dependent semantics for older hosts.
    Fresh Python 3.12 and 3.13 eager-annotation differences are therefore not bugs.

45. **Optimizer toolchain helper overflow.**
    Private test helper `nogil_sum` can overflow a Windows C `long`; it is not production code.

46. **Export directory writers are additive.**
    Optional files are not removed when a later write omits them; these APIs do not synchronize directories.

47. **Missing goldens are created by the regression helper.**
    This is the documented delete-and-rerun regeneration workflow.

48. **Mutable cached-hash backend place objects.**
    Post-construction mutation can corrupt sets or dictionaries, but the compiler performs no such mutation.

49. **Traceback cause restoration after traceback printing fails.**
    Defensive recovery was declined once diagnostic output itself is unavailable.

50. **Optimizer profiling across concurrent programmatic builds.**
    Profiling is process-global and internal; concurrent callers must coordinate reset, snapshot, and toggling.

51. **Impure value identity through optimizer place fusion.**
    No reachable counterexample was found because separate evaluations receive distinct value IDs.

52. **Backend helper `run_fuse_rmw` with a side-effecting right operand.**
    The direct test helper can reorder a hand-built malformed expression, but production treeification
    materializes the operand and all normal optimization pipelines preserve behavior.

53. **Generic bounds and constraints are static metadata.**
    Python permits runtime parameterization outside a PEP 695 bound or constraint. Sonolus enforces only concrete
    storage and layout requirements, leaving ordinary bounds to static type checkers.

54. **Private `_remainder` constant folding at a zero divisor.**
    Its native constant evaluator can raise `ZeroDivisionError`, but the helper has no public registration or
    production caller. This dormant path does not justify changing native folding policy by itself.

55. **Fixed lead-time guidance for scheduled effects.**
    The current public contract recommends scheduling the three scheduled sound-effect operations at least 0.5
    seconds ahead when possible. Removing that wording was rejected because the latest user decision explicitly
    restored it.

56. **Meta-function positional configuration.**
    Runtime `meta_fn(False)` remains invalid; the corrected overload exposes configuration only as a keyword and
    supports both direct and decorator-factory forms.

57. **Abstract contracts without `ABC`.**
    `SonolusDescriptor` and `BackingValue` deliberately avoid `ABC` because of metaclass interactions. Their
    abstract methods document the contract and raise `NotImplementedError`, but instantiation is not blocked.

58. **Internal array-iterator generic annotations.**
    Private iterator type variables describe the backing container rather than its element in a few annotations.
    Runtime behavior is correct and these underscore types are unpublished, so this was not escalated.

59. **Unused random draws may be optimized away.**
    SCCP and dead-code elimination can remove a random operand whose result cannot affect the program. Existing
    optimizer tests and the runtime-cost policy explicitly permit deleting such unused draws. This does not cover
    the initialized numeric faults in item 10.

60. **Normal build, verbose diagnostics, callback, export, and localization behavior was rechecked.**
    Module detection, `BuildConfig.verbose`, port defaults, callback lists, export requirements, and localization
    values match their definitions and documented contracts. The fresh build audit found no additional issue
    beyond leads independently recorded above.

61. **Compiled range, random, builtin, and container behavior was rechecked.**
    Range arithmetic outside the fixed `index` shape, reversed-bound uniform, duplicate keys, generic cache
    isolation, and runtime `zip(strict=...)` match their supported contracts. The fresh builtin and backend audits
    found no additional issue beyond leads independently recorded above.

62. **Generated documentation and release integrity checks.**
    Private-name leaks fixed in prior passes were not reintroduced; the new leaks are recorded in item 17.
    Entity-data terminology is not contradictory, and strict MkDocs found no broken links or anchors. The source
    version `0.18.2` versus the last `0.18.1` release is normal unreleased-version practice, not version skew.

63. **Keyword-only defaults of `None` in nested functions.**
    Source `None` is wrapped as a compile-time constant and is not confused with a missing default.

64. **Flow-edge numeric annotation difference.**
    Annotating a condition as `float` still accepts `int` under the typing numeric tower; this is not an API bug.

65. **Invalid surplus arguments to `DebugPause`.**
    The operation is zero-argument in public and emitted code. Extra operands occur only in malformed internal IR.

66. **Regression prose and whitespace-only debt.**
    Non-semantic trailing spaces and already-covered mechanical prose are not separate correctness findings.

67. **Repeated generator consumption after exhaustion is unsupported.**
    A fresh repro found that consuming a generator a second time can replay code after its final `yield`. Both the
    concepts guide and `SonolusIterator` contract already say that consuming an exhausted iterator again is not
    supported and may behave unexpectedly, so this is not an open correctness issue.

68. **Visitor evaluation order and ordinary argument binding were rechecked.**
    Mixed starred arguments and keywords, defaults for every parameter kind, mutable defaults, argument snapshots,
    decorator evaluation, assignment sequencing, match flow, and ordinary expression termination matched Python
    or their documented subset contracts. No additional issue was found outside item 4's iterator continuation.

69. **Scope scanners and established nested-function cases were rechecked.**
    Function lexical locals, dead-path `global` or `nonlocal` rejection, late closure lookup, keyword-only `None`
    defaults, eager outermost generator-expression iterables, preceding-target lookup, same-line lambda
    disambiguation, and definition-time write scanning behaved as intended. Conservatively tracking a bare
    annotation at a loop header caused only unnecessary bookkeeping, with no semantic divergence found.
