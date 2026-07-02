from collections.abc import Callable
from concurrent.futures import Executor

from sonolus.backend._opt import driver as _driver
from sonolus.backend.ir import IRConst, IRInstr
from sonolus.backend.mode import Mode
from sonolus.backend.ops import Op
from sonolus.backend.optimize import OptimizationLevel
from sonolus.backend.optimize.flow import BasicBlock
from sonolus.script.archetype import _BaseArchetype
from sonolus.script.internal.callbacks import CallbackInfo
from sonolus.script.internal.context import (
    CallbackContextState,
    Context,
    ModeContextState,
    ProjectContextState,
    context_to_cfg,
    ctx,
    using_ctx,
)
from sonolus.script.internal.error import CompilationError
from sonolus.script.internal.visitor import compile_and_call_at_definition
from sonolus.script.num import _is_num


def compile_mode(
    mode: Mode,
    project_state: ProjectContextState,
    archetypes: list[type[_BaseArchetype]] | None,
    global_callbacks: list[tuple[CallbackInfo, Callable]] | None,
    level: OptimizationLevel | None = None,
    thread_pool: Executor | None = None,
    validate_only: bool = False,
) -> dict:
    """Thin delegator to the compiled per-mode compile driver.

    The work loop lives in ``sonolus.backend._opt.driver.compile_mode`` (moved
    there in M4 so the compile driver sits in the compiled package). The frontend
    tracer ``callback_to_cfg`` stays in Python and is passed through. This name is
    kept because ``engine.py`` and tests import ``compile_mode`` from here.
    """
    return _driver.compile_mode(
        mode,
        project_state,
        archetypes,
        global_callbacks,
        callback_to_cfg,
        level,
        thread_pool,
        validate_only,
    )


def callback_to_cfg(
    project_state: ProjectContextState,
    mode_state: ModeContextState,
    callback: Callable,
    name: str,
    archetype: type[_BaseArchetype] | None = None,
) -> BasicBlock:
    try:
        # Default to no_eval=True for performance unless there's an error.
        return _callback_to_cfg(project_state, mode_state, callback, name, archetype, no_eval=True)
    except CompilationError:
        return _callback_to_cfg(project_state, mode_state, callback, name, archetype, no_eval=False)


def _callback_to_cfg(
    project_state: ProjectContextState,
    mode_state: ModeContextState,
    callback: Callable,
    name: str,
    archetype: type[_BaseArchetype] | None,
    no_eval: bool,
) -> BasicBlock:
    callback_state = CallbackContextState(name, no_eval=no_eval)
    context = Context(project_state, mode_state, callback_state)
    with using_ctx(context):
        if archetype is not None:
            result = compile_and_call_at_definition(callback, archetype._for_compilation())
        else:
            result = compile_and_call_at_definition(callback)
        if _is_num(result):
            ctx().add_statements(IRInstr(Op.Break, [IRConst(1), result.ir()]))
    return context_to_cfg(context)
