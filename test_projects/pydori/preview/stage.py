from math import floor

from pydori.lib.layer import LAYER_LANE, LAYER_PREVIEW_COVER, LAYER_STAGE, LAYER_TIME_LINE, get_z
from pydori.lib.layout import END_LANE, START_LANE
from pydori.lib.skin import Skin
from pydori.lib.ui import init_ui
from pydori.preview.layout import (
    PREVIEW_BAR_LINE_ALPHA,
    PREVIEW_COVER_ALPHA,
    PREVIEW_Y_MAX,
    PREVIEW_Y_MIN,
    PreviewData,
    PreviewLayout,
    init_preview_layout,
    layout_preview_bar_line,
    layout_preview_lane,
    layout_preview_stage_border_left,
    layout_preview_stage_border_right,
    print_at_time,
)
from sonolus.script.archetype import PreviewArchetype, callback
from sonolus.script.printing import PrintColor, PrintFormat
from sonolus.script.quad import Rect
from sonolus.script.runtime import screen


class PreviewStage(PreviewArchetype):
    """Draws the stage and time bar lines."""

    name = "Stage"

    @callback(order=1)
    def preprocess(self):
        init_ui()
        init_preview_layout()

    def render(self):
        self.render_border()
        self.render_lanes()
        self.render_cover()
        self.render_times()

    def render_border(self):
        for col in range(PreviewLayout.column_count):
            left_border_layout = layout_preview_stage_border_left(col)
            right_border_layout = layout_preview_stage_border_right(col)
            Skin.stage_left_border.draw(left_border_layout, z=LAYER_STAGE)
            Skin.stage_right_border.draw(right_border_layout, z=LAYER_STAGE)

    def render_lanes(self):
        for col in range(PreviewLayout.column_count):
            for lane in range(START_LANE, END_LANE + 1):
                layout = layout_preview_lane(lane, col)
                Skin.lane.draw(layout, z=LAYER_LANE)

    def render_cover(self):
        left_x = screen().l
        right_x = PreviewLayout.column_width * PreviewLayout.column_count + 1
        Skin.cover.draw(
            Rect(
                l=left_x,
                r=right_x,
                b=-1,
                t=PREVIEW_Y_MIN,
            ),
            z=get_z(LAYER_PREVIEW_COVER),
            a=PREVIEW_COVER_ALPHA,
        )
        Skin.cover.draw(
            Rect(
                l=left_x,
                r=right_x,
                b=PREVIEW_Y_MAX,
                t=1,
            ),
            z=get_z(LAYER_PREVIEW_COVER),
            a=PREVIEW_COVER_ALPHA,
        )

    def render_times(self):
        for time in range(floor(PreviewData.last_time) + 1):
            print_at_time(
                time,
                time,
                fmt=PrintFormat.TIME,
                decimal_places=0,
                color=PrintColor.CYAN,
                side="left",
            )
            Skin.time_line.draw(
                layout_preview_bar_line(time, "left_only"),
                z=LAYER_TIME_LINE,
                a=PREVIEW_BAR_LINE_ALPHA,
            )
