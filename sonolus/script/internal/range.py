from typing import Final, Self

from sonolus.script.array_like import ArrayLike, get_positive_index
from sonolus.script.debug import assert_true, static_error
from sonolus.script.internal.context import ctx
from sonolus.script.internal.impl import validate_value
from sonolus.script.internal.meta_fn import meta_fn
from sonolus.script.iterator import SonolusIterator
from sonolus.script.maybe import Maybe, Nothing, Some
from sonolus.script.num import Num
from sonolus.script.record import Record


class Range(Record, ArrayLike[int]):
    start: int
    stop: int
    step: int

    def __new__(cls, start: int, stop: int | None = None, step: int = 1, /):
        if stop is None:
            start, stop = 0, start
        return super().__new__(cls, start, stop, step)

    @classmethod
    @meta_fn
    def frozen(cls, start: int, stop: int | None = None, step: int = 1, /) -> Self:
        if stop is None:
            start, stop = 0, start
        start = Num._accept_(start)
        stop = Num._accept_(stop)
        step = Num._accept_(step)
        assert_true(start % 1 == 0, "range() arguments must be integers")
        assert_true(stop % 1 == 0, "range() arguments must be integers")
        assert_true(step % 1 == 0, "range() arguments must be integers")
        assert_true(step != 0, "range() arg 3 must not be zero")
        return super().frozen(start, stop, step)

    def __iter__(self) -> SonolusIterator:
        return RangeIterator(self.start, self.stop, self.step)

    def __contains__(self, item):
        if self.step > 0:
            return self.start <= item < self.stop and (item - self.start) % self.step == 0
        else:
            return self.stop < item <= self.start and (self.start - item) % -self.step == 0

    def __len__(self) -> int:
        if self.step > 0:
            diff = self.stop - self.start
            if diff <= 0:
                return 0
            return (diff + self.step - 1) // self.step
        else:
            diff = self.start - self.stop
            if diff <= 0:
                return 0
            return (diff - self.step - 1) // -self.step

    def __getitem__(self, index: int) -> int:
        return self.start + get_positive_index(index, len(self)) * self.step

    def get_unchecked(self, index: Num) -> int:
        return self.start + index * self.step

    def __setitem__(self, index: int, value: int):
        static_error("Range does not support item assignment")

    def index(self, value: int, start: int = 0, stop: int | None = None) -> int:
        """Return the index of the first element of the range equal to the given value.

        Args:
            value: The value to search for.
            start: The index to start searching from.
            stop: The index to stop searching at. If `None`, search to the end of the range.
        """
        result = super().index(value, start, stop)
        assert_true(result != -1, "range.index(x): x not in range")
        return result

    @property
    def last(self) -> int:
        return self[len(self) - 1]

    def __eq__(self, other):
        if not isinstance(other, Range):
            return False
        len_self = len(self)
        len_other = len(other)
        if len_self != len_other:
            return False
        if len_self == 0:
            return True
        return self.start == other.start and self.last == other.last

    def __ne__(self, other):
        return not self == other

    def __hash__(self):
        raise TypeError("Range is not hashable")


class RangeIterator(Record, SonolusIterator):
    value: int
    stop: Final[int]
    step: Final[int]

    def next(self) -> Maybe[int]:
        has_next = self.value < self.stop if self.step > 0 else self.value > self.stop
        if has_next:
            current = self.value
            self.value += self.step
            return Some(current)
        return Nothing


@meta_fn
def range_or_tuple(start: int, stop: int | None = None, step: int = 1) -> Range | tuple[int, ...]:
    if stop is None:
        start, stop = 0, start
    if not ctx():
        return range(start, stop, step)  # type: ignore
    start = Num._accept_(start)
    stop = Num._accept_(stop) if stop is not None else None
    step = Num._accept_(step)
    assert_true(start % 1 == 0, "range() arguments must be integers")
    assert_true(stop % 1 == 0, "range() arguments must be integers")
    assert_true(step % 1 == 0, "range() arguments must be integers")
    if start._is_py_() and stop._is_py_() and step._is_py_():
        start_int = start._as_py_()
        stop_int = stop._as_py_() if stop is not None else None
        if stop_int is None:
            start_int, stop_int = 0, start_int
        step_int = step._as_py_()
        # Keep it as a runtime failure if step is 0
        if step_int != 0:
            return validate_value(tuple(range(int(start_int), int(stop_int), int(step_int))))  # type: ignore
    return Range.frozen(start, stop, step)
