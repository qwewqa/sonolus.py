from enum import IntEnum

from sonolus.backend.mode import Mode
from sonolus.script.array import Array
from sonolus.script.collections import VarArray
from sonolus.script.globals import (
    play_runtime_environment,
    play_runtime_update,
    preview_runtime_environment,
    runtime_touch_array,
    runtime_ui,
    runtime_ui_configuration,
    tutorial_runtime_environment,
    tutorial_runtime_update,
    watch_runtime_environment,
    watch_runtime_update,
)
from sonolus.script.internal.context import ctx
from sonolus.script.internal.impl import self_impl
from sonolus.script.record import Record
from sonolus.script.vec import Vec2


@play_runtime_environment
class _PlayRuntimeEnvironment:
    is_debug: bool
    aspect_ratio: float
    audio_offset: float
    input_offset: float
    is_multiplayer: bool


@watch_runtime_environment
class _WatchRuntimeEnvironment:
    is_debug: bool
    aspect_ratio: float
    audio_offset: float
    input_offset: float
    is_replay: bool


@preview_runtime_environment
class _PreviewRuntimeEnvironment:
    is_debug: bool
    aspect_ratio: float


@tutorial_runtime_environment
class _TutorialRuntimeEnvironment:
    is_debug: bool
    aspect_ratio: float
    audio_offset: float


@play_runtime_update
class _PlayRuntimeUpdate:
    time: float
    delta_time: float
    scaled_time: float
    touch_count: int


@watch_runtime_update
class _WatchRuntimeUpdate:
    time: float
    delta_time: float
    scaled_time: float
    is_skip: bool


@tutorial_runtime_update
class _TutorialRuntimeUpdate:
    time: float
    delta_time: float
    navigation_direction: int


class UiConfig(Record):
    scale: float
    alpha: float


@runtime_ui_configuration
class _UiConfigs:
    menu: UiConfig
    judgment: UiConfig
    combo: UiConfig
    primary_metric: UiConfig
    secondary_metric: UiConfig
    progress: UiConfig


class HorizontalAlign(IntEnum):
    Left = -1
    Center = 0
    Right = 1


class UiLayout(Record):
    anchor: Vec2
    pivot: Vec2
    dimensions: Vec2
    rotation: float
    alpha: float
    horizontal_align: int
    background: bool

    def update(
        self,
        anchor: Vec2 | None = None,
        pivot: Vec2 | None = None,
        dimensions: Vec2 | None = None,
        rotation: float | None = None,
        alpha: float | None = None,
        horizontal_align: int | None = None,
        background: bool | None = None,
    ):
        if anchor is not None:
            self.anchor = anchor
        if pivot is not None:
            self.pivot = pivot
        if dimensions is not None:
            self.dimensions = dimensions
        if rotation is not None:
            self.rotation = rotation
        if alpha is not None:
            self.alpha = alpha
        if horizontal_align is not None:
            self.horizontal_align = horizontal_align
        if background is not None:
            self.background = background


@runtime_ui
class _UiLayouts:
    menu: UiLayout
    judgment: UiLayout
    combo_value: UiLayout
    combo_text: UiLayout
    primary_metric_bar: UiLayout
    primary_metric_value: UiLayout
    secondary_metric_bar: UiLayout
    secondary_metric_value: UiLayout
    progress: UiLayout


class TouchInfo(Record):
    id: int
    started: bool
    ended: bool
    time: float
    start_time: float
    position: Vec2
    start_position: Vec2
    delta: Vec2
    velocity: Vec2
    speed: float
    angle: float

    @property
    def total_time(self) -> float:
        return self.time - self.start_time

    @property
    def total_delta(self) -> Vec2:
        return self.position - self.start_position

    @property
    def total_velocity(self) -> Vec2:
        return self.total_delta / self.total_time if self.total_time > 0 else Vec2(0, 0)

    @property
    def total_speed(self) -> float:
        return self.total_velocity.magnitude()

    @property
    def total_angle(self) -> float:
        return self.total_delta.angle()


@runtime_touch_array
class _TouchArray:
    touches: Array[TouchInfo, 999]


class _Runtime(Record):
    @property
    def is_debug(self) -> bool:
        if self.is_play:
            return _PlayRuntimeEnvironment.is_debug
        if self.is_watch:
            return _WatchRuntimeEnvironment.is_debug
        if self.is_preview:
            return _PreviewRuntimeEnvironment.is_debug
        if self.is_tutorial:
            return _TutorialRuntimeEnvironment.is_debug
        return False

    @property
    def aspect_ratio(self) -> float:
        if self.is_play:
            return _PlayRuntimeEnvironment.aspect_ratio
        if self.is_watch:
            return _WatchRuntimeEnvironment.aspect_ratio
        if self.is_preview:
            return _PreviewRuntimeEnvironment.aspect_ratio
        if self.is_tutorial:
            return _TutorialRuntimeEnvironment.aspect_ratio
        return 16 / 9

    @property
    def audio_offset(self) -> float:
        if self.is_play:
            return _PlayRuntimeEnvironment.audio_offset
        if self.is_watch:
            return _WatchRuntimeEnvironment.audio_offset
        if self.is_tutorial:
            return _TutorialRuntimeEnvironment.audio_offset
        return 0

    @property
    def input_offset(self) -> float:
        if self.is_play:
            return _PlayRuntimeEnvironment.input_offset
        if self.is_watch:
            return _WatchRuntimeEnvironment.input_offset
        return 0

    @property
    def is_multiplayer(self) -> bool:
        if self.is_play:
            return _PlayRuntimeEnvironment.is_multiplayer
        return False

    @property
    def is_replay(self) -> bool:
        if self.is_watch:
            return _WatchRuntimeEnvironment.is_replay
        return False

    @property
    def time(self) -> float:
        if self.is_play:
            return _PlayRuntimeUpdate.time
        if self.is_watch:
            return _WatchRuntimeUpdate.time
        if self.is_tutorial:
            return _TutorialRuntimeUpdate.time
        return 0

    @property
    def delta_time(self) -> float:
        if self.is_play:
            return _PlayRuntimeUpdate.delta_time
        if self.is_watch:
            return _WatchRuntimeUpdate.delta_time
        if self.is_tutorial:
            return _TutorialRuntimeUpdate.delta_time
        return 0

    @property
    def scaled_time(self) -> float:
        if self.is_play:
            return _PlayRuntimeUpdate.scaled_time
        if self.is_watch:
            return _WatchRuntimeUpdate.scaled_time
        if self.is_tutorial:
            return _TutorialRuntimeUpdate.time
        return 0

    @property
    def _touch_count(self) -> int:
        if self.is_play:
            return _PlayRuntimeUpdate.touch_count
        return 0

    @property
    def touches(self) -> VarArray[TouchInfo, 999]:
        if self.is_play:
            return VarArray(self._touch_count, _TouchArray.touches)
        return VarArray(0, Array[TouchInfo, 0].of())

    @property
    def is_skip(self) -> bool:
        if self.is_watch:
            return _WatchRuntimeUpdate.is_skip
        return False

    @property
    def navigation_direction(self) -> int:
        if self.is_tutorial:
            return _TutorialRuntimeUpdate.navigation_direction
        return 0

    @property
    def ui_config(self) -> _UiConfigs:
        return _UiConfigs

    @property
    def ui(self) -> _UiLayouts:
        return _UiLayouts

    @property
    @self_impl
    def is_play(self) -> bool:
        return ctx().global_state.mode is Mode.Play

    @property
    @self_impl
    def is_watch(self) -> bool:
        return ctx().global_state.mode is Mode.Watch

    @property
    @self_impl
    def is_preview(self) -> bool:
        return ctx().global_state.mode is Mode.Preview

    @property
    @self_impl
    def is_tutorial(self) -> bool:
        return ctx().global_state.mode is Mode.Tutorial


runtime = _Runtime()
