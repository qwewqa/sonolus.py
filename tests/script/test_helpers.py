import pytest

from sonolus.script.debug import terminate
from sonolus.script.internal.context import ctx
from sonolus.script.internal.impl import meta_fn
from tests.script.conftest import run_and_validate


def test_run_and_validate_error_if_returned_results_differ():
    @meta_fn
    def fn():
        if ctx():
            return 1
        else:
            return 2

    with pytest.raises(AssertionError):
        run_and_validate(fn)


def test_run_and_validate_error_if_only_py_raises():
    @meta_fn
    def fn():
        if ctx():
            return 1
        else:
            raise RuntimeError()

    with pytest.raises(AssertionError):
        run_and_validate(fn)


def test_run_and_validate_error_if_only_compiled_raises():
    def terminate_if_compiled():
        if ctx():
            terminate()

    @meta_fn
    def fn():
        terminate_if_compiled()
        return 1

    with pytest.raises(AssertionError):
        run_and_validate(fn)


def test_run_and_validate_raises_if_both_error():
    @meta_fn
    def fn():
        terminate()
        return 1

    with pytest.raises(RuntimeError, match="Terminated"):
        run_and_validate(fn)


def test_run_and_validate_success_if_both_return_same():
    @meta_fn
    def fn():
        return 1

    run_and_validate(fn)
