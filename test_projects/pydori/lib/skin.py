from sonolus.script.sprite import RenderMode, StandardSprite, skin


@skin
class Skin:
    # Since this engine uses accurate 3D perspective rendering, it should always use lightweight rendering.
    render_mode = RenderMode.LIGHTWEIGHT

    cover: StandardSprite.STAGE_COVER

    lane: StandardSprite.LANE
    stage_left_border: StandardSprite.STAGE_LEFT_BORDER
    stage_right_border: StandardSprite.STAGE_RIGHT_BORDER
    stage_middle: StandardSprite.STAGE_MIDDLE
    judgment_line: StandardSprite.JUDGMENT_LINE
    slot: StandardSprite.NOTE_SLOT

    tap_note: StandardSprite.NOTE_HEAD_CYAN

    flick_note: StandardSprite.NOTE_HEAD_RED
    flick_arrow: StandardSprite.DIRECTIONAL_MARKER_RED

    right_flick_note: StandardSprite.NOTE_HEAD_YELLOW
    right_flick_arrow: StandardSprite.DIRECTIONAL_MARKER_YELLOW

    left_flick_note: StandardSprite.NOTE_HEAD_PURPLE
    left_flick_arrow: StandardSprite.DIRECTIONAL_MARKER_PURPLE

    hold_head_note: StandardSprite.NOTE_HEAD_GREEN
    hold_end_note: StandardSprite.NOTE_TAIL_GREEN
    hold_tick_note: StandardSprite.NOTE_TICK_GREEN
    hold_connector: StandardSprite.NOTE_CONNECTION_GREEN_SEAMLESS

    sim_line: StandardSprite.SIMULTANEOUS_CONNECTION_NEUTRAL_SEAMLESS

    # Preview mode bar lines
    bpm_change_line: StandardSprite.GRID_PURPLE
    timescale_change_line: StandardSprite.GRID_YELLOW
    measure_line: StandardSprite.GRID_NEUTRAL
    time_line: StandardSprite.GRID_CYAN
