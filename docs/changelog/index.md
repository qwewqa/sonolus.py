### 0.8.0

- Changelog introduced
- Fixed some errors when iterating over iterators that are statically determined to be empty
- Added [`Rect.from_margin(...)`][sonolus.script.quad.Rect.from_margin]
- Added [`SpriteGroup`][sonolus.script.sprite.SpriteGroup], [`EffectGroup`][sonolus.script.effect.EffectGroup], and
  [`ParticleGroup`][sonolus.script.particle.ParticleGroup] for array-like access to sprites, effects, and particles
- Added mid-edge properties like [`Quad.mt`][sonolus.script.quad.Quad.mt] and [`Rect.mb`][sonolus.script.quad.Rect.mb]
- Added a warning when an invalid `item.json` is found when loading resources
