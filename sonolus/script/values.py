from sonolus.script.internal.context import ctx
from sonolus.script.internal.generic import validate_concrete_type
from sonolus.script.internal.impl import meta_fn, validate_value


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
    """Make a new instance of the given type initialized with zeros."""
    type_ = validate_concrete_type(type_)
    if ctx():
        return copy(type_._from_list_([0] * type_._size_()))
    else:
        return type_._from_list_([0] * type_._size_())._as_py_()


@meta_fn
def copy[T](value: T) -> T:
    """Make a deep copy of the given value."""
    value = validate_value(value)
    if ctx():
        return value._copy_()
    else:
        return value._copy_()._as_py_()
