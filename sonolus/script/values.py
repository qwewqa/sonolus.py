from sonolus.script.internal.context import ctx
from sonolus.script.internal.generic import validate_concrete_type
from sonolus.script.internal.impl import self_impl, validate_value


@self_impl
def make[T](type_: type[T]) -> T:
    """Returns a new instance of the given type initialized with zeros."""
    type_ = validate_concrete_type(type_)
    if ctx():
        return type_._from_list_([0] * type_._size_())._get_()
    else:
        return type_._from_list_([0] * type_._size_())._as_py_()


@self_impl
def copy[T](value: T) -> T:
    """Returns a deep copy of the given value."""
    value = validate_value(value)
    if ctx():
        return value._copy_()
    else:
        return value._copy_()._as_py_()


@self_impl
def with_default[T](value: T, default: T) -> T:
    """Returns the given value if it's not None, otherwise the default value."""
    value = validate_value(value)
    default = validate_value(default)
    if value._is_py_() and value._as_py_() is None:
        return default
    return value
