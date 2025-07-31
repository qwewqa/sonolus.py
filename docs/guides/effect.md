# Sound Effects

[Sound effects][sonolus.script.effect.Effect] are used to play audio, typically short clips, during the course of
a level.

## Declaration

Effects are declared with the [`@effects`][sonolus.script.effect.effects] decorator. Standard Sonolus effects are
declared by using a value from [`StandardEffect`][sonolus.script.effect.StandardEffect] as the type hint.
Custom effects may also be defined using the [`Effect`][sonolus.script.effect.Effect] type hint and the
[`effect`][sonolus.script.effect.effect] function.

```python
from sonolus.script.effect import effects, effect, StandardEffect, Effect


@effects
class Effects:
    perfect: StandardEffect.PERFECT
    great: StandardEffect.GREAT

    custom_effect: Effect = effect("name_of_custom_effect")
```

## Playing an Effect

To play an effect, you can use the [`play`][sonolus.script.effect.Effect.play] method of the effect. This method
accepts an optional distance parameter to prevent the effect from playing if it was already played within the specified
time in seconds:

```python
from sonolus.script.effect import Effect

my_effect: Effect = ...
my_effect.play()
my_effect.play(distance=0.1)
```

Using a small non-zero distance is recommended as two instances of the same effect played in quick
succession can be unpleasant to hear.

## Scheduling an Effect

An effect can be scheduled to play at a specific time using the [`schedule`][sonolus.script.effect.Effect.schedule]
method:

```python
from sonolus.script.effect import Effect

my_effect: Effect = ...
my_effect.schedule(time=5.0)
my_effect.schedule(time=5.0, distance=0.1)
```

Scheduling is not suitable for real-time effects such as responses to user input and may not work if the scheduled
time is too close to the current time. Use [`play`][sonolus.script.effect.Effect.play] instead for real-time effects.

## Looping an Effect

An effect can be played in a loop using the [`loop`][sonolus.script.effect.Effect.loop] method, which returns a
[`LoopedEffectHandle`][sonolus.script.effect.LoopedEffectHandle] that can be used to stop the loop:

```python
from sonolus.script.effect import Effect, LoopedEffectHandle

my_effect: Effect = ...
loop_handle: LoopedEffectHandle = my_effect.loop()

# Later, stop the loop
loop_handle.stop()
```

Similarly, an effect can be scheduled to loop using the [`schedule_loop`][sonolus.script.effect.Effect.schedule_loop]
method:

```python
from sonolus.script.effect import Effect

my_effect: Effect = ...
my_effect.schedule_loop(start_time=3.0).stop(end_time=10.0)
```

## Checking Effect Availability

Some effects may not be available depending on which effect packs a user has selected. To check if an effect is
available, you can use the [`is_available`][sonolus.script.effect.Effect.is_available] method:

```python
from sonolus.script.effect import Effect

my_effect: Effect = ...

if my_effect.is_available():
    # The effect is available, you can use it.
    ...
else:
    # Do something else, such as using a fallback.
    ...
```
