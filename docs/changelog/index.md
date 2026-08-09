### 0.18.2

- [`entity_data()`][sonolus.script.archetype.entity_data], previously identical to
  [`imported()`][sonolus.script.archetype.imported], is now private to the engine: entity data fields are no
  longer part of the archetype schema, may not be set when constructing level data, and are never loaded from a
  level.
- `Archetype.schema()` now reports the flat field names level data contains, such as `pos.x` or an
  `imported(name=...)` override, rather than Python attribute names.
- `Archetype.schema()` now also reports an `exports` list holding the archetype's
  [`exported()`][sonolus.script.archetype.exported] field names, which is empty for a watch or preview archetype.
- [`Project.schema()`][sonolus.script.project.Project.schema] now reports each archetype's `exported()` field
  names in a separate `exports` list, and `fields` is limited to the names a level supplies. A name may appear in
  both lists.
- Fixed a write to part of an [`exported()`][sonolus.script.archetype.exported] field, such as a member of a
  `Record` export or an element of an `Array` export, silently exporting nothing.
- Fixed a [`Record`][sonolus.script.record.Record] field of type
  [`EntityRef`][sonolus.script.archetype.EntityRef] copying its constructor argument rather than aliasing it.
- Fixed a `match` value pattern, such as a tuple, [`Array`][sonolus.script.array.Array], or `range` constant,
  failing to compile against a subject holding runtime values.
- Fixed a chained comparison with a link between incomparable types evaluating the whole chain as a compile-time
  constant, ignoring the comparisons before that link.
- Fixed a number on the left of `==` or `!=` against a value of another type, as in `5 == x`, always
  comparing unequal even if a qualifying right operator overload exists.
- Fixed a `match` statement, or an `if`/`elif` chain comparing a value against numeric constants, taking a case's
  branch for a value that is extremely close to that case without being equal to any case.
- `**` unpacking in a dict literal, such as `{**base, "b": 2}`, now reports that it is unsupported rather than
  failing with an internal error.
- Comparing dicts with `==` now reports that dict equality comparison is unsupported, rather than reporting an
  unsupported `raise` statement.
- `range()` with a step of zero now fails an assertion with `range() arg 3 must not be zero`.
- [`interp`][sonolus.script.interval.interp] now also checks that the final `xp` segment is in increasing order.
- Fixed `max()` and `min()` failing to compile when given an explicit `key=None` together with two or more
  arguments.
- Fixed an augmented assignment such as `interval &= other` failing to compile when the type's corresponding
  binary operator branches on a runtime value.
- Fixed a lambda defined inside a `yield` or `yield from` expression failing to compile when it was later called
  from compiled code.
- A default value on a [`@level_memory`][sonolus.script.globals.level_memory] or
  [`@level_data`][sonolus.script.globals.level_data] field is now rejected when the class is defined. Previously
  it was silently discarded.
- Fixed [`Stream.iter_items_since_previous_frame`][sonolus.script.stream.Stream.iter_items_since_previous_frame]
  and its key and value variants yielding the stream's last item again on the next frame when the previous
  frame's time was exactly that item's key.
- An item name that cannot be stored in a collection is now rejected when the item is added: `info` and `list`
  in any letter case (previously such an item silently overwrote the category index), an empty name, a name
  containing a path separator, and `.` or `..`.
- A level name that cannot be stored in a collection is now rejected by `sonolus-py build` and
  [`Project.build`][sonolus.script.project.Project.build].
- An item loaded from an `.scp` file now keeps its full filename as its name.
- `.scp` files, resource directories, and archive entries are now loaded in sorted order, so category listing
  order and default resource selection no longer depend on filesystem or archive order.
- [`Level.export()`][sonolus.script.level.Level.export] and
  [`Engine.export()`][sonolus.script.engine.Engine.export] now accept a plain path string for `cover`, `bgm`,
  `preview`, and `thumbnail`.
- `sonolus-py schema` now prints its progress messages to stderr.
- Fixed dev server rebuilds running level converters on the previous rebuild's output rather than on the levels
  as loaded from resources.
- Fixed a change to a project's `resources` path taking effect one rebuild late in the dev server.
- Corrected the published signatures of `reversed`, `dict`, `max`, `min`, `range`, `random.randrange`, `super`,
  and the `math` module functions to match the calls they accept.
- Reduced the compiled cost of [`Quad.contains_point`][sonolus.script.quad.Quad.contains_point],
  [`pnpoly`][sonolus.script.vec.pnpoly], and [`shuffle`][sonolus.script.array_like.ArrayLike.shuffle].
- Fixed the optimizer moving a loop-invariant division, logarithm, or similar operation ahead of a loop that may
  run zero times.

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
  Previously it was only rejected once the sub-pattern was reached, so an arm whose length test failed at compile
  time silently did not match instead of reporting the unsupported pattern.
- Fixed the message expression of an `assert` being evaluated even when the assertion passed. An `assert` also now
  converts its test the same way `if` does, so a value with `__bool__` or `__len__` behaves alike in both.
- Fixed an internal error when using a bare variable annotation (`x: int`); it is now a no-op. As in Python, it
  also makes the name local, so reading it before it is assigned is an error rather than reading a global.
- A [`Record`][sonolus.script.record.Record] field annotated with a generic type missing its type arguments, such as
  [`EntityRef`][sonolus.script.archetype.EntityRef] rather than `EntityRef[Any]`, is now rejected when the class is
  defined. Previously it was accepted and the field silently overlapped the following field.
- An [`Array`][sonolus.script.array.Array] element type that is not a concrete supported type, such as `Array`
  without its own type arguments, is now rejected when the array type is parameterized. Equivalent spellings of the
  element type, such as `int | float`, `Final[int]`, and `Annotated[int, ...]`, are now normalized so that they
  share a single parameterization.
- An `Array` size that is negative or not an integer is now rejected.
- Fixed a `@streams` data field annotated with a plain type such as `int` being silently dropped.
- [`@streams`][sonolus.script.stream.streams] now reports the offending field name in every declaration error, and
  correctly distinguishes a field annotated with `Annotated[...]` from one with a real default value.
- The declaration decorators ([`@options`][sonolus.script.options.options], [`@skin`][sonolus.script.sprite.skin],
  [`@buckets`][sonolus.script.bucket.buckets], `@streams`, ...) now reject a base class other than `object`;
  previously, using one caused every inherited declaration to be silently dropped.
- `round`'s second parameter is now named `ndigits`, matching Python, so `round(x, ndigits=2)` works.
- Fixed `callable` always returning `False`.
- Fixed `bool` failing on runtime values, and on values whose `__bool__` returns a `Num`. `bool` of an empty string
  or `None` now correctly returns `False`.
- Fixed `reversed()` failing to compile for values whose length is not a compile time constant.
- `sum` now reports an error for non-numeric values instead of modifying the value passed as `start`.
- `min` and `max` now honor the `default` argument for `Array`, [`VarArray`][sonolus.script.containers.VarArray],
  and `range`, which previously ignored it.
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

- Reduced the runtime check overhead from archetype is at checks.

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
- Added [`add_life_scheduled`][sonolus.script.runtime.add_life_scheduled] for scheduling life additions at specific times.
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
