from math import pi

from pydori.tutorial.instructions import InstructionIcons
from sonolus.script.interval import interp_clamped, lerp_clamped, remap
from sonolus.script.runtime import runtime_ui
from sonolus.script.vec import Vec2

# Scale factor for flick motion.
FLICK_MOTION_DISTANCE = 0.75


def instruction_scale() -> float:
    return runtime_ui().instruction_config.scale


def instruction_alpha() -> float:
    return runtime_ui().instruction_config.alpha


def _paint_tap(
    pos: Vec2,
    progress: float,
    a: float = 1,
):
    angle = lerp_clamped(pi / 6, pi / 3, progress)
    position = Vec2(0, -1).rotate(pi / 3) * (0.25 * instruction_scale()) + pos
    InstructionIcons.hand.paint(
        position=Vec2(0, 1).rotate(angle) * 0.25 * instruction_scale() + position,
        size=0.25 * instruction_scale(),
        rotation=(180 * angle) / pi,
        z=0,
        a=a * instruction_alpha(),
    )


def paint_tap_motion(pos: Vec2, progress: float, fade_out: bool = True):
    if fade_out:
        a = interp_clamped(
            (0, 0.05, 0.75, 0.95),
            (0, 1, 1, 0),
            progress,
        )
    else:
        a = interp_clamped(
            (0, 0.25),
            (0, 1),
            progress,
        )
    tap_progress = interp_clamped(
        (0.25, 0.75),
        (0, 1),
        progress,
    )
    _paint_tap(pos, tap_progress, a)


def paint_release_motion(pos: Vec2, progress: float):
    a = interp_clamped(
        (0.25, 0.75),
        (1, 0),
        progress,
    )
    tap_progress = interp_clamped(
        (0.25, 0.75),
        (1, 0),  # Reversed for release motion
        progress,
    )
    _paint_tap(
        pos,
        tap_progress,
        a,
    )


def paint_hold_motion(
    pos: Vec2,
    a: float = 1,
):
    _paint_tap(pos, 1, a)


def paint_follow_motion(
    from_pos: Vec2,
    to_pos: Vec2,
    progress: float,
    a: float = 1,
):
    pos = lerp_clamped(from_pos, to_pos, progress)
    paint_hold_motion(pos, a=a)


def paint_flick_motion(
    from_pos: Vec2,
    angle: float,
    progress: float,
):
    to_pos = from_pos + Vec2.unit(angle) * FLICK_MOTION_DISTANCE
    a = interp_clamped(
        (0.25, 0.75),
        (1, 0),
        progress,
    )
    paint_follow_motion(from_pos, to_pos, progress, a=a)


def paint_tap_flick_motion(from_pos: Vec2, angle: float, progress: float, tap_duration: float, flick_duration: float):
    flick_start_progress = tap_duration / (tap_duration + flick_duration)
    if progress < flick_start_progress:
        paint_tap_motion(from_pos, remap(0, flick_start_progress, 0, 1, progress), fade_out=False)
    else:
        paint_flick_motion(from_pos, angle, remap(flick_start_progress, 1, 0, 1, progress))


def paint_hold_flick_motion(
    from_pos: Vec2,
    angle: float,
    progress: float,
    hold_duration: float,
    flick_duration: float,
):
    flick_start_progress = hold_duration / (hold_duration + flick_duration)
    if progress < flick_start_progress:
        paint_hold_motion(from_pos)
    else:
        paint_flick_motion(from_pos, angle, remap(flick_start_progress, 1, 0, 1, progress))
