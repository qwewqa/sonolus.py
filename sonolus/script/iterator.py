from __future__ import annotations

from abc import abstractmethod
from typing import Any

from sonolus.script.internal.impl import meta_fn
from sonolus.script.record import Record


class SonolusIterator[T]:
    """Base class for Sonolus iterators.

    This class is used to define custom iterators that can be used in Sonolus.py.

    Inheritors must implement the `has_next`, `get`, and `advance` methods.
    The `__next__` and `__iter__` methods are implemented by default.

    Usage:
        ```python
        class MyIterator(Record, SonolusIterator):
            def has_next(self) -> bool:
                ...

            def get(self) -> Any:
                ...

            def advance(self):
                ...
        ```
    """

    _allow_instance_check_ = True

    def next(self) -> T:
        result = self.get()
        self.advance()
        return result

    @abstractmethod
    def has_next(self) -> bool:
        """Return whether the iterator has more elements."""
        raise NotImplementedError

    @abstractmethod
    def get(self) -> T:
        """Return the next element of the iterator.

        May be called multiple times before calling `advance`.

        Must not be called if `has_next` returns `False`.
        """
        raise NotImplementedError

    @abstractmethod
    def advance(self):
        """Advance the iterator to the next element.

        Must not be called if `has_next` returns `False`.
        """
        raise NotImplementedError

    def __next__(self) -> T:
        if not self.has_next():
            raise StopIteration
        return self.next()

    def __iter__(self) -> SonolusIterator[T]:
        return self


class _Enumerator[V: SonolusIterator](Record, SonolusIterator):
    i: int
    offset: int
    iterator: V

    def has_next(self) -> bool:
        return self.iterator.has_next()

    def get(self) -> tuple[int, Any]:
        return self.i + self.offset, self.iterator.get()

    def advance(self):
        self.i += 1
        self.iterator.advance()


class _Zipper[T](Record, SonolusIterator):
    # Can be a, Pair[a, b], Pair[a, Pair[b, c]], etc.
    iterators: T

    @meta_fn
    def has_next(self) -> bool:
        from sonolus.backend.visitor import compile_and_call

        return compile_and_call(self._has_next, self._get_iterators())

    def _get_iterators(self) -> tuple[SonolusIterator, ...]:
        from sonolus.script.containers import Pair

        iterators = []
        v = self.iterators
        while isinstance(v, Pair):
            iterators.append(v.first)
            v = v.second
        iterators.append(v)
        return tuple(iterators)

    def _has_next(self, iterators: tuple[SonolusIterator, ...]) -> bool:
        for iterator in iterators:  # noqa: SIM110
            if not iterator.has_next():
                return False
        return True

    @meta_fn
    def get(self) -> tuple[Any, ...]:
        from sonolus.backend.visitor import compile_and_call

        return tuple(compile_and_call(iterator.get) for iterator in self._get_iterators())

    @meta_fn
    def advance(self):
        from sonolus.backend.visitor import compile_and_call

        for iterator in self._get_iterators():
            compile_and_call(iterator.advance)


class _EmptyIterator(Record, SonolusIterator):
    def has_next(self) -> bool:
        return False

    def get(self) -> Any:
        return None

    def advance(self):
        pass


class _MappingIterator[T, Fn](Record, SonolusIterator):
    fn: Fn
    iterator: T

    def has_next(self) -> bool:
        return self.iterator.has_next()

    def get(self) -> Any:
        return self.fn(self.iterator.get())

    def advance(self):
        self.iterator.advance()


class _FilteringIterator[T, Fn](Record, SonolusIterator):
    fn: Fn
    iterator: T

    def has_next(self) -> bool:
        while self.iterator.has_next():
            if self.fn(self.iterator.get()):
                return True
            self.iterator.advance()
        return False

    def get(self) -> Any:
        return self.iterator.get()

    def advance(self):
        self.iterator.advance()


class _ChainingIterator[T](Record, SonolusIterator):
    iterator: T

    def has_next(self) -> bool:
        return self.iterator.has_next()

    def get(self) -> Any:
        return self.iterator.get().get()

    def advance(self):
        self.iterator.get().advance()
        while not self.iterator.get().has_next():
            self.iterator.advance()
            if not self.iterator.has_next():
                break
