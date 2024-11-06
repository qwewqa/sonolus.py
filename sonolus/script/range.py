from sonolus.script.iterator import ArrayLike, SonolusIterator
from sonolus.script.num import Num
from sonolus.script.record import Record


class Range(Record, ArrayLike[Num]):
    start: int
    end: int
    step: int

    def __new__(cls, start: Num, end: Num | None = None, step: Num = 1):
        if end is None:
            start, end = 0, start
        return super().__new__(cls, start, end, step)

    def __iter__(self) -> SonolusIterator:
        return RangeIterator(self.start, self.end, self.step)

    def __contains__(self, item):
        if self.step > 0:
            return self.start <= item < self.end and (item - self.start) % self.step == 0
        else:
            return self.end < item <= self.start and (self.start - item) % -self.step == 0

    def size(self) -> int:
        if self.step > 0:
            diff = self.end - self.start
            if diff <= 0:
                return 0
            return (diff + self.step - 1) // self.step
        else:
            diff = self.start - self.end
            if diff <= 0:
                return 0
            return (diff - self.step - 1) // -self.step

    def __getitem__(self, index: Num) -> Num:
        return self.start + index * self.step

    def __setitem__(self, index: Num, value: Num):
        raise TypeError("Range does not support item assignment")


class RangeIterator(Record, SonolusIterator):
    value: int
    end: int
    step: int

    def has_next(self) -> bool:
        if self.step > 0:
            return self.value < self.end
        else:
            return self.value > self.end

    def next(self) -> Num:
        value = self.value
        self.value += self.step
        return value
