import re
import sys

import pytest

from sonolus.script.internal.error import CompilationError
from tests.script.conftest import run_and_validate, run_compiled


def _callback_from_expression(tmp_path, expression):
    source = f"def callback():\n    values = {expression}\n    return 1\n"
    path = tmp_path / "comprehension_boundary.py"
    path.write_text(source, encoding="utf-8")
    namespace = {}
    exec(compile(source, str(path), "exec"), namespace)
    return namespace["callback"]


@pytest.mark.skipif(sys.version_info < (3, 15), reason="Comprehension unpacking requires Python 3.15")
@pytest.mark.parametrize(
    ("expression", "message"),
    [
        ("[*values for values in ((1, 2),)]", "List comprehensions are not supported"),
        ("{*values for values in ((1, 2),)}", "Set comprehensions are not supported"),
        ("{**mapping for mapping in ({1: 2},)}", "Dict comprehensions are not supported"),
        (
            "(*(values := (1, 2)) for _ in range(1))",
            "Assignment expressions (`:=`) in a generator expression are not supported.",
        ),
    ],
)
def test_unpacking_comprehension_restrictions(tmp_path, expression, message):
    # These constructs have Python semantics but are outside the compiled subset.
    callback = _callback_from_expression(tmp_path, expression)
    with pytest.raises(CompilationError, match=re.escape(message)):
        run_compiled(callback)


@pytest.mark.parametrize("unpack", [False, True])
@pytest.mark.parametrize("async_clause", [0, 1])
def test_async_generator_expressions_are_rejected(tmp_path, unpack, async_clause):
    if unpack and sys.version_info < (3, 15):
        pytest.skip("Generator unpacking requires Python 3.15")
    element = "*range(size)" if unpack else "size"
    outer = "async for" if async_clause == 0 else "for"
    inner = "async for" if async_clause == 1 else "for"
    expression = f"({element} {outer} size in range(3) {inner} _ in range(1))"
    callback = _callback_from_expression(tmp_path, expression)
    # The subset has no async iteration protocol, even when the result is never consumed.
    with pytest.raises(CompilationError, match="Async generator expressions are not supported"):
        run_compiled(callback)


@pytest.mark.parametrize(
    "expression",
    [
        "(await operation() for _ in ())",
        "(value for value in () if await operation())",
        "(value for value in () for _ in await operation())",
        "(value for target[await operation()] in ())",
        "([value async for value in source] for _ in ())",
        "({value async for value in source} for _ in ())",
        "({value: value async for value in source} for _ in ())",
        "((value for value in await operation()) for _ in ())",
        "((lambda arg=await operation(): arg) for _ in ())",
        pytest.param(
            "(*(await operation()) for _ in ())",
            marks=pytest.mark.skipif(sys.version_info < (3, 15), reason="Generator unpacking requires Python 3.15"),
        ),
    ],
)
def test_implicit_async_generator_expressions_are_rejected_even_when_empty(tmp_path, expression):
    callback = _callback_from_expression(tmp_path, expression)
    # Empty iteration must not hide an unsupported async generator from the compiler.
    with pytest.raises(CompilationError, match="Async generator expressions are not supported"):
        run_compiled(callback)


@pytest.mark.parametrize(
    "expression",
    [
        "((value async for value in source) for _ in ())",
        "((lambda: (await operation() for _ in ())) for _ in ())",
    ],
)
def test_nested_async_scopes_do_not_make_empty_outer_generator_async(tmp_path, expression):
    callback = _callback_from_expression(tmp_path, expression)
    assert run_and_validate(callback) == 1
