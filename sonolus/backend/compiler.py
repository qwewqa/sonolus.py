from collections.abc import Callable

from sonolus.backend.ir import IRConst, IRInstr
from sonolus.backend.mode import Mode
from sonolus.backend.ops import Op
from sonolus.backend.visitor import compile_and_call
from sonolus.script.internal.context import (
    CallbackContextState,
    Context,
    GlobalContextState,
    context_to_cfg,
    ctx,
    using_ctx,
)
from sonolus.script.num import Num


class Compiler:
    global_state: GlobalContextState

    def __init__(self, mode: Mode = Mode.Play):
        self.global_state = GlobalContextState(mode)

    def compile_callback(self, callback: Callable, name: str):
        callback_state = CallbackContextState(name)
        context = Context(self.global_state, callback_state)
        with using_ctx(context):
            result = compile_and_call(callback)
            if isinstance(result, Num):
                ctx().add_statements(IRInstr(Op.Break, [IRConst(1), result.ir()]))
        return context_to_cfg(context)
