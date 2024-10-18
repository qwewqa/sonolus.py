import ast
import functools
import inspect
from contextlib import contextmanager
from types import MethodType, FunctionType
from typing import Callable, Any, Never

from sonolus.compile.excepthook import install_excepthook
from sonolus.compile.utils import get_function
from sonolus.script.comptime import Comptime
from sonolus.script.internal.context import set_ctx, ctx, Scope, Context
from sonolus.script.internal.error import CompilationError
from sonolus.script.internal.impl import validate_value
from sonolus.script.internal.value import Value
from sonolus.script.record import RecordField

_compiler_internal_ = True


def compile_and_call[** P, R](fn: Callable[P, R], /, *args: P.args, **kwargs: P.kwargs) -> R:
    return generate_fn_impl(fn)(*args, **kwargs)


def generate_fn_impl(fn: Callable):
    install_excepthook()
    match fn:
        case MethodType() as method:
            return functools.partial(generate_fn_impl(method.__func__), method.__self__)
        case FunctionType() as function:
            if getattr(function, "_self_impl_", False):
                return function
            return functools.partial(eval_fn, function)
        case _:
            if hasattr(fn, "__call__"):
                return generate_fn_impl(fn.__call__)
            else:
                raise TypeError(f"Unsupported callable {fn!r}")


def eval_fn(fn: Callable, /, *args, **kwargs):
    source_file, node = get_function(fn)
    bound_args = inspect.signature(fn).bind(*args, **kwargs)
    closurevars = inspect.getclosurevars(fn)
    global_vars = {**closurevars.nonlocals, **closurevars.globals, **closurevars.builtins}
    return Visitor(source_file, bound_args, global_vars).run(node)


unary_ops = {
    ast.Invert: "__invert__",
    ast.Not: "__not__",
    ast.UAdd: "__pos__",
    ast.USub: "__neg__",
}

bin_ops = {
    ast.Add: "__add__",
    ast.Sub: "__sub__",
    ast.Mult: "__mul__",
    ast.Div: "__truediv__",
    ast.FloorDiv: "__floordiv__",
    ast.Mod: "__mod__",
    ast.Pow: "__pow__",
    ast.LShift: "__lshift__",
    ast.RShift: "__rshift__",
    ast.BitOr: "__or__",
    ast.BitAnd: "__and__",
    ast.BitXor: "__xor__",
    ast.MatMult: "__matmul__",
}

rbin_ops = {
    ast.Add: "__radd__",
    ast.Sub: "__rsub__",
    ast.Mult: "__rmul__",
    ast.Div: "__rtruediv__",
    ast.FloorDiv: "__rfloordiv__",
    ast.Mod: "__rmod__",
    ast.Pow: "__rpow__",
    ast.LShift: "__rlshift__",
    ast.RShift: "__rrshift__",
    ast.BitOr: "__ror__",
    ast.BitAnd: "__rand__",
    ast.BitXor: "__rxor__",
    ast.MatMult: "__rmatmul__",
}

inplace_ops = {
    ast.Add: "__iadd__",
    ast.Sub: "__isub__",
    ast.Mult: "__imul__",
    ast.Div: "__itruediv__",
    ast.FloorDiv: "__ifloordiv__",
    ast.Mod: "__imod__",
    ast.Pow: "__ipow__",
    ast.LShift: "__ilshift__",
    ast.RShift: "__irshift__",
    ast.BitOr: "__ior__",
    ast.BitXor: "__ixor__",
    ast.BitAnd: "__iand__",
    ast.MatMult: "__imatmul__",
}

comp_ops = {
    ast.Eq: "__eq__",
    ast.NotEq: "__ne__",
    ast.Lt: "__lt__",
    ast.LtE: "__le__",
    ast.Gt: "__gt__",
    ast.GtE: "__ge__",
    ast.In: "__contains__",
    ast.NotIn: "__contains__",
}


class Visitor(ast.NodeVisitor):
    source_file: str
    globals: dict[str, Any]
    bound_args: inspect.BoundArguments
    used_names: dict[str, int]
    return_ctxs: list[Context]  # Contexts at return statements, which will branch to the exit
    loop_head_ctxs: list[Context]  # Contexts at loop heads, from outer to inner
    break_ctxs: list[list[Context]]  # Contexts at break statements, from outer to inner

    def __init__(self, source_file: str, bound_args: inspect.BoundArguments, global_vars: dict[str, Any]):
        self.source_file = source_file
        self.globals = {k: validate_value(v) for k, v in global_vars.items()}
        self.bound_args = bound_args
        self.used_names = {}
        self.return_ctxs = []
        self.loop_head_ctxs = []
        self.break_ctxs = []

    def run(self, node: ast.FunctionDef):
        before_ctx = ctx()
        set_ctx(before_ctx.branch_with_scope(None, Scope()))
        for name, value in self.bound_args.arguments.items():
            ctx().scope.set_value(name, validate_value(value))
        ctx().scope.set_value("$return", validate_value(None))
        self.visit(node)
        after_ctx = ctx()
        set_ctx(after_ctx.branch_with_scope(None, before_ctx.scope.copy()))

    def visit_FunctionDef(self, node):
        raise NotImplementedError("Nested functions are not supported")

    def visit_AsyncFunctionDef(self, node):
        raise NotImplementedError("Async functions are not supported")

    def visit_ClassDef(self, node):
        raise NotImplementedError("Classes within functions are not supported")

    def visit_Return(self, node):
        value = self.visit(node.value)
        ctx().scope.set_value("$return", value)
        self.return_ctxs.append(ctx())
        set_ctx(ctx().into_dead())

    def visit_Delete(self, node):
        raise NotImplementedError("Delete statements are not supported")

    def visit_Assign(self, node):
        pass

    def handle_Assign(self, target: ast.AST, value: Value):
        match target:
            case ast.Name(id=name):
                ctx().scope.set_value(name, value)
            case ast.Attribute(value=attr_value, attr=attr):
                attr_value = self.visit(attr_value)
                attr_value.set_attr(attr, value)
            case ast.Subscript(value=subs_value, slice=subs_slice):
                subs_value = self.visit(subs_value)
                subs_slice = self.visit(subs_slice)
                subs_value.set_item(subs_slice, value)
            case _:
                raise NotImplementedError(f"Unsupported assignment target {target!r}")

    def handle_getattr(self, node: ast.stmt, target: Value, key: str) -> Value:
        with self.reporting_errors_at_node(node):
            descriptor = getattr(type(target), key, None)
            match descriptor:
                case property(fget=getter):
                    return self.handle_call(node, getter, target)
                case RecordField() | FunctionType() | classmethod() | staticmethod() | None:
                    return getattr(target, key)
                case _:
                    raise TypeError(f"Unsupported field descriptor {descriptor!r}")

    def handle_setattr(self, node: ast.stmt, target: Value, key: str, value: Value):
        with self.reporting_errors_at_node(node):
            descriptor = getattr(type(target), key, None)
            match descriptor:
                case property(fset=setter):
                    if setter is None:
                        raise AttributeError(f"Cannot set attribute {key!r} because property has no setter")
                    self.handle_call(node, setter, target, value)
                case RecordField():
                    setattr(target, key, value)
                case _:
                    raise TypeError(f"Unsupported field descriptor {descriptor!r}")

    def handle_call[** P, R](self, node: ast.stmt, fn: Callable[P, R], /, *args: P.args, **kwargs: P.kwargs) -> R:
        """Handles a call to the given callable."""
        if isinstance(fn, Comptime) and isinstance(fn.value(), type):
            return self.execute_at_node(node, fn.value(), *args, **kwargs)
        else:
            return self.execute_at_node(node, lambda: validate_value(ctx().call(fn, *args, **kwargs)))

    def handle_setitem(self, node: ast.stmt, target: Value, key: Value, value: Value):
        if hasattr(target, "__setitem__"):
            return self.handle_call(node, target.__setitem__, key, value)
        else:
            self.raise_exception_at_node(node, TypeError(f"Cannot set items on {type(target).__name__}"))

    def raise_exception_at_node(self, node: ast.stmt, cause: Exception) -> Never:
        """Throws a compilation error at the given node."""

        def thrower() -> Never:
            raise CompilationError(str(cause)) from cause

        self.execute_at_node(node, thrower)

    def execute_at_node[** P, R](self, node: ast.stmt, fn: Callable[P, R], /, *args: P.args, **kwargs: P.kwargs) -> R:
        """Executes the given function at the given node for a better traceback."""
        expr = ast.Expression(
            body=ast.Call(
                func=ast.Name(id="fn", ctx=ast.Load()),
                args=[ast.Starred(value=ast.Name(id="args", ctx=ast.Load()), ctx=ast.Load())],
                keywords=[ast.keyword(value=ast.Name(id="kwargs", ctx=ast.Load()), arg=None)],
                lineno=node.lineno,
                col_offset=node.col_offset,
                end_lineno=node.end_lineno,
                end_col_offset=node.end_col_offset,
            ),
        )
        expr = ast.fix_missing_locations(expr)
        return exec(
            compile(expr, filename=self.source_file, mode="eval"),
            {"fn": fn, "args": args, "kwargs": kwargs, "_filter_traceback_": True},
        )

    @contextmanager
    def reporting_errors_at_node(self, node: ast.AST):
        try:
            yield
        except CompilationError as e:
            raise e from None
        except Exception as e:
            self.raise_exception_at_node(node, e)

    def new_name(self, name: str):
        self.used_names[name] = self.used_names.get(name, 0) + 1
        return f"${name}_{self.used_names[name]}"
