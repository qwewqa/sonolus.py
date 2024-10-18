from scripts.out.ops import Op
from sonolus.script.comptime import Comptime
from sonolus.script.internal.impl import self_impl
from sonolus.script.internal.native import native_function
from sonolus.script.num import Num


@self_impl
def error(message: str) -> None:
    message = Comptime._accept_(message)._as_py_()
    if not isinstance(message, str):
        raise ValueError("Expected a string")
    # TODO


@native_function(Op.DebugLog)
def debug_log(value: Num):
    print(f"[DEBUG] {value}")
