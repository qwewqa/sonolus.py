from collections.abc import Callable
from datetime import timedelta

from hypothesis import settings

from sonolus.backend.blocks import PlayBlock
from sonolus.backend.finalize import cfg_to_engine_node
from sonolus.backend.interpret import Interpreter
from sonolus.backend.mode import Mode
from sonolus.backend.optimize.optimize import optimize_and_allocate
from sonolus.backend.place import BlockPlace
from sonolus.backend.visitor import compile_and_call
from sonolus.build.compile import callback_to_cfg
from sonolus.script.debug import debug_log_callback
from sonolus.script.internal.context import GlobalContextState
from sonolus.script.internal.error import CompilationError
from sonolus.script.internal.impl import meta_fn
from sonolus.script.num import Num
from sonolus.script.vec import Vec2

settings.register_profile(
    "standard",
    deadline=timedelta(milliseconds=1000),
)
settings.load_profile("standard")


def compile_fn(callback: Callable):
    global_state = GlobalContextState(Mode.PLAY)
    return callback_to_cfg(global_state, callback, ""), global_state.rom.values


def validate_dual_run[**P, R](fn: Callable[P, R], *args: P.args, **kwargs: P.kwargs) -> R:
    """Runs a function as a regular function and as a compiled function, and checks that the results are the same."""
    exception = None
    regular_result = None
    log_entries = []

    def log_cb(x):
        log_entries.append(x)
        return 0

    debug_log_callback_token = debug_log_callback.set(log_cb)
    try:
        regular_result = fn(*args, **kwargs)
    except Exception as e:
        exception = e
    finally:
        debug_log_callback.reset(debug_log_callback_token)

    result_type = None

    @meta_fn
    def run_compiled():
        nonlocal result_type
        result = compile_and_call(fn, *args, **kwargs)
        # If terminated, this line won't run
        # We can check whether this value is 1 to see if the compiled function finished without terminating
        Num._from_place_(BlockPlace(-1, 0))._set_(Num(1))
        result_type = type(result)
        target = result_type._from_place_(BlockPlace(-2, 0))
        if result_type._is_value_type_():
            target._set_(result)
        else:
            target._copy_from_(result)
        return result

    try:
        cfg, rom_values = compile_fn(run_compiled)
    except CompilationError as e:
        assert exception is not None
        while isinstance(e, CompilationError) and e.__cause__ is not None:
            e = e.__cause__
        assert str(e) == str(exception)  # noqa: PT017
        assert type(e) is type(exception)  # noqa: PT017
        raise exception from None

    cfg = optimize_and_allocate(cfg)
    entry = cfg_to_engine_node(cfg)
    interpreter = Interpreter()
    interpreter.blocks[PlayBlock.EngineRom] = rom_values

    num_result = interpreter.run(entry)
    if exception is None:
        if result_type == Num:
            assert num_result == regular_result
        else:
            assert num_result == 0
    compiled_result = result_type._from_list_([interpreter.get(-2, i) for i in range(result_type._size_())])._as_py_()
    compiled_terminated = interpreter.get(-1, 0) != 1

    if exception is not None:
        assert compiled_terminated, "Compiled function should terminate if regular function raises exception"
        raise exception

    assert compiled_result == regular_result
    assert interpreter.log == log_entries

    return regular_result


def compiled_run[**P](fn: Callable[P, Num], *args: P.args, **kwargs: P.kwargs) -> Num:
    """Runs a function as a compiled function and returns the result."""

    @meta_fn
    def run_compiled():
        return compile_and_call(fn, *args, **kwargs)

    cfg, rom_values = compile_fn(run_compiled)
    cfg = optimize_and_allocate(cfg)
    entry = cfg_to_engine_node(cfg)
    interpreter = Interpreter()
    interpreter.blocks[PlayBlock.EngineRom] = rom_values
    return interpreter.run(entry)


def implies(a: bool, b: bool) -> bool:
    if a:
        return b
    return True


def is_close(a: float | Vec2, b: float | Vec2, rel_tol: float = 1e-8, abs_tol: float = 1e-8) -> bool:
    if isinstance(a, Vec2):
        return is_close(a.x, b.x, rel_tol, abs_tol) and is_close(a.y, b.y, rel_tol, abs_tol)
    return abs(a - b) <= max(rel_tol * max(abs(a), abs(b)), abs_tol)
