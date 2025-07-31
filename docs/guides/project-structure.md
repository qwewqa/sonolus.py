# Project Structure

Typically, a Sonolus.py project will have something resembling the following structure:

```
<project_name>/
├── lib/          # Common code shared between modes
├── play/         # Code for play mode
├── watch/        # Code for watch mode
├── preview/      # Code for preview mode
├── tutorial/     # Code for tutorial mode
├── .../           # Additional code e.g. for converting charts from other formats
├── project.py    # Project configuration
└── level.py      # Level configuration (for development server levels)
resources/        # Resources for the engine
```

When starting a new project, you'll probably want to use the
[template project](https://github.com/qwewqa/sonolus.py-template-project) to initialize your project structure.

## Resources

The `resources/` directory contains the resources used by the engine, and its contents are used by the development
server. It supports resource source code (like the contents of
[sonolus-free-pack](https://github.com/Sonolus/sonolus-free-pack)) sonolus collection (`.scp`) files, and raw
image and audio files.

## Project Configuration

By default, Sonolus.py searches for a module named `project` in the root package of the project (i.e. the
`<project_name>/project.py` file). This module should contain the project configuration and typically should
contain the engine configuration as well. For example, pydori's project.py contains the following:

??? note "[pydori/project.py](https://github.com/qwewqa/pydori/blob/master/pydori/project.py)"
    ```python
    from sonolus.script.engine import Engine, EngineData
    from sonolus.script.project import Project
    
    from pydori.level import load_levels
    from pydori.lib.options import Options
    from pydori.lib.ui import ui_config
    from pydori.play.mode import play_mode
    from pydori.preview.mode import preview_mode
    from pydori.tutorial.mode import tutorial_mode
    from pydori.watch.mode import watch_mode
    
    engine = Engine(
        name="pydori",
        title="pydori",
        skin="pixel",
        particle="pixel",
        background="darkblue",
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
        levels=load_levels,
    )
    ```

This file defines the name and title of the engine, some defaults for players, and links to the various modes as
well as the ui and option configurations. It also links to the levels to load for the development server.
