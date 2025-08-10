from math import floor, pi, trunc
from typing import Literal

from pydori.lib.layout import END_LANE, LANE_COUNT, START_LANE
from sonolus.script.globals import level_data
from sonolus.script.interval import lerp
from sonolus.script.printing import PrintColor, PrintFormat, print_number
from sonolus.script.quad import Quad, Rect
from sonolus.script.runtime import HorizontalAlign, ScrollDirection, canvas, screen
from sonolus.script.vec import Vec2

# Number of seconds each column represents.
PREVIEW_COLUMN_SECS = 2

# Gap to add at top and bottom of the screen.
PREVIEW_MARGIN_Y = 0.1

# Gap to add on each side of each column (i.e. double this for the total gap between columns).
PREVIEW_MARGIN_X = 0.25

# Gap to add between text and lanes and edges of each column.
PREVIEW_TEXT_MARGIN_X = 0.015

# Gap to add between text and bar line positions.
PREVIEW_TEXT_MARGIN_Y = 0

# Height of text to be printed.
PREVIEW_TEXT_HEIGHT = 0.12

# Width of text to be printed.
PREVIEW_TEXT_WIDTH = PREVIEW_MARGIN_X - 2 * PREVIEW_TEXT_MARGIN_X

# Width of each lane.
PREVIEW_LANE_WIDTH = 0.072

# Width of stage borders.
PREVIEW_STAGE_BORDER_WIDTH = 0.25 * PREVIEW_LANE_WIDTH

# Width of each note.
PREVIEW_NOTE_WIDTH = PREVIEW_LANE_WIDTH

# Height of bar lines.
PREVIEW_BAR_LINE_HEIGHT = 0.0036

# Alpha of bar lines.
PREVIEW_BAR_LINE_ALPHA = 0.8

# Alpha of the top and bottom cover.
PREVIEW_COVER_ALPHA = 1.0

# The y-coordinate of the bottom of the screen plus the margin.
PREVIEW_Y_MIN = -1 + PREVIEW_MARGIN_Y

# The y-coordinate of the top of the screen minus the margin.
PREVIEW_Y_MAX = 1 - PREVIEW_MARGIN_Y

# Amount bar lines are extended on each side if configured to do so.
PREVIEW_BAR_EXTEND_WIDTH = 3 * PREVIEW_LANE_WIDTH

# Offset added to flick arrows to make them appear above their notes.
PREVIEW_FLICK_ARROW_Y_OFFSET = 0.9 * PREVIEW_NOTE_WIDTH


@level_data
class PreviewData:
    highest_lane: float
    last_time: float
    last_beat: float


@level_data
class PreviewLayout:
    column_count: int
    column_width: float


def init_preview_layout():
    PreviewLayout.column_count = floor(PreviewData.last_time / PREVIEW_COLUMN_SECS) + 1
    PreviewLayout.column_width = 2 * PREVIEW_MARGIN_X + PREVIEW_LANE_WIDTH * LANE_COUNT

    canvas().update(
        scroll_direction=ScrollDirection.LEFT_TO_RIGHT,
        size=PreviewLayout.column_width * PreviewLayout.column_count,
    )


def time_to_preview_col(time: float) -> int:
    return trunc(time / PREVIEW_COLUMN_SECS)


def time_to_preview_y(time: float) -> float:
    return lerp(PREVIEW_Y_MIN, PREVIEW_Y_MAX, time % PREVIEW_COLUMN_SECS / PREVIEW_COLUMN_SECS)


def lane_to_preview_x(lane: float, col: int) -> float:
    return (col + 0.5) * PreviewLayout.column_width + lane * PREVIEW_LANE_WIDTH - screen().w / 2


def lane_to_preview_left_x(lane: float, col: int) -> float:
    return lane_to_preview_x(lane - 0.5, col)


def lane_to_preview_right_x(lane: float, col: int) -> float:
    return lane_to_preview_x(lane + 0.5, col)


def layout_preview_lane(lane: float, col: int) -> Rect:
    return Rect(
        l=lane_to_preview_left_x(lane, col),
        r=lane_to_preview_right_x(lane, col),
        t=1,
        b=-1,
    )


def layout_preview_stage_border_left(col: int) -> Rect:
    return Rect(
        l=lane_to_preview_left_x(START_LANE, col) - PREVIEW_STAGE_BORDER_WIDTH,
        r=lane_to_preview_left_x(START_LANE, col),
        t=1,
        b=-1,
    )


def layout_preview_stage_border_right(col: int) -> Rect:
    return Rect(
        l=lane_to_preview_right_x(END_LANE, col),
        r=lane_to_preview_right_x(END_LANE, col) + PREVIEW_STAGE_BORDER_WIDTH,
        t=1,
        b=-1,
    )


def layout_preview_note(lane: float, time: float) -> Rect:
    col = time_to_preview_col(time)
    y = time_to_preview_y(time)
    return Rect(
        l=lane_to_preview_left_x(lane, col),
        r=lane_to_preview_right_x(lane, col),
        t=y + PREVIEW_NOTE_WIDTH / 2,
        b=y - PREVIEW_NOTE_WIDTH / 2,
    )


def layout_preview_flick_arrow(lane: float, time: float) -> Quad:
    col = time_to_preview_col(time)
    left_x = lane_to_preview_left_x(lane, col)
    right_x = lane_to_preview_right_x(lane, col)
    y = time_to_preview_y(time) + PREVIEW_FLICK_ARROW_Y_OFFSET
    return Quad(
        bl=Vec2(left_x, y - PREVIEW_NOTE_WIDTH / 2),
        br=Vec2(right_x, y - PREVIEW_NOTE_WIDTH / 2),
        tr=Vec2(right_x, y + PREVIEW_NOTE_WIDTH / 2),
        tl=Vec2(left_x, y + PREVIEW_NOTE_WIDTH / 2),
    )


def layout_preview_directional_flick_arrow(lane: float, time: float, direction: float) -> Quad:
    col = time_to_preview_col(time)
    left_x = lane_to_preview_left_x(lane, col)
    right_x = lane_to_preview_right_x(lane, col)
    y = time_to_preview_y(time)
    return (
        Rect(
            l=left_x,
            r=right_x,
            t=y + PREVIEW_NOTE_WIDTH / 2,
            b=y - PREVIEW_NOTE_WIDTH / 2,
        )
        .as_quad()
        .rotate_centered(
            -pi / 2 if direction > 0 else pi / 2,
        )
    )


def layout_preview_connector(
    lane_a: float,
    lane_b: float,
    time_a: float,
    time_b: float,
    col: int,
) -> Quad:
    col_time = col * PREVIEW_COLUMN_SECS
    left_x_a = lane_to_preview_left_x(lane_a, col)
    right_x_a = lane_to_preview_right_x(lane_a, col)
    left_x_b = lane_to_preview_left_x(lane_b, col)
    right_x_b = lane_to_preview_right_x(lane_b, col)
    y_a = lerp(
        PREVIEW_Y_MIN,
        PREVIEW_Y_MAX,
        (time_a - col_time) / PREVIEW_COLUMN_SECS,
    )
    y_b = lerp(
        PREVIEW_Y_MIN,
        PREVIEW_Y_MAX,
        (time_b - col_time) / PREVIEW_COLUMN_SECS,
    )
    return Quad(
        bl=Vec2(left_x_a, y_a),
        tl=Vec2(left_x_b, y_b),
        tr=Vec2(right_x_b, y_b),
        br=Vec2(right_x_a, y_a),
    )


def layout_preview_sim_line(
    lane_a: float,
    lane_b: float,
    time: float,
) -> Quad:
    col = time_to_preview_col(time)
    left_x = lane_to_preview_x(lane_a, col)
    right_x = lane_to_preview_x(lane_b, col)
    y = time_to_preview_y(time)
    return Quad(
        bl=Vec2(left_x, y - PREVIEW_LANE_WIDTH / 2 / 4),
        br=Vec2(right_x, y - PREVIEW_LANE_WIDTH / 2 / 4),
        tr=Vec2(right_x, y + PREVIEW_LANE_WIDTH / 2 / 4),
        tl=Vec2(left_x, y + PREVIEW_LANE_WIDTH / 2 / 4),
    )


def layout_preview_bar_line(
    time: float,
    extend: Literal["left", "right", "both", "none", "left_only", "right_only"] = "none",
) -> Quad:
    col = time_to_preview_col(time)
    left_lane = START_LANE - 0.5
    right_lane = END_LANE + 0.5
    left_x = lane_to_preview_x(left_lane, col)
    right_x = lane_to_preview_x(right_lane, col)
    match extend:
        case "left":
            left_x -= PREVIEW_BAR_EXTEND_WIDTH
        case "right":
            right_x += PREVIEW_BAR_EXTEND_WIDTH
        case "both":
            left_x -= PREVIEW_BAR_EXTEND_WIDTH
            right_x += PREVIEW_BAR_EXTEND_WIDTH
        case "left_only":
            right_x = left_x
            left_x -= PREVIEW_BAR_EXTEND_WIDTH
        case "right_only":
            left_x = right_x
            right_x += PREVIEW_BAR_EXTEND_WIDTH
        case _:
            pass
    y = time_to_preview_y(time)
    return Quad(
        bl=Vec2(left_x, y - PREVIEW_BAR_LINE_HEIGHT / 2),
        br=Vec2(right_x, y - PREVIEW_BAR_LINE_HEIGHT / 2),
        tr=Vec2(right_x, y + PREVIEW_BAR_LINE_HEIGHT / 2),
        tl=Vec2(left_x, y + PREVIEW_BAR_LINE_HEIGHT / 2),
    )


def print_at_time(
    value: float,
    time: float,
    *,
    fmt: PrintFormat,
    decimal_places: int = -1,
    color: PrintColor,
    side: Literal["left", "right"],
):
    print_number(
        value=value,
        fmt=fmt,
        decimal_places=decimal_places,
        anchor=Vec2(
            lane_to_preview_x(
                -LANE_COUNT / 2 if side == "left" else LANE_COUNT / 2,
                time_to_preview_col(time),
            )
            + (-PREVIEW_TEXT_MARGIN_X if side == "left" else PREVIEW_TEXT_MARGIN_X),
            time_to_preview_y(time) + PREVIEW_TEXT_MARGIN_Y,
        ),
        pivot=Vec2(1 if side == "left" else 0, 0),
        dimensions=Vec2(PREVIEW_TEXT_WIDTH, PREVIEW_TEXT_HEIGHT),
        color=color,
        horizontal_align=HorizontalAlign.RIGHT if side == "left" else HorizontalAlign.LEFT,
        background=False,
    )
