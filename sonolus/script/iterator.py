from __future__ import annotations

from abc import abstractmethod
from collections.abc import Collection, Iterator

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


class ArrayLike[T](Collection):
    @abstractmethod
    def size(self) -> int:
        pass

    @abstractmethod
    def __getitem__(self, index: Num) -> T:
        pass

    @abstractmethod
    def __setitem__(self, index: Num, value: T):
        pass

    def __len__(self) -> int:
        return self.size()

    def __iter__(self) -> SonolusIterator[T]:
        return ArrayIterator(0, self)

    def __contains__(self, value: T) -> bool:
        i = 0
        while i < self.size():
            if self[i] == value:
                return True
            i += 1
        return False

    def reversed(self) -> ArrayLike[T]:
        return ArrayReverser(self)

    def iter(self) -> SonolusIterator[T]:
        return self.__iter__()  # noqa: PLC2801

    def enumerate(self, start: Num = 0) -> SonolusIterator[T]:
        return ArrayEnumerator(0, start, self)

    def index_of(self, value: T, start: Num = 0) -> Num:
        i = start
        while i < self.size():
            if self[i] == value:
                return i
            i += 1
        return -1

    def last_index_of(self, value: T) -> Num:
        i = self.size() - 1
        while i >= 0:
            if self[i] == value:
                return i
            i -= 1
        return -1

    def index_of_max(self) -> Num:
        if self.size() == 0:
            return -1
        max_index = 0
        i = 1
        while i < self.size():
            if self[i] > self[max_index]:
                max_index = i
            i += 1
        return max_index

    def index_of_min(self) -> Num:
        if self.size() == 0:
            return -1
        min_index = 0
        i = 1
        while i < self.size():
            if self[i] < self[min_index]:
                min_index = i
            i += 1
        return min_index

    def max(self) -> T:
        return self[self.index_of_max()]

    def min(self) -> T:
        return self[self.index_of_min()]

    def swap(self, i: Num, j: Num):
        temp = copy(self[i])
        self[i] = self[j]
        self[j] = temp

    def sort(self, *, reverse: bool = False):
        if self.size() < 15:
            _insertion_sort(self, 0, self.size(), reverse)
        else:
            _heap_sort(self, 0, self.size(), reverse)


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


class ArrayIterator[V: ArrayLike](Record, SonolusIterator):
    i: int
    array: V

    def has_next(self) -> bool:
        return self.i < self.array.size()

    def next(self) -> V:
        value = self.array[self.i]
        self.i += 1
        return value


class ArrayReverser[V: ArrayLike](Record, ArrayLike):
    array: V

    def size(self) -> int:
        return self.array.size()

    def __getitem__(self, index: Num) -> V:
        return self.array[self.size() - 1 - index]

    def __setitem__(self, index: Num, value: V):
        self.array[self.size() - 1 - index] = value

    def reversed(self) -> ArrayLike[V]:
        return self.array


class Enumerator[V: SonolusIterator](Record, SonolusIterator):
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


class ArrayEnumerator[V: ArrayLike](Record, SonolusIterator):
    i: int
    offset: int
    array: V

    def has_next(self) -> bool:
        return self.i < self.array.size()

    def next(self):
        value = self.array[self.i]
        index = self.i + self.offset
        self.i += 1
        return index, value
