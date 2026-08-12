import ast
import inspect
from collections.abc import Callable
from functools import cache
from pathlib import Path
from types import CodeType


class FunctionNotFoundError(ValueError):
    """No definition in the tree claims the requested line."""


@cache
def get_function(fn: Callable) -> tuple[str, ast.FunctionDef]:
    # Parsing the whole file rather than the function's own source keeps line and column offsets
    # absolute, which the same-line tiebreak in find_function relies on.
    source_file = inspect.getsourcefile(fn)
    _, start_line = inspect.getsourcelines(fn)
    base_tree = get_tree_from_file(source_file)
    try:
        return source_file, find_function(base_tree, start_line, getattr(fn, "__code__", None))
    except FunctionNotFoundError:
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

    def _visit_scope(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef):
        node.declared_locals = set()
        outer_fn = self.current_fn
        self.current_fn = node
        self.generic_visit(node)
        self.current_fn = outer_fn

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self.results.append(node)
        self._visit_scope(node)

    def visit_Lambda(self, node: ast.Lambda):
        self.results.append(node)
        self._visit_scope(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._visit_scope(node)

    def visit_ClassDef(self, node: ast.ClassDef):
        self._visit_scope(node)

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


def _get_functions_by_line(tree: ast.Module) -> dict[int, list[ast.FunctionDef | ast.Lambda]]:
    index = getattr(tree, "functions_by_line", None)
    if index is None:
        index = {}
        for node in get_functions(tree):
            index.setdefault(node.lineno, []).append(node)
            if isinstance(node, ast.FunctionDef) and node.decorator_list:
                # inspect.getsourcelines / co_firstlineno returns exactly the first decorator's
                # start line for a decorated function, so index that line precisely rather than the
                # whole span up to the def line (which would also claim a lambda nested in a later
                # decorator).
                decorator_line = node.decorator_list[0].lineno
                if decorator_line != node.lineno:
                    index.setdefault(decorator_line, []).append(node)
        # On the tree rather than cached separately: the dev server drops the tree cache on
        # rebuild, and an index in its own cache would outlive that.
        tree.functions_by_line = index
    return index


def find_function(tree: ast.Module, line: int, code: CodeType | None = None):
    candidates = _get_functions_by_line(tree).get(line)
    if not candidates:
        raise FunctionNotFoundError("Function not found")
    if len(candidates) == 1:
        return candidates[0]
    return _disambiguate(candidates, line, code)


def _disambiguate(candidates: list[ast.FunctionDef | ast.Lambda], line: int, code: CodeType | None):
    """Pick the definition among several sharing a line that `code` was compiled from."""
    if code is not None:
        want_lambda = code.co_name == "<lambda>"
        matching = [
            node
            for node in candidates
            if isinstance(node, ast.Lambda) is want_lambda and (want_lambda or node.name == code.co_name)
        ]
        if not matching:
            raise FunctionNotFoundError("Function not found")
        if len(matching) == 1:
            return matching[0]
        # A code object reports the positions of the expressions in its own body, so a candidate qualifies
        # by its BODY containing every position: a lambda's positions are its body's columns and never the
        # `lambda` keyword's, and matching on the body is what separates a lambda from an enclosing one
        # whose body is nothing but this lambda. Columns are all None under -X no_debug_ranges, which
        # leaves nothing to disambiguate with.
        positions = [
            (position_line, col)
            for position_line, _, col, _ in code.co_positions()
            # Every code object opens with a RESUME at (co_firstlineno, 0), which lies outside its body.
            if position_line is not None and col is not None and (position_line, col) != (code.co_firstlineno, 0)
        ]
        if positions:
            contained = [node for node in matching if all(_body_contains(node, position) for position in positions)]
            if contained:
                # A nested definition's body sits inside its parent's, so the innermost match is the one compiled.
                return max(contained, key=lambda node: (node.lineno, node.col_offset))
    raise ValueError(f"Multiple functions defined on the same line are not supported (line {line})")


def _body_contains(node: ast.FunctionDef | ast.Lambda, position: tuple[int, int]) -> bool:
    body = node.body
    first, last = (body[0], body[-1]) if isinstance(body, list) else (body, body)
    return (first.lineno, first.col_offset) <= position <= (last.end_lineno, last.end_col_offset)


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
