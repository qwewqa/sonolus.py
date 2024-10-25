from sonolus.script.comptime import Comptime
from sonolus.script.internal.context import ctx
from sonolus.script.internal.impl import meta_fn
from sonolus.script.record import Record


class Sprite[Name](Record):
    name: Comptime.of(Name, str)

    @property
    @meta_fn
    def id(self) -> int:
        if ctx():
            return ctx().map_sprite(self.name)
        else:
            return 0


class BuiltinSprite:
    NoteHeadNeutral = Sprite("#NOTE_HEAD_NEUTRAL")
    NoteHeadRed = Sprite("#NOTE_HEAD_RED")
    NoteHeadGreen = Sprite("#NOTE_HEAD_GREEN")
    NoteHeadBlue = Sprite("#NOTE_HEAD_BLUE")
    NoteHeadYellow = Sprite("#NOTE_HEAD_YELLOW")
    NoteHeadPurple = Sprite("#NOTE_HEAD_PURPLE")
    NoteHeadCyan = Sprite("#NOTE_HEAD_CYAN")

    NoteTickNeutral = Sprite("#NOTE_TICK_NEUTRAL")
    NoteTickRed = Sprite("#NOTE_TICK_RED")
    NoteTickGreen = Sprite("#NOTE_TICK_GREEN")
    NoteTickBlue = Sprite("#NOTE_TICK_BLUE")
    NoteTickYellow = Sprite("#NOTE_TICK_YELLOW")
    NoteTickPurple = Sprite("#NOTE_TICK_PURPLE")
    NoteTickCyan = Sprite("#NOTE_TICK_CYAN")

    NoteTailNeutral = Sprite("#NOTE_TAIL_NEUTRAL")
    NoteTailRed = Sprite("#NOTE_TAIL_RED")
    NoteTailGreen = Sprite("#NOTE_TAIL_GREEN")
    NoteTailBlue = Sprite("#NOTE_TAIL_BLUE")
    NoteTailYellow = Sprite("#NOTE_TAIL_YELLOW")
    NoteTailPurple = Sprite("#NOTE_TAIL_PURPLE")
    NoteTailCyan = Sprite("#NOTE_TAIL_CYAN")

    NoteConnectionNeutral = Sprite("#NOTE_CONNECTION_NEUTRAL")
    NoteConnectionRed = Sprite("#NOTE_CONNECTION_RED")
    NoteConnectionGreen = Sprite("#NOTE_CONNECTION_GREEN")
    NoteConnectionBlue = Sprite("#NOTE_CONNECTION_BLUE")
    NoteConnectionYellow = Sprite("#NOTE_CONNECTION_YELLOW")
    NoteConnectionPurple = Sprite("#NOTE_CONNECTION_PURPLE")
    NoteConnectionCyan = Sprite("#NOTE_CONNECTION_CYAN")

    NoteConnectionNeutralSeamless = Sprite("#NOTE_CONNECTION_NEUTRAL_SEAMLESS")
    NoteConnectionRedSeamless = Sprite("#NOTE_CONNECTION_RED_SEAMLESS")
    NoteConnectionGreenSeamless = Sprite("#NOTE_CONNECTION_GREEN_SEAMLESS")
    NoteConnectionBlueSeamless = Sprite("#NOTE_CONNECTION_BLUE_SEAMLESS")
    NoteConnectionYellowSeamless = Sprite("#NOTE_CONNECTION_YELLOW_SEAMLESS")
    NoteConnectionPurpleSeamless = Sprite("#NOTE_CONNECTION_PURPLE_SEAMLESS")
    NoteConnectionCyanSeamless = Sprite("#NOTE_CONNECTION_CYAN_SEAMLESS")

    SimultaneousConnectionNeutral = Sprite("#SIMULTANEOUS_CONNECTION_NEUTRAL")
    SimultaneousConnectionRed = Sprite("#SIMULTANEOUS_CONNECTION_RED")
    SimultaneousConnectionGreen = Sprite("#SIMULTANEOUS_CONNECTION_GREEN")
    SimultaneousConnectionBlue = Sprite("#SIMULTANEOUS_CONNECTION_BLUE")
    SimultaneousConnectionYellow = Sprite("#SIMULTANEOUS_CONNECTION_YELLOW")
    SimultaneousConnectionPurple = Sprite("#SIMULTANEOUS_CONNECTION_PURPLE")
    SimultaneousConnectionCyan = Sprite("#SIMULTANEOUS_CONNECTION_CYAN")

    SimultaneousConnectionNeutralSeamless = Sprite("#SIMULTANEOUS_CONNECTION_NEUTRAL_SEAMLESS")
    SimultaneousConnectionRedSeamless = Sprite("#SIMULTANEOUS_CONNECTION_RED_SEAMLESS")
    SimultaneousConnectionGreenSeamless = Sprite("#SIMULTANEOUS_CONNECTION_GREEN_SEAMLESS")
    SimultaneousConnectionBlueSeamless = Sprite("#SIMULTANEOUS_CONNECTION_BLUE_SEAMLESS")
    SimultaneousConnectionYellowSeamless = Sprite("#SIMULTANEOUS_CONNECTION_YELLOW_SEAMLESS")
    SimultaneousConnectionPurpleSeamless = Sprite("#SIMULTANEOUS_CONNECTION_PURPLE_SEAMLESS")
    SimultaneousConnectionCyanSeamless = Sprite("#SIMULTANEOUS_CONNECTION_CYAN_SEAMLESS")

    DirectionalMarkerNeutral = Sprite("#DIRECTIONAL_MARKER_NEUTRAL")
    DirectionalMarkerRed = Sprite("#DIRECTIONAL_MARKER_RED")
    DirectionalMarkerGreen = Sprite("#DIRECTIONAL_MARKER_GREEN")
    DirectionalMarkerBlue = Sprite("#DIRECTIONAL_MARKER_BLUE")
    DirectionalMarkerYellow = Sprite("#DIRECTIONAL_MARKER_YELLOW")
    DirectionalMarkerPurple = Sprite("#DIRECTIONAL_MARKER_PURPLE")
    DirectionalMarkerCyan = Sprite("#DIRECTIONAL_MARKER_CYAN")

    SimultaneousMarkerNeutral = Sprite("#SIMULTANEOUS_MARKER_NEUTRAL")
    SimultaneousMarkerRed = Sprite("#SIMULTANEOUS_MARKER_RED")
    SimultaneousMarkerGreen = Sprite("#SIMULTANEOUS_MARKER_GREEN")
    SimultaneousMarkerBlue = Sprite("#SIMULTANEOUS_MARKER_BLUE")
    SimultaneousMarkerYellow = Sprite("#SIMULTANEOUS_MARKER_YELLOW")
    SimultaneousMarkerPurple = Sprite("#SIMULTANEOUS_MARKER_PURPLE")
    SimultaneousMarkerCyan = Sprite("#SIMULTANEOUS_MARKER_CYAN")

    StageMiddle = Sprite("#STAGE_MIDDLE")
    StageLeftBorder = Sprite("#STAGE_LEFT_BORDER")
    StageRightBorder = Sprite("#STAGE_RIGHT_BORDER")
    StageTopBorder = Sprite("#STAGE_TOP_BORDER")
    StageBottomBorder = Sprite("#STAGE_BOTTOM_BORDER")

    StageLeftBorderSeamless = Sprite("#STAGE_LEFT_BORDER_SEAMLESS")
    StageRightBorderSeamless = Sprite("#STAGE_RIGHT_BORDER_SEAMLESS")
    StageTopBorderSeamless = Sprite("#STAGE_TOP_BORDER_SEAMLESS")
    StageBottomBorderSeamless = Sprite("#STAGE_BOTTOM_BORDER_SEAMLESS")

    StageTopLeftCorner = Sprite("#STAGE_TOP_LEFT_CORNER")
    StageTopRightCorner = Sprite("#STAGE_TOP_RIGHT_CORNER")
    StageBottomLeftCorner = Sprite("#STAGE_BOTTOM_LEFT_CORNER")
    StageBottomRightCorner = Sprite("#STAGE_BOTTOM_RIGHT_CORNER")

    Lane = Sprite("#LANE")
    LaneSeamless = Sprite("#LANE_SEAMLESS")
    LaneAlternative = Sprite("#LANE_ALTERNATIVE")
    LaneAlternativeSeamless = Sprite("#LANE_ALTERNATIVE_SEAMLESS")

    JudgmentLine = Sprite("#JUDGMENT_LINE")
    NoteSlot = Sprite("#NOTE_SLOT")
    StageCover = Sprite("#STAGE_COVER")

    GridNeutral = Sprite("#GRID_NEUTRAL")
    GridRed = Sprite("#GRID_RED")
    GridGreen = Sprite("#GRID_GREEN")
    GridBlue = Sprite("#GRID_BLUE")
    GridYellow = Sprite("#GRID_YELLOW")
    GridPurple = Sprite("#GRID_PURPLE")
    GridCyan = Sprite("#GRID_CYAN")
