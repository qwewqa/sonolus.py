from pydori.lib.buckets import init_buckets, init_score
from pydori.lib.layout import init_layout
from pydori.lib.stage import (
    StageData,
    draw_stage,
    init_stage_data,
    play_lane_particle,
    play_lane_sfx,
)
from pydori.lib.streams import Streams
from pydori.lib.ui import init_ui
from pydori.play.input import refresh_input_state, unclaimed_taps
from pydori.play.note import ALL_NOTE_TYPES, NoteMemory
from sonolus.script.archetype import PlayArchetype, callback
from sonolus.script.array import Dim
from sonolus.script.containers import ArraySet
from sonolus.script.runtime import time


class Stage(PlayArchetype):
    """Draws the stage and performs other global game functions."""

    name = "Stage"

    def preprocess(self):
        init_buckets()
        init_score()
        init_ui()
        init_layout()
        init_stage_data()
        for note_type in ALL_NOTE_TYPES:
            note_type.global_preprocess()

    def spawn_order(self) -> float:
        return -1e8

    def should_spawn(self) -> bool:
        return True

    @callback(order=-1)
    def update_sequential(self):
        refresh_input_state()
        NoteMemory.active_notes.clear()

    def update_parallel(self):
        draw_stage()

    @callback(order=2)
    def touch(self):
        self.handle_empty_lane_taps()

    @staticmethod
    def handle_empty_lane_taps():
        effect_lanes = ArraySet[float, Dim[16]].new()
        for tap in unclaimed_taps():
            for lane, quad in StageData.lane_layouts.items():
                if quad.contains_point(tap.position):
                    effect_lanes.add(lane)
                    play_lane_particle(lane)
                    play_lane_sfx()
        if len(effect_lanes) > 0:
            # Record this so it can be replayed in watch mode since there's no direct
            # access to touches in watch mode.
            Streams.effect_lanes[time()] = effect_lanes
