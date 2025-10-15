# Buckets & Judgment

## Declaring Buckets

[Buckets][sonolus.script.bucket.Bucket] define how notes are displayed on the results screen. Typically, each kind of
note has its own bucket and therefore its own entry on the results screen. Buckets are defined using the
[`@buckets`][sonolus.script.bucket.buckets] decorator and the [`bucket`][sonolus.script.bucket.bucket] function:

```python
from sonolus.script.bucket import buckets, bucket, Bucket, bucket_sprite
from sonolus.script.text import StandardText

Skin = ...

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
```

Buckets have sprites used for diplay on the results screen, which are defined using the
[`bucket_sprite`][sonolus.script.bucket.bucket_sprite] function. They may also have a unit, which can either be
a built-in from [`StandardText`][sonolus.script.text.StandardText] or a custom string. Conventionally, the unit for
most engines should be set to
[`StandardText.MILLISECOND_UNIT`][sonolus.script.text.StandardText] to indicate that the bucket is
measured in milliseconds.

## Judgment Windows

Sonolus notes are judged on a scale from best to worst of:

- [`Judgment.PERFECT`][sonolus.script.bucket.Judgment.PERFECT]
- [`Judgment.GREAT`][sonolus.script.bucket.Judgment.GREAT]
- [`Judgment.GOOD`][sonolus.script.bucket.Judgment.GOOD]
- [`Judgment.MISS`][sonolus.script.bucket.Judgment.MISS]

The [`JudgmentWindow`][sonolus.script.bucket.JudgmentWindow] class is useful for defining the judgment windows of a
note in seconds, and judging a note based on the actual and expected (target) hit times:

```python
from sonolus.script.bucket import JudgmentWindow, Judgment
from sonolus.script.interval import Interval

note_judgment_window = JudgmentWindow(
    perfect=Interval(-0.05, 0.05),
    great=Interval(-0.1, 0.1),
    good=Interval(-0.15, 0.15),
)

actual_time: float = ...
expected_time: float = ...
judgment = note_judgment_window.judge(actual=actual_time, expected=expected_time)
```

## Bucket Window

For each bucket, the [`window`][sonolus.script.bucket.Bucket.window] attribute should be set during preprocessing to
define the judgment windows for the bucket:

```python
from sonolus.script.bucket import JudgmentWindow

note_judgment_window: JudgmentWindow = ...
Buckets = ...

def init_buckets():
    # Multiply by 1000 so buckets are in milliseconds.
    Buckets.tap_note.window @= note_judgment_window * 1000
```
