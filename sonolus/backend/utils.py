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
    try:
        return source_file, find_function(base_tree, start_line)
    except ValueError:
        raise ValueError(f"Function {fn} not found in source file {source_file}") from None


@cache
def get_signature(fn: Callable) -> inspect.Signature:
    return inspect.signature(fn)


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
        node.declared_locals = set()
        outer_fn = self.current_fn
        self.current_fn = node
        self.generic_visit(node)
        self.current_fn = outer_fn

    def visit_Lambda(self, node: ast.Lambda):
        self.results.append(node)
        node.declared_locals = set()
        outer_fn = self.current_fn
        self.current_fn = node
        self.generic_visit(node)
        self.current_fn = outer_fn

    # Visitors have high overhead, so we detect generators here rather than in a separate pass.

    def visit_Yield(self, node):
        self.current_fn.has_yield = True
        self.generic_visit(node)

    def visit_YieldFrom(self, node):
        self.current_fn.has_yield = True
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        # A bare annotation makes the name local to the enclosing function for the whole body, including reads
        # that precede it, so it has to be known before the body is visited. Only an unparenthesized name counts:
        # `(x): int` is `simple=0` and leaves x resolving outward.
        if node.value is None and node.simple and self.current_fn is not None:
            self.current_fn.declared_locals.add(node.target.id)
        self.generic_visit(node)


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
            # inspect.getsourcelines / co_firstlineno returns exactly the first decorator's
            # start line for a decorated function, so match that line precisely rather than the
            # whole span up to the def line (which would also claim a lambda nested in the
            # decorator).
            and node.decorator_list[0].lineno == line
        ):
            return node
    raise ValueError("Function not found")


class ScanWrites(ast.NodeVisitor):
    def __init__(self):
        self.writes = []

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Store | ast.Delete):
            self.writes.append(node.id)

    def visit_FunctionDef(self, node):
        # A nested def binds its name through a plain str attribute rather than a Name node, so it must be
        # recorded here to be treated as loop-carried.
        self.writes.append(node.name)
        self.generic_visit(node)

    def visit_MatchAs(self, node):
        if node.name is not None:
            self.writes.append(node.name)
        self.generic_visit(node)

    def visit_MatchStar(self, node):
        if node.name is not None:
            self.writes.append(node.name)
        self.generic_visit(node)

    def visit_MatchMapping(self, node):
        if node.rest is not None:
            self.writes.append(node.rest)
        self.generic_visit(node)


def scan_writes(node: ast.AST) -> set[str]:
    visitor = ScanWrites()
    visitor.visit(node)
    return set(visitor.writes)
