from dataclasses import dataclass, field
from enum import StrEnum


class UiMetric(StrEnum):
    """A metric to display on the UI."""

    ARCADE = "arcade"
    ARCADE_PERCENTAGE = "arcadePercentage"
    ACCURACY = "accuracy"
    ACCURACY_PERCENTAGE = "accuracyPercentage"
    LIFE = "life"
    PERFECT = "perfect"
    PERFECT_PERCENTAGE = "perfectPercentage"
    GREAT_GOOD_MISS = "greatGoodMiss"
    GREAT_GOOD_MISS_PERCENTAGE = "greatGoodMissPercentage"
    MISS = "miss"
    MISS_PERCENTAGE = "missPercentage"
    ERROR_HEATMAP = "errorHeatmap"


class UiJudgmentErrorStyle(StrEnum):
    """The style of the judgment error.

    The name of each member refers to what's used for positive (late) judgment errors.
    """

    NONE = "none"
    LATE = "late"
    EARLY = "early"  # Not really useful
    PLUS = "plus"
    MINUS = "minus"
    ARROW_UP = "arrowUp"
    ARROW_DOWN = "arrowDown"
    ARROW_LEFT = "arrowLeft"
    ARROW_RIGHT = "arrowRight"
    TRIANGLE_UP = "triangleUp"
    TRIANGLE_DOWN = "triangleDown"
    TRIANGLE_LEFT = "triangleLeft"
    TRIANGLE_RIGHT = "triangleRight"


class UiJudgmentErrorPlacement(StrEnum):
    """The placement of the judgment error."""

    LEFT = "left"
    RIGHT = "right"
    LEFT_RIGHT = "leftRight"
    TOP = "top"
    BOTTOM = "bottom"
    TOP_BOTTOM = "topBottom"
    CENTER = "center"


class EaseType(StrEnum):
    """The easing function to use."""

    LINEAR = "linear"
    NONE = "none"
    IN_SINE = "inSine"
    IN_QUAD = "inQuad"
    IN_CUBIC = "inCubic"
    IN_QUART = "inQuart"
    IN_QUINT = "inQuint"
    IN_EXPO = "inExpo"
    IN_CIRC = "inCirc"
    IN_BACK = "inBack"
    IN_ELASTIC = "inElastic"
    OUT_SINE = "outSine"
    OUT_QUAD = "outQuad"
    OUT_CUBIC = "outCubic"
    OUT_QUART = "outQuart"
    OUT_QUINT = "outQuint"
    OUT_EXPO = "outExpo"
    OUT_CIRC = "outCirc"
    OUT_BACK = "outBack"
    OUT_ELASTIC = "outElastic"
    IN_OUT_SINE = "inOutSine"
    IN_OUT_QUAD = "inOutQuad"
    IN_OUT_CUBIC = "inOutCubic"
    IN_OUT_QUART = "inOutQuart"
    IN_OUT_QUINT = "inOutQuint"
    IN_OUT_EXPO = "inOutExpo"
    IN_OUT_CIRC = "inOutCirc"
    IN_OUT_BACK = "inOutBack"
    IN_OUT_ELASTIC = "inOutElastic"
    OUT_IN_SINE = "outInSine"
    OUT_IN_QUAD = "outInQuad"
    OUT_IN_CUBIC = "outInCubic"
    OUT_IN_QUART = "outInQuart"
    OUT_IN_QUINT = "outInQuint"
    OUT_IN_EXPO = "outInExpo"
    OUT_IN_CIRC = "outInCirc"
    OUT_IN_BACK = "outInBack"
    OUT_IN_ELASTIC = "outInElastic"


@dataclass
class UiAnimationTween:
    """Tween animation configuration for UI elements.

    Args:
        start: The initial value.
        end: The final value.
        duration: The duration of the animation.
        ease: The easing function to use.
    """

    start: float
    end: float
    duration: float
    ease: EaseType

    def to_dict(self):
        return {
            "from": self.start,
            "to": self.end,
            "duration": self.duration,
            "ease": self.ease,
        }


@dataclass
class UiAnimation:
    """Animation configuration for UI elements.

    Args:
        scale: The animation applied to scale.
        alpha: The animation applied to alpha.
    """

    scale: UiAnimationTween
    alpha: UiAnimationTween

    def to_dict(self):
        return {
            "scale": self.scale.to_dict(),
            "alpha": self.alpha.to_dict(),
        }


@dataclass
class UiVisibility:
    """Visibility configuration for UI elements.

    Args:
        scale: The scale of the element.
        alpha: The alpha of the element.
    """

    scale: float = 1.0
    alpha: float = 1.0

    def to_dict(self):
        return {
            "scale": self.scale,
            "alpha": self.alpha,
        }


@dataclass
class UiConfig:
    """Configuration for UI elements.

    Args:
        scope: The scope of the configuration.
        primary_metric: The primary metric to display.
        secondary_metric: The secondary metric to display.
        menu_visibility: The visibility configuration for the menu.
        judgment_visibility: The visibility configuration for judgments.
        combo_visibility: The visibility configuration for the combo.
        primary_metric_visibility: The visibility configuration for the primary metric.
        secondary_metric_visibility: The visibility configuration for the secondary metric.
        progress_visibility: The visibility configuration for progress.
        tutorial_navigation_visibility: The visibility configuration for tutorial navigation.
        tutorial_instruction_visibility: The visibility configuration for tutorial instructions.
        judgment_animation: The animation configuration for judgments.
        combo_animation: The animation configuration for the combo.
        judgment_error_style: The style of the judgment error.
        judgment_error_placement: The placement of the judgment error.
        judgment_error_min: The minimum judgment error.
    """

    scope: str | None = None
    primary_metric: UiMetric = UiMetric.ARCADE
    secondary_metric: UiMetric = UiMetric.LIFE
    menu_visibility: UiVisibility = field(default_factory=UiVisibility)
    judgment_visibility: UiVisibility = field(default_factory=UiVisibility)
    combo_visibility: UiVisibility = field(default_factory=UiVisibility)
    primary_metric_visibility: UiVisibility = field(default_factory=UiVisibility)
    secondary_metric_visibility: UiVisibility = field(default_factory=UiVisibility)
    progress_visibility: UiVisibility = field(default_factory=UiVisibility)
    tutorial_navigation_visibility: UiVisibility = field(default_factory=UiVisibility)
    tutorial_instruction_visibility: UiVisibility = field(default_factory=UiVisibility)
    judgment_animation: UiAnimation = field(
        default_factory=lambda: UiAnimation(
            scale=UiAnimationTween(0, 1, 0.1, EaseType.OUT_CUBIC), alpha=UiAnimationTween(1, 0, 0.3, EaseType.NONE)
        )
    )
    combo_animation: UiAnimation = field(
        default_factory=lambda: UiAnimation(
            scale=UiAnimationTween(1.2, 1, 0.2, EaseType.IN_CUBIC), alpha=UiAnimationTween(1, 1, 0, EaseType.NONE)
        )
    )
    judgment_error_style: UiJudgmentErrorStyle = UiJudgmentErrorStyle.LATE
    judgment_error_placement: UiJudgmentErrorPlacement = UiJudgmentErrorPlacement.TOP
    judgment_error_min: float = 0.0

    def to_dict(self):
        data = {
            "primaryMetric": self.primary_metric,
            "secondaryMetric": self.secondary_metric,
            "menuVisibility": self.menu_visibility.to_dict(),
            "judgmentVisibility": self.judgment_visibility.to_dict(),
            "comboVisibility": self.combo_visibility.to_dict(),
            "primaryMetricVisibility": self.primary_metric_visibility.to_dict(),
            "secondaryMetricVisibility": self.secondary_metric_visibility.to_dict(),
            "progressVisibility": self.progress_visibility.to_dict(),
            "tutorialNavigationVisibility": self.tutorial_navigation_visibility.to_dict(),
            "tutorialInstructionVisibility": self.tutorial_instruction_visibility.to_dict(),
            "judgmentAnimation": self.judgment_animation.to_dict(),
            "comboAnimation": self.combo_animation.to_dict(),
            "judgmentErrorStyle": self.judgment_error_style,
            "judgmentErrorPlacement": self.judgment_error_placement,
            "judgmentErrorMin": self.judgment_error_min,
        }
        if self.scope is not None:
            data["scope"] = self.scope
        return data
