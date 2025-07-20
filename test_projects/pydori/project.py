from pydori.level import demo_level
from pydori.lib.options import Options
from pydori.lib.ui import ui_config
from pydori.play.mode import play_mode
from pydori.preview.mode import preview_mode
from pydori.tutorial.mode import tutorial_mode
from pydori.watch.mode import watch_mode
from sonolus.script.engine import Engine, EngineData
from sonolus.script.project import Project

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
    levels=[
        demo_level(),
    ],
)
