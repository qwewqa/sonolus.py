from collections.abc import Callable, Sequence
from contextvars import ContextVar
from typing import Any, Never

from sonolus.backend.mode import Mode
from sonolus.backend.ops import Op
from sonolus.backend.optimize.flow import cfg_to_mermaid
from sonolus.backend.optimize.passes import CompilerPass, run_passes
from sonolus.backend.optimize.simplify import CoalesceFlow
from sonolus.script.internal.context import GlobalContextState, ctx, set_ctx
from sonolus.script.internal.impl import meta_fn, validate_value
from sonolus.script.internal.native import native_function
from sonolus.script.num import Num

debug_log_callback = ContextVar[Callable[[Num], None]]("debug_log_callback")


@meta_fn
def error(message: str | None = None) -> None:
    message = message._as_py_() if message is not None else "Error"
    if not isinstance(message, str):
        raise ValueError("Expected a string")
    if ctx():
        debug_log(ctx().map_constant(message))
        debug_pause()
        terminate()
    else:
        raise RuntimeError(message)


@meta_fn
def debug_log(value: Num):
    """Log a value in debug mode."""
    if debug_log_callback.get(None):
        return debug_log_callback.get()(value)
    else:
        return _debug_log(value)


@native_function(Op.DebugLog)
def _debug_log(value: Num):
    print(f"[DEBUG] {value}")
    return 0


@native_function(Op.DebugPause)
def debug_pause():
    """Pause the game if in debug mode."""
    input("[DEBUG] Paused")


def assert_true(value: Num, message: str | None = None):
    message = message if message is not None else "Assertion failed"
    if not value:
        error(message)


def assert_false(value: Num, message: str | None = None):
    message = message if message is not None else "Assertion failed"
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


def visualize_cfg(fn: Callable[[], Any], passes: Sequence[CompilerPass] | None = None) -> str:
    from sonolus.build.compile import callback_to_cfg

    if passes is None:
        passes = [CoalesceFlow()]

    cfg = callback_to_cfg(GlobalContextState(Mode.PLAY), fn, "")
    cfg = run_passes(cfg, passes)
    return cfg_to_mermaid(cfg)
