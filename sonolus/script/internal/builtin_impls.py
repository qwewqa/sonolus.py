from sonolus.script.internal.impl import self_impl, validate_value
from sonolus.script.iterator import ArrayLike, Enumerator


@self_impl
def _isinstance(value, type_):
    value = validate_value(value)
    type_ = validate_value(type_)._as_py_()
    return validate_value(isinstance(value, type_))


@self_impl
def _len(value):
    from sonolus.backend.visitor import compile_and_call

    value = validate_value(value)
    if not hasattr(value, "__len__"):
        raise TypeError(f"object of type '{type(value).__name__}' has no len()")
    return compile_and_call(value.__len__)


@self_impl
def _enumerate(iterable, start=0):
    from sonolus.backend.visitor import compile_and_call

    iterable = validate_value(iterable)
    if not hasattr(iterable, "__iter__"):
        raise TypeError(f"'{type(iterable).__name__}' object is not iterable")
    if isinstance(iterable, ArrayLike):
        return compile_and_call(iterable.enumerate, start)
    else:
        return Enumerator(0, start, compile_and_call(iterable.__iter__))


@self_impl
def _abs(value):
    from sonolus.backend.visitor import compile_and_call

    value = validate_value(value)
    if not hasattr(value, "__abs__"):
        raise TypeError(f"bad operand type for abs(): '{type(value).__name__}'")
    return compile_and_call(value.__abs__)


BUILTIN_IMPLS = {
    id(isinstance): _isinstance,
}
