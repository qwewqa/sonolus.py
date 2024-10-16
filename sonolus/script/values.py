from sonolus.script.internal.context import ctx
from sonolus.script.internal.generic import validate_concrete_type
from sonolus.script.internal.impl import self_impl, validate_value


@self_impl
def alloc[T](type_: type[T]) -> T:
    """Return a new instance of the given type initialized with arbitrary values."""
    type_ = validate_concrete_type(type_)
    if ctx():
        return type_.from_place_(ctx().alloc(size=type_.size_()))
    else:
        return type_.from_list_([-1] * type_.size_()).as_py_()


@self_impl
def copy[T](value: T) -> T:
    value = validate_value(value)
    if ctx():
        return value.copy_()
    else:
        return value.copy_().as_py_()
