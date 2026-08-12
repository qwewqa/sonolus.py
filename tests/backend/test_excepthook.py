import sys
from types import SimpleNamespace

from sonolus.backend.excepthook import filter_traceback, should_filter_traceback, truncate_traceback


class _FakeTraceback:
    def __init__(self, frame, next_):
        self.tb_frame = frame
        self.tb_next = next_


def _fake_traceback(length: int, *, filter_last: bool = False, root_last: bool = False):
    tb = None
    for i in reversed(range(length)):
        globals_ = {"_filter_traceback_": filter_last and i == length - 1}
        locals_ = {"_traceback_root_": root_last and i == length - 1}
        tb = _FakeTraceback(SimpleNamespace(f_globals=globals_, f_locals=locals_), tb)
    return tb


def test_traceback_helpers_handle_deep_chains_iteratively():
    old_limit = sys.getrecursionlimit()
    try:
        sys.setrecursionlimit(300)
        tb = _fake_traceback(500, filter_last=True, root_last=True)

        assert should_filter_traceback(tb)
        assert filter_traceback(tb) is tb
        root = truncate_traceback(tb)
        assert root is not None
        assert root.tb_next is None
    finally:
        sys.setrecursionlimit(old_limit)


def test_filter_traceback_removes_deep_internal_chain():
    old_limit = sys.getrecursionlimit()
    try:
        sys.setrecursionlimit(300)
        tb = _fake_traceback(500)
        current = tb
        while current.tb_next is not None:
            current.tb_frame.f_locals["_compiler_internal_"] = True
            current = current.tb_next

        assert filter_traceback(tb) is current
    finally:
        sys.setrecursionlimit(old_limit)
