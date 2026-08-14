"""PYTEST_DONT_REWRITE"""  # ruff: ignore[missing-terminal-punctuation]

import itertools
import os
import random
import sys
from collections.abc import Callable
from datetime import timedelta
from types import CellType

from hypothesis import settings

from sonolus.backend.blocks import PlayBlock
from sonolus.backend.interpret import Interpreter
from sonolus.backend.mode import Mode
from sonolus.backend.optimize import (
    FAST_PASSES,
    MINIMAL_PASSES,
    STANDARD_PASSES,
    OptimizerConfig,
    cfg_to_engine_node,
    run_passes,
)
from sonolus.backend.optimize.flow import BasicBlock
from sonolus.backend.place import BlockPlace
from sonolus.build.compile import callback_to_cfg
from sonolus.script.debug import debug_log_callback, simulation_context
from sonolus.script.internal.context import ModeContextState, ProjectContextState, RuntimeChecks, ctx
from sonolus.script.internal.dict_impl import DictImpl
from sonolus.script.internal.error import CompilationError
from sonolus.script.internal.impl import validate_value
from sonolus.script.internal.meta_fn import meta_fn
from sonolus.script.internal.set_impl import SetImpl
from sonolus.script.internal.tuple_impl import TupleImpl
from sonolus.script.internal.visitor import clear_frontend_caches, compile_and_call
from sonolus.script.num import Num
from sonolus.script.vec import Vec2

PRIMARY_PYTHON_VERSION = (3, 14)


def is_ci() -> bool:
    return os.getenv("CI", "false").lower() in {"true", "1"}


settings.register_profile(
    "default",
    settings.get_profile("default"),
    max_examples=100,
    deadline=timedelta(seconds=10),
)
settings.register_profile(
    "ci",
    settings.get_profile("ci"),
    max_examples=40,
    deadline=timedelta(seconds=10),
)
settings.load_profile("ci" if is_ci() else "default")

optimization_levels = [
    MINIMAL_PASSES,
    FAST_PASSES,
    STANDARD_PASSES,
]

if is_ci() and sys.version_info < PRIMARY_PYTHON_VERSION:
    optimization_levels = [STANDARD_PASSES]


def compile_fn(
    callback: Callable, runtime_checks: RuntimeChecks = RuntimeChecks.NONE
) -> tuple[BasicBlock, list[float]]:
    clear_frontend_caches()
    project_state = ProjectContextState(runtime_checks=runtime_checks)
    mode_state = ModeContextState(Mode.PLAY)
    return callback_to_cfg(project_state, mode_state, callback, ""), project_state.rom.values


def run_and_validate[**P, R](
    fn: Callable[P, R], *args: P.args, use_simulation_context: bool = False, **kwargs: P.kwargs
) -> R:
    """Runs a function as a regular function and as a compiled function, and checks that the results are the same.

    One compiled leg rewrites every closure cell into engine ROM, which holds 32-bit floats, so closure values
    must stay within that range.
    """
    exception = None
    regular_result = None
    log_entries = []

    if getattr(fn, "_meta_fn_", False) and hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
        fn._meta_fn_ = True

    def log_cb(x):
        log_entries.append(x)
        return 0

    debug_log_callback_token = debug_log_callback.set(log_cb)
    try:
        if use_simulation_context:
            with simulation_context():
                regular_result = fn(*args, **kwargs)
        else:
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

    @meta_fn
    def run_compiled_with_closure_from_rom():
        nonlocal result_type
        closure: list[CellType] | None = getattr(fn, "__closure__", None)
        if closure:
            original_values = [cell.cell_contents for cell in closure]

            def value_to_rom(v):
                value = validate_value(v)
                if isinstance(value, TupleImpl):
                    return TupleImpl(tuple(value_to_rom(entry) for entry in value.value))
                elif isinstance(value, SetImpl):
                    return SetImpl(
                        DictImpl.from_items(
                            tuple((value_to_rom(key), None) for key, _ in value._dict._items_with_py_keys())
                        )
                    )
                elif isinstance(value, DictImpl):
                    return DictImpl.from_items(
                        tuple((value_to_rom(key), value_to_rom(item)) for key, item in value._items_with_py_keys())
                    )
                else:
                    return type(value)._from_place_(ctx().rom[tuple(value._to_list_())])

            try:
                for name, cell, original_value in zip(fn.__code__.co_freevars, closure, original_values, strict=True):
                    try:
                        cell.cell_contents = value_to_rom(original_value)
                    except ValueError as e:
                        # Attribute this to the harness: the ROM error is raised from host code with no source
                        # location, so it reads as if the code under test produced it. Re-raising it as a
                        # CompilationError instead would reach the legs below, which would compare it against
                        # the exception the test itself expects.
                        raise RuntimeError(
                            f"run_and_validate cannot intern closure variable {name!r} "
                            f"(value {original_value!r}) into engine ROM: {e}"
                        ) from e
                result = compile_and_call(fn, *args, **kwargs)
            finally:
                for cell, original_value in zip(closure, original_values, strict=True):
                    cell.cell_contents = original_value
        else:
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

    # Check that it compiles with runtime checks set to None. Exception behavior can differ though, so we don't
    # bother actually running with runtime checks fully disabled.
    for read_closure_from_rom in (False, True):
        try:
            compile_fn(
                run_compiled_with_closure_from_rom if read_closure_from_rom else run_compiled,
                runtime_checks=RuntimeChecks.NONE,
            )
        except CompilationError as e:
            if exception is None:
                raise
            while isinstance(e, CompilationError) and e.__cause__ is not None:
                e = e.__cause__
            assert str(e) == str(exception)  # ruff: ignore[pytest-assert-in-except]
            assert type(e) is type(exception)  # ruff: ignore[pytest-assert-in-except]
            raise exception from None

    # The traced CFG depends on the callback and runtime_checks but not on the optimization level, so trace
    # once per closure variant and share the CFG across the level loop: run_passes is non-destructive on its
    # input. result_type is set as a tracing side effect, so it is recorded per trace and restored per run.
    traced = {}
    for read_closure_from_rom in (False, True):
        try:
            cfg, rom_values = compile_fn(
                run_compiled_with_closure_from_rom if read_closure_from_rom else run_compiled,
                runtime_checks=RuntimeChecks.TERMINATE,
            )
        except CompilationError as e:
            if exception is None:
                raise
            while isinstance(e, CompilationError) and e.__cause__ is not None:
                e = e.__cause__
            assert str(e) == str(exception)  # ruff: ignore[pytest-assert-in-except]
            assert type(e) is type(exception)  # ruff: ignore[pytest-assert-in-except]
            raise exception from None
        traced[read_closure_from_rom] = (cfg, rom_values, result_type)

    for read_closure_from_rom, passes in itertools.product((False, True), optimization_levels):
        cfg, rom_values, result_type = traced[read_closure_from_rom]
        cfg = run_passes(cfg, passes, OptimizerConfig())
        entry = cfg_to_engine_node(cfg)
        interpreter = Interpreter()
        # A fresh copy per run: the interpreter writes into the block lists it is handed.
        interpreter.blocks[PlayBlock.EngineRom] = list(rom_values)

        num_result = interpreter.run(entry)
        if exception is None:
            if result_type == Num:
                assert num_result == regular_result
            else:
                assert num_result == 0
        if result_type is None:
            # Every traced path of the call terminated, so the tracing side effect that records the result
            # type never ran and nothing was stored in the result slot. The termination flag below is what
            # these runs check.
            compiled_result = None
        else:
            compiled_result = result_type._from_list_(
                [interpreter.get(-2, i) for i in range(result_type._size_())]
            )._as_py_()
        compiled_terminated = interpreter.get(-1, 0) != 1

        if exception is not None:
            assert compiled_terminated, "Compiled function should terminate if regular function raises exception"
            assert interpreter.log == log_entries
            continue

        assert compiled_result == regular_result
        assert interpreter.log == log_entries

    if exception is not None:
        raise exception

    return regular_result


def run_compiled[**P](
    fn: Callable[P, Num],
    *args: P.args,
    runtime_checks: RuntimeChecks | None = None,
    log_callback: Callable[[float], None] | None = None,
    **kwargs: P.kwargs,
) -> Num:
    """Runs a function as a compiled function and returns the result."""
    if log_callback is None:
        log_callback = lambda x: None  # ruff: ignore[lambda-assignment]

    @meta_fn
    def wrapper():
        return compile_and_call(fn, *args, **kwargs)

    runtime_checks_values = {
        RuntimeChecks.NONE: (RuntimeChecks.NONE,),
        RuntimeChecks.TERMINATE: (RuntimeChecks.TERMINATE,),
        RuntimeChecks.NOTIFY_AND_TERMINATE: (RuntimeChecks.NOTIFY_AND_TERMINATE,),
        None: (RuntimeChecks.NONE, RuntimeChecks.TERMINATE, RuntimeChecks.NOTIFY_AND_TERMINATE),
    }[runtime_checks]

    results = []
    logs = []
    initial_random_state = random.getstate()

    # One trace per runtime_checks value rather than one per (level, runtime_checks): the traced CFG does not
    # depend on the optimization level, and run_passes is non-destructive on its input.
    traced = {}
    for runtime_checks_value in runtime_checks_values:
        random.setstate(initial_random_state)
        traced[runtime_checks_value] = compile_fn(wrapper, runtime_checks=runtime_checks_value)

    for passes in optimization_levels:
        for runtime_checks_value in runtime_checks_values:
            cfg, rom_values = traced[runtime_checks_value]
            cfg = run_passes(cfg, passes, OptimizerConfig())
            entry = cfg_to_engine_node(cfg)
            # The state reset is what makes every interpreter run draw the same stream from the Random and
            # RandomInteger ops, so it belongs before each run now that the traces are hoisted.
            random.setstate(initial_random_state)
            interpreter = Interpreter()
            # A fresh copy per run: the interpreter writes into the block lists it is handed.
            interpreter.blocks[PlayBlock.EngineRom] = list(rom_values)
            result = interpreter.run(entry)
            results.append(result)
            logs.append(interpreter.log.copy())

    if logs and not all(log == logs[0] for log in logs):
        raise ValueError(f"Logs differ between iterations: {logs}")

    if logs:
        for entry in logs[0]:
            log_callback(entry)

    if len(set(results)) != 1:
        raise ValueError(f"Compiled results differ between optimization levels: {results}")

    return results[0]


def implies(a: bool, b: bool) -> bool:
    if a:
        return b
    return True


def is_close(a: float | Vec2, b: float | Vec2, rel_tol: float = 1e-8, abs_tol: float = 1e-8) -> bool:
    if isinstance(a, Vec2):
        return is_close(a.x, b.x, rel_tol, abs_tol) and is_close(a.y, b.y, rel_tol, abs_tol)
    return abs(a - b) <= max(rel_tol * max(abs(a), abs(b)), abs_tol)
