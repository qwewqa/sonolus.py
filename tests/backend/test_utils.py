import ast

from sonolus.backend.utils import find_function, get_function


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
