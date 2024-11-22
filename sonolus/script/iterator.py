from __future__ import annotations

from abc import abstractmethod
from collections.abc import Iterator, Sequence
from typing import Any

from sonolus.script.num import Num
from sonolus.script.record import Record
from sonolus.script.values import copy


class SonolusIterator[T](Iterator[T]):
    @abstractmethod
    def has_next(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def next(self) -> T:
        raise NotImplementedError

    def __next__(self) -> T:
        if not self.has_next():
            raise StopIteration
        return self.next()

    def __iter__(self) -> SonolusIterator[T]:
        return self


class ArrayLike[T](Sequence):
    # We can't use range() here since Range itself depends on ArrayLike

    @abstractmethod
    def __len__(self) -> int:
        pass

    @abstractmethod
    def __getitem__(self, index: Num) -> T:
        pass

    @abstractmethod
    def __setitem__(self, index: Num, value: T):
        pass

    def __iter__(self) -> SonolusIterator[T]:
        return _ArrayIterator(0, self)

    def __contains__(self, value: Any) -> bool:
        i = 0
        while i < len(self):
            if self[i] == value:
                return True
            i += 1
        return False

    def __reversed__(self):
        return _ArrayReverser(self)

    def _enumerate_(self, start: Num = 0) -> SonolusIterator[T]:
        return _ArrayEnumerator(0, start, self)

    def index(self, value: T, start: Num = 0, stop: Num | None = None) -> Num:
        if stop is None:
            stop = len(self)
        i = start
        while i < stop:
            if self[i] == value:
                return i
            i += 1
        return -1

    def count(self, value: T) -> Num:
        count = 0
        i = 0
        while i < len(self):
            if self[i] == value:
                count += 1
            i += 1
        return count

    def last_index(self, value: T) -> Num:
        i = len(self) - 1
        while i >= 0:
            if self[i] == value:
                return i
            i -= 1
        return -1

    def index_of_max(self) -> Num:
        if len(self) == 0:
            return -1
        max_index = 0
        i = 1
        while i < len(self):
            if self[i] > self[max_index]:
                max_index = i
            i += 1
        return max_index

    def index_of_min(self) -> Num:
        if len(self) == 0:
            return -1
        min_index = 0
        i = 1
        while i < len(self):
            if self[i] < self[min_index]:
                min_index = i
            i += 1
        return min_index

    def _max_(self) -> T:
        return self[self.index_of_max()]

    def _min_(self) -> T:
        return self[self.index_of_min()]

    def swap(self, i: Num, j: Num):
        temp = copy(self[i])
        self[i] = self[j]
        self[j] = temp

    def sort(self, *, reverse: bool = False):
        if len(self) < 15:
            _insertion_sort(self, 0, len(self), reverse)
        else:
            _heap_sort(self, 0, len(self), reverse)

    def reverse(self):
        i = 0
        j = len(self) - 1
        while i < j:
            self.swap(i, j)
            i += 1
            j -= 1


def _insertion_sort[T](array: ArrayLike[T], start: Num, end: Num, reverse: bool):
    i = start + 1
    while i < end:
        value = copy(array[i])
        j = i - 1
        while j >= start and (array[j] > value) != reverse:
            array[j + 1] = array[j]
            j -= 1
        array[j + 1] = value
        i += 1


def _heapify[T](array: ArrayLike[T], end: Num, index: Num, reverse: bool):
    while True:
        left = index * 2 + 1
        right = left + 1
        largest = index
        if left < end and (array[left] > array[largest]) != reverse:
            largest = left
        if right < end and (array[right] > array[largest]) != reverse:
            largest = right
        if largest == index:
            break
        array.swap(index, largest)
        index = largest


# Heap sort is simple to implement iteratively without dynamic memory allocation
def _heap_sort[T](array: ArrayLike[T], start: Num, end: Num, reverse: bool):
    i = end // 2 - 1
    while i >= start:
        _heapify(array, end, i, reverse)
        i -= 1
    i = end - 1
    while i > start:
        array.swap(start, i)
        _heapify(array, i, start, reverse)
        i -= 1


class _ArrayIterator[V: ArrayLike](Record, SonolusIterator):
    i: int
    array: V

    def has_next(self) -> bool:
        return self.i < len(self.array)

    def next(self) -> V:
        value = self.array[self.i]
        self.i += 1
        return value


class _ArrayReverser[V: ArrayLike](Record, ArrayLike):
    array: V

    def __len__(self) -> int:
        return len(self.array)

    def __getitem__(self, index: Num) -> V:
        return self.array[len(self) - 1 - index]

    def __setitem__(self, index: Num, value: V):
        self.array[len(self) - 1 - index] = value

    def reversed(self) -> ArrayLike[V]:
        return self.array


class _Enumerator[V: SonolusIterator](Record, SonolusIterator):
    i: int
    offset: int
    iterator: V

    def has_next(self) -> bool:
        return self.iterator.has_next()

    def next(self):
        value = self.iterator.next()
        index = self.i + self.offset
        self.i += 1
        return index, value


class _ArrayEnumerator[V: ArrayLike](Record, SonolusIterator):
    i: int
    offset: int
    array: V

    def has_next(self) -> bool:
        return self.i < len(self.array)

    def next(self):
        value = self.array[self.i]
        index = self.i + self.offset
        self.i += 1
        return index, value
