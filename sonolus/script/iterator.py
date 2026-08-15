from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Final

from sonolus.script.debug import require
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
    strict: bool

    def next(self) -> Maybe[tuple[Any, ...]]:
        return _zip_next(self.iterators, (), self.strict, "zip")


class _MapZipper[T](Record, SonolusIterator):
    iterators: T
    strict: bool

    def next(self) -> Maybe[tuple[Any, ...]]:
        return _zip_next(self.iterators, (), self.strict, "map")


@meta_fn
def _zip_next(chain, values, strict, name) -> Maybe[tuple[Any, ...]]:
    from sonolus.script.containers import Pair
    from sonolus.script.internal.visitor import compile_and_call

    argument = len(values) + 1
    if isinstance(chain, Pair):
        if values:
            message = _zip_length_error_message(name, argument, "shorter")
            return compile_and_call(_zip_next_pair, chain.first, chain.second, values, strict, name, message)
        return compile_and_call(_zip_next_first_pair, chain.first, chain.second, strict, name)
    if values:
        message = _zip_length_error_message(name, argument, "shorter")
        return compile_and_call(_zip_next_last, chain, values, strict, message)
    return compile_and_call(_zip_next_single, chain)


def _zip_next_first_pair(arm, rest, strict, name) -> Maybe[tuple[Any, ...]]:
    value = _validate_next_result(arm.next())
    if value.is_nothing:
        if strict:
            _zip_check_exhausted(rest, 2, name)
        return Nothing
    return _zip_next(rest, (value.get_unsafe(),), strict, name)


def _zip_next_pair(arm, rest, values, strict, name, message) -> Maybe[tuple[Any, ...]]:
    value = _validate_next_result(arm.next())
    if value.is_nothing:
        if strict:
            require(False, message)
        return Nothing
    return _zip_next(rest, (*values, value.get_unsafe()), strict, name)


def _zip_next_last(arm, values, strict, message) -> Maybe[tuple[Any, ...]]:
    value = _validate_next_result(arm.next())
    if value.is_nothing:
        if strict:
            require(False, message)
        return Nothing
    return Some((*values, value.get_unsafe()))


def _zip_next_single(arm) -> Maybe[tuple[Any, ...]]:
    value = _validate_next_result(arm.next())
    if value.is_nothing:
        return Nothing
    return Some((value.get_unsafe(),))


def _zip_length_error_message(name: str, argument: int, relation: str) -> str:
    previous = "argument 1" if argument == 2 else f"arguments 1-{argument - 1}"
    return f"{name}() argument {argument} is {relation} than {previous}"


@meta_fn
def _zip_check_exhausted(chain, argument, name) -> Maybe[tuple[Any, ...]]:
    from sonolus.script.containers import Pair
    from sonolus.script.internal.visitor import compile_and_call

    message = _zip_length_error_message(name, argument, "longer")
    if isinstance(chain, Pair):
        return compile_and_call(_zip_check_exhausted_pair, chain.first, chain.second, argument, name, message)
    return compile_and_call(_zip_check_exhausted_last, chain, message)


@meta_fn
def _advance_without_owner_check(iterator):
    from sonolus.script.internal.context import disable_iterator_owner_checks
    from sonolus.script.internal.visitor import compile_and_call

    if not ctx():
        return iterator.next()
    with disable_iterator_owner_checks():
        return compile_and_call(iterator.next)


def _zip_check_exhausted_pair(arm, rest, argument, name, message) -> Maybe[tuple[Any, ...]]:
    value = _validate_next_result(_advance_without_owner_check(arm))
    if value.is_some:
        require(False, message)
        return Nothing
    return _zip_check_exhausted(rest, argument + 1, name)


def _zip_check_exhausted_last(arm, message) -> Maybe[tuple[Any, ...]]:
    value = _validate_next_result(_advance_without_owner_check(arm))
    if value.is_some:
        require(False, message)
    return Nothing


class _EmptyIterator(Record, SonolusIterator):
    def next(self) -> Maybe[Any]:
        return Nothing


class _IteratorWithoutOwnerChecks[T](Record, SonolusIterator):
    iterator: T

    def next(self) -> Maybe[Any]:
        return _validate_next_result(_advance_without_owner_check(self.iterator))


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
