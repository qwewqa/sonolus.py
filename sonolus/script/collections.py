from sonolus.script.array import Array
from sonolus.script.debug import assert_true
from sonolus.script.iterator import ArrayLike
from sonolus.script.record import Record


class VarArray[T, Capacity](Record, ArrayLike[T]):
    _size: int
    _array: Array[T, Capacity]

    def size(self) -> int:
        return self._size

    def __getitem__(self, item) -> T:
        return self._array[item]

    def __setitem__(self, key: int, value: T):
        self._array[key] = value

    def push(self, value: T):
        assert_true(self._size < self._array.size())
        self._array[self._size] = value
        self._size += 1

    def pop(self) -> T:
        assert_true(self._size > 0)
        self._size -= 1
        return self._array[self._size]

    def clear(self):
        self._size = 0
