from abc import abstractmethod
from collections.abc import Iterable
from typing import Any, Self

from sonolus.backend.place import BlockPlace


class Value:
    """Base class for values."""

    @classmethod
    def _is_concrete_(cls) -> bool:
        """Returns whether this type is concrete (i.e. can be instantiated)."""
        return False

    @classmethod
    @abstractmethod
    def _size_(cls) -> int:
        """Returns the size of this value."""
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def _is_value_type_(cls) -> bool:
        """Returns whether this is a value type.

        If this is true, the value behaves immutably and _set_ is supported.
        """
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def _from_place_(cls, place: BlockPlace) -> Self:
        """Creates a value from a place."""
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def _accepts_(cls, value: Any) -> bool:
        """Returns whether this value can accept the given value."""
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def _accept_(cls, value: Any) -> Self:
        """Accepts the given value."""
        raise NotImplementedError

    @abstractmethod
    def _is_py_(self) -> bool:
        """Returns whether this value is a valid Python value."""
        raise NotImplementedError

    @abstractmethod
    def _as_py_(self) -> Any:
        """Returns the Python equivalent of this value.

        Will fail if _is_py_ returns false.
        """
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def _from_list_(cls, values: Iterable[float | BlockPlace]) -> Self:
        """Creates a value from a list of floats."""
        raise NotImplementedError

    @abstractmethod
    def _to_list_(self, level_refs: dict[Any, int] | None = None) -> list[float | BlockPlace]:
        """Converts this value to a list of floats."""
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def _flat_keys_(cls, prefix: str) -> list[str]:
        """Returns the keys to a flat representation of this value."""
        raise NotImplementedError

    def _to_flat_dict_(self, prefix: str, level_refs: dict[Any, int] | None = None) -> dict[str, float | BlockPlace]:
        """Converts this value to a flat dictionary."""
        return dict(zip(self._flat_keys_(prefix), self._to_list_(level_refs), strict=False))

    @abstractmethod
    def _get_(self) -> Self:
        """Implements access to the value.

        This is used when accessing a value from a record or array (and when storing into a record).
        For value (immutable) types, this makes a copy to preserve the appearance of immutability.
        For mutable types, this returns self.

        For instance:
        ```
        class X(Record):
            v: Num

        a = 1
        b = X(a) # (1) _get() is called on a
        c = b.v # (2) _get() is called on the value for v

        # (1) prevents this from changing the value of a
        # (2) prevents this from changing the value of c
        # Thus, both calls to _get() are necessary to ensure values behave immutably.
        b.v = 2
        ```
        """
        raise NotImplementedError

    @abstractmethod
    def _set_(self, value: Self):
        """Implements assignment (=).

        This is only supported by value types.
        This method must not change the active context like by branching.

        In some places, = might instead call assign_, such as when setting the value of a record field
        with a reference type.
        """
        raise NotImplementedError

    @abstractmethod
    def _copy_from_(self, value: Self):
        """Implements copy assignment (@=).

        This is only supported by mutable reference types.
        """
        raise NotImplementedError

    @abstractmethod
    def _copy_(self) -> Self:
        """Returns a deep copy of this value."""
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def _alloc_(cls) -> Self:
        """Allocates a new value which may be uninitialized."""
        raise NotImplementedError

    def __imatmul__(self, other):
        self._copy_from_(other)
        return self


Value.__imatmul__._meta_fn_ = True
