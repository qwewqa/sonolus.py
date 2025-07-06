# ruff: noqa: B905
from typing import Any, Self

from sonolus.script.internal.impl import meta_fn, validate_value
from sonolus.script.internal.transient import TransientValue


class TupleImpl(TransientValue):
    value: tuple

    def __init__(self, value: tuple):
        self.value = value

    @meta_fn
    def __getitem__(self, item):
        item = validate_value(item)
        if not item._is_py_():
            raise TypeError(f"Cannot index tuple with non compile-time constant {item}")
        item = item._as_py_()
        if not isinstance(item, int | float):
            raise TypeError(f"Cannot index tuple with {item}")
        if int(item) != item:
            raise TypeError(f"Cannot index tuple with non-integer {item}")
        if not (-len(self.value) <= item < len(self.value)):
            raise IndexError(f"Tuple index out of range: {item}")
        if item < 0:
            item += len(self.value)
        return self.value[int(item)]

    @meta_fn
    def __len__(self):
        return len(self.value)

    def __eq__(self, other):
        if not self._is_tuple_impl(other):
            return False
        if len(self) != len(other):
            return False
        for a, b in zip(self, other):  # noqa: SIM110
            if a != b:
                return False
        return True

    def __ne__(self, other):
        if not self._is_tuple_impl(other):
            return True
        if len(self) != len(other):
            return True
        for a, b in zip(self, other):  # noqa: SIM110
            if a != b:
                return True
        return False

    def __lt__(self, other):
        if not self._is_tuple_impl(other):
            return NotImplemented
        for a, b in zip(self, other):
            if a != b:
                return a < b
        return len(self.value) < len(other.value)

    def __le__(self, other):
        if not self._is_tuple_impl(other):
            return NotImplemented
        for a, b in zip(self, other):
            if a != b:
                return a < b
        return len(self.value) <= len(other.value)

    def __gt__(self, other):
        if not self._is_tuple_impl(other):
            return NotImplemented
        for a, b in zip(self, other):
            if a != b:
                return a > b
        return len(self.value) > len(other.value)

    def __ge__(self, other):
        if not self._is_tuple_impl(other):
            return NotImplemented
        for a, b in zip(self, other):
            if a != b:
                return a > b
        return len(self.value) >= len(other.value)

    def __hash__(self):
        return hash(self.value)

    @meta_fn
    def __add__(self, other) -> Self:
        other = TupleImpl._accept_(other)
        return TupleImpl._accept_(self.value + other.value)

    @staticmethod
    @meta_fn
    def _is_tuple_impl(value: Any) -> bool:
        return isinstance(value, TupleImpl)

    @classmethod
    def _accepts_(cls, value: Any) -> bool:
        return isinstance(value, cls | tuple)

    @classmethod
    def _accept_(cls, value: Any) -> Self:
        if not cls._accepts_(value):
            raise TypeError(f"Cannot accept {value} as {cls.__name__}")
        if isinstance(value, cls):
            return value
        else:
            return cls(tuple(validate_value(item) for item in value))

    def _is_py_(self) -> bool:
        return all(item._is_py_() for item in self.value)

    def _as_py_(self) -> tuple:
        return tuple(item._as_py_() for item in self.value)


TupleImpl.__name__ = "tuple"
TupleImpl.__qualname__ = "tuple"
