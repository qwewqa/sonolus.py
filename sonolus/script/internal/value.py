from abc import abstractmethod
from collections.abc import Callable, Iterable
from types import NotImplementedType
from typing import Any, Self

from sonolus.backend.ir import IRConst, IRExpr, IRStmt
from sonolus.backend.place import BlockPlace


class BackingValue:
    def read(self) -> IRExpr:
        raise NotImplementedError()

    def write(self, value: IRExpr) -> IRStmt:
        raise NotImplementedError()


class ExprBackingValue(BackingValue):
    """A backing value that is backed by an expression."""

    def __init__(self, expr: IRExpr):
        self._expr = expr

    def read(self) -> IRExpr:
        return self._expr

    def write(self, value: IRExpr) -> IRStmt:
        raise RuntimeError("Value is read-only, cannot write to it")


type DataValue = BlockPlace | BackingValue | float | int | bool
type BackingSource = Callable[[IRExpr], BackingValue]


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
    def _from_backing_source_(cls, source: BackingSource) -> Self:
        """Creates a value from a backing source."""
        return cls._from_list_(source(IRConst(i)) for i in range(cls._size_()))

    @classmethod
    @abstractmethod
    def _from_list_(cls, values: Iterable[DataValue]) -> Self:
        """Creates a value from a list of data values."""
        raise NotImplementedError

    @abstractmethod
    def _to_list_(self, level_refs: dict[Any, str] | None = None) -> list[DataValue | str]:
        """Converts this value to a list of data values."""
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def _flat_keys_(cls, prefix: str) -> list[str]:
        """Returns the keys to a flat representation of this value."""
        raise NotImplementedError

    def _to_flat_dict_(self, prefix: str, level_refs: dict[Any, str] | None = None) -> dict[str, DataValue | str]:
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
            v: int

        a = 1
        b = X(a) # (1) _get_() is called on a
        c = b.v # (2) _get_() is called on the value for v

        # (1) prevents this from changing the value of a
        # (2) prevents this from changing the value of c
        # Thus, both calls to _get_() are necessary to ensure values behave immutably.
        b.v = 2
        ```
        """
        raise NotImplementedError

    def _get_readonly_(self) -> Self:
        """Implements access to the value without copying if the underlying value is immutable.

        The returned value should not be intentionally modified, but it is not guaranteed to be immutable.

        For example, a Num might be backed internally by rom, which is immutable. If we aren't going to modify
        (e.g. by putting it into a record where it can be modified), we can just return the original value, which
        avoids unnecessary copying.
        """
        return self._get_()

    @abstractmethod
    def _set_(self, value: Any):
        """Implements assignment (=).

        This is only supported by value types.
        This method must not change the active context like by branching.

        In some places, = might instead call assign_, such as when setting the value of a record field
        with a reference type.
        """
        raise NotImplementedError

    @abstractmethod
    def _copy_from_(self, value: Any):
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

    @classmethod
    @abstractmethod
    def _zero_(cls) -> Self:
        """Returns a zero-initialized value of this type."""
        raise NotImplementedError

    @classmethod
    def _get_merge_target_(cls, values: list[Any]) -> Any | NotImplementedType:
        """Return the target when merging values from multiple code paths.

        E.g. for code like this:
        ```
        if cond:
            x = 1
        else:
            x = 2
        do_something(x)
        ```
        This is called to create a target value for x after the if-else block,
        and at the end of each block, that target value is assigned to the respective value (1 or 2).
        This lets us keep the value of x as a constant within if and else branches, and only have it
        become a runtime value after the if-else block.

        This is an overrideable method to allow for some other special behavior, namely the Maybe type.
        """
        if cls._is_value_type_():
            from sonolus.script.internal.context import ctx

            return cls._from_place_(ctx().alloc(size=cls._size_()))
        else:
            return NotImplemented

    def __imatmul__(self, other):
        self._copy_from_(other)
        return self


Value.__imatmul__._meta_fn_ = True  # type: ignore
