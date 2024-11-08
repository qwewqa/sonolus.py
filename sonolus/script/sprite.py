from dataclasses import dataclass
from typing import Annotated, Any, NewType, dataclass_transform, get_origin

from sonolus.backend.ops import Op
from sonolus.script.graphics import QuadLike, flatten_quad
from sonolus.script.internal.introspection import get_field_specifiers
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
    raise NotImplementedError


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
    raise NotImplementedError


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
    raise NotImplementedError


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
    raise NotImplementedError


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
    raise NotImplementedError


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
    raise NotImplementedError


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
    raise NotImplementedError


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
    raise NotImplementedError


@dataclass
class SkinSprite:
    name: str


def skin_sprite(name: str) -> Any:
    return SkinSprite(name)


type Skin = NewType("Skin", Any)


@dataclass_transform()
def skin[T](cls: type[T]) -> T | Skin:
    if len(cls.__bases__) != 1:
        raise ValueError("Skin class must not inherit from any class (except object)")
    instance = cls()
    names = []
    for i, (name, annotation) in enumerate(get_field_specifiers(cls).items()):
        if get_origin(annotation) is not Annotated:
            raise TypeError(f"Invalid annotation for skin: {annotation}")
        annotation_type = annotation.__args__[0]
        annotation_values = annotation.__metadata__
        if annotation_type is not Sprite:
            raise TypeError(f"Invalid annotation for skin: {annotation}, expected annotation of type Sprite")
        if len(annotation_values) != 1 or not isinstance(annotation_values[0], SkinSprite):
            raise TypeError(f"Invalid annotation for skin: {annotation}, expected a single string annotation value")
        sprite_name = annotation_values[0].name
        names.append(sprite_name)
        setattr(instance, name, Sprite(i))
    instance._sprites_ = names
    instance._is_comptime_value_ = True
    return instance


class StandardSprite:
    NOTE_HEAD_NEUTRAL = Annotated[Sprite, skin_sprite("#NOTE_HEAD_NEUTRAL")]
    NOTE_HEAD_RED = Annotated[Sprite, skin_sprite("#NOTE_HEAD_RED")]
    NOTE_HEAD_GREEN = Annotated[Sprite, skin_sprite("#NOTE_HEAD_GREEN")]
    NOTE_HEAD_BLUE = Annotated[Sprite, skin_sprite("#NOTE_HEAD_BLUE")]
    NOTE_HEAD_YELLOW = Annotated[Sprite, skin_sprite("#NOTE_HEAD_YELLOW")]
    NOTE_HEAD_PURPLE = Annotated[Sprite, skin_sprite("#NOTE_HEAD_PURPLE")]
    NOTE_HEAD_CYAN = Annotated[Sprite, skin_sprite("#NOTE_HEAD_CYAN")]

    NOTE_TICK_NEUTRAL = Annotated[Sprite, skin_sprite("#NOTE_TICK_NEUTRAL")]
    NOTE_TICK_RED = Annotated[Sprite, skin_sprite("#NOTE_TICK_RED")]
    NOTE_TICK_GREEN = Annotated[Sprite, skin_sprite("#NOTE_TICK_GREEN")]
    NOTE_TICK_BLUE = Annotated[Sprite, skin_sprite("#NOTE_TICK_BLUE")]
    NOTE_TICK_YELLOW = Annotated[Sprite, skin_sprite("#NOTE_TICK_YELLOW")]
    NOTE_TICK_PURPLE = Annotated[Sprite, skin_sprite("#NOTE_TICK_PURPLE")]
    NOTE_TICK_CYAN = Annotated[Sprite, skin_sprite("#NOTE_TICK_CYAN")]

    NOTE_TAIL_NEUTRAL = Annotated[Sprite, skin_sprite("#NOTE_TAIL_NEUTRAL")]
    NOTE_TAIL_RED = Annotated[Sprite, skin_sprite("#NOTE_TAIL_RED")]
    NOTE_TAIL_GREEN = Annotated[Sprite, skin_sprite("#NOTE_TAIL_GREEN")]
    NOTE_TAIL_BLUE = Annotated[Sprite, skin_sprite("#NOTE_TAIL_BLUE")]
    NOTE_TAIL_YELLOW = Annotated[Sprite, skin_sprite("#NOTE_TAIL_YELLOW")]
    NOTE_TAIL_PURPLE = Annotated[Sprite, skin_sprite("#NOTE_TAIL_PURPLE")]
    NOTE_TAIL_CYAN = Annotated[Sprite, skin_sprite("#NOTE_TAIL_CYAN")]

    NOTE_CONNECTION_NEUTRAL = Annotated[Sprite, skin_sprite("#NOTE_CONNECTION_NEUTRAL")]
    NOTE_CONNECTION_RED = Annotated[Sprite, skin_sprite("#NOTE_CONNECTION_RED")]
    NOTE_CONNECTION_GREEN = Annotated[Sprite, skin_sprite("#NOTE_CONNECTION_GREEN")]
    NOTE_CONNECTION_BLUE = Annotated[Sprite, skin_sprite("#NOTE_CONNECTION_BLUE")]
    NOTE_CONNECTION_YELLOW = Annotated[Sprite, skin_sprite("#NOTE_CONNECTION_YELLOW")]
    NOTE_CONNECTION_PURPLE = Annotated[Sprite, skin_sprite("#NOTE_CONNECTION_PURPLE")]
    NOTE_CONNECTION_CYAN = Annotated[Sprite, skin_sprite("#NOTE_CONNECTION_CYAN")]

    NOTE_CONNECTION_NEUTRAL_SEAMLESS = Annotated[Sprite, skin_sprite("#NOTE_CONNECTION_NEUTRAL_SEAMLESS")]
    NOTE_CONNECTION_RED_SEAMLESS = Annotated[Sprite, skin_sprite("#NOTE_CONNECTION_RED_SEAMLESS")]
    NOTE_CONNECTION_GREEN_SEAMLESS = Annotated[Sprite, skin_sprite("#NOTE_CONNECTION_GREEN_SEAMLESS")]
    NOTE_CONNECTION_BLUE_SEAMLESS = Annotated[Sprite, skin_sprite("#NOTE_CONNECTION_BLUE_SEAMLESS")]
    NOTE_CONNECTION_YELLOW_SEAMLESS = Annotated[Sprite, skin_sprite("#NOTE_CONNECTION_YELLOW_SEAMLESS")]
    NOTE_CONNECTION_PURPLE_SEAMLESS = Annotated[Sprite, skin_sprite("#NOTE_CONNECTION_PURPLE_SEAMLESS")]
    NOTE_CONNECTION_CYAN_SEAMLESS = Annotated[Sprite, skin_sprite("#NOTE_CONNECTION_CYAN_SEAMLESS")]

    SIMULTANEOUS_CONNECTION_NEUTRAL = Annotated[Sprite, skin_sprite("#SIMULTANEOUS_CONNECTION_NEUTRAL")]
    SIMULTANEOUS_CONNECTION_RED = Annotated[Sprite, skin_sprite("#SIMULTANEOUS_CONNECTION_RED")]
    SIMULTANEOUS_CONNECTION_GREEN = Annotated[Sprite, skin_sprite("#SIMULTANEOUS_CONNECTION_GREEN")]
    SIMULTANEOUS_CONNECTION_BLUE = Annotated[Sprite, skin_sprite("#SIMULTANEOUS_CONNECTION_BLUE")]
    SIMULTANEOUS_CONNECTION_YELLOW = Annotated[Sprite, skin_sprite("#SIMULTANEOUS_CONNECTION_YELLOW")]
    SIMULTANEOUS_CONNECTION_PURPLE = Annotated[Sprite, skin_sprite("#SIMULTANEOUS_CONNECTION_PURPLE")]
    SIMULTANEOUS_CONNECTION_CYAN = Annotated[Sprite, skin_sprite("#SIMULTANEOUS_CONNECTION_CYAN")]

    SIMULTANEOUS_CONNECTION_NEUTRAL_SEAMLESS = Annotated[
        Sprite, skin_sprite("#SIMULTANEOUS_CONNECTION_NEUTRAL_SEAMLESS")
    ]
    SIMULTANEOUS_CONNECTION_RED_SEAMLESS = Annotated[Sprite, skin_sprite("#SIMULTANEOUS_CONNECTION_RED_SEAMLESS")]
    SIMULTANEOUS_CONNECTION_GREEN_SEAMLESS = Annotated[Sprite, skin_sprite("#SIMULTANEOUS_CONNECTION_GREEN_SEAMLESS")]
    SIMULTANEOUS_CONNECTION_BLUE_SEAMLESS = Annotated[Sprite, skin_sprite("#SIMULTANEOUS_CONNECTION_BLUE_SEAMLESS")]
    SIMULTANEOUS_CONNECTION_YELLOW_SEAMLESS = Annotated[Sprite, skin_sprite("#SIMULTANEOUS_CONNECTION_YELLOW_SEAMLESS")]
    SIMULTANEOUS_CONNECTION_PURPLE_SEAMLESS = Annotated[Sprite, skin_sprite("#SIMULTANEOUS_CONNECTION_PURPLE_SEAMLESS")]
    SIMULTANEOUS_CONNECTION_CYAN_SEAMLESS = Annotated[Sprite, skin_sprite("#SIMULTANEOUS_CONNECTION_CYAN_SEAMLESS")]

    DIRECTIONAL_MARKER_NEUTRAL = Annotated[Sprite, skin_sprite("#DIRECTIONAL_MARKER_NEUTRAL")]
    DIRECTIONAL_MARKER_RED = Annotated[Sprite, skin_sprite("#DIRECTIONAL_MARKER_RED")]
    DIRECTIONAL_MARKER_GREEN = Annotated[Sprite, skin_sprite("#DIRECTIONAL_MARKER_GREEN")]
    DIRECTIONAL_MARKER_BLUE = Annotated[Sprite, skin_sprite("#DIRECTIONAL_MARKER_BLUE")]
    DIRECTIONAL_MARKER_YELLOW = Annotated[Sprite, skin_sprite("#DIRECTIONAL_MARKER_YELLOW")]
    DIRECTIONAL_MARKER_PURPLE = Annotated[Sprite, skin_sprite("#DIRECTIONAL_MARKER_PURPLE")]
    DIRECTIONAL_MARKER_CYAN = Annotated[Sprite, skin_sprite("#DIRECTIONAL_MARKER_CYAN")]

    SIMULTANEOUS_MARKER_NEUTRAL = Annotated[Sprite, skin_sprite("#SIMULTANEOUS_MARKER_NEUTRAL")]
    SIMULTANEOUS_MARKER_RED = Annotated[Sprite, skin_sprite("#SIMULTANEOUS_MARKER_RED")]
    SIMULTANEOUS_MARKER_GREEN = Annotated[Sprite, skin_sprite("#SIMULTANEOUS_MARKER_GREEN")]
    SIMULTANEOUS_MARKER_BLUE = Annotated[Sprite, skin_sprite("#SIMULTANEOUS_MARKER_BLUE")]
    SIMULTANEOUS_MARKER_YELLOW = Annotated[Sprite, skin_sprite("#SIMULTANEOUS_MARKER_YELLOW")]
    SIMULTANEOUS_MARKER_PURPLE = Annotated[Sprite, skin_sprite("#SIMULTANEOUS_MARKER_PURPLE")]
    SIMULTANEOUS_MARKER_CYAN = Annotated[Sprite, skin_sprite("#SIMULTANEOUS_MARKER_CYAN")]

    STAGE_MIDDLE = Annotated[Sprite, skin_sprite("#STAGE_MIDDLE")]
    STAGE_LEFT_BORDER = Annotated[Sprite, skin_sprite("#STAGE_LEFT_BORDER")]
    STAGE_RIGHT_BORDER = Annotated[Sprite, skin_sprite("#STAGE_RIGHT_BORDER")]
    STAGE_TOP_BORDER = Annotated[Sprite, skin_sprite("#STAGE_TOP_BORDER")]
    STAGE_BOTTOM_BORDER = Annotated[Sprite, skin_sprite("#STAGE_BOTTOM_BORDER")]

    STAGE_LEFT_BORDER_SEAMLESS = Annotated[Sprite, skin_sprite("#STAGE_LEFT_BORDER_SEAMLESS")]
    STAGE_RIGHT_BORDER_SEAMLESS = Annotated[Sprite, skin_sprite("#STAGE_RIGHT_BORDER_SEAMLESS")]
    STAGE_TOP_BORDER_SEAMLESS = Annotated[Sprite, skin_sprite("#STAGE_TOP_BORDER_SEAMLESS")]
    STAGE_BOTTOM_BORDER_SEAMLESS = Annotated[Sprite, skin_sprite("#STAGE_BOTTOM_BORDER_SEAMLESS")]

    STAGE_TOP_LEFT_CORNER = Annotated[Sprite, skin_sprite("#STAGE_TOP_LEFT_CORNER")]
    STAGE_TOP_RIGHT_CORNER = Annotated[Sprite, skin_sprite("#STAGE_TOP_RIGHT_CORNER")]
    STAGE_BOTTOM_LEFT_CORNER = Annotated[Sprite, skin_sprite("#STAGE_BOTTOM_LEFT_CORNER")]
    STAGE_BOTTOM_RIGHT_CORNER = Annotated[Sprite, skin_sprite("#STAGE_BOTTOM_RIGHT_CORNER")]

    LANE = Annotated[Sprite, skin_sprite("#LANE")]
    LANE_SEAMLESS = Annotated[Sprite, skin_sprite("#LANE_SEAMLESS")]
    LANE_ALTERNATIVE = Annotated[Sprite, skin_sprite("#LANE_ALTERNATIVE")]
    LANE_ALTERNATIVE_SEAMLESS = Annotated[Sprite, skin_sprite("#LANE_ALTERNATIVE_SEAMLESS")]

    JUDGMENT_LINE = Annotated[Sprite, skin_sprite("#JUDGMENT_LINE")]
    NOTE_SLOT = Annotated[Sprite, skin_sprite("#NOTE_SLOT")]
    STAGE_COVER = Annotated[Sprite, skin_sprite("#STAGE_COVER")]

    GRID_NEUTRAL = Annotated[Sprite, skin_sprite("#GRID_NEUTRAL")]
    GRID_RED = Annotated[Sprite, skin_sprite("#GRID_RED")]
    GRID_GREEN = Annotated[Sprite, skin_sprite("#GRID_GREEN")]
    GRID_BLUE = Annotated[Sprite, skin_sprite("#GRID_BLUE")]
    GRID_YELLOW = Annotated[Sprite, skin_sprite("#GRID_YELLOW")]
    GRID_PURPLE = Annotated[Sprite, skin_sprite("#GRID_PURPLE")]
    GRID_CYAN = Annotated[Sprite, skin_sprite("#GRID_CYAN")]


@skin
class EmptySkin:
    pass
