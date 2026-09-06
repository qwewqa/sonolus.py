import ast
import re
import traceback

import pytest

from sonolus.backend.utils import get_function, get_functions, get_tree_from_file
from sonolus.script.internal import visitor
from sonolus.script.internal.error import CompilationError
from tests.script.conftest import run_and_validate, run_compiled


def test_loop_writes_are_cached_independently_and_immutably(monkeypatch):
    first, second, empty = ast.parse(
        """
for target in (iter_write := values):
    body_write = target
else:
    else_write = 1

while (test_write := condition):
    def nested(default=(default_write := 1)):
        nested_write = 2
else:
    while_else_write = 3

while condition:
    pass
"""
    ).body

    original_scan_writes = visitor.scan_writes
    calls = []

    def counting_scan_writes(*nodes):
        calls.append(nodes)
        return original_scan_writes(*nodes)

    monkeypatch.setattr(visitor, "scan_writes", counting_scan_writes)

    first_writes = visitor._loop_writes(first)
    second_writes = visitor._loop_writes(second)
    empty_writes = visitor._loop_writes(empty)

    assert all(isinstance(writes, frozenset) for writes in (first_writes, second_writes, empty_writes))
    assert first_writes == frozenset({"target", "body_write"})
    assert second_writes == frozenset({"test_write", "nested", "default_write"})
    assert empty_writes == frozenset()
    assert len(calls) == 3
    assert visitor._loop_writes(first) is first_writes
    assert visitor._loop_writes(second) is second_writes
    assert visitor._loop_writes(empty) is empty_writes
    assert len(calls) == 3


def test_pattern_star_results_are_cached_for_true_and_false(monkeypatch):
    match_node = ast.parse(
        """
match subject:
    case [[value, *rest]]:
        pass
    case [first, second]:
        pass
"""
    ).body[0]
    star_pattern, plain_pattern = (case.pattern for case in match_node.cases)

    original_walk = ast.walk
    calls = []

    def counting_walk(node):
        calls.append(node)
        return original_walk(node)

    monkeypatch.setattr(ast, "walk", counting_walk)

    assert visitor._pattern_contains_star(star_pattern) is True
    assert visitor._pattern_contains_star(plain_pattern) is False
    assert calls == [star_pattern, plain_pattern]
    assert visitor._pattern_contains_star(star_pattern) is True
    assert visitor._pattern_contains_star(plain_pattern) is False
    assert calls == [star_pattern, plain_pattern]


def test_parser_cache_replacement_recomputes_ast_metadata(tmp_path, monkeypatch):
    source = tmp_path / "source.py"
    source.write_text("def callback():\n    while condition:\n        value = 1\n", encoding="utf-8")

    original_scan_writes = visitor.scan_writes
    calls = 0

    def counting_scan_writes(*nodes):
        nonlocal calls
        calls += 1
        return original_scan_writes(*nodes)

    monkeypatch.setattr(visitor, "scan_writes", counting_scan_writes)

    first_tree = get_tree_from_file(source)
    first_loop = get_functions(first_tree)[0].body[0]
    assert visitor._loop_writes(first_loop) == frozenset({"value"})
    assert visitor._loop_writes(first_loop) == frozenset({"value"})
    assert calls == 1

    get_functions.cache_clear()
    get_function.cache_clear()
    get_tree_from_file.cache_clear()

    second_tree = get_tree_from_file(source)
    second_loop = get_functions(second_tree)[0].body[0]
    assert second_tree is not first_tree
    assert second_loop is not first_loop
    assert visitor._loop_writes(second_loop) == frozenset({"value"})
    assert calls == 2


def test_source_backed_recompilation_reuses_loop_metadata(monkeypatch):
    def fn():
        value = 0
        while value < 3:
            value += 1
        return value

    function_node = get_function(fn)[1]
    loop = next(node for node in function_node.body if isinstance(node, ast.While))
    loop_scan_nodes = (loop.test, *loop.body)
    original_scan_writes = visitor.scan_writes
    loop_scans = 0

    def counting_scan_writes(*nodes):
        nonlocal loop_scans
        if len(nodes) == len(loop_scan_nodes) and all(
            actual is expected for actual, expected in zip(nodes, loop_scan_nodes, strict=True)
        ):
            loop_scans += 1
        return original_scan_writes(*nodes)

    monkeypatch.setattr(visitor, "scan_writes", counting_scan_writes)

    assert run_and_validate(fn) == 3
    visitor.clear_frontend_caches()
    assert run_and_validate(fn) == 3
    assert loop_scans == 1


def test_nested_star_failure_is_identical_on_cached_ast_revisit():
    def fn():
        subject = (1, 2)
        match subject:
            case [[value, *rest]]:  # ruff: ignore[unused-variable]
                return value
            case _:
                return -1

    message = re.escape("Star sub-patterns (e.g. `case [a, *rest]:`) in sequence match patterns are not supported")
    errors = []
    for _ in range(2):
        with pytest.raises(CompilationError, match=message) as exc_info:
            run_compiled(fn)
        errors.append(exc_info.value)

    assert str(errors[1]) == str(errors[0])
    assert type(errors[1].__cause__) is type(errors[0].__cause__) is NotImplementedError
    assert str(errors[1].__cause__) == str(errors[0].__cause__)
    match_line = next(node.lineno for node in get_function(fn)[1].body if isinstance(node, ast.Match))
    for error in errors:
        source_lines = [
            frame.lineno
            for frame in traceback.extract_tb(error.__traceback__)
            if frame.filename == fn.__code__.co_filename and frame.name == fn.__name__
        ]
        assert source_lines == [match_line]


def test_unreachable_star_arm_is_not_rejected():
    def fn():
        subject = (1, 2)
        match subject:
            case (1, 2):
                return 7
            case [*rest]:
                return len(rest)
        return -1

    assert run_and_validate(fn) == 7
