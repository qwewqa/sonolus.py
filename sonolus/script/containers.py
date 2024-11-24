from __future__ import annotations

from sonolus.script.array import Array
from sonolus.script.array_like import ArrayLike
from sonolus.script.debug import error
from sonolus.script.iterator import SonolusIterator
from sonolus.script.record import Record
from sonolus.script.values import alloc, copy


class Pair[T, U](Record):
    first: T
    second: U

    def __lt__(self, other):
        if self.first == other.first:
            return self.second < other.second
        return self.first < other.first

    def __le__(self, other):
        if self.first == other.first:
            return self.second <= other.second
        return self.first <= other.first

    def __gt__(self, other):
        if self.first == other.first:
            return self.second > other.second
        return self.first > other.first

    def __ge__(self, other):
        if self.first == other.first:
            return self.second >= other.second
        return self.first >= other.first


class VarArray[T, Capacity](Record, ArrayLike[T]):
    _size: int
    _array: Array[T, Capacity]

    @classmethod
    def new(cls):
        element_type = cls.type_arg_value(T)
        capacity = cls.type_arg_value(Capacity)
        return cls(0, alloc(Array[element_type, capacity]))

    def __len__(self) -> int:
        return self._size

    @classmethod
    def capacity(cls) -> int:
        return cls.type_arg_value(Capacity)

    def is_full(self) -> bool:
        return self._size == self.capacity()

    def __getitem__(self, item) -> T:
        return self._array[item]

    def __setitem__(self, key: int, value: T):
        self._array[key] = value

    def append(self, value: T):
        """Appends a copy of the given value to the end of the array."""
        assert self._size < len(self._array)
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
            for i in range(index, self._size):
                self._array[i] = self._array[i + 1]
        return value

    def insert(self, index: int, value: T):
        """Inserts a copy of the given value at the given index.

        Preserves the relative order of the elements.
        """
        assert 0 <= index <= self._size
        assert self._size < len(self._array)
        self._size += 1
        for i in range(self._size - 1, index, -1):
            self._array[i] = self._array[i - 1]
        self._array[index] = value

    def remove(self, value: T) -> bool:
        """Removes the first occurrence of the given value, returning whether the value was removed.

        Preserves the relative order of the elements.
        """
        index = self.index(value)
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
        if self._size >= len(self._array):
            return False
        if value in self:
            return False
        self.append(value)
        return True

    def set_remove(self, value: T) -> bool:
        """Removes the first occurrence of the given value, returning whether the value was removed.

        Does not preserve the relative order of the elements.
        """
        index = self.index(value)
        if index < 0:
            return False
        if index < self._size - 1:
            self._array[index] = self._array[self._size - 1]
        self._size -= 1
        return True

    def __eq__(self, other):
        if not isinstance(other, ArrayLike):
            return False
        if len(self) != len(other):
            return False
        i = 0
        while i < len(self):
            if self[i] != other[i]:
                return False
            i += 1
        return True

    def __ne__(self, other):
        return not self == other

    def __hash__(self):
        raise TypeError("unhashable type: 'VarArray'")


class _ArrayMapEntry[K, V](Record):
    key: K
    value: V


class ArrayMap[K, V, Capacity](Record):
    _size: int
    _array: Array[_ArrayMapEntry[K, V], Capacity]

    @classmethod
    def new(cls):
        key_type = cls.type_arg_value(K)
        value_type = cls.type_arg_value(V)
        capacity = cls.type_arg_value(Capacity)
        return cls(0, alloc(Array[_ArrayMapEntry[key_type, value_type], capacity]))

    def __len__(self) -> int:
        return self._size

    @classmethod
    def capacity(cls) -> int:
        return cls.type_arg_value(Capacity)

    def is_full(self) -> bool:
        return self._size == self.capacity()

    def keys(self) -> SonolusIterator[K]:
        return _ArrayMapKeyIterator(self, 0)

    def values(self) -> SonolusIterator[V]:
        return _ArrayMapValueIterator(self, 0)

    def items(self) -> SonolusIterator[tuple[K, V]]:
        return _ArrayMapEntryIterator(self, 0)

    def __iter__(self):
        return self.keys()

    def __getitem__(self, key: K) -> V:
        for i in range(self._size):
            entry = self._array[i]
            if entry.key == key:
                return entry.value
        error()

    def __setitem__(self, key: K, value: V):
        for i in range(self._size):
            entry = self._array[i]
            if entry.key == key:
                entry.value = value
                return
        assert self._size < self.capacity()
        self._array[self._size] = _ArrayMapEntry(key, value)
        self._size += 1

    def __contains__(self, key: K) -> bool:
        for i in range(self._size):  # noqa: SIM110
            if self._array[i].key == key:
                return True
        return False

    def pop(self, key: K) -> V:
        for i in range(self._size):
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
        return self._index < len(self._map)

    def get(self) -> K:
        return self._map._array[self._index].key

    def advance(self):
        self._index += 1


class _ArrayMapValueIterator[K, V, Capacity](Record, SonolusIterator):
    _map: ArrayMap[K, V, Capacity]
    _index: int

    def has_next(self) -> bool:
        return self._index < len(self._map)

    def get(self) -> V:
        return self._map._array[self._index].value

    def advance(self):
        self._index += 1


class _ArrayMapEntryIterator[K, V, Capacity](Record, SonolusIterator):
    _map: ArrayMap[K, V, Capacity]
    _index: int

    def has_next(self) -> bool:
        return self._index < len(self._map)

    def get(self) -> tuple[K, V]:
        entry = self._map._array[self._index]
        return entry.key, entry.value

    def advance(self):
        self._index += 1
