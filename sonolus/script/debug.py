from scripts.out.ops import Op
from sonolus.script.comptime import Comptime
from sonolus.script.internal.context import ctx, set_ctx
from sonolus.script.internal.impl import self_impl
from sonolus.script.internal.native import native_function
from sonolus.script.num import Num
from sonolus.script.values import with_default


@self_impl
def error(message: str) -> None:
    message = Comptime._accept_(message)._as_py_()
    if not isinstance(message, str):
        raise ValueError("Expected a string")
    if ctx():
        debug_log(ctx().map_constant(message))
        debug_pause()
    else:
        raise RuntimeError(message)


@native_function(Op.DebugLog)
def debug_log(value: Num):
    print(f"[DEBUG] {value}")


@native_function(Op.DebugPause)
def debug_pause():
    input("[DEBUG] Paused")


def assert_true(value: Num, message: str | None = None):
    message = with_default(message, "Assertion failed")
    if not value:
        error(message)
        terminate()


@self_impl
def terminate():
    if ctx():
        set_ctx(ctx().into_dead())
    else:
        raise RuntimeError("Terminated")
