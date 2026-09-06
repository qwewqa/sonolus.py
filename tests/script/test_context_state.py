"""Context singleton ownership and unsupported compiled access checks."""

import gc
import traceback
import weakref

import pytest

from sonolus.backend.mode import Mode
from sonolus.script.internal import context as context_module
from sonolus.script.internal.context import (
    CallbackContextState,
    Context,
    ModeContextState,
    ProjectContextState,
    RuntimeChecks,
    ctx,
    set_ctx,
    using_ctx,
)
from sonolus.script.internal.context import ctx as imported_ctx
from sonolus.script.internal.error import CompilationError
from tests.script.conftest import compile_fn, run_and_validate


def make_context():
    return Context(
        ProjectContextState(runtime_checks=RuntimeChecks.NONE),
        ModeContextState(Mode.PLAY),
        CallbackContextState("updateSequential"),
    )


def test_set_ctx_returns_previous_identity_and_accepts_same_object():
    original = set_ctx(None)
    first = make_context()
    second = make_context()
    try:
        assert ctx() is None
        assert set_ctx(first) is None
        assert ctx() is first
        assert set_ctx(first) is first
        assert ctx() is first
        assert set_ctx(second) is first
        assert ctx() is second
        assert set_ctx(None) is second
        assert ctx() is None
    finally:
        set_ctx(original)


def test_using_ctx_restores_nested_none_and_exception_scopes():
    original = ctx()
    first = make_context()
    second = make_context()

    def fail_inside_scope():
        with using_ctx(second):
            assert ctx() is second
            raise RuntimeError("nested context failed")

    with using_ctx(first):
        assert ctx() is first
        with using_ctx(None):
            assert ctx() is None
            with using_ctx(second):
                assert ctx() is second
            assert ctx() is None
        assert ctx() is first
        with pytest.raises(RuntimeError, match="nested context failed"):
            fail_inside_scope()
        assert ctx() is first
    assert ctx() is original


@pytest.mark.parametrize("cyclic", [False, True], ids=["reference-counted", "cyclic"])
def test_active_context_owns_current_and_releases_previous(cyclic):
    original = set_ctx(None)
    value = make_context()
    if cyclic:
        value.outgoing[None] = value
    reference = weakref.ref(value)
    try:
        set_ctx(value)
        del value
        gc.collect()
        assert reference() is ctx()
        assert reference() is not None
        previous = set_ctx(None)
        assert previous is reference()
        gc.collect()
        assert reference() is previous
        del previous
        if cyclic:
            gc.collect()
        assert reference() is None
    finally:
        set_ctx(original)


def direct_context_access():
    return ctx()


def imported_context_access():
    return imported_ctx()


def module_context_access():
    return context_module.ctx()


@pytest.mark.parametrize(
    ("fn", "message", "cause_type"),
    [
        (direct_context_access, "Unexpected use of ctx in non meta-function", ValueError),
        (imported_context_access, "Unexpected use of ctx in non meta-function", ValueError),
        (module_context_access, "Unsupported value: .*ctx", TypeError),
    ],
)
def test_compiled_context_access_is_rejected_without_leaking_state(fn, message, cause_type):
    original = ctx()
    with pytest.raises(CompilationError, match=message) as caught:
        compile_fn(fn)
    assert ctx() is original
    assert isinstance(caught.value.__cause__, cause_type)
    frames = traceback.extract_tb(caught.value.__traceback__)
    assert any(frame.filename == fn.__code__.co_filename and frame.name == fn.__name__ for frame in frames)

    def valid_callback():
        return 23

    assert run_and_validate(valid_callback) == 23
    assert ctx() is original
