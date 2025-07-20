from pydori.lib.layer import LAYER_CONNECTOR, LAYER_SIM_LINE, get_z
from pydori.lib.layout import layout_hold_connector, layout_sim_line, note_y_to_alpha
from pydori.lib.options import Options
from pydori.lib.skin import Skin


def draw_hold_connector(
    lane_a: float,
    lane_b: float,
    y_a: float,
    y_b: float,
):
    layout = layout_hold_connector(lane_a, lane_b, y_a, y_b)
    sprite = Skin.hold_connector
    sprite.draw(layout, z=get_z(LAYER_CONNECTOR, lane=min(lane_a, lane_b), y=min(y_a, y_b)), a=Options.connector_alpha)


def draw_sim_line(
    lane_a: float,
    lane_b: float,
    y: float,
):
    if not Options.sim_lines_enabled:
        return
    alpha = Options.sim_line_alpha * note_y_to_alpha(y)
    if alpha <= 0:
        return
    layout = layout_sim_line(lane_a, lane_b, y)
    sprite = Skin.sim_line
    sprite.draw(layout, z=get_z(LAYER_SIM_LINE, lane=min(lane_a, lane_b), y=y), a=alpha)
