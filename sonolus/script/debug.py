from collections.abc import Callable, Sequence
from contextvars import ContextVar
from typing import Any, Literal, Never, assert_never

from sonolus.backend.mode import Mode
from sonolus.backend.ops import Op
from sonolus.backend.optimize.flow import cfg_to_mermaid
from sonolus.backend.optimize.passes import CompilerPass, OptimizerConfig, run_passes
from sonolus.backend.optimize.simplify import RenumberVars
from sonolus.script.internal.context import ModeContextState, ProjectContextState, RuntimeChecks, ctx, set_ctx
from sonolus.script.internal.impl import meta_fn, validate_value
from sonolus.script.internal.native import native_function
from sonolus.script.internal.simulation_context import SimulationContext
from sonolus.script.num import Num

debug_log_callback = ContextVar[Callable[[Num], None]]("debug_log_callback")


@meta_fn
def error(message: str | None = None) -> Never:  # type: ignore
    """Raise an error, and if runtime checks are set to notify, log a message and pause the game.

    This function is used to raise an error during runtime and terminate the current callback.

    If runtime checks are set to notify (default in dev), this function will log a message and pause the game
    before terminating.

    Args:
        message: The message to log.
    """
    message = validate_value(message)._as_py_() or "Error"  # type: ignore
    if not isinstance(message, str):
        raise ValueError("Expected a string")
    if ctx():
        match ctx().project_state.runtime_checks:
            case RuntimeChecks.NOTIFY_AND_TERMINATE:
                debug_log(ctx().map_debug_message(message))
                debug_pause()
            case RuntimeChecks.TERMINATE | RuntimeChecks.NONE:
                pass
            case _ as unreachable:
                assert_never(unreachable)
        terminate()
    else:
        raise RuntimeError(message)


@meta_fn
def static_error(message: str | None = None) -> Never:
    """Raise a static error.

    This function is used to raise an error during compile-time if the compiler cannot guarantee that
    this function will not be called during runtime.

    Args:
        message: The message to log.
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


@meta_fn
def notify(message: str):
    """Log a code that can be decoded by the dev server and pause the game if runtime checks are set to notify.

    If runtime checks are not set to notify, this function will do nothing.

    Args:
        message: The message to log.
    """
    message = validate_value(message)._as_py_()  # type: ignore
    if not isinstance(message, str):
        raise ValueError("Expected a string")
    if ctx():
        if ctx().project_state.runtime_checks == RuntimeChecks.NOTIFY_AND_TERMINATE:
            debug_log(ctx().map_debug_message(message))
            debug_pause()
    else:
        print(f"[NOTIFY] {message}")


@meta_fn
def require(value: int | float | bool, message: str | None = None):
    """Require a condition to be true, or raise an error.

    Similar to assert, but does not get stripped in non-dev builds.

    If in a dev build, this function will log a message and pause the game if the condition is false.

    In non-dev builds, this function will terminate the current callback silently if the condition is false.

    Args:
        value: The condition to check.
        message: The message to log if the condition is false.
    """
    if not ctx():
        if not value:
            raise AssertionError(message if message is not None else "Assertion failed")
        return
    value = Num._accept_(validate_value(value))
    message = validate_value(message)
    message = message._as_py_() or "Assertion failed"
    if value._is_py_():
        if value._as_py_():
            return
        else:
            error(message)
    else:
        ctx().test = value.ir()
        t_branch = ctx().branch(None)
        f_branch = ctx().branch(0)
        set_ctx(f_branch)
        error(message)  # type: ignore
        set_ctx(t_branch)


@meta_fn
def assert_true(value: int | float | bool, message: str | None = None):
    if ctx() and ctx().project_state.runtime_checks == RuntimeChecks.NONE:
        return
    require(value, message)


def assert_false(value: int | float | bool, message: str | None = None):
    assert_true(not value, message)


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

    project_state = ProjectContextState()
    mode_state = ModeContextState(
        mode,
        {a: i for i, a in enumerate(archetypes)} if archetypes is not None else None,
    )

    cfg = callback_to_cfg(project_state, mode_state, fn, callback, archetype=archetype)  # type: ignore
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
