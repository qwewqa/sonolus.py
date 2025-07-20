from sonolus.script.options import options, slider_option, toggle_option
from sonolus.script.text import StandardText


@options
class Options:
    speed: float = slider_option(
        name=StandardText.SPEED,
        standard=True,
        default=1,
        min=0.5,
        max=2,
        step=0.05,
        unit=StandardText.PERCENTAGE_UNIT,
        scope=None,
    )
    note_speed: float = slider_option(
        name=StandardText.NOTE_SPEED,
        default=10,
        min=1,
        max=20,
        step=0.05,
        unit=None,
        scope="pydori",
    )
    note_size: float = slider_option(
        name=StandardText.NOTE_SIZE,
        default=1,
        min=0.1,
        max=2,
        step=0.05,
        unit=StandardText.PERCENTAGE_UNIT,
        scope="pydori",
    )
    lane_width: float = slider_option(
        name=StandardText.LANE_SIZE,
        default=1,
        min=0.1,
        max=1.5,
        step=0.05,
        unit=StandardText.PERCENTAGE_UNIT,
        scope="pydori",
    )
    lane_length: float = slider_option(
        name="Lane Length",
        default=0.8,
        min=0.1,
        max=1,
        step=0.05,
        unit=StandardText.PERCENTAGE_UNIT,
        scope="pydori",
    )

    connector_alpha: float = slider_option(
        name=StandardText.CONNECTOR_ALPHA,
        default=0.8,
        min=0.1,
        max=1,
        step=0.05,
        unit=StandardText.PERCENTAGE_UNIT,
        scope="pydori",
    )

    note_effect_enabled: bool = toggle_option(
        name=StandardText.NOTE_EFFECT,
        default=True,
        scope="pydori",
    )
    lane_effect_enabled: bool = toggle_option(
        name=StandardText.LANE_EFFECT,
        default=True,
        scope="pydori",
    )

    sim_lines_enabled: bool = toggle_option(
        name=StandardText.SIMLINE,
        default=True,
        scope="pydori",
    )
    sim_line_alpha: float = slider_option(
        name=StandardText.SIMLINE_ALPHA,
        default=0.5,
        min=0.1,
        max=1,
        step=0.05,
        unit=StandardText.PERCENTAGE_UNIT,
        scope="pydori",
    )

    sfx_enabled: bool = toggle_option(
        name=StandardText.EFFECT,
        default=True,
        scope="pydori",
    )
    auto_sfx_enabled: bool = toggle_option(
        name=StandardText.EFFECT_AUTO,
        default=False,
        scope="pydori",
    )

    mirror: bool = toggle_option(
        name=StandardText.MIRROR,
        default=False,
        scope="pydori",
    )
