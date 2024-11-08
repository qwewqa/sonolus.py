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
from sonolus.script.pointer import deref
from sonolus.script.record import Record
from sonolus.script.sprite import Sprite


class JudgmentWindow(Record):
    perfect: Interval
    great: Interval
    good: Interval

    def update(
        self,
        perfect: Interval | None = None,
        great: Interval | None = None,
        good: Interval | None = None,
    ):
        if perfect is not None:
            self.perfect = perfect
        if great is not None:
            self.great = great
        if good is not None:
            self.good = good

    def judge(self, actual: float, target: float) -> Judgment:
        return _judge(
            actual,
            target,
            *self.perfect.tuple,
            *self.great.tuple,
            *self.good.tuple,
        )

    def __mul__(self, other: float | int) -> JudgmentWindow:
        return JudgmentWindow(
            self.perfect * other,
            self.great * other,
            self.good * other,
        )


class Judgment(IntEnum):
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
    id: int

    @property
    @meta_fn
    def window(self) -> JudgmentWindow:
        if not ctx():
            raise RuntimeError("Bucket window access outside of compilation")
        match ctx().global_state.mode:
            case Mode.PLAY:
                return deref(ctx().blocks.LevelBucket, self.id * JudgmentWindow._size_(), JudgmentWindow)
            case Mode.WATCH:
                return deref(ctx().blocks.LevelBucket, self.id * JudgmentWindow._size_(), JudgmentWindow)
            case _:
                raise RuntimeError("Invalid mode for bucket window access")

    @window.setter
    @meta_fn
    def window(self, value: JudgmentWindow):
        if not ctx():
            raise RuntimeError("Bucket window access outside of compilation")
        self.window.update(value.perfect, value.great, value.good)


@dataclass
class BucketSprite:
    id: int
    fallback_id: int | None
    x: int
    y: int
    w: int
    h: int
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
class BucketInfo:
    sprites: list[BucketSprite]
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
    x: int,
    y: int,
    w: int,
    h: int,
    rotation: float = 0,
) -> BucketSprite:
    return BucketSprite(sprite.id, fallback_sprite.id if fallback_sprite else None, x, y, w, h, rotation)


def bucket(*, sprites: list[BucketSprite], unit: str | None = None) -> Any:
    return BucketInfo(sprites, unit)


type Buckets = NewType("Buckets", Any)


@dataclass_transform()
def buckets[T](cls: type[T]) -> T | Buckets:
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
        if len(annotation_values) != 1 or not isinstance(annotation_values[0], BucketInfo):
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
