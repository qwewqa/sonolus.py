import pytest

from sonolus.script.internal.context import enable_debug
from sonolus.script.internal.meta_fn import meta_fn
from sonolus.script.options import options, slider_option
from tests.script.conftest import compile_fn


@options
class _WriteOpts:
    foo: float = slider_option(default=0.5, min=0.0, max=1.0, step=0.1)


def test_option_unchecked_write_does_not_raise():
    @meta_fn
    def write():
        _WriteOpts.foo = 0.7

    # Non-debug default: options are read-only, so a write must still raise (regression guard).
    with pytest.raises(AttributeError, match="read-only"):
        compile_fn(write)

    # Debug (unchecked_writes=True): the write is emitted and must complete cleanly.
    # Before the fix, __set__ emitted the write and then unconditionally raised AttributeError.
    with enable_debug():
        compile_fn(write)
