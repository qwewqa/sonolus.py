# Remaining review issues

This manifest records every issue remaining after commit `2fa4076`, together with the accepted limitations and
refuted leads retained from the full review session. Issues fixed by that commit are omitted.

Confidence labels:

- **Validated**: independently reproduced after the audit lead was reported.
- **Reproduced**: reproduced during the audit but not independently challenged.

## Open compiler and runtime correctness issues

1. **High, validated: closures yielded from a paused generator can use a stale enclosing context.**
   The generator visitor does not refresh its active context before an anonymous lambda resolves its captures.
   A lambda yielded directly, through `yield from`, or from a generator expression can therefore crash while
   resolving its parent scope or observe an old or future value instead of the current generator-local value.
   Naming the lambda can hide the bug by incidentally refreshing the context. Each repro advances a fresh iterator
   only once, so the documented restriction on reusing iterators does not apply.

2. **Medium, validated: a nonreturning generator expression retains a phantom completion path.**
   A source iterator whose `next()` always returns `Some`, combined with a compile-time-false filter, can neither
   yield nor finish. The generator machinery nevertheless manufactures a live no-yield merge and reports
   exhaustion, so `yield from` visits later unreachable code and can reject it. This also occurs on a fresh,
   single consumption and is distinct from unsupported post-exhaustion reuse.

3. **Medium, validated: ordinary missing-attribute lookup executes `__getattr__` on the host.**
   Direct access, `getattr`, `hasattr`, and class patterns group an absent ordinary descriptor with host-side
   lookup. A user-defined `__getattr__` that depends on a runtime `Num` is therefore executed as Python instead of
   being traced, producing a compilation failure or the wrong compile-time behavior. Property fallback already
   uses the traced call path, so the ordinary missing-attribute path should do the same.

4. **Medium, validated: conditional property fallback loses a live sibling CFG path.**
   If one runtime branch of a property lookup ends in `AttributeError`, the outer lookup catches it and invokes
   `__getattr__`, but the getter visitor's other live branch remains linked directly to exit without producing the
   property's value. This is reachable without unsupported `raise` syntax: an ordinary missing attribute read
   inside one property branch is enough. Direct access, `getattr`, `hasattr`, and class patterns share the problem.

5. **Medium, validated: inherited classmethod reflected operators lose strict-subclass priority.**
   Compile-time binary negotiation compares raw descriptor identity while binding classmethods. A real strict
   subclass inheriting a classmethod reflected operator can consequently run the left operand first, where Python
   grants the right operand reflected priority and returns its result.

6. **Low, validated: ABC virtual subclasses receive reflected-operator priority.**
   Compile-time negotiation uses `issubclass`, so registration with an ABC or a custom `__subclasscheck__` is
   treated as a strict subtype relationship. Python grants reflected priority only to a real subtype in the
   right operand's MRO, and can therefore choose the left result where compilation chooses the right result.

7. **Low, reproduced: augmented assignment omits compile-time operator negotiation.**
   Ordinary binary expressions unwrap supported compile-time values and run their Python operator protocol, but
   `visit_AugAssign` only probes the wrapper value. Valid operations such as a union between builtin type objects
   work with `|` and fail with `|=` instead of following the normal in-place, binary, and reflected fallback order.

8. **Medium, validated: call diagnostics eagerly inspect callable names.**
   Before visiting any arguments, `visit_Call` reads `__qualname__` and `__name__` from every compile-time callable
   solely to prepare possible keyword-error text. A valid callable can observe, reject, or add side effects to
   those irrelevant lookups, changing a call that needs no diagnostic.

9. **Low, reproduced: narrow compile-time membership results skip Python truth conversion.**
   The recent membership path correctly truth-tests `Record` results, but a compile-time `__contains__` returning
   another constant such as a string or `None` still fails conversion instead of using that object's Python truth
   value. This is an exotic compile-time protocol path, and the general supported constant-truthiness scope is not
   spelled out in the concepts guide, so it is kept low rather than treated as a broad membership regression.

10. **Low, reproduced: truth testing binds classmethod and staticmethod protocols incorrectly.**
    `convert_to_boolean_num` fetches `__bool__` and `__len__` from the value's type and always supplies the value
    as an explicit argument. That is correct for an ordinary method but not for a `classmethod` or `staticmethod`;
    boolean contexts, including membership-result conversion, can therefore reject a valid descriptor definition.

11. **Low, reproduced: lambda keyword diagnostics expose an internal qualified name.**
    A lambda's generated callable has its `__name__` corrected to `<lambda>` but retains the implementation
    function's `Visitor.visit_Lambda.<locals>.fn` qualified name. Duplicate expanded-keyword errors print that
    internal name instead of Python's `<lambda>` spelling.

12. **Low, validated: class patterns accept malformed `__match_args__`.**
    A class whose `__match_args__` is a list is accepted for positional patterns, while Python requires a tuple
    and raises `TypeError`. The visitor also needs to validate that selected entries are strings before treating
    them as attribute names.

13. **Low, reproduced: `VarArray.insert` checks bounds before clipping the index.**
    The implementation applies `clamp`, but first calls the bounds-checking form of `get_positive_index`, so an
    index beyond either end raises or terminates before it can be clipped. Python's list-like `insert` clips such
    indices, and the existing clamp shows that behavior was intended here.

## Open documentation, prose, and optimizer issues

14. **Low, validated: compiled `set()` documentation omits accepted `frozenset` inputs.**
    The stub and concepts prose present tuple, dict, enum class, and set as the exhaustive supported sources.
    `validate_value` deliberately represents a host `frozenset` as the same compiled set implementation, and
    `set(frozenset(...))` compiles successfully, so the published overload and restriction text are too narrow.

15. **Low, reproduced: published stubs duplicate return prose.**
    Many `math` and `random` functions, plus `reversed`, state the returned value in both the summary and a
    `Returns:` section. The duplicated sections add no contract information and conflict with the retained prose
    policy; parameter and restriction details can remain while each return fact is stated once.

16. **Low, validated: match documentation omits terminating-read restrictions.**
    The concepts guide says all patterns are supported except its listed syntax forms. The visitor deliberately
    rejects a property or `__len__` read that terminates every path inside a class or sequence subpattern because
    the partially opened pattern CFG cannot be continued. That source-visible restriction needs to join the list.

17. **Low, reproduced: the fold-kernel arity contract overstates a binary invariant.**
    The kernel header says arithmetic left-fold ops can only be binary before emission, but the backend interpreter
    accepts n-ary nodes and hand-authored internal CFGs can carry them into optimization. N-ary `Subtract`,
    `Divide`, and `Power` then miss constant folding. Normal frontend output does not produce this shape, so this
    is a dormant internal optimization gap rather than a production miscompile.

## Decided not to fix, accepted policy, and refuted leads

18. **Unused or reordered runtime numeric faults need not be preserved.**
    The optimizer may delete an unused scalar fault, sink it past another operation, or move it into only a branch
    that consumes it. The Sonolus runtime does not expose these reference-interpreter faults as supported behavior;
    they are considered undefined, like invalid accesses, so eager fault timing is not an optimization constraint.

19. **Source recovery does not index lambdas inside annotations.**
    Python 3.14 can expose a callable lambda through a lazy annotation, but compiling a call to it may fail source
    recovery. Supporting this niche annotation path was explicitly declined.

20. **Nested generic functions do not bind their runtime type parameters.**
    A nested PEP 695 definition that reads its own type parameter can report the name as undefined during
    compilation. This is distinct from static generic bounds, but the runtime use is niche and was declined.

21. **Published signatures may expose internal value and runtime-check types.**
    Public inheritance and annotations currently render names such as `Value`, `GenericValue`, `TransientValue`,
    and `RuntimeChecks` without public reference pages. Hiding or replacing these leaks was explicitly deferred.

22. **The current changelog section is intentionally headed 0.19.0.**
    The prior 0.18.2 working heading was changed as requested while its prose was condensed. This is the intended
    next release heading, not a source-version mismatch.

23. **Constructor `Usage:` blocks on public constructible records are intentional.**
    Generated constructors do not render in the reference. Keep pseudo-signature `Usage:` blocks consistently on
    public records users construct, including `LifeInfo` and `Particle`; omit them from returned or engine-owned
    records. These blocks are not redundant with the visible class signature.

24. **Public debugging helpers remain undocumented and semi-public.**
    `visualize_cfg` and `simulation_context` intentionally have no public docstrings and do not render on the
    debug reference page. Their internal types and options are not a documentation gap by themselves.

25. **`SimulationContext` setup is not transactional.**
    Entry or import-hook substitution failures can leave partial testing state installed. Simulation context is a
    testing aid, and transactional rollback was explicitly judged not worth the complexity.

26. **Dictionary ordered lookup assumes consistent comparisons.**
    Fast lookup relies on keys and probes providing mutually consistent equality and total ordering. Partial or
    inconsistent user comparisons can make lookup size-dependent; this is a documented caller requirement.

27. **Extreme finite `round(..., ndigits)` values.**
    Very large positive or negative digit counts can overflow or underflow the scaling implementation instead of
    matching Python's limiting result. This uncommon range remains unsupported and is documented.

28. **`make_comparable_float` input-domain validation.**
    Its documented integer and range restrictions remain caller obligations. Additional guards were declined
    because the function is niche.

29. **Optimizer marshal validation for malformed numeric IR metadata.**
    Fractional block IDs, offsets, and temporary sizes can truncate, but normal frontend output does not produce
    them. Additional boundary checks were declined for performance reasons.

30. **Duplicate CFG labels in manually authored backend graphs.**
    Normal compilation stores successors in a keyed mapping and cannot create equal-label duplicates. Aggressive
    marshal validation for externally mutated internal graphs was declined.

31. **Public `BuildConfig` exposing the backend optimization type.**
    Generated signatures still show `optimize.OptimizationLevel`. Publishing or hiding the type was deferred.

32. **Useful standard-Python behavior remains in stub prose.**
    Behavioral context may remain when needed for a standalone reference. This does not justify repeating a
    signature or duplicating the same fact in a summary and `Returns:` section.

33. **Case-distinct collection names and Windows component limits.**
    Cross-platform casefold uniqueness and pre-validating the Windows 255-unit component limit were explicitly
    declined portability constraints.

34. **`sonolus-py check` remains a lightweight frontend check.**
    It does not guarantee that names, level data, configuration JSON, optimization, or packaging will succeed.

35. **Development-server reload, publication, and network limitations.**
    Stale bytecode, disabled address reuse, IPv4-only operation, in-place partial publication, retained endpoints,
    timestamp races, repository trust, and production hardening remain accepted for the manual dev server.

36. **Floating-point format, midpoint, and edge-value differences.**
    Users must not rely on f32-versus-f64 precision, exact midpoint agreement for `round`, NaN ordering,
    signed-zero operand identity, or non-finite switch-label behavior.

37. **Interpreter diagnostics differing from target invalid access.**
    Diagnostic sentinels and faults intentionally expose uninitialized optimizer reads. Optimizations need not
    preserve those interpreter-only invalid-access failures. The prior partial-array packing lead read an
    uninitialized element; no initialized valid-read counterexample was found.

38. **Deep recursive debug formatting and adversarial optimizer recursion.**
    Output at recursion-exhausting depth is not considered useful, and no practical compiler-generated subtree
    recursion failure was found.

39. **Empty closure cells referenced only by dead code.**
    Functions created before such a closure cell is initialized remain outside the intended compiled-code style.

40. **Dictionary hashes and heterogeneous unsortable constant keys.**
    Compiled lookup does not execute user hashes, and equal keys must satisfy Python's equal-hash contract.
    Heterogeneous unsortable constant keys remain unsupported.

41. **`min` and `max` over arbitrary mutable-reference iterables.**
    The compiler cannot preserve arbitrary reference semantics; supported array-like and numeric iterator paths
    remain the contract. The published `default` restriction is intentionally not narrowed to `ArrayLike`:
    runtime iterators and runtime-length array-like values both require numeric elements when emptiness is known
    only at runtime.

42. **Compile-time-only archetypes returned by name lookup.**
    Exposing these bases during compilation is accepted even though they are not shipped and have no runtime ID.

43. **`Project.resources` and `Project.converters` documentation mentions only `dev`.**
    Programmatic builds also consume them, but correcting this published description was explicitly declined.

44. **Source recovery assumes UTF-8.**
    Non-UTF-8 engine source remains outside the project convention even when Python can execute it via PEP 263.

45. **Compilation installs a process-global exception hook.**
    Preserving or chaining an application's existing hook remains an unresolved integration choice.

46. **Python 3.14 semantics apply on every supported host.**
    The compiler follows Python 3.14 behavior rather than adding version-dependent semantics for older hosts.
    Python 3.12 and 3.13 eager-annotation differences are therefore not bugs.

47. **Optimizer toolchain helper overflow.**
    Private test helper `nogil_sum` can overflow a Windows C `long`; it is not production code.

48. **Export directory writers are additive.**
    Optional files are not removed when a later write omits them; these APIs do not synchronize directories.

49. **Missing goldens are created by the regression helper.**
    This is the documented delete-and-rerun regeneration workflow.

50. **Mutable cached-hash backend place objects.**
    Post-construction mutation can corrupt sets or dictionaries, but the compiler performs no such mutation.

51. **Traceback cause restoration after traceback printing fails.**
    Defensive recovery was declined once diagnostic output itself is unavailable.

52. **Optimizer profiling across concurrent programmatic builds.**
    Profiling is process-global and internal; concurrent callers must coordinate reset, snapshot, and toggling.

53. **Impure value identity through optimizer place fusion.**
    No reachable counterexample was found because separate evaluations receive distinct value IDs.

54. **Backend helper `run_fuse_rmw` with a side-effecting right operand.**
    The direct test helper can reorder a hand-built malformed expression, but production treeification
    materializes the operand and all normal optimization pipelines preserve behavior.

55. **Generic bounds and constraints are static metadata.**
    Python permits runtime parameterization outside a PEP 695 bound or constraint. Sonolus enforces only concrete
    storage and layout requirements, leaving ordinary bounds to static type checkers.

56. **Private `_remainder` constant folding at a zero divisor.**
    Its native constant evaluator can raise `ZeroDivisionError`, but the helper has no public registration or
    production caller. This dormant path does not justify changing native folding policy by itself.

57. **Fixed lead-time guidance for scheduled effects.**
    The current public contract recommends scheduling the three scheduled sound-effect operations at least 0.5
    seconds ahead when possible. Removing that wording was rejected because the latest user decision restored it.

58. **Meta-function positional configuration.**
    Runtime `meta_fn(False)` remains invalid; the corrected overload exposes configuration only as a keyword and
    supports both direct and decorator-factory forms.

59. **Abstract contracts without `ABC`.**
    `SonolusDescriptor` and `BackingValue` deliberately avoid `ABC` because of metaclass interactions. Their
    abstract methods document the contract and raise `NotImplementedError`, but instantiation is not blocked.

60. **Internal array-iterator generic annotations.**
    Private iterator type variables describe the backing container rather than its element in a few annotations.
    Runtime behavior is correct and these underscore types are unpublished, so this was not escalated.

61. **Unused random draws may be optimized away.**
    SCCP and dead-code elimination can remove a random operand whose result cannot affect the program. Existing
    optimizer tests and the runtime-cost policy explicitly permit deleting such unused draws. Runtime numeric
    faults are governed by the broader accepted optimization policy in item 18.

62. **Normal build, verbose diagnostics, callback, export, and localization behavior was rechecked.**
    Module detection, `BuildConfig.verbose`, port defaults, callback lists, export requirements, and localization
    values match their definitions and documented contracts. The build audit found no additional issue beyond
    leads independently recorded above.

63. **Compiled range, random, builtin, and container behavior was rechecked.**
    Range arithmetic outside the fixed `index` shape, reversed-bound uniform, duplicate keys, generic cache
    isolation, and runtime `zip(strict=...)` match their supported contracts. The builtin and backend audits found
    no additional issue beyond leads independently recorded above.

64. **Generated documentation and release integrity checks.**
    Private-name leaks fixed in prior passes were not reintroduced; the remaining internal-type exposure is the
    accepted policy in item 21. Entity-data terminology is not contradictory, and strict MkDocs found no broken
    links or anchors. The intentionally forward `0.19.0` changelog heading is covered by item 22.

65. **Keyword-only defaults of `None` in nested functions.**
    Source `None` is wrapped as a compile-time constant and is not confused with a missing default.

66. **Flow-edge numeric annotation difference.**
    Annotating a condition as `float` still accepts `int` under the typing numeric tower; this is not an API bug.

67. **Invalid surplus arguments to `DebugPause`.**
    The operation is zero-argument in public and emitted code. Extra operands occur only in malformed internal IR.

68. **Regression prose and whitespace-only debt.**
    Non-semantic trailing spaces and already-covered mechanical prose are not separate correctness findings.

69. **Repeated generator consumption after exhaustion is unsupported.**
    Consuming a generator a second time can replay code after its final `yield`. Both the concepts guide and
    `SonolusIterator` contract already say that consuming an exhausted iterator again is unsupported and may
    behave unexpectedly, so this is not an open correctness issue.

70. **Visitor evaluation order and ordinary argument binding were rechecked.**
    Mixed starred arguments and keywords, defaults for every parameter kind, mutable defaults, argument snapshots,
    decorator evaluation, assignment sequencing, match flow, and ordinary expression termination matched Python
    or their documented subset contracts. Fresh exceptions are recorded explicitly above.

71. **Scope scanners and established nested-function cases were rechecked.**
    Function lexical locals, dead-path `global` or `nonlocal` rejection, late closure lookup, keyword-only `None`
    defaults, eager outermost generator-expression iterables, preceding-target lookup, same-line lambda
    disambiguation, and definition-time write scanning behaved as intended. Conservatively tracking a bare
    annotation at a loop header caused only unnecessary bookkeeping, with no semantic divergence found.
