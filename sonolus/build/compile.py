from collections.abc import Callable

from sonolus.backend.finalize import cfg_to_engine_node
from sonolus.backend.ir import IRConst, IRInstr
from sonolus.backend.mode import Mode
from sonolus.backend.ops import Op
from sonolus.backend.optimize.flow import BasicBlock
from sonolus.backend.optimize.optimize import optimize_and_allocate
from sonolus.backend.visitor import compile_and_call
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
from sonolus.script.num import _is_num


def compile_mode(
    mode: Mode,
    rom: ReadOnlyMemory,
    archetypes: list[type[_BaseArchetype]] | None,
    global_callbacks: list[tuple[CallbackInfo, Callable]] | None,
) -> dict:
    global_state = GlobalContextState(
        mode, {a: i for i, a in enumerate(archetypes)} if archetypes is not None else None, rom
    )
    nodes = OutputNodeGenerator()
    results = {}
    if archetypes is not None:
        archetype_entries = []
        for archetype in archetypes:
            archetype_data = {
                "name": archetype.name,
                "hasInput": archetype.is_scored,
                "imports": [{"name": name, "index": index} for name, index in archetype._imported_keys_.items()],
            }
            if mode == Mode.PLAY:
                archetype_data["exports"] = [
                    {"name": name, "index": index} for name, index in archetype._exported_keys_.items()
                ]
            for cb_name, cb_info in archetype._supported_callbacks_.items():
                cb = getattr(archetype, cb_name)
                if cb in archetype._default_callbacks_:
                    continue
                cb_order = getattr(cb, "_callback_order_", 0)
                if not cb_info.supports_order and cb_order != 0:
                    raise ValueError(f"Callback '{cb_name}' does not support a non-zero order")
                cfg = callback_to_cfg(global_state, cb, cb_info.name, archetype)
                cfg = optimize_and_allocate(cfg)
                node = cfg_to_engine_node(cfg)
                node_index = nodes.add(node)
                archetype_data[cb_info.name] = {
                    "index": node_index,
                    "order": cb_order,
                }
            archetype_entries.append(archetype_data)
        results["archetypes"] = archetype_entries
    if global_callbacks is not None:
        for cb_info, cb in global_callbacks:
            cfg = callback_to_cfg(global_state, cb, cb_info.name)
            cfg = optimize_and_allocate(cfg)
            node = cfg_to_engine_node(cfg)
            node_index = nodes.add(node)
            results[cb_info.name] = node_index
    results["nodes"] = nodes.get()
    return results


def callback_to_cfg(
    global_state: GlobalContextState, callback: Callable, name: str, archetype: type[_BaseArchetype] | None = None
) -> BasicBlock:
    callback_state = CallbackContextState(name)
    context = Context(global_state, callback_state)
    with using_ctx(context):
        if archetype is not None:
            result = compile_and_call(callback, archetype._for_compilation())
        else:
            result = compile_and_call(callback)
        if _is_num(result):
            ctx().add_statements(IRInstr(Op.Break, [IRConst(1), result.ir()]))
    return context_to_cfg(context)
