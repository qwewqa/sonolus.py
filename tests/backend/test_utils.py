import ast

import pytest

from sonolus.backend.utils import find_function, get_function, get_functions, scan_writes


def _identity_deco(*args, **kwargs):
    def wrap(f):
        # Return the original function unchanged so getsourcelines resolves to it.
        return f

    return wrap


@_identity_deco(
    "a",
    "b",
)
def _fn_multiline_first_decorator():
    return 42


@_identity_deco("x")
def _fn_singleline_first_decorator():
    return 7


def test_get_function_multiline_first_decorator():
    # Regression: a function whose FIRST decorator spans multiple source lines must still
    # be locatable. get_function uses inspect.getsourcelines (== the decorator's START
    # line); find_function previously compared against decorator_list[0].end_lineno, so a
    # multi-line first decorator failed to match and raised ValueError, aborting compilation.
    _source_file, node = get_function(_fn_multiline_first_decorator)
    assert node.name == "_fn_multiline_first_decorator"


def test_get_function_singleline_first_decorator_still_ok():
    _source_file, node = get_function(_fn_singleline_first_decorator)
    assert node.name == "_fn_singleline_first_decorator"


def test_find_function_lambda_inside_multiline_decorator_resolves_to_lambda():
    # A lambda on an interior line of an enclosing function's multi-line first decorator must
    # resolve to the lambda, not the enclosing function. (Matching the first decorator's exact
    # start line rather than the whole span avoids this ambiguity.)
    src = "@deco(\n    lambda q: q + 1,\n    'b',\n)\ndef outer():\n    return 1\n"
    tree = ast.parse(src)
    node = find_function(tree, 2)  # the lambda's line
    assert isinstance(node, ast.Lambda)


_first, _second = lambda: 111.0, lambda: 222.0


def test_two_lambdas_on_one_line_resolve_separately():
    # Two lambdas starting on the same source line must each resolve to their own node. Matching on
    # lineno alone returns the first one visited for both, so the compiler traces the wrong body, and
    # when every free name in that body still resolves the build silently ships it.
    _file, node_first = get_function(_first)
    _file, node_second = get_function(_second)
    assert ast.unparse(node_first.body) == "111.0"
    assert ast.unparse(node_second.body) == "222.0"


def _defaults_holder(g=lambda: 555.0):
    return g


_lambda_default = _defaults_holder()


def test_lambda_default_does_not_resolve_to_its_enclosing_def():
    # A lambda in a def's default argument shares the def's line. co_name distinguishes them: a
    # '<lambda>' lookup must never return an ast.FunctionDef.
    _file, node = get_function(_lambda_default)
    assert isinstance(node, ast.Lambda), f"got {type(node).__name__}"
    assert ast.unparse(node.body) == "555.0"


def _gen_defaults_holder(h=lambda: 777.0):
    yield h


_lambda_in_gen = next(_gen_defaults_holder())


def test_lambda_default_does_not_inherit_has_yield():
    # FindFunction stamps has_yield onto the node it returns and the visitor reads it back to decide
    # generator-ness, so a wrong resolution also compiles a plain lambda down the generator path.
    _file, node = get_function(_lambda_in_gen)
    assert ast.unparse(node.body) == "777.0"
    assert not getattr(node, "has_yield", False)


_decorator_lambdas = []


def _capturing_deco(fn):
    _decorator_lambdas.append(fn)
    return lambda f: f


@_capturing_deco(lambda q: q + 1)
def _fn_lambda_on_decorator_line():
    return 3


def test_lambda_on_a_single_line_decorator_resolves_separately():
    # A decorated def is looked up by its first decorator's line, which a lambda written inside that
    # decorator shares.
    _file, def_node = get_function(_fn_lambda_on_decorator_line)
    _file, lambda_node = get_function(_decorator_lambdas[0])
    assert def_node.name == "_fn_lambda_on_decorator_line"
    assert ast.unparse(lambda_node.body) == "q + 1"


_nesting_lambda = lambda: lambda: 111.0  # ruff: ignore[lambda-assignment]
_enclosing_lambda = lambda: sorted([2.0, 1.0], key=lambda v: -v)  # ruff: ignore[lambda-assignment]


def test_lambda_whose_body_is_another_lambda_resolves_separately():
    # The outer lambda's only reported position is where the inner one starts, which falls inside the
    # inner's own span.
    _file, outer_node = get_function(_nesting_lambda)
    _file, inner_node = get_function(_nesting_lambda())
    assert ast.unparse(outer_node.body) == "lambda: 111.0"
    assert ast.unparse(inner_node.body) == "111.0"


_closure_lambda = lambda v: lambda: v  # ruff: ignore[lambda-assignment]


def test_lambda_holding_a_closure_cell_resolves_separately():
    # Setting up the cell for v is reported at column 0, outside either lambda's body.
    _file, outer_node = get_function(_closure_lambda)
    _file, inner_node = get_function(_closure_lambda(1.0))
    assert ast.unparse(outer_node.body) == "lambda: v"
    assert ast.unparse(inner_node.body) == "v"


def test_lambda_enclosing_a_lambda_mid_body_resolves_to_itself():
    # The enclosing lambda is the only candidate its positions fit in, so nothing may narrow it away.
    _file, node = get_function(_enclosing_lambda)
    assert ast.unparse(node.body) == "sorted([2.0, 1.0], key=lambda v: -v)"


class _MethodHolder:
    def method(self):
        return 3.0


def test_method_of_a_class_is_still_found():
    # A class body is a scope, but FindFunction must still descend into it: every archetype callback is a
    # method, so a scope push that skipped the body would leave the whole build unable to locate them.
    _file, node = get_function(_MethodHolder.method)
    assert node.name == "method"


def _outer_with_nested_class():
    if False:

        class _Nested:
            _annotated: int

    return 1


def _outer_with_nested_async_generator():
    if False:

        async def _agen():  # ruff: ignore[unused-async]
            yield 1

    return 1


def test_nested_class_annotation_is_not_a_local_of_the_enclosing_function():
    # A bare annotation in a class body declares a class attribute, not a local of the function the class is
    # written in, and the visitor reads declared_locals back to decide that a name resolves outward.
    _file, node = get_function(_outer_with_nested_class)
    assert "_annotated" not in node.declared_locals


def test_nested_async_generator_does_not_make_the_enclosing_function_a_generator():
    # has_yield decides which path the visitor compiles the function down, so a yield belonging to a nested
    # async def must not reach the function containing it.
    _file, node = get_function(_outer_with_nested_async_generator)
    assert not getattr(node, "has_yield", False)


class _NoPositionCode:
    """A code object whose columns are all None, as under -X no_debug_ranges."""

    co_name = "<lambda>"
    co_firstlineno = 1

    def co_positions(self):
        return [(1, 1, None, None), (1, 1, None, None)]


def test_ambiguous_line_without_position_info_raises():
    # Without columns there is nothing left to disambiguate two lambdas with, and a build that stops
    # beats one that ships another callable's body.
    tree = ast.parse("first, second = lambda: 111.0, lambda: 222.0\n")
    with pytest.raises(ValueError, match="Multiple functions defined on the same line"):
        find_function(tree, 1, _NoPositionCode())
    with pytest.raises(ValueError, match="Multiple functions defined on the same line"):
        find_function(tree, 1)


def test_yields_in_definition_expressions_belong_to_enclosing_scope():
    tree = ast.parse(
        """
def outer():
    def by_default(value=(yield 1)):
        return value
    @(yield 2)
    def by_decorator():
        return 0
    def by_annotation() -> (yield 3):
        return 0
    class ByBase((yield 4)):
        pass
"""
    )
    outer, by_default, by_decorator, by_annotation = get_functions(tree)

    assert outer.has_yield
    assert not getattr(by_default, "has_yield", False)
    assert not getattr(by_decorator, "has_yield", False)
    assert not getattr(by_annotation, "has_yield", False)


def test_all_lexical_local_bindings_are_predeclared():
    tree = ast.parse(
        """
def outer():
    assigned = 1
    for loop_target in ():
        pass
    with manager as with_target:
        pass
    try:
        pass
    except Exception as exception_target:
        pass
    import package as imported
    from package import member
    def nested():
        nested_only = 1
    class Nested:
        class_only = 1
    [comprehension_target for comprehension_target in ()]
"""
    )
    outer = get_functions(tree)[0]

    assert outer.declared_locals == {
        "assigned",
        "loop_target",
        "with_target",
        "exception_target",
        "imported",
        "member",
        "nested",
        "Nested",
    }


def test_loop_write_scan_excludes_else_and_nested_scope_bodies():
    loop = ast.parse(
        """
while condition:
    body_value = 1
    def nested():
        nested_value = 2
    class Nested:
        class_value = 3
else:
    else_value = 4
"""
    ).body[0]

    assert scan_writes(loop.test, *loop.body) == {"body_value", "nested", "Nested"}


def test_loop_write_scan_includes_nested_definition_expressions():
    loop = ast.parse(
        """
while condition:
    @(decorator_value := identity)
    def nested(default=(default_value := 1)):
        nested_value = 2
"""
    ).body[0]

    assert scan_writes(loop.test, *loop.body) == {"nested", "decorator_value", "default_value"}


def test_for_loop_write_scan_excludes_iterable_expression():
    loop = ast.parse(
        """
for target in (iter_value := values):
    body_value = target
else:
    else_value = 1
"""
    ).body[0]

    assert scan_writes(loop.target, *loop.body) == {"target", "body_value"}
