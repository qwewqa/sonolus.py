from pydori.lib.layer import LAYER_CONNECTOR, LAYER_SIM_LINE, get_z
from pydori.lib.options import Options
from pydori.lib.skin import Skin
from pydori.preview.layout import layout_preview_connector, layout_preview_sim_line, time_to_preview_col
from pydori.preview.note import PreviewNote
from sonolus.script.archetype import EntityRef, PreviewArchetype, imported


class PreviewHoldConnector(PreviewArchetype):
    """A connector for hold notes."""

    name = "HoldConnector"

    first_ref: EntityRef[PreviewNote] = imported()
    second_ref: EntityRef[PreviewNote] = imported()

    def render(self):
        first_col = time_to_preview_col(self.first.target_time)
        second_col = time_to_preview_col(self.second.target_time)
        for col in range(first_col, second_col + 1):
            Skin.hold_connector.draw(
                layout_preview_connector(
                    self.first.lane, self.second.lane, self.first.target_time, self.second.target_time, col
                ),
                z=get_z(
                    LAYER_CONNECTOR,
                    lane=min(self.first.lane, self.second.lane),
                    y=min(self.first.target_time, self.second.target_time),
                ),
                a=Options.connector_alpha,
            )

    @property
    def first(self):
        return self.first_ref.get()

    @property
    def second(self):
        return self.second_ref.get()


class PreviewSimLine(PreviewArchetype):
    """A line connecting two simultaneous notes."""

    name = "SimLine"

    first_ref: EntityRef[PreviewNote] = imported()
    second_ref: EntityRef[PreviewNote] = imported()

    def render(self):
        if Options.sim_lines_enabled:
            Skin.sim_line.draw(
                layout_preview_sim_line(self.first.lane, self.second.lane, self.first.target_time),
                z=get_z(
                    LAYER_SIM_LINE,
                    lane=min(self.first.lane, self.second.lane),
                    y=self.first.target_time,
                ),
                a=Options.sim_line_alpha,
            )

    @property
    def first(self):
        return self.first_ref.get()

    @property
    def second(self):
        return self.second_ref.get()
