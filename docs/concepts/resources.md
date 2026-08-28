# Resources & Declarations
This page covers two kinds of declaration: the storage available to modes and archetypes (level memory, level data,
imported and exported fields, entity data, entity memory, shared memory, and streams) and the resources an engine
declares (skins, sound effects, particles, buckets, tutorial instructions, options, and UI configuration).
For where the underlying asset files are placed on disk, see [Resource Files](project.md#resource-files).

## Global Variables

Global storage fields must be annotated and cannot be assigned values in the class body. Class-level values may be
declared with `ClassVar` and are not stored as globals.

### Level Memory
Level memory is defined with the [`@level_memory`][sonolus.script.globals.level_memory] class decorator:

```python
from typing import ClassVar

from sonolus.script.globals import level_memory


@level_memory
class LevelMemory:
    value: int
    scale: ClassVar[int] = 2
```

Alternatively, it may be called as a function as well by passing the type as an argument:

```python
from sonolus.script.globals import level_memory
from sonolus.script.vec import Vec2


level_memory_value = level_memory(Vec2)
```

Level memory exists in play, watch, and tutorial mode. Preview mode has no level memory; use
[`@level_data`][sonolus.script.globals.level_data] there instead.

In those modes, level memory may be read in any callback and modified in these callbacks (see
[Modes](project.md#modes) for each mode's callbacks):

- Play: `preprocess`, `update_sequential`, `touch`
- Watch: `preprocess`, `update_sequential`
- Tutorial: `preprocess`, `navigate`, `update`

All level memory in a mode shares a combined limit of 4096 values; exceeding it raises a compilation error.

### Level Data
Level data is defined with the [`@level_data`][sonolus.script.globals.level_data] class decorator:

```python
from sonolus.script.globals import level_data


@level_data
class LevelData:
    value: int
```

Alternatively, it may be called as a function as well by passing the type as an argument:

```python
from sonolus.script.globals import level_data
from sonolus.script.vec import Vec2


level_data_value = level_data(Vec2)
```

Level data may only be modified in the `preprocess` callback and may be read in any callback.

All level data in a mode shares a combined limit of 4096 values; exceeding it raises a compilation error.

## Archetype Variables

### Imported
Imported fields are declared with [`imported()`][sonolus.script.archetype.imported]:

```python
from sonolus.script.archetype import PlayArchetype, imported

class MyArchetype(PlayArchetype):
    field: int = imported()
    field_with_explicit_name: int = imported(name="field_name")
    field_with_default: int = imported(default=0)
```

Imported fields may be loaded from the level. In watch mode, data may also be loaded from a corresponding
exported field in play mode.

Imported fields may only be updated in the `preprocess` callback, and are read-only in other callbacks.

### Exported
Exported fields are declared with [`exported()`][sonolus.script.archetype.exported]:

```python
from sonolus.script.archetype import PlayArchetype, exported

class MyArchetype(PlayArchetype):
    field: int = exported()
    field_with_explicit_name: int = exported(name="#FIELD")
```

This is only usable in play mode to export data to be loaded in watch mode. Exported fields are write-only.

### Entity Data
Entity data fields are declared with [`entity_data()`][sonolus.script.archetype.entity_data]:

```python
from sonolus.script.archetype import PlayArchetype, entity_data

class MyArchetype(PlayArchetype):
    field: int = entity_data()
```

Entity data is accessible from other entities, but may only be updated in the `preprocess` callback. It is read-only
in other callbacks.

Entity data shares storage with [`imported()`][sonolus.script.archetype.imported] fields but is private to the
engine: it is not part of the archetype schema, may not be set when constructing level data, and is never loaded
from a level.

### Entity Memory
Entity memory fields are declared with [`entity_memory()`][sonolus.script.archetype.entity_memory]:

```python
from sonolus.script.archetype import PlayArchetype, entity_memory

class MyArchetype(PlayArchetype):
    field: int = entity_memory()
```

Entity memory is private to the entity and is not accessible from other entities. It may be read or updated in any
callback associated with the entity.

Entity memory exists in play and watch mode. Preview mode has no entity memory; compute per-entity values in
`preprocess` and store them in [`entity_data()`][sonolus.script.archetype.entity_data] or
[`shared_memory()`][sonolus.script.archetype.shared_memory] fields, which are read-only in `render`.

Entity memory fields may also be set when an entity is spawned using the
[`spawn()`][sonolus.script.archetype.PlayArchetype.spawn] method.

### Shared Memory
Shared memory fields are declared with [`shared_memory()`][sonolus.script.archetype.shared_memory]:

```python
from sonolus.script.archetype import PlayArchetype, shared_memory

class MyArchetype(PlayArchetype):
    field: int = shared_memory()
```

Shared memory is accessible from other entities.

Shared memory may be read in any callback, but may only be updated by sequential callbacks (`preprocess`,
`update_sequential`, and `touch`).

## Streams
Streams are defined with the [`@streams`][sonolus.script.stream.streams] decorator:

```python
from sonolus.script.array import Dim
from sonolus.script.stream import streams, Stream, StreamGroup
from sonolus.script.num import Num
from sonolus.script.vec import Vec2

@streams
class Streams:
    stream_1: Stream[Num]  # A stream of Num values
    stream_2: Stream[Vec2]  # A stream of Vec2 values
    group_1: StreamGroup[Num, Dim[10]]  # A group of 10 Num streams
    group_2: StreamGroup[Vec2, Dim[5]]  # A group of 5 Vec2 streams
    
    data_field_1: Num  # A data field of type Num
    data_field_2: Vec2  # A data field of type Vec2
```
    
Streams and stream groups are declared by annotating class attributes with [`Stream`][sonolus.script.stream.Stream]
or [`StreamGroup`][sonolus.script.stream.StreamGroup].

Other types are also supported in the form of data fields. They may be used to store additional data to export from
play mode to watch mode.

In either case, data is write-only in play mode and read-only in watch mode.

This should only be used once in most projects, as multiple decorated classes will overlap with each other and
interfere when both are used at the same time.

For backwards compatibility, new streams and stream groups should be added to the end of existing ones, and
lengths and element types of existing streams and stream groups should not be changed. Otherwise, old replays may
not work on new versions of the engine.

## Skins
Skins are defined with the [`@skin`][sonolus.script.sprite.skin] decorator:

```python
from sonolus.script.sprite import skin, StandardSprite, sprite, Sprite, sprite_group, SpriteGroup, RenderMode


@skin
class Skin:
    render_mode: RenderMode = RenderMode.DEFAULT

    note: StandardSprite.NOTE_HEAD_RED
    other: Sprite = sprite("other")
    group: SpriteGroup = sprite_group(["one", "two", "three"])
```

Standard sprites are defined by annotating the field with the corresponding value from
[`StandardSprite`][sonolus.script.sprite.StandardSprite].

Custom sprites are defined by annotating the field with [`Sprite`][sonolus.script.sprite.Sprite] and calling
[`sprite`][sonolus.script.sprite.sprite] with the sprite name.

A group of sprites sharing consecutive IDs can be defined by annotating the field with
[`SpriteGroup`][sonolus.script.sprite.SpriteGroup] and calling [`sprite_group`][sonolus.script.sprite.sprite_group]
with the sprite names; indexing the group returns the [`Sprite`][sonolus.script.sprite.Sprite] at that index.

To set the render mode for the skin, set the `render_mode` field to the desired value from
[`RenderMode`][sonolus.script.sprite.RenderMode].

The [`draw`][sonolus.script.sprite.Sprite.draw] methods take a `z` argument, which may be a single value or a tuple
of up to four values, where later values break ties on earlier ones. Values that are not supplied are treated
as `0`.

## Sound Effects
Sound effects are defined with the [`@effects`][sonolus.script.effect.effects] decorator:

```python
from sonolus.script.effect import effects, StandardEffect, Effect, effect, effect_group, EffectGroup


@effects
class Effects:
    tap_perfect: StandardEffect.PERFECT
    other: Effect = effect("other")
    group: EffectGroup = effect_group(["one", "two", "three"])
```

Standard sound effects are defined by annotating the field with the corresponding value from
[`StandardEffect`][sonolus.script.effect.StandardEffect].

Custom sound effects are defined by annotating the field with [`Effect`][sonolus.script.effect.Effect] and calling
[`effect`][sonolus.script.effect.effect] with the effect name.

A group of sound effects sharing consecutive IDs can be defined by annotating the field with
[`EffectGroup`][sonolus.script.effect.EffectGroup] and calling [`effect_group`][sonolus.script.effect.effect_group]
with the effect names; indexing the group returns the [`Effect`][sonolus.script.effect.Effect] at that index.

## Particles
Particles are defined with the [`@particles`][sonolus.script.particle.particles] decorator:

```python
from sonolus.script.particle import particles, StandardParticle, Particle, particle, particle_group, ParticleGroup


@particles
class Particles:
    tap: StandardParticle.NOTE_CIRCULAR_TAP_RED
    other: Particle = particle("other")
    group: ParticleGroup = particle_group(["one", "two", "three"])
```

Standard particles are defined by annotating the field with the corresponding value from
[`StandardParticle`][sonolus.script.particle.StandardParticle].

Custom particles are defined by annotating the field with [`Particle`][sonolus.script.particle.Particle] and calling
[`particle`][sonolus.script.particle.particle] with the particle name.

A group of particles sharing consecutive IDs can be defined by annotating the field with
[`ParticleGroup`][sonolus.script.particle.ParticleGroup] and calling
[`particle_group`][sonolus.script.particle.particle_group] with the particle names; indexing the group returns the
[`Particle`][sonolus.script.particle.Particle] at that index.

## Buckets
Buckets are defined with the [`@buckets`][sonolus.script.bucket.buckets] decorator:

```python
from sonolus.script.bucket import buckets, bucket_sprite, bucket, Bucket
from sonolus.script.text import StandardText
from my_engine.common.skin import Skin

@buckets
class Buckets:
    note: Bucket = bucket(
        sprites=[
            bucket_sprite(
                sprite=Skin.note,
                x=0,
                y=0,
                w=2,
                h=2,
            )
        ],
        unit=StandardText.MILLISECOND_UNIT,
    )
```

Buckets are defined by annotating the field with [`Bucket`][sonolus.script.bucket.Bucket] and calling
[`bucket`][sonolus.script.bucket.bucket] with the sprites that make up the bucket's icon.

The `unit` label may be a plain string or an [`AnyText`][sonolus.script.metadata.AnyText] localization dict mapping
locale codes to text.

### Judging

Each bucket has a [`window`][sonolus.script.bucket.Bucket.window] holding the
[`JudgmentWindow`][sonolus.script.bucket.JudgmentWindow] used to judge hits, which is built from a `perfect`,
`great`, and `good` [`Interval`][sonolus.script.interval.Interval]. It is writable only during
[`preprocess`][sonolus.script.archetype.PlayArchetype.preprocess].

[`JudgmentWindow.judge`][sonolus.script.bucket.JudgmentWindow.judge] compares an `actual` time against a `target`
time and returns the matching [`Judgment`][sonolus.script.bucket.Judgment], which is one of `PERFECT`, `GREAT`,
`GOOD`, or `MISS`.

An entity reports its outcome by writing to [`result`][sonolus.script.archetype.PlayArchetype.result], a
[`PlayEntityInput`][sonolus.script.archetype.PlayEntityInput] with `judgment`, `accuracy`, `bucket`,
`bucket_value`, and `haptic` fields. This is only meaningful for archetypes that set
[`is_scored`][sonolus.script.archetype.PlayArchetype.is_scored], since those are the entities that contribute to
combo and score.

## Tutorial Instructions
Tutorial instructions are defined with the [`@instructions`][sonolus.script.instruction.instructions] decorator:

```python
from sonolus.script.instruction import instructions, StandardInstruction, Instruction, instruction


@instructions
class Instructions:
    tap: StandardInstruction.TAP
    other: Instruction = instruction("other")
```

Standard instructions are defined by annotating the field with the corresponding value from
[`StandardInstruction`][sonolus.script.instruction.StandardInstruction].

Custom instructions are defined by annotating the field with
[`Instruction`][sonolus.script.instruction.Instruction] and calling
[`instruction`][sonolus.script.instruction.instruction] with the instruction name.

The instruction name given to [`instruction`][sonolus.script.instruction.instruction] may be a plain string or an
[`AnyText`][sonolus.script.metadata.AnyText] localization dict.

## Tutorial Instruction Icons
Tutorial instruction icons are defined with the
[`@instruction_icons`][sonolus.script.instruction.instruction_icons] decorator:

```python
from sonolus.script.instruction import instruction_icons, StandardInstructionIcon, InstructionIcon, instruction_icon


@instruction_icons
class InstructionIcons:
    hand: StandardInstructionIcon.HAND
    other: InstructionIcon = instruction_icon("other")
```

Standard instruction icons are defined by annotating the field with the corresponding value from
[`StandardInstructionIcon`][sonolus.script.instruction.StandardInstructionIcon].

Custom instruction icons are defined by annotating the field with
[`InstructionIcon`][sonolus.script.instruction.InstructionIcon] and calling
[`instruction_icon`][sonolus.script.instruction.instruction_icon] with the icon name.

## Options
Engine options are defined with the [`@options`][sonolus.script.options.options] decorator:

```python
from sonolus.script.options import OptionCategory, options, select_option, slider_option, toggle_option


@options
class Options:
    gameplay = OptionCategory(title="Gameplay")

    slider_option: float = slider_option(
        name="Slider Option",
        category=gameplay,
        title="Slider Option Title",
        standard=True,
        advanced=False,
        default=0.5,
        min=0,
        max=1,
        step=0.1,
        unit="unit",
        scope="scope",
    )
    toggle_option: bool = toggle_option(
        name="Toggle Option",
        title="Toggle Option Title",
        standard=True,
        advanced=False,
        default=True,
        scope="scope",
    )
    select_option: int = select_option(
        name="Select Option",
        title="Select Option Title",
        standard=True,
        advanced=False,
        default="value",
        values=["value"],
        scope="scope",
    )
```

There are three types of options available:

1. [`slider_option`][sonolus.script.options.slider_option]: A slider control for numeric values
2. [`toggle_option`][sonolus.script.options.toggle_option]: A toggle switch for boolean values
3. [`select_option`][sonolus.script.options.select_option]: A dropdown menu for selecting from predefined values

If `title` is unset, the option's `name` is shown instead. `title`, `description`, and (for `slider_option`) `unit`
may each be a plain string or an [`AnyText`][sonolus.script.metadata.AnyText] localization dict, as can each entry of
`select_option`'s `values`.

Options can be grouped by assigning [`OptionCategory`][sonolus.script.options.OptionCategory] objects to attributes
on the options class. Pass the category object to an option's `category` parameter. A string matching a declared
category's name is also accepted. If a category's `name` is unset, its attribute name is used. If its `title` is
unset, its resolved name is shown. Options without a `category` remain uncategorized. Category titles may also be
a plain string or an [`AnyText`][sonolus.script.metadata.AnyText] localization dict.

## UI
UI configuration is defined with the [`UiConfig`][sonolus.script.ui.UiConfig] class:

```python
from sonolus.script.ui import (
    EaseType,
    UiAnimation,
    UiAnimationTween,
    UiConfig,
    UiJudgmentErrorPlacement,
    UiJudgmentErrorStyle,
    UiMetric,
    UiVisibility,
)

ui_config = UiConfig(
    scope="my_engine",
    primary_metric=UiMetric.ARCADE,
    secondary_metric=UiMetric.LIFE,
    menu_visibility=UiVisibility(
        scale=1.0,
        alpha=1.0,
    ),
    judgment_visibility=UiVisibility(
        scale=1.0,
        alpha=1.0,
    ),
    combo_visibility=UiVisibility(
        scale=1.0,
        alpha=1.0,
    ),
    primary_metric_visibility=UiVisibility(
        scale=1.0,
        alpha=1.0,
    ),
    secondary_metric_visibility=UiVisibility(
        scale=1.0,
        alpha=1.0,
    ),
    progress_visibility=UiVisibility(
        scale=1.0,
        alpha=1.0,
    ),
    tutorial_navigation_visibility=UiVisibility(
        scale=1.0,
        alpha=1.0,
    ),
    tutorial_instruction_visibility=UiVisibility(
        scale=1.0,
        alpha=1.0,
    ),
    judgment_animation=UiAnimation(
        scale=UiAnimationTween(
            start=1.0,
            end=1.0, 
            duration=0.0,
            ease=EaseType.NONE,
        ),
        alpha=UiAnimationTween(
            start=1.0,
            end=1.0,
            duration=0.0,
            ease=EaseType.NONE,
        ),
    ),
    combo_animation=UiAnimation(
        scale=UiAnimationTween(
            start=1.2, 
            end=1.0, 
            duration=0.2,
            ease=EaseType.IN_CUBIC,
        ),
        alpha=UiAnimationTween(
            start=1.0,
            end=1.0, 
            duration=0.0,
            ease=EaseType.NONE,
        ),
    ),
    judgment_error_style=UiJudgmentErrorStyle.LATE,
    judgment_error_placement=UiJudgmentErrorPlacement.TOP,
    judgment_error_min=0.0,
)
```
