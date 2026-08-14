import ast
from pathlib import Path

DOC_STUBS = Path(__file__).parents[2] / "doc_stubs"


def get_functions(stub: str, name: str) -> list[ast.FunctionDef]:
    tree = ast.parse((DOC_STUBS / stub).read_text())
    return [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name]


def positional_args(fn: ast.FunctionDef) -> list[ast.arg]:
    return [*fn.args.posonlyargs, *fn.args.args]


def test_truth_testing_stubs_accept_objects():
    all_fn, any_fn = (get_functions("builtins.pyi", name)[0] for name in ("all", "any"))
    filter_fn = get_functions("builtins.pyi", "filter")[0]

    assert ast.unparse(positional_args(all_fn)[0].annotation) == "Iterable[builtins.object]"
    assert ast.unparse(positional_args(any_fn)[0].annotation) == "Iterable[builtins.object]"
    assert "Callable[[T], builtins.object]" in ast.unparse(positional_args(filter_fn)[0].annotation)


def test_zip_stub_preserves_heterogeneous_element_types():
    zip_fns = get_functions("builtins.pyi", "zip")
    return_types = {ast.unparse(fn.returns) for fn in zip_fns}

    assert "Iterator[tuple[T1, T2]]" in return_types
    assert "Iterator[tuple[T1, T2, T3, T4, T5]]" in return_types


def test_uniform_stub_documents_unordered_endpoints():
    uniform_fn = get_functions("random.pyi", "uniform")[0]

    assert "between a and b, inclusive" in ast.get_docstring(uniform_fn)
