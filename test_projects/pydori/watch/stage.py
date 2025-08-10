from pydori.lib.buckets import init_buckets, init_score
from pydori.lib.layout import init_layout
from pydori.lib.stage import (
    draw_stage,
    init_stage_data,
    play_lane_particle,
    schedule_lane_sfx,
)
from pydori.lib.streams import Streams
from pydori.lib.ui import init_ui
from pydori.watch.note import ALL_WATCH_NOTE_TYPES
from sonolus.script.archetype import WatchArchetype, entity_memory
from sonolus.script.runtime import delta_time, is_replay, time
from sonolus.script.timing import time_to_scaled_time


class WatchStage(WatchArchetype):
    """Draws the stage and performs other global game functions."""

    name = "Stage"

    def preprocess(self):
        init_buckets()
        init_score()
        init_ui()
        init_layout()
        init_stage_data()
        for note_type in ALL_WATCH_NOTE_TYPES:
            note_type.global_preprocess()
        self.schedule_effects()

    def spawn_time(self) -> float:
        return -1e8

    def despawn_time(self) -> float:
        return 1e8

    def update_parallel(self):
        draw_stage()

    @staticmethod
    def schedule_effects():
        """Schedule lane sound effects upfront."""
        if not is_replay():
            return
        for effect_time, lanes in Streams.effect_lanes.iter_items_from(-10):
            schedule_lane_sfx(effect_time)
            for lane in lanes:
                WatchScheduledLaneEffect.spawn(time=effect_time, lane=lane)


class WatchScheduledLaneEffect(WatchArchetype):
    """Plays a lane particle effect at a scheduled time."""

    name = "ScheduledLaneEffect"

    time: float = entity_memory()
    lane: float = entity_memory()

    def spawn_time(self) -> float:
        return time_to_scaled_time(self.time)

    def despawn_time(self) -> float:
        return self.spawn_time() + 1

    def update_parallel(self):
        if time() - delta_time() < self.time <= time():
            play_lane_particle(self.lane)
