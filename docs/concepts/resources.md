# Resources & Declarations

## Global Variables

### Level Memory
Level memory is defined with the [`@level_memory`][sonolus.script.globals.level_memory] class decorator:

```python
from sonolus.script.globals import level_memory


@level_memory
class LevelMemory:
    value: int
```

Alternatively, it may be called as a function as well by passing the type as an argument:

```python
from sonolus.script.globals import level_memory
from sonolus.script.vec import Vec2


level_memory_value = level_memory(Vec2)
```

Level memory may be modified in sequential callbacks:

- `preprocess`
- `update_sequential`
- `touch`

and may be read in any callback.

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

## Archetype Variables

### Imported
Imported fields are declared with [`imported()`][sonolus.script.archetype.imported]:

```python
from sonolus.script.archetype import PlayArchetype, imported

class MyArchetype(PlayArchetype):
    field: int = imported()
    field_with_explicit_name: int = imported(name="field_name")
```

Imported fields may be loaded from the level data. In watch mode, data may also be loaded from a corresponding exported field in play mode.

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

Entity data is accessible from other entities, but may only be updated in the `preprocess` callback and is read-only in other callbacks.

It functions like [`imported()`][sonolus.script.archetype.imported] and shares the same underlying storage, except that it is not loaded from a level.

### Entity Memory
Entity memory fields are declared with [`entity_memory()`][sonolus.script.archetype.entity_memory]:

```python
from sonolus.script.archetype import PlayArchetype, entity_memory

class MyArchetype(PlayArchetype):
    field: int = entity_memory()
```

Entity memory is private to the entity and is not accessible from other entities. It may be read or updated in any callback associated with the entity.

Entity memory fields may also be set when an entity is spawned using the [`spawn()`][sonolus.script.archetype.PlayArchetype.spawn] method.

### Shared Memory
Shared memory fields are declared with [`shared_memory()`][sonolus.script.archetype.shared_memory]:

```python
from sonolus.script.archetype import PlayArchetype, shared_memory

class MyArchetype(PlayArchetype):
    field: int = shared_memory()
```

Shared memory is accessible from other entities.

Shared memory may be read in any callback, but may only be updated by sequential callbacks (`preprocess`, `update_sequential`, and `touch`).

## Streams
Streams are defined with the [`@streams`][sonolus.script.stream.streams] decorator:

```python
from sonolus.script.stream import streams, Stream, StreamGroup
from sonolus.script.num import Num
from sonolus.script.vec import Vec2

@streams
class Streams:
    stream_1: Stream[Num]  # A stream of Num values
    stream_2: Stream[Vec2]  # A stream of Vec2 values
    group_1: StreamGroup[Num, 10]  # A group of 10 Num streams
    group_2: StreamGroup[Vec2, 5]  # A group of 5 Vec2 streams
    
    data_field_1: Num  # A data field of type Num
    data_field_2: Vec2  # A data field of type Vec2
```
    
Streams and stream groups are declared by annotating class attributes with [`Stream`][sonolus.script.stream.Stream] or [`StreamGroup`][sonolus.script.stream.StreamGroup].

Other types are also supported in the form of data fields. They may be used to store additional data to export from
Play to Watch mode.

In either case, data is write-only in Play mode and read-only in Watch mode.

This should only be used once in most projects, as multiple decorated classes will overlap with each other and
interfere when both are used at the same time.

For backwards compatibility, new streams and stream groups should be added to the end of existing ones, and
lengths and element types of existing streams and stream groups should not be changed. Otherwise, old replays may
not work on new versions of the engine.

## Skins
Skins are defined with the [`@skin`][sonolus.script.sprite.skin] decorator:

```python
from sonolus.script.sprite import skin, StandardSprite, sprite, Sprite, RenderMode


@skin
class Skin:
    render_mode: RenderMode = RenderMode.DEFAULT

    note: StandardSprite.NOTE_HEAD_RED
    other: Sprite = sprite("other")
```

Standard sprites are defined by annotating the field with the corresponding value from [`StandardSprite`][sonolus.script.sprite.StandardSprite].

Custom sprites are defined by annotating the field with [`Sprite`][sonolus.script.sprite.Sprite] and calling [`sprite`][sonolus.script.sprite.sprite] with the sprite name.

To set the render mode for the skin, set the `render_mode` field to the desired value from [`RenderMode`][sonolus.script.sprite.RenderMode].

## Sound Effects
Sound effects are defined with the [`@effects`][sonolus.script.effect.effects] decorator:

```python
from sonolus.script.effect import effects, StandardEffect, Effect, effect


@effects
class Effects:
    tap_perfect: StandardEffect.PERFECT
    other: Effect = effect("other")
```

Standard sound effects are defined by annotating the field with the corresponding value from [`StandardEffect`][sonolus.script.effect.StandardEffect].

Custom sound effects are defined by annotating the field with [`Effect`][sonolus.script.effect.Effect] and calling [`effect`][sonolus.script.effect.effect] with the effect name.

## Particles
Particles are defined with the [`@particles`][sonolus.script.particle.particles] decorator:

```python
from sonolus.script.particle import particles, StandardParticle, Particle, particle


@particles
class Particles:
    tap: StandardParticle.NOTE_CIRCULAR_TAP_RED
    other: Particle = particle("other")
```

Standard particles are defined by annotating the field with the corresponding value from [`StandardParticle`][sonolus.script.particle.StandardParticle].

Custom particles are defined by annotating the field with [`Particle`][sonolus.script.particle.Particle] and calling [`particle`][sonolus.script.particle.particle] with the particle name.

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

Buckets are defined by annotating the field with [`Bucket`][sonolus.script.bucket.Bucket] and calling [`bucket`][sonolus.script.bucket.bucket] with the bucket name.

## Tutorial Instructions
Tutorial instructions are defined with the [`@instructions`][sonolus.script.instruction.instructions] decorator:

```python
from sonolus.script.instruction import instructions, StandardInstruction, Instruction, instruction


@instructions
class Instructions:
    tap: StandardInstruction.TAP
    other: Instruction = instruction("other")
```

Standard instructions are defined by annotating the field with the corresponding value from [`StandardInstruction`][sonolus.script.instruction.StandardInstruction].

Custom instructions are defined by annotating the field with [`Instruction`][sonolus.script.instruction.Instruction] and calling [`instruction`][sonolus.script.instruction.instruction] with the instruction name.

## Tutorial Instruction Icons
Tutorial instruction icons are defined with the [`@instruction_icons`][sonolus.script.instruction.instruction_icons] decorator:

```python
from sonolus.script.instruction import instruction_icons, StandardInstructionIcon, InstructionIcon, instruction_icon


@instruction_icons
class InstructionIcons:
    hand: StandardInstructionIcon.HAND
    other: InstructionIcon = instruction_icon("other")
```

Standard instruction icons are defined by annotating the field with the corresponding value from [`StandardInstructionIcon`][sonolus.script.instruction.StandardInstructionIcon].

Custom instruction icons are defined by annotating the field with [`InstructionIcon`][sonolus.script.instruction.InstructionIcon] and calling [`instruction_icon`][sonolus.script.instruction.instruction_icon] with the icon name.

## Options
Engine options are defined with the [`@options`][sonolus.script.options.options] decorator:

```python
from sonolus.script.options import options, select_option, slider_option, toggle_option


@options
class Options:
    slider_option: float = slider_option(
        name="Slider Option",
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
        standard=True,
        advanced=False,
        default=True,
        scope="scope",
    )
    select_option: int = select_option(
        name="Select Option",
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

## UI
Ui configuration is defined with the [`UiConfig`][sonolus.script.ui.UiConfig] class:

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
