from pydori.lib.buckets import Buckets
from pydori.lib.effect import Effects
from pydori.lib.particle import Particles
from pydori.lib.skin import Skin
from pydori.watch.connector import WatchHoldConnector, WatchSimLine
from pydori.watch.event import WatchBpmChange, WatchTimescaleChange
from pydori.watch.note import ALL_WATCH_NOTE_TYPES, WatchHoldManager
from pydori.watch.stage import WatchScheduledLaneEffect, WatchStage
from pydori.watch.update_spawn import update_spawn
from sonolus.script.engine import WatchMode

watch_mode = WatchMode(
    archetypes=[
        WatchStage,
        WatchScheduledLaneEffect,
        *ALL_WATCH_NOTE_TYPES,
        WatchHoldManager,
        WatchHoldConnector,
        WatchSimLine,
        WatchBpmChange,
        WatchTimescaleChange,
    ],
    skin=Skin,
    effects=Effects,
    particles=Particles,
    buckets=Buckets,
    update_spawn=update_spawn,
)
