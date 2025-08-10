from __future__ import annotations

from enum import IntEnum

from pydori.lib.buckets import Buckets
from pydori.lib.effect import Effects
from pydori.lib.layer import LAYER_ARROW, LAYER_NOTE, LAYER_NOTE_HEAD, get_z
from pydori.lib.layout import (
    Layout,
    layout_directional_flick_arrow,
    layout_flick_arrow,
    layout_note_body,
    layout_note_circular_particle,
    layout_note_linear_particle,
    note_y_to_alpha,
)
from pydori.lib.options import Options
from pydori.lib.particle import Particles
from pydori.lib.skin import Skin
from pydori.lib.stage import play_lane_particle
from sonolus.script.archetype import PlayArchetype, WatchArchetype
from sonolus.script.bucket import Bucket, Judgment
from sonolus.script.easing import ease_out_quad
from sonolus.script.effect import Effect, LoopedEffectHandle
from sonolus.script.interval import interp_clamped
from sonolus.script.particle import Particle, ParticleHandle
from sonolus.script.runtime import time
from sonolus.script.sprite import Sprite

# Time spent by the flick arrow fading in each animation cycle.
FLICK_FADE_IN_TIME = 0.1

# Time spent by the flick arrow fading out each animation cycle.
FLICK_FADE_OUT_TIME = 0.1

# Length of one cycle of the flick arrow animation.
FLICK_ARROW_PERIOD = 0.3

# Length of one cycle of the directional flick arrow animation.
DIRECTIONAL_FLICK_ARROW_PERIOD = 0.3

# Duration of note particles.
NOTE_PARTICLE_DURATION = 0.5

# Duration of active_hold_note particles.
ACTIVE_HOLD_PARTICLE_DURATION = 1.5

# Minimum speed for a flick input to be counted in terms of lane widths per second.
FLICK_SPEED_THRESHOLD = 6.0

# Minimum speed for a directional flick input to be counted in terms of lane widths per second.
# The total threshold is calculated as base + increment * abs(direction).
DIRECTIONAL_FLICK_SPEED_THRESHOLD_BASE = 2.0
DIRECTIONAL_FLICK_SPEED_THRESHOLD_INCREMENT = 2.0


class NoteKind(IntEnum):
    TAP = 1
    FLICK = 2
    DIRECTIONAL_FLICK = 3
    HOLD_HEAD = 4
    HOLD_TICK = 5
    HOLD_ANCHOR = 6  # Invisible 'ticks' for curved hold notes
    HOLD_END = 7


def get_note_body_sprite(kind: NoteKind, direction: int) -> Sprite:
    result = +Sprite
    match kind:
        case NoteKind.TAP:
            result @= Skin.tap_note
        case NoteKind.FLICK:
            result @= Skin.flick_note
        case NoteKind.DIRECTIONAL_FLICK:
            if direction > 0:
                result @= Skin.right_flick_note
            else:
                result @= Skin.left_flick_note
        case NoteKind.HOLD_HEAD:
            result @= Skin.hold_head_note
        case NoteKind.HOLD_TICK:
            result @= Skin.hold_tick_note
        case NoteKind.HOLD_ANCHOR:
            pass
        case NoteKind.HOLD_END:
            result @= Skin.hold_end_note
    return result


def get_note_arrow_sprite(kind: NoteKind, direction: int) -> Sprite:
    result = +Sprite
    match kind:
        case NoteKind.FLICK:
            result @= Skin.flick_arrow
        case NoteKind.DIRECTIONAL_FLICK:
            if direction > 0:
                result @= Skin.right_flick_arrow
            else:
                result @= Skin.left_flick_arrow
        case _:
            pass
    return result


def get_note_linear_particle(kind: NoteKind, direction: int) -> Particle:
    result = +Particle
    match kind:
        case NoteKind.TAP:
            result @= Particles.tap_linear
        case NoteKind.FLICK:
            result @= Particles.flick_linear
        case NoteKind.DIRECTIONAL_FLICK:
            if direction > 0:
                result @= Particles.right_flick_linear
            else:
                result @= Particles.left_flick_linear
        case NoteKind.HOLD_HEAD | NoteKind.HOLD_TICK | NoteKind.HOLD_END:
            result @= Particles.hold_linear
        case _:
            pass
    return result


def get_note_circular_particle(kind: NoteKind, direction: int) -> Particle:
    result = +Particle
    match kind:
        case NoteKind.TAP:
            result @= Particles.tap_circular
        case NoteKind.FLICK:
            result @= Particles.flick_circular
        case NoteKind.DIRECTIONAL_FLICK:
            if direction > 0:
                result @= Particles.right_flick_circular
            else:
                result @= Particles.left_flick_circular
        case NoteKind.HOLD_HEAD | NoteKind.HOLD_TICK | NoteKind.HOLD_END:
            result @= Particles.hold_circular
        case _:
            pass
    return result


def get_note_head_sprite() -> Sprite:
    """Return the sprite for hold note heads.

    Used by connectors to draw the head of an active hold note at the judgment line.
    """
    return Skin.hold_head_note


def get_note_active_circular_particle() -> Particle:
    """Return the active circular particle effect for hold notes.

    Used by connectors to draw the active circular particle effect for hold notes.
    """
    return Particles.hold_active_circular


def get_note_bucket(kind: NoteKind) -> Bucket:
    result = +Bucket
    match kind:
        case NoteKind.TAP:
            result @= Buckets.tap_note
        case NoteKind.FLICK:
            result @= Buckets.flick_note
        case NoteKind.DIRECTIONAL_FLICK:
            result @= Buckets.directional_flick_note
        case NoteKind.HOLD_HEAD:
            result @= Buckets.hold_head_note
        case NoteKind.HOLD_TICK:
            result @= Buckets.hold_tick_note
        case NoteKind.HOLD_ANCHOR:
            pass  # Hold anchors do not have a bucket
        case NoteKind.HOLD_END:
            result @= Buckets.hold_end_note
    return result


def draw_note(
    kind: NoteKind,
    lane: float,
    y: float,
    direction: int = 0,
):
    if kind == NoteKind.HOLD_ANCHOR:
        return
    body_sprite = get_note_body_sprite(kind, direction)
    arrow_sprite = get_note_arrow_sprite(kind, direction)
    draw_note_body(body_sprite, lane, y)
    if kind == NoteKind.FLICK:
        draw_flick_arrow(arrow_sprite, lane, y)
    elif kind == NoteKind.DIRECTIONAL_FLICK:
        draw_directional_flick_arrow(arrow_sprite, lane, y, direction)


def draw_note_body(sprite: Sprite, lane: float, y: float):
    alpha = note_y_to_alpha(y)
    if alpha <= 0:
        return
    layout = layout_note_body(lane, y)
    sprite.draw(layout, z=get_z(LAYER_NOTE, lane=lane, y=y), a=alpha)


def draw_flick_arrow(sprite: Sprite, lane: float, y: float):
    cycle_time = time() % FLICK_ARROW_PERIOD
    progress = cycle_time / FLICK_ARROW_PERIOD
    alpha = note_y_to_alpha(y) * ease_out_quad(
        interp_clamped(
            (
                0,
                FLICK_FADE_IN_TIME,
                FLICK_ARROW_PERIOD - FLICK_FADE_OUT_TIME,
                FLICK_ARROW_PERIOD,
            ),
            (
                0,
                1,
                1,
                0,
            ),
            cycle_time,
        )
    )
    if alpha <= 0:
        return
    layout = layout_flick_arrow(lane, y, progress)
    sprite.draw(layout, z=get_z(LAYER_ARROW, lane=lane, y=y), a=alpha)


def draw_directional_flick_arrow(sprite: Sprite, lane: float, y: float, direction: int):
    cycle_time = time() % DIRECTIONAL_FLICK_ARROW_PERIOD
    progress = cycle_time / DIRECTIONAL_FLICK_ARROW_PERIOD
    base_alpha = note_y_to_alpha(y)
    for i in range(abs(direction)):
        alpha = base_alpha * ease_out_quad(
            interp_clamped(
                (
                    0,
                    FLICK_FADE_IN_TIME,
                    DIRECTIONAL_FLICK_ARROW_PERIOD - FLICK_FADE_OUT_TIME,
                    DIRECTIONAL_FLICK_ARROW_PERIOD,
                ),
                (
                    0 if i == 0 else 1,  # Fade in only for the first arrow
                    1,
                    1,
                    0 if i == abs(direction) - 1 else 1,  # Fade out only for the last arrow
                ),
                cycle_time,
            )
        )
        if alpha <= 0:
            continue
        layout = layout_directional_flick_arrow(lane, y, direction, i, progress)
        sprite.draw(layout, z=get_z(LAYER_ARROW, lane=lane, y=y), a=alpha)


def play_note_particle(
    kind: NoteKind,
    lane: float,
    direction: int = 0,
):
    if kind == NoteKind.HOLD_ANCHOR:
        return
    if not Options.note_effect_enabled:
        return
    linear_particle = get_note_linear_particle(kind, direction)
    linear_layout = layout_note_linear_particle(lane)
    linear_particle.spawn(linear_layout, NOTE_PARTICLE_DURATION)
    circular_particle = get_note_circular_particle(kind, direction)
    circular_layout = layout_note_circular_particle(lane)
    circular_particle.spawn(circular_layout, NOTE_PARTICLE_DURATION)
    play_lane_particle(lane)


def get_note_sfx(kind: NoteKind, judgment: int) -> Effect:
    result = +Effect
    match kind:
        case NoteKind.FLICK | NoteKind.DIRECTIONAL_FLICK:
            match judgment:
                case Judgment.PERFECT:
                    result @= Effects.perfect_alt
                case Judgment.GREAT:
                    result @= Effects.great_alt
                case Judgment.GOOD:
                    result @= Effects.good_alt
        case _:
            match judgment:
                case Judgment.PERFECT:
                    result @= Effects.perfect
                case Judgment.GREAT:
                    result @= Effects.great
                case Judgment.GOOD:
                    result @= Effects.good
    return result


def play_note_sfx(kind: NoteKind, judgment: int):
    if not Options.sfx_enabled:
        return
    if kind == NoteKind.HOLD_ANCHOR:
        return
    sfx = get_note_sfx(kind, judgment)
    sfx.play()


def schedule_note_sfx(kind: NoteKind, judgment: int, target_time: float):
    if not Options.sfx_enabled:
        return
    if kind == NoteKind.HOLD_ANCHOR:
        return
    sfx = get_note_sfx(kind, judgment)
    sfx.schedule(target_time)


def draw_note_head(lane: float):
    """Draw an active hold note head at the judgment line."""
    layout = layout_note_body(lane, 0)
    Skin.hold_head_note.draw(layout, z=get_z(LAYER_NOTE_HEAD, lane=lane, y=0), a=1)


def update_hold_particle(handle: ParticleHandle, lane: float):
    """Update an active hold note particle effect handle to the current lane, spawning a new particle if needed."""
    layout = layout_note_circular_particle(lane)
    if handle.id == 0:
        handle @= Particles.hold_active_circular.spawn(layout, ACTIVE_HOLD_PARTICLE_DURATION, loop=True)
    else:
        handle.move(layout)


def destroy_particle(handle: ParticleHandle):
    """Destroy a particle effect if it exists."""
    if handle.id != 0:
        handle.destroy()
        handle.id = 0


def update_hold_sfx(handle: LoopedEffectHandle):
    """Start an active hold note looped sound effect if it's not already playing."""
    if not Options.sfx_enabled:
        return
    if handle.id == 0:
        handle @= Effects.hold.loop()


def stop_looped_sfx(handle: LoopedEffectHandle):
    """Stop a looped sound effect if it's playing."""
    if handle.id != 0:
        handle.stop()
        handle.id = 0


def schedule_hold_sfx(start_time: float, end_time: float):
    """Schedule an active hold note looped sound effect to play over an interval."""
    if not Options.sfx_enabled:
        return
    Effects.hold.schedule_loop(start_time).stop(end_time)


def init_note_life(archetype: type[PlayArchetype | WatchArchetype]):
    match archetype.key:
        case NoteKind.HOLD_TICK:
            archetype.life.update(
                perfect_increment=1,
                miss_increment=-20,
            )
        case _:
            archetype.life.update(
                perfect_increment=1,
                miss_increment=-100,
            )


def get_flick_speed_threshold(direction: int) -> float:
    """Return the speed threshold for a flick input to be counted."""
    if direction == 0:
        return FLICK_SPEED_THRESHOLD * Layout.lane_width
    else:
        return (
            DIRECTIONAL_FLICK_SPEED_THRESHOLD_BASE + DIRECTIONAL_FLICK_SPEED_THRESHOLD_INCREMENT * abs(direction)
        ) * Layout.lane_width
