# Screen

Sonolus has a screen coordinate-system with y-coordinates ranging from `+1` at the top of the screen to `-1` at the 
bottom of the screen. The x-coordinate in the screen depend on the aspect ratio of a device, with `-aspect_ratio` 
at the left and `+aspect_ratio` at the right. When designing engines, it's important to keep in mind that aspect ratios
typically range from narrow (e.g. 4:3) to wide (e.g. 21:9) to ensure that nothing is cut off on devices with some
aspect ratios.

The screen coordinate system is used for drawing sprites, positioning particles, and positioning UI elements.

## Checking Screen Size

You can check aspect ratio using the [`aspect_ratio`][sonolus.script.runtime.aspect_ratio] function:

```python
from sonolus.script.runtime import aspect_ratio


the_aspect_ratio = aspect_ratio()
```

For convenience, the [`screen`][sonolus.script.runtime.screen] function returns a [`Rect`][sonolus.script.quad.Rect] 
that can be used to check the coordinates of each corner of the screen:

```python
from sonolus.script.runtime import screen

the_screen = screen()
top_left = the_screen.tl
```
