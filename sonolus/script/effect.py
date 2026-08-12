from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Annotated, Any, NewType, dataclass_transform, get_origin

from sonolus.backend.mode import Mode
from sonolus.backend.ops import Op
from sonolus.script.array_like import ArrayLike, check_positive_index
from sonolus.script.debug import static_error
from sonolus.script.internal.context import ctx
from sonolus.script.internal.introspection import describe_value, get_field_specifiers
from sonolus.script.internal.meta_fn import meta_fn, perf_meta_fn
from sonolus.script.internal.native import native_function
from sonolus.script.record import Record


@meta_fn
def _check_effect_playback_mode() -> None:
    if ctx() and ctx().mode_state.mode is Mode.PREVIEW:
        raise RuntimeError(
            f"Effect playback is not available in '{ctx().mode_state.mode.name}' mode, only in PLAY, WATCH, "
            "TUTORIAL. In code shared with preview, guard the call with a compile-time mode check such as "
            "'if not is_preview():', which eliminates the branch in preview."
        )


class Effect(Record):
    """Sound effect clip.

    Usage:
        ```python
        Effect(id: int)
        ```
    """

    id: int
    """Effect ID."""

    @property
    @perf_meta_fn
    def is_available(self) -> bool:
        """Whether the effect clip is available."""
        return _has_effect_clip(self.id)

    @perf_meta_fn
    def play(self, distance: float = 0) -> None:
        """Play the effect clip.

        If the clip was already played within the specified distance, it will be skipped.

        Not available in preview mode.

        Args:
            distance: Minimum time in seconds since the last play for the effect to play.
        """
        _check_effect_playback_mode()
        _play(self.id, distance)

    @perf_meta_fn
    def schedule(self, time: float, distance: float = 0) -> None:
        """Schedule the effect clip to play at a specific time.

        This is not suitable for real-time effects such as responses to user input.
        Use [`play`][sonolus.script.effect.Effect.play] instead.

        This may be called in preprocess to schedule effects upfront.

        Schedule at least 0.5 seconds before the target time when possible. Scheduling closer to the target may
        cause unexpected latency.

        If the clip would play within the specified distance of another play, it will be skipped.

        Not available in preview mode.

        Args:
            time: Time in seconds when the effect should play.
            distance: Minimum time in seconds after a previous play for the effect to play.
        """
        _check_effect_playback_mode()
        _play_scheduled(self.id, time, distance)

    @perf_meta_fn
    def loop(self) -> LoopedEffectHandle:
        """Play the effect clip in a loop until stopped.

        Not available in preview mode.

        Returns:
            A handle to stop the loop.
        """
        _check_effect_playback_mode()
        return LoopedEffectHandle(_play_looped(self.id))

    @perf_meta_fn
    def schedule_loop(self, start_time: float) -> ScheduledLoopedEffectHandle:
        """Schedule the effect clip to play in a loop until stopped.

        This is not suitable for real-time effects such as responses to user input.
        Use [`loop`][sonolus.script.effect.Effect.loop] instead.

        Schedule at least 0.5 seconds before the target time when possible. Scheduling closer to the target may
        cause unexpected latency.

        Not available in preview mode.

        Args:
            start_time: The time in seconds at which to start the effect.

        Returns:
            A handle to stop the loop.
        """
        _check_effect_playback_mode()
        return ScheduledLoopedEffectHandle(_play_looped_scheduled(self.id, start_time))


class LoopedEffectHandle(Record):
    """Handle to stop a looped effect."""

    id: int

    def stop(self) -> None:
        """Stop the looped effect.

        Not available in preview mode.
        """
        _check_effect_playback_mode()
        _stop_looped(self.id)


class ScheduledLoopedEffectHandle(Record):
    """Handle to stop a scheduled looped effect."""

    id: int

    def stop(self, end_time: float) -> None:
        """Stop the scheduled looped effect.

        Schedule at least 0.5 seconds before the target time when possible. Scheduling closer to the target may
        cause unexpected latency.

        Not available in preview mode.

        Args:
            end_time: The time at which to stop the effect.
        """
        _check_effect_playback_mode()
        _stop_looped_scheduled(self.id, end_time)


class EffectGroup(Record, ArrayLike[Effect]):
    """A group of effect clips.

    Does not support negative indexes.

    Usage:
        ```python
        EffectGroup(start_id: int, size: int)
        ```
    """

    start_id: int
    size: int

    def __len__(self) -> int:
        """Return the number of effect clips in the group."""
        return self.size

    def __getitem__(self, index: int) -> Effect:
        """Return the effect clip at the given index.

        Args:
            index: The index of the effect clip. Must be an integer between 0 and `len(self) - 1`.
        """
        check_positive_index(index, self.size)
        return Effect(self.start_id + index)

    def get_unchecked(self, index: int) -> Effect:
        """Return the effect clip at the given index, possibly without bounds checking."""
        return Effect(self.start_id + index)

    def __setitem__(self, index: int, value: Effect) -> None:
        """Raise a compile-time error; effect groups are read-only."""
        static_error("EffectGroup is read-only")


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


@dataclass
class EffectGroupInfo:
    names: list[str]


def effect(name: str) -> Any:
    """Define a sound effect clip with the given name."""
    return EffectInfo(name)


def effect_group(names: Iterable[str]) -> Any:
    """Define an effect group with the given names."""
    if isinstance(names, str | bytes | bytearray):
        raise TypeError(f"Expected a sequence of names, got {names!r}; one name is written ({names!r},)")
    return EffectGroupInfo(list(names))


type Effects = NewType("Effects", Any)  # type: ignore


@dataclass_transform(kw_only_default=True)
def effects[T](cls: type[T]) -> T | Effects:
    """Decorator to define effect clips.

    Usage:
        ```python
        @effects
        class Effects:
            miss: StandardEffect.MISS
            other: Effect = effect("other")
            group_1: EffectGroup = effect_group(["one", "two", "three"])
            group_2: EffectGroup = effect_group(f"name_{i}" for i in range(10))
        ```
    """
    if cls.__bases__ != (object,):
        raise ValueError("Effects class must not inherit from any class (except object)")
    instance = cls()
    names = []
    i = 0
    for name, annotation in get_field_specifiers(cls).items():
        described = describe_value(annotation)
        if get_origin(annotation) is not Annotated:
            raise TypeError(f"Invalid annotation for effects: {described} on field {name}")
        annotation_type = annotation.__args__[0]
        annotation_values = annotation.__metadata__
        if len(annotation_values) != 1:
            raise TypeError(f"Invalid annotation for effects: {described} on field {name}, too many annotation values")
        effect_info = annotation_values[0]
        match effect_info:
            case EffectInfo(name=effect_name):
                if annotation_type is not Effect:
                    raise TypeError(f"Invalid annotation for effects: {described} on field {name}, expected Effect")
                names.append(effect_name)
                setattr(instance, name, Effect(i))
                i += 1
            case EffectGroupInfo(names=effect_names):
                if annotation_type is not EffectGroup:
                    raise TypeError(
                        f"Invalid annotation for effects: {described} on field {name}, expected EffectGroup"
                    )
                start_id = i
                count = len(effect_names)
                names.extend(effect_names)
                setattr(instance, name, EffectGroup(start_id, count))
                i += count
            case _:
                raise TypeError(
                    f"Invalid annotation for effects: {described} on field {name}, unknown effect info, "
                    f"expected an effect() or effect_group() specifier"
                )
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
    """An effect set with no effects, used as the default when a mode declares none."""
