# z-index constants for ordering sprites.

LAYER_STAGE = 0
LAYER_LANE = 1
LAYER_JUDGE_LINE = 2

LAYER_CONNECTOR = 10

LAYER_PREVIEW_COVER = 20
LAYER_MEASURE_LINE = 21
LAYER_SIM_LINE = 22
LAYER_TIME_LINE = 23
LAYER_BPM_CHANGE_LINE = 24
LAYER_TIMESCALE_CHANGE_LINE = 25

LAYER_NOTE_HEAD = 30
LAYER_NOTE = 31
LAYER_ARROW = 32


def get_z(layer: int, lane: float = 0, y: float = 0) -> float:
    """Calculate z-index based on layer, lane, and y-coordinate.

    Lane and y are used to prevent z-fighting between sprites in the same layer.
    """
    return layer * 10000 + lane * 100 + y
