from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Final

from sonolus.script.internal.context import ctx
from sonolus.script.internal.meta_fn import meta_fn
from sonolus.script.maybe import Maybe, Nothing, Some
from sonolus.script.record import Record


class SonolusIterator[T]:
    """Base class for Sonolus iterators.

    This class is used to define custom iterators that can be used in Sonolus.py.

    Inheritors must implement the [`next`][sonolus.script.iterator.SonolusIterator.next] method,
    which should return a [`Maybe[T]`][sonolus.script.maybe.Maybe].

    Use an iterator only once: in one `for` loop, in one call to `next()`, or by passing it once to another iterator
    consumer.

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
        raise NotImplementedError("SonolusIterator subclasses must implement next()")

    def __next__(self) -> T:
        """Return the next item, for use outside of compiled code only.

        This is not intended to be overridden, and just serves to allow iterators to work in regular Python code.
        """
        result = _validate_next_result(self.next())
        if result.is_some:
            return result.get_unsafe()
        else:
            raise StopIteration

    def __iter__(self) -> SonolusIterator[T]:
        """Return the iterator itself."""
        return self


@meta_fn
def _validate_next_result(value) -> Maybe[Any]:
    if not isinstance(value, Maybe):
        from sonolus.script.internal.builtin_impls import _type_name

        raise TypeError(f"Iterator.next() returned '{_type_name(value)}', expected Maybe")
    return value


class _Enumerator[V: SonolusIterator](Record, SonolusIterator):
    i: int
    offset: Final[int]
    iterator: V

    def next(self) -> Maybe[tuple[int, Any]]:
        value = _validate_next_result(self.iterator.next())
        if value.is_nothing:
            return Nothing
        result = (self.i + self.offset, value.get_unsafe())
        self.i += 1
        return Some(result)


class _Zipper[T](Record, SonolusIterator):
    # Can be a, Pair[a, b], Pair[a, Pair[b, c]], etc.
    iterators: T

    def next(self) -> Maybe[tuple[Any, ...]]:
        return _zip_next(self.iterators, ())


@meta_fn
def _zip_next(chain, values) -> Maybe[tuple[Any, ...]]:
    from sonolus.script.containers import Pair
    from sonolus.script.internal.visitor import compile_and_call

    if isinstance(chain, Pair):
        return compile_and_call(_zip_next_pair, chain.first, chain.second, values)
    return compile_and_call(_zip_next_last, chain, values)


def _zip_next_pair(arm, rest, values) -> Maybe[tuple[Any, ...]]:
    value = _validate_next_result(arm.next())
    if value.is_nothing:
        return Nothing
    return _zip_next(rest, (*values, value.get_unsafe()))


def _zip_next_last(arm, values) -> Maybe[tuple[Any, ...]]:
    value = _validate_next_result(arm.next())
    if value.is_nothing:
        return Nothing
    return Some((*values, value.get_unsafe()))


class _EmptyIterator(Record, SonolusIterator):
    def next(self) -> Maybe[Any]:
        return Nothing


class _MappingIterator[T, Fn](Record, SonolusIterator):
    fn: Fn
    iterator: T

    def next(self) -> Maybe[Any]:
        return _validate_next_result(self.iterator.next()).map(self.fn)


class _FilteringIterator[T, Fn](Record, SonolusIterator):
    fn: Fn
    iterator: T

    def next(self) -> Maybe[T]:
        while True:
            value = _validate_next_result(self.iterator.next())
            if value.is_nothing:
                return Nothing
            inside = value.get_unsafe()
            if self.fn(inside):
                return Some(inside)


@meta_fn
def maybe_next[T](iterator: Iterator[T]) -> Maybe[T]:
    """Get the next item from an iterator as a [`Maybe`][sonolus.script.maybe.Maybe].

    The iterator must be a [`SonolusIterator`][sonolus.script.iterator.SonolusIterator] instance.
    """
    from sonolus.script.internal.visitor import compile_and_call

    if not isinstance(iterator, SonolusIterator):
        raise TypeError("Iterator must be an instance of SonolusIterator.")
    if ctx():
        from sonolus.script.internal.builtin_impls import _advance_iterator_once

        result = compile_and_call(_advance_iterator_once, iterator)
        if not ctx().live:
            return Nothing
        return _validate_next_result(result)
    else:
        return _validate_next_result(iterator.next())
