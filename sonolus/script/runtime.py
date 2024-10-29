from enum import IntEnum

from sonolus.backend.mode import Mode
from sonolus.script.array import Array
from sonolus.script.containers import VarArray
from sonolus.script.globals import (
    play_runtime_environment,
    play_runtime_update,
    preview_runtime_environment,
    runtime_background,
    runtime_particle_transform,
    runtime_skin_transform,
    runtime_touch_array,
    runtime_ui,
    runtime_ui_configuration,
    tutorial_runtime_environment,
    tutorial_runtime_update,
    watch_runtime_environment,
    watch_runtime_update,
)
from sonolus.script.graphics import Quad, Rect
from sonolus.script.internal.context import ctx
from sonolus.script.internal.impl import meta_fn
from sonolus.script.record import Record
from sonolus.script.transform import Transform2d
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


class RuntimeUiConfig(Record):
    scale: float
    alpha: float


@runtime_ui_configuration
class _RuntimeUiConfigs:
    menu: RuntimeUiConfig
    judgment: RuntimeUiConfig
    combo: RuntimeUiConfig
    primary_metric: RuntimeUiConfig
    secondary_metric: RuntimeUiConfig
    progress: RuntimeUiConfig


class HorizontalAlign(IntEnum):
    Left = -1
    Center = 0
    Right = 1


class RuntimeUiLayout(Record):
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
class _RuntimeUi:
    menu: RuntimeUiLayout
    judgment: RuntimeUiLayout
    combo_value: RuntimeUiLayout
    combo_text: RuntimeUiLayout
    primary_metric_bar: RuntimeUiLayout
    primary_metric_value: RuntimeUiLayout
    secondary_metric_bar: RuntimeUiLayout
    secondary_metric_value: RuntimeUiLayout
    progress: RuntimeUiLayout


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


@runtime_skin_transform
class _SkinTransform:
    value: Array[Array[float, 4], 4]

    @property
    @meta_fn
    def transform(self) -> Transform2d:
        values = self.value._to_list_()
        return Transform2d(
            a00=values[0 * 4 + 0],
            a01=values[0 * 4 + 1],
            a02=values[0 * 4 + 2],
            a10=values[1 * 4 + 0],
            a11=values[1 * 4 + 1],
            a12=values[1 * 4 + 2],
            a20=values[2 * 4 + 0],
            a21=values[2 * 4 + 1],
        )


@runtime_particle_transform
class _ParticleTransform:
    value: Array[Array[float, 4], 4]

    @property
    @meta_fn
    def transform(self) -> Transform2d:
        values = self.value._to_list_()
        return Transform2d(
            a00=values[0 * 4 + 0],
            a01=values[0 * 4 + 1],
            a02=values[0 * 4 + 2],
            a10=values[1 * 4 + 0],
            a11=values[1 * 4 + 1],
            a12=values[1 * 4 + 2],
            a20=values[2 * 4 + 0],
            a21=values[2 * 4 + 1],
        )


@runtime_background
class _Background:
    value: Quad


@meta_fn
def is_debug() -> bool:
    if not ctx():
        return False
    match ctx().global_state.mode:
        case Mode.Play:
            return _PlayRuntimeEnvironment.is_debug
        case Mode.Watch:
            return _WatchRuntimeEnvironment.is_debug
        case Mode.Preview:
            return _PreviewRuntimeEnvironment.is_debug
        case Mode.Tutorial:
            return _TutorialRuntimeEnvironment.is_debug
        case _:
            return False


@meta_fn
def aspect_ratio() -> float:
    if not ctx():
        return 16 / 9
    match ctx().global_state.mode:
        case Mode.Play:
            return _PlayRuntimeEnvironment.aspect_ratio
        case Mode.Watch:
            return _WatchRuntimeEnvironment.aspect_ratio
        case Mode.Preview:
            return _PreviewRuntimeEnvironment.aspect_ratio
        case Mode.Tutorial:
            return _TutorialRuntimeEnvironment.aspect_ratio


def audio_offset() -> float:
    if not ctx():
        return 0
    match ctx().global_state.mode:
        case Mode.Play:
            return _PlayRuntimeEnvironment.audio_offset
        case Mode.Watch:
            return _WatchRuntimeEnvironment.audio_offset
        case Mode.Tutorial:
            return _TutorialRuntimeEnvironment.audio_offset
        case _:
            return 0


def input_offset() -> float:
    if not ctx():
        return 0
    match ctx().global_state.mode:
        case Mode.Play:
            return _PlayRuntimeEnvironment.input_offset
        case Mode.Watch:
            return _WatchRuntimeEnvironment.input_offset
        case _:
            return 0


def is_multiplayer() -> bool:
    if not ctx():
        return False
    match ctx().global_state.mode:
        case Mode.Play:
            return _PlayRuntimeEnvironment.is_multiplayer
        case _:
            return False


def is_replay() -> bool:
    if not ctx():
        return False
    match ctx().global_state.mode:
        case Mode.Watch:
            return _WatchRuntimeEnvironment.is_replay
        case _:
            return False


def time() -> float:
    if not ctx():
        return 0
    match ctx().global_state.mode:
        case Mode.Play:
            return _PlayRuntimeUpdate.time
        case Mode.Watch:
            return _WatchRuntimeUpdate.time
        case Mode.Tutorial:
            return _TutorialRuntimeUpdate.time
        case _:
            return 0


def delta_time() -> float:
    if not ctx():
        return 0
    match ctx().global_state.mode:
        case Mode.Play:
            return _PlayRuntimeUpdate.delta_time
        case Mode.Watch:
            return _WatchRuntimeUpdate.delta_time
        case Mode.Tutorial:
            return _TutorialRuntimeUpdate.delta_time
        case _:
            return 0


def scaled_time() -> float:
    if not ctx():
        return 0
    match ctx().global_state.mode:
        case Mode.Play:
            return _PlayRuntimeUpdate.scaled_time
        case Mode.Watch:
            return _WatchRuntimeUpdate.scaled_time
        case Mode.Tutorial:
            return _TutorialRuntimeUpdate.time
        case _:
            return 0


def touches() -> VarArray[TouchInfo, 999]:
    if not ctx():
        return VarArray(0, Array[TouchInfo, 0]())
    match ctx().global_state.mode:
        case Mode.Play:
            return VarArray(_PlayRuntimeUpdate.touch_count, _TouchArray.touches)
        case _:
            return VarArray(0, Array[TouchInfo, 0]())


def is_skip() -> bool:
    if not ctx():
        return False
    match ctx().global_state.mode:
        case Mode.Watch:
            return _WatchRuntimeUpdate.is_skip
        case _:
            return False


def navigation_direction() -> int:
    if not ctx():
        return 0
    match ctx().global_state.mode:
        case Mode.Tutorial:
            return _TutorialRuntimeUpdate.navigation_direction
        case _:
            return 0


def skin_transform() -> Transform2d:
    return _SkinTransform.transform


@meta_fn
def set_skin_transform(value: Transform2d):
    _SkinTransform.transform._copy_from_(value)


def particle_transform() -> Transform2d:
    return _ParticleTransform.transform


@meta_fn
def set_particle_transform(value: Transform2d):
    _ParticleTransform.transform._copy_from_(value)


def background() -> Quad:
    return _Background.value


def set_background(value: Quad):
    _Background.value = value


runtime_ui = _RuntimeUi
runtime_ui_configs = _RuntimeUiConfigs


def screen() -> Rect:
    return Rect(t=1, r=aspect_ratio(), b=-1, l=-aspect_ratio())
