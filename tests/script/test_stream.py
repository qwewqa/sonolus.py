from typing import Annotated, ClassVar, Final

import pytest

from sonolus.script.num import Num
from sonolus.script.stream import (
    Stream,
    StreamGroup,
    _StreamDataField,  # noqa: PLC2701
    _StreamField,  # noqa: PLC2701
    streams,
)
from sonolus.script.vec import Vec2


def test_streams_rejects_final_stream():
    # Final[Stream[...]] normalizes back to Stream[...], which must not be accepted as a data field.
    @streams
    class Strms:
        bad: Final[Stream[int]]

    with pytest.raises(TypeError, match="not supported as a streams data field"):
        type(Strms)._init_()


def test_streams_rejects_final_stream_group():
    @streams
    class Strms:
        bad: Final[StreamGroup[int, 10]]

    with pytest.raises(TypeError, match="not supported as a streams data field"):
        type(Strms)._init_()


def test_streams_reports_a_real_default_value():
    @streams
    class Strms:
        bad: Stream[int] = 5

    with pytest.raises(TypeError, match="Default values are not supported for streams fields"):
        type(Strms)._init_()


def test_streams_does_not_report_annotated_as_a_default_value():
    # A class value arrives as Annotated[...], and so does a user-written Annotated[...] with no default at all.
    @streams
    class Strms:
        bad: Annotated[Stream[int], "doc"]

    with pytest.raises(TypeError, match="not supported as a streams data field"):
        type(Strms)._init_()


@pytest.mark.parametrize(
    ("annotation", "message"),
    [
        (str, "Unsupported type spec"),
        (ClassVar[int], "Unsupported value"),
        (Stream, "Must have type arguments"),
    ],
    ids=["unsupported-type", "classvar", "unparameterized-stream"],
)
def test_streams_errors_name_the_offending_field(annotation, message):
    @streams
    class Strms:
        good: Stream[int]
        offender: annotation

    with pytest.raises(TypeError, match=rf"Error processing streams field 'offender'.*{message}"):
        type(Strms)._init_()


def test_streams_layout_and_descriptors():
    @streams
    class Strms:
        stream: Stream[int]
        group: StreamGroup[int, 10]
        data_field: int
        final_data_field: Final[int]
        vec_field: Vec2

    cls = type(Strms)
    cls._init_()

    assert cls._streams_ == [
        ("stream", 1, Stream[Num]),
        ("group", 2, StreamGroup[Num, 10]),
        ("data_field", 12, Num),
        ("final_data_field", 13, Num),
        ("vec_field", 14, Vec2),
    ]

    descriptors = {name: cls.__dict__[name] for name, _, _ in cls._streams_}
    assert type(descriptors["stream"]) is _StreamField
    assert type(descriptors["group"]) is _StreamField
    assert type(descriptors["data_field"]) is _StreamDataField
    assert type(descriptors["final_data_field"]) is _StreamDataField
    assert type(descriptors["vec_field"]) is _StreamDataField
