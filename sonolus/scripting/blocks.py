from __future__ import annotations

import warnings
from enum import Enum
from typing import Type, TypeVar, overload

from sonolus.scripting.boolean import *
from sonolus.scripting.draw import *
from sonolus.scripting.number import *
from sonolus.scripting.point import *
from sonolus.scripting.values import *
from sonolus.backend.ir import MemoryBlock

__all__ = (
    "MemoryBlock",
    "LevelDataStruct",
    "LevelUIStruct",
    "UIHorizontalAlign",
    "LevelUIElement",
    "LevelUIConfigurationEntry",
    "LevelUIConfigurationStruct",
    "LevelScoreStruct",
    "LevelLifeStruct",
    "TouchDataStruct",
    "get_level_memory",
    "get_full_level_data",
    "get_custom_level_data",
    "get_level_options",
    "get_level_transform",
    "get_level_background",
    "get_level_ui",
    "get_level_ui_configuration",
    "get_level_score",
    "get_level_life",
    "get_engine_rom",
    "get_temporary_data",
    "LevelData",
    "LevelTransform",
    "LevelBackground",
    "LevelUI",
    "LevelUIConfiguration",
    "LevelScore",
    "LevelLife",
    "TouchData",
)

T = TypeVar("T", bound=Value)


class LevelDataStruct(Struct):
    time: Num
    delta_time: Num
    aspect_ratio: Num
    audio_offset: Num
    input_offset: Num
    render_scale: Num
    anit_aliasing: Num


class UIHorizontalAlign(int, Enum):
    Left = -1
    Center = 0
    Right = 1


class LevelUIElement(Struct):
    anchor: Point
    pivot: Point
    width: Num
    height: Num
    rotation: Num = 0
    alpha: Num = 1
    horizontal_align: Num
    background: Bool


class LevelUIStruct(Struct):
    menu: LevelUIElement
    judgement: LevelUIElement
    combo_value: LevelUIElement
    combo_text: LevelUIElement
    primary_metric_bar: LevelUIElement
    primary_metric_value: LevelUIElement
    secondary_metric_bar: LevelUIElement
    secondary_metric_value: LevelUIElement


class LevelUIConfigurationEntry(Struct):
    scale: Num
    alpha: Num


class LevelUIConfigurationStruct(Struct):
    menu: LevelUIConfigurationEntry
    judgement: LevelUIConfigurationEntry
    combo: LevelUIConfigurationEntry
    primary_metric: LevelUIConfigurationEntry
    secondary_metric: LevelUIConfigurationEntry


class LevelScoreStruct(Struct):
    perfect_multiplier: Num
    great_multiplier: Num
    good_multiplier: Num
    consecutive_perfect_multiplier: Num
    consecutive_perfect_step: Num
    consecutive_perfect_cap: Num
    consecutive_great_multiplier: Num
    consecutive_great_step: Num
    consecutive_great_cap: Num
    consecutive_good_multiplier: Num
    consecutive_good_step: Num
    consecutive_good_cap: Num


class LevelLifeStruct(Struct):
    consecutive_perfect_increment: Num
    consecutive_perfect_step: Num
    consecutive_great_increment: Num
    consecutive_great_step: Num
    consecutive_good_increment: Num
    consecutive_good_step: Num


class TouchDataStruct(Struct):
    id: Num
    started: Bool
    ended: Bool
    time: Num
    start_time: Num
    position: Point
    start_position: Point
    delta_position: Point
    velocity_vector: Point
    velocity_magnitude: Num
    velocity_angle: Num


def get_level_memory(type_: Type[T], /) -> T:
    if type_._size_ > 4095:
        warnings.warn(f"Type {type_} may be too large for level memory.")
    return Pointer[type_](MemoryBlock.LEVEL_MEMORY, 0).deref()


@overload
def get_full_level_data() -> LevelDataStruct:
    ...


@overload
def get_full_level_data(type_: Type[T]) -> T:
    ...


def get_full_level_data(type_: Type[T] = LevelDataStruct, /) -> T:
    if type_._size_ > 4096:
        warnings.warn(f"Type {type_} may be too large for level data.")
    return Pointer[type_](MemoryBlock.LEVEL_DATA, 0).deref()


def get_custom_level_data(type_: Type[T], /) -> T:
    if type_._size_ > 4096 - LevelDataStruct._size_:
        warnings.warn(f"Type {type_} may be too large for level data.")
    return Pointer[type_](MemoryBlock.LEVEL_DATA, LevelDataStruct._size_).deref()


def get_level_options(type_: Type[T], /) -> T:
    return Pointer[type_](MemoryBlock.LEVEL_OPTION, 0).deref()


@overload
def get_level_transform() -> Array[Num, 4, 4]:
    ...


@overload
def get_level_transform(type_: Type[T]) -> T:
    ...


def get_level_transform(type_: Type[T] = Array[Array[Num, 4], 4], /) -> T:
    if type_._size_ != 16:
        warnings.warn(f"Type {type_} may have an incorrect size for level transform.")
    return Pointer[type_](MemoryBlock.LEVEL_MEMORY, 0).deref()


@overload
def get_level_background() -> Quad:
    ...


@overload
def get_level_background(type_: Type[T]) -> T:
    ...


def get_level_background(type_: Type[T] = Quad, /) -> T:
    if type_._size_ != 8:
        warnings.warn(f"Type {type_} may have an incorrect size for level background.")
    return Pointer[type_](MemoryBlock.LEVEL_BACKGROUND, 0).deref()


@overload
def get_level_ui() -> LevelUIStruct:
    ...


@overload
def get_level_ui(type_: Type[T]) -> T:
    ...


def get_level_ui(type_: Type[T] = LevelUIStruct, /) -> T:
    if type_._size_ != 80:
        warnings.warn(f"Type {type_} may have an incorrect size for level ui.")
    return Pointer[type_](MemoryBlock.LEVEL_UI, 0).deref()


@overload
def get_level_ui_configuration() -> LevelUIConfigurationStruct:
    ...


@overload
def get_level_ui_configuration(type_: Type[T]) -> T:
    ...


def get_level_ui_configuration(type_: Type[T] = LevelUIConfigurationStruct, /) -> T:
    if type_._size_ != 10:
        warnings.warn(
            f"Type {type_} may have an incorrect size for level ui configuration."
        )
    return Pointer[type_](MemoryBlock.LEVEL_UI_CONFIGURATION, 0).deref()


@overload
def get_level_score() -> LevelScoreStruct:
    ...


@overload
def get_level_score(type_: Type[T]) -> T:
    ...


def get_level_score(type_: Type[T] = LevelScoreStruct, /) -> T:
    if type_._size_ != 12:
        warnings.warn(f"Type {type_} may have an incorrect size for level score.")
    return Pointer[type_](MemoryBlock.LEVEL_SCORE, 0).deref()


@overload
def get_level_life() -> LevelLifeStruct:
    ...


@overload
def get_level_life(type_: Type[T]) -> T:
    ...


def get_level_life(type_: Type[T] = LevelLifeStruct, /) -> T:
    if type_._size_ != 6:
        warnings.warn(f"Type {type_} may have an incorrect size for level life.")
    return Pointer[type_](MemoryBlock.LEVEL_LIFE, 0).deref()


def get_engine_rom(type_: Type[T], /) -> T:
    return Pointer[type_](MemoryBlock.ENGINE_ROM, 0).deref()


@overload
def get_temporary_data() -> TouchDataStruct:
    ...


@overload
def get_temporary_data(type_: Type[T]) -> T:
    ...


def get_temporary_data(type_: Type[T] = TouchDataStruct, /) -> T:
    if type_._size_ != 15:
        warnings.warn(f"Type {type_} may have an incorrect size for temporary data.")
    return Pointer[type_](MemoryBlock.TEMPORARY_DATA, 0).deref()


LevelData = get_full_level_data(LevelDataStruct)
LevelTransform = get_level_transform(Array[Array[Num, 4], 4])
LevelBackground = get_level_background(Quad)
LevelUI = get_level_ui(LevelUIStruct)
LevelUIConfiguration = get_level_ui_configuration(LevelUIConfigurationStruct)
LevelScore = get_level_score(LevelScoreStruct)
LevelLife = get_level_life(LevelLifeStruct)
TouchData = get_temporary_data(TouchDataStruct)
