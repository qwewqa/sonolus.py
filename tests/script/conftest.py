"""PYTEST_DONT_REWRITE"""  # noqa: D415

import contextlib
import itertools
import os
import random
import sys
from collections.abc import Callable
from datetime import timedelta
from types import CellType

from hypothesis import settings

from sonolus.backend.blocks import PlayBlock
from sonolus.backend.finalize import cfg_to_engine_node
from sonolus.backend.interpret import Interpreter
from sonolus.backend.mode import Mode
from sonolus.backend.optimize.flow import BasicBlock
from sonolus.backend.optimize.optimize import FAST_PASSES, MINIMAL_PASSES, STANDARD_PASSES
from sonolus.backend.optimize.passes import OptimizerConfig, run_passes
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
from sonolus.script.internal.visitor import compile_and_call
from sonolus.script.num import Num
from sonolus.script.vec import Vec2

PRIMARY_PYTHON_VERSION = (3, 14)

# Corpus capture (PORT.md T0.5): only active when SONOLUS_CAPTURE_CORPUS is set;
# default runs keep _CAPTURE = None and behave exactly as before.
_CAPTURE = None
if os.environ.get("SONOLUS_CAPTURE_CORPUS"):
    from tests.corpus_capture import get_capture

    _CAPTURE = get_capture()

# Rust backend lane (PORT.md T1.4): when SONOLUS_BACKEND=rust, run_and_validate and
# run_compiled keep the (unchanged, Python) frontend trace but route compilation
# through encode_cfg -> sonolus_backend.run_pipeline and execute the result on the
# Rust interpreter. The default lane (env unset) is behaviorally untouched.
_BACKEND = os.environ.get("SONOLUS_BACKEND", "")
if _BACKEND not in {"", "python", "rust"}:
    raise RuntimeError(f"Unsupported SONOLUS_BACKEND value {_BACKEND!r}; expected 'rust', 'python', or unset")
RUST_BACKEND_LANE = _BACKEND == "rust"
sonolus_backend = None
if RUST_BACKEND_LANE:
    if _CAPTURE is not None:
        raise RuntimeError(
            "SONOLUS_CAPTURE_CORPUS and SONOLUS_BACKEND=rust are mutually exclusive: "
            "corpus capture instruments the legacy Python backend pipeline, which the rust lane bypasses"
        )
    try:
        import sonolus_backend
    except ImportError as e:
        raise RuntimeError(
            "SONOLUS_BACKEND=rust requires the sonolus_backend extension module; "
            "build it with: uv run maturin develop -m rust/sonolus-backend-py/Cargo.toml"
        ) from e
    from sonolus.backend.encode import encode_cfg

# Optimization levels implemented by the Rust pipeline so far, in pipeline-prefix
# order (the Rust level names accepted by sonolus_backend.run_pipeline). PORT.md
# S2/S3 append "fast" and "standard" here as they land; the rust-lane loops below
# pick new levels up with no other change. (Once more than one level exists, the
# non-primary-Python CI trimming applied to optimization_levels above should be
# mirrored here.)
RUST_OPTIMIZATION_LEVELS = ("minimal",)


def _passes_label(passes) -> str:
    if passes is MINIMAL_PASSES:
        return "minimal"
    if passes is FAST_PASSES:
        return "fast"
    if passes is STANDARD_PASSES:
        return "standard"
    return "custom"


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
    project_state = ProjectContextState(runtime_checks=runtime_checks)
    mode_state = ModeContextState(Mode.PLAY)
    return callback_to_cfg(project_state, mode_state, callback, ""), project_state.rom.values


@contextlib.contextmanager
def _recording_rng_tape(tape: list[float]):
    """Records every value drawn through random.uniform/random.randrange (rust lane).

    Direct execution of the sonolus random builtins bottoms out in exactly these two
    stdlib calls with the same arguments their compiled Op.Random/Op.RandomInteger
    forms receive (sonolus/script/internal/random.py), so the recorded values replay
    verbatim as a Rust interpreter RNG tape. Draws through other stdlib entry points
    (random.random(), random.randint(), random.shuffle(), ...) bypass these hooks:
    tests using them are random-value-insensitive by construction (the legacy lane
    never aligned their direct draws with the interpreter's either) and, when such a
    test records no tape at all, the rust lane falls back to a seeded interpreter
    RNG. A function mixing recorded and unrecorded draws produces a short tape and
    fails loudly with the interpreter's "RNG tape exhausted" error.

    Note: direct stdlib random.randrange calls record the raw drawn value, which only
    aligns with the compiled _randrange lowering (start + step * RandomInteger(0, n))
    for start=0, step=1 — the only direct form the suite uses. A nonzero start would
    surface as a loud result mismatch, not silent corruption.

    The wrappers delegate to the originals, so direct-execution behavior is
    unchanged (the same call-window pattern as the T0.5 capture instrumentation).
    """
    orig_uniform = random.uniform
    orig_randrange = random.randrange

    def recording_uniform(a, b):
        value = orig_uniform(a, b)
        tape.append(float(value))
        return value

    def recording_randrange(*args, **kwargs):
        value = orig_randrange(*args, **kwargs)
        tape.append(float(value))
        return value

    random.uniform = recording_uniform
    random.randrange = recording_randrange
    try:
        yield
    finally:
        random.uniform = orig_uniform
        random.randrange = orig_randrange


def _rust_interpreter(rom_values, *, rng_tape, seed):
    """Builds a Rust interpreter preloaded with the ROM block.

    Tape mode when direct execution recorded RNG draws (exact value equality with
    the direct run); seeded mode otherwise (deterministic, and identical streams
    across levels/runtime-check variants within one helper invocation).
    """
    if rng_tape:
        interpreter = sonolus_backend.Interpreter(tape=list(rng_tape))
    else:
        interpreter = sonolus_backend.Interpreter(seed=seed)
    interpreter.set_block(int(PlayBlock.EngineRom), [float(v) for v in rom_values])
    return interpreter


def _assert_rng_tape_consumed(interpreter):
    """Asserts the compiled run consumed the entire recorded RNG tape.

    The bindings expose no tape cursor, so this probes with one extra draw: a fully
    consumed tape raises the interpreter's exhaustion error, while a leftover entry
    is returned and reported as a hard failure (the compiled code drew fewer random
    values than direct execution). Exhaustion *during* the main run already raises
    on its own; together the two checks enforce exact tape-length equality.
    """
    probe = sonolus_backend.EngineNodes(("Random", (0, 1)))
    try:
        leftover = interpreter.run(probe)
    except RuntimeError as e:
        assert str(e) == "RNG tape exhausted"  # noqa: PT017
        return
    raise AssertionError(
        f"RNG tape length mismatch: the compiled run drew fewer random values than "
        f"direct execution (next unconsumed value: {leftover})"
    )


def run_and_validate[**P, R](
    fn: Callable[P, R], *args: P.args, use_simulation_context: bool = False, **kwargs: P.kwargs
) -> R:
    """Runs a function as a regular function and as a compiled function, and checks that the results are the same."""
    exception = None
    regular_result = None
    log_entries = []

    if getattr(fn, "_meta_fn_", False) and hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
        fn._meta_fn_ = True

    def log_cb(x):
        log_entries.append(x)
        return 0

    # Rust lane: record the RNG draws made by direct execution as a tape for the
    # Rust interpreter. The default lane records nothing (nullcontext).
    rng_tape: list[float] = []
    rng_recorder = _recording_rng_tape(rng_tape) if RUST_BACKEND_LANE else contextlib.nullcontext()

    debug_log_callback_token = debug_log_callback.set(log_cb)
    try:
        with rng_recorder:
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
                        DictImpl.from_dict({value_to_rom(k): None for k in value._dict._as_dict_with_py_keys()})
                    )
                elif isinstance(value, DictImpl):
                    return DictImpl.from_dict(
                        {value_to_rom(k): value_to_rom(v) for k, v in value._as_dict_with_py_keys().items()}
                    )
                else:
                    return type(value)._from_place_(ctx().rom[tuple(value._to_list_())])

            try:
                for cell, original_value in zip(closure, original_values, strict=True):
                    cell.cell_contents = value_to_rom(original_value)
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
            assert str(e) == str(exception)  # noqa: PT017
            assert type(e) is type(exception)  # noqa: PT017
            raise exception from None

    if RUST_BACKEND_LANE:
        # Rust lane: frontend trace (above, unchanged) -> encode_cfg -> Rust
        # pipeline -> Rust interpreter, with the same assertions as the legacy
        # loop below. One seed per run_and_validate invocation covers tape-free
        # functions (and keeps cross-level equality once more levels exist).
        seed = random.getrandbits(63)
        for read_closure_from_rom, level in itertools.product((False, True), RUST_OPTIMIZATION_LEVELS):
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
                assert str(e) == str(exception)  # noqa: PT017
                assert type(e) is type(exception)  # noqa: PT017
                raise exception from None

            nodes = sonolus_backend.run_pipeline(encode_cfg(cfg), level)
            interpreter = _rust_interpreter(rom_values, rng_tape=rng_tape, seed=seed)
            num_result = interpreter.run(nodes)
            if exception is None:
                if result_type == Num:
                    assert num_result == regular_result
                else:
                    assert num_result == 0
            compiled_result = result_type._from_list_(
                [interpreter.get(-2, i) for i in range(result_type._size_())]
            )._as_py_()
            compiled_terminated = interpreter.get(-1, 0) != 1
            if rng_tape:
                _assert_rng_tape_consumed(interpreter)

            if exception is not None:
                assert compiled_terminated, "Compiled function should terminate if regular function raises exception"
                raise exception

            assert compiled_result == regular_result
            assert interpreter.log == log_entries

        return regular_result

    for read_closure_from_rom, passes in itertools.product((False, True), optimization_levels):
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
            assert str(e) == str(exception)  # noqa: PT017
            assert type(e) is type(exception)  # noqa: PT017
            raise exception from None

        capture_ref = None
        if _CAPTURE is not None and passes is optimization_levels[0]:
            capture_ref = _CAPTURE.cfg_ref(cfg)
        cfg = run_passes(cfg, passes, OptimizerConfig())
        # The post-pass CFG must be captured here: cfg_to_engine_node destroys it.
        post_ref = None
        if capture_ref is not None:
            post_ref = _CAPTURE.post_cfg_ref(cfg, level=_passes_label(passes))
        entry = cfg_to_engine_node(cfg)
        interpreter = Interpreter() if capture_ref is None else _CAPTURE.make_interpreter()
        interpreter.blocks[PlayBlock.EngineRom] = rom_values

        if capture_ref is None:
            num_result = interpreter.run(entry)
        else:
            num_result = _CAPTURE.run_and_record(
                interpreter,
                entry,
                capture_ref,
                level=_passes_label(passes),
                runtime_checks="terminate",
                temp_memory_block=int(PlayBlock.TemporaryMemory),
                post_cfg=post_ref,
            )
        if exception is None:
            if result_type == Num:
                assert num_result == regular_result
            else:
                assert num_result == 0
        compiled_result = result_type._from_list_(
            [interpreter.get(-2, i) for i in range(result_type._size_())]
        )._as_py_()
        compiled_terminated = interpreter.get(-1, 0) != 1

        if exception is not None:
            assert compiled_terminated, "Compiled function should terminate if regular function raises exception"
            raise exception

        assert compiled_result == regular_result
        assert interpreter.log == log_entries

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
        log_callback = lambda x: None  # noqa: E731

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
    if RUST_BACKEND_LANE:
        # Rust lane: no direct execution happens here, so there is no tape; one
        # seed per invocation keeps results identical across levels and
        # runtime-check variants (the legacy lane uses the setstate dance below
        # for the same purpose).
        seed = random.getrandbits(63)
        for level in RUST_OPTIMIZATION_LEVELS:
            for runtime_checks_value in runtime_checks_values:
                cfg, rom_values = compile_fn(wrapper, runtime_checks=runtime_checks_value)
                nodes = sonolus_backend.run_pipeline(encode_cfg(cfg), level)
                interpreter = _rust_interpreter(rom_values, rng_tape=None, seed=seed)
                results.append(interpreter.run(nodes))
                logs.append(list(interpreter.log))
    else:
        initial_random_state = random.getstate()
        for passes in optimization_levels:
            for runtime_checks_value in runtime_checks_values:
                random.setstate(initial_random_state)
                cfg, rom_values = compile_fn(wrapper, runtime_checks=runtime_checks_value)
                capture_ref = None
                if _CAPTURE is not None and passes is optimization_levels[0]:
                    capture_ref = _CAPTURE.cfg_ref(cfg)
                cfg = run_passes(cfg, passes, OptimizerConfig())
                # The post-pass CFG must be captured here: cfg_to_engine_node destroys it.
                post_ref = None
                if capture_ref is not None:
                    post_ref = _CAPTURE.post_cfg_ref(cfg, level=_passes_label(passes))
                entry = cfg_to_engine_node(cfg)
                interpreter = Interpreter() if capture_ref is None else _CAPTURE.make_interpreter()
                interpreter.blocks[PlayBlock.EngineRom] = rom_values
                if capture_ref is None:
                    result = interpreter.run(entry)
                else:
                    result = _CAPTURE.run_and_record(
                        interpreter,
                        entry,
                        capture_ref,
                        level=_passes_label(passes),
                        runtime_checks=runtime_checks_value.name.lower(),
                        temp_memory_block=int(PlayBlock.TemporaryMemory),
                        post_cfg=post_ref,
                    )
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
