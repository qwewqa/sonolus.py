"""Exception hook to filter out compiler internal frames from tracebacks."""

import sys
from types import TracebackType


def should_filter_traceback(tb: TracebackType | None) -> bool:
    return tb is not None and (
        tb.tb_frame.f_globals.get("_filter_traceback_", False) or should_filter_traceback(tb.tb_next)
    )


def is_compiler_internal(tb: TracebackType):
    return tb.tb_frame.f_locals.get("_compiler_internal_", False) or tb.tb_frame.f_globals.get(
        "_compiler_internal_", False
    )


def is_traceback_root(tb: TracebackType | None) -> bool:
    return tb.tb_frame.f_locals.get("_traceback_root_", False) or tb.tb_frame.f_globals.get("_traceback_root_", False)


def filter_traceback(tb: TracebackType | None) -> TracebackType | None:
    if tb is None:
        return None
    if is_compiler_internal(tb):
        return filter_traceback(tb.tb_next)
    tb.tb_next = filter_traceback(tb.tb_next)
    return tb


def truncate_traceback(tb: TracebackType | None) -> TracebackType | None:
    if tb is None:
        return None
    if is_compiler_internal(tb):
        return truncate_traceback(tb.tb_next)
    if is_traceback_root(tb):
        return tb
    else:
        return truncate_traceback(tb.tb_next)


def excepthook(exc, value, tb):
    import traceback

    if should_filter_traceback(tb):
        tb = filter_traceback(tb)
    traceback.print_exception(exc, value, tb)


def print_simple_traceback(exc, value, tb):
    import traceback

    if should_filter_traceback(tb):
        tb = filter_traceback(tb)
        truncated = truncate_traceback(tb)
        tb = truncated if truncated is not None else tb
        cause = value.__cause__
        value.__cause__ = None
        traceback.print_exception(exc, value, tb)
        value.__cause__ = cause
    else:
        traceback.print_exception(exc, value, tb)


def install_excepthook():
    sys.excepthook = excepthook
