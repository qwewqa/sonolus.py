from pydori.lib.skin import Skin
from sonolus.script.bucket import Bucket, JudgmentWindow, bucket, bucket_sprite, buckets
from sonolus.script.interval import Interval
from sonolus.script.runtime import level_score
from sonolus.script.text import StandardText


@buckets
class Buckets:
    tap_note: Bucket = bucket(
        sprites=[
            bucket_sprite(
                sprite=Skin.tap_note,
                x=0,
                y=0,
                w=2,
                h=2,
                rotation=-90,
            ),
        ],
        unit=StandardText.MILLISECOND_UNIT,
    )
    hold_head_note: Bucket = bucket(
        sprites=[
            bucket_sprite(
                sprite=Skin.hold_connector,
                x=0.5,
                y=0,
                w=2,
                h=5,
                rotation=-90,
            ),
            bucket_sprite(
                sprite=Skin.hold_head_note,
                x=-2,
                y=0,
                w=2,
                h=2,
                rotation=-90,
            ),
        ],
        unit=StandardText.MILLISECOND_UNIT,
    )
    hold_end_note: Bucket = bucket(
        sprites=[
            bucket_sprite(
                sprite=Skin.hold_connector,
                x=-0.5,
                y=0,
                w=2,
                h=5,
                rotation=-90,
            ),
            bucket_sprite(
                sprite=Skin.hold_end_note,
                x=2,
                y=0,
                w=2,
                h=2,
                rotation=-90,
            ),
        ],
        unit=StandardText.MILLISECOND_UNIT,
    )
    hold_tick_note: Bucket = bucket(
        sprites=[
            bucket_sprite(
                sprite=Skin.hold_connector,
                x=0,
                y=0,
                w=2,
                h=5.5,
                rotation=-90,
            ),
            bucket_sprite(
                sprite=Skin.hold_tick_note,
                x=0,
                y=0,
                w=2,
                h=2,
                rotation=-90,
            ),
        ],
        unit=StandardText.MILLISECOND_UNIT,
    )
    flick_note: Bucket = bucket(
        sprites=[
            bucket_sprite(
                sprite=Skin.flick_note,
                x=0,
                y=0,
                w=2,
                h=2,
                rotation=-90,
            ),
            bucket_sprite(
                sprite=Skin.flick_arrow,
                x=1,
                y=0,
                w=2,
                h=2,
                rotation=-90,
            ),
        ],
        unit=StandardText.MILLISECOND_UNIT,
    )
    directional_flick_note: Bucket = bucket(
        sprites=[
            bucket_sprite(
                sprite=Skin.right_flick_note,
                x=2,
                y=0,
                w=2,
                h=2,
                rotation=-90,
            ),
            bucket_sprite(
                sprite=Skin.left_flick_note,
                x=-2,
                y=0,
                w=2,
                h=2,
                rotation=90,
            ),
            bucket_sprite(
                sprite=Skin.right_flick_arrow,
                x=3,
                y=0,
                w=2,
                h=2,
                rotation=-90,
            ),
            bucket_sprite(
                sprite=Skin.left_flick_arrow,
                x=-3,
                y=0,
                w=2,
                h=2,
                rotation=90,
            ),
        ],
        unit=StandardText.MILLISECOND_UNIT,
    )


note_judgment_window = JudgmentWindow(
    perfect=Interval(-0.05, 0.05),
    great=Interval(-0.1, 0.1),
    good=Interval(-0.15, 0.15),
)


def init_buckets():
    # Multiply by 1000 so buckets are in milliseconds.
    Buckets.tap_note.window @= note_judgment_window * 1000
    Buckets.hold_head_note.window @= note_judgment_window * 1000
    Buckets.hold_end_note.window @= note_judgment_window * 1000
    Buckets.hold_tick_note.window @= note_judgment_window * 1000
    Buckets.flick_note.window @= note_judgment_window * 1000
    Buckets.directional_flick_note.window @= note_judgment_window * 1000


def init_score():
    level_score().update(
        perfect_multiplier=1,
        great_multiplier=0.5,
        good_multiplier=0.25,
    )
