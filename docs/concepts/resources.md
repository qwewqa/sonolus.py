# Resources

## Skins
Skins are defined with the `@skin` decorator:

```python
from sonolus.script.sprite import skin, StandardSprite, sprite, Sprite, RenderMode


@skin
class Skin:
    render_mode: RenderMode = RenderMode.DEFAULT

    note: StandardSprite.NOTE_HEAD_RED
    other: Sprite = sprite("other")
```

Standard sprites are defined by annotating the field with the corresponding value from `StandardSprite`.

Custom sprites are defined by annotating the field with `Sprite` and calling `skin_sprite` with the sprite name.

To set the render mode for the skin, set the `render_mode` field to the desired value from `RenderMode`.

## Sound Effects
Sound effects are defined with the `@effects` decorator:

```python
from sonolus.script.effect import effects, StandardEffect, Effect, effect


@effects
class Effects:
    tap_perfect: StandardEffect.PERFECT
    other: Effect = effect("other")
```

Standard sound effects are defined by annotating the field with the corresponding value from `StandardEffect`.

Custom sound effects are defined by annotating the field with `Effect` and calling `effect` with the effect name.

## Particles
Particles are defined with the `@particles` decorator:

```python
from sonolus.script.particle import particles, StandardParticle, Particle, particle


@particles
class Particles:
    tap: StandardParticle.NOTE_CIRCULAR_TAP_RED
    other: Particle = particle("other")
```

Standard particles are defined by annotating the field with the corresponding value from `StandardParticle`.

Custom particles are defined by annotating the field with `Particle` and calling `particle` with the particle name.

## Buckets
Buckets are defined with the `@buckets` decorator:

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

Buckets are defined by annotating the field with `Bucket` and calling `bucket` with the bucket name.

## Tutorial Instructions
Tutorial instructions are defined with the `@instructions` decorator:

```python
from sonolus.script.instruction import instructions, StandardInstruction, Instruction, instruction


@instructions
class Instructions:
    tap: StandardInstruction.TAP
    other: Instruction = instruction("other")
```

Standard instructions are defined by annotating the field with the corresponding value from `StandardInstruction`.

Custom instructions are defined by annotating the field with `Instruction` and calling `instruction` with the instruction name.

## Tutorial Instruction Icons
Tutorial instruction icons are defined with the `@instruction_icons` decorator:

```python
from sonolus.script.instruction import instruction_icons, StandardInstructionIcon, InstructionIcon, instruction_icon


@instruction_icons
class InstructionIcons:
    hand: StandardInstructionIcon.HAND
    other: InstructionIcon = instruction_icon("other")
```

Standard instruction icons are defined by annotating the field with the corresponding value from `StandardInstructionIcon`.

Custom instruction icons are defined by annotating the field with `InstructionIcon` and calling `instruction_icon` with the icon name.

## Options
Engine options are defined with the `@options` decorator:

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
1. `slider_option`: A slider control for numeric values
2. `toggle_option`: A toggle switch for boolean values
3. `select_option`: A dropdown menu for selecting from predefined values

## UI
Ui configuration is defined with the `UiConfig` class:

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
    judgment_error_style=UiJudgmentErrorStyle.NONE,
    judgment_error_placement=UiJudgmentErrorPlacement.BOTH,
    judgment_error_min=0.0,
)
```
