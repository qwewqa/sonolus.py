from sonolus.script.array import Array
from sonolus.script.iterator import ArrayLike
from sonolus.script.range import Range
from sonolus.script.record import Record
from sonolus.script.values import copy


class VarArray[T, Capacity](Record, ArrayLike[T]):
    _size: int
    _array: Array[T, Capacity]

    def size(self) -> int:
        return self._size

    def __getitem__(self, item) -> T:
        return self._array[item]

    def __setitem__(self, key: int, value: T):
        self._array[key] = value

    def append(self, value: T):
        """Appends a copy of the given value to the end of the array."""
        assert self._size < self._array.size()
        self._array[self._size] = value
        self._size += 1

    def pop(self, index: int) -> T:
        """Removes and returns a copy of the value at the given index."""
        assert 0 <= index < self._size
        value = copy(self._array[index])
        self._size -= 1
        if index < self._size:
            for i in Range(index, self._size):
                self._array[i] = self._array[i + 1]
        return value

    def remove(self, value: T) -> bool:
        """Removes the first occurrence of the given value, returning whether the value was removed.

        Preserves the relative order of the elements.
        """
        index = self.index_of(value)
        if index < 0:
            return False
        self.pop(index)
        return True

    def clear(self):
        """Sets size to zero."""
        self._size = 0

    def set_add(self, value: T) -> bool:
        """Adds a copy of the given value if it is not already present, returning whether the value was added."""
        if self._size >= self._array.size():
            return False
        if value in self:
            return False
        self.append(value)
        return True

    def set_remove(self, value: T) -> bool:
        """Removes the first occurrence of the given value, returning whether the value was removed.

        Does not preserve the relative order of the elements.
        """
        index = self.index_of(value)
        if index < 0:
            return False
        if index < self._size - 1:
            self._array[index] = self._array[self._size - 1]
        self._size -= 1
        return True
