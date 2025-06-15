# Project
Project details are defined in a file called `project.py` in the top-level package of the project:

```python
from sonolus.script.engine import Engine, EngineData
from sonolus.script.project import Project

from my_engine.common.options import Options
from my_engine.common.ui import ui_config
from my_engine.level import my_level
from my_engine.play.mode import play_mode
from my_engine.preview.mode import preview_mode
from my_engine.tutorial.mode import tutorial_mode
from my_engine.watch.mode import watch_mode

engine = Engine(
    name="my_engine",
    title="Demo Engine",
    skin="pixel",
    particle="pixel",
    background="vanilla",
    data=EngineData(
        ui=ui_config,
        options=Options,
        play=play_mode,
        watch=watch_mode,
        preview=preview_mode,
        tutorial=tutorial_mode,
    ),
)

project = Project(
    engine=engine,
    levels=[my_level],
)
```

A typical project structure might look like this:

```
my_engine/
    __init__.py
    project.py
    common/
        ...
    play/
        ...
    preview/
        ...
    tutorial/
        ...
resources/
    ...
```

## Modes
Modes are defined using the [`PlayMode`][sonolus.script.engine.PlayMode], [`WatchMode`][sonolus.script.engine.WatchMode], [`PreviewMode`][sonolus.script.engine.PreviewMode], and [`TutorialMode`][sonolus.script.engine.TutorialMode] classes.

### Play Mode

```python
from sonolus.script.engine import PlayMode

from my_engine.common.buckets import Buckets
from my_engine.common.effect import Effects
from my_engine.play.init import Init
from my_engine.play.note import Note
from my_engine.common.particle import Particles
from my_engine.common.skin import Skin
from my_engine.play.stage import Stage


play_mode = PlayMode(
    archetypes=[Init, Stage, Note],
    skin=Skin,
    effects=Effects,
    particles=Particles,
    buckets=Buckets,
)

```

Play mode archetypes subclass [`PlayArchetype`][sonolus.script.archetype.PlayArchetype] and implement the following callbacks:

- [`should_spawn`][sonolus.script.archetype.PlayArchetype.should_spawn] (required)
- [`preprocess`][sonolus.script.archetype.PlayArchetype.preprocess]
- [`spawn_order`][sonolus.script.archetype.PlayArchetype.spawn_order]
- [`initialize`][sonolus.script.archetype.PlayArchetype.initialize]
- [`update_sequential`][sonolus.script.archetype.PlayArchetype.update_sequential]
- [`update_parallel`][sonolus.script.archetype.PlayArchetype.update_parallel]
- [`touch`][sonolus.script.archetype.PlayArchetype.touch]
- [`terminate`][sonolus.script.archetype.PlayArchetype.terminate]

Archetypes for scored notes should have the [`is_scored`][sonolus.script.archetype.PlayArchetype.is_scored] class variable set to `True`.

### Watch Mode

```python
from sonolus.script.engine import WatchMode

from my_engine.common.buckets import Buckets
from my_engine.common.effect import Effects
from my_engine.common.particle import Particles
from my_engine.common.skin import Skin
from my_engine.watch.init import Init
from my_engine.watch.note import Note
from my_engine.watch.stage import Stage
from my_engine.watch.update_spawn import update_spawn

watch_mode = WatchMode(
    archetypes=[Init, Stage, Note],
    skin=Skin,
    effects=Effects,
    particles=Particles,
    buckets=Buckets,
    update_spawn=update_spawn,
)
```

Watch mode archetypes subclass [`WatchArchetype`][sonolus.script.archetype.WatchArchetype] and implement the following callbacks:

- [`spawn_time`][sonolus.script.archetype.WatchArchetype.spawn_time] (required)
- [`despawn_time`][sonolus.script.archetype.WatchArchetype.despawn_time] (required)
- [`preprocess`][sonolus.script.archetype.WatchArchetype.preprocess]
- [`initialize`][sonolus.script.archetype.WatchArchetype.initialize]
- [`update_sequential`][sonolus.script.archetype.WatchArchetype.update_sequential]
- [`update_parallel`][sonolus.script.archetype.WatchArchetype.update_parallel]
- [`terminate`][sonolus.script.archetype.WatchArchetype.terminate]

Watch mode also has the `update_spawn` global callback, which is invoked every frame and should return the reference
time to compare against spawn and despawn times of archetypes. Typically, this can be either the current time or the
current scaled time.

### Preview Mode

```python
from sonolus.script.engine import PreviewMode

from my_engine.common.skin import Skin
from my_engine.preview.bar_line import BpmChange, TimescaleChange
from my_engine.preview.init import Init
from my_engine.preview.note import Note
from my_engine.preview.stage import Stage

preview_mode = PreviewMode(
    archetypes=[BpmChange, TimescaleChange, Init, Stage, Note],
    skin=Skin,
)
```

Preview mode archetypes subclass [`PreviewArchetype`][sonolus.script.archetype.PreviewArchetype] and implement the following callbacks:

- [`preprocess`][sonolus.script.archetype.PreviewArchetype.preprocess]
- [`render`][sonolus.script.archetype.PreviewArchetype.render]

### Tutorial Mode

```python
from sonolus.script.engine import TutorialMode

from my_engine.common.effect import Effects
from my_engine.common.particle import Particles
from my_engine.common.skin import Skin
from my_engine.tutorial.init import preprocess
from my_engine.tutorial.instructions import Instructions, InstructionIcons
from my_engine.tutorial.navigate import navigate
from my_engine.tutorial.update import update

tutorial_mode = TutorialMode(
    skin=Skin,
    effects=Effects,
    particles=Particles,
    instructions=Instructions,
    instruction_icons=InstructionIcons,
    preprocess=preprocess,
    navigate=navigate,
    update=update,
)
```

Tutorial mode does not have archetypes, but has the following global callbacks:

- `preprocess` - Invoked once before the tutorial starts
- `navigate` - Invoked when the player navigates forward or backward in the tutorial  
- `update` - Invoked every frame and should handle most of the drawing logic

## Levels
Levels are defined using the [`Level`][sonolus.script.level.Level] class:

```python
from sonolus.script.level import LevelData, BpmChange, Level, TimescaleChange

from my_engine.play.init import Init
from my_engine.play.note import Note
from my_engine.play.stage import Stage


my_level = Level(
    name="my_level",
    title="My Level",
    bgm="bgm.mp3",
    data=LevelData(
        bgm_offset=0,
        entities=[
            Init(),
            Stage(),
            BpmChange(beat=0, bpm=87),
            BpmChange(beat=2, bpm=87),
            BpmChange(beat=34, bpm=174),
            TimescaleChange(beat=298, timescale=1.5),
            TimescaleChange(beat=346, timescale=1),
            Note(beat=1),
            Note(beat=2),
            Note(beat=3),
        ],
    ),
)
```

## Resources
Resources should be placed in the `resources` directory next to the top-level package of the project.

They can be `.scp` files, regular `.mp3` or `.png` files, or be organized as unpacked Sonolus resources
(see [sonolus-pack](https://github.com/Sonolus/sonolus-pack)).
