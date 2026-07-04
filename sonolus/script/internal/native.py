import functools
import inspect
from collections.abc import Callable, Iterable

from sonolus.backend.ir import IRConst, IRInstr, IRPureInstr, IRSet
from sonolus.backend.ops import Op
from sonolus.script.internal.context import ctx
from sonolus.script.internal.impl import validate_value
from sonolus.script.num import Num, _is_num


def native_call(op: Op, *args: int | float | bool) -> Num:
    if not ctx():
        raise RuntimeError("Unexpected native call")
    args = tuple(validate_value(arg) for arg in args)
    if not all(_is_num(arg) for arg in args):
        raise RuntimeError("All arguments must be of type Num")
    result = ctx().alloc(size=1)
    ctx().add_statements(IRSet(result, (IRPureInstr if op.pure else IRInstr)(op, [arg.ir() for arg in args])))
    return Num._from_place_(result)


def native_switch_membership(value: int | float | bool, cases: Iterable[int | float]) -> bool:
    """Check whether value equals one of the given compile time constant cases.

    Emits a SwitchWithDefault mapping each case to 1 with a default of 0. Cases are
    deduplicated and sorted so the emitted keys are canonical and dense ranges lower well.
    """
    if not ctx():
        raise RuntimeError("Unexpected native switch membership check")
    value = validate_value(value)
    if not _is_num(value):
        raise RuntimeError("Value must be of type Num")
    cases = sorted({float(case) for case in cases})
    if value._is_py_():
        return Num._accept_(float(value._as_py_()) in cases)
    if not cases:
        return Num._accept_(False)
    args = [value.ir()]
    for case in cases:
        args.append(IRConst(case))
        args.append(IRConst(1))
    args.append(IRConst(0))
    result = ctx().alloc(size=1)
    ctx().add_statements(IRSet(result, IRPureInstr(Op.SwitchWithDefault, args)))
    return Num._from_place_(result)


native_switch_membership._meta_fn_ = True


def native_function[**P, R](op: Op, const_eval: bool = False) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorator(fn: Callable[P, int | float | bool]) -> Callable[P, Num]:
        signature = inspect.signature(fn)

        @functools.wraps(fn)
        def wrapper(*args: int | float | bool) -> Num:
            if len(args) < sum(1 for p in signature.parameters.values() if p.default == inspect.Parameter.empty):
                raise TypeError(f"Expected {len(signature.parameters)} arguments, got {len(args)}")
            if ctx():
                if const_eval:
                    args = tuple(validate_value(arg) for arg in args)
                    if not all(_is_num(arg) for arg in args):
                        raise RuntimeError("All arguments must be of type Num")
                    if all(arg._is_py_() for arg in args):
                        return Num._accept_(fn(*[arg._as_py_() for arg in args]))
                bound_args = signature.bind(*args)
                bound_args.apply_defaults()
                return native_call(op, *bound_args.args)
            return fn(*args)  # type: ignore

        wrapper._meta_fn_ = True
        return wrapper

    return decorator  # type: ignore
