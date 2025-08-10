from pydori.lib.effect import SFX_DISTANCE, Effects
from pydori.lib.layer import LAYER_JUDGE_LINE, LAYER_LANE
from pydori.lib.layout import (
    END_LANE,
    LANE_COUNT_DIM,
    START_LANE,
    layout_judge_line,
    layout_lane,
    layout_stage_left_border,
    layout_stage_right_border,
)
from pydori.lib.options import Options
from pydori.lib.particle import Particles
from pydori.lib.skin import Skin
from sonolus.script.containers import ArrayMap
from sonolus.script.globals import level_data
from sonolus.script.quad import Quad

# The duration of the lane effect particle.
LANE_EFFECT_DURATION = 0.2


@level_data
class StageData:
    lane_layouts: ArrayMap[float, Quad, LANE_COUNT_DIM]
    left_border_layout: Quad
    right_border_layout: Quad
    judge_line_layout: Quad


def init_stage_data():
    for lane in range(START_LANE, END_LANE + 1):
        StageData.lane_layouts[lane] = layout_lane(lane)
    StageData.left_border_layout = layout_stage_left_border()
    StageData.right_border_layout = layout_stage_right_border()
    StageData.judge_line_layout = layout_judge_line()


def get_lane_quad(lane: float) -> Quad:
    """Return the precomputed layout quad for a given lane."""
    return StageData.lane_layouts[lane]


def draw_stage():
    """Draw the stage, including lanes, borders, and the judgment line."""
    for lane_quad in StageData.lane_layouts.values():
        Skin.lane.draw(lane_quad, z=LAYER_LANE)
    Skin.stage_left_border.draw(StageData.left_border_layout, z=LAYER_LANE)
    Skin.stage_right_border.draw(StageData.right_border_layout, z=LAYER_LANE)
    Skin.judgment_line.draw(StageData.judge_line_layout, z=LAYER_JUDGE_LINE)


def play_lane_sfx():
    if not Options.sfx_enabled:
        return
    Effects.stage.play(SFX_DISTANCE)


def schedule_lane_sfx(target_time: float):
    if not Options.sfx_enabled:
        return
    Effects.stage.schedule(target_time, SFX_DISTANCE)


def play_lane_particle(lane: float):
    if not Options.lane_effect_enabled:
        return
    Particles.lane.spawn(
        get_lane_quad(lane),
        duration=LANE_EFFECT_DURATION,
    )
