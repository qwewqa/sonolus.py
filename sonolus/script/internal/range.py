from sonolus.script.array_like import ArrayLike
from sonolus.script.iterator import SonolusIterator
from sonolus.script.num import Num
from sonolus.script.record import Record


class Range(Record, ArrayLike[Num]):
    start: int
    stop: int
    step: int

    def __new__(cls, start: Num, stop: Num | None = None, step: Num = 1):
        if stop is None:
            start, stop = 0, start
        return super().__new__(cls, start, stop, step)

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

    def __getitem__(self, index: Num) -> Num:
        return self.start + index * self.step

    def __setitem__(self, index: Num, value: Num):
        raise TypeError("Range does not support item assignment")

    @property
    def last(self) -> Num:
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
    stop: int
    step: int

    def has_next(self) -> bool:
        if self.step > 0:
            return self.value < self.stop
        else:
            return self.value > self.stop

    def get(self) -> int:
        return self.value

    def advance(self):
        self.value += self.step
