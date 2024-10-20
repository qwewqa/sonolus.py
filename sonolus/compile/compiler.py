from collections.abc import Callable

from sonolus.backend.mode import Mode, PlayMode
from sonolus.compile.visitor import compile_and_call
from sonolus.script.internal.context import CallbackContextState, Context, GlobalContextState, context_to_cfg, using_ctx


class Compiler:
    global_state: GlobalContextState

    def __init__(self, mode: Mode = PlayMode):
        self.global_state = GlobalContextState(mode)

    def compile_callback(self, callback: Callable, name: str):
        callback_state = CallbackContextState(name)
        context = Context(self.global_state, callback_state)
        with using_ctx(context):
            compile_and_call(callback)
        return context_to_cfg(context)
