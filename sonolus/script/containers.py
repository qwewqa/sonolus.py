from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, Self

from sonolus.backend.visitor import compile_and_call
from sonolus.script.archetype import AnyArchetype, EntityRef
from sonolus.script.array import Array
from sonolus.script.array_like import ArrayLike, get_positive_index
from sonolus.script.debug import error
from sonolus.script.internal.context import ctx
from sonolus.script.internal.impl import meta_fn
from sonolus.script.interval import clamp
from sonolus.script.iterator import SonolusIterator
from sonolus.script.maybe import Maybe, Nothing, Some
from sonolus.script.num import Num
from sonolus.script.pointer import _deref
from sonolus.script.record import Record
from sonolus.script.values import copy, zeros


class Box[T](Record):
    """A box that contains a value.

    This can be helpful for generic code that can handle both Num and non-Num types.

    Usage:
        ```python
        Box[T](value: T)
        ```

    Examples:
        ```python
        box = Box(1)
        box = Box[int](2)

        x: T = ...
        y: T = ...
        box = Box(x)
        box.value = y  # Works regardless of whether x is a Num, array, or record
        ```
    """

    value: T
    """The value contained in the box."""


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

    Supports negative indexes.

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
    def new(cls) -> Self:
        """Create a new empty array."""
        element_type = cls.type_var_value(T)
        capacity = cls.type_var_value(Capacity)
        return cls(0, zeros(Array[element_type, capacity]))

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

        Supports negative indexes.

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
        return self._array.get_unchecked(get_positive_index(item, self._size))

    def __setitem__(self, key: int, value: T):
        """Update the element at the given index."""
        self._array.set_unchecked(get_positive_index(key, self._size), value)

    def __delitem__(self, key: int):
        """Remove the element at the given index."""
        self.pop(key)

    def append(self, value: T):
        """Append a copy of the given value to the end of the array.

        Args:
            value: The value to append.
        """
        assert self._size < len(self._array), "Array is full"
        self._array.set_unchecked(self._size, value)
        self._size += 1

    def append_unchecked(self, value: T):
        """Append the given value to the end of the array without checking the capacity.

        Use with caution as this may cause hard to debug issues if the array is full.

        Args:
            value: The value to append.
        """
        self._array.set_unchecked(self._size, value)
        self._size += 1

    def extend(self, values: ArrayLike[T]):
        """Appends copies of the values in the given array to the end of the array.

        Args:
            values: The values to append.
        """
        assert self._size + len(values) <= len(self._array), "Array is full"
        i = 0
        while i < len(values):
            self._array.set_unchecked(self._size + i, values.get_unchecked(i))
            i += 1
        self._size += len(values)

    def pop(self, index: int | None = None) -> T:
        """Remove and return a copy of the value at the given index.

        Preserves the relative order of the elements.

        Args:
            index: The index of the value to remove. If None, the last element is removed.
        """
        if index is None:
            index = self._size - 1
        index = get_positive_index(index, self._size)
        value = copy(self._array.get_unchecked(index))
        self._size -= 1
        if index < self._size:
            for i in range(index, self._size):
                self._array.set_unchecked(i, self._array.get_unchecked(i + 1))
        return value

    def insert(self, index: int, value: T):
        """Insert a copy of the given value at the given index.

        Preserves the relative order of the elements.

        Args:
            index: The index at which to insert the value. Must be in the range [0, size].
            value: The value to insert.
        """
        index = clamp(get_positive_index(index, self._size, include_end=True), 0, self._size)
        assert self._size < len(self._array), "Array is full"
        self._size += 1
        for i in range(self._size - 1, index, -1):
            self._array.set_unchecked(i, self._array.get_unchecked(i - 1))
        self._array.set_unchecked(index, value)

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
            self._array.set_unchecked(index, self._array.get_unchecked(self._size - 1))
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
            if self.get_unchecked(i) != other.get_unchecked(i):
                return False
            i += 1
        return True

    def __ne__(self, other):
        return not self == other

    def __hash__(self):
        raise TypeError("unhashable type: 'VarArray'")


class ArrayPointer[T](Record, ArrayLike[T]):
    """An array defined by a size and pointer to the first element.

    Supports negative indexes.

    This is intended to be created internally and improper use may result in hard to debug issues.

    Usage:
        ```python
        ArrayPointer[T](size: int, block: int, offset: int)
        ```
    """

    size: int
    block: int
    offset: int

    def __len__(self) -> int:
        """Return the number of elements in the array."""
        return self.size

    @classmethod
    def element_type(cls) -> type[T]:
        """Return the type of the elements in the array."""
        return cls.type_var_value(T)

    @meta_fn
    def _get_item(self, item: int) -> T:
        if not ctx():
            raise TypeError("ArrayPointer values cannot be accessed outside of a context")
        return _deref(
            # Allows a compile time constant block so we can warn based on callback read/write access
            (self._value_["block"]._is_py_() and self._value_["block"]._as_py_()) or self.block,
            self.offset + Num._accept_(item) * Num._accept_(self.element_type()._size_()),
            self.element_type(),
        )

    def __getitem__(self, item) -> T:
        return self.get_unchecked(get_positive_index(item, self.size))

    def __setitem__(self, key: int, value: T):
        self.set_unchecked(get_positive_index(key, self.size), value)

    @meta_fn
    def get_unchecked(self, item: int) -> T:
        return self._get_item(item)._get_()

    @meta_fn
    def set_unchecked(self, key: int, value: T):
        dst = self._get_item(key)
        if self.element_type()._is_value_type_():
            dst._set_(value)
        else:
            dst._copy_from__(value)


class ArraySet[T, Capacity](Record):
    """A set implemented as an array with a fixed maximum capacity.

    Usage:
        ```python
        ArraySet[T, Capacity].new()  # Create a new empty set
        ```

    Examples:
        ```python
        s = ArraySet[int, 10].new()
        s.add(1)
        s.add(2)
        assert 1 in s
        assert 3 not in s
        s.remove(1)
        assert 1 not in s
        ```
    """

    _values: VarArray[T, Capacity]

    @classmethod
    def new(cls) -> Self:
        """Create a new empty set."""
        element_type = cls.type_var_value(T)
        capacity = cls.type_var_value(Capacity)
        return cls(VarArray[element_type, capacity].new())

    def __len__(self):
        """Return the number of elements in the set."""
        return len(self._values)

    def __contains__(self, value):
        """Return whether the given value is present in the set."""
        return value in self._values

    def __iter__(self) -> SonolusIterator[T]:
        """Return an iterator over the values in the set."""
        return self._values.__iter__()

    def add(self, value: T) -> bool:
        """Add a copy of the given value to the set.

        This has no effect and returns False if the value is already present or if the set is full.

        Args:
            value: The value to add.

        Returns:
            True if the value was added, False otherwise.
        """
        return self._values.set_add(value)

    def remove(self, value: T) -> bool:
        """Remove the given value from the set.

        This has no effect and returns False if the value is not present.

        Args:
            value: The value to remove.

        Returns:
            True if the value was removed, False otherwise.
        """
        return self._values.set_remove(value)

    def clear(self):
        """Clear the set, removing all elements."""
        self._values.clear()


class FrozenNumSet[Size](Record):
    _values: Array[Num, Size]

    @classmethod
    @meta_fn
    def of(cls, *values: Num) -> Self:
        if ctx():
            try:
                num_values = [Num._accept_(v) for v in values]
            except TypeError:
                raise TypeError("Only sets of numeric values are supported") from None
            if all(v._is_py_() for v in num_values):
                const_values = [v._as_py_() for v in num_values]
                arr = Array[Num, len(const_values)]._with_value([Num(v) for v in sorted(const_values)])
                return cls(arr)
            else:
                arr = Array[Num, len(values)](*values)
                compile_and_call(arr.sort)
        else:
            arr = Array[Num, len(values)](*sorted(values))
        return cls(arr)

    def __len__(self) -> int:
        return len(self._values)

    def __contains__(self, value: Num) -> bool:
        if len(self) < 8:
            return value in self._as_tuple()
        else:
            left = 0
            right = len(self) - 1
            while left <= right:
                mid = (left + right) // 2
                mid_value = self._values.get_unchecked(mid)
                if mid_value == value:
                    return True
                elif mid_value < value:
                    left = mid + 1
                else:
                    right = mid - 1
            return False

    def __iter__(self) -> SonolusIterator[Num]:
        return self._values.__iter__()

    @meta_fn
    def _as_tuple(self) -> tuple[Num, ...]:
        return tuple(self._values.get_unchecked(i) for i in range(Num._accept_(len(self))._as_py_()))


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
    def new(cls) -> Self:
        """Create a new empty map."""
        key_type = cls.type_var_value(K)
        value_type = cls.type_var_value(V)
        capacity = cls.type_var_value(Capacity)
        return cls(0, zeros(Array[_ArrayMapEntry[key_type, value_type], capacity]))

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

    def __iter__(self) -> SonolusIterator[K]:
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
            entry = self._array.get_unchecked(i)
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
            entry = self._array.get_unchecked(i)
            if entry.key == key:
                entry.value = value
                return
        assert self._size < self.capacity(), "Map is full"
        self._array.set_unchecked(self._size, _ArrayMapEntry(key, value))
        self._size += 1

    def __delitem__(self, key: K):
        """Remove the key-value pair associated with the given key.

        Must be called with a key that is present in the map.

        Args:
            key: The key to remove
        """
        for i in range(self._size):
            entry = self._array.get_unchecked(i)
            if entry.key == key:
                self._size -= 1
                if i < self._size:
                    self._array.set_unchecked(i, self._array.get_unchecked(self._size))
                return
        error()

    def __contains__(self, key: K) -> bool:
        """Return whether the given key is present in the map.

        Args:
            key: The key to check for

        Returns:
            True if the key is present, False otherwise.
        """
        for i in range(self._size):  # noqa: SIM110
            if self._array.get_unchecked(i).key == key:
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
            entry = self._array.get_unchecked(i)
            if entry.key == key:
                value = copy(entry.value)
                self._size -= 1
                if i < self._size:
                    self._array.set_unchecked(i, self._array.get_unchecked(self._size))
                return value
        error()

    def clear(self):
        """Clear the map, removing all key-value pairs."""
        self._size = 0


class _ArrayMapKeyIterator[K, V, Capacity](Record, SonolusIterator):
    _map: ArrayMap[K, V, Capacity]
    _index: int

    def next(self) -> Maybe[K]:
        if self._index < len(self._map):
            key = self._map._array.get_unchecked(self._index).key
            self._index += 1
            return Some(key)
        return Nothing


class _ArrayMapValueIterator[K, V, Capacity](Record, SonolusIterator):
    _map: ArrayMap[K, V, Capacity]
    _index: int

    def next(self) -> Maybe[V]:
        if self._index < len(self._map):
            value = self._map._array.get_unchecked(self._index).value
            self._index += 1
            return Some(value)
        return Nothing


class _ArrayMapEntryIterator[K, V, Capacity](Record, SonolusIterator):
    _map: ArrayMap[K, V, Capacity]
    _index: int

    def next(self) -> Maybe[tuple[K, V]]:
        if self._index < len(self._map):
            entry = self._map._array.get_unchecked(self._index)
            result = (entry.key, entry.value)
            self._index += 1
            return Some(result)
        return Nothing


class _LinkedListNodeRef[TKey, TValue](Protocol):
    def get_value(self) -> TValue: ...

    def get_next(self) -> Self: ...

    def set_next(self, next_node: Self): ...

    def set_prev(self, prev_node: Self):
        # No-op for singly linked lists
        return

    def is_present(self) -> bool: ...

    def set(self, other: Self): ...

    def copy(self) -> Self: ...

    def empty(self) -> Self: ...


def _merge_linked_list_nodes[TNode: _LinkedListNodeRef](
    a: TNode,
    b: TNode,
) -> TNode:
    head = a.empty()
    tail = a.empty()
    left = a.copy()
    right = b.copy()

    while left.is_present() and right.is_present():
        if left.get_value() <= right.get_value():
            if not head.is_present():
                head.set(left)
                tail.set(left)
            else:
                tail.set_next(left)
                tail.set(left)
            left.set(left.get_next())
        else:
            if not head.is_present():
                head.set(right)
                tail.set(right)
            else:
                tail.set_next(right)
                tail.set(right)
            right.set(right.get_next())

    while left.is_present():
        if not head.is_present():
            head.set(left)
            tail.set(left)
        else:
            tail.set_next(left)
            tail.set(left)
        left.set(left.get_next())

    while right.is_present():
        if not head.is_present():
            head.set(right)
            tail.set(right)
        else:
            tail.set_next(right)
            tail.set(right)
        right.set(right.get_next())

    if tail.is_present():
        tail.set_next(a.empty())

    return head


def _merge_sort_linked_list_nodes[TNode: _LinkedListNodeRef](
    head: TNode,
) -> TNode:
    # Calculate length
    length = 0
    node = head.copy()
    while node.is_present():
        length += 1
        node.set(node.get_next())

    # Trivial case
    if length <= 1:
        return head

    # Bottom-up merge sort: start with sublists of size 1, then 2, 4, 8, etc.
    size = 1
    while size < length:
        current = head.copy()
        new_head = head.empty()
        new_tail = head.empty()

        # Process all pairs of sublists of the current size
        while current.is_present():
            # Extract the first sublist
            left = current.copy()
            prev = current.empty()
            i = 0
            while i < size and current.is_present():
                prev.set(current)
                current.set(current.get_next())
                i += 1
            if prev.is_present():
                prev.set_next(prev.empty())

            # We've made it to the end without a second sublist to merge, so just attach it to the end
            if not current.is_present():
                # Since size < length, we know a full iteration must have happened already, so new_tail is valid
                new_tail.set_next(left)
                break

            # Extract the second sublist
            right = current.copy()
            prev = current.empty()
            i = 0
            while i < size and current.is_present():
                prev.set(current)
                current.set(current.get_next())
                i += 1
            if prev.is_present():
                prev.set_next(prev.empty())

            merged = _merge_linked_list_nodes(left, right)

            # Append the merged result
            if not new_head.is_present():
                new_head.set(merged)
                new_tail.set(merged)
            else:
                new_tail.set_next(merged)

            # Move tail to the end of the merged section
            while new_tail.get_next().is_present():
                new_tail.set(new_tail.get_next())

        # Update head for the next iteration
        head.set(new_head)
        size *= 2

    return head


class _EntityNodeRef[Archetype, GetValue, GetNextRef, GetPrevRef](Record):
    index: int

    def get_value(self) -> Any:
        return self.get_value_fn(self.archetype.at(self.index))

    def get_next(self) -> _EntityNodeRef:
        next_ref = self.get_next_ref_fn(self.archetype.at(self.index))
        return self.with_index(next_ref.index)

    def set_next(self, next_node: _EntityNodeRef):
        entity = self.archetype.at(self.index)
        next_ref = self.get_next_ref_fn(entity)
        next_ref.index = next_node.index

    def set_prev(self, prev_node: _EntityNodeRef):
        if self.get_prev_ref_fn is not None:
            entity = self.archetype.at(self.index)
            prev_ref = self.get_prev_ref_fn(entity)
            prev_ref.index = prev_node.index

    def is_present(self) -> bool:
        return self.index > 0

    def set(self, other: _EntityNodeRef):
        self.index = other.index

    def copy(self) -> _EntityNodeRef:
        return self.with_index(self.index)

    def empty(self) -> _EntityNodeRef:
        return self.with_index(0)

    def with_index(self, index: int) -> _EntityNodeRef:
        return _EntityNodeRef[
            self.archetype,
            self.get_value_fn,
            self.get_next_ref_fn,
            self.get_prev_ref_fn,
        ](index)

    @property
    def archetype(self):
        return self.type_var_value(Archetype)

    @property
    def get_value_fn(self):
        return self.type_var_value(GetValue)

    @property
    def get_next_ref_fn(self):
        return self.type_var_value(GetNextRef)

    @property
    def get_prev_ref_fn(self):
        return self.type_var_value(GetPrevRef)


def sort_linked_entities[T: AnyArchetype](
    head_ref: EntityRef[T],
    /,
    *,
    get_value: Callable[[T], Any],
    get_next_ref: Callable[[T], EntityRef[T]],
    get_prev_ref: Callable[[T], EntityRef[T]] | None = None,
) -> EntityRef[T]:
    """Sort a linked list of entities using merge sort.

    If get_prev_ref is provided, the backward links will be updated as well.

    Usage:
        ```python
        class MyArchetype(PlayArchetype):
            sort_key: int
            next: EntityRef[MyArchetype]

        def sort_my_archetype(head: EntityRef[MyArchetype]) -> EntityRef[MyArchetype]:
            return sort_linked_entities(
                head,
                get_value=lambda e: e.sort_key,
                get_next_ref=lambda e: e.next,
            )
        ```

    Args:
        head_ref: A reference to the head of the linked list.
        get_value: A function that takes an entity and returns the value to sort by.
        get_next_ref: A function that takes an entity and returns a reference to the next entity.
        get_prev_ref: An optional function that takes an entity and returns a reference to the previous entity.

    Returns:
        A reference to the head of the sorted linked list.
    """
    archetype = head_ref.archetype()

    sorted_head_index = _merge_sort_linked_list_nodes(
        _EntityNodeRef[archetype, get_value, get_next_ref, get_prev_ref](head_ref.index)
    ).index

    if get_prev_ref is not None:
        current_ref = _EntityNodeRef[archetype, get_value, get_next_ref, get_prev_ref](sorted_head_index)
        prev_ref = current_ref.empty()
        while current_ref.is_present():
            current_ref.set_prev(prev_ref)
            prev_ref.set(current_ref)
            current_ref.set(current_ref.get_next())

    return EntityRef[archetype](sorted_head_index)
