import ast
import inspect
from collections.abc import Callable
from functools import cache
from pathlib import Path
from types import CodeType, FunctionType, MethodType


class FunctionNotFoundError(ValueError):
    """No definition in the tree claims the requested line."""


@cache
def get_function(fn: Callable) -> tuple[str, ast.FunctionDef]:
    # Parsing the whole file rather than the function's own source keeps line and column offsets
    # absolute, which the same-line tiebreak in find_function relies on.
    code = fn.__func__.__code__ if inspect.ismethod(fn) else fn.__code__
    source_file = inspect.getsourcefile(code)
    start_line = code.co_firstlineno
    base_tree = get_tree_from_file(source_file)
    try:
        return source_file, find_function(base_tree, start_line, code)
    except FunctionNotFoundError:
        raise ValueError(f"Function {fn} not found in source file {source_file}") from None


@cache
def get_signature(fn: Callable) -> inspect.Signature:
    function = fn.__func__ if inspect.ismethod(fn) else fn
    if not isinstance(function, FunctionType):
        return inspect.signature(fn, follow_wrapped=False)
    physical_function = FunctionType(
        function.__code__, function.__globals__, function.__name__, function.__defaults__, function.__closure__
    )
    physical_function.__kwdefaults__ = function.__kwdefaults__
    target = MethodType(physical_function, fn.__self__) if inspect.ismethod(fn) else physical_function
    return inspect.signature(target, follow_wrapped=False)


@cache
def get_tree_from_file(file: str | Path) -> ast.Module:
    return ast.parse(Path(file).read_text(encoding="utf-8"))


def is_async_generator_expression(node: ast.GeneratorExp) -> bool:
    result = getattr(node, "is_async_generator", None)
    if result is None:
        result = _has_async_expression(node.elt) or any(
            generator.is_async
            or _has_async_expression(generator.target)
            or any(_has_async_expression(condition) for condition in generator.ifs)
            or (index > 0 and _has_async_expression(generator.iter))
            for index, generator in enumerate(node.generators)
        )
        node.is_async_generator = result
    return result


def _has_async_expression(node: ast.AST) -> bool:
    if isinstance(node, ast.Await):
        return True
    if isinstance(node, ast.GeneratorExp):
        # A nested generator evaluates only its outermost iterable in this scope.
        return _has_async_expression(node.generators[0].iter)
    if isinstance(node, ast.Lambda):
        return any(
            _has_async_expression(default)
            for default in [*node.args.defaults, *node.args.kw_defaults]
            if default is not None
        )
    if isinstance(node, ast.comprehension) and node.is_async:
        return True
    return any(_has_async_expression(child) for child in ast.iter_child_nodes(node))


class FindFunction(ast.NodeVisitor):
    def __init__(self, line):
        self.line = line
        self.results: list[ast.FunctionDef | ast.Lambda] = []
        self.current_fn = None

    def _visit_scope(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef, body):
        node.declared_locals = set()
        outer_fn = self.current_fn
        self.current_fn = node
        if not isinstance(node, ast.ClassDef):
            for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
                node.declared_locals.add(arg.arg)
            if node.args.vararg is not None:
                node.declared_locals.add(node.args.vararg.arg)
            if node.args.kwarg is not None:
                node.declared_locals.add(node.args.kwarg.arg)
        for entry in body:
            self.visit(entry)
        self.current_fn = outer_fn

    def _visit_arguments(self, arguments: ast.arguments):
        for arg in [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]:
            if arg.annotation is not None:
                self.visit(arg.annotation)
        if arguments.vararg is not None and arguments.vararg.annotation is not None:
            self.visit(arguments.vararg.annotation)
        if arguments.kwarg is not None and arguments.kwarg.annotation is not None:
            self.visit(arguments.kwarg.annotation)
        for default in [*arguments.defaults, *arguments.kw_defaults]:
            if default is not None:
                self.visit(default)

    def _visit_function_expressions(self, node: ast.FunctionDef | ast.AsyncFunctionDef):
        for decorator in node.decorator_list:
            self.visit(decorator)
        self._visit_arguments(node.args)
        if node.returns is not None:
            self.visit(node.returns)
        for type_param in getattr(node, "type_params", ()):
            self.visit(type_param)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        if self.current_fn is not None:
            self.current_fn.declared_locals.add(node.name)
        self.results.append(node)
        self._visit_function_expressions(node)
        self._visit_scope(node, node.body)

    def visit_Lambda(self, node: ast.Lambda):
        self.results.append(node)
        self._visit_arguments(node.args)
        self._visit_scope(node, (node.body,))

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        if self.current_fn is not None:
            self.current_fn.declared_locals.add(node.name)
        self._visit_function_expressions(node)
        self._visit_scope(node, node.body)

    def visit_ClassDef(self, node: ast.ClassDef):
        if self.current_fn is not None:
            self.current_fn.declared_locals.add(node.name)
        for expression in [*node.decorator_list, *node.bases]:
            self.visit(expression)
        for keyword in node.keywords:
            self.visit(keyword.value)
        for type_param in getattr(node, "type_params", ()):
            self.visit(type_param)
        self._visit_scope(node, node.body)

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
        if node.value is None:
            if node.simple and self.current_fn is not None:
                self.current_fn.declared_locals.add(node.target.id)
            elif isinstance(node.target, ast.Attribute):
                self.visit(node.target.value)
            elif isinstance(node.target, ast.Subscript):
                self.visit(node.target.value)
                self.visit(node.target.slice)
            return
        self.visit(node.target)
        self.visit(node.value)

    def visit_Name(self, node: ast.Name):
        if isinstance(node.ctx, ast.Store | ast.Del) and self.current_fn is not None:
            self.current_fn.declared_locals.add(node.id)

    def visit_Import(self, node: ast.Import):
        if self.current_fn is not None:
            for alias in node.names:
                self.current_fn.declared_locals.add(alias.asname or alias.name.partition(".")[0])

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if self.current_fn is not None:
            for alias in node.names:
                if alias.name != "*":
                    self.current_fn.declared_locals.add(alias.asname or alias.name)

    def visit_ExceptHandler(self, node: ast.ExceptHandler):
        if node.name is not None and self.current_fn is not None:
            self.current_fn.declared_locals.add(node.name)
        self.generic_visit(node)

    def visit_MatchAs(self, node: ast.MatchAs):
        if node.name is not None and self.current_fn is not None:
            self.current_fn.declared_locals.add(node.name)
        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar):
        if node.name is not None and self.current_fn is not None:
            self.current_fn.declared_locals.add(node.name)

    def visit_MatchMapping(self, node: ast.MatchMapping):
        if node.rest is not None and self.current_fn is not None:
            self.current_fn.declared_locals.add(node.rest)
        self.generic_visit(node)

    def _visit_comprehension(self, node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp):
        for generator in node.generators:
            self.visit(generator.iter)
            for condition in generator.ifs:
                self.visit(condition)
        if isinstance(node, ast.DictComp):
            self.visit(node.key)
            if node.value is not None:
                self.visit(node.value)
        else:
            self.visit(node.elt)

    def visit_ListComp(self, node: ast.ListComp):
        self._visit_comprehension(node)

    def visit_SetComp(self, node: ast.SetComp):
        self._visit_comprehension(node)

    def visit_DictComp(self, node: ast.DictComp):
        self._visit_comprehension(node)

    def visit_GeneratorExp(self, node: ast.GeneratorExp):
        self._visit_comprehension(node)


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
        self.writes.append(node.name)
        for decorator in node.decorator_list:
            self.visit(decorator)
        self.visit(node.args)
        if node.returns is not None:
            self.visit(node.returns)
        for type_param in getattr(node, "type_params", ()):
            self.visit(type_param)

    def visit_AsyncFunctionDef(self, node):
        self.visit_FunctionDef(node)

    def visit_Lambda(self, node):
        self.visit(node.args)

    def visit_ClassDef(self, node):
        self.writes.append(node.name)
        for expression in [*node.decorator_list, *node.bases]:
            self.visit(expression)
        for keyword in node.keywords:
            self.visit(keyword.value)
        for type_param in getattr(node, "type_params", ()):
            self.visit(type_param)

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

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            self.writes.append(alias.asname or alias.name.partition(".")[0])

    def visit_ImportFrom(self, node: ast.ImportFrom):
        for alias in node.names:
            if alias.name != "*":
                self.writes.append(alias.asname or alias.name)

    def visit_ExceptHandler(self, node: ast.ExceptHandler):
        if node.name is not None:
            self.writes.append(node.name)
        self.generic_visit(node)

    def _visit_comprehension(self, node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp):
        for generator in node.generators:
            self.visit(generator.iter)
            for condition in generator.ifs:
                self.visit(condition)
        if isinstance(node, ast.DictComp):
            self.visit(node.key)
            if node.value is not None:
                self.visit(node.value)
        else:
            self.visit(node.elt)

    def visit_ListComp(self, node: ast.ListComp):
        self._visit_comprehension(node)

    def visit_SetComp(self, node: ast.SetComp):
        self._visit_comprehension(node)

    def visit_DictComp(self, node: ast.DictComp):
        self._visit_comprehension(node)

    def visit_GeneratorExp(self, node: ast.GeneratorExp):
        self._visit_comprehension(node)


def scan_writes(*nodes: ast.AST) -> set[str]:
    visitor = ScanWrites()
    for node in nodes:
        visitor.visit(node)
    return set(visitor.writes)
