from enum import IntEnum

from sonolus.backend.mode import Mode
from sonolus.script.globals import (
    play_runtime_environment,
    preview_runtime_environment,
    runtime_ui,
    runtime_ui_configuration,
    singleton,
    tutorial_runtime_environment,
    watch_runtime_environment, play_runtime_update, watch_runtime_update, tutorial_runtime_update,
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


@singleton
class Runtime(Record):
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
    def touch_count(self) -> int:
        if self.is_play:
            return _PlayRuntimeUpdate.touch_count
        return 0

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


class UiConfig(Record):
    scale: float
    alpha: float


@runtime_ui_configuration
class UiConfigs:
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


@runtime_ui
class UiLayouts:
    menu: UiLayout
    judgment: UiLayout
    combo_value: UiLayout
    combo_text: UiLayout
    primary_metric_bar: UiLayout
    primary_metric_value: UiLayout
    secondary_metric_bar: UiLayout
    secondary_metric_value: UiLayout
    progress: UiLayout
