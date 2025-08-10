from __future__ import annotations

from typing import cast

from pydori.lib.buckets import note_judgment_window
from pydori.lib.layout import Hitbox, get_note_y, preempt_time
from pydori.lib.note import (
    NoteKind,
    destroy_particle,
    draw_note,
    draw_note_head,
    get_flick_speed_threshold,
    get_note_bucket,
    init_note_life,
    play_note_particle,
    play_note_sfx,
    schedule_note_sfx,
    stop_looped_sfx,
    update_hold_particle,
    update_hold_sfx,
)
from pydori.lib.options import Options
from pydori.lib.streams import Streams
from pydori.play.input import claim_touch, unclaimed_taps, unclaimed_touches
from sonolus.script.archetype import (
    EntityRef,
    PlayArchetype,
    StandardImport,
    callback,
    entity_data,
    entity_memory,
    exported,
    imported,
    shared_memory,
)
from sonolus.script.array import Dim
from sonolus.script.bucket import Judgment, JudgmentWindow
from sonolus.script.containers import VarArray
from sonolus.script.effect import LoopedEffectHandle
from sonolus.script.globals import level_memory
from sonolus.script.interval import Interval, clamp
from sonolus.script.particle import ParticleHandle
from sonolus.script.quad import Quad
from sonolus.script.runtime import input_offset, offset_adjusted_time, scaled_time, time, touches
from sonolus.script.timing import beat_to_time, time_to_scaled_time
from sonolus.script.vec import Vec2

DEFAULT_BEST_JUDGMENT_TIME = -1e8


class Note(PlayArchetype):
    """Common archetype for notes."""

    lane: float = imported()
    beat: StandardImport.BEAT = imported()
    direction: int = imported()
    prev_ref: EntityRef[Note] = imported()
    next_ref: EntityRef[Note] = imported()

    judgment_window: JudgmentWindow = entity_data()
    target_time: float = entity_data()
    target_scaled_time: float = entity_data()
    start_scaled_time: float = entity_data()
    input_interval: Interval = entity_data()
    head_ref: EntityRef[Note] = entity_data()
    end_ref: EntityRef[Note] = entity_data()

    best_judgment_time: float = entity_memory()

    _active_touch_id: int = shared_memory()
    _hold_lane: float = shared_memory()
    is_judged: bool = shared_memory()

    end_time: float = exported()

    def preprocess(self):
        if Options.mirror:
            self.lane = -self.lane
            self.direction = -self.direction

        self.judgment_window = note_judgment_window
        self.target_time = beat_to_time(self.beat)
        self.target_scaled_time = time_to_scaled_time(self.target_time)
        self.start_scaled_time = self.target_scaled_time - preempt_time()
        self.input_interval = self.judgment_window.good + self.target_time + input_offset()
        self.result.bucket = get_note_bucket(self.kind)
        self.result.accuracy = 1.0

        self.best_judgment_time = DEFAULT_BEST_JUDGMENT_TIME

        self.head_ref = self.ref()
        while self.head.has_prev:
            self.head_ref = self.head.prev_ref

        self.end_ref = self.ref()
        while self.end.has_next:
            self.end_ref = self.end.next_ref

        if Options.auto_sfx_enabled:
            schedule_note_sfx(self.kind, Judgment.PERFECT, self.target_time)

    def should_spawn(self) -> bool:
        return scaled_time() >= self.start_scaled_time

    def spawn_order(self) -> float:
        return self.start_scaled_time

    def initialize(self):
        if self.has_next and not self.has_prev:
            Streams.hold_activity[self.head.index][-10] = False
            HoldManager.spawn(head_ref=self.head_ref, end_ref=self.end_ref)

    def update_sequential(self):
        if self.kind == NoteKind.HOLD_ANCHOR:
            self.despawn = True
            return
        if time() > self.input_interval.end:
            self.despawn = True
            return
        if time() in self.input_interval:
            NoteMemory.active_notes.append(self.ref())
        if self.best_judgment_time > DEFAULT_BEST_JUDGMENT_TIME:
            # For holds ticks and flicks, we wait until it's impossible to improve the judgment before judging.
            # E.g. the player might be within a hold tick's hitbox at the early good window, move their finger away,
            # then move it back inside within the perfect window. It would be unfair to judge the note immediately
            # when the player moved their finger away, so we wait until it would be impossible to improve the judgment
            # before judging.
            can_improve = (
                self.best_judgment_time < self.target_time
                and offset_adjusted_time() - self.target_time < self.target_time - self.best_judgment_time
            )
            if not can_improve:
                if self.target_time - 1 / 30 <= self.best_judgment_time <= self.target_time:
                    # If the best judgment time is just before the target time, we assume that they player has been
                    # continuous holding the tick or flicking, and we treat it as having perfect timing.
                    self.best_judgment_time = self.target_time
                self.judge(self.best_judgment_time)
        # Compared to the original Bandori mechanics, pydori is more lenient with releasing hold notes.
        # Releasing a hold is no longer an automatic miss, so accidentally releasing a hold note early is okay
        # as long as the player puts their finger back down before the next tick or the hold end.
        # We still keep track of a single active touch so players can't cheat using multiple fingers on the same hold.
        if self.has_active_touch:
            claim_touch(self.active_touch_id)

    def update_parallel(self):
        if self.despawn:
            return
        draw_note(
            self.kind,
            self.lane,
            self.y,
            self.direction,
        )

    def touch(self):
        if self.despawn:
            return
        hitbox_quad = self.calculate_hitbox().layout()
        match self.kind:
            case NoteKind.TAP | NoteKind.HOLD_HEAD:
                self.handle_tap_input(hitbox_quad)
            case NoteKind.HOLD_TICK:
                self.handle_hold_input(hitbox_quad)
            case NoteKind.HOLD_END:
                self.handle_release_input(hitbox_quad)
            case NoteKind.FLICK | NoteKind.DIRECTIONAL_FLICK:
                self.handle_flick_input(hitbox_quad)

    def handle_tap_input(self, quad: Quad):
        if time() not in self.input_interval:
            return
        for touch in unclaimed_taps():
            if not quad.contains_point(touch.position):
                continue
            claim_touch(touch.id)
            self.active_touch_id = touch.id
            self.judge(touch.start_time)
            break

    def handle_hold_input(self, quad: Quad):
        if self.has_prev and not (self.head.is_judged or self.head.is_despawned):
            # If the hold head is still around, require players to tap the head to start the hold.
            return
        if time() not in self.input_interval:
            return
        self.capture_touch_if_needed(quad)
        if self.has_active_touch:
            for touch in touches():
                if touch.id != self.active_touch_id:
                    continue
                if quad.contains_point(touch.position):
                    self.update_best_judgment_time_with_current_time()
                if self.best_judgment_time >= self.target_time:
                    self.judge(self.best_judgment_time)
                break

    def handle_release_input(self, quad: Quad):
        if self.has_prev and not (self.head.is_judged or self.head.is_despawned):
            # If the hold head is still around, require players to tap the head to start the hold.
            return
        if time() not in self.input_interval:
            return
        self.capture_touch_if_needed(quad)
        if self.has_active_touch:
            for touch in touches():
                if touch.id != self.active_touch_id:
                    continue
                if touch.ended:
                    if quad.contains_point(touch.position):
                        self.judge(offset_adjusted_time())
                    else:
                        self.fail()
                break

    def handle_flick_input(self, quad: Quad):
        if self.has_prev and not (self.head.is_judged or self.head.is_despawned):
            # If the hold head is still around, require players to tap the head to start the hold.
            return
        if time() not in self.input_interval:
            return
        if self.has_prev:
            # If this is a hold-flick, then we don't require a new tap to start the flick.
            self.capture_touch_if_needed(quad)
        else:
            # Regular flicks require a tap before flicking.
            self.capture_tap_if_needed(quad)
        if self.has_active_touch:
            for touch in touches():
                if touch.id != self.active_touch_id:
                    continue
                meets_speed = touch.speed >= get_flick_speed_threshold(self.direction)
                meets_direction = self.direction == 0 or touch.velocity.dot(Vec2(self.direction, 0)) > 0
                if quad.contains_point(touch.position) and meets_speed and meets_direction:
                    self.update_best_judgment_time_with_current_time()
                if touch.ended or self.best_judgment_time >= self.target_time:
                    self.judge(self.best_judgment_time)
                break

    def capture_touch_if_needed(self, quad: Quad):
        """If there is no active touch, try to claim a touch that is inside the quad and set it as the active touch."""
        if not self.has_active_touch:
            for touch in unclaimed_touches():
                if not quad.contains_point(touch.position):
                    continue
                if touch.ended:
                    continue
                claim_touch(touch.id)
                self.active_touch_id = touch.id
                break

    def capture_tap_if_needed(self, quad: Quad):
        """If there is no active touch, try to claim a tap that is inside the quad and set it as the active touch."""
        if not self.has_active_touch:
            for touch in unclaimed_taps():
                if not quad.contains_point(touch.position):
                    continue
                claim_touch(touch.id)
                self.active_touch_id = touch.id
                break

    def update_best_judgment_time_with_current_time(self):
        prev_error = abs(self.best_judgment_time - self.target_time)
        new_error = abs(offset_adjusted_time() - self.target_time)
        if new_error < prev_error:
            self.best_judgment_time = offset_adjusted_time()

    def calculate_hitbox(self) -> Hitbox:
        base_hitbox = self.base_hitbox
        right_overlap = 0
        left_overlap = 0
        other_notes = (ref.get() for ref in NoteMemory.active_notes if ref.index != self.index)
        simultaneous_notes = (
            note
            for note in other_notes
            if not note.is_judged and abs(note.target_time - self.target_time) <= 0.005 and not note.has_active_touch
        )
        for sim_note in simultaneous_notes:
            sim_hitbox = sim_note.base_hitbox
            if sim_note.lane > self.lane:
                # The overlap between the hitboxes is how much the right side of the base hitbox
                # extends beyond the left side of the sim note's hitbox.
                right_overlap = max(right_overlap, base_hitbox.right - sim_hitbox.left)
            elif sim_note.lane < self.lane:
                # The same logic the other way around for the left side.
                left_overlap = max(left_overlap, sim_hitbox.right - base_hitbox.left)
        # Shrink the base hitbox by half the overlap on each side.
        return Hitbox(left=base_hitbox.left + left_overlap / 2, right=base_hitbox.right - right_overlap / 2)

    def terminate(self):
        self.end_time = time()

    def judge(self, judgment_time: float):
        judgment = self.judgment_window.judge(actual=judgment_time, target=self.target_time)
        self.result.judgment = judgment
        self.result.accuracy = clamp(judgment_time - self.target_time, -1.0, 1.0)
        self.result.bucket_value = self.result.accuracy * 1000
        if judgment != Judgment.MISS:
            if not Options.auto_sfx_enabled:
                play_note_sfx(self.kind, judgment)
            play_note_particle(self.kind, self.lane, self.direction)
        self.despawn = True
        self.is_judged = True

    def fail(self):
        self.result.judgment = Judgment.MISS
        self.result.accuracy = 1.0
        self.result.bucket_value = self.result.accuracy * 1000
        self.despawn = True
        self.is_judged = True

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
    def prev(self) -> Note:
        return self.prev_ref.get()

    @property
    def has_next(self) -> bool:
        return self.next_ref.index > 0

    @property
    def next(self) -> Note:
        return self.next_ref.get()

    @property
    def head(self) -> Note:
        return self.head_ref.get()

    @property
    def end(self) -> Note:
        return self.end_ref.get()

    @property
    def active_touch_id(self) -> int:
        return self.head._active_touch_id

    @active_touch_id.setter
    def active_touch_id(self, value: int):
        self.head._active_touch_id = value

    @property
    def has_active_touch(self) -> bool:
        return self.active_touch_id > 0

    @property
    def hold_lane(self) -> float:
        return self.head._hold_lane

    @hold_lane.setter
    def hold_lane(self, value: float):
        self.head._hold_lane = value

    @property
    def base_hitbox(self) -> Hitbox:
        return Hitbox.for_note(self.lane, self.direction)


TapNote = Note.derive("Tap", is_scored=True, key=NoteKind.TAP)
FlickNote = Note.derive("Flick", is_scored=True, key=NoteKind.FLICK)
DirectionalFlickNote = Note.derive("DirectionalFlick", is_scored=True, key=NoteKind.DIRECTIONAL_FLICK)
HoldHeadNote = Note.derive("HoldHead", is_scored=True, key=NoteKind.HOLD_HEAD)
HoldTickNote = Note.derive("HoldTick", is_scored=True, key=NoteKind.HOLD_TICK)
HoldAnchorNote = Note.derive("HoldAnchor", is_scored=False, key=NoteKind.HOLD_ANCHOR)
HoldEndNote = Note.derive("HoldEnd", is_scored=True, key=NoteKind.HOLD_END)

ALL_NOTE_TYPES = (
    TapNote,
    FlickNote,
    DirectionalFlickNote,
    HoldHeadNote,
    HoldTickNote,
    HoldAnchorNote,
    HoldEndNote,
)


class HoldManager(PlayArchetype):
    """Manages the particle and looping sfx of a hold note."""

    name = "HoldManager"

    head_ref: EntityRef[Note] = entity_memory()
    end_ref: EntityRef[Note] = entity_memory()

    particle: ParticleHandle = entity_memory()
    sfx: LoopedEffectHandle = entity_memory()

    def update_parallel(self):
        if time() > self.end.target_time:
            destroy_particle(self.particle)
            stop_looped_sfx(self.sfx)
            self.despawn = True
            return
        if time() < self.head.target_time:
            return
        Streams.hold_activity[self.head.index][time()] = self.head.has_active_touch
        if self.head.has_active_touch:
            draw_note_head(self.head.hold_lane)
            update_hold_particle(self.particle, self.head.hold_lane)
            update_hold_sfx(self.sfx)
        else:
            destroy_particle(self.particle)
            stop_looped_sfx(self.sfx)

    @callback(order=1)
    def touch(self):
        if not self.head.has_active_touch:
            return
        active_touch_id = self.head.active_touch_id
        for touch in touches():
            if touch.id != active_touch_id:
                continue
            if touch.ended:
                self.head.active_touch_id = 0
            break

    @property
    def head(self) -> Note:
        return self.head_ref.get()

    @property
    def end(self) -> Note:
        return self.end_ref.get()


@level_memory
class NoteMemory:
    active_notes: VarArray[EntityRef[Note], Dim[16]]
