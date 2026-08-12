# Remaining review issues

This manifest records issues remaining after commit `c28af93`. Fixed items from the previous manifest are omitted.
Items under "Decided not to fix" are retained so future reviews do not repeatedly rediscover settled policy.

Confidence labels:

- **Validated**: independently reproduced after the audit lead was reported.
- **Reproduced**: reproduced during the audit but not independently challenged.
- **Lead**: supported by source analysis and still needs focused validation.

## Open correctness issues

1. **High, validated: case-distinct item names collide in Windows collection output.**
   A category can contain both `pixel` and `PIXEL`, but Windows writes them to one file. The category list
   advertises two items while only the last payload remains. Reject Windows-casefold collisions within each
   category, including items loaded from SCP archives.

2. **Medium, validated: cross-mode block enums can cause optimizer wrong-code in hand-built IR.**
   [`sonolus/backend/_opt/ir.pyx`](sonolus/backend/_opt/ir.pyx) trusts permission metadata on a supplied
   `BlockData` enum even when `OptimizerConfig.mode` names another mode. Numeric block IDs overlap, so a foreign
   enum can mark a writable block readonly and let LICM or GVN move reads across writes. Normal script compilation
   canonicalizes blocks correctly; the failure requires direct backend IR construction.

3. **Medium, validated: `check` skips deterministic project-data validation.**
   `sonolus-py check` validates callback tracing but never loads levels, validates output names, serializes level
   data, or validates and serializes engine configuration. It can report success for projects that deterministically
   fail to build because of reserved names, NaN data, or invalid option configuration. Optimization may remain
   intentionally excluded, but cheap project-data validation should match the documented project-validation scope.

4. **Medium, validated: `select_option` accepts invalid default indices.**
   [`sonolus/script/options.py`](sonolus/script/options.py) accepts negative, out-of-range, boolean, and empty-list
   integer defaults and emits them into engine configuration. Require a non-boolean integer satisfying
   `0 <= default < len(values)`.

5. **Medium, validated: integer-domain guards are missing from several builtins.**
   Compiled `range`, `enumerate(start=...)`, and `round(ndigits=...)` accept fractional values despite their
   published integer contracts. Use runtime assertions so invalid calls in dead runtime branches remain compilable.

6. **Medium, validated: `__len__` results are not validated consistently.**
   Direct `len`, truth conversion, and array-like extrema accept fractional or negative numeric results from a
   custom `__len__`. Validate a non-negative integer-valued number at their shared protocol boundary.

7. **Medium, validated: dev-server reload can reuse stale timestamp bytecode.**
   Purging `sys.modules` does not invalidate a `.pyc` when an edit preserves source size and falls in the same
   timestamp second. A rebuild can therefore import the previous project or dependency source. Invalidating import
   finder caches is insufficient; the relevant bytecode cache must be removed or bypassed.

8. **Medium, validated: item names over Windows' component limit fail only while writing.**
   A name longer than 255 UTF-16 code units passes `validate_item_name` and then fails publication on Windows.
   Validate the portable component-length limit when the item is admitted.

9. **Medium, validated: `StreamGroup.__contains__` accepts fractional indices.**
    Compiled `0.5 in StreamGroup[...]` is true when in bounds, while `group[0.5]` rejects the same index and the
    public contract calls it an integer. Membership should require an integral value and return false otherwise.

10. **Medium, reproduced: dev-server restart may fail because address reuse is disabled.**
    The server uses `socketserver.TCPServer`, whose `allow_reuse_address` is false. Restarting after a connection
    can fail with `EADDRINUSE`; an HTTP-server-compatible reusable server should be used.

11. **Low-medium, validated: strict-subclass reflected operator priority is missing.**
    Binary operations, rich comparisons, augmented-assignment fallback, and dictionary/set equality always try
    the left or stored operand first. CPython gives a strict-subclass right operand priority in defined cases.
    The divergence is currently difficult to reach through supported public `Record` values because subclassing a
    concrete record is rejected, but it affects custom/internal `Value` hierarchies.

12. **Low-medium, reproduced: multi-valued `Literal` dimensions silently use the first value.**
    `Array[int, Literal[2, 3]]` becomes a two-element array, and `Literal[True]` bypasses normal dimension
    normalization. Reject multi-valued literals and recursively normalize a single literal value.

13. **Low-medium, reproduced: the supported `range` alias is not represented as a type.**
    The builtin mapping exposes `range` as the bound method `Range.frozen`, so `type(range)` and
    `isinstance(value, range)` do not behave like the other supported builtin type aliases. Decide whether range is
    constructor-only or extend the type shim consistently.

14. **Low, validated: duplicate backend CFG labels compile nondeterministically.**
    Hand-built `BasicBlock` graphs may connect one source to different targets with the same label. Production
    optimizer entry points do not call `verify`, and unordered edge marshaling decides which target survives.
    Normal frontend CFGs enforce unique labels. Reject duplicates at `connect_to` or the marshal boundary.

15. **Low, reproduced: dev-server help crashes on extremely narrow terminals.**
    A terminal width of one or two produces a non-positive `textwrap.fill` width. Clamp the content width.

16. **Low, reproduced: repeated programmatic project imports duplicate the current directory in `sys.path`.**
    [`sonolus/build/cli.py`](sonolus/build/cli.py) compares a `Path` with string entries, so the membership test
    always misses and inserts another copy.

17. **Low, reproduced: the dev server is IPv4-only.**
    Address discovery filters IPv6 and the server uses `AF_INET`, leaving no reachable advertised address on an
    IPv6-only host. Decide whether IPv6 is a supported dev environment.

18. **Low, validated: optimizer toolchain helper `nogil_sum` overflows on Windows.**
    Its C `long` accumulator overflows at `n=65537`, invoking signed-overflow undefined behavior. It is used only
    by optimizer toolchain tests, not production compilation; use a 64-bit accumulator if the helper is retained.

## Open documentation and published API issues

19. **Medium, reproduced: public `QuadLike` still exposes private `_QuadLike`.**
    The generated Quad reference renders `_QuadLike` in the public alias. Promote or rename the protocol so every
    public signature uses a documented public type.

20. **Low, reproduced: builtin truth-testing stubs are too narrow.**
    `all` and `any` accept truth-testable objects, not only `bool`, and `filter` predicates may return any
    truth-testable value. Their published signatures currently reject supported code in type checkers.

21. **Low, reproduced: `zip`'s published signature loses heterogeneous element types.**
    The single type parameter says all input iterables share one element type, although the compiler supports
    heterogeneous inputs. Add bounded heterogeneous overloads or another accurate public signature.

22. **Low, reproduced: `random.uniform` documents only ordered endpoints.**
    The implementation follows Python and accepts reversed endpoints, but its docs say `a <= N <= b` and tests
    cover only non-negative widths. Document the result as lying between the endpoints and add reversed-bound
    coverage, or explicitly reject reversed bounds.

23. **Low, reproduced: existing documentation exceeds the prose line-length standard.**
    Pre-existing lines over 120 columns remain across several concepts pages. This is style debt rather than a
    rendered-doc correctness issue.

## Decided not to fix

24. **Floating-point format differences across Python, the interpreter, and Sonolus.**
    Users must not rely on f32-versus-f64 precision, NaN ordering, or signed-zero operand identity. This includes
    SCCP/folding precision differences, constant branch selection near f32 rounding boundaries, and NaN edge-label
    ordering.

25. **Interpreter `Get` returns diagnostic sentinels for invalid reads.**
    This intentionally makes uninitialized optimizer reads easier to diagnose even though the runtime returns zero.

26. **Deep recursive engine-node formatting.**
    The formatter is for debugging and output at such depth is not practically readable.

27. **Empty closure cells referenced only by dead code.**
    Compiling functions created before a closure cell is initialized is outside the intended compiled-code style.

28. **Dictionary lookup ignores hashes.**
    Supporting compiled hashes is impractical; equal keys are expected to honor Python's equal-hash contract.

29. **Heterogeneous unsortable compile-time dictionary keys.**
    These remain unsupported to avoid input-dependent lookup-performance changes.

30. **`min`/`max` first-operand identity on unsupported signed-zero or NaN ties.**
    The observable cases depend on numeric edge semantics the project does not guarantee.

31. **`min`/`max` over arbitrary mutable-reference iterables.**
    The compiler cannot preserve reference semantics for arbitrary iterables; supported array-like and numeric
    iterator paths remain the contract.

32. **Compile-time-only archetypes returned by `get_archetype_by_name`.**
    Exposing these bases at compile time is accepted even though they are not shipped and have no runtime ID.

33. **Dev-server in-place publication, stale files, and partial writes.**
    The dev server may retain removed endpoints or expose a partial tree after a failed write. Atomic publication is
    not considered worth its complexity for the development-only server.

34. **Resource modification during a dev rebuild can miss the next reload.**
    The narrow timestamp race is accepted for the development-only server.

35. **Existing repository blobs are trusted by filename.**
    Content is not re-hashed before reuse in the development output repository.

36. **`Project.resources` and `Project.converters` documentation mentions only `dev`.**
    Programmatic builds also consume them, but this documentation correction was explicitly declined.

37. **Source recovery assumes UTF-8.**
    PEP 263 encoded source can execute in Python but fail compiler source recovery. Non-UTF-8 engine source remains
    outside the intended project convention.

38. **Compilation installs a process-global exception hook.**
    Preserving or chaining a host application's existing `sys.excepthook` remains an unresolved integration-policy
    choice.

39. **Dev-server repository and network hardening beyond local development needs.**
    The server is not intended as a production HTTP service; security, atomicity, and broad network compatibility
    should be evaluated against that scope before implementation.

40. **Nested annotation evaluation on older Python hosts.**
    On Python 3.12 and 3.13 without `from __future__ import annotations`, the compiler defers nested function
    annotations instead of evaluating them at definition time. Following Python 3.14's deferred semantics is the
    project policy; this narrow divergence does not justify version-dependent compiler behavior.
