from __future__ import annotations

from typing import cast

from pydori.lib.layout import get_note_y, preempt_time
from pydori.lib.note import (
    NoteKind,
    destroy_particle,
    draw_note,
    draw_note_head,
    get_note_bucket,
    init_note_life,
    play_note_particle,
    schedule_hold_sfx,
    schedule_note_sfx,
    update_hold_particle,
)
from pydori.lib.options import Options
from pydori.lib.streams import Streams
from sonolus.script.archetype import (
    EntityRef,
    StandardImport,
    WatchArchetype,
    entity_data,
    entity_memory,
    imported,
    shared_memory,
)
from sonolus.script.bucket import Judgment
from sonolus.script.particle import ParticleHandle
from sonolus.script.runtime import is_replay, is_skip, scaled_time, time
from sonolus.script.timing import beat_to_time, time_to_scaled_time


class WatchNote(WatchArchetype):
    """Common archetype for notes."""

    lane: float = imported()
    beat: StandardImport.BEAT = imported()
    direction: int = imported()
    prev_ref: EntityRef[WatchNote] = imported()
    next_ref: EntityRef[WatchNote] = imported()

    target_time: float = entity_data()
    target_scaled_time: float = entity_data()
    start_scaled_time: float = entity_data()
    end_scaled_time: float = entity_data()
    head_ref: EntityRef[WatchNote] = entity_data()
    end_ref: EntityRef[WatchNote] = entity_data()

    _hold_lane: float = shared_memory()

    end_time: float = imported()
    judgment: StandardImport.JUDGMENT = imported()
    accuracy: StandardImport.ACCURACY = imported()

    def preprocess(self):
        if Options.mirror:
            self.lane = -self.lane
            self.direction = -self.direction

        self.target_time = beat_to_time(self.beat)
        self.target_scaled_time = time_to_scaled_time(self.target_time)
        self.start_scaled_time = self.target_scaled_time - preempt_time()
        self.end_scaled_time = time_to_scaled_time(self.end_time)
        self.result.bucket = get_note_bucket(self.kind)

        self.result.target_time = self.target_time

        self.head_ref = self.ref()
        while self.head.has_prev:
            self.head_ref = self.head.prev_ref

        self.end_ref = self.ref()
        while self.end.has_next:
            self.end_ref = self.end.next_ref

        if is_replay():
            if self.judgment != Judgment.MISS:
                schedule_note_sfx(self.kind, self.judgment, self.end_time)
            self.result.bucket_value = self.accuracy * 1000
        else:
            self.judgment = Judgment.PERFECT
            schedule_note_sfx(self.kind, Judgment.PERFECT, self.target_time)

        if self.has_prev and not self.has_next:
            WatchHoldManager.spawn(head_ref=self.head_ref, end_ref=self.end_ref)
            self.schedule_hold_sfx()

    def spawn_time(self) -> float:
        return self.start_scaled_time

    def despawn_time(self) -> float:
        if is_replay():
            return self.end_scaled_time
        else:
            return self.target_scaled_time

    def update_parallel(self):
        draw_note(
            self.kind,
            self.lane,
            self.y,
            self.direction,
        )

    def terminate(self):
        if is_skip():
            return
        if self.judgment != Judgment.MISS:
            play_note_particle(self.kind, self.lane, self.direction)

    def schedule_hold_sfx(self):
        if is_replay():
            was_active = False
            start_time = 0
            for input_time, is_active in Streams.hold_activity[self.head.index].iter_items_from(-10):
                if input_time < self.head.target_time:
                    # This is before the head meets the judge line, so it's irrelevant.
                    continue
                if input_time > self.end.target_time:
                    # This is after the end, so we can stop checking.
                    break
                if is_active and not was_active:
                    # Record the start time of a timespan where the hold is active.
                    start_time = input_time
                    was_active = True
                if not is_active and was_active:
                    # An active timespan has ended, so we can schedule the hold sfx over it.
                    schedule_hold_sfx(start_time, input_time)
                    was_active = False
            if was_active:
                # If the last recorded value was that the hold was active, we can assume it was active until the end,
                # and schedule the sfx from the current start time to the end of the hold.
                schedule_hold_sfx(start_time, self.end.target_time)
        else:
            schedule_hold_sfx(self.head.target_time, self.end.target_time)

    @classmethod
    def global_preprocess(cls):
        init_note_life(cls)

    @property
    def kind(self) -> NoteKind:
        return cast(NoteKind, self.key)

    @property
    def y(self) -> float:
        return get_note_y(scaled_time(), self.target_scaled_time)

    @property
    def has_prev(self) -> bool:
        return self.prev_ref.index > 0

    @property
    def prev(self) -> WatchNote:
        return self.prev_ref.get()

    @property
    def has_next(self) -> bool:
        return self.next_ref.index > 0

    @property
    def next(self) -> WatchNote:
        return self.next_ref.get()

    @property
    def head(self) -> WatchNote:
        return self.head_ref.get()

    @property
    def end(self) -> WatchNote:
        return self.end_ref.get()

    @property
    def has_active_touch(self) -> bool:
        if is_replay():
            return Streams.hold_activity[self.head.index].get_previous_inclusive(time())
        else:
            return True

    @property
    def hold_lane(self) -> float:
        return self.head._hold_lane

    @hold_lane.setter
    def hold_lane(self, value: float):
        self.head._hold_lane = value


WatchTapNote = WatchNote.derive("Tap", is_scored=True, key=NoteKind.TAP)
WatchFlickNote = WatchNote.derive("Flick", is_scored=True, key=NoteKind.FLICK)
WatchDirectionalFlickNote = WatchNote.derive("DirectionalFlick", is_scored=True, key=NoteKind.DIRECTIONAL_FLICK)
WatchHoldHeadNote = WatchNote.derive("HoldHead", is_scored=True, key=NoteKind.HOLD_HEAD)
WatchHoldTickNote = WatchNote.derive("HoldTick", is_scored=True, key=NoteKind.HOLD_TICK)
WatchHoldAnchorNote = WatchNote.derive("HoldAnchor", is_scored=False, key=NoteKind.HOLD_ANCHOR)
WatchHoldEndNote = WatchNote.derive("HoldEnd", is_scored=True, key=NoteKind.HOLD_END)

ALL_WATCH_NOTE_TYPES = (
    WatchTapNote,
    WatchFlickNote,
    WatchDirectionalFlickNote,
    WatchHoldHeadNote,
    WatchHoldTickNote,
    WatchHoldAnchorNote,
    WatchHoldEndNote,
)


class WatchHoldManager(WatchArchetype):
    """Manages the particle of a hold note.

    Unlike in play mode, the sfx is scheduled ahead of time so it's not handled here.
    """

    name = "HoldManager"

    head_ref: EntityRef[WatchNote] = entity_memory()
    end_ref: EntityRef[WatchNote] = entity_memory()

    particle: ParticleHandle = entity_memory()

    def spawn_time(self) -> float:
        return self.head.target_scaled_time

    def despawn_time(self) -> float:
        return self.end.target_scaled_time

    def update_parallel(self):
        if is_skip():
            destroy_particle(self.particle)
        if self.head.has_active_touch:
            draw_note_head(self.head.hold_lane)
            update_hold_particle(self.particle, self.head.hold_lane)
        else:
            destroy_particle(self.particle)

    def terminate(self):
        destroy_particle(self.particle)

    @property
    def head(self) -> WatchNote:
        return self.head_ref.get()

    @property
    def end(self) -> WatchNote:
        return self.end_ref.get()
