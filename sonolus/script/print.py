from enum import IntEnum

from sonolus.backend.ops import Op
from sonolus.script.internal.native import native_function
from sonolus.script.runtime import HorizontalAlign
from sonolus.script.vec import Vec2


class PrintFormat(IntEnum):
    """Print format."""

    NUMBER = 0
    PERCENTAGE = 1
    TIME = 10
    SCORE = 11
    BPM = 20
    TIMESCALE = 21
    BEAT_COUNT = 30
    MEASURE_COUNT = 31
    ENTITY_COUNT = 32


class PrintColor(IntEnum):
    """Print color."""

    THEME = -1
    NEUTRAL = 0
    RED = 1
    GREEN = 2
    BLUE = 3
    YELLOW = 4
    PURPLE = 5
    CYAN = 6


@native_function(Op.Print)
def _print(
    value: int | float,
    format: PrintFormat,  # noqa: A002
    decimal_places: int,
    anchor_x: float,
    anchor_y: float,
    pivot_x: float,
    pivot_y: float,
    width: float,
    height: float,
    rotation: float,
    color: PrintColor,
    alpha: float,
    horizontal_align: HorizontalAlign,
    background: bool,
) -> None:
    raise NotImplementedError


def print_number(
    value: int | float,
    *,
    fmt: PrintFormat,
    decimal_places: int = 0,
    anchor: Vec2,
    pivot: Vec2,
    dimensions: Vec2,
    rotation: float = 0,
    color: PrintColor = PrintColor.THEME,
    alpha: float = 1,
    horizontal_align: HorizontalAlign = HorizontalAlign.LEFT,
    background: bool = False,
):
    """Print a number.

    Only supported in preview mode.

    Args:
        value: The value to print.
        fmt: The print format.
        decimal_places: The number of decimal places.
        anchor: The anchor.
        pivot: The pivot.
        dimensions: The dimensions.
        rotation: The rotation.
        color: The color.
        alpha: The alpha.
        horizontal_align: The horizontal alignment.
        background: Whether to show a background.
    """
    _print(
        value,
        fmt,
        decimal_places,
        anchor.x,
        anchor.y,
        pivot.x,
        pivot.y,
        dimensions.x,
        dimensions.y,
        rotation,
        color,
        alpha,
        horizontal_align,
        background,
    )
