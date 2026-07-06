"""Tests for sonolus.script.containers.ArrayPointer."""

from sonolus.script.containers import ArrayPointer
from sonolus.script.record import Record
from tests.script.conftest import run_compiled


class Point(Record):
    x: int
    y: int


def test_array_pointer_setitem_record_element():
    def fn():
        ptr = ArrayPointer[Point](2, -5, 0)
        ptr[0] = Point(3, 4)
        ptr[1] = Point(5, 6)
        return ptr[0].x + ptr[0].y * 10 + ptr[1].x * 100 + ptr[1].y * 1000

    assert run_compiled(fn) == 6543
