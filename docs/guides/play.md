# Play

Play mode is generally the most important mode of a Sonolus engine, as it defines the interactive gameplay experience.

## Configuration

Play mode is configured using the [`PlayMode`][sonolus.script.engine.PlayMode] class:

```python
from sonolus.script.engine import PlayMode

# ... import your archetypes, skin, effects, particles, and buckets

play_mode = PlayMode(
    archetypes=[
        Stage,
        Note,
        # etc.
    ],
    skin=Skin,
    effects=Effects,
    particles=Particles,
    buckets=Buckets,
)
```

## Play Archetypes

Gameplay logic in play mode is defined via archetypes. Archetypes may correspond to individual notes, or other gameplay
elements such as the stage. They may also be dedicated to special purposes such as initializing, input handing, and
so forth depending on the needs of an engine.

In play mode, archetypes inherit from the [`PlayArchetype`][sonolus.script.archetype.PlayArchetype] class:

```python
from sonolus.script.archetype import PlayArchetype


class Note(PlayArchetype):
    is_score = True

    # All of these methods are optional

    def preprocess(self):
        ...

    def spawn_order(self) -> float:
        ...

    def should_spawn(self) -> bool:
        ...

    def initialize(self):
        ...

    def update_sequential(self):
        ...

    def touch(self):
        ...

    def update_parallel(self):
        ...

    def terminate(self):
        ...

```

Archetypes that correspond to notes and contribute to scoring should set the `is_score` attribute to `True`.

### Entities

Entities are instances of archetypes. For example an engine may have a `Note` archetype, and each individual note is
considered an entity.

### Callbacks

Callbacks determine the behavior of entities in play mode:

- [`preprocess`][sonolus.script.archetype.PlayArchetype.preprocess]:
  Called as the level is loaded
- [`spawn_order`][sonolus.script.archetype.PlayArchetype.spawn_order]:
  Called after preprocessing is done to determine which order entities. should be spawned in.
  Smaller values are spawned first; a common approach is to use the spawn time of the entity.
- [`should_spawn`][sonolus.script.archetype.PlayArchetype.should_spawn]:
  Called to determine whether the entity should be spawned. Called each frame if the previous entity is spawned.
- [`initialize`][sonolus.script.archetype.PlayArchetype.initialize]:
  Called when the entity is spawned at the start of the frame. Runs in parallel with other `initialize` calls.
- [`update_sequential`][sonolus.script.archetype.PlayArchetype.update_sequential]:
  Called each frame an entity is active after `initialize` callbacks are done.
  Since it's called sequentially, it can update shared state.
- [`touch`][sonolus.script.archetype.PlayArchetype.touch]:
  Called sequentially each frame after `update_sequential` if there's touch input. Has access to touch input data.
- [`update_parallel`][sonolus.script.archetype.PlayArchetype.update_parallel]:
  Called after `touch` and `update_sequential` callbacks are done. Runs in parallel with other `update_parallel` calls.
  Has better performance due to parallel execution, so most logic such as drawing sprites should be done here.
- [`terminate`][sonolus.script.archetype.PlayArchetype.terminate]:
  Called after `update_parallel` callbacks are done when an entity is being despawned.
  Runs in parallel with other `terminate` calls.

If not defined in an archetype, the default behavior of each callback is to do nothing.
