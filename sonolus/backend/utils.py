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
        self.results: list[ast.FunctionDef | ast.Lambda] = []
        self.current_fn = None

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self.results.append(node)
        outer_fn = self.current_fn
        self.current_fn = node
        self.generic_visit(node)
        self.current_fn = outer_fn

    def visit_Lambda(self, node: ast.Lambda):
        self.results.append(node)
        outer_fn = self.current_fn
        self.current_fn = node
        self.generic_visit(node)
        self.current_fn = outer_fn

    # Visitors have high overhead, so we detect generators here rather than in a separate pass.

    def visit_Yield(self, node):
        self.current_fn.has_yield = True

    def visit_YieldFrom(self, node):
        self.current_fn.has_yield = True


@cache
def get_functions(tree: ast.Module) -> list[ast.FunctionDef | ast.Lambda]:
    visitor = FindFunction(0)
    visitor.visit(tree)
    return visitor.results


def find_function(tree: ast.Module, line: int):
    for node in get_functions(tree):
        if node.lineno == line or (
            isinstance(node, ast.FunctionDef)
            and node.decorator_list
            and (node.decorator_list[-1].end_lineno <= line <= node.lineno)
        ):
            return node
    raise ValueError("Function not found")


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
