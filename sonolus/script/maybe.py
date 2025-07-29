from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sonolus.script.internal.context import ctx
from sonolus.script.internal.impl import meta_fn, validate_value
from sonolus.script.internal.transient import TransientValue
from sonolus.script.internal.value import Value
from sonolus.script.num import Num
from sonolus.script.values import copy, zeros


class Maybe[T](TransientValue):
    """A type that either has a value or is empty.

    `Maybe` has special behavior when returned from a function: unlike records and arrays, it may be returned from
    multiple places in a function, provided that all but one return statement returns the literal
    [`Nothing`][sonolus.script.maybe.Nothing].

    Storing values of this type in a Record, Array, or Archetype is not supported.

    Usage:
        ```python
        def fn(a, b):
            if a:
                return Some(b)
            else:
                return Nothing

        result = fn(..., ...)
        if result.is_some:
            value = result.get()
            ...
        ```
    """

    _present: Num
    _value: T

    def __init__(self, *, present: bool, value: T):
        self._present = Num._accept_(present)
        self._value = validate_value(value)

    @property
    @meta_fn
    def is_some(self) -> bool:
        """Check if the value is present."""
        if ctx():
            if self._present._is_py_():
                # Makes this a compile time constant.
                return self._present
            return self._present._get_readonly_()
        else:
            return self._present._as_py_()

    @property
    def is_nothing(self) -> bool:
        """Check if the value is empty."""
        return not self.is_some

    def get(self) -> T:
        """Get the value if present, otherwise raise an error."""
        assert self.is_some
        return self.get_unsafe()

    @meta_fn
    def get_unsafe(self) -> T:
        if ctx():
            return self._value
        else:
            return self._value._as_py_()

    def map[R](self, fn: Callable[[T], R], /) -> Maybe[R]:
        """Map the contained value to a new value using the provided function.

        If the value is not present, returns [`Nothing`][sonolus.script.maybe.Nothing].

        Args:
            fn: A function that takes the contained value and returns a new value.

        Returns:
            A [`Maybe`][sonolus.script.maybe.Maybe] instance containing the result of the function if the value
            is present, otherwise [`Nothing`][sonolus.script.maybe.Nothing].
        """
        if self.is_some:
            return Some(fn(self.get_unsafe()))
        return Nothing

    def flat_map[R](self, fn: Callable[[T], Maybe[R]], /) -> Maybe[R]:
        """Flat map the contained value to a new [`Maybe`][sonolus.script.maybe.Maybe] using the provided function.

        If the value is not present, returns [`Nothing`][sonolus.script.maybe.Nothing].

        Args:
            fn: A function that takes the contained value and returns a new [`Maybe`][sonolus.script.maybe.Maybe].

        Returns:
            A [`Maybe`][sonolus.script.maybe.Maybe] instance containing the result of the function if the value
            is present, otherwise [`Nothing`][sonolus.script.maybe.Nothing].
        """
        if self.is_some:
            return fn(self.get_unsafe())
        return Nothing

    def or_default(self, default: T) -> T:
        """Return a copy of the contained value if present, otherwise return a copy of the given default value.

        Args:
            default: The default value to return if the contained value is not present.

        Returns:
            A copy of the contained value if present, otherwise a copy of the default value.
        """
        result = _box(copy(default))
        if self.is_some:
            result.value = self.get_unsafe()
        return result.value

    @meta_fn
    def or_else(self, fn: Callable[[], T], /) -> T:
        """Return a copy of the contained value if present, otherwise return a copy of the result of the given function.

        Args:
            fn: A function that returns a value to use if the contained value is not present.

        Returns:
            A copy of the contained value if present, otherwise a copy of the result of calling the function.
        """
        from sonolus.backend.visitor import compile_and_call

        if ctx():
            if self.is_some._is_py_():  # type: ignore
                if self.is_some._as_py_():  # type: ignore
                    return copy(self.get_unsafe())
                else:
                    return copy(compile_and_call(fn))
            else:
                return compile_and_call(self._or_else, fn)
        elif self.is_some:
            return copy(self.get_unsafe())
        else:
            return copy(fn())

    def _or_else(self, fn: Callable[[], T], /) -> T:
        result = _box(zeros(self.contained_type))
        if self.is_some:
            result.value = self.get_unsafe()
        else:
            result.value = fn()
        return result.value

    @property
    def tuple(self) -> tuple[bool, T]:
        """Return whether the value is present and a copy of the contained value if present as a tuple.

        If the value is not present, the tuple will contain `False` and a zero initialized value of the contained type.
        """
        result_value = _box(zeros(self.contained_type))
        if self.is_some:
            result_value.value = self.get_unsafe()
        return self.is_some, result_value.value

    @property
    @meta_fn
    def contained_type(self):
        return type(self._value)

    @classmethod
    def _accepts_(cls, value: Any) -> bool:
        return isinstance(value, cls)

    @classmethod
    def _accept_(cls, value: Any) -> Maybe[T]:
        if not cls._accepts_(value):
            raise TypeError(f"Cannot accept value of type {type(value).__name__} as {cls.__name__}.")
        return value

    def _is_py_(self) -> bool:
        return not self._present or not isinstance(self._value, Value) or self._value._is_py_()

    def _as_py_(self) -> Any:
        if not self._is_py_():
            raise ValueError("Not a python value")
        return self

    def _copy_from_(self, value: Any):
        raise TypeError("Maybe does not support mutation.")

    def _copy_(self) -> Maybe[T]:
        raise TypeError("Maybe does not support copying.")

    def _set_(self, value: Any):
        if not self._accepts_(value):
            raise TypeError(f"Cannot set value of type {type(value).__name__} to {self.__class__.__name__}.")
        if value is not Nothing and self._value is not value._value:
            raise TypeError(f"Cannot set value of type {type(value._value).__name__} to {self.__class__.__name__}.")
        self._present._set_(value._present)

    @classmethod
    def _get_merge_target_(cls, values: list[Any]) -> Any:
        if not all(isinstance(v, cls) for v in values):
            return NotImplemented
        distinct = []
        seen_ids = set()
        for v in values:
            if v is Nothing:
                continue
            if id(v._value) not in seen_ids:
                distinct.append(v)
                seen_ids.add(id(v._value))
        match distinct:
            case []:
                return Nothing
            case [v]:
                return Maybe(present=Num._alloc_(), value=v._value)
            case _:
                return NotImplemented


def Some[T](value: T) -> Maybe[T]:  # noqa: N802
    """Create a [`Maybe`][sonolus.script.maybe.Maybe] instance with a value.

    Args:
        value: The contained value.

    Returns:
        A [`Maybe`][sonolus.script.maybe.Maybe] instance that contains the provided value.
    """
    return Maybe(present=True, value=value)


Nothing: Maybe[Any] = Maybe(present=False, value=None)  # type: ignore

# Note: has to come after the definition to hide the definition in the docs.
Nothing: Maybe[Any]
"""The empty [`Maybe`][sonolus.script.maybe.Maybe] instance."""


@meta_fn
def _box(value):
    from sonolus.script.containers import Box

    return Box(value)
