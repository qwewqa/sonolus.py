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
Modes are defined using the `PlayMode`, `WatchMode`, `PreviewMode`, and `TutorialMode` classes:

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

## Levels
Levels are defined using the `Level` class:

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
