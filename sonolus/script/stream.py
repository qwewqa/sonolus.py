from __future__ import annotations

from math import inf
from typing import cast, dataclass_transform

from sonolus.backend.ir import IRConst, IRExpr, IRInstr, IRPureInstr
from sonolus.backend.mode import Mode
from sonolus.backend.ops import Op
from sonolus.script.internal.context import ctx
from sonolus.script.internal.descriptor import SonolusDescriptor
from sonolus.script.internal.impl import meta_fn
from sonolus.script.internal.introspection import get_field_specifiers
from sonolus.script.internal.native import native_function
from sonolus.script.internal.value import BackingValue, Value
from sonolus.script.iterator import SonolusIterator
from sonolus.script.num import Num
from sonolus.script.record import Record
from sonolus.script.runtime import prev_time, time
from sonolus.script.values import sizeof


class _StreamField(SonolusDescriptor):
    offset: int
    type_: type[Stream] | type[StreamGroup]

    def __init__(self, offset: int, type_: type[Stream] | type[StreamGroup]):
        self.offset = offset
        self.type_ = type_

    def __get__(self, instance, owner):
        _check_can_read_or_write_stream()
        return self.type_(self.offset)

    def __set__(self, instance, value):
        raise AttributeError("Cannot set attribute")


class _StreamDataField(SonolusDescriptor):
    offset: int
    type_: type[Value]

    def __init__(self, offset: int, type_: type[Value]):
        self.offset = offset
        self.type_ = type_

    def _get(self):
        return self.type_._from_backing_source_(lambda offset: _SparseStreamBacking(self.offset, Num(offset)))

    def __get__(self, instance, owner):
        _check_can_read_or_write_stream()
        return self._get()._get_()

    def __set__(self, instance, value):
        _check_can_write_stream()
        if self.type_._is_value_type_():
            self._get()._set_(value)
        else:
            self._get()._copy_from_(value)


@dataclass_transform()
def streams[T](cls: type[T]) -> T:
    """Decorator to define streams and stream groups.

    Streams and stream groups are declared by annotating class attributes with `Stream` or `StreamGroup`.

    Other types are also supported in the form of data fields. They may be used to store additional data to export from
    Play to Watch mode.

    In either case, data is write-only in Play mode and read-only in Watch mode.

    This should only be used once in most projects, as multiple decorated classes will overlap with each other and
    interfere when both are used at the same time.

    For backwards compatibility, new streams and stream groups should be added to the end of existing ones, and
    lengths and element types of existing streams and stream groups should not be changed. Otherwise, old replays may
    not work on new versions of the engine.

    Usage:
        ```python
        @streams
        class Streams:
            stream_1: Stream[Num]  # A stream of Num values
            stream_2: Stream[Vec2]  # A stream of Vec2 values
            group_1: StreamGroup[Num, 10]  # A group of 10 Num streams
            group_2: StreamGroup[Vec2, 5]  # A group of 5 Vec2 streams

            data_field_1: Num  # A data field of type Num
            data_field_2: Vec2  # A data field of type Vec2
        ```
    """
    if len(cls.__bases__) != 1:
        raise ValueError("Options class must not inherit from any class (except object)")
    instance = cls()
    entries = []
    # Offset 0 is unused so we can tell when a stream object is uninitialized since it'll have offset 0.
    offset = 1
    for name, annotation in get_field_specifiers(cls).items():
        if issubclass(annotation, Stream | StreamGroup):
            annotation = cast(type[Stream | StreamGroup], annotation)
            if annotation is Stream or annotation is StreamGroup:
                raise TypeError(f"Invalid annotation for streams: {annotation}. Must have type arguments.")
            setattr(cls, name, _StreamField(offset, annotation))
            # Streams store their data across several backing streams
            entries.append((name, offset, annotation))
            offset += annotation.backing_size()
        elif issubclass(annotation, Value) and annotation._is_concrete_():
            setattr(cls, name, _StreamDataField(offset, annotation))
            # Data fields store their data in a single backing stream at different offsets in the same stream
            entries.append((name, offset, annotation))
            offset += 1
    instance._streams_ = entries
    instance._is_comptime_value_ = True
    return instance


@meta_fn
def _check_can_read_stream() -> None:
    if not ctx() or ctx().global_state.mode != Mode.WATCH:
        raise RuntimeError("Stream read operations are only allowed in watch mode.")


@meta_fn
def _check_can_write_stream() -> None:
    if not ctx() or ctx().global_state.mode != Mode.PLAY:
        raise RuntimeError("Stream write operations are only allowed in play mode.")


@meta_fn
def _check_can_read_or_write_stream() -> None:
    if not ctx() or ctx().global_state.mode not in {Mode.PLAY, Mode.WATCH}:
        raise RuntimeError("Stream operations are only allowed in play and watch modes.")


class _StreamBacking(BackingValue):
    id: Num
    index: Num

    def __init__(self, stream_id: int, index: Num):
        super().__init__()
        self.id = Num._accept_(stream_id)
        self.index = Num._accept_(index)

    def read(self) -> IRExpr:
        """Read the value from the stream."""
        _check_can_read_stream()
        return IRPureInstr(Op.StreamGetValue, [self.id.ir(), self.index.ir()])

    def write(self, value: IRExpr) -> None:
        """Write the value to the stream."""
        _check_can_write_stream()
        ctx().add_statement(IRInstr(Op.StreamSet, [self.id.ir(), self.index.ir(), value]))


class _SparseStreamBacking(BackingValue):
    id: Num
    index: Num

    def __init__(self, stream_id: int, index: Num):
        super().__init__()
        self.id = Num._accept_(stream_id)
        self.index = Num._accept_(index)

    def read(self) -> IRExpr:
        """Read the value from the stream."""
        _check_can_read_stream()
        return IRPureInstr(Op.StreamGetValue, [self.id.ir(), self.index.ir()])

    def write(self, value: IRExpr) -> None:
        """Write the value to the stream."""
        _check_can_write_stream()
        ctx().add_statements(
            IRInstr(Op.StreamSet, [self.id.ir(), self.index.ir(), value]),
            IRInstr(Op.StreamSet, [self.id.ir(), (self.index - 0.5).ir(), IRConst(0)]),
            IRInstr(Op.StreamSet, [self.id.ir(), (self.index + 0.5).ir(), IRConst(0)]),
        )


class Stream[T](Record):
    """Represents a stream.

    Most users should use [`@streams`][sonolus.script.stream.streams] to declare streams and stream groups rather than
    using this class directly.

    If used directly, it is important that streams do not overlap. No other streams should have an offset in
    `range(self.offset, self.offset + max(1, sizeof(self.element_type())))`, or they will overlap and interfere
    with each other.

    Usage:
        Declaring a stream:
        ```python
        @streams
        class Streams:
            my_stream_1: Stream[Num]  # A stream of Num values
            my_stream_2: Stream[Vec2]  # A stream of Vec2 values
        ```

        Directly creating a stream (advanced usage):
        ```python
        stream = Stream[Num](offset=0)
        ```
    """

    offset: int

    @classmethod
    def element_type(cls) -> type[T] | type[Value]:
        """Return the type of elements in this array type."""
        return cls.type_var_value(T)

    @classmethod
    @meta_fn
    def backing_size(cls) -> int:
        """Return the number of underlying single-value streams backing this stream."""
        return max(1, sizeof(cls.element_type()))

    def __contains__(self, item: int | float) -> bool:
        """Check if the stream contains the key."""
        _check_can_read_stream()
        return _stream_has(self.offset, item)

    @meta_fn
    def __getitem__(self, key: int | float) -> T:
        """Get the value corresponding to the key.

        If the key is not in the stream, interpolates linearly between surrounding values.
        If the stream is empty, returns the zero value of the element type.
        """
        # This is allowed in Play mode since a stream value may be accessed just to write to it without reading.
        _check_can_read_or_write_stream()
        return self.element_type()._from_backing_source_(lambda offset: _StreamBacking(self.offset + Num(offset), key))

    @meta_fn
    def __setitem__(self, key: int | float, value: T) -> None:
        """Set the value corresponding to the key."""
        _check_can_write_stream()
        if not self.element_type()._accepts_(value):
            raise TypeError(f"Cannot set value of type {type(value)} to stream of type {self.element_type()}.")
        if self.element_type()._size_() == 0:
            # We still need to store something to preserve the key, so this is a special case.
            _stream_set(self.offset, key, 0)
        else:
            for i, v in enumerate(value._to_list_()):
                _stream_set(self.offset + i, key, Num(v))

    def next_key(self, key: int | float) -> int:
        """Get the next key, or the key unchanged if it is the last key or the stream is empty.

        If the key is in the stream and there is a next key, returns the next key.
        """
        _check_can_read_stream()
        return _stream_get_next_key(self.offset, key)

    def next_key_or_default(self, key: int | float, default: int | float) -> int:
        """Get the next key, or the default value if there is no next key."""
        _check_can_read_stream()
        next_key = self.next_key(key)
        return next_key if next_key > key else default

    def previous_key(self, key: int | float) -> int:
        """Get the previous key, or the key unchanged if it is the first key or the stream is empty.

        If the key is in the stream and there is a previous key, returns the previous key.
        """
        _check_can_read_stream()
        return _stream_get_previous_key(self.offset, key)

    def previous_key_or_default(self, key: int | float, default: int | float) -> int:
        """Get the previous key, or the default value if there is no previous key."""
        _check_can_read_stream()
        previous_key = self.previous_key(key)
        return previous_key if previous_key < key else default

    def has_next_key(self, key: int | float) -> bool:
        """Check if there is a next key after the given key in the stream."""
        _check_can_read_stream()
        next_key = self.next_key(key)
        return next_key > key

    def has_previous_key(self, key: int | float) -> bool:
        """Check if there is a previous key before the given key in the stream."""
        _check_can_read_stream()
        previous_key = self.previous_key(key)
        return previous_key < key

    def next_key_inclusive(self, key: int | float) -> int:
        """Like `next_key`, but returns the key itself if it is in the stream."""
        _check_can_read_stream()
        return key if key in self else self.next_key(key)

    def previous_key_inclusive(self, key: int | float) -> int:
        """Like `previous_key`, but returns the key itself if it is in the stream."""
        _check_can_read_stream()
        return key if key in self else self.previous_key(key)

    def get_next(self, key: int | float) -> T:
        """Get the value corresponding to the next key.

        If there is no next key, returns the value at the given key. Equivalent to `self[self.next_key(key)]`.
        """
        _check_can_read_stream()
        return self[self.next_key(key)]

    def get_previous(self, key: int | float) -> T:
        """Get the value corresponding to the previous key.

        If there is no previous key, returns the value at the given key. Equivalent to `self[self.previous_key(key)]`.
        """
        _check_can_read_stream()
        return self[self.previous_key(key)]

    def get_next_inclusive(self, key: int | float) -> T:
        """Get the value corresponding to the next key, or the value at the given key if it is in the stream.

        Equivalent to `self[self.next_key_inclusive(key)]`.
        """
        _check_can_read_stream()
        return self[self.next_key_inclusive(key)]

    def get_previous_inclusive(self, key: int | float) -> T:
        """Get the value corresponding to the previous key, or the value at the given key if it is in the stream.

        Equivalent to `self[self.previous_key_inclusive(key)]`.
        """
        _check_can_read_stream()
        return self[self.previous_key_inclusive(key)]

    def iter_items_from(self, start: int | float, /) -> SonolusIterator[tuple[int | float, T]]:
        """Iterate over the items in the stream in ascending order starting from the given key.

        If the key is in the stream, it will be included in the iteration.

        Usage:
        ```python
        stream = ...
        for key, value in stream.iter_items_from(0):
            do_something(key, value)
        ```
        """
        _check_can_read_stream()
        return _StreamAscIterator(self, self.next_key_inclusive(start))

    def iter_items_since_previous_frame(self) -> SonolusIterator[tuple[int | float, T]]:
        """Iterate over the items in the stream since the last frame.

        This is a convenience method that iterates over the items in the stream occurring after the time of the
        previous frame and up to and including the current time.

        Usage:
        ```python
        stream = ...
        for key, value in stream.iter_items_since_previous_frame():
            do_something(key, value)
        ```
        """
        _check_can_read_stream()
        return _StreamBoundedAscIterator(self, self.next_key(prev_time()), time())

    def iter_items_from_desc(self, start: int | float, /) -> SonolusIterator[tuple[int | float, T]]:
        """Iterate over the items in the stream in descending order starting from the given key.

        If the key is in the stream, it will be included in the iteration.

        Usage:
        ```python
        stream = ...
        for key, value in stream.iter_items_from_desc(0):
            do_something(key, value)
        ```
        """
        _check_can_read_stream()
        return _StreamDescIterator(self, self.previous_key_inclusive(start))

    def iter_keys_from(self, start: int | float, /) -> SonolusIterator[int | float]:
        """Iterate over the keys in the stream in ascending order starting from the given key.

        If the key is in the stream, it will be included in the iteration.

        Usage:
        ```python
        stream = ...
        for key in stream.iter_keys_from(0):
            do_something(key)
        ```
        """
        _check_can_read_stream()
        return _StreamAscKeyIterator(self, self.next_key_inclusive(start))

    def iter_keys_since_previous_frame(self) -> SonolusIterator[int | float]:
        """Iterate over the keys in the stream since the last frame.

        This is a convenience method that iterates over the keys in the stream occurring after the time of the
        previous frame and up to and including the current time.

        Usage:
        ```python
        stream = ...
        for key in stream.iter_keys_since_previous_frame():
            do_something(key)
        ```
        """
        _check_can_read_stream()
        return _StreamBoundedAscKeyIterator(self, self.next_key(prev_time()), time())

    def iter_keys_from_desc(self, start: int | float, /) -> SonolusIterator[int | float]:
        """Iterate over the keys in the stream in descending order starting from the given key.

        If the key is in the stream, it will be included in the iteration.

        Usage:
        ```python
        stream = ...
        for key in stream.iter_keys_from_desc(0):
            do_something(key)
        ```
        """
        _check_can_read_stream()
        return _StreamDescKeyIterator(self, self.previous_key_inclusive(start))

    def iter_values_from(self, start: int | float, /) -> SonolusIterator[T]:
        """Iterate over the values in the stream in ascending order starting from the given key.

        If the key is in the stream, it will be included in the iteration.

        Usage:
        ```python
        stream = ...
        for value in stream.iter_values_from(0):
            do_something(value)
        ```
        """
        _check_can_read_stream()
        return _StreamAscValueIterator(self, self.next_key_inclusive(start))

    def iter_values_since_previous_frame(self) -> SonolusIterator[T]:
        """Iterate over the values in the stream since the last frame.

        This is a convenience method that iterates over the values in the stream occurring after the time of the
        previous frame and up to and including the current time.

        Usage:
        ```python
        stream = ...
        for value in stream.iter_values_since_previous_frame():
            do_something(value)
        ```
        """
        _check_can_read_stream()
        return _StreamBoundedAscValueIterator(self, self.next_key(prev_time()), time())

    def iter_values_from_desc(self, start: int | float, /) -> SonolusIterator[T]:
        """Iterate over the values in the stream in descending order starting from the given key.

        If the key is in the stream, it will be included in the iteration.

        Usage:
        ```python
        stream = ...
        for value in stream.iter_values_from_desc(0):
            do_something(value)
        ```
        """
        _check_can_read_stream()
        return _StreamDescValueIterator(self, self.previous_key_inclusive(start))


class StreamGroup[T, Size](Record):
    """Represents a group of streams.

    Most users should use [`@streams`][sonolus.script.stream.streams] to declare stream groups rather than using this
    class directly.

    Usage:
        Declaring a stream group:
        ```python
        @streams
        class Streams:
            my_group_1: StreamGroup[Num, 10]  # A group of 10 Num streams
            my_group_2: StreamGroup[Vec2, 5]  # A group of 5 Vec2 streams
        ```
    """

    offset: int

    @classmethod
    def size(cls) -> Size:
        """Return the size of the group."""
        return cls.type_var_value(Size)

    @classmethod
    def element_type(cls) -> type[T] | type[Value]:
        """Return the type of elements in this group."""
        return cls.type_var_value(T)

    @classmethod
    @meta_fn
    def backing_size(cls) -> int:
        """Return the number of underlying single-value streams backing this stream."""
        return max(1, sizeof(cls.element_type())) * cls.size()

    def __contains__(self, item: int) -> bool:
        """Check if the group contains the stream with the given index."""
        _check_can_read_or_write_stream()
        return 0 <= item < self.size()

    def __getitem__(self, index: int) -> Stream[T]:
        """Get the stream at the given index."""
        _check_can_read_or_write_stream()
        assert index in self
        # Size 0 elements still need 1 stream to preserve the key.
        return Stream[self.type_var_value(T)](max(1, sizeof(self.element_type())) * index + self.offset)


class _StreamAscIterator[T](Record, SonolusIterator[tuple[int | float, T]]):
    stream: Stream[T]
    current_key: int | float

    def has_next(self) -> bool:
        return self.current_key in self.stream

    def get(self) -> tuple[int | float, T]:
        return self.current_key, self.stream[self.current_key]

    def advance(self):
        self.current_key = self.stream.next_key_or_default(self.current_key, inf)


class _StreamBoundedAscIterator[T](Record, SonolusIterator[tuple[int | float, T]]):
    stream: Stream[T]
    current_key: int | float
    end_key: int | float

    def has_next(self) -> bool:
        return self.current_key in self.stream and self.current_key <= self.end_key

    def get(self) -> tuple[int | float, T]:
        return self.current_key, self.stream[self.current_key]

    def advance(self):
        self.current_key = self.stream.next_key_or_default(self.current_key, inf)


class _StreamDescIterator[T](Record, SonolusIterator[tuple[int | float, T]]):
    stream: Stream[T]
    current_key: int | float

    def has_next(self) -> bool:
        return self.current_key in self.stream

    def get(self) -> tuple[int | float, T]:
        return self.current_key, self.stream[self.current_key]

    def advance(self):
        self.current_key = self.stream.previous_key_or_default(self.current_key, -inf)


class _StreamAscKeyIterator[T](Record, SonolusIterator[int | float]):
    stream: Stream[T]
    current_key: int | float

    def has_next(self) -> bool:
        return self.current_key in self.stream

    def get(self) -> int | float:
        return self.current_key

    def advance(self):
        self.current_key = self.stream.next_key_or_default(self.current_key, inf)


class _StreamBoundedAscKeyIterator[T](Record, SonolusIterator[int | float]):
    stream: Stream[T]
    current_key: int | float
    end_key: int | float

    def has_next(self) -> bool:
        return self.current_key in self.stream and self.current_key <= self.end_key

    def get(self) -> int | float:
        return self.current_key

    def advance(self):
        self.current_key = self.stream.next_key_or_default(self.current_key, inf)


class _StreamDescKeyIterator[T](Record, SonolusIterator[int | float]):
    stream: Stream[T]
    current_key: int | float

    def has_next(self) -> bool:
        return self.current_key in self.stream

    def get(self) -> int | float:
        return self.current_key

    def advance(self):
        self.current_key = self.stream.previous_key_or_default(self.current_key, -inf)


class _StreamAscValueIterator[T](Record, SonolusIterator[T]):
    stream: Stream[T]
    current_key: int | float

    def has_next(self) -> bool:
        return self.current_key in self.stream

    def get(self) -> T:
        return self.stream[self.current_key]

    def advance(self):
        self.current_key = self.stream.next_key_or_default(self.current_key, inf)


class _StreamBoundedAscValueIterator[T](Record, SonolusIterator[T]):
    stream: Stream[T]
    current_key: int | float
    end_key: int | float

    def has_next(self) -> bool:
        return self.current_key in self.stream and self.current_key <= self.end_key

    def get(self) -> T:
        return self.stream[self.current_key]

    def advance(self):
        self.current_key = self.stream.next_key_or_default(self.current_key, inf)


class _StreamDescValueIterator[T](Record, SonolusIterator[T]):
    stream: Stream[T]
    current_key: int | float

    def has_next(self) -> bool:
        return self.current_key in self.stream

    def get(self) -> T:
        return self.stream[self.current_key]

    def advance(self):
        self.current_key = self.stream.previous_key_or_default(self.current_key, -inf)


@native_function(Op.StreamGetNextKey)
def _stream_get_next_key(stream_id: int, key: int | float) -> int:
    """Get the next key in the stream, or the key unchanged if it is the last key or the stream is empty."""
    raise NotImplementedError


@native_function(Op.StreamGetPreviousKey)
def _stream_get_previous_key(stream_id: int, key: int | float) -> int:
    """Get the previous key in the stream, or the key unchanged if it is the first key or the stream is empty."""
    raise NotImplementedError


@native_function(Op.StreamGetValue)
def _stream_get_value(stream_id: int, key: int | float) -> float:
    """Get the value of the key in the stream."""
    raise NotImplementedError


@native_function(Op.StreamHas)
def _stream_has(stream_id: int, key: int | float) -> bool:
    """Check if the stream has the key."""
    raise NotImplementedError


@native_function(Op.StreamSet)
def _stream_set(stream_id: int, key: int | float, value: float) -> None:
    """Set the value of the key in the stream."""
    raise NotImplementedError
