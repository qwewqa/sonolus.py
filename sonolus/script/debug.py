from collections.abc import Callable
from typing import Any, Never

from sonolus.backend.flow import cfg_to_mermaid
from sonolus.backend.mode import Mode
from sonolus.backend.ops import Op
from sonolus.backend.simplify import CoalesceFlow
from sonolus.script.comptime import Comptime
from sonolus.script.internal.context import GlobalContextState, ctx, set_ctx
from sonolus.script.internal.impl import meta_fn, validate_value
from sonolus.script.internal.native import native_function
from sonolus.script.num import Num
from sonolus.script.values import with_default


@meta_fn
def error(message: str | None = None) -> None:
    message = Comptime._accept_(message)._as_py_() if message is not None else "Error"
    if not isinstance(message, str):
        raise ValueError("Expected a string")
    if ctx():
        debug_log(ctx().map_constant(message))
        debug_pause()
        terminate()
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


def assert_false(value: Num, message: str | None = None):
    message = with_default(message, "Assertion failed")
    if value:
        error(message)


@meta_fn
def assert_unreachable(message: str | None = None) -> Never:
    message = validate_value(message)._as_py_() or "Unreachable code reached"
    raise RuntimeError(message)


@meta_fn
def terminate():
    if ctx():
        set_ctx(ctx().into_dead())
    else:
        raise RuntimeError("Terminated")


def visualize_cfg(fn: Callable[[], Any]) -> str:
    from sonolus.build.compile import callback_to_cfg

    cfg = callback_to_cfg(GlobalContextState(Mode.Play), fn, "")
    cfg = CoalesceFlow().run(cfg)
    return cfg_to_mermaid(cfg)
