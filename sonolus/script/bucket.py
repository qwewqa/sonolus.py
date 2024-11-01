from dataclasses import dataclass
from typing import Annotated, Protocol, get_origin

from sonolus.backend.mode import Mode
from sonolus.script.internal.context import ctx
from sonolus.script.internal.impl import meta_fn
from sonolus.script.internal.introspection import get_field_specifiers
from sonolus.script.pointer import deref
from sonolus.script.record import Record
from sonolus.script.sprite import Sprite


class BucketWindow(Record):
    perfect_min: float
    perfect_max: float
    great_min: float
    great_max: float
    good_min: float
    good_max: float

    def update(
        self,
        perfect_min: float,
        perfect_max: float,
        great_min: float,
        great_max: float,
        good_min: float,
        good_max: float,
    ):
        self.perfect_min = perfect_min
        self.perfect_max = perfect_max
        self.great_min = great_min
        self.great_max = great_max
        self.good_min = good_min
        self.good_max = good_max


class Bucket(Record):
    id: int

    @property
    @meta_fn
    def window(self) -> BucketWindow:
        if not ctx():
            raise RuntimeError("Bucket window access outside of compilation")
        match ctx().global_state.mode:
            case Mode.Play:
                return deref(ctx().blocks.LevelBucket, self.id * BucketWindow._size_(), BucketWindow)
            case Mode.Watch:
                return deref(ctx().blocks.LevelBucket, self.id * BucketWindow._size_(), BucketWindow)
            case _:
                raise RuntimeError("Invalid mode for bucket window access")


@dataclass
class BucketSprite:
    id: int
    fallback_id: int | None
    x: int
    y: int
    w: int
    h: int
    rotation: float


@dataclass
class BucketInfo:
    sprites: list[BucketSprite]
    unit: str | None = None


def bucket_sprite(
    *,
    sprite: Sprite,
    fallback_sprite: Sprite | None = None,
    x: int,
    y: int,
    w: int,
    h: int,
    rotation: float,
) -> BucketSprite:
    return BucketSprite(sprite.id, fallback_sprite.id if fallback_sprite else None, x, y, w, h, rotation)


def bucket(*, sprites: list[BucketSprite], unit: str | None = None) -> BucketInfo:
    return BucketInfo(sprites, unit)


class Buckets(Protocol):
    _buckets_: list[BucketInfo]


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
