from dataclasses import dataclass, field
from typing import Literal

UiMetric = Literal[
    "arcade",
    "arcadePercentage",
    "accuracy",
    "accuracyPercentage",
    "life",
    "perfect",
    "perfectPercentage",
    "greatGoodMiss",
    "greatGoodMissPercentage",
    "miss",
    "missPercentage",
    "errorHeatmap",
]

UiJudgmentErrorStyle = Literal[
    "none",
    "plus",
    "minus",
    "arrowUp",
    "arrowDown",
    "arrowLeft",
    "arrowRight",
    "triangleUp",
    "triangleDown",
    "triangleLeft",
    "triangleRight",
]

UiJudgmentErrorPlacement = Literal["both", "left", "right"]

EaseType = Literal[
    "linear",
    "none",
    "inSine",
    "inQuad",
    "inCubic",
    "inQuart",
    "inQuint",
    "inExpo",
    "inCirc",
    "inBack",
    "inElastic",
    "outSine",
    "outQuad",
    "outCubic",
    "outQuart",
    "outQuint",
    "outExpo",
    "outCirc",
    "outBack",
    "outElastic",
    "inOutSine",
    "inOutQuad",
    "inOutCubic",
    "inOutQuart",
    "inOutQuint",
    "inOutExpo",
    "inOutCirc",
    "inOutBack",
    "inOutElastic",
    "outInSine",
    "outInQuad",
    "outInCubic",
    "outInQuart",
    "outInQuint",
    "outInExpo",
    "outInCirc",
    "outInBack",
    "outInElastic",
]


@dataclass
class UiAnimationTween:
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
    scale: UiAnimationTween = field(default_factory=lambda: UiAnimationTween(1, 1, 0, "none"))
    alpha: UiAnimationTween = field(default_factory=lambda: UiAnimationTween(1, 0, 0.2, "outCubic"))

    def to_dict(self):
        return {
            "scale": self.scale.to_dict(),
            "alpha": self.alpha.to_dict(),
        }


@dataclass
class UiVisibility:
    scale: float = 1.0
    alpha: float = 1.0

    def to_dict(self):
        return {
            "scale": self.scale,
            "alpha": self.alpha,
        }


@dataclass
class UiConfig:
    scope: str | None = None
    primary_metric: UiMetric = "arcade"
    secondary_metric: UiMetric = "life"
    menu_visibility: UiVisibility = field(default_factory=UiVisibility)
    judgment_visibility: UiVisibility = field(default_factory=UiVisibility)
    combo_visibility: UiVisibility = field(default_factory=UiVisibility)
    primary_metric_visibility: UiVisibility = field(default_factory=UiVisibility)
    secondary_metric_visibility: UiVisibility = field(default_factory=UiVisibility)
    progress_visibility: UiVisibility = field(default_factory=UiVisibility)
    tutorial_navigation_visibility: UiVisibility = field(default_factory=UiVisibility)
    tutorial_instruction_visibility: UiVisibility = field(default_factory=UiVisibility)
    judgment_animation: UiAnimation = field(default_factory=UiAnimation)
    combo_animation: UiAnimation = field(
        default_factory=lambda: UiAnimation(
            scale=UiAnimationTween(1.2, 1, 0.2, "inCubic"), alpha=UiAnimationTween(1, 1, 0, "none")
        )
    )
    judgment_error_style: UiJudgmentErrorStyle = "none"
    judgment_error_placement: UiJudgmentErrorPlacement = "both"
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
