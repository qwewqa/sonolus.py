from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Annotated, Any, NewType, dataclass_transform, get_origin

from sonolus.backend.mode import Mode
from sonolus.backend.ops import Op
from sonolus.script.internal.context import ctx
from sonolus.script.internal.impl import meta_fn
from sonolus.script.internal.introspection import get_field_specifiers
from sonolus.script.internal.native import native_function
from sonolus.script.interval import Interval
from sonolus.script.pointer import _deref
from sonolus.script.record import Record
from sonolus.script.sprite import Sprite


class JudgmentWindow(Record):
    """The window for judging the accuracy of a hit.

    Usage:
        ```python
        JudgmentWindow(perfect: Interval, great: Interval, good: Interval)
        ```
    """

    perfect: Interval
    """Interval for a perfect hit."""

    great: Interval
    """Interval for a great hit."""

    good: Interval
    """Interval for a good hit."""

    def update(
        self,
        perfect: Interval | None = None,
        great: Interval | None = None,
        good: Interval | None = None,
    ):
        """Update the window with the given intervals.

        Args:
            perfect: The interval for a perfect hit.
            great: The interval for a great hit.
            good: The interval for a good hit.
        """
        if perfect is not None:
            self.perfect = perfect
        if great is not None:
            self.great = great
        if good is not None:
            self.good = good

    def judge(self, actual: float, target: float) -> Judgment:
        """Judge the accuracy of a hit.

        Args:
            actual: The actual time of the hit.
            target: The target time of the hit.

        Returns:
            The [`Judgment`][sonolus.script.bucket.Judgment] of the hit.
        """
        return _judge(
            actual,
            target,
            *self.perfect.tuple,
            *self.great.tuple,
            *self.good.tuple,
        )

    def __mul__(self, other: float | int) -> JudgmentWindow:
        """Multiply the intervals by a scalar."""
        return JudgmentWindow(
            self.perfect * other,
            self.great * other,
            self.good * other,
        )

    def __add__(self, other: float | int) -> JudgmentWindow:
        """Add a scalar to the intervals."""
        return JudgmentWindow(
            self.perfect + other,
            self.great + other,
            self.good + other,
        )

    @property
    def start(self) -> float:
        """The start time of the good interval."""
        return self.good.start

    @property
    def end(self) -> float:
        """The end time of the good interval."""
        return self.good.end


class Judgment(IntEnum):
    """The judgment of a hit."""

    MISS = 0
    PERFECT = 1
    GREAT = 2
    GOOD = 3


@native_function(Op.Judge)
def _judge(
    actual: float,
    target: float,
    perfect_min: float,
    perfect_max: float,
    great_min: float,
    great_max: float,
    good_min: float,
    good_max: float,
) -> Judgment:
    diff = actual - target
    if perfect_min <= diff <= perfect_max:
        return Judgment.PERFECT
    if great_min <= diff <= great_max:
        return Judgment.GREAT
    if good_min <= diff <= good_max:
        return Judgment.GOOD
    return Judgment.MISS


class Bucket(Record):
    """A bucket for entity judgment results.

    Usage:
        ```python
        Bucket(id: int)
        ```
    """

    id: int
    """Bucket ID."""

    @property
    @meta_fn
    def window(self) -> JudgmentWindow:
        """The judgment window of the bucket."""
        if not ctx():
            raise RuntimeError("Bucket window access outside of compilation")
        match ctx().global_state.mode:
            case Mode.PLAY:
                return _deref(ctx().blocks.LevelBucket, self.id * JudgmentWindow._size_(), JudgmentWindow)
            case Mode.WATCH:
                return _deref(ctx().blocks.LevelBucket, self.id * JudgmentWindow._size_(), JudgmentWindow)
            case _:
                raise RuntimeError("Invalid mode for bucket window access")

    @window.setter
    @meta_fn
    def window(self, value: JudgmentWindow):
        if not ctx():
            raise RuntimeError("Bucket window access outside of compilation")
        self.window.update(value.perfect, value.great, value.good)


@dataclass
class _BucketSprite:
    id: int
    fallback_id: int | None
    x: float
    y: float
    w: float
    h: float
    rotation: float

    def to_dict(self):
        results = {
            "id": self.id,
            "x": self.x,
            "y": self.y,
            "w": self.w,
            "h": self.h,
            "rotation": self.rotation,
        }
        if self.fallback_id is not None:
            results["fallbackId"] = self.fallback_id
        return results


@dataclass
class _BucketInfo:
    sprites: list[_BucketSprite]
    unit: str | None = None

    def to_dict(self):
        results = {
            "sprites": [sprite.to_dict() for sprite in self.sprites],
        }
        if self.unit is not None:
            results["unit"] = self.unit
        return results


def bucket_sprite(
    *,
    sprite: Sprite,
    fallback_sprite: Sprite | None = None,
    x: float,
    y: float,
    w: float,
    h: float,
    rotation: float = 0,
) -> _BucketSprite:
    """Define a sprite for a bucket."""
    return _BucketSprite(sprite.id, fallback_sprite.id if fallback_sprite else None, x, y, w, h, rotation)


def bucket(*, sprites: list[_BucketSprite], unit: str | None = None) -> Any:
    """Define a bucket with the given sprites and unit."""
    return _BucketInfo(sprites, unit)


type Buckets = NewType("Buckets", Any)


@dataclass_transform()
def buckets[T](cls: type[T]) -> T | Buckets:
    """Decorator to define a buckets class.

    Usage:
        ```python
        @buckets
        class Buckets:
            note: Bucket = bucket(
                sprites=[
                    bucket_sprite(
                        sprite=Skin.note,
                        x=0,
                        y=0,
                        w=2,
                        h=2,
                    )
                ],
                unit=StandardText.MILLISECOND_UNIT,
            )
        ```
    """
    if len(cls.__bases__) != 1:
        raise ValueError("Buckets class must not inherit from any class (except object)")
    instance = cls()
    bucket_info = []
    for i, (name, annotation) in enumerate(get_field_specifiers(cls).items()):
        if get_origin(annotation) is not Annotated:
            raise TypeError(f"Invalid annotation for buckets: {annotation}")
        annotation_type = annotation.__args__[0]
        annotation_values = annotation.__metadata__
        if annotation_type is not Bucket:
            raise TypeError(f"Invalid annotation for buckets: {annotation}, expected annotation of type Bucket")
        if len(annotation_values) != 1 or not isinstance(annotation_values[0], _BucketInfo):
            raise TypeError(
                f"Invalid annotation for buckets: {annotation}, expected a single BucketInfo annotation value"
            )
        info = annotation_values[0]
        bucket_info.append(info)
        setattr(instance, name, Bucket(i))
    instance._buckets_ = bucket_info
    instance._is_comptime_value_ = True
    return instance


@buckets
class EmptyBuckets:
    pass
