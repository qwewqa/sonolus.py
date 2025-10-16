from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any, NewType, dataclass_transform, get_origin

from sonolus.backend.ops import Op
from sonolus.script.array_like import ArrayLike, check_positive_index
from sonolus.script.debug import static_error
from sonolus.script.internal.impl import perf_meta_fn
from sonolus.script.internal.introspection import get_field_specifiers
from sonolus.script.internal.native import native_function
from sonolus.script.num import Num
from sonolus.script.quad import QuadLike, flatten_quad
from sonolus.script.record import Record
from sonolus.script.vec import Vec2


class Sprite(Record):
    """Skin sprite.

    Usage:
        ```python
        Sprite(id: int)
        ```
    """

    id: int

    @property
    def is_available(self) -> bool:
        """Check if the sprite is available."""
        return _has_skin_sprite(self.id)

    @perf_meta_fn
    def draw(self, quad: QuadLike, z: float = 0.0, a: float = 1.0):
        """Draw the sprite.

        Arguments:
            quad: The quad to draw the sprite on.
            z: The z-index of the sprite.
            a: The alpha of the sprite.
        """
        _draw(self.id, *flatten_quad(quad), z, a)

    @perf_meta_fn
    def draw_curved_b(self, quad: QuadLike, cp: Vec2, n: float, z: float = 0.0, a: float = 1.0):
        """Draw the sprite with a curved bottom with a quadratic Bézier curve.

        Arguments:
            quad: The quad to draw the sprite on.
            cp: The control point of the curve.
            n: The number of segments to approximate the curve (higher is smoother but more expensive).
            z: The z-index of the sprite.
            a: The alpha of the sprite.
        """
        _draw_curved_b(self.id, *flatten_quad(quad), z, a, n, *cp.tuple)

    @perf_meta_fn
    def draw_curved_t(self, quad: QuadLike, cp: Vec2, n: float, z: float = 0.0, a: float = 1.0):
        """Draw the sprite with a curved top with a quadratic Bézier curve.

        Arguments:
            quad: The quad to draw the sprite on.
            cp: The control point of the curve.
            n: The number of segments to approximate the curve (higher is smoother but more expensive).
            z: The z-index of the sprite.
            a: The alpha of the sprite.
        """
        _draw_curved_t(self.id, *flatten_quad(quad), z, a, n, *cp.tuple)

    @perf_meta_fn
    def draw_curved_l(self, quad: QuadLike, cp: Vec2, n: float, z: float = 0.0, a: float = 1.0):
        """Draw the sprite with a curved left side with a quadratic Bézier curve.

        Arguments:
            quad: The quad to draw the sprite on.
            cp: The control point of the curve.
            n: The number of segments to approximate the curve (higher is smoother but more expensive).
            z: The z-index of the sprite.
            a: The alpha of the sprite.
        """
        _draw_curved_l(self.id, *flatten_quad(quad), z, a, n, *cp.tuple)

    @perf_meta_fn
    def draw_curved_r(self, quad: QuadLike, cp: Vec2, n: float, z: float = 0.0, a: float = 1.0):
        """Draw the sprite with a curved right side with a quadratic Bézier curve.

        Arguments:
            quad: The quad to draw the sprite on.
            cp: The control point of the curve.
            n: The number of segments to approximate the curve (higher is smoother but more expensive).
            z: The z-index of the sprite.
            a: The alpha of the sprite.
        """
        _draw_curved_r(self.id, *flatten_quad(quad), z, a, n, *cp.tuple)

    @perf_meta_fn
    def draw_curved_bt(self, quad: QuadLike, cp1: Vec2, cp2: Vec2, n: float, z: float = 0.0, a: float = 1.0):
        """Draw the sprite with a curved bottom and top with a cubic Bézier curve.

        Arguments:
            quad: The quad to draw the sprite on.
            cp1: The control point of the bottom curve.
            cp2: The control point of the top curve.
            n: The number of segments to approximate the curve (higher is smoother but more expensive).
            z: The z-index of the sprite.
            a: The alpha of the sprite.
        """
        _draw_curved_bt(self.id, *flatten_quad(quad), z, a, n, *cp1.tuple, *cp2.tuple)

    @perf_meta_fn
    def draw_curved_lr(self, quad: QuadLike, cp1: Vec2, cp2: Vec2, n: float, z: float = 0.0, a: float = 1.0):
        """Draw the sprite with a curved left and right side with a cubic Bézier curve.

        Arguments:
            quad: The quad to draw the sprite on.
            cp1: The control point of the left curve.
            cp2: The control point of the right curve.
            n: The number of segments to approximate the curve (higher is smoother but more expensive).
            z: The z-index of the sprite.
            a: The alpha of the sprite.
        """
        _draw_curved_lr(self.id, *flatten_quad(quad), z, a, n, *cp1.tuple, *cp2.tuple)


class SpriteGroup(Record, ArrayLike[Sprite]):
    """A group of sprites.

    Usage:
        ```python
        SpriteGroup(start_id: int, size: int)
        ```
    """

    start_id: int
    size: int

    def __len__(self) -> int:
        return self.size

    def __getitem__(self, index: int) -> Sprite:
        check_positive_index(index, self.size)
        return Sprite(self.start_id + index)

    def get_unchecked(self, index: Num) -> Sprite:
        return Sprite(self.start_id + index)

    def __setitem__(self, index: int, value: Sprite) -> None:
        static_error("SpriteGroup is read-only")


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
) -> None:
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
) -> None:
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
) -> None:
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
) -> None:
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
) -> None:
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
) -> None:
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
) -> None:
    raise NotImplementedError


@dataclass
class SkinSprite:
    name: str


@dataclass
class SkinSpriteGroup:
    names: list[str]


def sprite(name: str) -> Any:
    """Define a sprite with the given name."""
    return SkinSprite(name)


def sprite_group(names: Iterable[str]) -> Any:
    """Define a sprite group with the given names."""
    return SkinSpriteGroup(list(names))


type Skin = NewType("Skin", Any)  # type: ignore


class RenderMode(StrEnum):
    """Render mode for sprites."""

    DEFAULT = "default"
    """Use the user's preferred render mode."""

    STANDARD = "standard"
    """Use the standard render mode with bilinear interpolation of textures."""

    LIGHTWEIGHT = "lightweight"
    """Use the lightweight render mode with projective interpolation of textures."""


@dataclass_transform(kw_only_default=True)
def skin[T](cls: type[T]) -> T | Skin:
    """Decorator to define a skin.

    Usage:
        ```python
        @skin
        class Skin:
            render_mode: RenderMode = RenderMode.LIGHTWEIGHT

            note: StandardSprite.NOTE_HEAD_RED
            other: Sprite = sprite("other")
            group_1: SpriteGroup = sprite_group(["one", "two", "three"])
            group_2: SpriteGroup = sprite_group(f"name_{i}" for i in range(10))
        ```
    """
    if len(cls.__bases__) != 1:
        raise ValueError("Skin class must not inherit from any class (except object)")
    instance = cls()
    names = []
    i = 0
    for name, annotation in get_field_specifiers(cls, skip={"render_mode"}).items():
        if get_origin(annotation) is not Annotated:
            raise TypeError(f"Invalid annotation for skin: {annotation} on field {name}")
        annotation_type = annotation.__args__[0]
        annotation_values = annotation.__metadata__
        if len(annotation_values) != 1:
            raise TypeError(f"Invalid annotation for skin: {annotation} on field {name}, too many annotation values")
        sprite_info = annotation_values[0]
        match sprite_info:
            case SkinSprite(name=sprite_name):
                if annotation_type is not Sprite:
                    raise TypeError(f"Invalid annotation for skin: {annotation} on field {name}, expected Sprite")
                names.append(sprite_name)
                setattr(instance, name, Sprite(i))
                i += 1
            case SkinSpriteGroup(names=sprite_names):
                if annotation_type is not SpriteGroup:
                    raise TypeError(f"Invalid annotation for skin: {annotation} on field {name}, expected SpriteGroup")
                start_id = i
                count = len(sprite_names)
                names.extend(sprite_names)
                setattr(instance, name, SpriteGroup(start_id, count))
                i += count
            case _:
                raise TypeError(
                    f"Invalid annotation for skin: {annotation} on field {name}, unknown sprite info, "
                    f"expected a skin() or sprite_group() specifier"
                )
    instance._sprites_ = names
    instance.render_mode = RenderMode(getattr(instance, "render_mode", RenderMode.DEFAULT))
    instance._is_comptime_value_ = True
    return instance


class StandardSprite:
    """Standard skin sprites."""

    NOTE_HEAD_NEUTRAL = Annotated[Sprite, sprite("#NOTE_HEAD_NEUTRAL")]
    NOTE_HEAD_RED = Annotated[Sprite, sprite("#NOTE_HEAD_RED")]
    NOTE_HEAD_GREEN = Annotated[Sprite, sprite("#NOTE_HEAD_GREEN")]
    NOTE_HEAD_BLUE = Annotated[Sprite, sprite("#NOTE_HEAD_BLUE")]
    NOTE_HEAD_YELLOW = Annotated[Sprite, sprite("#NOTE_HEAD_YELLOW")]
    NOTE_HEAD_PURPLE = Annotated[Sprite, sprite("#NOTE_HEAD_PURPLE")]
    NOTE_HEAD_CYAN = Annotated[Sprite, sprite("#NOTE_HEAD_CYAN")]

    NOTE_TICK_NEUTRAL = Annotated[Sprite, sprite("#NOTE_TICK_NEUTRAL")]
    NOTE_TICK_RED = Annotated[Sprite, sprite("#NOTE_TICK_RED")]
    NOTE_TICK_GREEN = Annotated[Sprite, sprite("#NOTE_TICK_GREEN")]
    NOTE_TICK_BLUE = Annotated[Sprite, sprite("#NOTE_TICK_BLUE")]
    NOTE_TICK_YELLOW = Annotated[Sprite, sprite("#NOTE_TICK_YELLOW")]
    NOTE_TICK_PURPLE = Annotated[Sprite, sprite("#NOTE_TICK_PURPLE")]
    NOTE_TICK_CYAN = Annotated[Sprite, sprite("#NOTE_TICK_CYAN")]

    NOTE_TAIL_NEUTRAL = Annotated[Sprite, sprite("#NOTE_TAIL_NEUTRAL")]
    NOTE_TAIL_RED = Annotated[Sprite, sprite("#NOTE_TAIL_RED")]
    NOTE_TAIL_GREEN = Annotated[Sprite, sprite("#NOTE_TAIL_GREEN")]
    NOTE_TAIL_BLUE = Annotated[Sprite, sprite("#NOTE_TAIL_BLUE")]
    NOTE_TAIL_YELLOW = Annotated[Sprite, sprite("#NOTE_TAIL_YELLOW")]
    NOTE_TAIL_PURPLE = Annotated[Sprite, sprite("#NOTE_TAIL_PURPLE")]
    NOTE_TAIL_CYAN = Annotated[Sprite, sprite("#NOTE_TAIL_CYAN")]

    NOTE_CONNECTION_NEUTRAL = Annotated[Sprite, sprite("#NOTE_CONNECTION_NEUTRAL")]
    NOTE_CONNECTION_RED = Annotated[Sprite, sprite("#NOTE_CONNECTION_RED")]
    NOTE_CONNECTION_GREEN = Annotated[Sprite, sprite("#NOTE_CONNECTION_GREEN")]
    NOTE_CONNECTION_BLUE = Annotated[Sprite, sprite("#NOTE_CONNECTION_BLUE")]
    NOTE_CONNECTION_YELLOW = Annotated[Sprite, sprite("#NOTE_CONNECTION_YELLOW")]
    NOTE_CONNECTION_PURPLE = Annotated[Sprite, sprite("#NOTE_CONNECTION_PURPLE")]
    NOTE_CONNECTION_CYAN = Annotated[Sprite, sprite("#NOTE_CONNECTION_CYAN")]

    NOTE_CONNECTION_NEUTRAL_SEAMLESS = Annotated[Sprite, sprite("#NOTE_CONNECTION_NEUTRAL_SEAMLESS")]
    NOTE_CONNECTION_RED_SEAMLESS = Annotated[Sprite, sprite("#NOTE_CONNECTION_RED_SEAMLESS")]
    NOTE_CONNECTION_GREEN_SEAMLESS = Annotated[Sprite, sprite("#NOTE_CONNECTION_GREEN_SEAMLESS")]
    NOTE_CONNECTION_BLUE_SEAMLESS = Annotated[Sprite, sprite("#NOTE_CONNECTION_BLUE_SEAMLESS")]
    NOTE_CONNECTION_YELLOW_SEAMLESS = Annotated[Sprite, sprite("#NOTE_CONNECTION_YELLOW_SEAMLESS")]
    NOTE_CONNECTION_PURPLE_SEAMLESS = Annotated[Sprite, sprite("#NOTE_CONNECTION_PURPLE_SEAMLESS")]
    NOTE_CONNECTION_CYAN_SEAMLESS = Annotated[Sprite, sprite("#NOTE_CONNECTION_CYAN_SEAMLESS")]

    SIMULTANEOUS_CONNECTION_NEUTRAL = Annotated[Sprite, sprite("#SIMULTANEOUS_CONNECTION_NEUTRAL")]
    SIMULTANEOUS_CONNECTION_RED = Annotated[Sprite, sprite("#SIMULTANEOUS_CONNECTION_RED")]
    SIMULTANEOUS_CONNECTION_GREEN = Annotated[Sprite, sprite("#SIMULTANEOUS_CONNECTION_GREEN")]
    SIMULTANEOUS_CONNECTION_BLUE = Annotated[Sprite, sprite("#SIMULTANEOUS_CONNECTION_BLUE")]
    SIMULTANEOUS_CONNECTION_YELLOW = Annotated[Sprite, sprite("#SIMULTANEOUS_CONNECTION_YELLOW")]
    SIMULTANEOUS_CONNECTION_PURPLE = Annotated[Sprite, sprite("#SIMULTANEOUS_CONNECTION_PURPLE")]
    SIMULTANEOUS_CONNECTION_CYAN = Annotated[Sprite, sprite("#SIMULTANEOUS_CONNECTION_CYAN")]

    SIMULTANEOUS_CONNECTION_NEUTRAL_SEAMLESS = Annotated[Sprite, sprite("#SIMULTANEOUS_CONNECTION_NEUTRAL_SEAMLESS")]
    SIMULTANEOUS_CONNECTION_RED_SEAMLESS = Annotated[Sprite, sprite("#SIMULTANEOUS_CONNECTION_RED_SEAMLESS")]
    SIMULTANEOUS_CONNECTION_GREEN_SEAMLESS = Annotated[Sprite, sprite("#SIMULTANEOUS_CONNECTION_GREEN_SEAMLESS")]
    SIMULTANEOUS_CONNECTION_BLUE_SEAMLESS = Annotated[Sprite, sprite("#SIMULTANEOUS_CONNECTION_BLUE_SEAMLESS")]
    SIMULTANEOUS_CONNECTION_YELLOW_SEAMLESS = Annotated[Sprite, sprite("#SIMULTANEOUS_CONNECTION_YELLOW_SEAMLESS")]
    SIMULTANEOUS_CONNECTION_PURPLE_SEAMLESS = Annotated[Sprite, sprite("#SIMULTANEOUS_CONNECTION_PURPLE_SEAMLESS")]
    SIMULTANEOUS_CONNECTION_CYAN_SEAMLESS = Annotated[Sprite, sprite("#SIMULTANEOUS_CONNECTION_CYAN_SEAMLESS")]

    DIRECTIONAL_MARKER_NEUTRAL = Annotated[Sprite, sprite("#DIRECTIONAL_MARKER_NEUTRAL")]
    DIRECTIONAL_MARKER_RED = Annotated[Sprite, sprite("#DIRECTIONAL_MARKER_RED")]
    DIRECTIONAL_MARKER_GREEN = Annotated[Sprite, sprite("#DIRECTIONAL_MARKER_GREEN")]
    DIRECTIONAL_MARKER_BLUE = Annotated[Sprite, sprite("#DIRECTIONAL_MARKER_BLUE")]
    DIRECTIONAL_MARKER_YELLOW = Annotated[Sprite, sprite("#DIRECTIONAL_MARKER_YELLOW")]
    DIRECTIONAL_MARKER_PURPLE = Annotated[Sprite, sprite("#DIRECTIONAL_MARKER_PURPLE")]
    DIRECTIONAL_MARKER_CYAN = Annotated[Sprite, sprite("#DIRECTIONAL_MARKER_CYAN")]

    SIMULTANEOUS_MARKER_NEUTRAL = Annotated[Sprite, sprite("#SIMULTANEOUS_MARKER_NEUTRAL")]
    SIMULTANEOUS_MARKER_RED = Annotated[Sprite, sprite("#SIMULTANEOUS_MARKER_RED")]
    SIMULTANEOUS_MARKER_GREEN = Annotated[Sprite, sprite("#SIMULTANEOUS_MARKER_GREEN")]
    SIMULTANEOUS_MARKER_BLUE = Annotated[Sprite, sprite("#SIMULTANEOUS_MARKER_BLUE")]
    SIMULTANEOUS_MARKER_YELLOW = Annotated[Sprite, sprite("#SIMULTANEOUS_MARKER_YELLOW")]
    SIMULTANEOUS_MARKER_PURPLE = Annotated[Sprite, sprite("#SIMULTANEOUS_MARKER_PURPLE")]
    SIMULTANEOUS_MARKER_CYAN = Annotated[Sprite, sprite("#SIMULTANEOUS_MARKER_CYAN")]

    STAGE_MIDDLE = Annotated[Sprite, sprite("#STAGE_MIDDLE")]
    STAGE_LEFT_BORDER = Annotated[Sprite, sprite("#STAGE_LEFT_BORDER")]
    STAGE_RIGHT_BORDER = Annotated[Sprite, sprite("#STAGE_RIGHT_BORDER")]
    STAGE_TOP_BORDER = Annotated[Sprite, sprite("#STAGE_TOP_BORDER")]
    STAGE_BOTTOM_BORDER = Annotated[Sprite, sprite("#STAGE_BOTTOM_BORDER")]

    STAGE_LEFT_BORDER_SEAMLESS = Annotated[Sprite, sprite("#STAGE_LEFT_BORDER_SEAMLESS")]
    STAGE_RIGHT_BORDER_SEAMLESS = Annotated[Sprite, sprite("#STAGE_RIGHT_BORDER_SEAMLESS")]
    STAGE_TOP_BORDER_SEAMLESS = Annotated[Sprite, sprite("#STAGE_TOP_BORDER_SEAMLESS")]
    STAGE_BOTTOM_BORDER_SEAMLESS = Annotated[Sprite, sprite("#STAGE_BOTTOM_BORDER_SEAMLESS")]

    STAGE_TOP_LEFT_CORNER = Annotated[Sprite, sprite("#STAGE_TOP_LEFT_CORNER")]
    STAGE_TOP_RIGHT_CORNER = Annotated[Sprite, sprite("#STAGE_TOP_RIGHT_CORNER")]
    STAGE_BOTTOM_LEFT_CORNER = Annotated[Sprite, sprite("#STAGE_BOTTOM_LEFT_CORNER")]
    STAGE_BOTTOM_RIGHT_CORNER = Annotated[Sprite, sprite("#STAGE_BOTTOM_RIGHT_CORNER")]

    LANE = Annotated[Sprite, sprite("#LANE")]
    LANE_SEAMLESS = Annotated[Sprite, sprite("#LANE_SEAMLESS")]
    LANE_ALTERNATIVE = Annotated[Sprite, sprite("#LANE_ALTERNATIVE")]
    LANE_ALTERNATIVE_SEAMLESS = Annotated[Sprite, sprite("#LANE_ALTERNATIVE_SEAMLESS")]

    JUDGMENT_LINE = Annotated[Sprite, sprite("#JUDGMENT_LINE")]
    NOTE_SLOT = Annotated[Sprite, sprite("#NOTE_SLOT")]
    STAGE_COVER = Annotated[Sprite, sprite("#STAGE_COVER")]

    GRID_NEUTRAL = Annotated[Sprite, sprite("#GRID_NEUTRAL")]
    GRID_RED = Annotated[Sprite, sprite("#GRID_RED")]
    GRID_GREEN = Annotated[Sprite, sprite("#GRID_GREEN")]
    GRID_BLUE = Annotated[Sprite, sprite("#GRID_BLUE")]
    GRID_YELLOW = Annotated[Sprite, sprite("#GRID_YELLOW")]
    GRID_PURPLE = Annotated[Sprite, sprite("#GRID_PURPLE")]
    GRID_CYAN = Annotated[Sprite, sprite("#GRID_CYAN")]


@skin
class EmptySkin:
    pass
