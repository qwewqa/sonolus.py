from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from sonolus.script.internal.context import ctx
from sonolus.script.internal.impl import meta_fn
from sonolus.script.maybe import Maybe, Nothing, Some
from sonolus.script.record import Record


class SonolusIterator[T]:
    """Base class for Sonolus iterators.

    This class is used to define custom iterators that can be used in Sonolus.py.

    Inheritors must implement the [`next`][sonolus.script.iterator.SonolusIterator.next] method,
    which should return a [`Maybe[T]`][sonolus.script.maybe.Maybe].

    Usage:
        ```python
        class MyIterator(Record, SonolusIterator):
            def next(self) -> Maybe[T]:
                ...
        ```
    """

    _allow_instance_check_ = True

    @meta_fn
    def next(self) -> Maybe[T]:
        """Return the next item from the iterator as a [`Maybe`][sonolus.script.maybe.Maybe]."""
        raise NotImplementedError

    def __next__(self) -> T:
        result = self.next()
        if result.is_some:
            return result.get_unsafe()
        else:
            raise StopIteration

    def __iter__(self) -> SonolusIterator[T]:
        return self


class _Enumerator[V: SonolusIterator](Record, SonolusIterator):
    i: int
    offset: int
    iterator: V

    def next(self) -> Maybe[tuple[int, Any]]:
        value = self.iterator.next()
        if value.is_nothing:
            return Nothing
        result = (self.i + self.offset, value.get_unsafe())
        self.i += 1
        return Some(result)


class _Zipper[T](Record, SonolusIterator):
    # Can be a, Pair[a, b], Pair[a, Pair[b, c]], etc.
    iterators: T

    @meta_fn
    def _get_iterators(self) -> tuple[SonolusIterator, ...]:
        from sonolus.script.containers import Pair

        iterators = []
        v = self.iterators
        while isinstance(v, Pair):
            iterators.append(v.first)
            v = v.second
        iterators.append(v)
        return tuple(iterators)

    @meta_fn
    def _get_next_values(self) -> tuple[Any, ...]:
        from sonolus.backend.visitor import compile_and_call

        return tuple(compile_and_call(iterator.next) for iterator in self._get_iterators())

    @meta_fn
    def _values_to_tuple(self, values: tuple[Any, ...]) -> tuple[Any, ...]:
        from sonolus.backend.visitor import compile_and_call

        return tuple(compile_and_call(value.get_unsafe) for value in values)

    def next(self) -> Maybe[tuple[Any, ...]]:
        values = self._get_next_values()
        for value in values:
            if value.is_nothing:
                return Nothing
        return Some(self._values_to_tuple(values))


class _EmptyIterator(Record, SonolusIterator):
    def next(self) -> Maybe[Any]:
        return Nothing


class _MappingIterator[T, Fn](Record, SonolusIterator):
    fn: Fn
    iterator: T

    def next(self) -> Maybe[Any]:
        return self.iterator.next().map(self.fn)


class _FilteringIterator[T, Fn](Record, SonolusIterator):
    fn: Fn
    iterator: T

    def next(self) -> Maybe[T]:
        while True:
            value = self.iterator.next()
            if value.is_nothing:
                return Nothing
            inside = value.get_unsafe()
            if self.fn(inside):
                return Some(inside)


@meta_fn
def maybe_next[T](iterator: Iterator[T]) -> Maybe[T]:
    """Get the next item from an iterator as a [`Maybe`][sonolus.script.maybe.Maybe]."""
    from sonolus.backend.visitor import compile_and_call

    if not isinstance(iterator, SonolusIterator):
        raise TypeError("Iterator must be an instance of SonolusIterator.")
    if ctx():
        return compile_and_call(iterator.next)
    else:
        return iterator.next()
