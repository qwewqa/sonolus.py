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
  [`@level_data`][sonolus.script.globals.level_data] field is now rejected when the class is defined.
- Fixed [`Stream.iter_items_since_previous_frame`][sonolus.script.stream.Stream.iter_items_since_previous_frame]
  and its key and value variants yielding the stream's last item again on the next frame when the previous
  frame's time was exactly that item's key.
- An item name that cannot be stored in a collection is now rejected when the item is added: `info` and `list`
  in any letter case, an empty name, a name containing a path separator, and `.` or `..`.
- A level or engine name that cannot be stored in a collection is now rejected by `sonolus-py build` and
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
- Fixed the dev server continuing to use resources from a project's previous `resources` path.
- Corrected the published signatures of `reversed`, `dict`, `max`, `min`, `range`, `random.randrange`, `super`,
  and the `math` module functions to match the calls they accept.
- Reduced the compiled cost of [`Quad.contains_point`][sonolus.script.quad.Quad.contains_point],
  [`pnpoly`][sonolus.script.vec.pnpoly], and [`shuffle`][sonolus.script.array_like.ArrayLike.shuffle].
- Fixed the optimizer moving a loop-invariant division, logarithm, or similar operation ahead of a loop that may
  run zero times.
- Fixed `zip`, and `map` over multiple iterables, advancing the iterators to the right of the first exhausted one
  an extra time, observable through `map` and `filter` callbacks, generator bodies, and custom iterators.
- Fixed a math function applied to compile-time constants outside its domain, such as `math.log(-1.0)` on a
  branch that is never taken, failing to compile.
- Fixed constant `+`, `-`, or `*` on large values failing to compile with `int too large to convert to float`.
- Fixed an `if`/`elif` chain or dict lookup comparing a value against a non-finite constant such as `math.inf`
  producing packaged engine data that is not valid JSON.
- A constant whose magnitude is too large for a 32-bit float now fails compilation with an error naming its
  location when it would be stored in engine data, rather than crashing the build during packaging.
- Fixed a decorated `def` inside compiled code evaluating default values before decorator expressions and
  decorator expressions bottom to top.
- Fixed constructing an [`Array`][sonolus.script.array.Array] from records with a `Final` field, or copying one
  with `+`, failing with `Cannot set a final field`.
- Assigning to a `range` element, directly or through `sort`, `reverse`, or `shuffle`, now reports that `range`
  does not support item assignment rather than an unsupported `raise` statement.
- An unpacking assignment with the wrong number of values now reports Python's counted messages, `too many values
  to unpack` or `not enough values to unpack`, rather than a generic message.
- Reading or writing a field or property of a [`Record`][sonolus.script.record.Record] or an archetype on the
  class rather than on an instance now reports that it must be accessed on an instance.
- Error messages now describe an unsupported value that has no `repr` of its own by its type name rather than
  with a memory address.
- A resource or option declaration rejecting a field's annotation, and a mode rejecting a resource of the wrong
  kind, now name the type of the rejected value rather than printing a memory address.
- Fixed compile time growing exponentially on chained operations over a value fixed after loading, such as
  repeated squaring or a chain of composed transforms.
- Reduced the compiled cost of reading an archetype score multiplier outside of `preprocess`, and of array
  accesses whose index is computed entirely from values fixed after loading.
- Fixed a failed dev server rebuild leaving the `decode` command answering with the failed build's debug message
  numbering rather than the running build's.
- Corrected the published signature of `random.shuffle`, which accepts a mutable array-like such as an
  [`Array`][sonolus.script.array.Array] rather than any mutable sequence.
- Two [`imported()`][sonolus.script.archetype.imported] fields of an archetype that resolve to the same name in
  level data, whether through an `imported(name=...)` override, a repeated
  [`StandardImport`][sonolus.script.archetype.StandardImport], or an inherited field, are now rejected.
- Two [`exported()`][sonolus.script.archetype.exported] fields of an archetype that resolve to the same export
  name are now rejected.
- An archetype field named after a property the archetype inherits, such as `index` or `result`, is now rejected.
- Listing the same archetype class more than once in a mode is now rejected.
- Two different archetype classes that resolve to the same name within a single mode now produce a warning.
- Fixed a falsy value given for an archetype's level data field, such as a
  [`Record`][sonolus.script.record.Record] whose `__bool__` returns false, being shipped as zeros instead of the
  value given. A falsy value of the wrong type, such as `None`, is now rejected rather than silently replaced with
  zeros.
- Fixed a generator expression over a runtime iterable such as an [`Array`][sonolus.script.array.Array] failing to
  compile when an `if` filter is false at compile time and another clause follows it.
- Fixed `visualize_cfg` in `sonolus.script.debug` rendering a graph optimized without the callback it was given,
  so the result could differ from what a build compiles.
- Fixed a generator expression failing to compile with `Binding ... has been modified since the generator was
  created` when an unrelated variable in an enclosing function shares a name with one the generator uses.
- Fixed a generator expression's outermost iterable being evaluated in the generator's own scope rather than the
  enclosing one, so a loop target sharing a name with something that iterable reads, as in
  `sum(x for x in Array(x, x + 1, x + 2))`, silently yielded the wrong values.
- Fixed two functions or lambdas defined on the same source line all compiling the leftmost one's body.
- Fixed a one-shot iterable of archetypes, such as a generator, given to
  [`PlayMode`][sonolus.script.engine.PlayMode], [`WatchMode`][sonolus.script.engine.WatchMode], or
  [`PreviewMode`][sonolus.script.engine.PreviewMode] silently building an engine with no archetypes.
- Fixed an archetype class that also lists a mixin skipping the mixin's `__init_subclass__`. Unrecognized
  keywords in an archetype's class statement are now rejected rather than silently ignored.
- Subclassing an archetype created by [`derive()`][sonolus.script.archetype.PlayArchetype.derive] is now
  rejected.
- A method decorated with [`@callback`][sonolus.script.archetype.callback] that is not a callback of the
  archetype's mode is now rejected, and the error lists the callbacks the archetype does have.
- An archetype field named after a method the archetype inherits, such as `spawn` or `ref`, is now rejected the
  same way as one named after a property.
- A default given to [`imported()`][sonolus.script.archetype.imported] whose type or number of values does not
  match the field it is for, such as a single number for a two-field `Record`, or a `Record` whose fields are
  declared in a different order, is now rejected with an error naming the field.
- A single string given where a sequence of names is expected, in `sprite_group`, `effect_group`,
  `particle_group`, or `replay_fallback_option_names`, is now rejected.
- A `global` or `nonlocal` statement is now rejected wherever it appears in a compiled function, including in
  code skipped at compile time.
- Calling [`add_life_scheduled`][sonolus.script.runtime.add_life_scheduled] anywhere but the `preprocess`
  callback of play or watch mode now fails compilation with an error, `spawn_order`, `spawn_time`, and
  `despawn_time` included.
- [`print_number`][sonolus.script.printing.print_number] outside preview mode, and
  [`InstructionIcon.paint`][sonolus.script.instruction.InstructionIcon.paint] outside tutorial mode, are now
  rejected during compilation.
- Playing an [`Effect`][sonolus.script.effect.Effect] or spawning a
  [`Particle`][sonolus.script.particle.Particle] in preview mode is now rejected during compilation.
- The packaged tutorial engine data no longer contains an extraneous empty `archetypes` list, which is not part
  of the tutorial data format.
- Errors for using archetype life or a score multiplier in an unsupported mode now name the mode rather than
  printing an internal tuple.
- An unpacking assignment from a value that cannot be unpacked, such as an
  [`Array`][sonolus.script.array.Array] or a [`Record`][sonolus.script.record.Record], now reports
  `Cannot unpack a value of type ...` rather than an unsupported starred expression.
- Calling a supported builtin such as `abs` or `math.sin` with arguments that do not match its signature now
  reports the builtin's own name and the number of arguments the call actually passes, rather than an internal
  implementation name and a count one higher.
- Calling a function or a [`Record`][sonolus.script.record.Record] method, or constructing a `Record`, with an
  unexpected keyword argument now reports the unexpected keyword and names the callee.
- An unexpected keyword argument to [`spawn()`][sonolus.script.archetype.PlayArchetype.spawn] now names the
  archetype as well.
- Error messages interpolating a compile-time constant no longer print a memory address.
- A build that gives up optimizing a callback now names the archetype it belongs to alongside the callback and
  the mode, and a callback no path can leave, such as one whose only loop has no exit, now reports that rather
  than an internal `Infinite loop detected`.
- Accessing [`runtime_ui`][sonolus.script.runtime.runtime_ui] outside of compilation now raises a clear
  `RuntimeError` like other runtime accessors, rather than an `AttributeError`.
- Added the 31 [`StandardText`][sonolus.script.text.StandardText] constants missing from the targeted Sonolus
  version, including prefixes, separators, and metadata labels, and corrected the documented text of 21 others.
- Fixed an invalid field declaration in an archetype or a [`@streams`][sonolus.script.stream.streams] class
  being reported during a build with an unrelated internal error, such as
  `Missing annotation for ..._imported_fields_`, instead of the declaration's own error. Processing a class's
  fields no longer leaves partial state behind when a declaration is rejected.
- Fixed a call to a function that terminates on every path, such as one that always fails an assertion or calls
  [`error()`][sonolus.script.debug.error], failing to compile with an internal message about `NoneType` when its
  result was used directly, as in `helper(-1).x`. Such code now compiles to the same termination that assigning
  the result to a variable first already produced.
- Building a level in which an [`EntityRef`][sonolus.script.archetype.EntityRef] field references an entity that
  was not added to the level now fails with an error naming the referring entity and the referenced archetype,
  rather than a bare `KeyError`.
- An archetype callback declared as a `@classmethod` or `@staticmethod` is now rejected when the class is
  defined, with an error naming the archetype and the callback.
- `range(...).index(value)` with a value not in the range now fails an assertion with
  `range.index(x): x not in range`.
- Added support for `.index()` on tuples, with optional `start` and `stop` bounds. A value not in the tuple
  fails an assertion with `tuple.index(x): x not in tuple`.
- An invalid annotation on an [`@options`][sonolus.script.options.options] field now reports the field name and
  the annotation.
- [`Engine.export()`][sonolus.script.engine.Engine.export] now requires `skin`, `background`, `effect`, and
  `particle` to be set, and raises an error naming any that are not.
- Added the `TIME` metric, introduced in Sonolus 1.1.3, to [`UiMetric`][sonolus.script.ui.UiMetric].
- The hint to rerun with `-v` after a compilation error, whether from `sonolus-py build`, `sonolus-py check`,
  or a failed dev server rebuild, now appears only when the full traceback has more to show; failures that
  already print in full, such as optimizer-stage errors, no longer carry it. `BuildConfig.verbose` is now
  documented as read by the dev server only.
- Documented that entity memory exists only in play and watch mode; preview mode has no entity memory.
- Fixed a `match` case's captures being visible on the paths where its pattern did not match. A capture is now
  applied only once the whole pattern has matched, as in Python.
- An assignment expression (`:=`) inside a generator expression is now rejected. Python binds such a target in
  the containing scope as the generator is consumed.
- Fixed an augmented assignment such as `v *= s` on a [`Record`][sonolus.script.record.Record] failing with
  `Cannot accept value NotImplemented` when the type's binary operator declines the right operand. The
  operand's reflected operator now runs, as it already did for `v = v * s`. This reaches `*=` and `/=` on
  [`Vec2`][sonolus.script.vec.Vec2], and a genuinely unsupported operand now reports
  `unsupported operand type(s) for *=` rather than the internal `NotImplemented` error.
- A hand-written in-place operator such as `__iadd__` may now return a new object, which augmented assignment
  stores the same way it stores the result of the corresponding binary operator.
- `in` and `not in` now fall back to iterating the right operand when it defines no `__contains__`, as in
  Python, so membership over a generator expression, `map`, `filter`, `zip`, `enumerate`, or a type defining
  only `__iter__` compiles. The scan stops at the first match, and an iterator is consumed up to it.
- A keyword argument supplied twice, as in `f(**kwargs, b=2)` where `kwargs` holds `b`, is now rejected with
  `got multiple values for keyword argument 'b'`.
- Item access and membership errors now mirror Python's wording: `'X' object is not subscriptable`,
  `'X' object does not support item assignment`, `'X' object does not support item deletion`, and
  `argument of type 'X' is not a container or iterable`. The not-iterable message now reads
  `'X' object is not iterable` wherever it is raised, rather than in two different wordings.
- A watch archetype's `spawn_time` and `despawn_time` now accept
  [`@callback(order=...)`][sonolus.script.archetype.callback], as the Sonolus specification allows.
- Calling [`spawn()`][sonolus.script.archetype.PlayArchetype.spawn] in preview or tutorial mode is now
  rejected during compilation.
- A [`bucket`][sonolus.script.bucket.bucket] whose sprite id or fallback sprite id does not name a sprite of
  the same mode's [`@skin`][sonolus.script.sprite.skin] is now rejected when the engine is built.
- A relative `Path` given for a level's `cover`, `bgm`, or `preview`, or for an engine's `thumbnail`, now
  resolves against the project's `resources` directory, the way the equivalent `str` already did.
- The optimizer now removes a store that writes a memory cell's own value back to it, which an `@=` copy could
  leave behind after optimization.
- A `class` or `async def` statement in code skipped at compile time no longer affects the function containing
  it.
- A non-finite value such as `Infinity` or `NaN` in an item's data, as a third-party resource pack can contain,
  now fails writing the collection with an error naming the file.
- Fixed a portion of compile-time state accumulating for the life of the process, which grew memory use across
  dev server rebuilds.

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
