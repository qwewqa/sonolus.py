from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sonolus.script.internal.context import ctx
from sonolus.script.internal.impl import meta_fn
from sonolus.script.internal.transient import TransientValue
from sonolus.script.internal.value import Value
from sonolus.script.num import Num


class Maybe[T](TransientValue):
    """A type that either has a value or is empty.

    Maybe has special behavior when returned from a function: it may be returned from multiple places
    in a function, provided that all but one return statement returns the literal `Nothing`.

    This type is not intended for other uses, such as being stored in a Record, Array, or Archetype.

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
        self._value = value

    @property
    @meta_fn
    def is_some(self) -> bool:
        """Check if the value is present."""
        if ctx():
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
        return self._value

    def map[R](self, fn: Callable[[T], R], /) -> Maybe[R]:
        if self.is_some:
            return Some(fn(self.get_unsafe()))
        return Nothing

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
    """Create a `Maybe` instance with a value.

    Args:
        value: The contained value.

    Returns:
        A `Maybe` instance that contains the provided value.
    """
    return Maybe(present=True, value=value)


Nothing: Maybe[Any] = Maybe(present=False, value=None)  # type: ignore

# Note: has to come after the definition to hide the definition in the docs.
Nothing: Maybe[Any]
"""The empty `Maybe` instance."""
