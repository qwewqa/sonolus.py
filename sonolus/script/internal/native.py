import functools
from collections.abc import Callable

from sonolus.backend.ir import IRSet, IRInstr, IRPureInstr
from sonolus.backend.ops import Op
from sonolus.script.internal.context import ctx
from sonolus.script.internal.impl import validate_value, self_impl
from sonolus.script.num import Num


def native_call(op: Op, *args: Num) -> Num:
    if not ctx():
        raise RuntimeError("Unexpected native call")
    args = tuple(validate_value(arg) for arg in args)
    if not all(isinstance(arg, Num) for arg in args):
        raise RuntimeError("All arguments must be of type Num")
    result = ctx().alloc(size=1)
    ctx().add_statements(
        IRSet(result, (IRPureInstr if op.pure else IRInstr)(op, [arg.ir() for arg in args]))
    )
    return Num._from_place_(result)


def native_function[** P](op: Op) -> Callable[[Callable[P, Num | None]], Callable[P, Num | None]]:
    def decorator(fn: Callable[P, Num]) -> Callable[P, Num]:
        @functools.wraps(fn)
        @self_impl
        def wrapper(*args: Num) -> Num:
            if ctx():
                return native_call(op, *args)
            return fn(*args)

        return wrapper

    return decorator
