import functools
import inspect
from collections.abc import Callable

from sonolus.backend.ir import IRInstr, IRPureInstr, IRSet
from sonolus.backend.ops import Op
from sonolus.script.internal.context import ctx
from sonolus.script.internal.impl import meta_fn, validate_value
from sonolus.script.num import Num, is_num


def native_call(op: Op, *args: Num) -> Num:
    if not ctx():
        raise RuntimeError("Unexpected native call")
    args = tuple(validate_value(arg) for arg in args)
    if not all(is_num(arg) for arg in args):
        raise RuntimeError("All arguments must be of type Num")
    result = ctx().alloc(size=1)
    ctx().add_statements(IRSet(result, (IRPureInstr if op.pure else IRInstr)(op, [arg.ir() for arg in args])))
    return Num._from_place_(result)


def native_function[**P, R](op: Op) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorator(fn: Callable[P, Num]) -> Callable[P, Num]:
        signature = inspect.signature(fn)

        @functools.wraps(fn)
        @meta_fn
        def wrapper(*args: Num) -> Num:
            if len(args) != len(signature.parameters):
                raise TypeError(f"Expected {len(signature.parameters)} arguments, got {len(args)}")
            if ctx():
                return native_call(op, *args)
            return fn(*args)

        return wrapper

    return decorator
