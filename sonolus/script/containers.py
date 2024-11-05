from __future__ import annotations

from sonolus.script.array import Array
from sonolus.script.debug import error
from sonolus.script.iterator import ArrayLike, SonolusIterator
from sonolus.script.range import Range
from sonolus.script.record import Record
from sonolus.script.values import alloc, copy


class Pair[T, U](Record):
    first: T
    second: U


class VarArray[T, Capacity](Record, ArrayLike[T]):
    _size: int
    _array: Array[T, Capacity]

    @classmethod
    def new(cls):
        element_type = cls._get_type_arg_(T)
        capacity = cls._get_type_arg_(Capacity)
        return cls(0, alloc(Array[element_type, capacity]))

    def size(self) -> int:
        return self._size

    @classmethod
    def capacity(cls) -> int:
        return cls._get_type_arg_(Capacity)

    def is_full(self) -> bool:
        return self._size == self.capacity()

    def __getitem__(self, item) -> T:
        return self._array[item]

    def __setitem__(self, key: int, value: T):
        self._array[key] = value

    def append(self, value: T):
        """Appends a copy of the given value to the end of the array."""
        assert self._size < self._array.size()
        self._array[self._size] = value
        self._size += 1

    def extend(self, values: ArrayLike[T]):
        """Appends copies of the values in the given array to the end of the array."""
        for value in values:
            self.append(value)

    def pop(self, index: int | None = None) -> T:
        """Removes and returns a copy of the value at the given index.

        Preserves the relative order of the elements.
        """
        if index is None:
            index = self._size - 1
        assert 0 <= index < self._size
        value = copy(self._array[index])
        self._size -= 1
        if index < self._size:
            for i in Range(index, self._size):
                self._array[i] = self._array[i + 1]
        return value

    def insert(self, index: int, value: T):
        """Inserts a copy of the given value at the given index.

        Preserves the relative order of the elements.
        """
        assert 0 <= index <= self._size
        assert self._size < self._array.size()
        self._size += 1
        for i in Range(self._size - 1, index, -1):
            self._array[i] = self._array[i - 1]
        self._array[index] = value

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
        """Adds a copy of the given value if it is not already present, returning whether the value was added.

        If the value is already present, the array is not modified.
        If the array is full, the value is not added.
        """
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

    def __eq__(self, other):
        if self.size() != other.size():
            return False
        i = 0
        while i < self.size():
            if self[i] != other[i]:
                return False
            i += 1
        return True

    def __ne__(self, other):
        return not self == other

    def __hash__(self):
        raise TypeError("unhashable type: 'VarArray'")


class ArrayMapEntry[K, V](Record):
    key: K
    value: V


class ArrayMap[K, V, Capacity](Record):
    _size: int
    _array: Array[ArrayMapEntry[K, V], Capacity]

    @classmethod
    def new(cls):
        key_type = cls._get_type_arg_(K)
        value_type = cls._get_type_arg_(V)
        capacity = cls._get_type_arg_(Capacity)
        return cls(0, alloc(Array[ArrayMapEntry[key_type, value_type], capacity]))

    def size(self) -> int:
        return self._size

    @classmethod
    def capacity(cls) -> int:
        return cls._get_type_arg_(Capacity)

    def is_full(self) -> bool:
        return self._size == self.capacity()

    def keys(self) -> SonolusIterator[K]:
        return _ArrayMapKeyIterator(self, 0)

    def values(self) -> SonolusIterator[V]:
        return ArrayMapValueIterator(self, 0)

    def items(self) -> SonolusIterator[tuple[K, V]]:
        return ArrayMapEntryIterator(self, 0)

    def __getitem__(self, key: K) -> V:
        for i in Range(self._size):
            entry = self._array[i]
            if entry.key == key:
                return entry.value
        error()

    def __setitem__(self, key: K, value: V):
        for i in Range(self._size):
            entry = self._array[i]
            if entry.key == key:
                entry.value = value
                return
        # assert self._size < self.capacity()
        self._array[self._size] = ArrayMapEntry(key, value)
        self._size += 1

    def __contains__(self, key: K) -> bool:
        for i in Range(self._size):  # noqa: SIM110
            if self._array[i].key == key:
                return True
        return False

    def pop(self, key: K) -> V:
        for i in Range(self._size):
            entry = self._array[i]
            if entry.key == key:
                value = copy(entry.value)
                self._size -= 1
                if i < self._size:
                    self._array[i] = self._array[self._size]
                return value
        error()

    def clear(self):
        self._size = 0


class _ArrayMapKeyIterator[K, V, Capacity](Record, SonolusIterator):
    _map: ArrayMap[K, V, Capacity]
    _index: int

    def has_next(self) -> bool:
        return self._index < self._map.size()

    def next(self) -> K:
        key = self._map._array[self._index].key
        self._index += 1
        return key


class ArrayMapValueIterator[K, V, Capacity](Record, SonolusIterator):
    _map: ArrayMap[K, V, Capacity]
    _index: int

    def has_next(self) -> bool:
        return self._index < self._map.size()

    def next(self) -> V:
        value = self._map._array[self._index].value
        self._index += 1
        return value


class ArrayMapEntryIterator[K, V, Capacity](Record, SonolusIterator):
    _map: ArrayMap[K, V, Capacity]
    _index: int

    def has_next(self) -> bool:
        return self._index < self._map.size()

    def next(self) -> tuple[K, V]:
        entry = self._map._array[self._index]
        self._index += 1
        return entry.key, entry.value
