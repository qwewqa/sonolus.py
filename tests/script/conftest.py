import functools
from collections.abc import Callable


def dual_run[T: Callable](fn: T) -> T:
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        regular_result = fn(*args, **kwargs)

    return wrapper
