from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, NewType, dataclass_transform, get_origin

from sonolus.backend.ops import Op
from sonolus.script.internal.introspection import get_field_specifiers
from sonolus.script.internal.native import native_function
from sonolus.script.record import Record


class Effect(Record):
    id: int

    def is_available(self) -> bool:
        return _has_effect_clip(self.id)

    def play(self, distance: float) -> None:
        _play(self.id, distance)

    def schedule(self, time: float, distance: float) -> None:
        _play_scheduled(self.id, time, distance)

    def loop(self) -> LoopedEffectHandle:
        return LoopedEffectHandle(_play_looped(self.id))

    def schedule_loop(self, start_time: float) -> ScheduledLoopedEffectHandle:
        return ScheduledLoopedEffectHandle(_play_looped_scheduled(self.id, start_time))


class LoopedEffectHandle(Record):
    id: int

    def stop(self) -> None:
        _stop_looped(self.id)


class ScheduledLoopedEffectHandle(Record):
    id: int

    def stop(self, end_time: float) -> None:
        _stop_looped_scheduled(self.id, end_time)


@native_function(Op.HasEffectClip)
def _has_effect_clip(effect_id: int) -> bool:
    raise NotImplementedError


@native_function(Op.Play)
def _play(effect_id: int, distance: float) -> None:
    raise NotImplementedError


@native_function(Op.PlayLooped)
def _play_looped(effect_id: int) -> int:
    raise NotImplementedError


@native_function(Op.PlayLoopedScheduled)
def _play_looped_scheduled(effect_id: int, start_time: float) -> int:
    raise NotImplementedError


@native_function(Op.PlayScheduled)
def _play_scheduled(effect_id: int, time: float, distance: float) -> None:
    raise NotImplementedError


@native_function(Op.StopLooped)
def _stop_looped(handle: int) -> None:
    raise NotImplementedError


@native_function(Op.StopLoopedScheduled)
def _stop_looped_scheduled(handle: int, end_time: float) -> None:
    raise NotImplementedError


@dataclass
class EffectInfo:
    name: str


def effect(name: str) -> Any:
    return EffectInfo(name)


type Effects = NewType("Effects", Any)


@dataclass_transform()
def effects[T](cls: type[T]) -> T | Effects:
    if len(cls.__bases__) != 1:
        raise ValueError("Effects class must not inherit from any class (except object)")
    instance = cls()
    names = []
    for i, (name, annotation) in enumerate(get_field_specifiers(cls).items()):
        if get_origin(annotation) is not Annotated:
            raise TypeError(f"Invalid annotation for effects: {annotation}")
        annotation_type = annotation.__args__[0]
        annotation_values = annotation.__metadata__
        if annotation_type is not Effect:
            raise TypeError(f"Invalid annotation for effects: {annotation}, expected annotation of type Effect")
        if len(annotation_values) != 1 or not isinstance(annotation_values[0], EffectInfo):
            raise TypeError(f"Invalid annotation for effects: {annotation}, expected a single string annotation value")
        effect_name = annotation_values[0].name
        names.append(effect_name)
        setattr(instance, name, Effect(i))
    instance._effects_ = names
    instance._is_comptime_value_ = True
    return instance


class StandardEffect:
    MISS = Annotated[Effect, effect("#MISS")]
    PERFECT = Annotated[Effect, effect("#PERFECT")]
    GREAT = Annotated[Effect, effect("#GREAT")]
    GOOD = Annotated[Effect, effect("#GOOD")]
    HOLD = Annotated[Effect, effect("#HOLD")]
    MISS_ALTERNATIVE = Annotated[Effect, effect("#MISS_ALTERNATIVE")]
    PERFECT_ALTERNATIVE = Annotated[Effect, effect("#PERFECT_ALTERNATIVE")]
    GREAT_ALTERNATIVE = Annotated[Effect, effect("#GREAT_ALTERNATIVE")]
    GOOD_ALTERNATIVE = Annotated[Effect, effect("#GOOD_ALTERNATIVE")]
    HOLD_ALTERNATIVE = Annotated[Effect, effect("#HOLD_ALTERNATIVE")]
    STAGE = Annotated[Effect, effect("#STAGE")]


@effects
class EmptyEffects:
    pass
