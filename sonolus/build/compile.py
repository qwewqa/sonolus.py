from collections.abc import Callable, Sequence
from concurrent.futures import Executor, Future

from sonolus.backend.finalize import cfg_to_engine_node
from sonolus.backend.ir import IRConst, IRInstr
from sonolus.backend.mode import Mode
from sonolus.backend.ops import Op
from sonolus.backend.optimize.flow import BasicBlock
from sonolus.backend.optimize.optimize import STANDARD_PASSES
from sonolus.backend.optimize.passes import CompilerPass, OptimizerConfig, run_passes
from sonolus.backend.visitor import compile_and_call_at_definition
from sonolus.build.node import OutputNodeGenerator
from sonolus.script.archetype import _BaseArchetype
from sonolus.script.internal.callbacks import CallbackInfo
from sonolus.script.internal.context import (
    CallbackContextState,
    Context,
    GlobalContextState,
    ReadOnlyMemory,
    context_to_cfg,
    ctx,
    using_ctx,
)
from sonolus.script.internal.error import CompilationError
from sonolus.script.num import _is_num


def compile_mode(
    mode: Mode,
    rom: ReadOnlyMemory,
    archetypes: list[type[_BaseArchetype]] | None,
    global_callbacks: list[tuple[CallbackInfo, Callable]] | None,
    passes: Sequence[CompilerPass] | None = None,
    thread_pool: Executor | None = None,
    validate_only: bool = False,
) -> dict:
    if passes is None:
        passes = STANDARD_PASSES

    global_state = GlobalContextState(
        mode,
        {a: i for i, a in enumerate(archetypes)} if archetypes is not None else None,
        rom,
    )
    nodes = OutputNodeGenerator()
    results = {}

    def process_callback(
        cb_info: CallbackInfo,
        cb: Callable,
        arch: type[_BaseArchetype] | None = None,
    ) -> tuple[str, int] | tuple[str, dict]:
        """Compile a single callback to a node.

        Returns either:
        - (cb_info.name, node_index) for global callbacks, or
        - (cb_info.name, {"index": node_index, "order": cb_order}) for archetype callbacks.
        """
        cfg = callback_to_cfg(global_state, cb, cb_info.name, arch)
        if validate_only:
            if arch is not None:
                cb_order = getattr(cb, "_callback_order_", 0)
                return cb_info.name, {"index": 0, "order": cb_order}
            else:
                return cb_info.name, 0
        cfg = run_passes(cfg, passes, OptimizerConfig(mode=mode, callback=cb_info.name))
        node = cfg_to_engine_node(cfg)
        node_index = nodes.add(node)

        if arch is not None:
            cb_order = getattr(cb, "_callback_order_", 0)
            return cb_info.name, {"index": node_index, "order": cb_order}
        else:
            return cb_info.name, node_index

    all_futures = {}
    base_archetype_entries = {}

    if archetypes is not None:
        base_archetypes = []
        seen_base_archetypes = set()
        for a in archetypes:
            base = getattr(a, "_derived_base_", a)
            if base not in seen_base_archetypes:
                seen_base_archetypes.add(base)
                base_archetypes.append(base)

        for archetype in base_archetypes:
            archetype._init_fields()

            archetype_data = {
                "name": archetype.name,
                "hasInput": archetype.is_scored,
                "imports": [{"name": name, "index": index} for name, index in archetype._imported_keys_.items()],
            }
            if mode == Mode.PLAY:
                archetype_data["exports"] = [*archetype._exported_keys_]

            callback_items = [
                (cb_name, cb_info, getattr(archetype, cb_name))
                for cb_name, cb_info in archetype._supported_callbacks_.items()
                if getattr(archetype, cb_name) not in archetype._default_callbacks_
            ]

            if thread_pool is not None:
                for cb_name, cb_info, cb in callback_items:
                    cb_order = getattr(cb, "_callback_order_", 0)
                    if not cb_info.supports_order and cb_order != 0:
                        raise ValueError(f"Callback '{cb_name}' does not support a non-zero order")
                    f: Future = thread_pool.submit(process_callback, cb_info, cb, archetype)
                    all_futures[f] = ("archetype", archetype_data, cb_name)
            else:
                for cb_name, cb_info, cb in callback_items:
                    cb_order = getattr(cb, "_callback_order_", 0)
                    if not cb_info.supports_order and cb_order != 0:
                        raise ValueError(f"Callback '{cb_name}' does not support a non-zero order")
                    cb_name, result_data = process_callback(cb_info, cb, archetype)
                    archetype_data[cb_name] = result_data

            base_archetype_entries[archetype] = archetype_data

    if global_callbacks is not None and thread_pool is not None:
        for cb_info, cb in global_callbacks:
            f: Future = thread_pool.submit(process_callback, cb_info, cb, None)
            all_futures[f] = ("global", None, cb_info.name)

    if thread_pool is not None:
        for f, (callback_type, archetype_data, cb_name) in all_futures.items():
            cb_name, result_data = f.result()
            if callback_type == "archetype":
                archetype_data[cb_name] = result_data
            else:  # global callback
                results[cb_name] = result_data
    elif global_callbacks is not None:
        for cb_info, cb in global_callbacks:
            cb_name, node_index = process_callback(cb_info, cb, None)
            results[cb_name] = node_index

    if archetypes is not None:
        results["archetypes"] = [
            {**base_archetype_entries[getattr(a, "_derived_base_", a)], "name": a.name, "hasInput": a.is_scored}
            for a in archetypes
        ]

    results["nodes"] = nodes.get()
    return results


def callback_to_cfg(
    global_state: GlobalContextState,
    callback: Callable,
    name: str,
    archetype: type[_BaseArchetype] | None = None,
) -> BasicBlock:
    try:
        # Default to no_eval=True for performance unless there's an error.
        return _callback_to_cfg(global_state, callback, name, archetype, no_eval=True)
    except CompilationError:
        return _callback_to_cfg(global_state, callback, name, archetype, no_eval=False)


def _callback_to_cfg(
    global_state: GlobalContextState,
    callback: Callable,
    name: str,
    archetype: type[_BaseArchetype] | None,
    no_eval: bool,
) -> BasicBlock:
    callback_state = CallbackContextState(name, no_eval=no_eval)
    context = Context(global_state, callback_state)
    with using_ctx(context):
        if archetype is not None:
            result = compile_and_call_at_definition(callback, archetype._for_compilation())
        else:
            result = compile_and_call_at_definition(callback)
        if _is_num(result):
            ctx().add_statements(IRInstr(Op.Break, [IRConst(1), result.ir()]))
    return context_to_cfg(context)
