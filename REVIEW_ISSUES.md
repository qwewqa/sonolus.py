# Remaining review issues

This manifest records every issue remaining after commit `5a5ea61`, together with the accepted limitations and
refuted leads retained from the full review session. Issues fixed by that commit are omitted.

Confidence labels:

- **Validated**: independently reproduced after the audit lead was reported.
- **Reproduced**: reproduced during the audit but not independently challenged.
- **Lead**: supported by source analysis and still needs focused validation.

## Open correctness and API issues

1. **Low, validated: `Project.schema()` loses exports for duplicate same-mode archetype names.**
   Engine compilation permits distinct archetype classes with the same runtime name after warning, but schema
   construction stores exports in a name-keyed mapping and keeps only the final class. Aggregate exports for the
   shared schema row so watch-field filtering and packaged data remain consistent.

2. **Low, validated: class-shaped global declarations treat `ClassVar` as stored data.**
   `level_memory`, `level_data`, and the shared internal global decorators reject or allocate `ClassVar` members,
   unlike records, archetypes, and normal dataclass-transform behavior. Exclude `ClassVar` annotations while
   preserving their class values and contiguous offsets for real fields.

3. **Low, validated: several path annotations exclude supported strings.**
   `Project` resource/build paths and exported engine or level output paths are annotated only as `PathLike`,
   although each accepts an ordinary `str` through `Path(...)`. Publish `str | PathLike[str]` consistently.

## Open documentation and prose issues

4. **Low, validated: `dict()` and `set()` documentation overstates iterable support.**
   `dict()` accepts another dict or a tuple of key-value pairs, while `set()` accepts a tuple, dict keys, an enum
   class, or another set. Published stubs and concepts pages still imply that arbitrary iterables are accepted.

5. **Low, validated: public class `Usage:` blocks repeat generated constructor signatures.**
   Sixteen public classes, including `Vec2`, `Interval`, `Quad`, `Effect`, `Instruction`, and several containers,
   contain fenced pseudo-signatures that add no information beyond the generated signature. Remove these blocks
   while retaining genuine usage examples and behavioral restrictions.

6. **Low, validated: public debugging helpers are absent from generated documentation.**
   `visualize_cfg` and `simulation_context` have no docstrings and therefore do not render on the debug reference
   page. Document their purpose, non-obvious options, and scope without exposing compiler internals unnecessarily.

7. **Low, validated: optimizer prose incorrectly calls left-fold operations associative.**
   Comments and tests describe Add, Multiply, Mod, and Rem trees as associative, although floating-point Add and
   Multiply cannot be reassociated and Mod or Rem are not associative at all. Use durable left-fold or left-spine
   terminology while preserving the ordering invariant.

8. **Low, validated: internal comments still contain stale or mechanical explanations.**
   A context comment still describes the removed `x or default` behavior, several visitor/context/generic comments
   narrate visible control flow, and dict/builtin comments call mechanisms hacks without stating an invariant.
   Rewrite only comments with a concrete consequence; remove the rest.

9. **Low, validated: existing published prose still exceeds the 120-column standard.**
   Long lines remain in the CLI option table and an older changelog entry. This is prose-quality debt rather
   than a rendering failure; strict MkDocs currently succeeds.

## Decided not to fix or accepted policy

10. **`SimulationContext` setup is not transactional.**
    Entry or import-hook substitution failures can leave partial testing state installed. Simulation context is a
    testing aid, and transactional rollback was explicitly judged not worth the complexity.

11. **Dictionary ordered lookup assumes consistent comparisons.**
    Fast lookup relies on keys and probes providing mutually consistent equality and total ordering. Partial or
    inconsistent user comparisons can make lookup size-dependent; this is now a documented caller requirement.

12. **Extreme finite `round(..., ndigits)` values.**
    Very large positive or negative digit counts can overflow or underflow the scaling implementation instead of
    matching Python's limiting result. This uncommon range remains unsupported and is documented.

13. **`make_comparable_float` input-domain validation.**
    Its documented integer and range restrictions remain caller obligations. Additional guards were declined
    because the function is niche.

14. **Optimizer marshal validation for malformed numeric IR metadata.**
    Fractional block IDs, offsets, and temporary sizes can truncate, but normal frontend output does not produce
    them. Additional boundary checks were declined for performance reasons.

15. **Duplicate CFG labels in manually authored backend graphs.**
    Normal compilation stores successors in a keyed mapping and cannot create equal-label duplicates. Aggressive
    marshal validation for externally mutated internal graphs was declined.

16. **Public `BuildConfig` exposing the backend optimization type.**
    Generated signatures still show `optimize.OptimizationLevel`. Publishing or hiding the type was deferred.

17. **Useful standard-Python behavior remains in stub prose.**
    Behavioral context may remain when needed for a standalone reference. This does not justify repeating a
    signature or duplicating the same fact in a summary and `Returns:` section.

18. **Case-distinct collection names and Windows component limits.**
    Cross-platform casefold uniqueness and pre-validating the Windows 255-unit component limit were explicitly
    declined portability constraints.

19. **`sonolus-py check` remains a lightweight frontend check.**
    It does not guarantee that names, level data, configuration JSON, optimization, or packaging will succeed.

20. **Development-server reload, publication, and network limitations.**
    Stale bytecode, disabled address reuse, IPv4-only operation, in-place partial publication, retained endpoints,
    timestamp races, repository trust, and production hardening remain accepted for the manual dev server.

21. **Floating-point format, midpoint, and edge-value differences.**
    Users must not rely on f32-versus-f64 precision, exact midpoint agreement for `round`, NaN ordering,
    signed-zero operand identity, or non-finite switch-label behavior.

22. **Interpreter diagnostics differing from target invalid access.**
    Diagnostic sentinels and faults intentionally expose uninitialized optimizer reads. Optimizations need not
    preserve those interpreter-only invalid-access failures.

23. **Deep recursive debug formatting and adversarial optimizer recursion.**
    Output at recursion-exhausting depth is not considered useful, and no practical compiler-generated subtree
    recursion failure was found.

24. **Empty closure cells referenced only by dead code.**
    Functions created before such a closure cell is initialized remain outside the intended compiled-code style.

25. **Dictionary hashes and heterogeneous unsortable constant keys.**
    Compiled lookup does not execute user hashes, and equal keys must satisfy Python's equal-hash contract.
    Heterogeneous unsortable constant keys remain unsupported.

26. **`min` and `max` over arbitrary mutable-reference iterables.**
    The compiler cannot preserve arbitrary reference semantics; supported array-like and numeric iterator paths
    remain the contract. The published `default` restriction is intentionally not narrowed to `ArrayLike`:
    runtime iterators and runtime-length array-like values both require numeric elements when emptiness is known
    only at runtime.

27. **Compile-time-only archetypes returned by name lookup.**
    Exposing these bases during compilation is accepted even though they are not shipped and have no runtime ID.

28. **`Project.resources` and `Project.converters` documentation mentions only `dev`.**
    Programmatic builds also consume them, but correcting this published description was explicitly declined.

29. **Source recovery assumes UTF-8.**
    Non-UTF-8 engine source remains outside the project convention even when Python can execute it via PEP 263.

30. **Compilation installs a process-global exception hook.**
    Preserving or chaining an application's existing hook remains an unresolved integration choice.

31. **Python 3.14 semantics apply on every supported host.**
    The compiler follows Python 3.14 behavior rather than adding version-dependent semantics for older hosts.

32. **Optimizer toolchain helper overflow.**
    Private test helper `nogil_sum` can overflow a Windows C `long`; it is not production code.

33. **Export directory writers are additive.**
    Optional files are not removed when a later write omits them; these APIs do not synchronize directories.

34. **Missing goldens are created by the regression helper.**
    This is the documented delete-and-rerun regeneration workflow.

35. **Mutable cached-hash backend place objects.**
    Post-construction mutation can corrupt sets or dictionaries, but the compiler performs no such mutation.

36. **Traceback cause restoration after traceback printing fails.**
    Defensive recovery was declined once diagnostic output itself is unavailable.

37. **Optimizer profiling across concurrent programmatic builds.**
    Profiling is process-global and internal; concurrent callers must coordinate reset, snapshot, and toggling.

38. **Impure value identity through optimizer place fusion.**
    No reachable counterexample was found because separate evaluations receive distinct value IDs.

39. **Backend helper `run_fuse_rmw` with a side-effecting right operand.**
    The direct test helper can reorder a hand-built malformed expression, but production treeification
    materializes the operand and all normal optimization pipelines preserve behavior.

40. **Generic bounds and constraints are static metadata.**
    Python permits runtime parameterization outside a PEP 695 bound or constraint. Sonolus enforces only concrete
    storage/layout requirements, leaving ordinary bounds to static type checkers.

41. **Private `_remainder` constant folding at a zero divisor.**
    Its native constant evaluator can raise `ZeroDivisionError`, but the helper has no public registration or
    production caller. This dormant path does not justify changing native folding policy by itself.

42. **Fixed lead-time guidance for scheduled effects.**
    The current public contract recommends scheduling the three scheduled sound-effect operations at least 0.5
    seconds ahead when possible. A fresh lead to remove that wording was rejected because the latest user decision
    explicitly restored it.

43. **Meta-function positional configuration.**
    Runtime `meta_fn(False)` remains invalid; the corrected overload exposes configuration only as a keyword and
    supports both direct and decorator-factory forms.

44. **Abstract contracts without `ABC`.**
    `SonolusDescriptor` and `BackingValue` deliberately avoid `ABC` because of metaclass interactions. Their
    abstract methods now document the contract and raise `NotImplementedError`, but instantiation is not blocked.

45. **Internal array-iterator generic annotations.**
    Private iterator type variables describe the backing container rather than its element in a few annotations.
    Runtime behavior is correct and these underscore types are unpublished, so this was not escalated.

46. **Unused random draws may be optimized away.**
    SCCP and dead-code elimination can remove a random operand whose result cannot affect the program. Existing
    optimizer tests and the runtime-cost policy explicitly permit deleting such unused draws.

47. **Normal build, verbose diagnostics, callback, export, and localization behavior checked in prior passes.**
    Module detection, `BuildConfig.verbose`, port defaults, callback lists, export requirements, and localization
    values match their definitions and documented contracts; suspected inconsistencies were refuted.

48. **Compiled range, random, and container behavior checked in prior passes.**
    Range arithmetic outside the fixed `index` shape, reversed-bound uniform, union ordering, duplicate keys,
    generic cache isolation, and runtime `zip(strict=...)` match their supported contracts.

49. **Generated documentation and release integrity checks.**
    The site does not leak the previously identified private names; entity-data terminology is not contradictory;
    unreleased version skew is normal; strict MkDocs found no broken links or anchors.

50. **Keyword-only defaults of `None` in nested functions.**
    Source `None` is wrapped as a compile-time constant and is not confused with a missing default.

51. **Flow-edge numeric annotation difference.**
    Annotating a condition as `float` still accepts `int` under the typing numeric tower; this is not an API bug.

52. **Invalid surplus arguments to `DebugPause`.**
    The operation is zero-argument in public and emitted code. Extra operands occur only in malformed internal IR.

53. **Regression prose and whitespace-only debt.**
    Non-semantic trailing spaces and already-covered mechanical prose are not separate correctness findings.
