from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, NewType, dataclass_transform, get_origin

from sonolus.backend.ops import Op
from sonolus.script.internal.introspection import get_field_specifiers
from sonolus.script.internal.native import native_function
from sonolus.script.record import Record


class Effect(Record):
    """Sound effect clip.

    Usage:
        ```python
        Effect(id: int)
        ```
    """

    id: int
    """Effect ID."""

    def is_available(self) -> bool:
        """Return whether the effect clip is available."""
        return _has_effect_clip(self.id)

    def play(self, distance: float = 0) -> None:
        """Play the effect clip.

        If the clip was already played within the specified distance, it will be skipped.

        Arguments:
            distance: Minimum time in seconds since the last play for the effect to play.
        """
        _play(self.id, distance)

    def schedule(self, time: float, distance: float = 0) -> None:
        """Schedule the effect clip to play at a specific time.

        This is not suitable for real-time effects such as responses to user input. Use `play` instead.

        This may be called in preprocess to schedule effects upfront.

        If the clip would play within the specified distance of another play, it will be skipped.

        Arguments:
            time: Time in seconds when the effect should play.
            distance: Minimum time in seconds after a previous play for the effect to play.
        """
        _play_scheduled(self.id, time, distance)

    def loop(self) -> LoopedEffectHandle:
        """Play the effect clip in a loop until stopped.

        Returns:
            A handle to stop the loop.
        """
        return LoopedEffectHandle(_play_looped(self.id))

    def schedule_loop(self, start_time: float) -> ScheduledLoopedEffectHandle:
        """Schedule the effect clip to play in a loop until stopped.

        This is not suitable for real-time effects such as responses to user input. Use `loop` instead.

        Returns:
            A handle to stop the loop.
        """
        return ScheduledLoopedEffectHandle(_play_looped_scheduled(self.id, start_time))


class LoopedEffectHandle(Record):
    """Handle to stop a looped effect."""

    id: int

    def stop(self) -> None:
        """Stop the looped effect."""
        _stop_looped(self.id)


class ScheduledLoopedEffectHandle(Record):
    """Handle to stop a scheduled looped effect."""

    id: int

    def stop(self, end_time: float) -> None:
        """Stop the scheduled looped effect."""
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
    """Define a sound effect clip with the given name."""
    return EffectInfo(name)


type Effects = NewType("Effects", Any)


@dataclass_transform()
def effects[T](cls: type[T]) -> T | Effects:
    """Decorator to define effect clips.

    Usage:
        ```python
        @effects
        class Effects:
            miss: StandardEffect.MISS
            other: Effect = effect("other")
        ```
    """
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
    """Standard sound effect clips."""

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
