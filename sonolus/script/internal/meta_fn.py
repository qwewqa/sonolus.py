from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import overload


@overload
def meta_fn[T: Callable](fn: T) -> T: ...


@overload
def meta_fn[T: Callable](show_in_stack: bool) -> Callable[[T], T]: ...


def meta_fn(fn=None, *, show_in_stack: bool = True):
    """Marks a function as a meta function to be called directly without the AST visitor.

    This can also improve performance in some cases by avoiding the overhead of the AST visitor.
    """

    # noinspection PyShadowingNames
    def decorator(fn):
        from sonolus.backend import visitor
        from sonolus.backend.utils import get_function

        base_fn = fn
        while hasattr(base_fn, "__wrapped__"):
            base_fn = base_fn.__wrapped__

        function_name = getattr(base_fn, "__name__", "<unnamed>")
        module = getattr(base_fn, "__module__", None)
        if module is not None:
            qualified_name = f"{module}.{getattr(base_fn, '__qualname__', function_name)}"
        else:
            qualified_name = f"<unknown>.{getattr(base_fn, '__qualname__', function_name)}"
        source_file, node = get_function(base_fn)

        @wraps(fn)
        def wrapper(*args, **kwargs):
            if ctx():
                completion_timer = visitor.mark_start(qualified_name)
                debug_stack = ctx().callback_state.debug_stack
                try:
                    if show_in_stack:
                        debug_stack.append(f'File "{source_file}", line {node.lineno}, in {function_name}')
                    return fn(*args, **kwargs)
                finally:
                    if show_in_stack:
                        debug_stack.pop()
                    completion_timer()
            return fn(*args, **kwargs)

        wrapper._meta_fn_ = True
        return wrapper

    if fn is None:
        return decorator
    return decorator(fn)


# To indicate this was used for performance reasons rather than functionality
perf_meta_fn = meta_fn

from sonolus.script.internal.context import ctx
