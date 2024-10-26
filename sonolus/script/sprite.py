import inspect
from typing import Annotated, get_origin

from sonolus.backend.ops import Op
from sonolus.script.graphics import QuadLike, flatten_quad
from sonolus.script.internal.native import native_function
from sonolus.script.record import Record
from sonolus.script.vec import Vec2


class Sprite(Record):
    id: int

    @property
    def is_available(self) -> bool:
        return _has_skin_sprite(self.id)

    def draw(self, quad: QuadLike, z: float = 0.0, a: float = 1.0):
        _draw(self.id, *flatten_quad(quad), z, a)

    def draw_curved_b(self, quad: QuadLike, cp: Vec2, n: float, z: float = 0.0, a: float = 1.0):
        _draw_curved_b(self.id, *flatten_quad(quad), z, a, n, *cp.tuple)

    def draw_curved_t(self, quad: QuadLike, cp: Vec2, n: float, z: float = 0.0, a: float = 1.0):
        _draw_curved_t(self.id, *flatten_quad(quad), z, a, n, *cp.tuple)

    def draw_curved_l(self, quad: QuadLike, cp: Vec2, n: float, z: float = 0.0, a: float = 1.0):
        _draw_curved_l(self.id, *flatten_quad(quad), z, a, n, *cp.tuple)

    def draw_curved_r(self, quad: QuadLike, cp: Vec2, n: float, z: float = 0.0, a: float = 1.0):
        _draw_curved_r(self.id, *flatten_quad(quad), z, a, n, *cp.tuple)

    def draw_curved_bt(self, quad: QuadLike, cp1: Vec2, cp2: Vec2, n: float, z: float = 0.0, a: float = 1.0):
        _draw_curved_bt(self.id, *flatten_quad(quad), z, a, n, *cp1.tuple, *cp2.tuple)

    def draw_curved_lr(self, quad: QuadLike, cp1: Vec2, cp2: Vec2, n: float, z: float = 0.0, a: float = 1.0):
        _draw_curved_lr(self.id, *flatten_quad(quad), z, a, n, *cp1.tuple, *cp2.tuple)


@native_function(Op.HasSkinSprite)
def _has_skin_sprite(sprite_id: int) -> bool:
    pass


@native_function(Op.Draw)
def _draw(
    sprite_id: int,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    x3: float,
    y3: float,
    x4: float,
    y4: float,
    z: float,
    a: float,
):
    pass


@native_function(Op.DrawCurvedB)
def _draw_curved_b(
    sprite_id: int,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    x3: float,
    y3: float,
    x4: float,
    y4: float,
    z: float,
    a: float,
    n: int,
    p: float,
    q: float,
):
    pass


@native_function(Op.DrawCurvedT)
def _draw_curved_t(
    sprite_id: int,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    x3: float,
    y3: float,
    x4: float,
    y4: float,
    z: float,
    a: float,
    n: int,
    p: float,
    q: float,
):
    pass


@native_function(Op.DrawCurvedL)
def _draw_curved_l(
    sprite_id: int,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    x3: float,
    y3: float,
    x4: float,
    y4: float,
    z: float,
    a: float,
    n: int,
    p: float,
    q: float,
):
    pass


@native_function(Op.DrawCurvedR)
def _draw_curved_r(
    sprite_id: int,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    x3: float,
    y3: float,
    x4: float,
    y4: float,
    z: float,
    a: float,
    n: int,
    p: float,
    q: float,
):
    pass


@native_function(Op.DrawCurvedBT)
def _draw_curved_bt(
    sprite_id: int,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    x3: float,
    y3: float,
    x4: float,
    y4: float,
    z: float,
    a: float,
    n: int,
    p1: float,
    q1: float,
    p2: float,
    q2: float,
):
    pass


@native_function(Op.DrawCurvedLR)
def _draw_curved_lr(
    sprite_id: int,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    x3: float,
    y3: float,
    x4: float,
    y4: float,
    z: float,
    a: float,
    n: int,
    p1: float,
    q1: float,
    p2: float,
    q2: float,
):
    pass


def skin[T](cls: type[T]) -> T:
    if len(cls.__bases__) != 1:
        raise ValueError("Skin class must not inherit from any class (except object)")
    instance = cls()
    names = []
    for i, (name, annotation) in enumerate(inspect.get_annotations(cls, eval_str=True).items()):
        if get_origin(annotation) is not Annotated:
            raise TypeError(f"Invalid annotation for skin: {annotation}")
        annotation_type = annotation.__args__[0]
        annotation_values = annotation.__metadata__
        if annotation_type is not Sprite:
            raise TypeError(f"Invalid annotation for skin: {annotation}, expected annotation of type Sprite")
        if len(annotation_values) != 1 or not isinstance(annotation_values[0], str):
            raise TypeError(f"Invalid annotation for skin: {annotation}, expected a single string annotation value")
        sprite_name = annotation_values[0]
        names.append(sprite_name)
        setattr(instance, name, Sprite(i))
    instance._sprites_ = names
    instance._is_comptime_value_ = True
    return instance


class StandardSprite:
    # Need to use Annotated[Sprite, str] instead of sprite(str) so type checkers work
    NoteHeadNeutral = Annotated[Sprite, "#NOTE_HEAD_NEUTRAL"]
    NoteHeadRed = Annotated[Sprite, "#NOTE_HEAD_RED"]
    NoteHeadGreen = Annotated[Sprite, "#NOTE_HEAD_GREEN"]
    NoteHeadBlue = Annotated[Sprite, "#NOTE_HEAD_BLUE"]
    NoteHeadYellow = Annotated[Sprite, "#NOTE_HEAD_YELLOW"]
    NoteHeadPurple = Annotated[Sprite, "#NOTE_HEAD_PURPLE"]
    NoteHeadCyan = Annotated[Sprite, "#NOTE_HEAD_CYAN"]

    NoteTickNeutral = Annotated[Sprite, "#NOTE_TICK_NEUTRAL"]
    NoteTickRed = Annotated[Sprite, "#NOTE_TICK_RED"]
    NoteTickGreen = Annotated[Sprite, "#NOTE_TICK_GREEN"]
    NoteTickBlue = Annotated[Sprite, "#NOTE_TICK_BLUE"]
    NoteTickYellow = Annotated[Sprite, "#NOTE_TICK_YELLOW"]
    NoteTickPurple = Annotated[Sprite, "#NOTE_TICK_PURPLE"]
    NoteTickCyan = Annotated[Sprite, "#NOTE_TICK_CYAN"]

    NoteTailNeutral = Annotated[Sprite, "#NOTE_TAIL_NEUTRAL"]
    NoteTailRed = Annotated[Sprite, "#NOTE_TAIL_RED"]
    NoteTailGreen = Annotated[Sprite, "#NOTE_TAIL_GREEN"]
    NoteTailBlue = Annotated[Sprite, "#NOTE_TAIL_BLUE"]
    NoteTailYellow = Annotated[Sprite, "#NOTE_TAIL_YELLOW"]
    NoteTailPurple = Annotated[Sprite, "#NOTE_TAIL_PURPLE"]
    NoteTailCyan = Annotated[Sprite, "#NOTE_TAIL_CYAN"]

    NoteConnectionNeutral = Annotated[Sprite, "#NOTE_CONNECTION_NEUTRAL"]
    NoteConnectionRed = Annotated[Sprite, "#NOTE_CONNECTION_RED"]
    NoteConnectionGreen = Annotated[Sprite, "#NOTE_CONNECTION_GREEN"]
    NoteConnectionBlue = Annotated[Sprite, "#NOTE_CONNECTION_BLUE"]
    NoteConnectionYellow = Annotated[Sprite, "#NOTE_CONNECTION_YELLOW"]
    NoteConnectionPurple = Annotated[Sprite, "#NOTE_CONNECTION_PURPLE"]
    NoteConnectionCyan = Annotated[Sprite, "#NOTE_CONNECTION_CYAN"]

    NoteConnectionNeutralSeamless = Annotated[Sprite, "#NOTE_CONNECTION_NEUTRAL_SEAMLESS"]
    NoteConnectionRedSeamless = Annotated[Sprite, "#NOTE_CONNECTION_RED_SEAMLESS"]
    NoteConnectionGreenSeamless = Annotated[Sprite, "#NOTE_CONNECTION_GREEN_SEAMLESS"]
    NoteConnectionBlueSeamless = Annotated[Sprite, "#NOTE_CONNECTION_BLUE_SEAMLESS"]
    NoteConnectionYellowSeamless = Annotated[Sprite, "#NOTE_CONNECTION_YELLOW_SEAMLESS"]
    NoteConnectionPurpleSeamless = Annotated[Sprite, "#NOTE_CONNECTION_PURPLE_SEAMLESS"]
    NoteConnectionCyanSeamless = Annotated[Sprite, "#NOTE_CONNECTION_CYAN_SEAMLESS"]

    SimultaneousConnectionNeutral = Annotated[Sprite, "#SIMULTANEOUS_CONNECTION_NEUTRAL"]
    SimultaneousConnectionRed = Annotated[Sprite, "#SIMULTANEOUS_CONNECTION_RED"]
    SimultaneousConnectionGreen = Annotated[Sprite, "#SIMULTANEOUS_CONNECTION_GREEN"]
    SimultaneousConnectionBlue = Annotated[Sprite, "#SIMULTANEOUS_CONNECTION_BLUE"]
    SimultaneousConnectionYellow = Annotated[Sprite, "#SIMULTANEOUS_CONNECTION_YELLOW"]
    SimultaneousConnectionPurple = Annotated[Sprite, "#SIMULTANEOUS_CONNECTION_PURPLE"]
    SimultaneousConnectionCyan = Annotated[Sprite, "#SIMULTANEOUS_CONNECTION_CYAN"]

    SimultaneousConnectionNeutralSeamless = Annotated[Sprite, "#SIMULTANEOUS_CONNECTION_NEUTRAL_SEAMLESS"]
    SimultaneousConnectionRedSeamless = Annotated[Sprite, "#SIMULTANEOUS_CONNECTION_RED_SEAMLESS"]
    SimultaneousConnectionGreenSeamless = Annotated[Sprite, "#SIMULTANEOUS_CONNECTION_GREEN_SEAMLESS"]
    SimultaneousConnectionBlueSeamless = Annotated[Sprite, "#SIMULTANEOUS_CONNECTION_BLUE_SEAMLESS"]
    SimultaneousConnectionYellowSeamless = Annotated[Sprite, "#SIMULTANEOUS_CONNECTION_YELLOW_SEAMLESS"]
    SimultaneousConnectionPurpleSeamless = Annotated[Sprite, "#SIMULTANEOUS_CONNECTION_PURPLE_SEAMLESS"]
    SimultaneousConnectionCyanSeamless = Annotated[Sprite, "#SIMULTANEOUS_CONNECTION_CYAN_SEAMLESS"]

    DirectionalMarkerNeutral = Annotated[Sprite, "#DIRECTIONAL_MARKER_NEUTRAL"]
    DirectionalMarkerRed = Annotated[Sprite, "#DIRECTIONAL_MARKER_RED"]
    DirectionalMarkerGreen = Annotated[Sprite, "#DIRECTIONAL_MARKER_GREEN"]
    DirectionalMarkerBlue = Annotated[Sprite, "#DIRECTIONAL_MARKER_BLUE"]
    DirectionalMarkerYellow = Annotated[Sprite, "#DIRECTIONAL_MARKER_YELLOW"]
    DirectionalMarkerPurple = Annotated[Sprite, "#DIRECTIONAL_MARKER_PURPLE"]
    DirectionalMarkerCyan = Annotated[Sprite, "#DIRECTIONAL_MARKER_CYAN"]

    SimultaneousMarkerNeutral = Annotated[Sprite, "#SIMULTANEOUS_MARKER_NEUTRAL"]
    SimultaneousMarkerRed = Annotated[Sprite, "#SIMULTANEOUS_MARKER_RED"]
    SimultaneousMarkerGreen = Annotated[Sprite, "#SIMULTANEOUS_MARKER_GREEN"]
    SimultaneousMarkerBlue = Annotated[Sprite, "#SIMULTANEOUS_MARKER_BLUE"]
    SimultaneousMarkerYellow = Annotated[Sprite, "#SIMULTANEOUS_MARKER_YELLOW"]
    SimultaneousMarkerPurple = Annotated[Sprite, "#SIMULTANEOUS_MARKER_PURPLE"]
    SimultaneousMarkerCyan = Annotated[Sprite, "#SIMULTANEOUS_MARKER_CYAN"]

    StageMiddle = Annotated[Sprite, "#STAGE_MIDDLE"]
    StageLeftBorder = Annotated[Sprite, "#STAGE_LEFT_BORDER"]
    StageRightBorder = Annotated[Sprite, "#STAGE_RIGHT_BORDER"]
    StageTopBorder = Annotated[Sprite, "#STAGE_TOP_BORDER"]
    StageBottomBorder = Annotated[Sprite, "#STAGE_BOTTOM_BORDER"]

    StageLeftBorderSeamless = Annotated[Sprite, "#STAGE_LEFT_BORDER_SEAMLESS"]
    StageRightBorderSeamless = Annotated[Sprite, "#STAGE_RIGHT_BORDER_SEAMLESS"]
    StageTopBorderSeamless = Annotated[Sprite, "#STAGE_TOP_BORDER_SEAMLESS"]
    StageBottomBorderSeamless = Annotated[Sprite, "#STAGE_BOTTOM_BORDER_SEAMLESS"]

    StageTopLeftCorner = Annotated[Sprite, "#STAGE_TOP_LEFT_CORNER"]
    StageTopRightCorner = Annotated[Sprite, "#STAGE_TOP_RIGHT_CORNER"]
    StageBottomLeftCorner = Annotated[Sprite, "#STAGE_BOTTOM_LEFT_CORNER"]
    StageBottomRightCorner = Annotated[Sprite, "#STAGE_BOTTOM_RIGHT_CORNER"]

    Lane = Annotated[Sprite, "#LANE"]
    LaneSeamless = Annotated[Sprite, "#LANE_SEAMLESS"]
    LaneAlternative = Annotated[Sprite, "#LANE_ALTERNATIVE"]
    LaneAlternativeSeamless = Annotated[Sprite, "#LANE_ALTERNATIVE_SEAMLESS"]

    JudgmentLine = Annotated[Sprite, "#JUDGMENT_LINE"]
    NoteSlot = Annotated[Sprite, "#NOTE_SLOT"]
    StageCover = Annotated[Sprite, "#STAGE_COVER"]

    GridNeutral = Annotated[Sprite, "#GRID_NEUTRAL"]
    GridRed = Annotated[Sprite, "#GRID_RED"]
    GridGreen = Annotated[Sprite, "#GRID_GREEN"]
    GridBlue = Annotated[Sprite, "#GRID_BLUE"]
    GridYellow = Annotated[Sprite, "#GRID_YELLOW"]
    GridPurple = Annotated[Sprite, "#GRID_PURPLE"]
    GridCyan = Annotated[Sprite, "#GRID_CYAN"]
