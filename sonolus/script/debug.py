from collections.abc import Callable, Sequence
from contextvars import ContextVar
from typing import Any, Literal, Never

from sonolus.backend.mode import Mode
from sonolus.backend.ops import Op
from sonolus.backend.optimize.flow import cfg_to_mermaid
from sonolus.backend.optimize.passes import CompilerPass, OptimizerConfig, run_passes
from sonolus.backend.optimize.simplify import RenumberVars
from sonolus.script.internal.context import GlobalContextState, ReadOnlyMemory, ctx, set_ctx
from sonolus.script.internal.impl import meta_fn, validate_value
from sonolus.script.internal.native import native_function
from sonolus.script.internal.simulation_context import SimulationContext
from sonolus.script.num import Num

debug_log_callback = ContextVar[Callable[[Num], None]]("debug_log_callback")


@meta_fn
def error(message: str | None = None) -> Never:  # type: ignore
    """Raise an error.

    This function is used to raise an error during runtime.
    When this happens, the game will pause in debug mode. The current callback will also immediately return 0.
    """
    message = validate_value(message)._as_py_() or "Error"  # type: ignore
    if not isinstance(message, str):
        raise ValueError("Expected a string")
    if ctx():
        debug_log(ctx().map_constant(message))
        debug_pause()
        terminate()
    else:
        raise RuntimeError(message)


@meta_fn
def static_error(message: str | None = None) -> Never:
    """Raise a static error.

    This function is used to raise an error during compile-time if the compiler cannot guarantee that
    this function will not be called during runtime.
    """
    message = validate_value(message)._as_py_() or "Error"  # type: ignore
    if not isinstance(message, str):
        raise ValueError("Expected a string")
    raise RuntimeError(message)


@meta_fn
def debug_log(value: int | float | bool):
    """Log a value in debug mode."""
    if debug_log_callback.get(None):
        return debug_log_callback.get()(value)  # type: ignore
    else:
        return _debug_log(value)


@native_function(Op.DebugLog)
def _debug_log(value: int | float | bool):
    print(f"[DEBUG] {value}")
    return 0


@native_function(Op.DebugPause)
def debug_pause():
    """Pause the game if in debug mode."""
    input("[DEBUG] Paused")


def assert_true(value: int | float | bool, message: str | None = None):
    message = message if message is not None else "Assertion failed"
    if not value:
        error(message)


def assert_false(value: int | float | bool, message: str | None = None):
    message = message if message is not None else "Assertion failed"
    if value:
        error(message)


def static_assert(value: int | float | bool, message: str | None = None):
    message = message if message is not None else "Static assertion failed"
    if not _is_static_true(value):
        static_error(message)


def try_static_assert(value: int | float | bool, message: str | None = None):
    message = message if message is not None else "Static assertion failed"
    if _is_static_false(value):
        static_error(message)
    if not value:
        error(message)


@meta_fn
def assert_unreachable(message: str | None = None) -> Never:
    # This works a bit differently from assert_never from typing in that it throws an error if the Sonolus.py
    # compiler cannot guarantee that this function will not be called, which is different from what type checkers
    # may be able to infer.
    message = validate_value(message)._as_py_() or "Unreachable code reached"  # type: ignore
    raise RuntimeError(message)


@meta_fn
def terminate():
    if ctx():
        set_ctx(ctx().into_dead())
    else:
        raise RuntimeError("Terminated")


def visualize_cfg(
    fn: Callable[[], Any] | Callable[[type], Any],
    /,
    *,
    mode: Mode = Mode.PLAY,
    callback: str = "",
    archetype: type | None = None,
    archetypes: list[type] | None = None,
    passes: Sequence[CompilerPass] | Literal["minimal", "fast", "standard"] = "fast",
) -> str:
    from sonolus.backend.optimize.optimize import FAST_PASSES, MINIMAL_PASSES, STANDARD_PASSES
    from sonolus.build.compile import callback_to_cfg

    match passes:
        case "minimal":
            passes = [
                *MINIMAL_PASSES[:-1],
                RenumberVars(),
            ]
        case "fast":
            passes = [
                *FAST_PASSES[:-1],
                RenumberVars(),
            ]
        case "standard":
            passes = [
                *STANDARD_PASSES[:-1],
                RenumberVars(),
            ]

    global_state = GlobalContextState(
        mode,
        {a: i for i, a in enumerate(archetypes)} if archetypes is not None else None,
        ReadOnlyMemory(),
    )

    cfg = callback_to_cfg(global_state, fn, callback, archetype=archetype)  # type: ignore
    cfg = run_passes(cfg, passes, OptimizerConfig(mode=mode))
    return cfg_to_mermaid(cfg)


def simulation_context() -> SimulationContext:
    return SimulationContext()


@meta_fn
def _is_static_true(value: int | float | bool) -> bool:
    if ctx() is None:
        return bool(value)
    else:
        value = validate_value(value)
        return value._is_py_() and value._as_py_()


@meta_fn
def _is_static_false(value: int | float | bool) -> bool:
    if ctx() is None:
        return not bool(value)
    else:
        value = validate_value(value)
        return value._is_py_() and not value._as_py_()
