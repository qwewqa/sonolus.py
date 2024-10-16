from sonolus.script.comptime import Comptime
from sonolus.script.internal.impl import self_impl


@self_impl
def error(message: str) -> None:
    message = Comptime.accept_(message).as_py_()
    if not isinstance(message, str):
        raise ValueError("Expected a string")
    # TODO
