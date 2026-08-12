"""A nested class or async def the tracer never reaches must not change the function containing it.

The live shapes are rejected, and tests/script/test_unsupported_constructs.py pins those messages. These
snippets put the same two constructs in a statically false arm, which the compiler does not check: what is
under test is the whole-file pre-pass that stamps `declared_locals` and `has_yield` on the enclosing function
before any statement is traced, so an unreachable nested scope can still break the function around it.
"""

from tests.script.conftest import run_and_validate

GLOBAL_X = 7.0


def dead_class_annotation():
    if False:

        class _Stub:
            GLOBAL_X: int

    return GLOBAL_X


def dead_async_generator():
    if False:

        async def _agen():  # noqa: RUF029
            yield 1

    return 5.0


def test_dead_class_annotation_leaves_the_global_readable():
    assert run_and_validate(dead_class_annotation) == 7.0


def test_dead_async_generator_leaves_the_function_a_plain_function():
    assert run_and_validate(dead_async_generator) == 5.0
