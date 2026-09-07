### 1.2.3

- Added Python 3.15 support.
- Fixed class patterns for built-in collections and public array and iterator interfaces.
- Fixed [`lerp`][sonolus.script.interval.lerp] and [`lerp_clamped`][sonolus.script.interval.lerp_clamped]
  failing to compile custom arithmetic operators.
- Fixed custom quad-like values failing to compile in drawing and particle helpers.
- Fixed [`is_static_true`][sonolus.script.debug.is_static_true] returning incorrect results for numeric constants.
- Fixed `min` and `max` initializing custom iterators twice when runtime checks are disabled.
- Fixed `type()` returning inconsistent types for tuples, dictionaries, sets, and ranges.

### 1.2.2

- Build performance improvements

### 1.2.1

- Optimizer improvements

### 1.2.0

- Now targeting Sonolus 1.1.4.
- Added support for grouping engine options with [`OptionCategory`][sonolus.script.options.OptionCategory].
- [`is_skip()`][sonolus.script.runtime.is_skip] now supports play mode.
- Added new [`StandardText`][sonolus.script.text.StandardText] constants.
- Documented support for `typing.cast` and `typing.assert_type`.
- Fixed compiled dictionary and set `!=` comparisons being accepted.

### 1.1.0

- Added [`VarArray.of(...)`][sonolus.script.containers.VarArray.of],
  [`ArraySet.of(...)`][sonolus.script.containers.ArraySet.of], and
  [`ArrayMap.of(...)`][sonolus.script.containers.ArrayMap.of] constructors with inferred types and capacities.
- Reduced project build times.

### 1.0.0

- Fixed equality comparisons of records whose fields implement custom runtime comparisons.
- Fixed compiled tuple `<=` and `>=` results for elements with custom comparison methods.

### 0.19.2

- Class-shaped [`@level_memory`][sonolus.script.globals.level_memory] and
  [`@level_data`][sonolus.script.globals.level_data] declarations now reject unannotated data fields.

### 0.19.1

- Compiled `zip` and `map` now support the `strict` parameter.
- Compiled `round` now accepts `None` for `ndigits`.
- Added tuple `.count()` support.
- Set displays now support starred unpacking from tuples and dictionaries.
- SCP collection loading now skips malformed or non-object item entries with a warning.

### 0.19.0

- `Archetype.schema()` now reports flat level-data field names.
- `Archetype.schema()` now reports exported field names.
- [`Project.schema()`][sonolus.script.project.Project.schema] now reports exported field names separately from
  level-data fields.
- Project schemas and engine compilation now reject distinct archetypes with the same runtime name in one mode.
- Union type expressions using `|` now accept `None` and existing unions as operands, and `None` can be used as a
  Record field type or generic type argument.
- Builtin constants, tuples, ranges, dictionaries, and sets now fall back to reflected operators when appropriate.
- Compile-time binary operators and augmented assignment now follow reflected dispatch and grant priority only to
  real strict subclasses, including inherited classmethod operators; builtin type aliases support unions in either
  order.
- Membership now supports iterable values that do not define `__contains__`.
- Ordinary missing-attribute lookup now traces `__getattr__` across direct access, `getattr`, `hasattr`, and class
  patterns.
- Standalone comparisons now preserve record-valued results from custom comparison methods.
- Boolean conditions now use Python truthiness for supported compile-time constants.
- The compiled `range` builtin now supports `type`, `isinstance`, `issubclass`, and union expressions.
- Compiled `dict.get(key)` now supports an omitted default for a compile-time constant key.
- Compiled `range.index` now accepts one positional argument.
- Added tuple `.index()` support with optional positional-only bounds.
- [`VarArray.insert`][sonolus.script.containers.VarArray.insert] now clips indices beyond either end.
- A watch archetype's `spawn_time` and `despawn_time` now accept
  [`@callback(order=...)`][sonolus.script.archetype.callback].
- [`Level.export()`][sonolus.script.level.Level.export] and
  [`Engine.export()`][sonolus.script.engine.Engine.export] now accept a plain path string for `cover`, `bgm`,
  `preview`, and `thumbnail`.
- [`QuadLike`][sonolus.script.quad.QuadLike] is now a public protocol.
- Added the `TIME` metric to [`UiMetric`][sonolus.script.ui.UiMetric].
- Asset URLs with a mixed-case `http` or `https` scheme are now recognized as URLs.
- Fixed partial writes to an [`exported()`][sonolus.script.archetype.exported] field not being exported.
- Fixed [`EntityRef`][sonolus.script.archetype.EntityRef] record fields not preserving their referenced entity.
- Fixed chained comparisons with incomparable links ignoring earlier comparisons.
- Fixed numeric match cases accepting nearby unequal values.
- Fixed stream item iterators repeating an item at a frame boundary.
- Fixed dev server rebuilds running level converters on stale level data.
- Fixed the dev server continuing to use resources from a project's previous `resources` path.
- Fixed the optimizer moving loop-invariant faulting operations before loops that may not run.
- Fixed `zip` and multi-iterable `map` advancing iterators after another one is exhausted.
- Fixed large constant arithmetic failing to compile.
- Fixed non-finite comparison constants producing invalid packaged engine data.
- Fixed evaluation order for decorators and default arguments of nested functions.
- Fixed [`Array`][sonolus.script.array.Array] construction and copying with records containing `Final` fields.
- Fixed falsy archetype level-data values being replaced with zeros.
- Fixed one-shot archetype iterables producing engines without archetypes.
- Fixed match captures being visible before their pattern completely matches.
- Fixed compile-time state accumulating across dev server rebuilds.
- Invalid class patterns and invalid generator returns now report their offending source locations.
- Compiled expressions, decorators, and default arguments now stop evaluating later subexpressions after an
  earlier subexpression terminates.
- Lambdas and generator expressions now treat assignment and comprehension targets as local throughout their
  bodies.
- Generator closures now track bindings read through nested callbacks across resumptions.
- Generators now advance correctly when runtime control flow skips a yield and remain exhausted after completion.
- Added runtime checks for reusing a generator through multiple iterator consumers.
- `for`, generator expressions, and `yield from` no longer trace impossible exhaustion paths for iterators that
  always yield a value.
- Generator expressions that cannot yield or advance now terminate when runtime checks are enabled.
- Fixed loop merging rejecting bindings that source code did not read.
- Duplicate expanded-keyword errors now identify a lambda as `<lambda>`.
- `yield from` now requires an iterator's `next()` method to return `Maybe`.
- Fixed lambdas defined inside `yield` or `yield from` expressions failing to compile.
- Fixed generator expressions over runtime iterables with compile-time false filters failing to compile.
- Fixed generator expressions failing when an unrelated enclosing binding shares a name with a generator binding.
- Fixed generator expression outermost iterables being evaluated in the wrong scope.
- Fixed multiple functions or lambdas on one source line compiling the same body.
- Compiled functions now reject `global` and `nonlocal` statements.
- Fixed direct use of a result from a function that terminates on every path.
- Assignment expressions inside generator expressions are now rejected.
- A `class` or `async def` statement in code skipped at compile time no longer affects the function containing
  it.
- Getter-only property assignments now report the property and target type consistently.
- Corrected the documented input restrictions for compiled `dict()` and `set()` construction.
- Membership now truth-tests custom `__contains__` results, including compile-time constants.
- Boolean contexts now bind `classmethod` and `staticmethod` implementations of `__bool__` and `__len__`
  correctly.
- Implicit operations now ignore special methods synthesized by `__getattr__`.
- Compilation errors now identify the property getter or `__getattr__` that raised `AttributeError` during an
  attribute lookup.
- Unsupported matrix multiplication now reports the operator error.
- Expanded keyword arguments now consistently reject invalid mappings and keys; duplicate keywords identify the
  callee.
- Class patterns now report excess positional subpatterns with the class and supported limit.
- Class patterns now reject a non-tuple `__match_args__` and non-string entries.
- [`ArrayLike.index`][sonolus.script.array_like.ArrayLike.index] and tuple `index` now require integer-valued
  bounds; tuple bounds are positional-only.
- Compiled membership now rejects `NotImplemented` from `__contains__`.
- A subclass attribute explicitly set to `None` now masks an inherited descriptor during compiled lookup.
- Compiled `range()` arguments, `enumerate()` starting indices, and `round()` digit counts must now be
  integer-valued.
- Compiled `len()`, truth testing through `__len__`, and `min()` or `max()` now reject invalid `__len__` results.
- [`StreamGroup`][sonolus.script.stream.StreamGroup] membership now treats fractional indices as absent.
- Fixed strict-subclass right-operand dispatch in binary operations, comparisons, augmented assignment, and
  dict or set membership.
- Dict unpacking now reports that it is unsupported.
- Dict equality comparison now reports that it is unsupported.
- `range()` now rejects a zero step.
- Fixed `max()` and `min()` failing to compile when given an explicit `key=None` together with two or more
  arguments.
- Fixed an augmented assignment such as `interval &= other` failing to compile when the type's corresponding
  binary operator branches on a runtime value.
- Corrected the published signatures of `reversed`, `dict`, `max`, `min`, `range`, `random.randrange`, `super`,
  and the `math` module functions to match the calls they accept.
- `range` item assignment now reports that it is unsupported.
- Invalid unpacking assignments now report counted unpacking errors.
- Fixed archetype mixins skipping `__init_subclass__`; unrecognized class keywords are now rejected.
- Invalid unpacking assignments now identify the value type.
- Builtin call errors now identify the builtin and supplied argument count.
- Unexpected-keyword errors now identify the keyword and callee.
- Unexpected-keyword errors for [`spawn()`][sonolus.script.archetype.PlayArchetype.spawn] now name the archetype.
- `range.index()` now rejects values outside the range.
- `random.randrange`, `random.randint`, and `random.choice` now reject invalid ranges and inputs.
- Sprite z-index tuples now require one to four entries, and [`pnpoly`][sonolus.script.vec.pnpoly] now requires a
  nonempty polygon.
- Fixed augmented assignment not using a reflected operator when applicable.
- Hand-written in-place operators may now return a new object.
- Calls now reject duplicate keyword arguments.
- Item access and membership errors now use consistent Python-style wording.
- Explicitly empty archetype names and imported or exported field names are now preserved.
- Class-shaped global declarations now treat `ClassVar` members as class constants rather than stored fields.
- Explicitly empty engine and level titles are now preserved.
- Tutorial instruction APIs now reject use outside tutorial mode.
- Building level data now rejects adding the same entity instance more than once.
- [`entity_data()`][sonolus.script.archetype.entity_data] is now private to the engine.
- A default value on a [`@level_memory`][sonolus.script.globals.level_memory] or
  [`@level_data`][sonolus.script.globals.level_data] field is now rejected when the class is defined.
- `sonolus-py schema` now prints its progress messages to stderr.
- Class-level record and archetype field access now reports that an instance is required.
- Reduced the compiled cost of certain archetype score multiplier and array accesses.
- Public signatures for buckets, runtime canvas, level score and life, entity references, and engine modes now use
  documented public types.
- Archetypes now reject imported fields with duplicate level-data names.
- Archetypes now reject exported fields with duplicate export names.
- Archetypes now reject fields that shadow inherited properties.
- Listing the same archetype class more than once in a mode is now rejected.
- Preview archetypes now reject entity-memory fields, and `is_scored` must be boolean.
- Rebinding an inherited callback to the mode's default callback now suppresses the inherited callback.
- Fixed `visualize_cfg` not rendering the callback it receives.
- Subclassing an archetype created by [`derive()`][sonolus.script.archetype.PlayArchetype.derive] is now
  rejected.
- Archetypes now reject callbacks unavailable in their mode.
- Archetypes now reject fields that shadow inherited methods.
- [`imported()`][sonolus.script.archetype.imported] now rejects defaults incompatible with their fields.
- [`add_life_scheduled`][sonolus.script.runtime.add_life_scheduled] is now restricted to supported callbacks.
- [`print_number`][sonolus.script.printing.print_number] outside preview mode, and
  [`InstructionIcon.paint`][sonolus.script.instruction.InstructionIcon.paint] outside tutorial mode, are now
  rejected during compilation.
- Playing an [`Effect`][sonolus.script.effect.Effect] or spawning a
  [`Particle`][sonolus.script.particle.Particle] in preview mode is now rejected during compilation.
- Removed an unsupported empty `archetypes` entry from packaged tutorial engine data.
- Unsupported archetype life and score multiplier errors now name the mode.
- Optimizer failures now identify the affected archetype, callback, and mode.
- Callbacks with no exit path now report a clear compilation error.
- Added missing [`StandardText`][sonolus.script.text.StandardText] constants and corrected existing text.
- Fixed invalid archetype and stream field declarations reporting unrelated errors.
- Invalid [`EntityRef`][sonolus.script.archetype.EntityRef] level references now identify the referring entity and
  missing target archetype.
- Archetypes now reject callbacks declared as `@classmethod` or `@staticmethod`.
- Invalid [`@options`][sonolus.script.options.options] annotations now identify the field and annotation.
- Documented that entity memory exists only in play and watch mode; preview mode has no entity memory.
- Calling [`spawn()`][sonolus.script.archetype.PlayArchetype.spawn] in preview or tutorial mode is now
  rejected during compilation.
- [`bucket`][sonolus.script.bucket.bucket] sprite IDs now validate against the mode's
  [`@skin`][sonolus.script.sprite.skin].
- Corrected project resource, build-directory, and exported output path annotations to include `str`.
- Projects now reject duplicate level names.
- Builds now abort when an existing output directory cannot be removed.
- Loading a source collection now rejects conflicting resource item names.
- Loading a source collection now warns and skips items whose `item.json` value is not an object.
- Fixed the dev-server help command crashing when the terminal is extremely narrow.
- Collection item names now reject reserved or unsupported filesystem names.
- `sonolus-py build` and [`Project.build`][sonolus.script.project.Project.build] now reject unsupported engine
  and level names.
- An item loaded from an `.scp` file now keeps its full filename as its name.
- Resource collections now load files, directories, and archive entries in sorted order.
- Resource and option declaration errors now identify rejected value types.
- Fixed failed dev server rebuilds changing `decode` message numbering.
- [`Engine.export()`][sonolus.script.engine.Engine.export] now requires all engine resource groups.
- Clarified that `BuildConfig.verbose` is used by the dev server only.
- Starting the dev server with port `0` now prints the ephemeral port selected by the operating system.
- Relative paths for level and engine media now resolve against the project's `resources` directory.
- Collection writing now rejects non-finite item data and identifies the file.
- Builtin iterator consumers now consistently require a custom iterator's `next()` method to return `Maybe`.
- [`select_option()`][sonolus.script.options.select_option] now rejects invalid integer defaults.
- Clarified that `random.uniform` accepts its endpoints in either order.
- Fixed value patterns failing to compile against runtime subjects.
- Fixed reflected equality dispatch for numbers and values of another type.
- [`interp`][sonolus.script.interval.interp] now also checks that the final `xp` segment is in increasing order.
- Fixed inherited properties accessed through `super()` failing to compile when they read runtime values.
- Reduced the compiled cost of [`Quad.contains_point`][sonolus.script.quad.Quad.contains_point],
  [`pnpoly`][sonolus.script.vec.pnpoly], and [`shuffle`][sonolus.script.array_like.ArrayLike.shuffle].
- Fixed unreachable out-of-domain constant math expressions failing to compile.
- Corrected the published signature of `random.shuffle`.
- Errors interpolating compile-time constants no longer print memory addresses.
- [`runtime_ui`][sonolus.script.runtime.runtime_ui] now reports unsupported use outside compilation clearly.
- Fixed exponential compilation time for chained operations over values fixed after loading.
- The optimizer now removes redundant self-stores.
- Compiled `getattr` and `hasattr` now reject non-string attribute names.
- Unsupported attribute access and assignment errors now identify the attribute and owning type.
- An [`Array`][sonolus.script.array.Array] dimension using `Literal` now requires one value.
- Constants too large for engine data now report their source location during compilation.
- Errors for unsupported values without a `repr` now use their type name.
- Declarations that require a sequence of names now reject a single string.
- The `-v` hint now appears only when a compilation error has more traceback to show.
- Corrected published signatures for `all`, `any`, `filter`, and `zip`.

### 0.18.1

- Fixed a reference type rebound inside a loop being silently read as its pre-loop value, whether read on a later
  iteration or after the loop.

### 0.18.0

- Added support for localized text in options, buckets, and instructions, given as an
  [`AnyText`][sonolus.script.metadata.AnyText] dict mapping locale codes to text.
- Added support for `issubclass`. Both `isinstance` and `issubclass` now also accept a tuple of types.
- Added support for `math.degrees` and `math.radians`.
- Added support for `map` and `filter` over tuples, dicts, sets, and enum classes, behaving like the equivalent
  generator expression. `iter` remains unsupported for these, which have no iterator, but now reports why.
- `enumerate`, `zip`, `min`, and `max` now accept a `set`.
- Fixed the `else` clause of a `for` loop being skipped when iterating over a tuple, dict, set, or enum class.
- Fixed generator expression `if` filters being ignored when iterating over a tuple, dict, set, or enum class.
- Fixed a name bound by a `match` capture pattern, or by a nested `def`, not being carried between loop iterations.
- Fixed a `dict` subject incorrectly matching a sequence pattern such as `case [a, b]`, and an internal error when
  matching a sequence pattern against an enum class subject.
- Fixed an internal error when using an empty sequence pattern (`case []`).
- A star sub-pattern in a sequence pattern, such as `case [a, *rest]`, is now rejected when the `case` is visited.
- Fixed the message expression of an `assert` being evaluated even when the assertion passed. An `assert` also now
  converts its test the same way `if` does, so a value with `__bool__` or `__len__` behaves alike in both.
- Fixed an internal error when using a bare variable annotation (`x: int`); it is now a no-op. As in Python, it
  also makes the name local, so reading it before it is assigned is an error rather than reading a global.
- A [`Record`][sonolus.script.record.Record] field annotated with a generic type missing its type arguments, such as
  [`EntityRef`][sonolus.script.archetype.EntityRef] rather than `EntityRef[Any]`, is now rejected when the class is
  defined.
- An [`Array`][sonolus.script.array.Array] element type that is not a concrete supported type, such as `Array`
  without its own type arguments, is now rejected when the array type is parameterized. Equivalent spellings of the
  element type, such as `int | float`, `Final[int]`, and `Annotated[int, ...]`, are now normalized so that they
  share a single parameterization.
- An `Array` size that is negative or not an integer is now rejected.
- Fixed a `@streams` data field annotated with a plain type such as `int` being silently dropped.
- [`@streams`][sonolus.script.stream.streams] now reports the offending field name in every declaration error, and
  correctly distinguishes a field annotated with `Annotated[...]` from one with a real default value.
- The declaration decorators ([`@options`][sonolus.script.options.options], [`@skin`][sonolus.script.sprite.skin],
  [`@buckets`][sonolus.script.bucket.buckets], `@streams`, ...) now reject a base class other than `object`.
- `round`'s second parameter is now named `ndigits`, matching Python, so `round(x, ndigits=2)` works.
- Fixed `callable` always returning `False`.
- Fixed `bool` failing on runtime values, and on values whose `__bool__` returns a `Num`. `bool` of an empty string
  or `None` now correctly returns `False`.
- Fixed `reversed()` failing to compile for values whose length is not a compile time constant.
- `sum` now reports an error for non-numeric values instead of modifying the value passed as `start`.
- `min` and `max` now honor the `default` argument for `Array`, [`VarArray`][sonolus.script.containers.VarArray],
  and `range`.
- `map` and `filter` now report a non-iterable argument the same way `iter` and `zip` do.
- Fixed `isinstance` reporting a `set` or `dict` as a `Record`; `issubclass` rejects those pairings in the same way.
- Fixed a numeric value returned by `next` on a generator changing when `next` was called again.
- Fixed an overflow error when raising a float to a large power.
- Fixed a copied `EntityRef` losing the entity it refers to, which wrote a dangling reference into level data. This
  affected [`copy()`][sonolus.script.values.copy], including copying an `Array` or `Record` that holds references,
  and constructing an `Array` directly from references, such as `Array(a.ref(), b.ref())`.
- Fixed `!=` on an `EntityRef` created by `ref()` comparing fields rather than the referenced entity, so that two
  references to different entities reported neither equal nor unequal while building level data.
- Fixed the optimizer removing a block whose only contents were side effects.

### 0.17.3

- Bug fixes.

### 0.17.2

- Performance improvements.

### 0.17.1

- Reduced the runtime check overhead of archetype checks.

### 0.17.0

- Improved performance.
- Rewritten optimizer.
- The `dev` command now uses standard (`-O2`) optimization by default.
- Exceeding level memory and level data capacity now results in an error.

### 0.16.0

- Now targeting Sonolus 1.1.2.
- Added support for multiple z-indexes.
- Added support for option titles.

### 0.15.9

- Fixed error due to a compile time division by zero in some cases.

### 0.15.8

- Minor improvements to optimization of commutative operators.

### 0.15.7

- Minor optimization improvements.

### 0.15.6

- Optimization improvements.

### 0.15.5

- Added loop invariant code motion optimization.
- Improved inlining optimization behavior surrounding loops.

### 0.15.4

- Improved common subexpression elimination.

### 0.15.3

- Improved optimization of memory accesses.

### 0.15.2

- Improvements to expression simplification in the optimizer.
- Added global common subexpression elimination to the standard optimizer passes.

### 0.15.1

- Fixed non-numeric variables defined outside a loop causing an error when redefined within the loop.

### 0.15.0

- Added support for the [`safe_area()`][sonolus.script.runtime.safe_area] function.

### 0.14.7

- Added support for calling `len()` on Enum classes.

### 0.14.6

- Added support for the `dict()` and `set()` functions.

### 0.14.5

- Support iterating through Enum classes to get members.

### 0.14.4

- Sets and dicts are now always distinct types and support `isinstance` checks.
- Improved the performance of some dict and set operations.
- Added support for `dict.get(item, default)` method with the limitation that the returned value is a copy.

### 0.14.3

- Access to a `dict` with a non-compile-time-constant key is now supported when all values are compile time constants
  of a single supported type (numeric, array, or record).
- Usage of set literals containing non-numeric types is now supported.

### 0.14.2

- Fixed a bug causing memory corruption when using generators.

### 0.14.1

- Fixed support for Archetypes to inherit from an `ABC`.

### 0.14.0

- Sonolus 1.1.0 support.
- Added haptic feedback support via [`HapticType`][sonolus.script.archetype.HapticType] enum in entity input.
- Added `progress_graph` UI layout support in watch mode.
- Added support for `replay_fallback_option_names` in engine configuration.
- Added [`add_life_scheduled`][sonolus.script.runtime.add_life_scheduled] for scheduling timed life additions.
- Added `initial` and `maximum` properties to level life configuration.
- Added the `archetype_score_multiplier` and `entity_score_multiplier` properties to archetypes.
- Added the `archetype_life` (replacing the now deprecated `life` property) and `entity_life` property to archetypes.
- Added import defaults via optional `default` parameter in [`imported()`][sonolus.script.archetype.imported].
- Expanded [`StandardText`][sonolus.script.text.StandardText] constants.
- Added validation for overriding reserved archetype field names.
- Improved support for `Literal` type specifications and `Enum` subclass handling in some edge cases.

### 0.13.2

- Improved dev server build times.

### 0.13.1

- Improved dev server build times.

### 0.13.0

- Compile time exception stack traces now show function names.
- Improved dev server rebuild times.

### 0.12.10

- More functions are now shown in the dev server's debug stack traces.
- Improved compiler performance slightly.
- Removed erroneous pytest dependency.

### 0.12.9

- Added support for `typing.Final` annotations in [`Record`][sonolus.script.record.Record] classes.

### 0.12.8

- Fix optimizer bug with the min/max functions.

### 0.12.7

- Improved the performance of [`make_comparable_float`][sonolus.script.numtools.make_comparable_float].

### 0.12.6

- Added [`make_comparable_float`][sonolus.script.numtools.make_comparable_float] for generating floats that adhere
  strictly to layers when calculating z-indexes.

### 0.12.5

- Improved some error messages, particularly around type annotations.

### 0.12.4

- Added a `-v`/`--verbose` flag to the cli that prints out full tracebacks on errors.
- Streams are now initialized lazily to allow circular imports as long as they're resolved before the stream is used.

### 0.12.3

- `assert False` is no longer stripped in release (non-dev) builds to allow asserting to the compiler that code is
  unreachable.
- Fixed the return value of `type()` on archetype instances being incorrect.

### 0.12.2

- Incorrectly declared resources such as Buckets missing an [`@buckets`][sonolus.script.bucket.buckets] decorator now
  result in a helpful error message.
- [`Record`][sonolus.script.record.Record] classes can now subclass `ABC` or `Protocol` from the Python standard
  library.
- Archetypes can now subclass `ABC` or `Protocol` from the Python standard library.

### 0.12.1

- Added support for the `getattr()`, `setattr()`, `hasattr()`, and `sum()` built-in functions.

### 0.12.0

- Improved support for mixin classes in archetypes, including support for callbacks and memory fields within mixins.
- Accessing archetypes at indexes such as with [`EntityRef`][sonolus.script.archetype.EntityRef] now checks that the
  entity at the index is of the correct archetype in dev builds by default.

### 0.11.1

- Memory usage no longer increases indefinitely when rebuilding with changes in the dev server.
- [`EntityRef`][sonolus.script.archetype.EntityRef] now throws an error when converted to a boolean.

### 0.11.0

- Added basic support for set literals of numbers, with support for membership checks (`in`, `not in`) and iteration.
- Added support for membership checks (`in`, `not in`) of tuples.
- Fixed some instances where error messages for archetype declarations were not shown correctly.
- Reduced memory usage slightly.

### 0.10.9

- Added [`Vec2.normalize_or_zero()`][sonolus.script.vec.Vec2.normalize_or_zero].
- Added `--gc`/`--no-gc` to cli commands and made no-gc the default behavior to improve performance.

### 0.10.8

- Fixed issue when parameterizing the `type` built-in as a generic type.

### 0.10.7

- Added support for the `type()` built-in function.
- Added the [`angle_diff()`][sonolus.script.vec.angle_diff] and
  [`signed_angle_diff()`][sonolus.script.vec.signed_angle_diff] functions.
- Added the [`sort_linked_entities()`][sonolus.script.containers.sort_linked_entities] function.

### 0.10.6

- Fixed the dev server becoming unresponsive after invalid command arguments.

### 0.10.5

- Fixed the dev server becoming unresponsive after a blank command.

### 0.10.4

- Fixed the dev server becoming unresponsive after a command syntax error.

### 0.10.3

- Added `--runtime-checks {none,terminate,notify}` to the `dev` and `build` commands to override runtime check
  (e.g. assertion) behavior.

### 0.10.2

- Fixed error with [`Vec2.normalize()`][sonolus.script.vec.Vec2.normalize].

### 0.10.1

- Assertions are now stripped in release (non-dev) builds.
- Added more assertion checks including bounds checks for arrays.
- Added [`require()`][sonolus.script.debug.require] for assertions not stripped in release builds.

### 0.10.0

- Added `[d]ecode` command to dev server for decoding debug message codes.
- Added `[h]elp` command to dev server.
- Added [`notify()`][sonolus.script.debug.notify] for logging debug messages.

### 0.9.3

- Added support for string use item values in levels.

### 0.9.2

- Fixed dev server sometimes not exiting without further input upon a keyboard interrupt.

### 0.9.1

- Added project urls.

### 0.9.0

- New dev server cli with faster rebuild times.
- Performance improvements.

### 0.8.0

- Changelog introduced.
- Fixed some errors when iterating over iterators that are statically determined to be empty.
- Added [`Rect.from_margin()`][sonolus.script.quad.Rect.from_margin].
- Added [`SpriteGroup`][sonolus.script.sprite.SpriteGroup], [`EffectGroup`][sonolus.script.effect.EffectGroup], and
  [`ParticleGroup`][sonolus.script.particle.ParticleGroup] for array-like access to sprites, effects, and particles.
- Added mid-edge properties like [`Quad.mt`][sonolus.script.quad.Quad.mt] and [`Rect.mb`][sonolus.script.quad.Rect.mb].
- Added a warning when an invalid `item.json` is found when loading resources.
