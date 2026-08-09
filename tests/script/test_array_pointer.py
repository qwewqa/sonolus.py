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


def test_array_pointer_getitem_negative_index():
    def fn():
        ptr = ArrayPointer[Point](3, -6, 0)
        ptr[0] = Point(1, 2)
        ptr[1] = Point(3, 4)
        ptr[2] = Point(5, 6)
        last = ptr[-1]
        second_last = ptr[-2]
        return last.x + last.y * 10 + second_last.x * 100 + second_last.y * 1000

    assert run_compiled(fn) == 4365


def test_array_pointer_setitem_negative_index():
    def fn():
        ptr = ArrayPointer[Point](3, -7, 0)
        ptr[-1] = Point(1, 2)
        ptr[-2] = Point(3, 4)
        ptr[-3] = Point(5, 6)
        first = ptr[0]
        middle = ptr[1]
        last = ptr[2]
        return first.x + first.y * 10 + middle.x * 100 + middle.y * 1000 + last.x * 10000 + last.y * 100000

    assert run_compiled(fn) == 214365
