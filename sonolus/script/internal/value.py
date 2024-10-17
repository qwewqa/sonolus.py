from abc import abstractmethod
from collections.abc import Iterable
from typing import Any, Self

from sonolus.backend.place import BlockPlace


class Value:
    """Base class for values."""

    @classmethod
    def is_concrete_(cls) -> bool:
        """Returns whether this type is concrete (i.e. can be instantiated)."""
        return False

    @classmethod
    @abstractmethod
    def size_(cls) -> int:
        """Returns the size of this value."""
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def is_value_type_(cls) -> bool:
        """Returns whether this is a value type.

        If this is true, the value behaves immutably and set_ is supported.
        """
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def from_place_(cls, place: BlockPlace) -> Self:
        """Creates a value from a place."""
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def accepts_(cls, value: Any) -> bool:
        """Returns whether this value can accept the given value."""
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def accept_(cls, value: Any) -> Self:
        """Accepts the given value."""
        raise NotImplementedError

    @abstractmethod
    def is_py_(self) -> bool:
        """Returns whether this value is a valid Python value.

        Essentially, this returns true if to_cells_ returns a list of integers (or an empty list).
        """
        raise NotImplementedError

    @abstractmethod
    def as_py_(self) -> Any:
        """Returns the Python equivalent of this value.

        Will fail if is_py_ returns false.
        """
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def from_list_(cls, values: Iterable[float]) -> Self:
        """Creates a value from a list of floats."""
        raise NotImplementedError

    @abstractmethod
    def to_list_(self) -> list[float]:
        """Converts this value to a list of floats."""
        raise NotImplementedError

    @abstractmethod
    def get_(self) -> Self:
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
    def set_(self, value: Self):
        """Implements assignment (=).

        This is only supported by value types.
        This method must not change the active context like by branching.

        In some places, = might instead call assign_, such as when setting the value of a record field
        with a reference type.
        """
        raise NotImplementedError

    @abstractmethod
    def copy_from_(self, value: Self):
        """Implements copy assignment (@=).

        This is only supported by mutable reference types.
        """
        raise NotImplementedError

    @abstractmethod
    def copy_(self) -> Self:
        """Returns a deep copy of this value."""
        raise NotImplementedError

    def __imatmul__(self, other):
        return self.copy_from_(other)

    # NOTE: Aug assign needs special treatment since normally something like
    # a[x] += y or a.x += y would be equivalent to a[x] = a[x] + y or a.x = a.x + y
    # But for reference types, we don't support set_
    # Instead, we need to do r = (q := a[x]).__iadd__(y) or r = (q := a.x).__iadd__(y)
    # Then check that r is q and error if not
    # Naturally this also means that reference types don't automatically support in-place operations
