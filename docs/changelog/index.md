### 0.12.5

- Improve some error messages, particularly around type annotations

### 0.12.4

- Added a `-v`/`--verbose` flag to the cli that prints out full tracebacks on errors
- Streams are now initialized lazily to allow circular imports as long as they're resolved before the stream is used

### 0.12.3

- `assert False` is no longer stripped in release (non-dev) builds to allow asserting to the compiler that code is
  unreachable
- Fixed the return value of `type()` on archetype instances being incorrect

### 0.12.2

- Incorrectly declared resources such as Buckets missing an [`@buckets`][sonolus.script.bucket.buckets] decorator now 
  result in a helpful error message
- [`Record`][sonolus.script.record.Record] classes can now subclass `ABC` or `Protocol` from the Python standard
  library
- Archetypes can now subclass `ABC` or `Protocol` from the Python standard library

### 0.12.1

- Added support for the `getattr()`, `setattr()`, `hasattr()`, and `sum()` built-in functions

### 0.12.0

- Improved support for mixin classes in archetypes, including support for callbacks and memory fields within mixins
- Accessing archetypes at indexes such as with [`EntityRef`][sonolus.script.archetype.EntityRef] now checks that the
  entity at the index is of the correct archetype in dev builds by default

### 0.11.1

- Memory usage no longer increases indefinitely when rebuilding with changes in the dev server
- [`EntityRef`][sonolus.script.archetype.EntityRef] now throws an error when converted to a boolean

### 0.11.0

- Added basic support for set literals of numbers, with support for membership checks (`in`, `not in`) and iteration
- Added support for membership checks (`in`, `not in`) of tuples
- Fixed some instances where error messages for archetype declarations were not shown correctly
- Reduced memory usage slightly

### 0.10.9

- Added [`Vec2.normalize_or_zero()`][sonolus.script.vec.Vec2.normalize_or_zero]
- Added `--gc`/`--no-gc` to cli commands and made no-gc the default behavior to improve performance

### 0.10.8

- Fixed issue when parameterizing the `type` built-in as a generic type

### 0.10.7

- Added support for the `type()` built-in function
- Added the [`angle_diff()`][sonolus.script.vec.angle_diff] and 
  [`signed_angle_diff()`][sonolus.script.vec.signed_angle_diff] functions
- Added the [`sort_linked_entities()`][sonolus.script.containers.sort_linked_entities] function

### 0.10.6

- Fixed the dev server becoming unresponsive after invalid command arguments

### 0.10.5

- Fixed the dev server becoming unresponsive after a blank command

### 0.10.4

- Fixed the dev server becoming unresponsive after a command syntax error

### 0.10.3

- Added `--runtime-checks {none,terminate,notify}` to the `dev` and `build` commands to override runtime check
  (e.g. assertion) behavior

### 0.10.2

- Fixed error with [`Vec2.normalize()`][sonolus.script.vec.Vec2.normalize]

### 0.10.1

- Assertions are now stripped in release (non-dev) builds
- Added more assertion checks including bounds checks for arrays
- Added [`require()`][sonolus.script.debug.require] for assertions not stripped in release builds

### 0.10.0

- Added `[d]ecode` command to dev server for decoding debug message codes
- Added `[h]elp` command to dev server
- Added [`notify()`][sonolus.script.debug.notify] for logging debug messages

### 0.9.3

- Added support for string use item values in levels

### 0.9.2

- Fixed dev server sometimes not exiting without further input upon a keyboard interrupt

### 0.9.1

- Added project urls

### 0.9.0

- New dev server cli with faster rebuild times
- Performance improvements

### 0.8.0

- Changelog introduced
- Fixed some errors when iterating over iterators that are statically determined to be empty
- Added [`Rect.from_margin()`][sonolus.script.quad.Rect.from_margin]
- Added [`SpriteGroup`][sonolus.script.sprite.SpriteGroup], [`EffectGroup`][sonolus.script.effect.EffectGroup], and
  [`ParticleGroup`][sonolus.script.particle.ParticleGroup] for array-like access to sprites, effects, and particles
- Added mid-edge properties like [`Quad.mt`][sonolus.script.quad.Quad.mt] and [`Rect.mb`][sonolus.script.quad.Rect.mb]
- Added a warning when an invalid `item.json` is found when loading resources
