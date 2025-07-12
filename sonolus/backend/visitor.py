# ruff: noqa: N802
import ast
import builtins
import functools
import inspect
from collections.abc import Callable, Sequence
from inspect import ismethod
from types import FunctionType, MethodType, MethodWrapperType
from typing import Any, Never, Self

from sonolus.backend.excepthook import install_excepthook
from sonolus.backend.utils import get_function, scan_writes
from sonolus.script.debug import assert_true
from sonolus.script.internal.builtin_impls import BUILTIN_IMPLS, _bool, _float, _int, _len
from sonolus.script.internal.constant import ConstantValue
from sonolus.script.internal.context import Context, EmptyBinding, Scope, ValueBinding, ctx, set_ctx
from sonolus.script.internal.descriptor import SonolusDescriptor
from sonolus.script.internal.error import CompilationError
from sonolus.script.internal.impl import validate_value
from sonolus.script.internal.tuple_impl import TupleImpl
from sonolus.script.internal.value import Value
from sonolus.script.iterator import SonolusIterator
from sonolus.script.num import Num, _is_num

_compiler_internal_ = True


def compile_and_call[**P, R](fn: Callable[P, R], /, *args: P.args, **kwargs: P.kwargs) -> R:
    if not ctx():
        return fn(*args, **kwargs)
    return validate_value(generate_fn_impl(fn)(*args, **kwargs))


def generate_fn_impl(fn: Callable):
    install_excepthook()
    match fn:
        case ConstantValue() as value if value._is_py_():
            return generate_fn_impl(value._as_py_())
        case MethodType() as method:
            return functools.partial(generate_fn_impl(method.__func__), method.__self__)
        case FunctionType() as function:
            if getattr(function, "_meta_fn_", False):
                return function
            return functools.partial(eval_fn, function)
        case _:
            if callable(fn) and isinstance(fn, Value):
                return generate_fn_impl(fn.__call__)
            elif callable(fn):
                raise TypeError(f"Unsupported callable {fn!r}")
            else:
                raise TypeError(f"'{type(fn).__name__}' object is not callable")


def eval_fn(fn: Callable, /, *args, **kwargs):
    source_file, node = get_function(fn)
    bound_args = inspect.signature(fn).bind(*args, **kwargs)
    bound_args.apply_defaults()
    if ismethod(fn):
        code = fn.__func__.__code__
        closure = fn.__func__.__closure__
    else:
        code = fn.__code__
        closure = fn.__closure__
    if closure is None:
        nonlocal_vars = {}
    else:
        nonlocal_vars = {
            var: cell.cell_contents for var, cell in zip(code.co_freevars, closure, strict=True) if cell is not None
        }
    global_vars = {
        **builtins.__dict__,
        **fn.__globals__,
        **nonlocal_vars,
    }
    return Visitor(source_file, bound_args, global_vars).run(node)


unary_ops = {
    ast.Invert: "__invert__",
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
}

rcomp_ops = {
    ast.Eq: "__eq__",
    ast.NotEq: "__ne__",
    ast.Lt: "__gt__",
    ast.LtE: "__ge__",
    ast.Gt: "__lt__",
    ast.GtE: "__le__",
    ast.In: "__contains__",  # Only supported on the right side
    ast.NotIn: "__contains__",
}

op_to_symbol = {
    ast.Add: "+",
    ast.Sub: "-",
    ast.Mult: "*",
    ast.Div: "/",
    ast.FloorDiv: "//",
    ast.Mod: "%",
    ast.Pow: "**",
    ast.Eq: "==",
    ast.NotEq: "!=",
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">=",
    ast.And: "and",
    ast.Or: "or",
    ast.BitAnd: "&",
    ast.BitOr: "|",
    ast.BitXor: "^",
    ast.LShift: "<<",
    ast.RShift: ">>",
    ast.USub: "-",
    ast.UAdd: "+",
    ast.Invert: "~",
    ast.Not: "not",
    ast.In: "in",
    ast.NotIn: "not in",
}


class Visitor(ast.NodeVisitor):
    source_file: str
    globals: dict[str, Any]
    bound_args: inspect.BoundArguments
    used_names: dict[str, int]
    return_ctxs: list[Context]  # Contexts at return statements, which will branch to the exit
    loop_head_ctxs: list[
        Context | list[Context]
    ]  # Contexts at loop heads, from outer to inner. Contains a list for unrolled (tuple) loops
    break_ctxs: list[list[Context]]  # Contexts at break statements, from outer to inner
    active_ctx: Context | None  # The active context for use in nested functions=
    parent: Self | None  # The parent visitor for use in nested functions

    def __init__(
        self,
        source_file: str,
        bound_args: inspect.BoundArguments,
        global_vars: dict[str, Any],
        parent: Self | None = None,
    ):
        self.source_file = source_file
        self.globals = global_vars
        self.bound_args = bound_args
        self.used_names = {}
        self.return_ctxs = []
        self.loop_head_ctxs = []
        self.break_ctxs = []
        self.active_ctx = None
        self.parent = parent

    def run(self, node):
        before_ctx = ctx()
        set_ctx(before_ctx.branch_with_scope(None, Scope()))
        for name, value in self.bound_args.arguments.items():
            ctx().scope.set_value(name, validate_value(value))
        match node:
            case ast.FunctionDef(body=body):
                ctx().scope.set_value("$return", validate_value(None))
                for stmt in body:
                    if not ctx().live:
                        break
                    self.visit(stmt)
            case ast.Lambda(body=body):
                result = self.visit(body)
                ctx().scope.set_value("$return", result)
            case _:
                raise NotImplementedError("Unsupported syntax")
        after_ctx = Context.meet([*self.return_ctxs, ctx()])
        self.active_ctx = after_ctx
        result_binding = after_ctx.scope.get_binding("$return")
        if not isinstance(result_binding, ValueBinding):
            raise ValueError("Function has conflicting return values")
        set_ctx(after_ctx.branch_with_scope(None, before_ctx.scope.copy()))
        return result_binding.value

    def visit(self, node):
        """Visit a node."""
        # We want this here so this is filtered out of tracebacks
        method = "visit_" + node.__class__.__name__
        visitor = getattr(self, method, self.generic_visit)
        with self.reporting_errors_at_node(node):
            return visitor(node)

    def visit_FunctionDef(self, node):
        name = node.name
        signature = self.arguments_to_signature(node.args)

        def fn(*args, **kwargs):
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            return Visitor(
                self.source_file,
                bound,
                self.globals,
                self,
            ).run(node)

        fn._meta_fn_ = True
        fn.__name__ = name
        fn.__qualname__ = name

        for decorator in reversed(node.decorator_list):
            fn = self.handle_call(decorator, self.visit(decorator), fn)

        ctx().scope.set_value(name, validate_value(fn))

    def visit_AsyncFunctionDef(self, node):
        raise NotImplementedError("Async functions are not supported")

    def visit_ClassDef(self, node):
        raise NotImplementedError("Classes within functions are not supported")

    def visit_Return(self, node):
        value = self.visit(node.value) if node.value else validate_value(None)
        ctx().scope.set_value("$return", value)
        self.return_ctxs.append(ctx())
        set_ctx(ctx().into_dead())

    def visit_Delete(self, node):
        for target in node.targets:
            match target:
                case ast.Name():
                    raise NotImplementedError("Deleting variables is not supported")
                case ast.Subscript(value=value, slice=slice):
                    self.handle_delitem(target, self.visit(value), self.visit(slice))
                case ast.Attribute():
                    raise NotImplementedError("Deleting attributes is not supported")
                case _:
                    raise NotImplementedError("Unsupported delete target")

    def visit_Assign(self, node):
        value = self.visit(node.value)
        for target in node.targets:
            self.handle_assign(target, value)

    def visit_TypeAlias(self, node):
        raise NotImplementedError("Type aliases are not supported")

    def visit_AugAssign(self, node):
        lhs_value = self.visit(node.target)
        rhs_value = self.visit(node.value)
        inplace_fn_name = inplace_ops[type(node.op)]
        regular_fn_name = bin_ops[type(node.op)]
        right_fn_name = rbin_ops[type(node.op)]
        if hasattr(lhs_value, inplace_fn_name):
            result = self.handle_call(node, getattr(lhs_value, inplace_fn_name), rhs_value)
            if not self.is_not_implemented(result):
                if result is not lhs_value:
                    raise ValueError("Inplace operation must return the same object")
                self.handle_assign(node.target, result)
                return
        if hasattr(lhs_value, regular_fn_name):
            result = self.handle_call(node, getattr(lhs_value, regular_fn_name), rhs_value)
            if not self.is_not_implemented(result):
                self.handle_assign(node.target, result)
                return
        if hasattr(rhs_value, right_fn_name) and type(lhs_value) is not type(rhs_value):
            result = self.handle_call(node, getattr(rhs_value, right_fn_name), lhs_value)
            if not self.is_not_implemented(result):
                self.handle_assign(node.target, result)
                return
        raise TypeError(
            f"unsupported operand type(s) for {op_to_symbol[type(node.op)]}=: "
            f"'{type(lhs_value).__name__}' and '{type(rhs_value).__name__}'"
        )

    def visit_AnnAssign(self, node):
        value = self.visit(node.value)
        self.handle_assign(node.target, value)

    def visit_For(self, node):
        from sonolus.script.internal.tuple_impl import TupleImpl

        iterable = self.visit(node.iter)
        if isinstance(iterable, TupleImpl):
            # Unroll the loop
            break_ctxs = []
            for value in iterable.value:
                set_ctx(ctx().branch(None))
                self.loop_head_ctxs.append([])
                self.break_ctxs.append([])
                self.handle_assign(node.target, validate_value(value))
                for stmt in node.body:
                    if not ctx().live:
                        break
                    self.visit(stmt)
                continue_ctxs = [*self.loop_head_ctxs.pop(), ctx()]
                break_ctxs.extend(self.break_ctxs.pop())
                set_ctx(Context.meet(continue_ctxs))
            if break_ctxs:
                set_ctx(Context.meet([*break_ctxs, ctx()]))
            return
        iterator = self.handle_call(node, iterable.__iter__)
        if not isinstance(iterator, SonolusIterator):
            raise ValueError("Unsupported iterator")
        writes = scan_writes(node)
        header_ctx = ctx().prepare_loop_header(writes)
        self.loop_head_ctxs.append(header_ctx)
        self.break_ctxs.append([])
        set_ctx(header_ctx)
        has_next = self.convert_to_boolean_num(node, self.handle_call(node, iterator.has_next))
        if has_next._is_py_() and not has_next._as_py_():
            # The loop will never run, continue after evaluating the condition
            self.loop_head_ctxs.pop()
            self.break_ctxs.pop()
            for stmt in node.orelse:
                if not ctx().live:
                    break
                self.visit(stmt)
            return
        ctx().test = has_next.ir()
        body_ctx = ctx().branch(None)
        else_ctx = ctx().branch(0)

        set_ctx(body_ctx)
        self.handle_assign(node.target, self.handle_call(node, iterator.next))
        for stmt in node.body:
            if not ctx().live:
                break
            self.visit(stmt)
        ctx().branch_to_loop_header(header_ctx)

        self.loop_head_ctxs.pop()
        break_ctxs = self.break_ctxs.pop()

        set_ctx(else_ctx)
        for stmt in node.orelse:
            if not ctx().live:
                break
            self.visit(stmt)
        else_end_ctx = ctx()

        after_ctx = Context.meet([else_end_ctx, *break_ctxs])
        set_ctx(after_ctx)

    def visit_While(self, node):
        writes = scan_writes(node)
        header_ctx = ctx().prepare_loop_header(writes)
        self.loop_head_ctxs.append(header_ctx)
        self.break_ctxs.append([])
        set_ctx(header_ctx)
        test = self.convert_to_boolean_num(node.test, self.visit(node.test))
        if test._is_py_():
            if test._as_py_():
                # The loop will run until a break / return
                body_ctx = ctx().branch(None)
                set_ctx(body_ctx)
                for stmt in node.body:
                    if not ctx().live:
                        break
                    self.visit(stmt)
                ctx().branch_to_loop_header(header_ctx)

                self.loop_head_ctxs.pop()
                break_ctxs = self.break_ctxs.pop()

                # Skip the else block

                after_ctx = Context.meet([ctx().into_dead(), *break_ctxs])
                set_ctx(after_ctx)
                return
            else:
                # The loop will never run, continue after evaluating the condition
                self.loop_head_ctxs.pop()
                self.break_ctxs.pop()
                for stmt in node.orelse:
                    if not ctx().live:
                        break
                    self.visit(stmt)
                return
        ctx().test = test.ir()
        body_ctx = ctx().branch(None)
        else_ctx = ctx().branch(0)

        set_ctx(body_ctx)
        for stmt in node.body:
            if not ctx().live:
                break
            self.visit(stmt)
        ctx().branch_to_loop_header(header_ctx)

        self.loop_head_ctxs.pop()
        break_ctxs = self.break_ctxs.pop()

        set_ctx(else_ctx)
        for stmt in node.orelse:
            if not ctx().live:
                break
            self.visit(stmt)
        else_end_ctx = ctx()

        after_ctx = Context.meet([else_end_ctx, *break_ctxs])
        set_ctx(after_ctx)

    def visit_If(self, node):
        test = self.convert_to_boolean_num(node.test, self.visit(node.test))

        if test._is_py_():
            if test._as_py_():
                for stmt in node.body:
                    if not ctx().live:
                        break
                    self.visit(stmt)
            else:
                for stmt in node.orelse:
                    if not ctx().live:
                        break
                    self.visit(stmt)
            return

        ctx_init = ctx()
        ctx_init.test = test.ir()
        true_ctx = ctx_init.branch(None)
        false_ctx = ctx_init.branch(0)

        set_ctx(true_ctx)
        for stmt in node.body:
            if not ctx().live:
                break
            self.visit(stmt)
        true_end_ctx = ctx()

        set_ctx(false_ctx)
        for stmt in node.orelse:
            if not ctx().live:
                break
            self.visit(stmt)
        false_end_ctx = ctx()

        set_ctx(Context.meet([true_end_ctx, false_end_ctx]))

    def visit_With(self, node):
        raise NotImplementedError("With statements are not supported")

    def visit_AsyncWith(self, node):
        raise NotImplementedError("Async with statements are not supported")

    def visit_Match(self, node):
        subject = self.visit(node.subject)
        end_ctxs = []
        for case in node.cases:
            if not ctx().live:
                break
            true_ctx, false_ctx = self.handle_match_pattern(subject, case.pattern)
            if not true_ctx.live:
                set_ctx(false_ctx)
                continue
            set_ctx(true_ctx)
            guard = (
                self.convert_to_boolean_num(case.guard, self.visit(case.guard)) if case.guard else validate_value(True)
            )
            if guard._is_py_():
                if guard._as_py_():
                    for stmt in case.body:
                        if not ctx().live:
                            break
                        self.visit(stmt)
                    end_ctxs.append(ctx())
                else:
                    # Merge failing before the guard and failing now at the guard (which we know is guaranteed to fail)
                    false_ctx = Context.meet([ctx(), false_ctx])
            else:
                ctx().test = guard.ir()
                guard_true_ctx = ctx().branch(None)
                guard_false_ctx = ctx().branch(0)
                set_ctx(guard_true_ctx)
                for stmt in case.body:
                    if not ctx().live:
                        break
                    self.visit(stmt)
                end_ctxs.append(ctx())
                false_ctx = Context.meet([false_ctx, guard_false_ctx])
            set_ctx(false_ctx)
        end_ctxs.append(ctx())
        if end_ctxs:
            set_ctx(Context.meet(end_ctxs))

    def handle_match_pattern(self, subject: Value, pattern: ast.pattern) -> tuple[Context, Context]:
        from sonolus.script.internal.generic import validate_type_spec
        from sonolus.script.internal.tuple_impl import TupleImpl

        if not ctx().live:
            return ctx().into_dead(), ctx()

        match pattern:
            case ast.MatchValue(value=value):
                value = self.visit(value)
                test = self.convert_to_boolean_num(pattern, validate_value(subject == value))
                if test._is_py_():
                    if test._as_py_():
                        return ctx(), ctx().into_dead()
                    else:
                        return ctx().into_dead(), ctx()
                ctx_init = ctx()
                ctx_init.test = test.ir()
                true_ctx = ctx_init.branch(None)
                false_ctx = ctx_init.branch(0)
                return true_ctx, false_ctx
            case ast.MatchSingleton(value=value):
                match value:
                    case True:
                        raise NotImplementedError("Matching against True is not supported, use 1 instead")
                    case False:
                        raise NotImplementedError("Matching against False is not supported, use 0 instead")
                    case None:
                        test = validate_value(subject._is_py_() and subject._as_py_() is None)
                    case _:
                        raise NotImplementedError("Unsupported match singleton")
                ctx_init = ctx()
                ctx_init.test = test.ir()
                true_ctx = ctx_init.branch(None)
                false_ctx = ctx_init.branch(0)
                return true_ctx, false_ctx
            case ast.MatchSequence(patterns=patterns):
                target_len = len(patterns)
                if not (isinstance(subject, Sequence | TupleImpl)):
                    return ctx().into_dead(), ctx()
                length_test = self.convert_to_boolean_num(pattern, validate_value(_len(subject) == target_len))
                ctx_init = ctx()
                if not length_test._is_py_():
                    ctx_init.test = length_test.ir()
                    true_ctx = ctx_init.branch(None)
                    false_ctxs = [ctx_init.branch(0)]
                elif length_test._as_py_():
                    true_ctx = ctx_init
                    false_ctxs = []
                else:
                    return ctx().into_dead(), ctx()
                set_ctx(true_ctx)
                for i, subpattern in enumerate(patterns):
                    if not ctx().live:
                        break
                    value = self.handle_getitem(subpattern, subject, validate_value(i))
                    true_ctx, false_ctx = self.handle_match_pattern(value, subpattern)
                    false_ctxs.append(false_ctx)
                    set_ctx(true_ctx)
                return true_ctx, Context.meet(false_ctxs)
            case ast.MatchMapping():
                raise NotImplementedError("Match mappings are not supported")
            case ast.MatchClass(cls=cls, patterns=patterns, kwd_attrs=kwd_attrs, kwd_patterns=kwd_patterns):
                cls = self.visit(cls)
                if cls._is_py_() and cls._as_py_() in {_int, _float, _bool}:
                    raise TypeError("Instance check against int, float, or bool is not supported, use Num instead")
                cls = validate_type_spec(cls)
                if not isinstance(cls, type):
                    raise TypeError("Class is not a type")
                if not isinstance(subject, cls):
                    return ctx().into_dead(), ctx()
                if patterns:
                    if not hasattr(cls, "__match_args__"):
                        raise TypeError("Class does not support match patterns")
                    if len(cls.__match_args__) < len(patterns):
                        raise ValueError("Too many match patterns")
                    # kwd_attrs can't be mixed with patterns on the syntax level,
                    # so we can just set it like this since it's empty
                    kwd_attrs = cls.__match_args__[: len(patterns)]
                    kwd_patterns = patterns
                if kwd_attrs:
                    true_ctx = ctx()
                    false_ctxs = []
                    for attr, subpattern in zip(kwd_attrs, kwd_patterns, strict=False):
                        if not hasattr(subject, attr):
                            raise AttributeError(f"Object has no attribute {attr}")
                        value = self.handle_getattr(subpattern, subject, attr)
                        true_ctx, false_ctx = self.handle_match_pattern(value, subpattern)
                        false_ctxs.append(false_ctx)
                        set_ctx(true_ctx)
                    return true_ctx, Context.meet(false_ctxs)
                return ctx(), ctx().into_dead()
            case ast.MatchStar():
                raise NotImplementedError("Match stars are not supported")
            case ast.MatchAs(pattern=pattern, name=name):
                if pattern:
                    true_ctx, false_ctx = self.handle_match_pattern(subject, pattern)
                    if name:
                        true_ctx.scope.set_value(name, subject)
                    return true_ctx, false_ctx
                else:
                    if name:
                        ctx().scope.set_value(name, subject)
                    return ctx(), ctx().into_dead()
            case ast.MatchOr():
                true_ctxs = []
                assert pattern.patterns
                for subpattern in pattern.patterns:
                    if not ctx().live:
                        break
                    true_ctx, false_ctx = self.handle_match_pattern(subject, subpattern)
                    true_ctxs.append(true_ctx)
                    set_ctx(false_ctx)
                return Context.meet(true_ctxs), ctx()

    def visit_Raise(self, node):
        raise NotImplementedError("Raise statements are not supported")

    def visit_Try(self, node):
        raise NotImplementedError("Try statements are not supported")

    def visit_TryStar(self, node):
        raise NotImplementedError("Try* statements are not supported")

    def visit_Assert(self, node):
        self.handle_call(
            node, assert_true, self.visit(node.test), self.visit(node.msg) if node.msg else validate_value(None)
        )

    def visit_Import(self, node):
        raise NotImplementedError("Import statements are not supported")

    def visit_ImportFrom(self, node):
        raise NotImplementedError("Import statements are not supported")

    def visit_Global(self, node):
        raise NotImplementedError("Global statements are not supported")

    def visit_Nonlocal(self, node):
        raise NotImplementedError("Nonlocal statements are not supported")

    def visit_Expr(self, node):
        return self.visit(node.value)

    def visit_Pass(self, node):
        pass

    def visit_Break(self, node):
        self.break_ctxs[-1].append(ctx())
        set_ctx(ctx().into_dead())

    def visit_Continue(self, node):
        loop_head = self.loop_head_ctxs[-1]
        if isinstance(loop_head, list):
            loop_head.append(ctx())
        else:
            ctx().branch_to_loop_header(loop_head)
        set_ctx(ctx().into_dead())

    def visit_BoolOp(self, node) -> Value:
        match node.op:
            case ast.And():
                handler = self.handle_and
            case ast.Or():
                handler = self.handle_or
            case _:
                raise NotImplementedError(f"Unsupported bool operator {op_to_symbol[type(node.op)]}")

        if not node.values:
            raise ValueError("Bool operator requires at least one operand")
        if len(node.values) == 1:
            return self.visit(node.values[0])
        initial, *rest = node.values
        return handler(self.visit(initial), ast.copy_location(ast.BoolOp(op=node.op, values=rest), node))

    def visit_NamedExpr(self, node):
        value = self.visit(node.value)
        self.handle_assign(node.target, value)
        return value

    def visit_BinOp(self, node):
        lhs = self.visit(node.left)
        rhs = self.visit(node.right)
        op = bin_ops[type(node.op)]
        if lhs._is_py_() and rhs._is_py_():
            lhs_py = lhs._as_py_()
            rhs_py = rhs._as_py_()
            if isinstance(lhs_py, type) and isinstance(rhs_py, type):
                return validate_value(getattr(lhs_py, op)(rhs_py))
        if hasattr(lhs, op):
            result = self.handle_call(node, getattr(lhs, op), rhs)
            if not self.is_not_implemented(result):
                return result
        if hasattr(rhs, rbin_ops[type(node.op)]) and type(lhs) is not type(rhs):
            result = self.handle_call(node, getattr(rhs, rbin_ops[type(node.op)]), lhs)
            if not self.is_not_implemented(result):
                return result
        raise TypeError(
            f"unsupported operand type(s) for {op_to_symbol[type(node.op)]}: "
            f"'{type(lhs).__name__}' and '{type(rhs).__name__}'"
        )

    def visit_UnaryOp(self, node):
        operand = self.visit(node.operand)
        if isinstance(node.op, ast.Not):
            return self.convert_to_boolean_num(node, operand).not_()
        op = unary_ops[type(node.op)]
        if operand._is_py_():
            operand_py = operand._as_py_()
            if isinstance(operand_py, type) and hasattr(type(operand_py), op):
                return self.handle_call(node, getattr(type(operand_py), op), operand_py)
        if hasattr(operand, op):
            return self.handle_call(node, getattr(operand, op))
        raise TypeError(f"bad operand type for unary {op_to_symbol[type(node.op)]}: '{type(operand).__name__}'")

    def visit_Lambda(self, node):
        signature = self.arguments_to_signature(node.args)

        def fn(*args, **kwargs):
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            return Visitor(
                self.source_file,
                bound,
                self.globals,
                self,
            ).run(node)

        fn._meta_fn_ = True
        fn.__name__ = "<lambda>"

        return validate_value(fn)

    def visit_IfExp(self, node):
        test = self.convert_to_boolean_num(node.test, self.visit(node.test))

        if test._is_py_():
            if test._as_py_():
                return self.visit(node.body)
            else:
                return self.visit(node.orelse)

        res_name = self.new_name("ifexp")
        ctx_init = ctx()
        ctx_init.test = test.ir()

        set_ctx(ctx_init.branch(None))
        true_value = self.visit(node.body)
        ctx().scope.set_value(res_name, true_value)
        ctx_true = ctx()

        set_ctx(ctx_init.branch(0))
        false_value = self.visit(node.orelse)
        ctx().scope.set_value(res_name, false_value)
        ctx_false = ctx()

        set_ctx(Context.meet([ctx_true, ctx_false]))
        return ctx().scope.get_value(res_name)

    def visit_Dict(self, node):
        return validate_value({self.visit(k): self.visit(v) for k, v in zip(node.keys, node.values, strict=True)})

    def visit_Set(self, node):
        raise NotImplementedError("Set literals are not supported")

    def visit_ListComp(self, node):
        raise NotImplementedError("List comprehensions are not supported")

    def visit_SetComp(self, node):
        raise NotImplementedError("Set comprehensions are not supported")

    def visit_DictComp(self, node):
        raise NotImplementedError("Dict comprehensions are not supported")

    def visit_GeneratorExp(self, node):
        raise NotImplementedError("Generator expressions are not supported")

    def visit_Await(self, node):
        raise NotImplementedError("Await expressions are not supported")

    def visit_Yield(self, node):
        raise NotImplementedError("Yield expressions are not supported")

    def visit_YieldFrom(self, node):
        raise NotImplementedError("Yield from expressions are not supported")

    def _has_real_method(self, obj: Value, method_name: str) -> bool:
        return hasattr(obj, method_name) and not isinstance(getattr(obj, method_name), MethodWrapperType)

    def visit_Compare(self, node):
        result_name = self.new_name("compare")
        ctx().scope.set_value(result_name, Num._accept_(0))
        l_val = self.visit(node.left)
        false_ctxs = []
        for i, (op, rhs) in enumerate(zip(node.ops, node.comparators, strict=True)):
            r_val = self.visit(rhs)
            inverted = isinstance(op, ast.NotIn)
            result = None
            if isinstance(op, ast.Is | ast.IsNot):
                if not (r_val._is_py_() and r_val._as_py_() is None):
                    raise TypeError("The right operand of 'is' must be None")
                if isinstance(op, ast.Is):
                    result = Num._accept_(l_val._is_py_() and l_val._as_py_() is None)
                else:
                    result = Num._accept_(not (l_val._is_py_() and l_val._as_py_() is None))
            elif type(op) in comp_ops and self._has_real_method(l_val, comp_ops[type(op)]):
                result = self.handle_call(node, getattr(l_val, comp_ops[type(op)]), r_val)
            if (
                (result is None or self.is_not_implemented(result))
                and type(op) in rcomp_ops
                and self._has_real_method(r_val, rcomp_ops[type(op)])
            ):
                result = self.handle_call(node, getattr(r_val, rcomp_ops[type(op)]), l_val)
            if result is None or self.is_not_implemented(result):
                # Can't defer to the default object.__eq__ or similar since reference equality (is) is not reliable
                if type(op) is ast.Eq and type(l_val) is not type(r_val):
                    return Num._accept_(False)
                elif type(op) is ast.NotEq and type(l_val) is not type(r_val):
                    return Num._accept_(True)
                else:
                    raise TypeError(
                        f"'{op_to_symbol[type(op)]}' not supported between instances of '{type(l_val).__name__}' and "
                        f"'{type(r_val).__name__}'"
                    )
            result = self.ensure_boolean_num(result)
            if inverted:
                result = result.not_()
            curr_ctx = ctx()
            if i == len(node.ops) - 1:
                curr_ctx.scope.set_value(result_name, result)
            elif result._is_py_():
                if result._as_py_():
                    l_val = r_val
                else:
                    false_ctxs.append(curr_ctx)
                    set_ctx(curr_ctx.into_dead())
                    break
            else:
                curr_ctx.test = result.ir()
                true_ctx = curr_ctx.branch(None)
                false_ctx = curr_ctx.branch(0)
                false_ctxs.append(false_ctx)
                set_ctx(true_ctx)
                l_val = r_val
        last_ctx = ctx()  # This is the result of the last comparison returning true
        set_ctx(Context.meet([last_ctx, *false_ctxs]))
        return ctx().scope.get_value(result_name)

    def visit_Call(self, node):
        from sonolus.script.internal.dict_impl import DictImpl

        fn = self.visit(node.func)
        args = []
        kwargs = {}
        for arg in node.args:
            if isinstance(arg, ast.Starred):
                args.extend(self.handle_starred(self.visit(arg.value)))
            else:
                args.append(self.visit(arg))
        for keyword in node.keywords:
            if keyword.arg:
                kwargs[keyword.arg] = self.visit(keyword.value)
            else:
                value = self.visit(keyword.value)
                if isinstance(value, DictImpl):
                    if not all(isinstance(k, str) for k in value.value):
                        raise ValueError("Keyword arguments must be strings")
                    kwargs.update(value.value)
                else:
                    raise ValueError("Starred keyword arguments (**kwargs) must be dictionaries")
        return self.handle_call(node, fn, *args, **kwargs)

    def visit_FormattedValue(self, node):
        raise NotImplementedError("F-strings are not supported")

    def visit_JoinedStr(self, node):
        raise NotImplementedError("F-strings are not supported")

    def visit_Constant(self, node):
        return validate_value(node.value)

    def visit_Attribute(self, node):
        return self.handle_getattr(node, self.visit(node.value), node.attr)

    def visit_Subscript(self, node):
        value = self.visit(node.value)
        slice_value = self.visit(node.slice)
        return self.handle_getitem(node, value, slice_value)

    def visit_Starred(self, node):
        raise NotImplementedError("Starred expressions are not supported")

    def visit_Name(self, node):
        self.active_ctx = ctx()
        v = self
        while v:
            if not isinstance(v.active_ctx.scope.get_binding(node.id), EmptyBinding):
                return v.active_ctx.scope.get_value(node.id)
            v = v.parent
        if node.id in self.globals:
            value = self.globals[node.id]
            if value is ctx:
                raise ValueError("Unexpected use of ctx in non meta-function")
            return validate_value(BUILTIN_IMPLS.get(id(value), value))
        raise NameError(f"Name {node.id} is not defined")

    def visit_List(self, node):
        raise NotImplementedError("List literals are not supported")

    def visit_Tuple(self, node):
        values = []
        for elt in node.elts:
            if isinstance(elt, ast.Starred):
                values.extend(self.handle_starred(self.visit(elt.value)))
            else:
                values.append(self.visit(elt))
        return validate_value(tuple(values))

    def visit_Slice(self, node):
        raise NotImplementedError("Slices are not supported")

    def handle_assign(self, target: ast.stmt | ast.expr, value: Value):
        match target:
            case ast.Name(id=name):
                ctx().scope.set_value(name, value)
            case ast.Attribute(value=attr_value, attr=attr):
                attr_value = self.visit(attr_value)
                self.handle_setattr(target, attr_value, attr, value)
            case ast.Subscript(value=sub_value, slice=slice_expr):
                sub_value = self.visit(sub_value)
                slice_value = self.visit(slice_expr)
                self.handle_setitem(target, sub_value, slice_value, value)
            case ast.Tuple(elts=elts) | ast.List(elts=elts):
                values = self.handle_starred(value)
                if len(elts) != len(values):
                    raise ValueError("Unpacking assignment requires the same number of elements")
                for elt, v in zip(elts, values, strict=False):
                    self.handle_assign(elt, validate_value(v))
            case ast.Starred():
                raise NotImplementedError("Starred assignment is not supported")
            case _:
                raise NotImplementedError("Unsupported assignment target")

    def handle_and(self, l_val: Value, r_expr: ast.expr) -> Value:
        ctx_init = ctx()
        l_val = self.ensure_boolean_num(l_val)

        if l_val._is_py_():
            if l_val._as_py_():
                # The rhs is definitely evaluated, so we can return it directly
                return self.ensure_boolean_num(self.visit(r_expr))
            else:
                return l_val

        ctx_init.test = l_val.ir()
        res_name = self.new_name("and")

        set_ctx(ctx_init.branch(None))
        r_val = self.ensure_boolean_num(self.visit(r_expr))
        ctx().scope.set_value(res_name, r_val)
        ctx_true = ctx()

        set_ctx(ctx_init.branch(0))
        ctx().scope.set_value(res_name, Num._accept_(0))
        ctx_false = ctx()

        set_ctx(Context.meet([ctx_true, ctx_false]))
        if l_val._is_py_() and r_val._is_py_():
            return Num._accept_(l_val._as_py_() and r_val._as_py_())
        return ctx().scope.get_value(res_name)

    def handle_or(self, l_val: Value, r_expr: ast.expr) -> Value:
        ctx_init = ctx()
        l_val = self.ensure_boolean_num(l_val)

        if l_val._is_py_():
            if l_val._as_py_():
                return l_val
            else:
                # The rhs is definitely evaluated, so we can return it directly
                return self.ensure_boolean_num(self.visit(r_expr))

        ctx_init.test = l_val.ir()
        res_name = self.new_name("or")

        set_ctx(ctx_init.branch(None))
        ctx().scope.set_value(res_name, l_val)
        ctx_true = ctx()

        set_ctx(ctx_init.branch(0))
        r_val = self.ensure_boolean_num(self.visit(r_expr))
        ctx().scope.set_value(res_name, r_val)
        ctx_false = ctx()

        set_ctx(Context.meet([ctx_true, ctx_false]))
        if l_val._is_py_() and r_val._is_py_():
            return Num._accept_(l_val._as_py_() or r_val._as_py_())
        return ctx().scope.get_value(res_name)

    def generic_visit(self, node):
        if isinstance(node, ast.stmt | ast.expr):
            with self.reporting_errors_at_node(node):
                raise NotImplementedError(f"Unsupported syntax: {type(node).__name__}")
        raise NotImplementedError(f"Unsupported syntax: {type(node).__name__}")

    def handle_getattr(self, node: ast.stmt | ast.expr, target: Value, key: str) -> Value:
        with self.reporting_errors_at_node(node):
            if isinstance(target, ConstantValue):
                # Unwrap so we can access fields
                target = target._as_py_()
            descriptor = None
            for cls in type.mro(type(target)):
                descriptor = cls.__dict__.get(key, None)
                if descriptor is not None:
                    break
            match descriptor:
                case property(fget=getter):
                    return self.handle_call(node, getter, target)
                case SonolusDescriptor() | FunctionType() | classmethod() | staticmethod() | None:
                    return validate_value(getattr(target, key))
                case non_descriptor if not hasattr(non_descriptor, "__get__"):
                    return validate_value(getattr(target, key))
                case _:
                    raise TypeError(f"Unsupported field or descriptor {key}")

    def handle_setattr(self, node: ast.stmt | ast.expr, target: Value, key: str, value: Value):
        with self.reporting_errors_at_node(node):
            if target._is_py_():
                target = target._as_py_()
            descriptor = getattr(type(target), key, None)
            match descriptor:
                case property(fset=setter):
                    if setter is None:
                        raise AttributeError(f"Cannot set attribute {key} because property has no setter")
                    self.handle_call(node, setter, target, value)
                case SonolusDescriptor():
                    setattr(target, key, value)
                case _:
                    raise TypeError(f"Unsupported field or descriptor {key}")

    def handle_call[**P, R](
        self, node: ast.stmt | ast.expr, fn: Callable[P, R], /, *args: P.args, **kwargs: P.kwargs
    ) -> R | Value:
        """Handles a call to the given callable."""
        self.active_ctx = ctx()
        if (
            isinstance(fn, Value)
            and fn._is_py_()
            and isinstance(fn._as_py_(), type)
            and issubclass(fn._as_py_(), Value)
        ):
            return validate_value(self.execute_at_node(node, fn._as_py_(), *args, **kwargs))
        else:
            return self.execute_at_node(node, lambda: validate_value(compile_and_call(fn, *args, **kwargs)))

    def handle_getitem(self, node: ast.stmt | ast.expr, target: Value, key: Value) -> Value:
        with self.reporting_errors_at_node(node):
            if target._is_py_() and isinstance(target._as_py_(), type):
                if not key._is_py_():
                    raise ValueError("Type parameters must be compile-time constants")
                return validate_value(target._as_py_()[key._as_py_()])
            else:
                if isinstance(target, Value) and hasattr(target, "__getitem__"):
                    return self.handle_call(node, target.__getitem__, key)
                raise TypeError(f"Cannot get items on {type(target).__name__}")

    def handle_setitem(self, node: ast.stmt | ast.expr, target: Value, key: Value, value: Value):
        with self.reporting_errors_at_node(node):
            if isinstance(target, Value) and hasattr(target, "__setitem__"):
                return self.handle_call(node, target.__setitem__, key, value)
            raise TypeError(f"Cannot set items on {type(target).__name__}")

    def handle_delitem(self, node: ast.stmt | ast.expr, target: Value, key: Value):
        with self.reporting_errors_at_node(node):
            if isinstance(target, Value) and hasattr(target, "__delitem__"):
                return self.handle_call(node, target.__delitem__, key)
            raise TypeError(f"Cannot delete items on {type(target).__name__}")

    def handle_starred(self, value: Value) -> tuple[Value, ...]:
        if isinstance(value, TupleImpl):
            return value.value
        raise ValueError("Unsupported starred expression")

    def is_not_implemented(self, value):
        value = validate_value(value)
        return value._is_py_() and value._as_py_() is NotImplemented

    def ensure_boolean_num(self, value) -> Num:
        # This just checks the type for now, although we could support custom __bool__ implementations in the future
        if not _is_num(value):
            raise TypeError(f"Invalid type where a bool (Num) was expected: {type(value).__name__}")
        return value

    def convert_to_boolean_num(self, node, value: Value) -> Num:
        if _is_num(value):
            return value
        if hasattr(type(value), "__bool__"):
            return self.ensure_boolean_num(self.handle_call(node, type(value).__bool__, validate_value(value)))
        if hasattr(type(value), "__len__"):
            length = self.handle_call(node, type(value).__len__, validate_value(value))
            if not _is_num(length):
                raise TypeError(f"Invalid type for __len__: {type(length).__name__}")
            if length._is_py_():
                return Num._accept_(length._as_py_() > 0)
            return length > Num._accept_(0)
        raise TypeError(f"Converting {type(value).__name__} to bool is not supported")

    def arguments_to_signature(self, arguments: ast.arguments) -> inspect.Signature:
        parameters: list[inspect.Parameter] = []
        pos_only_count = len(arguments.posonlyargs)
        for i, arg in enumerate(arguments.posonlyargs):
            default_idx = i - pos_only_count + len(arguments.defaults)
            default = self.visit(arguments.defaults[default_idx]) if default_idx >= 0 else None
            param = inspect.Parameter(
                name=arg.arg,
                kind=inspect.Parameter.POSITIONAL_ONLY,
                default=default if default_idx >= 0 else inspect.Parameter.empty,
                annotation=inspect.Parameter.empty,
            )
            parameters.append(param)

        pos_kw_count = len(arguments.args)
        for i, arg in enumerate(arguments.args):
            default_idx = i - pos_kw_count + len(arguments.defaults)
            default = self.visit(arguments.defaults[default_idx]) if default_idx >= 0 else None
            param = inspect.Parameter(
                name=arg.arg,
                kind=inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=default if default_idx >= 0 else inspect.Parameter.empty,
                annotation=inspect.Parameter.empty,
            )
            parameters.append(param)

        if arguments.vararg:
            param = inspect.Parameter(
                name=arguments.vararg.arg,
                kind=inspect.Parameter.VAR_POSITIONAL,
                default=inspect.Parameter.empty,
                annotation=inspect.Parameter.empty,
            )
            parameters.append(param)

        for i, arg in enumerate(arguments.kwonlyargs):
            default = self.visit(arguments.kw_defaults[i]) if arguments.kw_defaults[i] is not None else None
            param = inspect.Parameter(
                name=arg.arg,
                kind=inspect.Parameter.KEYWORD_ONLY,
                default=default if default is not None else inspect.Parameter.empty,
                annotation=inspect.Parameter.empty,
            )
            parameters.append(param)

        if arguments.kwarg:
            param = inspect.Parameter(
                name=arguments.kwarg.arg,
                kind=inspect.Parameter.VAR_KEYWORD,
                default=inspect.Parameter.empty,
                annotation=inspect.Parameter.empty,
            )
            parameters.append(param)

        return inspect.Signature(parameters)

    def raise_exception_at_node(self, node: ast.stmt | ast.expr, cause: Exception) -> Never:
        """Throws a compilation error at the given node."""

        def thrower() -> Never:
            raise CompilationError(str(cause)) from cause

        self.execute_at_node(node, thrower)

    def execute_at_node[**P, R](
        self, node: ast.stmt | ast.expr, fn: Callable[P, R], /, *args: P.args, **kwargs: P.kwargs
    ) -> R:
        """Executes the given function at the given node for a better traceback."""
        location_args = {
            "lineno": node.lineno,
            "col_offset": node.col_offset,
            "end_lineno": node.end_lineno,
            "end_col_offset": node.end_col_offset,
        }

        expr = ast.Expression(
            body=ast.Call(
                func=ast.Name(id="fn", ctx=ast.Load(), **location_args),
                args=[
                    ast.Starred(
                        value=ast.Name(id="args", ctx=ast.Load(), **location_args),
                        ctx=ast.Load(),
                        **location_args,
                    )
                ],
                keywords=[
                    ast.keyword(
                        value=ast.Name(id="kwargs", ctx=ast.Load(), **location_args),
                        arg=None,
                        **location_args,
                    )
                ],
                **location_args,
            )
        )
        return eval(
            compile(expr, filename=self.source_file, mode="eval"),
            {"fn": fn, "args": args, "kwargs": kwargs, "_filter_traceback_": True},
        )

    def reporting_errors_at_node(self, node: ast.stmt | ast.expr):
        return ReportingErrorsAtNode(self, node)

    def new_name(self, name: str):
        self.used_names[name] = self.used_names.get(name, 0) + 1
        return f"${name}_{self.used_names[name]}"


# Not using @contextmanager so it doesn't end up in tracebacks
class ReportingErrorsAtNode:
    def __init__(self, compiler, node: ast.stmt | ast.expr):
        self.compiler = compiler
        self.node = node

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            return

        if issubclass(exc_type, CompilationError):
            raise exc_value from exc_value.__cause__

        if exc_value is not None:
            self.compiler.raise_exception_at_node(self.node, exc_value)
