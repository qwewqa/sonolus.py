from pydori.lib.layer import LAYER_ARROW, LAYER_CONNECTOR, LAYER_NOTE, get_z
from pydori.lib.layout import (
    layout_directional_flick_arrow,
    layout_flick_arrow,
    layout_hold_connector,
    layout_note_body,
    transform_vec,
)
from pydori.lib.note import NoteKind, get_note_arrow_sprite, get_note_body_sprite
from pydori.lib.skin import Skin
from sonolus.script.sprite import Sprite
from sonolus.script.transform import Transform2d
from sonolus.script.vec import Vec2

# Scale factor for tutorial intro notes
INTRO_SCALE = 3

# The y-coordinate tutorial intro notes are drawn at prior to being centered and scaled up
INTRO_DRAW_Y = 1

# Length of connector to show before and/or after hold notes before centering and scaling up
INTRO_CONNECTOR_LENGTH = 1


def draw_tutorial_intro_note(
    kind: NoteKind,
    direction: int = 0,
    lane: float = 0,
    is_hold_flick_end: bool = False,
):
    """Draw the intro for a tutorial note.

    This works by laying out a note normally (like in play mode), then translating and scaling it so it's
    larger and centered on the screen.
    """
    orig_center = transform_vec(Vec2(0, INTRO_DRAW_Y))
    post_transform = (
        Transform2d.new()
        .translate(-orig_center)  # Center the note on the screen
        .scale(Vec2(INTRO_SCALE, INTRO_SCALE))  # Scale it up
    )
    body_sprite = get_note_body_sprite(kind, direction)
    arrow_sprite = get_note_arrow_sprite(kind, direction)
    draw_tutorial_intro_note_body(body_sprite, post_transform, lane)
    match kind:
        case NoteKind.FLICK:
            draw_tutorial_intro_flick_arrow(arrow_sprite, post_transform, lane)
            if is_hold_flick_end:
                draw_tutorial_intro_connector(INTRO_DRAW_Y - INTRO_CONNECTOR_LENGTH, INTRO_DRAW_Y, post_transform, lane)
        case NoteKind.DIRECTIONAL_FLICK:
            draw_tutorial_intro_directional_flick_arrow(arrow_sprite, direction, post_transform, lane)
        case NoteKind.HOLD_HEAD:
            draw_tutorial_intro_connector(INTRO_DRAW_Y, INTRO_DRAW_Y + INTRO_CONNECTOR_LENGTH, post_transform, lane)
        case NoteKind.HOLD_END:
            draw_tutorial_intro_connector(INTRO_DRAW_Y - INTRO_CONNECTOR_LENGTH, INTRO_DRAW_Y, post_transform, lane)
        case NoteKind.HOLD_TICK:
            draw_tutorial_intro_connector(
                INTRO_DRAW_Y - INTRO_CONNECTOR_LENGTH, INTRO_DRAW_Y + INTRO_CONNECTOR_LENGTH, post_transform, lane
            )
        case _:
            pass


def draw_tutorial_intro_note_body(
    sprite: Sprite,
    post_transform: Transform2d,
    lane: float,
):
    original_layout = layout_note_body(lane, INTRO_DRAW_Y)
    layout = post_transform.transform_quad(original_layout)
    sprite.draw(layout, z=get_z(LAYER_NOTE))


def draw_tutorial_intro_flick_arrow(
    sprite: Sprite,
    post_transform: Transform2d,
    lane: float,
):
    original_layout = layout_flick_arrow(lane, INTRO_DRAW_Y, progress=0.5)
    layout = post_transform.transform_quad(original_layout)
    sprite.draw(layout, z=get_z(LAYER_ARROW))


def draw_tutorial_intro_directional_flick_arrow(
    sprite: Sprite,
    direction: int,
    post_transform: Transform2d,
    lane: float,
):
    for i in range(abs(direction)):
        original_layout = layout_directional_flick_arrow(lane, INTRO_DRAW_Y, direction, i, progress=0.5)
        layout = post_transform.transform_quad(original_layout)
        sprite.draw(layout, z=get_z(LAYER_ARROW))


def draw_tutorial_intro_connector(
    y_a: float,
    y_b: float,
    post_transform: Transform2d,
    lane: float,
):
    original_layout = layout_hold_connector(lane, lane, y_a, y_b)
    layout = post_transform.transform_quad(original_layout)
    sprite = Skin.hold_connector
    sprite.draw(layout, z=get_z(LAYER_CONNECTOR))
