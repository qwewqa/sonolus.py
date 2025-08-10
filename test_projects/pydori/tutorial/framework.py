from __future__ import annotations

from pydori.lib.layout import Layout
from pydori.lib.note import destroy_particle, stop_looped_sfx
from sonolus.script.effect import LoopedEffectHandle
from sonolus.script.globals import level_memory
from sonolus.script.instruction import clear_instruction
from sonolus.script.interval import lerp, remap, remap_clamped
from sonolus.script.particle import ParticleHandle
from sonolus.script.record import Record
from sonolus.script.runtime import time


@level_memory
class PhaseState:
    start_time: float
    hold_particle: ParticleHandle
    hold_sfx: LoopedEffectHandle
    hold_was_accessed: bool
    prev_time: float


def update_start():
    """Update tutorial state at the beginning of each frame."""
    PhaseState.hold_was_accessed = False
    clear_instruction()


def update_end():
    """Update tutorial state at the end of each frame."""
    if not PhaseState.hold_was_accessed:
        destroy_particle(PhaseState.hold_particle)
        stop_looped_sfx(PhaseState.hold_sfx)
    PhaseState.prev_time = time()


def reset_phase():
    """Reset the tutorial phase time to zero."""
    PhaseState.start_time = time()


def get_hold_particle() -> ParticleHandle:
    """Mark that the hold particle was accessed and return it."""
    PhaseState.hold_was_accessed = True
    return PhaseState.hold_particle


def get_hold_sfx() -> LoopedEffectHandle:
    """Mark that the hold sound effect was accessed and return it."""
    PhaseState.hold_was_accessed = True
    return PhaseState.hold_sfx


def current_phase_time() -> PhaseTime:
    """Return the current phase timing information."""
    return PhaseTime(time() - PhaseState.start_time, PhaseState.prev_time - PhaseState.start_time)


class PhaseTime(Record):
    time: float
    prev_time: float

    def range(self, start: float, end: float, segments: int = 1) -> PhaseRange:
        return PhaseRange(self, start, end, segments)

    def first(self, end: float, repeats: int = 1) -> PhaseRange:
        return self.range(0, end * repeats, segments=repeats)

    def instant(self, timing: float) -> PhaseInstant:
        return PhaseInstant(self, timing)


class PhaseRange(Record):
    phase: PhaseTime
    start: float
    end: float
    segments: int

    @property
    def is_active(self):
        return self.start <= self.phase.time < self.end

    @property
    def progress(self) -> float:
        if self.segments == 1:
            return remap(self.start, self.end, 0, 1, self.phase.time)
        else:
            return remap_clamped(self.start, self.end, 0, 1, self.phase.time) * self.segments % 1

    @property
    def is_done(self):
        return self.phase.time >= self.end

    def next(self, duration: float, repeats: int = 1):
        return self.phase.range(self.end, self.end + duration * repeats, segments=repeats)

    def start_instant(self) -> PhaseInstant:
        return self.phase.instant(self.start)

    def end_instant(self) -> PhaseInstant:
        return self.phase.instant(self.end)

    def __bool__(self):
        return self.is_active


class PhaseInstant(Record):
    phase: PhaseTime
    timing: float

    @property
    def is_active(self):
        return self.phase.prev_time < self.timing <= self.phase.time

    @property
    def is_done(self):
        return self.phase.time >= self.timing and not self.is_active

    @property
    def is_upcoming(self):
        return self.phase.time < self.timing

    def __bool__(self):
        return self.is_active


def progress_to_y(p: float) -> float:
    """Interpolate a 0-1 progress value to a y-max to 0 y-coordinate.

    Progresses less than 0 and greater than 1 will result in y-coordinates
    above y-max or below 0, respectively.

    Args:
        p: A progress value where 1 is when the note meets the judgment line.

    Returns:
        The y-coordinate value that corresponds to the progress.
    """
    return lerp(Layout.note_y_max, 0, p)
