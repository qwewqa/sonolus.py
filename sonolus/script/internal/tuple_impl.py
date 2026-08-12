# ruff: noqa: B905
from enum import Enum
from typing import Any, Self

from sonolus.script.internal.impl import validate_value
from sonolus.script.internal.introspection import describe_value
from sonolus.script.internal.simple_meta_fn import simple_meta_fn
from sonolus.script.internal.transient import TransientValue


@simple_meta_fn
def _index_not_found():
    # Local import, and hence a meta_fn: sonolus.script.debug imports sonolus.script.internal.impl, which
    # imports this module, so a module-scope import from it is a cycle. A traced function cannot carry an
    # import statement of its own.
    from sonolus.script.debug import error, runtime_checks_enabled

    # Gated on runtime_checks_enabled rather than written as assert_true(False, ...): a miss in a tuple of
    # compile-time constants folds to a statically false condition, and assert_true deliberately still fires on
    # those, which would terminate even with checks disabled and diverge from Range.index.
    if runtime_checks_enabled():
        error("tuple.index(x): x not in tuple")


class TupleImpl(TransientValue):
    value: tuple

    def __init__(self, value: tuple):
        self.value = value

    def __repr__(self):
        # Without this, object.__repr__ puts a heap address into any error message interpolating a tuple,
        # directly or through the repr of a value holding one.
        if len(self.value) == 1:
            return f"({describe_value(self.value[0])},)"
        return f"({', '.join(describe_value(item) for item in self.value)})"

    @simple_meta_fn
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

    @simple_meta_fn
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

    @simple_meta_fn
    def __add__(self, other) -> Self:
        other = TupleImpl._accept_(other)
        return TupleImpl._accept_(self.value + other.value)

    def __contains__(self, item):
        for element in self.value:  # noqa: SIM110
            if element == item:
                return True
        return False

    def index(self, value, start: int = 0, stop: int | None = None):
        """Return the index of the first element of the tuple equal to the given value.

        With runtime checks enabled, a missing value terminates the callback rather than returning -1 as
        array-like types do, which is as close as the subset gets to the `ValueError` Python raises. The
        termination is gated on those checks, so with them disabled -1 surfaces instead, matching
        `Range.index`. The elements are compared in order at compile time, so a tuple whose elements are all
        compile-time constants resolves to a constant index.

        Args:
            value: The value to search for.
            start: The index to start searching from.
            stop: The index to stop searching at. If `None`, search to the end of the tuple.
        """
        length = len(self.value)
        if stop is None:
            stop = length
        # Bounds are clipped rather than rejected, as in Python: a negative bound counts from the end, and one
        # out of range is pulled back to the nearest end.
        start = max(start + (start < 0) * length, 0)
        stop = min(stop + (stop < 0) * length, length)
        for i, element in enumerate(self.value):
            if start <= i < stop and element == value:
                return i
        _index_not_found()
        return -1

    @staticmethod
    @simple_meta_fn
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

    def _tuple_iter_(self) -> tuple:
        return self.value


TupleImpl.__name__ = "tuple"
TupleImpl.__qualname__ = "tuple"


def has_tuple_iter(v) -> bool:
    v = validate_value(v)
    if hasattr(v, "_tuple_iter_"):
        return True
    if v._is_py_():
        v_py = v._as_py_()
        if isinstance(v_py, type):
            return issubclass(v_py, Enum)
    return False


def tuple_iter(v) -> tuple:
    v = validate_value(v)
    if hasattr(v, "_tuple_iter_"):
        return v._tuple_iter_()
    if v._is_py_():
        v_py = v._as_py_()
        if isinstance(v_py, type):
            if issubclass(v_py, Enum):
                return tuple(v_py)
            raise TypeError(f"Cannot iterate over non-Enum class {v_py}")
    raise TypeError(f"Cannot iterate over {v}")
