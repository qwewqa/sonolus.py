from __future__ import annotations

from sonolus.script.array import Array
from sonolus.script.array_like import ArrayLike
from sonolus.script.debug import error
from sonolus.script.iterator import SonolusIterator
from sonolus.script.record import Record
from sonolus.script.values import alloc, copy


class Pair[T, U](Record):
    """A generic pair of values.

    Usage:
        ```python
        Pair[T, U](first: T, second: U)
        ```

    Examples:
        ```python
        pair = Pair(1, 2)
        pair = Pair[int, Pair[int, int]](1, Pair(2, 3))
        ```
    """

    first: T
    """The first value."""

    second: U
    """The second value."""

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
    """An array with a variable size and fixed maximum capacity.

    Usage:
        ```python
        VarArray[T, Capacity].new()  # Create a new empty array
        ```

    Examples:
        ```python
        array = VarArray[int, 10].new()
        array.append(1)
        ```
    """

    _size: int
    _array: Array[T, Capacity]

    @classmethod
    def new(cls):
        """Create a new empty array."""
        element_type = cls.type_var_value(T)
        capacity = cls.type_var_value(Capacity)
        return cls(0, alloc(Array[element_type, capacity]))

    def __len__(self) -> int:
        """Return the number of elements in the array."""
        return self._size

    @classmethod
    def capacity(cls) -> int:
        """Return the maximum number of elements the array can hold."""
        return cls.type_var_value(Capacity)

    def is_full(self) -> bool:
        """Return whether the array is full."""
        return self._size == self.capacity()

    def __getitem__(self, item) -> T:
        """Return the element at the given index.

        The returned value continues to be part of the array.
        Future modifications to the array will affect the returned value.

        Note:
            Future modifications to the array may cause unexpected changes to the returned value.
            If the array may be modified in the future, it's recommended to make a copy of the value.

            For example:
            ```python
            a = VarArray[Pair, 10].new()
            a.append(Pair(1, 2))
            a.append(Pair(3, 4))
            a.append(Pair(5, 6))
            p = a[1]
            a.pop(0)  # Elements are shifted back
            assert p == Pair(5, 6)  # The value of p has changed
            ```
        """
        return self._array[item]

    def __setitem__(self, key: int, value: T):
        """Update the element at the given index."""
        self._array[key] = value

    def __delitem__(self, key: int):
        """Remove the element at the given index."""
        self.pop(key)

    def append(self, value: T):
        """Append a copy of the given value to the end of the array.

        Args:
            value: The value to append.
        """
        assert self._size < len(self._array)
        self._array[self._size] = value
        self._size += 1

    def extend(self, values: ArrayLike[T]):
        """Appends copies of the values in the given array to the end of the array.

        Args:
            values: The values to append.
        """
        for value in values:
            self.append(value)

    def pop(self, index: int | None = None) -> T:
        """Remove and return a copy of the value at the given index.

        Preserves the relative order of the elements.

        Args:
            index: The index of the value to remove. If None, the last element is removed.
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
        """Insert a copy of the given value at the given index.

        Preserves the relative order of the elements.

        Args:
            index: The index at which to insert the value. Must be in the range [0, size].
            value: The value to insert.
        """
        assert 0 <= index <= self._size
        assert self._size < len(self._array)
        self._size += 1
        for i in range(self._size - 1, index, -1):
            self._array[i] = self._array[i - 1]
        self._array[index] = value

    def remove(self, value: T) -> bool:
        """Remove the first occurrence of the given value, returning whether the value was removed.

        Preserves the relative order of the elements.

        Args:
            value: The value to remove

        Returns:
            True if the value was removed, False otherwise.
        """
        index = self.index(value)
        if index < 0:
            return False
        self.pop(index)
        return True

    def clear(self):
        """Clear the array, removing all elements.

        References to elements are not immediately changed, but future insertions may overwrite them.
        """
        self._size = 0

    def set_add(self, value: T) -> bool:
        """Adds a copy of the given value if it is not already present, returning whether the value was added.

        If the value is already present, the array is not modified.
        If the array is full, the value is not added.

        Args:
            value: The value to add

        Returns:
            True if the value was added, False otherwise.
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

        Args:
            value: The value to remove

        Returns:
            True if the value was removed, False otherwise.
        """
        index = self.index(value)
        if index < 0:
            return False
        if index < self._size - 1:
            self._array[index] = self._array[self._size - 1]
        self._size -= 1
        return True

    def __iadd__(self, other):
        """Appends copies of the values in the given array to the end of the array."""
        self.extend(other)
        return self

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
    """A map implemented as an array of key-value pairs with a fixed maximum capacity.

    Usage:
        ```python
        ArrayMap[K, V, Capacity].new()  # Create a new empty map
        ```

    Examples:
        ```python
        map = ArrayMap[int, int, 10].new()
        map[1] = 2
        map[3] = 4
        assert 1 in map
        assert 2 not in map
        assert map[3] == 4
        ```
    """

    _size: int
    _array: Array[_ArrayMapEntry[K, V], Capacity]

    @classmethod
    def new(cls):
        """Create a new empty map."""
        key_type = cls.type_var_value(K)
        value_type = cls.type_var_value(V)
        capacity = cls.type_var_value(Capacity)
        return cls(0, alloc(Array[_ArrayMapEntry[key_type, value_type], capacity]))

    def __len__(self) -> int:
        """Return the number of key-value pairs in the map."""
        return self._size

    @classmethod
    def capacity(cls) -> int:
        """Return the maximum number of key-value pairs the map can hold."""
        return cls.type_var_value(Capacity)

    def is_full(self) -> bool:
        """Return whether the map is full."""
        return self._size == self.capacity()

    def keys(self) -> SonolusIterator[K]:
        """Return an iterator over the keys in the map."""
        return _ArrayMapKeyIterator(self, 0)

    def values(self) -> SonolusIterator[V]:
        """Return an iterator over the values in the map."""
        return _ArrayMapValueIterator(self, 0)

    def items(self) -> SonolusIterator[tuple[K, V]]:
        """Return an iterator over the key-value pairs in the map."""
        return _ArrayMapEntryIterator(self, 0)

    def __iter__(self):
        """Return an iterator over the keys in the map."""
        return self.keys()

    def __getitem__(self, key: K) -> V:
        """Return the value associated with the given key.

        Must be called with a key that is present in the map.

        The returned value continues to be part of the map.
        Future modifications to the map will affect the returned value.

        Notes:
            Future modifications to the map may cause unexpected changes to the returned value.
            If the map may be modified in the future, it's recommended to make a copy of the value.

            For example:
            ```python
            map = ArrayMap[int, Pair[int, int], 10].new()
            map[1] = Pair(2, 3)
            map[3] = Pair(4, 5)
            map[5] = Pair(6, 7)
            p = map[3]
            map.pop(1)
            # The value of `p` may now be different
            ```
        """
        for i in range(self._size):
            entry = self._array[i]
            if entry.key == key:
                return entry.value
        error()

    def __setitem__(self, key: K, value: V):
        """Associate the given key with the given value.

        If the key is already present in the map, the value is updated.
        Must not be called if the map is full.

        Args:
            key: The key to associate with the value.
            value: The value to associate with the key
        """
        for i in range(self._size):
            entry = self._array[i]
            if entry.key == key:
                entry.value = value
                return
        assert self._size < self.capacity()
        self._array[self._size] = _ArrayMapEntry(key, value)
        self._size += 1

    def __contains__(self, key: K) -> bool:
        """Return whether the given key is present in the map.

        Args:
            key: The key to check for

        Returns:
            True if the key is present, False otherwise.
        """
        for i in range(self._size):  # noqa: SIM110
            if self._array[i].key == key:
                return True
        return False

    def pop(self, key: K) -> V:
        """Remove and return a copy of the value associated with the given key.

        Must be called with a key that is present in the map.

        Args:
            key: The key to remove

        Returns:
            The value associated with the key
        """
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
        """Clear the map, removing all key-value pairs."""
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
