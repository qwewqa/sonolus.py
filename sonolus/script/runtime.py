from enum import IntEnum

from sonolus.backend.mode import Mode
from sonolus.script.array import Array
from sonolus.script.containers import VarArray
from sonolus.script.globals import (
    _level_life,
    _level_score,
    _play_runtime_environment,
    _play_runtime_update,
    _preview_runtime_environment,
    _runtime_background,
    _runtime_particle_transform,
    _runtime_skin_transform,
    _runtime_touch_array,
    _runtime_ui,
    _runtime_ui_configuration,
    _tutorial_runtime_environment,
    _tutorial_runtime_update,
    _watch_runtime_environment,
    _watch_runtime_update,
)
from sonolus.script.graphics import Quad, Rect
from sonolus.script.internal.context import ctx
from sonolus.script.internal.impl import meta_fn
from sonolus.script.num import Num
from sonolus.script.record import Record
from sonolus.script.transform import Transform2d
from sonolus.script.vec import Vec2


@_play_runtime_environment
class _PlayRuntimeEnvironment:
    is_debug: bool
    aspect_ratio: float
    audio_offset: float
    input_offset: float
    is_multiplayer: bool


@_watch_runtime_environment
class _WatchRuntimeEnvironment:
    is_debug: bool
    aspect_ratio: float
    audio_offset: float
    input_offset: float
    is_replay: bool


@_preview_runtime_environment
class _PreviewRuntimeEnvironment:
    is_debug: bool
    aspect_ratio: float


@_tutorial_runtime_environment
class _TutorialRuntimeEnvironment:
    is_debug: bool
    aspect_ratio: float
    audio_offset: float


@_play_runtime_update
class _PlayRuntimeUpdate:
    time: float
    delta_time: float
    scaled_time: float
    touch_count: int


@_watch_runtime_update
class _WatchRuntimeUpdate:
    time: float
    delta_time: float
    scaled_time: float
    is_skip: bool


@_tutorial_runtime_update
class _TutorialRuntimeUpdate:
    time: float
    delta_time: float
    navigation_direction: int


class RuntimeUiConfig(Record):
    scale: float
    alpha: float


@_runtime_ui_configuration
class _RuntimeUiConfigs:
    menu: RuntimeUiConfig
    judgment: RuntimeUiConfig
    combo: RuntimeUiConfig
    primary_metric: RuntimeUiConfig
    secondary_metric: RuntimeUiConfig
    progress: RuntimeUiConfig


class HorizontalAlign(IntEnum):
    LEFT = -1
    CENTER = 0
    RIGHT = 1


class RuntimeUiLayout(Record):
    anchor: Vec2
    pivot: Vec2
    dimensions: Vec2
    rotation: float
    alpha: float
    horizontal_align: HorizontalAlign
    background: bool

    def update(
        self,
        anchor: Vec2 | None = None,
        pivot: Vec2 | None = None,
        dimensions: Vec2 | None = None,
        rotation: float | None = None,
        alpha: float | None = None,
        horizontal_align: HorizontalAlign | None = None,
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


@_runtime_ui
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


class Touch(Record):
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
        return self.total_velocity.magnitude

    @property
    def total_angle(self) -> float:
        return self.total_delta.angle


@_runtime_touch_array
class _TouchArray:
    touches: Array[Touch, 999]


@_runtime_skin_transform
class _SkinTransform:
    value: Array[Array[float, 4], 4]

    @property
    @meta_fn
    def transform(self) -> Transform2d:
        values = self.value._to_list_()
        return Transform2d._raw(
            a00=Num(values[0 * 4 + 0]),
            a01=Num(values[0 * 4 + 1]),
            a02=Num(values[0 * 4 + 3]),
            a10=Num(values[1 * 4 + 0]),
            a11=Num(values[1 * 4 + 1]),
            a12=Num(values[1 * 4 + 3]),
            a20=Num(values[3 * 4 + 0]),
            a21=Num(values[3 * 4 + 1]),
        )


@_runtime_particle_transform
class _ParticleTransform:
    value: Array[Array[float, 4], 4]

    @property
    @meta_fn
    def transform(self) -> Transform2d:
        values = self.value._to_list_()
        return Transform2d._raw(
            a00=Num(values[0 * 4 + 0]),
            a01=Num(values[0 * 4 + 1]),
            a02=Num(values[0 * 4 + 3]),
            a10=Num(values[1 * 4 + 0]),
            a11=Num(values[1 * 4 + 1]),
            a12=Num(values[1 * 4 + 3]),
            a20=Num(values[3 * 4 + 0]),
            a21=Num(values[3 * 4 + 1]),
        )


@_runtime_background
class _Background:
    value: Quad


@_level_score
class _LevelScore:
    perfect_multiplier: float
    great_multiplier: float
    good_multiplier: float
    consecutive_perfect_multiplier: float
    consecutive_perfect_step: float
    consecutive_perfect_cap: float
    consecutive_great_multiplier: float
    consecutive_great_step: float
    consecutive_great_cap: float
    consecutive_good_multiplier: float
    consecutive_good_step: float
    consecutive_good_cap: float

    def update(
        self,
        perfect_multiplier: float | None = None,
        great_multiplier: float | None = None,
        good_multiplier: float | None = None,
        consecutive_perfect_multiplier: float | None = None,
        consecutive_perfect_step: float | None = None,
        consecutive_perfect_cap: float | None = None,
        consecutive_great_multiplier: float | None = None,
        consecutive_great_step: float | None = None,
        consecutive_great_cap: float | None = None,
        consecutive_good_multiplier: float | None = None,
        consecutive_good_step: float | None = None,
        consecutive_good_cap: float | None = None,
    ):
        if perfect_multiplier is not None:
            self.perfect_multiplier = perfect_multiplier
        if great_multiplier is not None:
            self.great_multiplier = great_multiplier
        if good_multiplier is not None:
            self.good_multiplier = good_multiplier
        if consecutive_perfect_multiplier is not None:
            self.consecutive_perfect_multiplier = consecutive_perfect_multiplier
        if consecutive_perfect_step is not None:
            self.consecutive_perfect_step = consecutive_perfect_step
        if consecutive_perfect_cap is not None:
            self.consecutive_perfect_cap = consecutive_perfect_cap
        if consecutive_great_multiplier is not None:
            self.consecutive_great_multiplier = consecutive_great_multiplier
        if consecutive_great_step is not None:
            self.consecutive_great_step = consecutive_great_step
        if consecutive_great_cap is not None:
            self.consecutive_great_cap = consecutive_great_cap
        if consecutive_good_multiplier is not None:
            self.consecutive_good_multiplier = consecutive_good_multiplier
        if consecutive_good_step is not None:
            self.consecutive_good_step = consecutive_good_step
        if consecutive_good_cap is not None:
            self.consecutive_good_cap = consecutive_good_cap


@_level_life
class _LevelLife:
    consecutive_perfect_increment: float
    consecutive_perfect_step: float
    consecutive_great_increment: float
    consecutive_great_step: float
    consecutive_good_increment: float
    consecutive_good_step: float

    def update(
        self,
        consecutive_perfect_increment: float | None = None,
        consecutive_perfect_step: float | None = None,
        consecutive_great_increment: float | None = None,
        consecutive_great_step: float | None = None,
        consecutive_good_increment: float | None = None,
        consecutive_good_step: float | None = None,
    ):
        if consecutive_perfect_increment is not None:
            self.consecutive_perfect_increment = consecutive_perfect_increment
        if consecutive_perfect_step is not None:
            self.consecutive_perfect_step = consecutive_perfect_step
        if consecutive_great_increment is not None:
            self.consecutive_great_increment = consecutive_great_increment
        if consecutive_great_step is not None:
            self.consecutive_great_step = consecutive_great_step
        if consecutive_good_increment is not None:
            self.consecutive_good_increment = consecutive_good_increment
        if consecutive_good_step is not None:
            self.consecutive_good_step = consecutive_good_step


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


@meta_fn
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


@meta_fn
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


@meta_fn
def is_multiplayer() -> bool:
    if not ctx():
        return False
    match ctx().global_state.mode:
        case Mode.Play:
            return _PlayRuntimeEnvironment.is_multiplayer
        case _:
            return False


@meta_fn
def is_replay() -> bool:
    if not ctx():
        return False
    match ctx().global_state.mode:
        case Mode.Watch:
            return _WatchRuntimeEnvironment.is_replay
        case _:
            return False


@meta_fn
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


@meta_fn
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


@meta_fn
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


@meta_fn
def touches() -> VarArray[Touch, 999]:
    if not ctx():
        return VarArray(0, Array[Touch, 0]())
    match ctx().global_state.mode:
        case Mode.Play:
            return VarArray(_PlayRuntimeUpdate.touch_count, _TouchArray.touches)
        case _:
            return VarArray(0, Array[Touch, 0]())


@meta_fn
def is_skip() -> bool:
    if not ctx():
        return False
    match ctx().global_state.mode:
        case Mode.Watch:
            return _WatchRuntimeUpdate.is_skip
        case _:
            return False


@meta_fn
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


def level_score() -> _LevelScore:
    return _LevelScore


def level_life() -> _LevelLife:
    return _LevelLife
