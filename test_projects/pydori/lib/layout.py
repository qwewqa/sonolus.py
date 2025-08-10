from math import pi
from typing import Self

from pydori.lib.options import Options
from sonolus.script.array import Dim
from sonolus.script.easing import ease_out_quad
from sonolus.script.globals import level_data
from sonolus.script.interval import clamp, interp_clamped, lerp, remap
from sonolus.script.quad import Quad, Rect
from sonolus.script.record import Record
from sonolus.script.transform import Transform2d
from sonolus.script.vec import Vec2

# Coordinate System Overview:
# - Many functions operate on an 'internal' coordinate system.
# - The internal coordinate system has the judge line at y=0 and notes approach it from a positive y value.
# - Notes are squares and lanes are rectangles in the internal coordinate system, making layout calculations easier.
# - Before being rendered, coordinates are transformed to the 'screen' coordinate system; this transformation
#   gives notes the appearance of approaching from an angle with 3D perspective.
# - Some layout calculations are done partially in the 'screen' coordinate system, such as arrow layout.

# Range of lanes in the game.
START_LANE = -3
END_LANE = 3
LANE_COUNT = 7
LANE_COUNT_DIM = Dim[7]

# The y-coordinate of the judge line in screen coordinates.
JUDGE_LINE_SCREEN_Y = -0.5

# The y-coordinate of the vanishing point in screen coordinates.
VANISHING_POINT_SCREEN_Y = 1.35

# Length of a lane at 100% lane length.
BASE_LANE_LENGTH = 8

# Width of a lane at 100% lane width.
BASE_LANE_WIDTH = 0.35

# Length over which notes fade in at the start and fade out at the end.
NOTE_FADE_LENGTH = 0.5

# Width of the stage border at 100% lane width.
BASE_STAGE_BORDER_WIDTH = BASE_LANE_WIDTH * 0.25

# Multiplier for directional flick arrow size compared to note width.
# Arrows are less visible than note bodies in typical skins, so we make them larger so they're easier to see.
DIRECTIONAL_FLICK_ARROW_SCALE = 1.5

# Initial and final vertical offset of the flick arrow during its animation cycle, relative to note width.
FLICK_ARROW_INITIAL_OFFSET = -0.1
FLICK_ARROW_FINAL_OFFSET = 0.4

# Horizontal offset of the directional flick arrow from note center.
# Negated for left flicks.
DIRECTIONAL_FLICK_OFFSET = 0.4

# Multiplier for circular effect size compared to note width.
CIRCULAR_EFFECT_SCALE = 1.8

# Multiplier for note hitbox width compared to lane width.
NOTE_HITBOX_SCALE = 2.5

# Scale factor for the note speed (note travel time).
# At this note speed, a note will take 1 second to travel from lane start to judge line.
# Higher values mean that the same speed will be slower / have a longer travel time.
REFERENCE_SPEED = 6


@level_data
class Layout:
    judge_line_screen_y: float
    vanishing_point_screen_y: float

    transform: Transform2d

    note_width: float
    lane_width: float
    stage_border_width: float

    note_y_max: float
    note_y_min: float


def init_layout():
    Layout.judge_line_screen_y = JUDGE_LINE_SCREEN_Y
    Layout.vanishing_point_screen_y = VANISHING_POINT_SCREEN_Y

    Layout.transform = Transform2d.new().perspective_y(
        Layout.judge_line_screen_y,
        Vec2(0, Layout.vanishing_point_screen_y),
    )

    Layout.note_width = BASE_LANE_WIDTH * Options.note_size
    Layout.lane_width = BASE_LANE_WIDTH * Options.lane_width
    Layout.stage_border_width = BASE_STAGE_BORDER_WIDTH

    Layout.note_y_max = Options.lane_length * BASE_LANE_LENGTH
    Layout.note_y_min = -1


def transform_vec(vec: Vec2) -> Vec2:
    """Apply perspective transformation to a vec."""
    return Layout.transform.transform_vec(vec)


def transform_quad(quad: Quad | Rect) -> Quad:
    """Apply perspective transformation to a quad."""
    return Layout.transform.transform_quad(quad)


def lane_to_x(lane: float) -> float:
    """Return the center x position of a lane."""
    return lane * Layout.lane_width


def lane_to_transformed_vec(lane: float) -> Vec2:
    """Return the transformed center x position of a lane at y=0."""
    return transform_vec(Vec2(lane_to_x(lane), 0))


def layout_lane(lane: float) -> Quad:
    center_x = lane_to_x(lane)
    return transform_quad(
        Rect(
            l=center_x - Layout.lane_width / 2,
            r=center_x + Layout.lane_width / 2,
            b=Layout.note_y_min,
            t=Layout.note_y_max,
        )
    )


def layout_stage_left_border() -> Quad:
    right = -LANE_COUNT / 2 * Layout.lane_width
    return transform_quad(
        Rect(
            l=right - Layout.stage_border_width,
            r=right,
            b=Layout.note_y_min,
            t=Layout.note_y_max,
        )
    )


def layout_stage_right_border() -> Quad:
    left = LANE_COUNT / 2 * Layout.lane_width
    return transform_quad(
        Rect(
            l=left,
            r=left + Layout.stage_border_width,
            b=Layout.note_y_min,
            t=Layout.note_y_max,
        )
    )


def layout_judge_line() -> Quad:
    return transform_quad(
        Rect(
            l=-LANE_COUNT / 2 * Layout.lane_width,
            r=LANE_COUNT / 2 * Layout.lane_width,
            b=-Layout.note_width / 2,
            t=Layout.note_width / 2,
        )
    )


def layout_note_body(lane: float, y: float) -> Quad:
    return transform_quad(
        Rect.from_center(
            Vec2(lane_to_x(lane), y),
            dimensions=Vec2(Layout.note_width, Layout.note_width),
        )
    )


def layout_flick_arrow(lane: float, y: float, progress: float) -> Quad:
    """Return the layout quad for a flick arrow.

    Args:
        lane: The lane number
        y: The y-coordinate of the arrow.
        progress: The progress of the flick animation (0 to 1).
    """
    base_bl = transform_vec(Vec2(lane_to_x(lane) - Layout.note_width / 2, y))
    base_br = transform_vec(Vec2(lane_to_x(lane) + Layout.note_width / 2, y))
    up = (base_br - base_bl).rotate(pi / 2)
    offset = lerp(FLICK_ARROW_INITIAL_OFFSET, FLICK_ARROW_FINAL_OFFSET, progress) * up
    bl = base_bl + offset
    br = base_br + offset
    tl = bl + up
    tr = br + up
    return Quad(bl=bl, br=br, tl=tl, tr=tr)


def layout_directional_flick_arrow(lane: float, y: float, direction: int, number: int, progress: float) -> Quad:
    """Return the layout quad for a directional flick arrow.

    Args:
        lane: The lane number
        y: The y-coordinate of the arrow.
        direction: The direction of the flick (positive for right, negative for left).
        number: The index of the arrow in the sequence ranging from 0 to (direction - 1) inclusive.
        progress: The progress of the flick animation (0 to 1).
    """
    direction_sign = 1 if direction > 0 else -1
    return transform_quad(
        Rect.from_center(
            center=Vec2(
                lane_to_x(lane + direction_sign * (DIRECTIONAL_FLICK_OFFSET + number + progress)),
                y,
            ),
            dimensions=Vec2(
                Layout.note_width * DIRECTIONAL_FLICK_ARROW_SCALE,
                Layout.note_width * DIRECTIONAL_FLICK_ARROW_SCALE,
            ),
        )
        .as_quad()
        .rotate_centered(
            # Rotate a quarter turn clockwise for right flicks, counterclockwise for left flicks.
            direction_sign * -pi / 2
        )
    )


def layout_note_linear_particle(lane: float) -> Quad:
    center_x = lane_to_x(lane)
    bl = transform_vec(Vec2(center_x - Layout.note_width / 2, 0))
    br = transform_vec(Vec2(center_x + Layout.note_width / 2, 0))
    # We want the effect to be as tall as it is wide
    height = (br - bl).magnitude
    tl = bl + Vec2(0, height)
    tr = br + Vec2(0, height)
    return Quad(bl=bl, br=br, tl=tl, tr=tr)


def layout_note_circular_particle(lane: float) -> Quad:
    center = Vec2(lane_to_x(lane), 0)
    result = transform_quad(
        Rect.from_center(
            center,
            dimensions=CIRCULAR_EFFECT_SCALE * Vec2(Layout.note_width, Layout.note_width),
        )
    )
    # Due to how particles are rendered, a perspective "trapezoid" shape will look weird,
    # so we make it a parallelogram instead.
    # Specifically, particles only render affine transformations correctly.
    mean_width = (result.tr.x - result.tl.x + result.br.x - result.bl.x) / 2
    mean_top_x = (result.tl.x + result.tr.x) / 2
    mean_bottom_x = (result.bl.x + result.br.x) / 2
    return Quad(
        bl=Vec2(mean_bottom_x - mean_width / 2, result.bl.y),
        br=Vec2(mean_bottom_x + mean_width / 2, result.br.y),
        tl=Vec2(mean_top_x - mean_width / 2, result.tl.y),
        tr=Vec2(mean_top_x + mean_width / 2, result.tr.y),
    )


def layout_hold_connector(
    lane_a: float,
    lane_b: float,
    y_a: float,
    y_b: float,
) -> Quad:
    y_a_adj = clamp(y_a, 0, Layout.note_y_max)
    y_b_adj = clamp(y_b, 0, Layout.note_y_max)
    lane_a_adj = remap(y_a, y_b, lane_a, lane_b, y_a_adj)
    lane_b_adj = remap(y_a, y_b, lane_a, lane_b, y_b_adj)
    center_x_a = lane_to_x(lane_a_adj)
    center_x_b = lane_to_x(lane_b_adj)
    return transform_quad(
        Quad(
            bl=Vec2(center_x_a - Layout.note_width / 2, y_a_adj),
            br=Vec2(center_x_a + Layout.note_width / 2, y_a_adj),
            tl=Vec2(center_x_b - Layout.note_width / 2, y_b_adj),
            tr=Vec2(center_x_b + Layout.note_width / 2, y_b_adj),
        )
    )


def layout_sim_line(
    lane_a: float,
    lane_b: float,
    y: float,
):
    left_x = lane_to_x(lane_a)
    right_x = lane_to_x(lane_b)
    return transform_quad(
        Rect(
            l=left_x,
            r=right_x,
            b=y - Layout.note_width / 2,
            t=y + Layout.note_width / 2,
        )
    )


def preempt_time() -> float:
    """Return the time between a note's spawn and when it reaches the judge line."""
    return REFERENCE_SPEED / Options.note_speed


def get_note_y(scaled_time: float, target_scaled_time: float) -> float:
    """Return the y-coordinate of a note at the given scaled time based on its target scaled time."""
    return remap(
        target_scaled_time - preempt_time(),
        target_scaled_time,
        Layout.note_y_max,
        0,
        scaled_time,
    )


def note_y_to_alpha(y: float) -> float:
    return ease_out_quad(
        interp_clamped(
            (
                Layout.note_y_min,
                Layout.note_y_min + NOTE_FADE_LENGTH,
                Layout.note_y_max - NOTE_FADE_LENGTH,
                Layout.note_y_max,
            ),
            (
                0,
                1,
                1,
                0,
            ),
            y,
        )
    )


class Hitbox(Record):
    """A hitbox bounded by a left and right edge."""

    left: float
    right: float

    @classmethod
    def from_center(cls, center: float, width: float) -> Self:
        left = center - width / 2
        right = center + width / 2
        return cls(left, right)

    @classmethod
    def for_note(cls, lane: float, direction: float) -> Self:
        result = cls.from_center(lane_to_x(lane), Layout.lane_width * NOTE_HITBOX_SCALE)
        if direction > 0:
            result.right += Layout.lane_width * direction
        if direction < 0:
            result.left += Layout.lane_width * direction
        return result

    def layout(self) -> Quad:
        return transform_quad(
            Rect(
                l=self.left,
                r=self.right,
                b=Layout.note_y_min,
                t=Layout.note_y_max,
            )
        )
