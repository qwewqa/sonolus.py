from __future__ import annotations

import random
from abc import abstractmethod
from collections.abc import Callable
from typing import Any

from sonolus.script.internal.context import ctx
from sonolus.script.internal.impl import meta_fn
from sonolus.script.iterator import SonolusIterator
from sonolus.script.num import Num
from sonolus.script.record import Record
from sonolus.script.values import copy

# Note: we don't use Range in this file because Range itself inherits from ArrayLike


class ArrayLike[T]:
    """Mixin for array-like objects.

    Inheritors must implement `__len__`, `__getitem__`, and `__setitem__`.

    Usage:
        ```python
        class MyArrayLike[T](Record, ArrayLike[T]):
            def __len__(self) -> int:
                ...

            def __getitem__(self, index: Num) -> T:
                ...

            def __setitem__(self, index: Num, value: T):
                ...
        ```
    """

    _allow_instance_check_ = True

    @abstractmethod
    def __len__(self) -> int:
        """Return the length of the array."""

    @abstractmethod
    def __getitem__(self, index: Num) -> T:
        """Return the item at the given index.

        Args:
            index: The index of the item. Must be an integer between 0 and `len(self) - 1`.
        """

    @abstractmethod
    def __setitem__(self, index: Num, value: T):
        """Set the value of the item at the given index.

        Args:
            index: The index of the item. Must be an integer between 0 and `len(self) - 1`.
            value: The value to set.
        """

    def __iter__(self) -> SonolusIterator[T]:
        """Return an iterator over the array."""
        return _ArrayIterator(0, self)

    def __contains__(self, value: Any) -> bool:
        """Return whether any element in the array is equal to the given value.

        Args:
            value: The value to check for.
        """
        i = 0
        while i < len(self):
            if self[i] == value:
                return True
            i += 1
        return False

    def __reversed__(self):
        """Return a reversed view of the array."""
        return _ArrayReverser(self)

    def _enumerate_(self, start: Num = 0) -> SonolusIterator[T]:
        return _ArrayEnumerator(0, start, self)

    def index(self, value: T, start: Num = 0, stop: Num | None = None) -> Num:
        """Return the index of the value in the array equal to the given value.

        Args:
            value: The value to search for.
            start: The index to start searching from.
            stop: The index to stop searching at. If `None`, search to the end of the array.
        """
        if stop is None:
            stop = len(self)
        i = start
        while i < stop:
            if self[i] == value:
                return i
            i += 1
        return -1

    def count(self, value: T) -> Num:
        """Return the number of elements in the array equal to the given value.

        Args:
            value: The value to count.
        """
        count = 0
        i = 0
        while i < len(self):
            if self[i] == value:
                count += 1
            i += 1
        return count

    def last_index(self, value: T) -> Num:
        """Return the last index of the value in the array equal to the given value.

        Args:
            value: The value to search for.
        """
        i = len(self) - 1
        while i >= 0:
            if self[i] == value:
                return i
            i -= 1
        return -1

    def index_of_max(self, *, key: Callable[T, Any] | None = None) -> Num:
        """Return the index of the maximum value in the array.

        Args:
            key: A one-argument ordering function to use for comparison like the one used in `max()`.
        """
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
        """Return the index of the minimum value in the array.

        Args:
            key: A one-argument ordering function to use for comparison like the one used in `min()`.
        """
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
        index = self.index_of_max(key=key)
        assert index != -1
        return self[index]

    def _min_(self, key: Callable[T, Any] | None = None) -> T:
        index = self.index_of_min(key=key)
        assert index != -1
        return self[index]

    def swap(self, i: Num, j: Num, /):
        """Swap the values at the given indices.

        Args:
            i: The first index.
            j: The second index.
        """
        temp = copy(self[i])
        self[i] = self[j]
        self[j] = temp

    def sort(self, *, key: Callable[T, Any] | None = None, reverse: bool = False):
        """Sort the values in the array in place.

        Args:
            key: A one-argument ordering function to use for comparison.
            reverse: If `True`, sort in descending order, otherwise sort in ascending order.
        """
        if len(self) < 15 or key is not None:
            if key is None:
                key = _identity
            # May be worth adding a block sort variant for better performance on large arrays in the future
            _insertion_sort(self, 0, len(self), key, reverse)
        else:
            # Heap sort is unstable, so if there's a key, we can't rely on it
            _heap_sort(self, 0, len(self), reverse)

    def shuffle(self):
        """Shuffle the values in the array in place."""
        random.shuffle(self)  # type: ignore

    def reverse(self):
        """Reverse the values in the array in place."""
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


@meta_fn
def get_positive_index(index: Num, length: Num) -> Num:
    """Get the positive index for the given index in the array of the given length.

    This is used to convert negative indixes relative to the end of the array to positive indices.

    Args:
        index: The index to convert.
        length: The length of the array.

    Returns:
        The positive index.
    """
    if not ctx():
        return index if index >= 0 else index + length
    index = Num._accept_(index)
    length = Num._accept_(length)
    if index._is_py_() and length._is_py_():
        return Num._accept_(index._as_py_() + length._as_py_() if index._as_py_() < 0 else index._as_py_())
    else:
        return index + (index < 0) * length
