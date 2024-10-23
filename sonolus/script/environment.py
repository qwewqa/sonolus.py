from enum import IntEnum

from sonolus.backend.mode import Mode
from sonolus.script.globals import (
    play_runtime_environment,
    preview_runtime_environment,
    runtime_ui,
    runtime_ui_configuration,
    singleton,
    tutorial_runtime_environment,
    watch_runtime_environment,
)
from sonolus.script.internal.context import ctx
from sonolus.script.internal.impl import self_impl
from sonolus.script.record import Record
from sonolus.script.vec import Vec2


@play_runtime_environment
class PlayRuntimeEnvironment:
    is_debug: bool
    aspect_ratio: float
    audio_offset: float
    input_offset: float
    is_multiplayer: bool


@watch_runtime_environment
class WatchRuntimeEnvironment:
    is_debug: bool
    aspect_ratio: float
    audio_offset: float
    input_offset: float
    is_replay: bool


@preview_runtime_environment
class PreviewRuntimeEnvironment:
    is_debug: bool
    aspect_ratio: float


@tutorial_runtime_environment
class TutorialRuntimeEnvironment:
    is_debug: bool
    aspect_ratio: float
    audio_offset: float


@singleton
class Runtime(Record):
    @property
    def is_debug(self):
        if self.is_play:
            return PlayRuntimeEnvironment.is_debug
        if self.is_watch:
            return WatchRuntimeEnvironment.is_debug
        if self.is_preview:
            return PreviewRuntimeEnvironment.is_debug
        if self.is_tutorial:
            return TutorialRuntimeEnvironment.is_debug
        return False

    @property
    def aspect_ratio(self):
        if self.is_play:
            return PlayRuntimeEnvironment.aspect_ratio
        if self.is_watch:
            return WatchRuntimeEnvironment.aspect_ratio
        if self.is_preview:
            return PreviewRuntimeEnvironment.aspect_ratio
        if self.is_tutorial:
            return TutorialRuntimeEnvironment.aspect_ratio
        return 16 / 9

    @property
    def audio_offset(self):
        if self.is_play:
            return PlayRuntimeEnvironment.audio_offset
        if self.is_watch:
            return WatchRuntimeEnvironment.audio_offset
        if self.is_tutorial:
            return TutorialRuntimeEnvironment.audio_offset
        return 0

    @property
    def input_offset(self):
        if self.is_play:
            return PlayRuntimeEnvironment.input_offset
        if self.is_watch:
            return WatchRuntimeEnvironment.input_offset
        return 0

    @property
    def is_multiplayer(self):
        if self.is_play:
            return PlayRuntimeEnvironment.is_multiplayer
        return False

    @property
    def is_replay(self):
        if self.is_watch:
            return WatchRuntimeEnvironment.is_replay
        return False

    @property
    @self_impl
    def is_play(self):
        return ctx().global_state.mode is Mode.Play

    @property
    @self_impl
    def is_watch(self):
        return ctx().global_state.mode is Mode.Watch

    @property
    @self_impl
    def is_preview(self):
        return ctx().global_state.mode is Mode.Preview

    @property
    @self_impl
    def is_tutorial(self):
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
