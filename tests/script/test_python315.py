import sys
import textwrap

import pytest

from sonolus.script.internal.error import CompilationError
from tests.script.conftest import compile_fn, run_and_validate

pytestmark = pytest.mark.skipif(sys.version_info < (3, 15), reason="Requires Python 3.15 syntax")


@pytest.mark.parametrize(
    "expression",
    [
        "(*values for values in ((1, 2), (), (3,)))",
        "(*range(size) for size in range(4))",
        "(*range(size) for size in range(5) if size % 2)",
        "(*range(size + offset) for offset in range(2) for size in range(3))",
        "(*(value * 2 for value in range(size)) for size in range(4))",
        "(*values for values in ((), ()))",
        "(*range(first + last) for first, last in ((0, 1), (1, 2)))",
        "(*(*range(size) for size in range(limit)) for limit in range(4))",
        "(*mapping for mapping in ({1: 10, 2: 20}, {}, {3: 30}))",
        "(*values for values in ({1, 2}, {3, 4}))",
    ],
)
def test_unpacking_generator_expression(tmp_path, expression):
    source = textwrap.dedent(
        f"""
        from sonolus.script.debug import debug_log

        def fn():
            total = 0
            for value in {expression}:
                debug_log(value)
                total += value
            return total
        """
    )
    path = tmp_path / "unpacking_generator.py"
    path.write_text(source, encoding="utf-8")
    namespace = {}
    exec(compile(source, str(path), "exec"), namespace)
    run_and_validate(namespace["fn"])


def test_unpacking_generator_expression_evaluation_order(tmp_path):
    source = textwrap.dedent(
        """
        from sonolus.script.debug import debug_log

        def outer():
            debug_log(10)
            return range(3)

        def inner(size):
            debug_log(20 + size)
            return range(size)

        def fn():
            values = (*inner(size) for size in outer())
            debug_log(30)
            total = 0
            for value in values:
                debug_log(40 + value)
                total += value
            return total
        """
    )
    path = tmp_path / "unpacking_generator_order.py"
    path.write_text(source, encoding="utf-8")
    namespace = {}
    exec(compile(source, str(path), "exec"), namespace)
    run_and_validate(namespace["fn"])


def make_python315_callback(tmp_path, source):
    source = textwrap.dedent(source)
    path = tmp_path / "python315_callback.py"
    path.write_text(source, encoding="utf-8")
    namespace = {}
    exec(compile(source, str(path), "exec"), namespace)
    return namespace["fn"]


@pytest.mark.parametrize("container", ["set", "frozenset", "tuple"])
def test_unpacking_generator_host_containers(tmp_path, container):
    fn = make_python315_callback(
        tmp_path,
        """
        from sonolus.script.debug import debug_log

        groups = (CONTAINER((1, 2)), CONTAINER(()), CONTAINER((3, 4)))

        def fn():
            total = 0
            for value in (*group for group in groups):
                debug_log(value)
                total += value
            return total
        """.replace("CONTAINER", container),
    )
    run_and_validate(fn)


def test_unpacking_generator_enum_type(tmp_path):
    fn = make_python315_callback(
        tmp_path,
        """
        from enum import IntEnum
        from sonolus.script.debug import debug_log

        class Color(IntEnum):
            RED = 1
            GREEN = 2
            BLUE = 3

        def fn():
            total = 0
            for value in (*Color for _ in range(2)):
                debug_log(value)
                total += value
            return total
        """,
    )
    run_and_validate(fn)


@pytest.mark.parametrize("consume", ["next(values)", "sum(values)", "0"])
def test_unpacking_generator_iterator_protocol_evaluation_order(tmp_path, consume):
    fn = make_python315_callback(
        tmp_path,
        """
        from sonolus.script.debug import debug_log
        from sonolus.script.iterator import SonolusIterator
        from sonolus.script.maybe import Nothing, Some
        from sonolus.script.record import Record

        class LoggedIterator(Record, SonolusIterator):
            current: int
            limit: int
            label: int

            def __iter__(self):
                debug_log(self.label + 1)
                return self

            def next(self):
                debug_log(self.label + 10 + self.current)
                if self.current >= self.limit:
                    return Nothing
                result = self.current
                self.current += 1
                return Some(result)

        class LoggedIterable(Record):
            limit: int
            label: int

            def __iter__(self):
                debug_log(self.label)
                return LoggedIterator(0, self.limit, self.label)

        def inner(size):
            debug_log(100 + size)
            return LoggedIterable(size, 200 + 100 * size)

        def fn():
            values = (*inner(size) for size in LoggedIterable(3, 10))
            debug_log(20)
            return CONSUME
        """.replace("CONSUME", consume),
    )
    run_and_validate(fn)


def test_unpacking_generator_early_break_preserves_inner_laziness(tmp_path):
    fn = make_python315_callback(
        tmp_path,
        """
        from sonolus.script.debug import debug_log

        def inner(size):
            for value in range(size):
                debug_log(10 * size + value)
                yield value
            debug_log(100 + size)

        def fn():
            total = 0
            for value in (*inner(size) for size in range(5)):
                debug_log(value)
                total += value
                if value == 1:
                    break
            debug_log(200)
            return total
        """,
    )
    run_and_validate(fn)


@pytest.mark.parametrize("enabled", [False, True])
def test_unpacking_generator_nested_runtime_filters(tmp_path, enabled):
    fn = make_python315_callback(
        tmp_path,
        """
        import random
        from sonolus.script.debug import debug_log

        def check(value):
            debug_log(value)
            return random.randrange(0, 1) == 0 and value % 2 == 0 and ENABLED

        def inner(value):
            debug_log(100 + value)
            return range(value)

        def fn():
            total = 0
            values = (*inner(size) for offset in range(3) if check(offset)
                      for size in range(offset + 1) if check(size))
            for value in values:
                debug_log(200 + value)
                total += value
            return total
        """.replace("ENABLED", repr(enabled)),
    )
    run_and_validate(fn)


def test_unpacking_generator_mutable_records_and_arrays(tmp_path):
    fn = make_python315_callback(
        tmp_path,
        """
        from sonolus.script.array import Array
        from sonolus.script.containers import Box
        from sonolus.script.debug import debug_log

        def fn():
            groups = Array(Array(Box(1), Box(2)), Array(Box(3), Box(4)))
            total = 0
            for record in (*group for group in groups):
                debug_log(record.value)
                record.value += 10
                groups[1][1].value += 1
                total += record.value
            for group in groups:
                for record in group:
                    debug_log(record.value)
            return total
        """,
    )
    run_and_validate(fn)


def test_unpacking_generator_yields_array_references(tmp_path):
    fn = make_python315_callback(
        tmp_path,
        """
        from sonolus.script.array import Array
        from sonolus.script.debug import debug_log

        def fn():
            groups = Array(Array(1, 2), Array(3, 4))
            total = 0
            for group in (*(group,) for group in groups):
                debug_log(group[0])
                group[0] += 10
                total += group[0]
            debug_log(groups[0][0])
            debug_log(groups[1][0])
            return total
        """,
    )
    run_and_validate(fn)


def test_unpacking_generator_scope_and_shadowing(tmp_path):
    fn = make_python315_callback(
        tmp_path,
        """
        from sonolus.script.debug import debug_log

        def fn():
            values = ((1, 2), (), (3,))
            total = sum((*values for values in values))
            for group in values:
                for value in group:
                    debug_log(value)
            size = 4
            total += sum((*(size for size in range(size)) for size in range(size)))
            debug_log(size)
            return total
        """,
    )
    run_and_validate(fn)


def test_unpacking_generator_inner_generator_early_return(tmp_path):
    fn = make_python315_callback(
        tmp_path,
        """
        from sonolus.script.debug import debug_log

        def inner(size):
            if size == 1:
                return
            yield size
            return None

        def fn():
            total = 0
            for value in (*inner(size) for size in range(3)):
                debug_log(value)
                total += value
            return total
        """,
    )
    run_and_validate(fn)


@pytest.mark.parametrize(("expression", "type_name"), [("None", "NoneType"), ("NonIterable()", "NonIterable")])
def test_unpacking_generator_rejects_non_iterable_element(tmp_path, expression, type_name):
    fn = make_python315_callback(
        tmp_path,
        """
        from sonolus.script.record import Record

        class NonIterable(Record):
            pass

        def fn():
            return sum((*value for value in (ELEMENT,)))
        """.replace("ELEMENT", expression),
    )
    with pytest.raises(TypeError, match=f"'{type_name}' object is not iterable"):
        run_and_validate(fn)


def test_unpacking_generator_rejects_non_none_inner_generator_return(tmp_path):
    fn = make_python315_callback(
        tmp_path,
        """
        def inner():
            yield 1
            return 10

        def fn():
            return sum((*inner() for _ in range(2)))
        """,
    )
    assert fn() == 2
    with pytest.raises(CompilationError, match="Generator function return statements must return None"):
        compile_fn(fn)
