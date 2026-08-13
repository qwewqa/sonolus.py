from __future__ import annotations

import ast
import builtins
import functools
import inspect
import os
from collections import ChainMap
from collections.abc import Callable, Iterable, Sequence
from inspect import ismethod
from time import perf_counter_ns
from types import FunctionType, MethodType, MethodWrapperType
from typing import Any, Never

from sonolus.backend.excepthook import install_excepthook
from sonolus.backend.utils import get_function, get_signature, scan_writes
from sonolus.script.debug import assert_true, error, require
from sonolus.script.internal.builtin_impls import (
    BUILTIN_IMPL_NAMES,
    BUILTIN_IMPLS,
    _bool,
    _float,
    _int,
    _len,
    _super,
    _type_name,
    _validate_len_result,
    contains_by_iteration,
    name_builtin_in_binding_error,
)
from sonolus.script.internal.constant import ConstantValue
from sonolus.script.internal.context import (
    Binding,
    ConflictBinding,
    Context,
    EmptyBinding,
    FunctionVisitStatistics,
    RuntimeChecks,
    Scope,
    ValueBinding,
    ctx,
    set_ctx,
    using_ctx,
)
from sonolus.script.internal.descriptor import SonolusDescriptor
from sonolus.script.internal.error import CompilationError, caused_by_attribute_error
from sonolus.script.internal.impl import bind_arguments, validate_value
from sonolus.script.internal.meta_fn import meta_fn
from sonolus.script.internal.transient import TransientValue
from sonolus.script.internal.tuple_impl import TupleImpl, has_tuple_iter, tuple_iter
from sonolus.script.internal.value import Value
from sonolus.script.iterator import SonolusIterator
from sonolus.script.maybe import Maybe, Nothing, Some
from sonolus.script.num import Num, _is_num
from sonolus.script.record import Record

_compiler_internal_ = True


def compile_and_call[**P, R](fn: Callable[P, R], /, *args: P.args, **kwargs: P.kwargs) -> R:
    if not ctx():
        return fn(*args, **kwargs)
    if type(fn) is FunctionType and not fn.__dict__.get("_meta_fn_", False):
        # Fast path equivalent to the FunctionType arm of generate_fn_impl.
        return validate_value(eval_fn(fn, *args, **kwargs))
    try:
        if getattr(fn, "_meta_fn_", False):
            return validate_value(fn(*args, **kwargs))
        return validate_value(generate_fn_impl(fn)(*args, **kwargs))
    except TypeError as e:
        name_builtin_in_binding_error(fn, e, args)
        raise


def compile_and_call_at_definition[**P, R](fn: Callable[P, R], /, *args: P.args, **kwargs: P.kwargs) -> R:
    if not ctx():
        return fn(*args, **kwargs)
    if getattr(fn, "_meta_fn_", False):
        return validate_value(fn(*args, **kwargs))
    source_file, node = get_function(fn)
    if ctx().no_eval:
        debug_stack = ctx().callback_state.debug_stack
        try:
            debug_stack.append(f'File "{source_file}", line {node.lineno}, in <callback>')
            return compile_and_call(fn, *args, **kwargs)
        finally:
            debug_stack.pop()
    location_args = {
        "lineno": node.lineno,
        "col_offset": node.col_offset,
        "end_lineno": node.lineno,
        "end_col_offset": node.col_offset + 1,
    }
    expr = ast.Expression(
        body=ast.Call(
            func=ast.Name(id="fn", ctx=ast.Load(), **location_args),
            args=[],
            keywords=[],
            **location_args,
        )
    )

    debug_stack = ctx().callback_state.debug_stack
    try:
        debug_stack.append(f'File "{source_file}", line {node.lineno}, in <callback>')
        return eval(
            compile(expr, filename=source_file, mode="eval").replace(co_name="<callback>"),
            {"fn": lambda: compile_and_call(fn, *args, **kwargs), "_filter_traceback_": True, "_traceback_root_": True},
        )
    finally:
        debug_stack.pop()


@functools.cache
def _ensure_excepthook() -> None:
    install_excepthook()


def generate_fn_impl(fn: Callable):
    _ensure_excepthook()
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
            comptime_marker = inspect.getattr_static(fn, "_is_comptime_value_", False)
            if callable(fn) and (isinstance(fn, Value) or bool(comptime_marker)):
                call_method = _bind_special_method(fn, "__call__")
                if call_method is not _SPECIAL_METHOD_MISSING:
                    return generate_fn_impl(call_method)
            elif isinstance(fn, type):
                raise TypeError(f"Calling class '{fn.__name__}' is not supported")
            elif callable(fn):
                raise TypeError(f"Calling objects of type '{_type_name(fn)}' is not supported")
            else:
                raise TypeError(f"'{_type_name(fn)}' object is not callable")


def _compute_fn_info(fn: Callable) -> tuple[str, str, ChainMap]:
    function_name = getattr(fn, "__name__", "<unnamed>")
    module = getattr(fn, "__module__", None)
    if module is not None:
        qualified_name = f"{module}.{getattr(fn, '__qualname__', function_name)}"
    else:
        qualified_name = f"<unknown>.{getattr(fn, '__qualname__', function_name)}"
    return function_name, qualified_name, ChainMap(fn.__globals__, builtins.__dict__)


# Only cache plain functions; bound methods are ephemeral and would leak cache entries.
_get_fn_info = functools.cache(_compute_fn_info)

_POSITIONAL_PARAM_KINDS = (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)


def _compute_fast_binder(fn: Callable):
    """Precompute a positional binder for `fn`, or None if its signature is not all-positional.

    For an all-positional signature called without kwargs, ``sig.bind(*args).apply_defaults()``
    produces exactly ``dict(zip(names, args))`` extended with the trailing parameter defaults
    (apply_defaults builds the arguments dict in parameter order). Signatures with
    *args/**kwargs/keyword-only parameters return None and keep using inspect's bind.
    """
    sig = get_signature(fn)
    params = list(sig.parameters.values())
    if not all(p.kind in _POSITIONAL_PARAM_KINDS for p in params):
        return None
    names = tuple(p.name for p in params)
    defaults = tuple(p.default for p in params)
    n_required = sum(1 for p in params if p.default is inspect.Parameter.empty)
    return names, defaults, len(params), n_required


_get_fast_binder = functools.cache(_compute_fast_binder)


def eval_fn(fn: Callable, /, *args, **kwargs):
    _ensure_excepthook()
    source_file, node = get_function(fn)
    if type(fn) is FunctionType:
        sig = get_signature(fn)
        function_name, qualified_name, global_base = _get_fn_info(fn)
        binder = _get_fast_binder(fn)
    else:
        sig = inspect.signature(fn)
        function_name, qualified_name, global_base = _compute_fn_info(fn)
        binder = None
    if binder is not None and not kwargs and binder[3] <= len(args) <= binder[2]:
        names, defaults, n_params, _n_required = binder
        n = len(args)
        arguments = dict(zip(names, args, strict=False))
        for i in range(n, n_params):
            arguments[names[i]] = defaults[i]
        bound_args = inspect.BoundArguments(sig, arguments)
    else:
        bound_args = bind_arguments(sig, getattr(fn, "__qualname__", function_name), args, kwargs)
        bound_args.apply_defaults()
    if ismethod(fn):
        code = fn.__func__.__code__
        closure = fn.__func__.__closure__
    else:
        code = fn.__code__
        closure = fn.__closure__
    if closure is None:
        global_vars = global_base
    else:
        nonlocal_vars = {
            var: cell.cell_contents for var, cell in zip(code.co_freevars, closure, strict=True) if cell is not None
        }
        global_vars = ChainMap(nonlocal_vars, *global_base.maps)
    return Visitor(
        source_file, bound_args, global_vars, parent=None, function_name=function_name, qualified_name=qualified_name
    ).run(node)


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
    ast.MatMult: "@",
}

_EQ_OP = ast.Eq()


def _is_strict_subclass(lhs: Value, rhs: Value) -> bool:
    lhs_type = type(lhs)
    rhs_type = type(rhs)
    return lhs_type is not rhs_type and lhs_type in rhs_type.__mro__[1:]


def _has_strict_subclass_reflected_priority(lhs: Value, rhs: Value, reflected_name: str) -> bool:
    lhs_type = type(lhs)
    rhs_type = type(rhs)
    return _is_strict_subclass(lhs, rhs) and getattr(rhs_type, reflected_name, None) is not getattr(
        lhs_type, reflected_name, None
    )


_NOT_IMPLEMENTED = object()
_SPECIAL_METHOD_MISSING = object()


def _raw_special_method(cls: type, name: str) -> Any:
    for base in type.__getattribute__(cls, "__mro__"):  # noqa: PLC2801 - bypass metaclass hooks
        namespace = type.__getattribute__(base, "__dict__")  # noqa: PLC2801 - bypass metaclass hooks
        if name in namespace:
            return namespace[name]
    return _SPECIAL_METHOD_MISSING


def _bind_special_method(value: Any, name: str) -> Any:
    descriptor = _raw_special_method(type(value), name)
    if descriptor is _SPECIAL_METHOD_MISSING:
        return descriptor
    descriptor_get = _raw_special_method(type(descriptor), "__get__")
    return (
        descriptor_get(descriptor, value, type(value)) if descriptor_get is not _SPECIAL_METHOD_MISSING else descriptor
    )


def _comptime_binop(lhs: Any, rhs: Any, op: str, reflected_op: str) -> Any:
    lhs_type = type(lhs)
    rhs_type = type(rhs)
    lhs_descriptor = _raw_special_method(lhs_type, op)
    rhs_descriptor = _raw_special_method(rhs_type, reflected_op)
    lhs_reflected_descriptor = _raw_special_method(lhs_type, reflected_op)
    right_has_priority = lhs_type in rhs_type.__mro__[1:] and (
        rhs_descriptor is not lhs_reflected_descriptor or isinstance(rhs_descriptor, classmethod)
    )
    if right_has_priority and rhs_descriptor is not _SPECIAL_METHOD_MISSING:
        result = _bind_special_method(rhs, reflected_op)(lhs)
        if result is not NotImplemented:
            return result
    if lhs_descriptor is not _SPECIAL_METHOD_MISSING:
        result = _bind_special_method(lhs, op)(rhs)
        if result is not NotImplemented:
            return result
    if not right_has_priority and rhs_descriptor is not _SPECIAL_METHOD_MISSING and lhs_type is not rhs_type:
        result = _bind_special_method(rhs, reflected_op)(lhs)
        if result is not NotImplemented:
            return result
    return _NOT_IMPLEMENTED


def _comptime_augassign(lhs: Any, rhs: Any, inplace_op: str, op: str, reflected_op: str) -> Any:
    inplace_descriptor = _raw_special_method(type(lhs), inplace_op)
    if inplace_descriptor is not _SPECIAL_METHOD_MISSING:
        result = _bind_special_method(lhs, inplace_op)(rhs)
        if result is not NotImplemented:
            return result
    return _comptime_binop(lhs, rhs, op, reflected_op)


# Binary operator method names implemented by Num. For two Num operands, these never return NotImplemented,
# so the NotImplemented negotiation protocol can be skipped as a fast path.
_NUM_BIN_OP_NAMES = frozenset(
    {
        "__add__",
        "__sub__",
        "__mul__",
        "__truediv__",
        "__floordiv__",
        "__mod__",
        "__pow__",
    }
)

# Type-keyed dispatch cache for Visitor.visit. AST node classes are a small closed set.
_VISITOR_DISPATCH: dict[type, Callable] = {}

_ACTIVE_VISITORS = []

# Cache of resolved descriptors (or None) keyed by (type, attribute name) for handle_getattr/handle_setattr.
# Within a single build session, classes and their signatures are assumed immutable, so a resolved
# descriptor stays valid for the whole build. Across build sessions a class may be redefined or a
# property monkeypatched, so this cache (together with get_signature and _get_fn_info) is reset once
# per build via clear_frontend_caches().
_descriptor_cache: dict[tuple[type, str], Any] = {}
_DESCRIPTOR_CACHE_MISS = object()


def clear_frontend_caches() -> None:
    """Reset the process-lifetime frontend caches that assume within-build immutability."""
    _get_fn_info.cache_clear()
    _get_fast_binder.cache_clear()
    get_signature.cache_clear()
    _descriptor_cache.clear()


def reject_instance_only_attribute(cls: type, key: str, attribute: Any) -> None:
    """Reject accessing `key` on the class object when `attribute` is the descriptor itself."""
    if attribute is not _resolve_descriptor(cls, key):
        return
    match attribute:
        case property():
            raise TypeError(f"Property '{key}' must be accessed on an instance of {cls.__name__}")
        case SonolusDescriptor():
            raise TypeError(f"Field '{key}' must be accessed on an instance of {cls.__name__}")


def _resolve_descriptor(target_type: type, key: str) -> Any:
    """Resolve `key` to the first descriptor found on target_type's MRO (or None), with caching."""
    descriptor = _descriptor_cache.get((target_type, key), _DESCRIPTOR_CACHE_MISS)
    if descriptor is _DESCRIPTOR_CACHE_MISS:
        descriptor = None
        for cls in type.mro(target_type):
            if key in cls.__dict__:
                descriptor = cls.__dict__[key]
                break
        _descriptor_cache[target_type, key] = descriptor
    return descriptor


# Visit-time statistics are for diagnostics only (see print_visit_stats in sonolus/build/engine.py),
# so they're gated behind an environment variable to avoid the bookkeeping overhead in normal builds.
VISIT_STATS_ENABLED = os.environ.get("SONOLUS_VISIT_STATS") == "1"


def record_visit_time(function_name: str, total_time: int, own_time: int) -> None:
    visit_stats = ctx().project_state.visit_stats
    if function_name not in visit_stats:
        visit_stats[function_name] = stats = FunctionVisitStatistics()
    else:
        stats = visit_stats[function_name]
    stats.total_time += total_time
    stats.own_time += own_time
    stats.call_count += 1
    ctx().callback_state.visitor_own_time += own_time


_SCOPE_DECLARATION_MESSAGES = {
    ast.Global: "Global statements are not supported",
    ast.Nonlocal: "Nonlocal statements are not supported",
}
_NESTED_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
_UNSCANNED = object()
# Only a user property or __len__ read by a sequence or class sub-pattern can terminate mid-pattern. Contexts
# the pattern has already opened may still be live with no continuation left to give them, so the pattern is
# rejected rather than traced on.
_TERMINATING_MATCH_READ_MESSAGE = "A call that terminates on every path is not supported inside a match pattern"


def _find_scope_declaration(node: ast.FunctionDef) -> ast.Global | ast.Nonlocal | None:
    """Return the first `global` or `nonlocal` statement in this function's own body, or None."""
    found = getattr(node, "scope_declaration", _UNSCANNED)
    if found is _UNSCANNED:
        found = _scan_scope_declaration(node)
        node.scope_declaration = found
    return found


def _scan_scope_declaration(node: ast.AST) -> ast.Global | ast.Nonlocal | None:
    for child in ast.iter_child_nodes(node):
        if isinstance(child, _NESTED_SCOPE_NODES):
            # A nested scope declares for itself, and is scanned when it is traced.
            continue
        if isinstance(child, ast.Global | ast.Nonlocal):
            return child
        found = _scan_scope_declaration(child)
        if found is not None:
            return found
    return None


def _exception_message(cause: Exception) -> str:
    """The message of `cause`, without the quotes KeyError's str() puts around it."""
    if type(cause) is KeyError and cause.args:
        return str(cause.args[0])
    return str(cause)


def _raise_property_getter_attribute_error(target_type: type, key: str, error: Exception) -> Never:
    message = f"The getter for property {key!r} on {target_type.__name__} raised AttributeError during compilation"
    if detail := _exception_message(error):
        message = f"{message}: {detail}"
    raise NotImplementedError(message) from error


def _raise_getattr_attribute_error(target_type: type, key: str, error: Exception) -> Never:
    message = f"{target_type.__name__}.__getattr__ raised AttributeError while looking up {key!r} during compilation"
    if detail := _exception_message(error):
        message = f"{message}: {detail}"
    raise NotImplementedError(message) from error


def _attribute_owner_name(target: Any) -> str:
    return target.__name__ if isinstance(target, type) else type(target).__name__


def reject_custom_record_getattribute(target: Any) -> None:
    if (
        isinstance(target, Record)
        and _raw_special_method(type(target), "__getattribute__") is not object.__getattribute__
    ):
        raise NotImplementedError(
            f"{type(target).__name__} overrides __getattribute__, which is not supported for Record subclasses"
        )


def _noop_mark_end() -> None:
    pass


def mark_start(function_name: str) -> Callable[[], None]:
    if not VISIT_STATS_ENABLED:
        return _noop_mark_end
    start_time = perf_counter_ns()
    start_own_time = ctx().callback_state.visitor_own_time

    def mark_end():
        total_time = perf_counter_ns() - start_time
        record_visit_time(
            function_name, total_time, total_time - (ctx().callback_state.visitor_own_time - start_own_time)
        )

    return mark_end


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
    yield_ctxs: list[Context]  # Contexts at yield statements, which will branch to the exit
    resume_ctxs: list[Context]  # Contexts after yield statements
    active_ctx: Context | None  # The active context for use in nested functions
    generator_observation_ctx: Context | None
    parent: Visitor | None  # The parent visitor for use in nested functions
    used_parent_binding_values: dict[str, Value]  # Values of parent bindings used in this
    generator_dependencies: dict[tuple[int, str], tuple[Visitor, str, Value]]
    function_name: str
    qualified_name: str

    def __init__(
        self,
        source_file: str,
        bound_args: inspect.BoundArguments,
        global_vars: dict[str, Any],
        parent: Visitor | None,
        function_name: str,
        qualified_name: str | None = None,
    ):
        self.source_file = source_file
        self.globals = global_vars
        self.bound_args = bound_args
        self.used_names = {}
        self.return_ctxs = []
        self.loop_head_ctxs = []
        self.break_ctxs = []
        self.yield_ctxs = []
        self.resume_ctxs = []
        self.active_ctx = None
        self.generator_observation_ctx = None
        self.is_running = False
        self.is_generator = False
        self.is_in_repeated_advance = False
        self.parent = parent
        self.used_parent_binding_values = {}
        self.generator_dependencies = {}
        self.yield_suspension_selections = {}
        self.declared_locals = frozenset()
        self.function_name = function_name
        if qualified_name is None:
            if parent is None:
                self.qualified_name = function_name
            else:
                self.qualified_name = f"{parent.qualified_name}.{function_name}"
        else:
            self.qualified_name = qualified_name

    def run(self, node, initial_iterator: Value | None = None):
        caller_ctx = ctx()
        caller_is_in_generator = caller_ctx.callback_state.is_in_generator
        self.is_running = True
        _ACTIVE_VISITORS.append(self)
        try:
            return self._run(node, initial_iterator)
        except Exception:
            failed_ctx = ctx()
            caller_ctx.callback_state.is_in_generator = caller_is_in_generator
            failed_ctx.scope = caller_ctx.scope.copy()
            set_ctx(failed_ctx)
            raise
        finally:
            assert _ACTIVE_VISITORS.pop() is self
            self.is_running = False

    def _run(self, node, initial_iterator: Value | None = None):
        completion_timer = mark_start(self.qualified_name)
        before_ctx = ctx()
        before_alloc_state = ctx().save_alloc_state()
        start_ctx = before_ctx.branch_with_scope(None, Scope())
        set_ctx(start_ctx)
        for name, value in self.bound_args.arguments.items():
            ctx().scope.set_value(name, validate_value(value))
        was_in_generator = ctx().callback_state.is_in_generator
        is_generator_fn = getattr(node, "has_yield", False)
        self.is_generator = is_generator_fn or isinstance(node, ast.GeneratorExp)
        ctx().callback_state.is_in_generator = ctx().callback_state.is_in_generator or is_generator_fn
        match node:
            case ast.FunctionDef(body=body):
                declaration = _find_scope_declaration(node)
                if declaration is not None:
                    self.raise_exception_at_node(
                        declaration, NotImplementedError(_SCOPE_DECLARATION_MESSAGES[type(declaration)])
                    )
                ctx().scope.set_value("$return", validate_value(None))
                self.declared_locals = getattr(node, "declared_locals", frozenset())
                self.visit_statements(body)
            case ast.Lambda(body=body):
                self.declared_locals = getattr(node, "declared_locals", frozenset())
                result = self.visit(body)
                ctx().scope.set_value("$return", result)
            case ast.GeneratorExp(elt=elt, generators=generators):
                ctx().callback_state.is_in_generator = True
                self.declared_locals = frozenset(
                    name for generator in generators for name in scan_writes(generator.target)
                )
                # The initial iterator is evaluated eagerly, in the enclosing scope, by visit_GeneratorExp.
                before_ctx = ctx().branch_with_scope(None, before_ctx.scope.copy())
                start_ctx = before_ctx.branch_with_scope(None, Scope())
                set_ctx(start_ctx)
                self.construct_genexpr(generators, elt, initial_iterator)
                ctx().scope.set_value("$return", validate_value(None))
            case _:
                raise NotImplementedError("Unsupported syntax")
        # has_yield is set by the find_function visitor
        if is_generator_fn or isinstance(node, ast.GeneratorExp):
            return_ctx = Context.meet([*self.return_ctxs, ctx()])
            result_binding = return_ctx.scope.get_binding("$return")
            if not isinstance(result_binding, ValueBinding):
                raise ValueError("Function has conflicting return values")
            if return_ctx.live and not (result_binding.value._is_py_() and result_binding.value._as_py_() is None):
                raise ValueError("Generator function return statements must return None")
            with using_ctx(start_ctx):
                state_var = Num._alloc_()
                is_present_var = Num._alloc_()
                owner_var = Num._alloc_() if ctx().project_state.runtime_checks != RuntimeChecks.NONE else None
            with using_ctx(before_ctx):
                state_var._set_(0)
                if owner_var is not None:
                    owner_var._set_(0)
            with using_ctx(return_ctx):
                state_var._set_(len(self.resume_ctxs) + 1)
                is_present_var._set_(0)
            del before_ctx.outgoing[None]  # Unlink the state machine body from the call site
            entry = before_ctx.new_empty_disconnected()
            entry.test = state_var.ir()
            for i, tgt in enumerate([start_ctx, *self.resume_ctxs]):
                entry.outgoing[i] = tgt
            yield_indices = {yield_ctx: i for i, yield_ctx in enumerate(self.yield_ctxs)}

            def reachable_yields(start: Context) -> list[int]:
                result = []
                seen = set()
                stack = [start]
                while stack:
                    current = stack.pop()
                    if current in seen:
                        continue
                    seen.add(current)
                    if current in yield_indices:
                        result.append(yield_indices[current])
                        continue
                    stack.extend(current.outgoing.values())
                return result

            first_yield_indices = reachable_yields(start_ctx)
            yield_between_ctxs = []
            for i, out in enumerate(self.yield_ctxs, start=1):
                between = out.branch(None)
                with using_ctx(between):
                    state_var._set_(i)
                yield_between_ctxs.append(between)
            if yield_between_ctxs:
                yield_merge_ctx = Context.meet(yield_between_ctxs)
            else:
                yield_merge_ctx = before_ctx.new_empty_disconnected()
            if not yield_merge_ctx.live:
                yield_value = Nothing
            else:
                yield_binding = yield_merge_ctx.scope.get_binding("$yield")
                match yield_binding:
                    case ValueBinding():
                        with using_ctx(yield_merge_ctx):
                            is_present_var._set_(1)
                        yield_value = (
                            Some(yield_binding.value)
                            if not return_ctx.live
                            else Maybe(present=is_present_var, value=yield_binding.value)
                        )
                    case EmptyBinding():
                        yield_value = Nothing
                    case ConflictBinding():
                        raise ValueError("Function has conflicting yield values")
            next_result_ctx = Context.meet([yield_merge_ctx, return_ctx])
            self.generator_observation_ctx = next_result_ctx
            entry.outgoing[None] = next_result_ctx
            first_suspensions = [yield_between_ctxs[index] for index in first_yield_indices]
            if not first_suspensions:
                first_suspension_ctx = next_result_ctx
            elif len(first_suspensions) == 1:
                first_suspension_ctx = first_suspensions[0]
            else:
                first_suspension_ctx = first_suspensions[0].copy_with_scope(Scope())
                Scope.apply_merge(first_suspension_ctx, first_suspensions)
            first_selection = [(self, first_suspension_ctx)]
            for index in first_yield_indices:
                delegated = self.yield_suspension_selections.get(self.yield_ctxs[index])
                if delegated is not None:
                    first_selection.extend(delegated[0])
            global_selection = [(self, next_result_ctx)]
            for _, selection in self.yield_suspension_selections.values():
                global_selection.extend(selection)

            def merge_selections(selections: list[tuple[Visitor, Context]]) -> list[tuple[Visitor, Context]]:
                by_owner = {}
                for owner, active_ctx in selections:
                    by_owner.setdefault(owner, []).append(active_ctx)
                result = []
                for owner, active_ctxs in by_owner.items():
                    active_ctxs = list(dict.fromkeys(active_ctxs))
                    if len(active_ctxs) == 1:
                        merged_ctx = active_ctxs[0]
                    else:
                        merged_ctx = active_ctxs[0].copy_with_scope(Scope())
                        Scope.apply_merge(merged_ctx, active_ctxs)
                    result.append((owner, merged_ctx))
                return result

            first_selection = merge_selections(first_selection)
            global_selection = merge_selections(global_selection)
            self.active_ctx = next_result_ctx
            set_ctx(before_ctx)
            return_test = Num._alloc_()
            next_result_ctx.test = return_test.ir()
            ctx().callback_state.is_in_generator = was_in_generator
            completion_timer()
            return Generator(
                return_test,
                owner_var,
                entry,
                next_result_ctx,
                yield_value,
                self.generator_dependencies,
                self,
                first_selection,
                global_selection,
            )
        after_ctx = Context.meet([*self.return_ctxs, ctx()])
        self.active_ctx = after_ctx
        result_binding = after_ctx.scope.get_binding("$return")
        if not isinstance(result_binding, ValueBinding):
            raise ValueError("Function has conflicting return values")
        result = result_binding.value
        if (
            type(result)._is_value_type_()
            and self.used_parent_binding_values
            and any(parent.is_generator for parent in self._parents())
        ):
            with using_ctx(after_ctx):
                result = result._get_readonly_()
        set_ctx(after_ctx.branch_with_scope(None, before_ctx.scope.copy()))
        terminated = not after_ctx.live
        # Nothing could have escaped, so allow reuse, which can allow naive allocation to succeed in the optimizer for
        # better compile times.
        if (terminated or result is validate_value(None)) and not ctx().callback_state.is_in_generator:
            ctx().restore_alloc_state(before_alloc_state)
        ctx().callback_state.is_in_generator = was_in_generator
        completion_timer()
        if terminated:
            return validate_value(None)
        return result

    def _parents(self) -> Iterable[Visitor]:
        parent = self.parent
        while parent is not None:
            yield parent
            parent = parent.parent

    def visit_statements(self, body: Iterable[ast.stmt]) -> None:
        for stmt in body:
            if not ctx().live:
                break
            self.visit(stmt)

    def construct_genexpr(
        self, generators: Iterable[ast.comprehension], elt: ast.expr, initial_iterator: Value | None = None
    ):
        from sonolus.script.internal.set_impl import SetImpl

        if not generators:
            # Note that there may effectively be multiple yields in an expression since
            # tuples are unrolled.
            value = self.visit(elt)
            if not ctx().live:
                return
            ctx().scope.set_value("$yield", validate_value(value))
            self.yield_ctxs.append(ctx())
            resume_ctx = ctx().new_disconnected()
            self.resume_ctxs.append(resume_ctx)
            set_ctx(resume_ctx)
            return
        generator, *others = generators
        if initial_iterator is not None:
            iterable = initial_iterator
        else:
            iterable = self.visit(generator.iter)
            if not ctx().live:
                return
        if isinstance(iterable, SetImpl):
            iterable = iterable._dict
        if has_tuple_iter(iterable):
            for value in tuple_iter(iterable):
                if not ctx().live:
                    break
                set_ctx(ctx().branch(None))
                self.handle_assign(generator.target, validate_value(value))
                if not ctx().live:
                    break
                # Unlike the iterator arm below there's no loop header to branch back to, so a filtered out
                # element falls forward into the next element's code instead, which is only emitted afterwards.
                skip_ctxs = []
                skipped = False
                for if_expr in generator.ifs:
                    test = self.convert_to_boolean_num(if_expr, self.visit(if_expr))
                    if not ctx().live:
                        skipped = True
                        break
                    if test._is_py_():
                        if test._as_py_():
                            continue
                        else:
                            skipped = True
                            break
                    else:
                        ctx().test = test.ir()
                        if_then_ctx = ctx().branch(None)
                        skip_ctxs.append(ctx().branch(0))
                        set_ctx(if_then_ctx)
                if not skipped:
                    self.construct_genexpr(others, elt)
                if skip_ctxs:
                    set_ctx(Context.meet([ctx(), *skip_ctxs]))
        else:
            if initial_iterator is not None:
                iterator = initial_iterator
            else:
                iter_method = _bind_special_method(iterable, "__iter__")
                if iter_method is _SPECIAL_METHOD_MISSING:
                    raise TypeError(f"'{_type_name(iterable)}' object is not iterable")
                iterator = self.handle_call(generator.iter, iter_method)
                if not ctx().live:
                    return
            if not isinstance(iterator, SonolusIterator):
                raise TypeError(f"iter() returned non-iterator of type '{_type_name(iterator)}'")
            header_ctx = ctx().branch(None)
            set_ctx(header_ctx)
            reject_custom_record_getattribute(iterator)
            previous_is_in_repeated_advance = self.is_in_repeated_advance
            self.is_in_repeated_advance = True
            try:
                next_value = self.handle_call(generator.iter, _bind_special_method(iterator, "next"))
            finally:
                self.is_in_repeated_advance = previous_is_in_repeated_advance
            if not ctx().live:
                # The header's only edge is the unconditional one into the terminating call, so returning
                # from here leaves nothing dangling.
                return
            if not isinstance(next_value, Maybe):
                raise TypeError(f"Iterator.next() returned '{_type_name(next_value)}', expected Maybe")
            if next_value._present._is_py_() and not next_value._present._as_py_():
                return
            if next_value._present._is_py_():
                body_ctx = ctx()
                else_ctx = None
            else:
                ctx().test = next_value._present.ir()
                body_ctx = ctx().branch(None)
                else_ctx = ctx().branch(0)
            set_ctx(body_ctx)
            self.handle_assign(generator.target, next_value._value)
            if not ctx().live:
                set_ctx(else_ctx if else_ctx is not None else ctx().into_dead())
                return
            skipped = False
            for if_expr in generator.ifs:
                test = self.convert_to_boolean_num(if_expr, self.visit(if_expr))
                if not ctx().live:
                    skipped = True
                    break
                if test._is_py_():
                    if test._as_py_():
                        continue
                    else:
                        if else_ctx is None:
                            self.handle_call(if_expr, error, validate_value("Generator cannot make progress"))
                        else:
                            ctx().outgoing[None] = header_ctx
                            set_ctx(ctx().into_dead())
                        skipped = True
                        break
                else:
                    if_then_ctx = ctx().branch(None)
                    if_else_ctx = ctx().branch(0)
                    ctx().test = test.ir()
                    if_else_ctx.outgoing[None] = header_ctx
                    set_ctx(if_then_ctx)
            if not skipped:
                self.construct_genexpr(others, elt)
                ctx().outgoing[None] = header_ctx
            set_ctx(else_ctx if else_ctx is not None else ctx().into_dead())

    def visit(self, node):
        """Visit a node."""
        # We want this here so this is filtered out of tracebacks
        node_cls = node.__class__
        visitor = _VISITOR_DISPATCH.get(node_cls)
        if visitor is None:
            visitor = getattr(Visitor, "visit_" + node_cls.__name__, Visitor.generic_visit)
            _VISITOR_DISPATCH[node_cls] = visitor
        try:
            return visitor(self, node)
        except Exception as e:
            if isinstance(e, CompilationError):
                raise
            self.raise_exception_at_node(node, e)

    def visit_FunctionDef(self, node):
        name = node.name
        # CPython evaluates decorator expressions top to bottom, then the defaults, and applies the decorators
        # bottom to top, so the expressions are collected here and applied after the function exists.
        decorators = []
        for decorator in node.decorator_list:
            decorator_value = self.visit(decorator)
            if not ctx().live:
                return
            decorators.append((decorator, decorator_value))
        signature = self.arguments_to_signature(node.args)
        if signature is None:
            return

        def fn(*args, **kwargs):
            bound = bind_arguments(signature, name, args, kwargs)
            bound.apply_defaults()
            return Visitor(
                self.source_file,
                bound,
                self.globals,
                parent=self,
                function_name=name,
            ).run(node)

        fn._meta_fn_ = True
        fn.__name__ = name
        fn.__qualname__ = name

        for decorator, decorator_value in reversed(decorators):
            fn = self.handle_call(decorator, decorator_value, fn)
            if not ctx().live:
                return

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
            if not ctx().live:
                return
            match target:
                case ast.Name():
                    raise NotImplementedError("Deleting variables is not supported")
                case ast.Subscript(value=value, slice=slice_expr):
                    value = self.visit(value)
                    if not ctx().live:
                        return
                    slice_value = self.visit(slice_expr)
                    if not ctx().live:
                        return
                    self.handle_delitem(target, value, slice_value)
                case ast.Attribute():
                    raise NotImplementedError("Deleting attributes is not supported")
                case _:
                    raise NotImplementedError("Unsupported delete target")

    def visit_Assign(self, node):
        value = self.visit(node.value)
        if not ctx().live:
            return
        for target in node.targets:
            self.handle_assign(target, value)
            if not ctx().live:
                return

    def visit_TypeAlias(self, node):
        raise NotImplementedError("Type aliases are not supported")

    def visit_AugAssign(self, node):
        target = node.target
        # Evaluate the target's base (and subscript key / attribute) exactly once so a
        # side-effecting subscript index or attribute base is not re-evaluated on write-back,
        # matching Python's rule that an augmented-assignment target is evaluated only once.
        match target:
            case ast.Attribute(value=base_expr, attr=attr):
                base = self.visit(base_expr)
                if not ctx().live:
                    return
                lhs_value = self.handle_getattr(target, base, attr)
                if not ctx().live:
                    return

                def store(result):
                    self.handle_setattr(target, base, attr, result)
            case ast.Subscript(value=base_expr, slice=slice_expr):
                base = self.visit(base_expr)
                if not ctx().live:
                    return
                key = self.visit(slice_expr)
                if not ctx().live:
                    return
                lhs_value = self.handle_getitem(target, base, key)
                if not ctx().live:
                    return

                def store(result):
                    self.handle_setitem(target, base, key, result)
            case _:
                lhs_value = self.visit(target)
                if not ctx().live:
                    return

                def store(result):
                    self.handle_assign(target, result)

        rhs_value = self.visit(node.value)
        if not ctx().live:
            return
        regular_fn_name = bin_ops[type(node.op)]
        if (
            type(lhs_value) is Num
            and type(rhs_value) is Num
            and regular_fn_name in _NUM_BIN_OP_NAMES
            and (active_ctx := ctx()).callback_state.no_eval
        ):
            # Num has no in-place operators and never returns NotImplemented for Num operands
            self.active_ctx = active_ctx
            store(getattr(lhs_value, regular_fn_name)(rhs_value))
            return
        inplace_fn_name = inplace_ops[type(node.op)]
        right_fn_name = rbin_ops[type(node.op)]
        if lhs_value._is_py_() and rhs_value._is_py_():
            lhs_py = lhs_value._as_py_()
            rhs_py = rhs_value._as_py_()
            if (isinstance(lhs_py, type) or getattr(lhs_py, "_is_comptime_value_", False)) and (
                isinstance(rhs_py, type) or getattr(rhs_py, "_is_comptime_value_", False)
            ):
                result = _comptime_augassign(lhs_py, rhs_py, inplace_fn_name, regular_fn_name, right_fn_name)
                if result is not _NOT_IMPLEMENTED:
                    store(validate_value(result))
                    return
        inplace_method = _bind_special_method(lhs_value, inplace_fn_name)
        if inplace_method is not _SPECIAL_METHOD_MISSING:
            result = self.handle_call(node, inplace_method, rhs_value)
            if not self.is_not_implemented(result):
                store(result)
                return
        right_has_priority = _has_strict_subclass_reflected_priority(lhs_value, rhs_value, right_fn_name)
        right_method = _bind_special_method(rhs_value, right_fn_name)
        if right_has_priority and right_method is not _SPECIAL_METHOD_MISSING:
            result = self.handle_call(node, right_method, lhs_value)
            if not self.is_not_implemented(result):
                store(result)
                return
        regular_method = _bind_special_method(lhs_value, regular_fn_name)
        if regular_method is not _SPECIAL_METHOD_MISSING:
            result = self.handle_call(node, regular_method, rhs_value)
            if not self.is_not_implemented(result):
                store(result)
                return
        if (
            not right_has_priority
            and right_method is not _SPECIAL_METHOD_MISSING
            and type(lhs_value) is not type(rhs_value)
        ):
            result = self.handle_call(node, right_method, lhs_value)
            if not self.is_not_implemented(result):
                store(result)
                return
        raise TypeError(
            f"unsupported operand type(s) for {op_to_symbol[type(node.op)]}=: "
            f"'{_type_name(lhs_value)}' and '{_type_name(rhs_value)}'"
        )

    def visit_AnnAssign(self, node):
        if node.value is None:
            # A bare annotation like `x: int` binds nothing and doesn't evaluate the annotation. For a non-simple
            # target CPython still evaluates the primary (and the subscript index) for their side effects, but
            # discards them without loading the attribute or item.
            match node.target:
                case ast.Attribute(value=primary):
                    self.visit(primary)
                case ast.Subscript(value=primary, slice=slice_expr):
                    self.visit(primary)
                    if not ctx().live:
                        return
                    self.visit(slice_expr)
            return
        value = self.visit(node.value)
        self.handle_assign(node.target, value)

    def visit_For(self, node):
        from sonolus.script.internal.set_impl import SetImpl

        iterable = self.visit(node.iter)
        if not ctx().live:
            return
        if isinstance(iterable, SetImpl):
            iterable = iterable._dict
        if has_tuple_iter(iterable):
            break_ctxs = []
            for value in tuple_iter(iterable):
                set_ctx(ctx().branch(None))
                self.loop_head_ctxs.append([])
                self.break_ctxs.append([])
                self.handle_assign(node.target, validate_value(value))
                self.visit_statements(node.body)
                continue_ctxs = [*self.loop_head_ctxs.pop(), ctx()]
                break_ctxs.extend(self.break_ctxs.pop())
                set_ctx(Context.meet(continue_ctxs))
            # Visited here, in the fall-through context, before the break contexts are merged in.
            self.visit_statements(node.orelse)
            if break_ctxs:
                set_ctx(Context.meet([*break_ctxs, ctx()]))
            return
        iter_method = _bind_special_method(iterable, "__iter__")
        if iter_method is _SPECIAL_METHOD_MISSING:
            raise TypeError(f"'{_type_name(iterable)}' object is not iterable")
        iterator = self.handle_call(node, iter_method)
        if not ctx().live:
            return
        if not isinstance(iterator, SonolusIterator):
            raise TypeError(f"iter() returned non-iterator of type '{_type_name(iterator)}'")
        writes = scan_writes(node.target, *node.body)
        header_ctx = ctx().prepare_loop_header(writes)
        self.loop_head_ctxs.append(header_ctx)
        self.break_ctxs.append([])
        set_ctx(header_ctx)
        reject_custom_record_getattribute(iterator)
        next_value = self.handle_call(node, _bind_special_method(iterator, "next"))
        if not ctx().live:
            # The loop is abandoned in its header, so its frame has to be closed here: an enclosing loop
            # pops next, and would otherwise close itself against this one.
            self.loop_head_ctxs.pop().check_loop_conflicts()
            self.break_ctxs.pop()
            return
        if not isinstance(next_value, Maybe):
            raise TypeError(f"Iterator.next() returned '{_type_name(next_value)}', expected Maybe")
        if next_value._present._is_py_() and not next_value._present._as_py_():
            self.loop_head_ctxs.pop().check_loop_conflicts()
            self.break_ctxs.pop()
            self.visit_statements(node.orelse)
            return
        if next_value._present._is_py_():
            body_ctx = ctx()
            else_ctx = None
        else:
            ctx().test = next_value._present.ir()
            body_ctx = ctx().branch(None)
            else_ctx = ctx().branch(0)

        set_ctx(body_ctx)
        self.handle_assign(node.target, next_value._value)
        self.visit_statements(node.body)
        ctx().branch_to_loop_header(header_ctx)

        # Before set_ctx(else_ctx), so the else block traces against the checked exit.
        break_ctxs = self.break_ctxs.pop()
        if else_ctx is None:
            else_end_ctx = ctx().into_dead()
            self.loop_head_ctxs.pop().check_loop_conflicts(else_end_ctx, break_ctxs)
        else:
            self.loop_head_ctxs.pop().check_loop_conflicts(else_ctx, break_ctxs)
            set_ctx(else_ctx)
            self.visit_statements(node.orelse)
            else_end_ctx = ctx()

        after_ctx = Context.meet([else_end_ctx, *break_ctxs])
        set_ctx(after_ctx)

    def visit_While(self, node):
        writes = scan_writes(node.test, *node.body)
        header_ctx = ctx().prepare_loop_header(writes)
        self.loop_head_ctxs.append(header_ctx)
        self.break_ctxs.append([])
        set_ctx(header_ctx)
        test = self.convert_to_boolean_num(node.test, self.visit(node.test))
        if not ctx().live:
            self.loop_head_ctxs.pop().check_loop_conflicts()
            self.break_ctxs.pop()
            return
        if test._is_py_():
            if test._as_py_():
                # The loop will run until a break / return
                body_ctx = ctx().branch(None)
                set_ctx(body_ctx)
                self.visit_statements(node.body)
                ctx().branch_to_loop_header(header_ctx)

                break_ctxs = self.break_ctxs.pop()
                # A statically true test has no fallthrough exit, so the dead continuation stands in.
                dead_ctx = ctx().into_dead()
                self.loop_head_ctxs.pop().check_loop_conflicts(dead_ctx, break_ctxs)

                after_ctx = Context.meet([dead_ctx, *break_ctxs])
                set_ctx(after_ctx)
                return
            else:
                self.loop_head_ctxs.pop().check_loop_conflicts()
                self.break_ctxs.pop()
                self.visit_statements(node.orelse)
                return
        ctx().test = test.ir()
        body_ctx = ctx().branch(None)
        else_ctx = ctx().branch(0)

        set_ctx(body_ctx)
        self.visit_statements(node.body)
        ctx().branch_to_loop_header(header_ctx)

        break_ctxs = self.break_ctxs.pop()
        self.loop_head_ctxs.pop().check_loop_conflicts(else_ctx, break_ctxs)

        set_ctx(else_ctx)
        self.visit_statements(node.orelse)
        else_end_ctx = ctx()

        after_ctx = Context.meet([else_end_ctx, *break_ctxs])
        set_ctx(after_ctx)

    def visit_If(self, node):
        test = self.convert_to_boolean_num(node.test, self.visit(node.test))
        if not ctx().live:
            return

        if test._is_py_():
            if test._as_py_():
                self.visit_statements(node.body)
            else:
                self.visit_statements(node.orelse)
            return

        ctx_init = ctx()
        ctx_init.test = test.ir()
        true_ctx = ctx_init.branch(None)
        false_ctx = ctx_init.branch(0)

        set_ctx(true_ctx)
        self.visit_statements(node.body)
        true_end_ctx = ctx()

        set_ctx(false_ctx)
        self.visit_statements(node.orelse)
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
            # Reject stars up front rather than in handle_match_pattern: a star nested inside a sequence pattern
            # whose length test fails statically is never visited, which would make the arm silently not match.
            if any(isinstance(sub, ast.MatchStar) for sub in ast.walk(case.pattern)):
                raise NotImplementedError(
                    "Star sub-patterns (e.g. `case [a, *rest]:`) in sequence match patterns are not supported"
                )
            true_ctx, false_ctx, captures = self.handle_match_pattern(subject, case.pattern)
            if not true_ctx.live:
                set_ctx(false_ctx)
                continue
            set_ctx(true_ctx)
            # Python applies a pattern's captures only once the whole pattern has matched, so they are
            # collected while matching and written here, where no not-matching path can reach them.
            # Before the guard on purpose: the guard reads them, and a guard that then fails keeps them
            # bound, since guard_false_ctx below branches off this context.
            for name, binding in captures:
                ctx().scope.set_binding(name, binding)
            guard = (
                self.convert_to_boolean_num(case.guard, self.visit(case.guard)) if case.guard else validate_value(True)
            )
            if guard._is_py_():
                if guard._as_py_():
                    self.visit_statements(case.body)
                    end_ctxs.append(ctx())
                else:
                    # Merge failing before the guard and failing now at the guard (which we know is guaranteed to fail)
                    false_ctx = Context.meet([ctx(), false_ctx])
            else:
                ctx().test = guard.ir()
                guard_true_ctx = ctx().branch(None)
                guard_false_ctx = ctx().branch(0)
                set_ctx(guard_true_ctx)
                self.visit_statements(case.body)
                end_ctxs.append(ctx())
                false_ctx = Context.meet([false_ctx, guard_false_ctx])
            set_ctx(false_ctx)
        end_ctxs.append(ctx())
        if end_ctxs:
            set_ctx(Context.meet(end_ctxs))

    def handle_match_pattern(
        self, subject: Value, pattern: ast.pattern
    ) -> tuple[Context, Context, list[tuple[str, Binding]]]:
        """Trace `pattern` against `subject`.

        Returns the context where the pattern matched, the context where it did not, and the bindings the
        pattern captures, in source order. The captures are returned rather than written into a scope so that
        the caller can apply them only once the whole pattern has matched, as Python does. They are bindings
        rather than values because an or-pattern's alternatives can conflict, which only a read may report.
        """
        from sonolus.script.internal.generic import validate_type_spec

        if not ctx().live:
            return ctx().into_dead(), ctx(), []

        match pattern:
            case ast.MatchValue(value=value):
                value = self.visit(value)
                with self.reporting_errors_at_node(pattern):
                    comparison = self.handle_comparison(pattern, _EQ_OP, subject, value)
                    test = self.convert_to_boolean_num(pattern, comparison)
                if test._is_py_():
                    if test._as_py_():
                        return ctx(), ctx().into_dead(), []
                    else:
                        return ctx().into_dead(), ctx(), []
                ctx_init = ctx()
                ctx_init.test = test.ir()
                true_ctx = ctx_init.branch(None)
                false_ctx = ctx_init.branch(0)
                return true_ctx, false_ctx, []
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
                if test._is_py_():
                    if test._as_py_():
                        return ctx(), ctx().into_dead(), []
                    else:
                        return ctx().into_dead(), ctx(), []
                else:
                    ctx_init = ctx()
                    ctx_init.test = test.ir()
                    true_ctx = ctx_init.branch(None)
                    false_ctx = ctx_init.branch(0)
                    return true_ctx, false_ctx, []
            case ast.MatchSequence(patterns=patterns):
                target_len = len(patterns)
                if not isinstance(subject, Sequence | TupleImpl):
                    return ctx().into_dead(), ctx(), []
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
                    return ctx().into_dead(), ctx(), []
                set_ctx(true_ctx)
                captures = []
                for i, subpattern in enumerate(patterns):
                    if not ctx().live:
                        break
                    value = self.handle_getitem(subpattern, subject, validate_value(i))
                    if not ctx().live:
                        raise NotImplementedError(_TERMINATING_MATCH_READ_MESSAGE)
                    true_ctx, false_ctx, sub_captures = self.handle_match_pattern(value, subpattern)
                    captures.extend(sub_captures)
                    false_ctxs.append(false_ctx)
                    set_ctx(true_ctx)
                if not false_ctxs:
                    return true_ctx, true_ctx.into_dead(), captures
                return true_ctx, Context.meet(false_ctxs), captures
            case ast.MatchMapping():
                raise NotImplementedError("Match mappings are not supported")
            case ast.MatchClass(cls=cls, patterns=patterns, kwd_attrs=kwd_attrs, kwd_patterns=kwd_patterns):
                cls = self.visit(cls)
                if not ctx().live:
                    return ctx().into_dead(), ctx(), []
                if cls._is_py_() and cls._as_py_() in {_int, _float, _bool}:
                    raise TypeError("Instance check against int, float, or bool is not supported, use Num instead")
                cls = validate_type_spec(cls)
                if not isinstance(cls, type):
                    raise TypeError("Class is not a type")
                if not isinstance(subject, cls):
                    return ctx().into_dead(), ctx(), []
                if patterns:
                    if not hasattr(cls, "__match_args__"):
                        raise TypeError("Class does not support match patterns")
                    match_args = cls.__match_args__
                    if not isinstance(match_args, tuple):
                        raise TypeError(
                            f"{cls.__name__}.__match_args__ must be a tuple (got {type(match_args).__name__})"
                        )
                    if len(match_args) < len(patterns):
                        limit = len(match_args)
                        plural = "" if limit == 1 else "s"
                        raise TypeError(
                            f"{cls.__name__}() accepts {limit} positional sub-pattern{plural} ({len(patterns)} given)"
                        )
                    # Positional sub-patterns bind to the first len(patterns) __match_args__.
                    # Python allows mixing them with keyword sub-patterns (e.g. Point(0, y=1)), so
                    # prepend the positional attrs/patterns to the existing keyword ones rather than
                    # overwriting them.
                    positional_attrs = match_args[: len(patterns)]
                    for attr in positional_attrs:
                        if not isinstance(attr, str):
                            raise TypeError(f"__match_args__ elements must be strings (got {type(attr).__name__})")
                    kwd_attrs = [*positional_attrs, *kwd_attrs]
                    kwd_patterns = [*patterns, *kwd_patterns]
                    # A positional and a keyword sub-pattern targeting the same attribute is a
                    # runtime TypeError in CPython; reject it rather than silently emitting an arm
                    # that can never match (it would test the attribute against both sub-patterns).
                    seen_attrs = set()
                    for attr in kwd_attrs:
                        if attr in seen_attrs:
                            raise TypeError(f"{cls.__name__}() got multiple sub-patterns for attribute {attr!r}")
                        seen_attrs.add(attr)
                if kwd_attrs:
                    true_ctx = ctx()
                    false_ctxs = []
                    captures = []
                    for attr, subpattern in zip(kwd_attrs, kwd_patterns, strict=False):
                        if not ctx().live:
                            break
                        try:
                            value = self.handle_getattr(subpattern, subject, attr, report_errors=False)
                        except Exception as e:
                            if not caused_by_attribute_error(e):
                                raise
                            false_ctxs.append(ctx())
                            return ctx().into_dead(), Context.meet(false_ctxs), captures
                        if not ctx().live:
                            raise NotImplementedError(_TERMINATING_MATCH_READ_MESSAGE)
                        true_ctx, false_ctx, sub_captures = self.handle_match_pattern(value, subpattern)
                        captures.extend(sub_captures)
                        false_ctxs.append(false_ctx)
                        set_ctx(true_ctx)
                    return true_ctx, Context.meet(false_ctxs), captures
                return ctx(), ctx().into_dead(), []
            case ast.MatchStar():
                # Unreachable: visit_Match rejects patterns containing stars before recursing here.
                raise NotImplementedError(
                    "Star sub-patterns (e.g. `case [a, *rest]:`) in sequence match patterns are not supported"
                )
            case ast.MatchAs(pattern=pattern, name=name):
                if pattern:
                    true_ctx, false_ctx, captures = self.handle_match_pattern(subject, pattern)
                    if name:
                        captures = [*captures, (name, ValueBinding(validate_value(subject)))]
                    return true_ctx, false_ctx, captures
                else:
                    return ctx(), ctx().into_dead(), [(name, ValueBinding(validate_value(subject)))] if name else []
            case ast.MatchOr():
                true_ctxs = []
                # Every alternative binds the same names, to a different value each. Merging those values
                # is what Context.meet does, and it works on scope bindings, so each alternative writes its
                # captures under a temporary name and the merged value is read back out below.
                temp_names: dict[str, str] = {}
                assert pattern.patterns
                for subpattern in pattern.patterns:
                    if not ctx().live:
                        break
                    true_ctx, false_ctx, captures = self.handle_match_pattern(subject, subpattern)
                    for name, binding in captures:
                        temp_name = temp_names.get(name)
                        if temp_name is None:
                            temp_name = self.new_name(f"match_{name}")
                            temp_names[name] = temp_name
                        true_ctx.scope.set_binding(temp_name, binding)
                    true_ctxs.append(true_ctx)
                    set_ctx(false_ctx)
                merged_ctx = Context.meet(true_ctxs)
                if not merged_ctx.live:
                    # No alternative can match, so nothing reads these captures. An alternative that died
                    # before reaching its capture never wrote its temporary either, so reading one here
                    # would be reading an unbound name.
                    return merged_ctx, ctx(), []
                merged_captures = []
                for name, temp_name in temp_names.items():
                    # The merged binding itself, not its value: alternatives that bind incompatible values
                    # merge to a conflict, which has to reach the capture's own name so that a read of it
                    # reports that name, and so that a case body never reading it still compiles.
                    merged_captures.append((name, merged_ctx.scope.get_binding(temp_name)))
                    # Deleted so that no context branched from this one carries it: scan_writes works on the
                    # source, which never spells a temporary, so a loop header allocates no slot for one and
                    # a match inside a loop would otherwise carry it to a back edge without one.
                    merged_ctx.scope.delete_binding(temp_name)
                return merged_ctx, ctx(), merged_captures

    def visit_Raise(self, node):
        raise NotImplementedError("Raise statements are not supported")

    def visit_Try(self, node):
        raise NotImplementedError("Try statements are not supported")

    def visit_TryStar(self, node):
        raise NotImplementedError("Try* statements are not supported")

    def visit_Assert(self, node):
        test = self.convert_to_boolean_num(node.test, self.visit(node.test))
        if not ctx().live:
            return
        if node.msg is None:
            self.handle_call(node, assert_true, test, validate_value(None))
            return
        # The message must not be emitted on the straight-line path, since its side effects would run even
        # when the assertion passes.
        if test._is_py_():
            if test._as_py_():
                return
            self.handle_call(node, assert_true, test, self.visit(node.msg))
            return
        if ctx().project_state.runtime_checks == RuntimeChecks.NONE:
            # Run the msg in a dead context to still get some errors out of it.
            active_ctx = self.active_ctx
            with using_ctx(ctx().new_disconnected()):
                self.visit(node.msg)
            self.active_ctx = active_ctx
            return
        ctx().test = test.ir()
        true_ctx = ctx().branch(None)
        false_ctx = ctx().branch(0)
        set_ctx(false_ctx)
        # The test is known to be false here, so this evaluates the message and terminates like assert_true would.
        message = self.visit(node.msg)
        if ctx().live:
            self.handle_call(node, require, validate_value(0), message)
        set_ctx(true_ctx)

    def visit_Import(self, node):
        raise NotImplementedError("Import statements are not supported")

    def visit_ImportFrom(self, node):
        raise NotImplementedError("Import statements are not supported")

    def visit_Global(self, node):
        raise NotImplementedError(_SCOPE_DECLARATION_MESSAGES[ast.Global])

    def visit_Nonlocal(self, node):
        raise NotImplementedError(_SCOPE_DECLARATION_MESSAGES[ast.Nonlocal])

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
        initial_value = self.visit(initial)
        if not ctx().live:
            return validate_value(None)
        return handler(initial_value, ast.copy_location(ast.BoolOp(op=node.op, values=rest), node))

    def visit_NamedExpr(self, node):
        if self.function_name == "<genexp>":
            raise NotImplementedError("Assignment expressions (`:=`) in a generator expression are not supported.")
        value = self.visit(node.value)
        self.handle_assign(node.target, value)
        return value

    def visit_BinOp(self, node):
        lhs = self.visit(node.left)
        if not ctx().live:
            return validate_value(None)
        rhs = self.visit(node.right)
        if not ctx().live:
            return validate_value(None)
        op = bin_ops[type(node.op)]
        if (
            type(lhs) is Num
            and type(rhs) is Num
            and op in _NUM_BIN_OP_NAMES
            and (active_ctx := ctx()).callback_state.no_eval
        ):
            # Num operators never return NotImplemented for Num operands, so the negotiation can be skipped
            self.active_ctx = active_ctx
            return getattr(lhs, op)(rhs)
        if lhs._is_py_() and rhs._is_py_():
            lhs_py = lhs._as_py_()
            rhs_py = rhs._as_py_()
            if (isinstance(lhs_py, type) or getattr(lhs_py, "_is_comptime_value_", False)) and (
                isinstance(rhs_py, type) or getattr(rhs_py, "_is_comptime_value_", False)
            ):
                result = _comptime_binop(lhs_py, rhs_py, op, rbin_ops[type(node.op)])
                if result is not _NOT_IMPLEMENTED:
                    return validate_value(result)
        right_op = rbin_ops[type(node.op)]
        right_has_priority = _has_strict_subclass_reflected_priority(lhs, rhs, right_op)
        right_method = _bind_special_method(rhs, right_op)
        if right_has_priority and right_method is not _SPECIAL_METHOD_MISSING:
            result = self.handle_call(node, right_method, lhs)
            if not self.is_not_implemented(result):
                return result
        left_method = _bind_special_method(lhs, op)
        if left_method is not _SPECIAL_METHOD_MISSING:
            result = self.handle_call(node, left_method, rhs)
            if not self.is_not_implemented(result):
                return result
        if not right_has_priority and right_method is not _SPECIAL_METHOD_MISSING and type(lhs) is not type(rhs):
            result = self.handle_call(node, right_method, lhs)
            if not self.is_not_implemented(result):
                return result
        raise TypeError(
            f"unsupported operand type(s) for {op_to_symbol[type(node.op)]}: "
            f"'{_type_name(lhs)}' and '{_type_name(rhs)}'"
        )

    def visit_UnaryOp(self, node):
        operand = self.visit(node.operand)
        if not ctx().live:
            return validate_value(None)
        if isinstance(node.op, ast.Not):
            return self.convert_to_boolean_num(node, operand).not_()
        op = unary_ops[type(node.op)]
        if operand._is_py_():
            operand_py = operand._as_py_()
            if isinstance(operand_py, type):
                method = _bind_special_method(operand_py, op)
                if method is not _SPECIAL_METHOD_MISSING:
                    return self.handle_call(node, method)
        method = _bind_special_method(operand, op)
        if method is not _SPECIAL_METHOD_MISSING:
            return self.handle_call(node, method)
        raise TypeError(f"bad operand type for unary {op_to_symbol[type(node.op)]}: '{_type_name(operand)}'")

    def visit_Lambda(self, node):
        signature = self.arguments_to_signature(node.args)
        if signature is None:
            return validate_value(None)

        def fn(*args, **kwargs):
            bound = bind_arguments(signature, "<lambda>", args, kwargs)
            bound.apply_defaults()
            return Visitor(
                self.source_file,
                bound,
                self.globals,
                parent=self,
                function_name="<lambda>",
            ).run(node)

        fn._meta_fn_ = True
        fn.__name__ = "<lambda>"
        fn.__qualname__ = "<lambda>"
        self.active_ctx = ctx()

        return validate_value(fn)

    def visit_IfExp(self, node):
        test = self.convert_to_boolean_num(node.test, self.visit(node.test))
        if not ctx().live:
            return validate_value(None)

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
        if not ctx().live:
            return validate_value(None)
        return ctx().scope.get_value(res_name)

    def visit_Dict(self, node):
        results = {}
        for k, v in zip(node.keys, node.values, strict=True):
            if not ctx().live:
                return validate_value(None)
            if k is None:
                # The AST uses a None key for a ** entry.
                raise NotImplementedError("** unpacking in dict literals is not supported")
            k_visited = self.visit(k)
            if not ctx().live:
                return validate_value(None)
            v_visited = self.visit(v)
            if not ctx().live:
                return validate_value(None)
            if not k_visited._is_py_():
                raise ValueError("Dict keys must be compile time constants")
            results[k_visited._as_py_()] = v_visited
        return validate_value(results)

    def visit_Set(self, node):
        from sonolus.script.internal.set_impl import SetImpl

        values = []
        for elt in node.elts:
            value = self.visit(elt)
            if not ctx().live:
                return validate_value(None)
            values.append(validate_value(value))
        return SetImpl.from_set(values)

    def visit_ListComp(self, node):
        raise NotImplementedError("List comprehensions are not supported")

    def visit_SetComp(self, node):
        raise NotImplementedError("Set comprehensions are not supported")

    def visit_DictComp(self, node):
        raise NotImplementedError("Dict comprehensions are not supported")

    def visit_GeneratorExp(self, node):
        from sonolus.script.internal.set_impl import SetImpl

        # Only the outermost iterable is evaluated in the enclosing scope, and eagerly, as in Python. Doing it
        # here rather than inside the generator's own visitor is what keeps a loop target that shadows a name
        # read there from changing which binding that read resolves to.
        first_generator = node.generators[0]
        iterable = self.visit(first_generator.iter)
        if not ctx().live:
            return validate_value(None)
        if isinstance(iterable, SetImpl):
            iterable = iterable._dict
        if has_tuple_iter(iterable):
            initial_iterator = iterable
        else:
            iter_method = _bind_special_method(iterable, "__iter__")
            if iter_method is _SPECIAL_METHOD_MISSING:
                raise TypeError(f"'{_type_name(iterable)}' object is not iterable")
            initial_iterator = self.handle_call(first_generator.iter, iter_method)
            if not ctx().live:
                return validate_value(None)
            if not isinstance(initial_iterator, SonolusIterator):
                raise TypeError(f"iter() returned non-iterator of type '{_type_name(initial_iterator)}'")
        # Recorded after the iterable, so it is the context the generator is created in: that is what
        # _validate_bindings compares a captured binding against.
        self.active_ctx = ctx()
        return Visitor(
            self.source_file, inspect.Signature([]).bind(), self.globals, parent=self, function_name="<genexp>"
        ).run(node, initial_iterator)

    def visit_Await(self, node):
        raise NotImplementedError("Await expressions are not supported")

    def visit_Yield(self, node):
        value = self.visit(node.value) if node.value else validate_value(None)
        if not ctx().live:
            return validate_value(None)
        ctx().scope.set_value("$yield", value)
        self.yield_ctxs.append(ctx())
        resume_ctx = ctx().new_disconnected()
        self.resume_ctxs.append(resume_ctx)
        set_ctx(resume_ctx)
        return validate_value(None)  # send() is unsupported, so yield returns None

    def visit_YieldFrom(self, node):
        value = self.visit(node.value)
        if not ctx().live:
            return validate_value(None)
        if has_tuple_iter(value):
            for entry in tuple_iter(value):
                ctx().scope.set_value("$yield", validate_value(entry))
                self.yield_ctxs.append(ctx())
                resume_ctx = ctx().new_disconnected()
                self.resume_ctxs.append(resume_ctx)
                set_ctx(resume_ctx)
            return validate_value(None)
        iter_method = _bind_special_method(value, "__iter__")
        if iter_method is _SPECIAL_METHOD_MISSING:
            raise TypeError(f"'{_type_name(value)}' object is not iterable")
        iterator = self.handle_call(node, iter_method)
        if not ctx().live:
            return validate_value(None)
        if not isinstance(iterator, SonolusIterator):
            raise TypeError(f"iter() returned non-iterator of type '{_type_name(iterator)}'")
        header = ctx().branch(None)
        set_ctx(header)
        delegated_selections = None
        if isinstance(iterator, Generator):
            delegated_selections = (iterator.first_selection, iterator.global_selection)
        reject_custom_record_getattribute(iterator)
        result = self.handle_call(node, _bind_special_method(iterator, "next"))
        if not ctx().live:
            return validate_value(None)
        if not isinstance(result, Maybe):
            raise TypeError(f"Iterator.next() returned '{_type_name(result)}', expected Maybe")
        if result._present._is_py_() and not result._present._as_py_():
            return validate_value(None)
        if result._present._is_py_():
            nothing_branch = None
            some_branch = ctx()
        else:
            nothing_branch = ctx().branch(0)
            some_branch = ctx().branch(None)
            ctx().test = result._present.ir()
        set_ctx(some_branch)
        ctx().scope.set_value("$yield", result._value)
        self.yield_ctxs.append(ctx())
        if delegated_selections is not None:
            self.yield_suspension_selections[ctx()] = delegated_selections
        resume_ctx = ctx().new_disconnected()
        self.resume_ctxs.append(resume_ctx)
        resume_ctx.outgoing[None] = header
        set_ctx(nothing_branch if nothing_branch is not None else ctx().into_dead())
        return validate_value(None)

    def _real_method(self, obj: Value, method_name: str) -> Any:
        method = _bind_special_method(obj, method_name)
        if method is _SPECIAL_METHOD_MISSING or isinstance(method, MethodWrapperType):
            return _SPECIAL_METHOD_MISSING
        return method

    def handle_comparison(self, node: ast.stmt | ast.expr | ast.pattern, op: ast.cmpop, l_val: Value, r_val: Value):
        """Evaluate `l_val op r_val` and return the raw result of the comparison.

        The caller applies `not in` inversion and truth-tests results where the surrounding syntax requires it.
        """
        if not ctx().live:
            return validate_value(None)
        if isinstance(op, ast.Is | ast.IsNot):
            if not (r_val._is_py_() and r_val._as_py_() is None):
                raise TypeError("The right operand of 'is' must be None")
            if isinstance(op, ast.Is):
                return Num._accept_(l_val._is_py_() and l_val._as_py_() is None)
            return Num._accept_(not (l_val._is_py_() and l_val._as_py_() is None))
        result = None
        reflected_name = rcomp_ops.get(type(op))
        right_has_priority = reflected_name is not None and _is_strict_subclass(l_val, r_val)
        right_method = (
            self._real_method(r_val, reflected_name) if reflected_name is not None else _SPECIAL_METHOD_MISSING
        )
        if (
            type(l_val) is Num
            and type(r_val) is Num
            and (comp_fn_name := comp_ops.get(type(op))) is not None
            and (active_ctx := ctx()).callback_state.no_eval
        ):
            # Num comparison operators never return NotImplemented for Num operands.
            self.active_ctx = active_ctx
            result = getattr(l_val, comp_fn_name)(r_val)
        elif right_has_priority and right_method is not _SPECIAL_METHOD_MISSING:
            result = self.handle_call(node, right_method, l_val)
        left_method = self._real_method(l_val, comp_ops[type(op)]) if type(op) in comp_ops else _SPECIAL_METHOD_MISSING
        if (result is None or self.is_not_implemented(result)) and left_method is not _SPECIAL_METHOD_MISSING:
            result = self.handle_call(node, left_method, r_val)
        if (
            (result is None or self.is_not_implemented(result))
            and not right_has_priority
            and right_method is not _SPECIAL_METHOD_MISSING
        ):
            result = self.handle_call(node, right_method, l_val)
        iter_method = self._real_method(r_val, "__iter__")
        if result is None and type(op) in {ast.In, ast.NotIn} and iter_method is not _SPECIAL_METHOD_MISSING:
            result = self.handle_call(node, contains_by_iteration, l_val, r_val)
        if result is not None and type(op) in {ast.In, ast.NotIn} and self.is_not_implemented(result):
            raise TypeError("NotImplemented should not be used in a boolean context")
        if result is None or self.is_not_implemented(result):
            # The default object.__eq__/__ne__ compares identity, which is not reliable for traced values.
            if type(op) is ast.Eq and type(l_val) is not type(r_val):
                return Num._accept_(False)
            elif type(op) is ast.NotEq and type(l_val) is not type(r_val):
                return Num._accept_(True)
            elif type(op) in {ast.In, ast.NotIn}:
                raise TypeError(f"argument of type '{_type_name(r_val)}' is not a container or iterable")
            else:
                raise TypeError(
                    f"'{op_to_symbol[type(op)]}' not supported between instances of '{_type_name(l_val)}' and "
                    f"'{_type_name(r_val)}'"
                )
        return result

    def visit_Compare(self, node):
        result_name = self.new_name("compare")
        ctx().scope.set_value(result_name, Num._accept_(0))
        l_val = self.visit(node.left)
        if not ctx().live:
            return validate_value(None)
        false_ctxs = []
        for i, (op, rhs) in enumerate(zip(node.ops, node.comparators, strict=True)):
            r_val = self.visit(rhs)
            if not ctx().live:
                break
            inverted = isinstance(op, ast.NotIn)
            raw_result = self.handle_comparison(node, op, l_val, r_val)
            if isinstance(op, ast.In | ast.NotIn) and raw_result._is_py_() and not _is_num(raw_result):
                raw_result = Num._accept_(bool(raw_result._as_py_()))
            if len(node.ops) == 1 and not isinstance(op, ast.In | ast.NotIn):
                return raw_result
            result = (
                self.convert_to_boolean_num(node, raw_result)
                if isinstance(op, ast.In | ast.NotIn)
                else self.ensure_boolean_num(raw_result)
            )
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
        if not ctx().live:
            return validate_value(None)
        args = []
        kwargs = {}
        if fn._is_py_():
            py_fn = fn._as_py_()
            callee_name = BUILTIN_IMPL_NAMES.get(
                id(py_fn), getattr(py_fn, "__qualname__", getattr(py_fn, "__name__", _type_name(fn)))
            )
        else:
            callee_name = _type_name(fn)
        for arg in node.args:
            if isinstance(arg, ast.Starred):
                value = self.visit(arg.value)
                if not ctx().live:
                    return validate_value(None)
                args.extend(self.handle_starred(value))
            else:
                value = self.visit(arg)
                if not ctx().live:
                    return validate_value(None)
                args.append(value)
        for keyword in node.keywords:
            if keyword.arg:
                value = self.visit(keyword.value)
                if not ctx().live:
                    return validate_value(None)
                if keyword.arg in kwargs:
                    raise TypeError(f"{callee_name}() got multiple values for keyword argument '{keyword.arg}'")
                kwargs[keyword.arg] = value
            else:
                value = self.visit(keyword.value)
                if not ctx().live:
                    return validate_value(None)
                if isinstance(value, DictImpl):
                    value_dict = value._as_dict_with_py_keys()
                    if not all(isinstance(k, str) for k in value_dict):
                        raise TypeError("keywords must be strings")
                    for key in value_dict:
                        if key in kwargs:
                            raise TypeError(f"{callee_name}() got multiple values for keyword argument '{key}'")
                    kwargs.update(value_dict)
                else:
                    raise TypeError(f"{callee_name}() argument after ** must be a mapping, not {_type_name(value)}")
        if not ctx().live:
            return validate_value(None)
        if fn._is_py_() and fn._as_py_() is _super and not args and not kwargs and "__class__" in self.globals:
            class_value = self.get_name("__class__")
            first_param_name = next(
                (
                    name
                    for name, param in self.bound_args.signature.parameters.items()
                    if param.kind in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
                ),
                None,
            )
            if first_param_name is not None:
                first_param_value = self.get_name(first_param_name)
                args = (validate_value(class_value), validate_value(first_param_value))
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
        if not ctx().live:
            return validate_value(None)
        slice_value = self.visit(node.slice)
        if not ctx().live:
            return validate_value(None)
        return self.handle_getitem(node, value, slice_value)

    def visit_Starred(self, node):
        raise NotImplementedError("Starred expressions are not supported")

    def visit_Name(self, node):
        return self.get_name(node.id)

    def get_name(self, name: str):
        used_parent_binding_values = self.used_parent_binding_values
        if name in used_parent_binding_values:
            return used_parent_binding_values[name]
        self.active_ctx = ctx()
        v = self
        while v:
            binding = v.active_ctx.scope.bindings.get(name)
            if binding is not None and type(binding) is not EmptyBinding:
                if type(binding) is ValueBinding:
                    binding.read_count += 1
                    result = binding.value
                else:
                    # Delegate to reproduce the error message from the scope
                    return v.active_ctx.scope.get_value(name)
                if v is not self:
                    child = self
                    while child is not v:
                        if child is self or child.is_running:
                            child.used_parent_binding_values[name] = result
                        child = child.parent
                    active_generator = next(
                        (
                            active_visitor
                            for active_visitor in reversed(_ACTIVE_VISITORS)
                            if active_visitor.is_generator
                        ),
                        None,
                    )
                    owner_is_transient_call = (
                        active_generator is not None
                        and v in _ACTIVE_VISITORS
                        and _ACTIVE_VISITORS.index(v) > _ACTIVE_VISITORS.index(active_generator)
                    )
                    if active_generator is not None and active_generator is not v and not owner_is_transient_call:
                        active_generator.generator_dependencies[id(v), name] = (v, name, result)
                return result
            if name in v.declared_locals:
                if v is not self:
                    raise NameError(
                        f"cannot access free variable '{name}' where it is not associated with a value "
                        "in enclosing scope"
                    )
                raise UnboundLocalError(
                    f"cannot access local variable '{name}' where it is not associated with a value"
                )
            v = v.parent
        if name in self.globals:
            value = self.globals[name]
            if value is ctx:
                raise ValueError("Unexpected use of ctx in non meta-function")
            return validate_value(BUILTIN_IMPLS.get(id(value), value))
        raise NameError(f"Name {name} is not defined")

    def visit_List(self, node):
        raise NotImplementedError("List literals are not supported")

    def visit_Tuple(self, node):
        values = []
        for elt in node.elts:
            if isinstance(elt, ast.Starred):
                value = self.visit(elt.value)
                if not ctx().live:
                    return validate_value(None)
                values.extend(self.handle_starred(value))
            else:
                value = self.visit(elt)
                if not ctx().live:
                    return validate_value(None)
                values.append(value)
        return validate_value(tuple(values))

    def visit_Slice(self, node):
        raise NotImplementedError("Slices are not supported")

    def handle_assign(self, target: ast.stmt | ast.expr, value: Value):
        if not ctx().live:
            return
        match target:
            case ast.Name(id=name):
                ctx().scope.set_value(name, value)
            case ast.Attribute(value=attr_value, attr=attr):
                attr_value = self.visit(attr_value)
                self.handle_setattr(target, attr_value, attr, value)
            case ast.Subscript(value=sub_value, slice=slice_expr):
                sub_value = self.visit(sub_value)
                if not ctx().live:
                    return
                slice_value = self.visit(slice_expr)
                if not ctx().live:
                    return
                self.handle_setitem(target, sub_value, slice_value, value)
            case ast.Tuple(elts=elts) | ast.List(elts=elts):
                if any(isinstance(elt, ast.Starred) for elt in elts):
                    raise NotImplementedError("Starred assignment is not supported")
                if not has_tuple_iter(value):
                    raise TypeError(f"Cannot unpack a value of type {_type_name(value)}")
                values = self.handle_starred(value)
                if len(values) > len(elts):
                    raise ValueError(f"too many values to unpack (expected {len(elts)}, got {len(values)})")
                if len(values) < len(elts):
                    raise ValueError(f"not enough values to unpack (expected {len(elts)}, got {len(values)})")
                for elt, v in zip(elts, values, strict=False):
                    self.handle_assign(elt, validate_value(v))
                    if not ctx().live:
                        return
            case ast.Starred():
                raise NotImplementedError("Starred assignment is not supported")
            case _:
                raise NotImplementedError("Unsupported assignment target")

    def handle_and(self, l_val: Value, r_expr: ast.expr) -> Value:
        ctx_init = ctx()
        l_val = self.ensure_boolean_num(l_val)

        if l_val._is_py_():
            if l_val._as_py_():
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

    def handle_getattr(
        self, node: ast.stmt | ast.expr, target: Value, key: str, *, report_errors: bool = True
    ) -> Value:
        if not ctx().live:
            return validate_value(None)

        def get_attribute():
            attribute_target = target
            was_constant = isinstance(attribute_target, ConstantValue)
            if isinstance(attribute_target, ConstantValue):
                attribute_target = attribute_target._as_py_()
            reject_custom_record_getattribute(attribute_target)
            target_type = type(attribute_target)
            descriptor = _resolve_descriptor(target_type, key)
            match descriptor:
                case property(fget=getter):
                    if getter is None:
                        error = AttributeError(f"property '{key}' of '{target_type.__name__}' object has no getter")
                    else:
                        try:
                            return self.handle_call(node, getter, attribute_target)
                        except Exception as e:
                            if not caused_by_attribute_error(e):
                                raise
                            _raise_property_getter_attribute_error(target_type, key, e)
                    fallback = _bind_special_method(attribute_target, "__getattr__")
                    if fallback is not _SPECIAL_METHOD_MISSING:
                        try:
                            return self.handle_call(node, fallback, validate_value(key))
                        except Exception as e:
                            if not caused_by_attribute_error(e):
                                raise
                            _raise_getattr_attribute_error(target_type, key, e)
                    raise error
                case None if (
                    not was_constant
                    and key not in getattr(attribute_target, "__dict__", {})
                    and not any(key in cls.__dict__ for cls in target_type.__mro__)
                ):
                    fallback = _bind_special_method(attribute_target, "__getattr__")
                    if fallback is not _SPECIAL_METHOD_MISSING:
                        try:
                            return self.handle_call(node, fallback, validate_value(key))
                        except Exception as e:
                            if not caused_by_attribute_error(e):
                                raise
                            _raise_getattr_attribute_error(target_type, key, e)
                    raise AttributeError(f"'{target_type.__name__}' object has no attribute '{key}'")
                case SonolusDescriptor() | FunctionType() | classmethod() | staticmethod() | None:
                    attribute = getattr(attribute_target, key)
                    if isinstance(attribute_target, type):
                        reject_instance_only_attribute(attribute_target, key, attribute)
                    return validate_value(attribute)
                case non_descriptor if _raw_special_method(type(non_descriptor), "__get__") is _SPECIAL_METHOD_MISSING:
                    return validate_value(getattr(attribute_target, key))
                case _:
                    raise TypeError(
                        f"Accessing attribute {key!r} on {_attribute_owner_name(attribute_target)} is not supported"
                    )

        # Keep descriptor handling aligned with builtin_impls._getattr.
        if report_errors:
            with self.reporting_errors_at_node(node):
                return get_attribute()
        return get_attribute()

    def handle_setattr(self, node: ast.stmt | ast.expr, target: Value, key: str, value: Value):
        if not ctx().live:
            return
        # Keep descriptor handling aligned with builtin_impls._setattr.
        with self.reporting_errors_at_node(node):
            if target._is_py_():
                target = target._as_py_()
            target_type = type(target)
            descriptor = _resolve_descriptor(target_type, key)
            match descriptor:
                case property(fset=setter):
                    if setter is None:
                        raise AttributeError(f"Cannot set attribute {key} because property has no setter")
                    self.handle_call(node, setter, target, value)
                case SonolusDescriptor():
                    setattr(target, key, value)
                case _:
                    if isinstance(target, type):
                        # The lookup above ran against the metaclass, which is what answers the class-level
                        # writes that do work (archetype_score_multiplier). Everything else lands here, where
                        # the class's own descriptors are what the author meant, so resolve those instead.
                        reject_instance_only_attribute(target, key, _resolve_descriptor(target, key))
                    raise TypeError(
                        f"Assigning to attribute {key!r} on {_attribute_owner_name(target)} is not supported"
                    )

    def handle_call[**P, R](
        self, node: ast.stmt | ast.expr, fn: Callable[P, R], /, *args: P.args, **kwargs: P.kwargs
    ) -> R | Value:
        """Handles a call to the given callable."""
        self.active_ctx = active_ctx = ctx()
        callback_state = active_ctx.callback_state
        debug_stack = callback_state.debug_stack
        debug_stack.append(f'File "{self.source_file}", line {node.lineno}, in {self.function_name}')
        try:
            if (
                isinstance(fn, Value)
                and fn._is_py_()
                and isinstance(fn._as_py_(), type)
                and issubclass(fn._as_py_(), Value)
            ):
                if callback_state.no_eval:
                    # Fast path equivalent to execute_at_node, which is a passthrough when no_eval is set
                    return validate_value(fn._as_py_()(*args, **kwargs))
                return validate_value(self.execute_at_node(node, fn._as_py_(), *args, **kwargs))
            elif callback_state.no_eval:
                # Fast path: execute_at_node is a passthrough when no_eval is set, so skip it and delegate
                # to compile_and_call (ctx() is known to be set here). compile_and_call validates its own
                # result and owns the callable dispatch -- including the FunctionType->eval_fn fast path --
                # so there's no outer validate_value and no duplicated dispatch logic here.
                return compile_and_call(fn, *args, **kwargs)
            else:
                return self.execute_at_node(node, lambda: validate_value(compile_and_call(fn, *args, **kwargs)))
        finally:
            debug_stack.pop()

    def handle_getitem(self, node: ast.stmt | ast.expr, target: Value, key: Value) -> Value:
        if not ctx().live:
            return validate_value(None)
        with self.reporting_errors_at_node(node):
            if target._is_py_() and isinstance(target._as_py_(), type):
                if not key._is_py_():
                    raise ValueError("Type parameters must be compile-time constants")
                return validate_value(target._as_py_()[key._as_py_()])
            elif target._is_py_() and getattr(target._as_py_(), "_is_comptime_value_", False):
                method = _bind_special_method(target._as_py_(), "__getitem__")
                if method is not _SPECIAL_METHOD_MISSING:
                    return self.handle_call(node, method, key)
            elif isinstance(target, Value):
                method = _bind_special_method(target, "__getitem__")
                if method is not _SPECIAL_METHOD_MISSING:
                    return self.handle_call(node, method, key)
            raise TypeError(f"'{_type_name(target)}' object is not subscriptable")

    def handle_setitem(self, node: ast.stmt | ast.expr, target: Value, key: Value, value: Value):
        if not ctx().live:
            return None
        with self.reporting_errors_at_node(node):
            if isinstance(target, Value):
                method = _bind_special_method(target, "__setitem__")
                if method is not _SPECIAL_METHOD_MISSING:
                    return self.handle_call(node, method, key, value)
            raise TypeError(f"'{_type_name(target)}' object does not support item assignment")

    def handle_delitem(self, node: ast.stmt | ast.expr, target: Value, key: Value):
        if not ctx().live:
            return None
        with self.reporting_errors_at_node(node):
            if isinstance(target, Value):
                method = _bind_special_method(target, "__delitem__")
                if method is not _SPECIAL_METHOD_MISSING:
                    return self.handle_call(node, method, key)
            raise TypeError(f"'{_type_name(target)}' object does not support item deletion")

    def handle_starred(self, value: Value) -> tuple[Value, ...]:
        if not ctx().live:
            return ()
        if has_tuple_iter(value):
            return tuple_iter(value)
        raise ValueError("Unsupported starred expression")

    def is_not_implemented(self, value):
        if type(value) is Num:
            # Num._as_py_() never returns NotImplemented
            return False
        value = validate_value(value)
        return value._is_py_() and value._as_py_() is NotImplemented

    def ensure_boolean_num(self, value) -> Num:
        if not ctx().live:
            return Num._accept_(0)
        if not _is_num(value):
            raise TypeError(f"Invalid type where a bool (Num) was expected: {_type_name(value)}")
        return value

    def convert_to_boolean_num(self, node, value: Value) -> Num:
        if not ctx().live:
            return Num._accept_(0)
        if _is_num(value):
            return value
        if isinstance(value, ConstantValue):
            return Num._accept_(bool(value._as_py_()))
        bool_method = _bind_special_method(value, "__bool__")
        if bool_method is not _SPECIAL_METHOD_MISSING:
            return self.ensure_boolean_num(self.handle_call(node, bool_method))
        len_method = _bind_special_method(value, "__len__")
        if len_method is not _SPECIAL_METHOD_MISSING:
            length = _validate_len_result(self.handle_call(node, len_method))
            if not ctx().live:
                return Num._accept_(0)
            if length._is_py_():
                return Num._accept_(length._as_py_() > 0)
            return length > Num._accept_(0)
        if isinstance(value, Record):
            return Num._accept_(1)
        raise TypeError(f"Converting {_type_name(value)} to bool is not supported")

    def arguments_to_signature(self, arguments: ast.arguments) -> inspect.Signature | None:
        parameters: list[inspect.Parameter] = []
        pos_only_count = len(arguments.posonlyargs)
        for i, arg in enumerate(arguments.posonlyargs):
            default_idx = i - pos_only_count - len(arguments.args) + len(arguments.defaults)
            default = self.visit(arguments.defaults[default_idx]) if default_idx >= 0 else None
            if not ctx().live:
                return None
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
            if not ctx().live:
                return None
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
            if not ctx().live:
                return None
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
        message = _exception_message(cause)

        def thrower() -> Never:
            raise CompilationError(message) from cause

        self.execute_at_node(node, thrower)

    def execute_at_node[**P, R](
        self, node: ast.stmt | ast.expr, fn: Callable[P, R], /, *args: P.args, **kwargs: P.kwargs
    ) -> R:
        """Executes the given function at the given node for a better traceback."""
        if ctx().no_eval:
            return fn(*args, **kwargs)

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
            compile(expr, filename=self.source_file, mode="eval").replace(co_name=self.function_name),
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


class Generator(TransientValue, SonolusIterator):
    def __init__(
        self,
        return_test: Num,
        owner: Num | None,
        entry: Context,
        exit_: Context,
        value: Maybe,
        dependencies: dict[tuple[int, str], tuple[Visitor, str, Value]],
        parent: Visitor,
        first_selection: list[tuple[Visitor, Context]],
        global_selection: list[tuple[Visitor, Context]],
    ):
        self.next_call_site = 0
        self.return_test = return_test
        self.owner = owner
        self.entry = entry
        self.exit = exit_
        self.value = value
        self.dependencies = dependencies
        self.parent = parent
        self.first_selection = first_selection
        self.global_selection = global_selection

    @meta_fn
    def next(self):
        if self.owner is not None:
            owner_id = ctx().callback_state.runtime_owner_id or ctx().allocate_runtime_owner_id()
            assert_true(
                Num.or_(self.owner == 0, self.owner == owner_id),
                "Generator cannot be used by more than one iterator consumer",
            )
            self.owner._set_(owner_id)
        active_generator = next(
            (active_visitor for active_visitor in reversed(_ACTIVE_VISITORS) if active_visitor.is_generator), None
        )
        if active_generator is not None and active_generator is not self.parent:
            active_generator_index = _ACTIVE_VISITORS.index(active_generator)
            active_generator.generator_dependencies.update(
                {
                    dependency_key: dependency
                    for dependency_key, dependency in self.dependencies.items()
                    if dependency[0] not in _ACTIVE_VISITORS
                    or _ACTIVE_VISITORS.index(dependency[0]) < active_generator_index
                }
            )
        for owner, key, value in self.dependencies.values():
            if not owner.is_generator or owner in _ACTIVE_VISITORS:
                continue
            observation_ctx = owner.generator_observation_ctx
            binding = observation_ctx.scope.get_binding(key) if observation_ctx is not None else EmptyBinding()
            if not isinstance(binding, ValueBinding) or binding.value is not value:
                raise NotImplementedError(
                    f"Nested generator captures changing local '{key}' from a suspended generator, "
                    "which is not supported"
                )
        self._validate_bindings()
        self.return_test._set_(self.next_call_site)
        after_ctx = ctx().new_disconnected()
        ctx().outgoing[None] = self.entry
        self.exit.outgoing[self.next_call_site] = after_ctx
        self.next_call_site += 1
        set_ctx(after_ctx)
        repeating_call_site = any(
            visitor.loop_head_ctxs or visitor.is_in_repeated_advance for visitor in _ACTIVE_VISITORS
        )
        selection = (
            self.first_selection if self.next_call_site == 1 and not repeating_call_site else self.global_selection
        )
        for owner, active_ctx in selection:
            owner.active_ctx = active_ctx
        return self.value

    def _validate_bindings(self):
        for owner, key, value in self.dependencies.values():
            result = owner.active_ctx.scope.get_value(key)
            if result is not value:
                raise ValueError(f"Binding '{key}' has been modified since the generator was created")

    def __iter__(self):
        return self

    @classmethod
    def _accepts_(cls, value: Any) -> bool:
        return isinstance(value, cls)

    @classmethod
    def _accept_(cls, value: Any) -> Generator:
        if not cls._accepts_(value):
            raise TypeError(f"Cannot accept value of type {type(value).__name__} as {cls.__name__}")
        return value

    def _is_py_(self) -> bool:
        return False

    def _as_py_(self) -> Any:
        raise NotImplementedError
