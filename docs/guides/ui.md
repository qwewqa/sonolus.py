# UI

## UI Configuration

Defaults for UI configuration such as the visibility of UI elements as well as animation settings can be set with the
[`UiConfig`][sonolus.script.ui.UiConfig] class:

```python
from sonolus.script.ui import UiConfig, UiJudgmentErrorStyle, UiJudgmentErrorPlacement

ui_config = UiConfig(
    judgment_error_style=UiJudgmentErrorStyle.LATE,
    judgment_error_placement=UiJudgmentErrorPlacement.TOP,
    judgment_error_min=20.0,
)
```

## Runtime UI Configuration

The placement of UI elements during gameplay are set during preprocessing of each mode. Sonolus.py provides the
[`runtime_ui`][sonolus.script.runtime.runtime_ui] function to access the runtime UI configuration, which can be used
to set the placement of UI elements:

```python

from sonolus.script.runtime import runtime_ui, screen, HorizontalAlign
from sonolus.script.vec import Vec2


def init_ui():
    ui = runtime_ui()
    ui.menu.update(
        anchor=screen().tr + Vec2(-0.05, -0.05),
        pivot=Vec2(1, 1),
        dimensions=Vec2(0.15, 0.15) * ui.menu_config.scale,
        rotation=0,
        alpha=ui.menu_config.alpha,
        horizontal_align=HorizontalAlign.CENTER,
        background=True,
    )
    ui.judgment.update(
        anchor=Vec2(0, -0.25),
        pivot=Vec2(0.5, 0),
        dimensions=Vec2(0, 0.15) * ui.judgment_config.scale,
        rotation=0,
        alpha=ui.judgment_config.alpha,
        horizontal_align=HorizontalAlign.CENTER,
        background=False,
    )
    ui.combo_value.update(
        anchor=Vec2(screen().r * 0.7, 0),
        pivot=Vec2(0.5, 0),
        dimensions=Vec2(0, 0.2) * ui.combo_config.scale,
        rotation=0,
        alpha=ui.combo_config.alpha,
        horizontal_align=HorizontalAlign.CENTER,
        background=False,
    )
    ui.combo_text.update(
        anchor=Vec2(screen().r * 0.7, 0),
        pivot=Vec2(0.5, 1),
        dimensions=Vec2(0, 0.12) * ui.combo_config.scale,
        rotation=0,
        alpha=ui.combo_config.alpha,
        horizontal_align=HorizontalAlign.CENTER,
        background=False,
    )
    ui.primary_metric_bar.update(
        anchor=screen().tl + Vec2(0.05, -0.05),
        pivot=Vec2(0, 1),
        dimensions=Vec2(0.75, 0.15) * ui.primary_metric_config.scale,
        rotation=0,
        alpha=ui.primary_metric_config.alpha,
        horizontal_align=HorizontalAlign.LEFT,
        background=True,
    )
    ui.primary_metric_value.update(
        anchor=screen().tl + Vec2(0.05, -0.05) + Vec2(0.715, -0.035) * ui.primary_metric_config.scale,
        pivot=Vec2(0, 1),
        dimensions=Vec2(0, 0.08) * ui.primary_metric_config.scale,
        rotation=0,
        alpha=ui.primary_metric_config.alpha,
        horizontal_align=HorizontalAlign.RIGHT,
        background=False,
    )
    ui.secondary_metric_bar.update(
        anchor=screen().tr - Vec2(0.05, 0.05) - Vec2(0.05, 0) - Vec2(0.15, 0) * ui.menu_config.scale,
        pivot=Vec2(1, 1),
        dimensions=Vec2(0.75, 0.15) * ui.secondary_metric_config.scale,
        rotation=0,
        alpha=ui.secondary_metric_config.alpha,
        horizontal_align=HorizontalAlign.LEFT,
        background=True,
    )
    ui.secondary_metric_value.update(
        anchor=screen().tr
               - Vec2(0.05, 0.05)
               - Vec2(0.05, 0)
               - Vec2(0.15, 0) * ui.menu_config.scale
               - Vec2(0.035, 0.035) * ui.primary_metric_config.scale,
        pivot=Vec2(1, 1),
        dimensions=Vec2(0, 0.08) * ui.secondary_metric_config.scale,
        rotation=0,
        alpha=ui.secondary_metric_config.alpha,
        horizontal_align=HorizontalAlign.RIGHT,
        background=False,
    )
    ui.progress.update(
        anchor=screen().bl + Vec2(0.05, 0.05),
        pivot=Vec2(0, 0),
        dimensions=Vec2(screen().w - 0.1, 0.15 * ui.progress_config.scale),
        rotation=0,
        alpha=ui.progress_config.alpha,
        horizontal_align=HorizontalAlign.CENTER,
        background=True,
    )
    ui.previous.update(
        anchor=Vec2(screen().l + 0.05, 0),
        pivot=Vec2(0, 0.5),
        dimensions=Vec2(0.15, 0.15) * ui.navigation_config.scale,
        rotation=0,
        alpha=ui.navigation_config.alpha,
        background=True,
    )
    ui.next.update(
        anchor=Vec2(screen().r - 0.05, 0),
        pivot=Vec2(1, 0.5),
        dimensions=Vec2(0.15, 0.15) * ui.navigation_config.scale,
        rotation=0,
        alpha=ui.navigation_config.alpha,
        background=True,
    )
    ui.instruction.update(
        anchor=Vec2(0, 0.2),
        pivot=Vec2(0.5, 0.5),
        dimensions=Vec2(1.2, 0.15) * ui.instruction_config.scale,
        rotation=0,
        alpha=ui.instruction_config.alpha,
        background=True,
    )
```

Since some UI elements only appear in certain modes, their configuration is ignored in other modes.
