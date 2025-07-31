# Skin & Sprites

[Skins][sonolus.script.sprite.skin] define [sprites][sonolus.script.sprite.Sprite] (images) that can be drawn in 
Sonolus.

## Declaration

Skins are declared with the [`@skin`][sonolus.script.sprite.skin] decorator. Standard Sonolus sprites are declared by
using a value from [`StandardSprite`][sonolus.script.sprite.StandardSprite] as the type hint. Custom sprites may also be
defined using the [`Sprite`][sonolus.script.sprite.Sprite] type hint and the [`sprite`][sonolus.script.sprite.sprite]
function.

```python
from sonolus.script.sprite import skin, StandardSprite, sprite, Sprite


@skin
class Skin:
    tap_note: StandardSprite.NOTE_HEAD_CYAN

    custom_sprite: Sprite = sprite("name_of_custom_sprite")
```

## Render Mode

The skin also defines the render mode for sprites. This is done by defining a `render_mode` attribute in a skin class
to a value from [`RenderMode`][sonolus.script.sprite.RenderMode].

```python
from sonolus.script.sprite import skin, RenderMode


@skin
class Skin:
    render_mode = RenderMode.LIGHTWEIGHT
    ...
```

The three render modes available are:

- [`RenderMode.LIGHTWEIGHT`][sonolus.script.sprite.RenderMode.LIGHTWEIGHT]: Less taxing and well suited for
engines implementing realistic 3D-like graphs, but is less accurate for some engines.
- [`RenderMode.STANDARD`][sonolus.script.sprite.RenderMode.STANDARD]: Slower, but more accurate for some engines and
works better with some special cases such as drawing sprites in a triangular shape.
- [`RenderMode.DEFAULT`][sonolus.script.sprite.RenderMode.DEFAULT]: Use either `LIGHTWEIGHT` or `STANDARD` based on
the user's preferences. This is the default mode, and is suitable for engines where `STANDARD` is preferred, but
are still playable in `LIGHTWEIGHT` when more performance is desired.

## Drawing a Sprite

To draw a sprite, you can use the [`draw`][sonolus.script.sprite.Sprite.draw] method of the sprite. This method
accepts a [`Quad`][sonolus.script.quad.Quad] object that defines the position of the sprite on the screen, as well as
an optional z-index to control the rendering order of the sprite and an alpha (transparency) value:

```python
from sonolus.script.sprite import Sprite
from sonolus.script.quad import Quad
from sonolus.script.vec import Vec2

my_sprite: Sprite = ...
my_quad = Quad(
    tl=Vec2(-0.5, 0.5),  # Top-left corner of the sprite
    tr=Vec2(0.5, 0.5),   # Top-right corner of the sprite
    bl=Vec2(-0.5, -0.5), # Bottom-left corner of the sprite
    br=Vec2(0.5, -0.5)   # Bottom-right corner of the sprite
)
my_sprite.draw(my_quad, z=123.4, a=1.0)
```

Z-index (`z`) is important to set correctly, as it ensures that sprites overlap in the correct order. It's especially
important for two sprites that may overlap to have different z-index values, or they may conflict and render 
incorrectly ([z-fighting](https://en.wikipedia.org/wiki/Z-fighting)).

Alpha (`a`) is a value between `0.0` (fully transparent) and `1.0` (fully opaque). It controls the transparency of the
sprite when drawn. If not provided, it defaults to `1.0`.

## Checking Sprite Availability

Some skins may not have some sprites available, especially custom sprites. To check if a sprite is available, you can
use the [`is_available`][sonolus.script.sprite.Sprite.is_available] property:

```python
from sonolus.script.sprite import Sprite

my_sprite: Sprite = ...

if my_sprite.is_available:
    # The sprite is available, you can use it.
    ...
else:
    # The sprite is not available, handle the case accordingly.
    # E.g. fall back to a different sprite or skip drawing it.
    ...
```
