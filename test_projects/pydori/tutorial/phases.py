from math import pi

from pydori.lib.connector import draw_hold_connector
from pydori.lib.layout import lane_to_transformed_vec
from pydori.lib.note import (
    NoteKind,
    draw_note,
    draw_note_head,
    play_note_particle,
    play_note_sfx,
    update_hold_particle,
    update_hold_sfx,
)
from pydori.tutorial.framework import PhaseTime, get_hold_particle, get_hold_sfx, progress_to_y
from pydori.tutorial.instructions import Instructions
from pydori.tutorial.intro import draw_tutorial_intro_note
from pydori.tutorial.painting import (
    paint_hold_flick_motion,
    paint_hold_motion,
    paint_release_motion,
    paint_tap_flick_motion,
    paint_tap_motion,
)
from sonolus.script.array import Array
from sonolus.script.bucket import Judgment
from sonolus.script.interval import remap

ANGLE_RIGHT = 0
ANGLE_UP = pi / 2
ANGLE_LEFT = pi

# Duration of the intro segment where the note intro sprite is shown in the center.
INTRO_DURATION = 1.5

# Duration of the fall phase where the note travels to the judgment line.
FALL_DURATION = 1.5

# Number of times to show the motion during the frozen phase.
FROZEN_REPEATS = 4

# Duration of the tap motion demonstration during each cycle of the frozen phase.
FROZEN_TAP_DURATION = 1

# Duration of the hold motion demonstration during each cycle of the frozen phase.
FROZEN_HOLD_DURATION = 1

# Duration of the flick motion demonstration during each cycle of the frozen phase.
FROZEN_FLICK_DURATION = 0.5

# Duration of the hold release motion demonstration during each cycle of the frozen phase.
FROZEN_RELEASE_DURATION = 1

# Duration after the frozen phase to wait before ending the phase.
END_DURATION = 1.5


def tap_phase(t: PhaseTime):
    intro = t.first(INTRO_DURATION)
    fall = intro.next(FALL_DURATION)
    frozen = fall.next(FROZEN_TAP_DURATION, repeats=FROZEN_REPEATS)
    hit = frozen.end_instant()
    end = frozen.next(END_DURATION)

    if intro:
        draw_tutorial_intro_note(NoteKind.TAP)
    if fall:
        draw_note(
            NoteKind.TAP,
            lane=0,
            y=progress_to_y(fall.progress),
        )
    if frozen:
        draw_note(
            NoteKind.TAP,
            lane=0,
            y=0,
        )
        paint_tap_motion(lane_to_transformed_vec(0), frozen.progress)
        Instructions.tap.show()
    if hit:
        play_note_particle(NoteKind.TAP, 0)
        play_note_sfx(NoteKind.TAP, Judgment.PERFECT)
    if end:
        pass
    return end.is_done


def flick_phase(t: PhaseTime):
    intro = t.first(INTRO_DURATION)
    fall = intro.next(FALL_DURATION)
    frozen = fall.next(FROZEN_TAP_DURATION + FROZEN_FLICK_DURATION, repeats=FROZEN_REPEATS)
    hit = t.instant(frozen.end - FROZEN_FLICK_DURATION)
    end = frozen.next(END_DURATION)

    if intro:
        draw_tutorial_intro_note(NoteKind.FLICK)
    if fall:
        draw_note(
            NoteKind.FLICK,
            lane=0,
            y=progress_to_y(fall.progress),
        )
    if frozen:
        if hit.is_upcoming:
            draw_note(
                NoteKind.FLICK,
                lane=0,
                y=0,
            )
        paint_tap_flick_motion(
            lane_to_transformed_vec(0), ANGLE_UP, frozen.progress, FROZEN_TAP_DURATION, FROZEN_FLICK_DURATION
        )
        Instructions.tap_flick.show()
    if hit:
        play_note_particle(NoteKind.FLICK, 0)
        play_note_sfx(NoteKind.FLICK, Judgment.PERFECT)
    if end:
        pass
    return end.is_done


def directional_flick_phase(t: PhaseTime):
    intro = t.first(INTRO_DURATION)
    fall = intro.next(FALL_DURATION)
    frozen = fall.next(FROZEN_TAP_DURATION + FROZEN_FLICK_DURATION, repeats=FROZEN_REPEATS)
    hit = t.instant(frozen.end - FROZEN_FLICK_DURATION)
    end = frozen.next(END_DURATION)

    if intro:
        draw_tutorial_intro_note(NoteKind.DIRECTIONAL_FLICK, direction=-1, lane=-0.55)
        draw_tutorial_intro_note(NoteKind.DIRECTIONAL_FLICK, direction=1, lane=0.55)
    if fall:
        draw_note(NoteKind.DIRECTIONAL_FLICK, lane=-1, y=progress_to_y(fall.progress), direction=-1)
        draw_note(NoteKind.DIRECTIONAL_FLICK, lane=1, y=progress_to_y(fall.progress), direction=1)
    if frozen:
        if hit.is_upcoming:
            draw_note(NoteKind.DIRECTIONAL_FLICK, lane=-1, y=0, direction=-1)
            draw_note(NoteKind.DIRECTIONAL_FLICK, lane=1, y=0, direction=1)
        paint_tap_flick_motion(
            lane_to_transformed_vec(-1), ANGLE_LEFT, frozen.progress, FROZEN_TAP_DURATION, FROZEN_FLICK_DURATION
        )
        paint_tap_flick_motion(
            lane_to_transformed_vec(1), ANGLE_RIGHT, frozen.progress, FROZEN_TAP_DURATION, FROZEN_FLICK_DURATION
        )
        Instructions.tap_flick.show()
    if hit:
        play_note_particle(NoteKind.DIRECTIONAL_FLICK, lane=-1, direction=-1)
        play_note_particle(NoteKind.DIRECTIONAL_FLICK, lane=1, direction=1)
        play_note_sfx(NoteKind.DIRECTIONAL_FLICK, Judgment.PERFECT)
    if end:
        pass
    return end.is_done


def hold_head_phase(t: PhaseTime):
    intro = t.first(INTRO_DURATION)
    fall = intro.next(FALL_DURATION)
    frozen = fall.next(FROZEN_TAP_DURATION, repeats=FROZEN_REPEATS)
    hit = frozen.end_instant()
    end = frozen.next(END_DURATION)

    if intro:
        draw_tutorial_intro_note(NoteKind.HOLD_HEAD)
    if fall:
        draw_note(
            NoteKind.HOLD_HEAD,
            lane=0,
            y=progress_to_y(fall.progress),
        )
        draw_hold_connector(0, 0, progress_to_y(fall.progress), 99)
    if frozen:
        draw_note(
            NoteKind.HOLD_HEAD,
            lane=0,
            y=0,
        )
        draw_hold_connector(0, 0, 0, 99)
        paint_tap_motion(lane_to_transformed_vec(0), frozen.progress, fade_out=False)
        Instructions.tap_hold.show()
    if hit:
        play_note_particle(NoteKind.HOLD_HEAD, 0)
        play_note_sfx(NoteKind.HOLD_HEAD, Judgment.PERFECT)
    if end:
        particle = get_hold_particle()
        sfx = get_hold_sfx()
        update_hold_particle(particle, 0)
        update_hold_sfx(sfx)
        paint_hold_motion(lane_to_transformed_vec(0))
        draw_hold_connector(0, 0, 0, 99)
        draw_note_head(0)
        Instructions.tap_hold.show()
    return end.is_done


def hold_tick_phase(t: PhaseTime):
    # Lane of each tick.
    tick_lanes = Array(
        0,  # Dummy for the hold head.
        0,
        2,
        0,
        -2,
        0,
        0,  # Dummy for the hold end.
    )

    # The fall progress at which each tick meets the judgment line.
    target_progresses = Array(
        -999,  # Dummy for the hold head.
        0.2,
        0.4,
        0.6,
        0.8,
        1.0,
        999,  # Dummy for the hold end.
    )

    # We do ticks a little differently since we want to show the motion of following ticks.
    # Therefore, the frozen phase comes before the fall phase so we can show the instructions
    # before the ticks fall.
    intro = t.first(INTRO_DURATION)
    frozen = intro.next(FROZEN_HOLD_DURATION, repeats=FROZEN_REPEATS)
    fall = frozen.next(FALL_DURATION)
    end = fall.next(END_DURATION)

    def progress_for_tick(index: int) -> float:
        return remap(target_progresses[index] - 1, target_progresses[index], 0, 1, max(0, fall.progress))

    def tick_y(index: int) -> float:
        return progress_to_y(progress_for_tick(index))

    if intro:
        draw_tutorial_intro_note(NoteKind.HOLD_TICK)

    # Draw the ticks, hold motion, and connectors in all phases after the intro.
    if intro.is_done:
        # Play the particle, draw the head, and paint the hold motion.

        particle = get_hold_particle()
        sfx = get_hold_sfx()

        hold_lane = 0
        for i in range(1, len(tick_lanes)):
            y = tick_y(i)
            prev_y = tick_y(i - 1)
            if y < 0:
                continue
            elif prev_y < 0:
                # Tick i is above the judgment line and tick i-1 is below it,
                # so we can use the two to find the current lane of the hold at the judgment line (y=0)
                hold_lane = remap(prev_y, y, tick_lanes[i - 1], tick_lanes[i], 0)
                break

        update_hold_particle(particle, hold_lane)
        if frozen.is_done:
            # Only play sfx once frozen is done because it's a bit distracting otherwise.
            update_hold_sfx(sfx)
        draw_note_head(hold_lane)
        paint_hold_motion(lane_to_transformed_vec(hold_lane))

        # Draw the connectors.
        for i in range(1, len(tick_lanes)):
            draw_hold_connector(tick_lanes[i - 1], tick_lanes[i], tick_y(i - 1), tick_y(i))

        # Draw ticks and play effects after hit.
        for i, (lane, target_progress) in enumerate(zip(tick_lanes, target_progresses, strict=False)):
            hit_instant = t.instant(fall.start + target_progress * FALL_DURATION)
            if hit_instant.is_upcoming:
                draw_note(
                    NoteKind.HOLD_TICK,
                    lane=lane,
                    y=progress_to_y(progress_for_tick(i)),
                )
            if hit_instant:
                play_note_particle(NoteKind.HOLD_TICK, lane)
                play_note_sfx(NoteKind.HOLD_TICK, Judgment.PERFECT)

    if frozen:
        paint_hold_motion(lane_to_transformed_vec(tick_lanes[0]))
        Instructions.hold_follow.show()
    return end.is_done


def hold_end_phase(t: PhaseTime):
    intro = t.first(INTRO_DURATION)
    fall = intro.next(FALL_DURATION)
    frozen = fall.next(FROZEN_RELEASE_DURATION, repeats=FROZEN_REPEATS)
    hit = frozen.end_instant()
    end = frozen.next(END_DURATION)

    if intro:
        draw_tutorial_intro_note(NoteKind.HOLD_END)
    if fall:
        particle = get_hold_particle()
        sfx = get_hold_sfx()
        draw_note(
            NoteKind.HOLD_END,
            lane=0,
            y=progress_to_y(fall.progress),
        )
        update_hold_particle(particle, 0)
        update_hold_sfx(sfx)
        draw_note_head(0)
        draw_hold_connector(0, 0, 0, progress_to_y(fall.progress))
        paint_hold_motion(lane_to_transformed_vec(0))
    if frozen:
        draw_note(
            NoteKind.HOLD_END,
            lane=0,
            y=0,
        )
        paint_release_motion(lane_to_transformed_vec(0), frozen.progress)
        Instructions.release.show()
    if hit:
        play_note_particle(NoteKind.HOLD_END, 0)
        play_note_sfx(NoteKind.HOLD_END, Judgment.PERFECT)
    return end.is_done


def hold_end_flick_phase(t: PhaseTime):
    intro = t.first(INTRO_DURATION)
    fall = intro.next(FALL_DURATION)
    frozen = fall.next(FROZEN_HOLD_DURATION + FROZEN_FLICK_DURATION, repeats=FROZEN_REPEATS)
    hit = t.instant(frozen.end - FROZEN_FLICK_DURATION)
    end = frozen.next(END_DURATION)

    if intro:
        draw_tutorial_intro_note(NoteKind.FLICK, is_hold_flick_end=True)
    if fall:
        particle = get_hold_particle()
        sfx = get_hold_sfx()
        draw_note(
            NoteKind.FLICK,
            lane=0,
            y=progress_to_y(fall.progress),
        )
        update_hold_particle(particle, 0)
        update_hold_sfx(sfx)
        draw_note_head(0)
        draw_hold_connector(0, 0, 0, progress_to_y(fall.progress))
        paint_hold_motion(lane_to_transformed_vec(0))
    if frozen:
        if hit.is_upcoming:
            draw_note(
                NoteKind.FLICK,
                lane=0,
                y=0,
            )
        paint_hold_flick_motion(
            lane_to_transformed_vec(0), ANGLE_UP, frozen.progress, FROZEN_HOLD_DURATION, FROZEN_FLICK_DURATION
        )
        Instructions.hold_flick.show()
    if hit:
        play_note_particle(NoteKind.FLICK, 0)
        play_note_sfx(NoteKind.FLICK, Judgment.PERFECT)
    return end.is_done


PHASES = (
    tap_phase,
    flick_phase,
    directional_flick_phase,
    hold_head_phase,
    hold_tick_phase,
    hold_end_phase,
    hold_end_flick_phase,
)
