from sonolus.script.internal.context import ctx
from sonolus.script.internal.generic import validate_concrete_type
from sonolus.script.internal.impl import meta_fn, validate_value
from sonolus.script.num import Num


@meta_fn
def alloc[T](type_: type[T]) -> T:
    """Return an uninitialized instance of the given type.

    Use this carefully as reading from uninitialized memory can lead to unexpected behavior.
    """
    type_ = validate_concrete_type(type_)
    if ctx():
        return type_._alloc_()
    else:
        return type_._alloc_()._as_py_()


@meta_fn
def zeros[T](type_: type[T]) -> T:
    """Make a new instance of the given type initialized with zeros.

    Generally works the same as the unary `+` operator on record and array types.
    """
    return validate_concrete_type(type_)._zero_()


@meta_fn
def copy[T](value: T) -> T:
    """Make a deep copy of the given value.

    Generally works the same as the unary `+` operator on records and arrays.
    """
    value = validate_value(value)
    if ctx():
        return value._copy_()
    else:
        return value._copy_()._as_py_()


def swap[T](a: T, b: T):
    """Swap the values of the two provided mutable values."""
    temp = copy(a)
    a @= b
    b @= temp


@meta_fn
def sizeof(type_: type, /) -> int:
    """Return the size of the given type."""
    type_ = validate_concrete_type(type_)
    if ctx():
        return Num(type_._size_())
    else:
        return type_._size_()
