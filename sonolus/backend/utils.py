# ruff: noqa: N802
import ast
import inspect
from collections.abc import Callable
from functools import cache
from pathlib import Path


@cache
def get_function(fn: Callable) -> tuple[str, ast.FunctionDef]:
    # This preserves both line number and column number in the returned node
    source_file = inspect.getsourcefile(fn)
    _, start_line = inspect.getsourcelines(fn)
    base_tree = get_tree_from_file(source_file)
    return source_file, find_function(base_tree, start_line)


@cache
def get_tree_from_file(file: str | Path) -> ast.Module:
    return ast.parse(Path(file).read_text(encoding="utf-8"))


class FindFunction(ast.NodeVisitor):
    def __init__(self, line):
        self.line = line
        self.node: ast.FunctionDef | None = None

    def visit_FunctionDef(self, node: ast.FunctionDef):
        if node.lineno == self.line or (
            node.decorator_list and (node.decorator_list[-1].end_lineno <= self.line <= node.lineno)
        ):
            self.node = node
        else:
            self.generic_visit(node)

    def visit_Lambda(self, node: ast.Lambda):
        if node.lineno == self.line:
            if self.node is not None:
                raise ValueError("Multiple functions defined on the same line are not supported")
            self.node = node
        else:
            self.generic_visit(node)


def find_function(tree: ast.Module, line: int):
    visitor = FindFunction(line)
    visitor.visit(tree)
    return visitor.node


class ScanWrites(ast.NodeVisitor):
    def __init__(self):
        self.writes = []

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Store | ast.Delete):
            self.writes.append(node.id)


def scan_writes(node: ast.AST) -> set[str]:
    visitor = ScanWrites()
    visitor.visit(node)
    return set(visitor.writes)
