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
        params = list(signature.parameters.values())
        n_params = len(params)
        n_required = sum(1 for p in params if p.default is inspect.Parameter.empty)
        # Native functions are always called positionally via ``wrapper(*args)``, so for an
        # all-positional signature ``bind(*args).args`` after ``apply_defaults()`` is just
        # ``args + trailing_defaults``. Anything more exotic (var-positional/keyword-only,
        # too many args) takes the signature.bind path below, which stays the source of
        # truth for behavior and error messages.
        simple_positional = all(
            p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD) for p in params
        )
        param_defaults = tuple(p.default for p in params)

        @functools.wraps(fn)
        def wrapper(*args: int | float | bool) -> Num:
            n = len(args)
            if n < n_required:
                raise TypeError(f"Expected {n_params} arguments, got {n}")
            if ctx():
                if const_eval:
                    args = tuple(validate_value(arg) for arg in args)
                    if not all(_is_num(arg) for arg in args):
                        raise RuntimeError("All arguments must be of type Num")
                    if all(arg._is_py_() for arg in args):
                        return Num._accept_(fn(*[arg._as_py_() for arg in args]))
                if simple_positional and n <= n_params:
                    full_args = args if n == n_params else args + param_defaults[n:]
                else:
                    bound_args = signature.bind(*args)
                    bound_args.apply_defaults()
                    full_args = bound_args.args
                return native_call(op, *full_args)
            return fn(*args)  # type: ignore

        wrapper._meta_fn_ = True
        return wrapper

    return decorator  # type: ignore
