from collections.abc import Callable

from sonolus.backend.allocate import AllocateBasic
from sonolus.backend.blocks import PlayBlock
from sonolus.backend.compiler import Compiler
from sonolus.backend.finalize import cfg_to_engine_node
from sonolus.backend.interpret import Interpreter
from sonolus.backend.place import BlockPlace
from sonolus.backend.simplify import CoalesceFlow
from sonolus.backend.visitor import compile_and_call
from sonolus.script.internal.impl import self_impl, validate_value


def dual_run[**P, R](fn: Callable[P, R], *args: P.args, **kwargs: P.kwargs) -> R:
    """Runs a function as a regular function and as a compiled function, and checks that the results are the same."""

    regular_result = fn(*args, **kwargs)
    result_type = type(validate_value(regular_result))

    @self_impl
    def run_compiled():
        result = compile_and_call(fn, *args, **kwargs)
        target = result_type._from_place_(BlockPlace(PlayBlock.LevelMemory, 0))
        if result_type._is_value_type_():
            target._set_(result)
        else:
            target._copy_from_(result)

    compiler = Compiler()
    cfg = compiler.compile_callback(run_compiled, "")
    cfg = CoalesceFlow().run(cfg)
    cfg = AllocateBasic().run(cfg)
    entry = cfg_to_engine_node(cfg)
    interpreter = Interpreter()
    interpreter.run(entry)
    compiled_result = result_type._from_list_(
        [interpreter.get(PlayBlock.LevelMemory, i) for i in range(result_type._size_())]
    )._as_py_()

    assert regular_result == compiled_result

    return regular_result
