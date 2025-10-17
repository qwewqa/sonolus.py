### 0.10.3

- Add `--runtime-checks {none,terminate,notify}` to the `dev` and `build` commands to override runtime check
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
- Added [`Rect.from_margin(...)`][sonolus.script.quad.Rect.from_margin]
- Added [`SpriteGroup`][sonolus.script.sprite.SpriteGroup], [`EffectGroup`][sonolus.script.effect.EffectGroup], and
  [`ParticleGroup`][sonolus.script.particle.ParticleGroup] for array-like access to sprites, effects, and particles
- Added mid-edge properties like [`Quad.mt`][sonolus.script.quad.Quad.mt] and [`Rect.mb`][sonolus.script.quad.Rect.mb]
- Added a warning when an invalid `item.json` is found when loading resources
