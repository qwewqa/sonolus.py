from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Sequence
from typing import Any

from sonolus.script.internal.impl import meta_fn
from sonolus.script.num import Num
from sonolus.script.record import Record
from sonolus.script.values import copy


class SonolusIterator[T](Iterator[T]):
    def next(self) -> T:
        result = self.get()
        self.advance()
        return result

    @abstractmethod
    def has_next(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def get(self) -> T:
        raise NotImplementedError

    @abstractmethod
    def advance(self):
        raise NotImplementedError

    def __next__(self) -> T:
        if not self.has_next():
            raise StopIteration
        return self.next()

    def __iter__(self) -> SonolusIterator[T]:
        return self


class ArrayLike[T](Sequence, ABC):
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

    def index_of_max(self, *, key: Callable[T, Any] | None = None) -> Num:
        if len(self) == 0:
            return -1
        if key is None:
            key = _identity
        max_index = 0
        i = 1
        while i < len(self):
            if key(self[i]) > key(self[max_index]):
                max_index = i
            i += 1
        return max_index

    def index_of_min(self, *, key: Callable[T, Any] | None = None) -> Num:
        if len(self) == 0:
            return -1
        if key is None:
            key = _identity
        min_index = 0
        i = 1
        while i < len(self):
            if key(self[i]) < key(self[min_index]):
                min_index = i
            i += 1
        return min_index

    def _max_(self, key: Callable[T, Any] | None = None) -> T:
        return self[self.index_of_max(key=key)]

    def _min_(self, key: Callable[T, Any] | None = None) -> T:
        return self[self.index_of_min(key=key)]

    def swap(self, i: Num, j: Num):
        temp = copy(self[i])
        self[i] = self[j]
        self[j] = temp

    def sort(self, *, key: Callable[T, Any] | None = None, reverse: bool = False):
        if len(self) < 15 or key is not None:
            if key is None:
                key = _identity
            _insertion_sort(self, 0, len(self), key, reverse)
        else:
            # Heap sort is unstable, so if there's a key, we can't rely on it
            _heap_sort(self, 0, len(self), reverse)

    def reverse(self):
        i = 0
        j = len(self) - 1
        while i < j:
            self.swap(i, j)
            i += 1
            j -= 1


def _identity[T](value: T) -> T:
    return value


def _insertion_sort[T](array: ArrayLike[T], start: Num, end: Num, key: Callable[T, Any], reverse: bool):
    i = start + 1
    if reverse:
        while i < end:
            j = i
            while j > start and key(array[j - 1]) < key(array[j]):
                array.swap(j - 1, j)
                j -= 1
            i += 1
    else:
        while i < end:
            j = i
            while j > start and key(array[j - 1]) > key(array[j]):
                array.swap(j - 1, j)
                j -= 1
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

    def get(self) -> V:
        return self.array[self.i]

    def advance(self):
        self.i += 1


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

    def get(self) -> tuple[int, Any]:
        return self.i + self.offset, self.iterator.get()

    def advance(self):
        self.i += 1
        self.iterator.advance()


class _ArrayEnumerator[V: ArrayLike](Record, SonolusIterator):
    i: int
    offset: int
    array: V

    def has_next(self) -> bool:
        return self.i < len(self.array)

    def get(self) -> tuple[int, Any]:
        return self.i + self.offset, self.array[self.i]

    def advance(self):
        self.i += 1


class _Zipper[T](Record, SonolusIterator):
    # Can be a, Pair[a, b], Pair[a, Pair[b, c]], etc.
    iterators: T

    @meta_fn
    def has_next(self) -> bool:
        from sonolus.backend.visitor import compile_and_call

        return compile_and_call(self._has_next, self._get_iterators())

    def _get_iterators(self) -> tuple[SonolusIterator, ...]:
        from sonolus.script.containers import Pair

        iterators = []
        v = self.iterators
        while isinstance(v, Pair):
            iterators.append(v.first)
            v = v.second
        iterators.append(v)
        return tuple(iterators)

    def _has_next(self, iterators: tuple[SonolusIterator, ...]) -> bool:
        for iterator in iterators:  # noqa: SIM110
            if not iterator.has_next():
                return False
        return True

    @meta_fn
    def get(self) -> tuple[Any, ...]:
        from sonolus.backend.visitor import compile_and_call

        return tuple(compile_and_call(iterator.get) for iterator in self._get_iterators())

    @meta_fn
    def advance(self):
        from sonolus.backend.visitor import compile_and_call

        for iterator in self._get_iterators():
            compile_and_call(iterator.advance)


class _EmptyIterator(SonolusIterator):
    def has_next(self) -> bool:
        return False

    def get(self) -> Any:
        return None

    def advance(self):
        pass


class _MappingIterator[T, Fn](Record, SonolusIterator):
    fn: Fn
    iterator: T

    def has_next(self) -> bool:
        return self.iterator.has_next()

    def get(self) -> Any:
        return self.fn(self.iterator.get())

    def advance(self):
        self.iterator.advance()


class _FilteringIterator[T, Fn](Record, SonolusIterator):
    fn: Fn
    iterator: T

    def has_next(self) -> bool:
        while self.iterator.has_next():
            if self.fn(self.iterator.get()):
                return True
            self.iterator.advance()
        return False

    def get(self) -> Any:
        return self.iterator.get()

    def advance(self):
        self.iterator.advance()
