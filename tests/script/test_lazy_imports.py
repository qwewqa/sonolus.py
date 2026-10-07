"""Compile before Python to exercise unresolved lazy imports on their first use."""

import builtins
import re
import sys
import textwrap
import types

import pytest

from sonolus.script.internal.context import RuntimeChecks
from sonolus.script.internal.error import CompilationError
from tests.script.conftest import run_compiled

pytestmark = pytest.mark.skipif(sys.version_info < (3, 15), reason="Lazy imports require Python 3.15")


@pytest.fixture
def lazy_modules(tmp_path, monkeypatch):
    package_path = tmp_path / "_lazy_import_fixture"
    package_path.mkdir()
    sources = {
        "__init__": "",
        "values": (
            "from math import floor as operation\n"
            "from sonolus.script.vec import Vec2 as Vector\n"
            "constant = 3\n"
            "def helper():\n"
            "    return operation(3.75)\n"
        ),
        "nested": (
            "lazy from .values import operation\n"
            "lazy import _sonolus_missing_lazy_import\n"
            "def helper():\n"
            "    return operation(3.75)\n"
        ),
    }
    for name, source in sources.items():
        (package_path / f"{name}.py").write_text(source, encoding="utf-8")
    monkeypatch.syspath_prepend(tmp_path)

    callback_index = 0

    def load(source, *, modules=None, builtins_namespace=None):
        for name, module_source in (modules or {}).items():
            (package_path / f"{name}.py").write_text(textwrap.dedent(module_source), encoding="utf-8")
        nonlocal callback_index
        callback_index += 1
        module_name = f"_lazy_import_fixture.callback_{callback_index}"
        source_path = package_path / f"callback_{callback_index}.py"
        source = textwrap.dedent(source)
        source_path.write_text(source, encoding="utf-8")
        namespace = {"__name__": module_name, "__package__": "_lazy_import_fixture", "__file__": str(source_path)}
        if builtins_namespace is not None:
            namespace["__builtins__"] = builtins_namespace
        exec(compile(source, str(source_path), "exec"), namespace)
        return namespace

    yield load

    for name in tuple(sys.modules):
        if name == "_lazy_import_fixture" or name.startswith("_lazy_import_fixture."):
            sys.modules.pop(name)


@pytest.mark.parametrize(
    ("import_statement", "expression", "binding"),
    [
        ("import _lazy_import_fixture.values as imported", "imported.operation(3.75)", "imported"),
        ("import _lazy_import_fixture.values", "_lazy_import_fixture.values.operation(3.75)", "_lazy_import_fixture"),
        ("from _lazy_import_fixture import values as imported", "imported.operation(3.75)", "imported"),
        ("from _lazy_import_fixture.values import operation as imported", "imported(3.75)", "imported"),
        ("from _lazy_import_fixture.values import helper as imported", "imported()", "imported"),
        ("from _lazy_import_fixture.values import constant as imported", "imported", "imported"),
        ("from _lazy_import_fixture.values import Vector as imported", "imported(3, 4).x", "imported"),
        ("from .values import operation as imported", "imported(3.75)", "imported"),
        ("from . import values as imported", "imported.operation(3.75)", "imported"),
    ],
)
def test_compiler_resolves_lazy_global_on_first_use(lazy_modules, import_statement, expression, binding):
    namespace = lazy_modules(
        f"lazy {import_statement}\nlazy import _sonolus_missing_lazy_import\ndef callback():\n    return {expression}\n"
    )
    callback = namespace["callback"]

    assert type(namespace[binding]) is types.LazyImportType
    assert "_lazy_import_fixture.values" not in sys.modules
    # The Python oracle would resolve the import before tracing, so compile first.
    compiled_result = run_compiled(callback)
    assert type(namespace[binding]) is not types.LazyImportType
    assert compiled_result == callback()
    assert type(namespace["_sonolus_missing_lazy_import"]) is types.LazyImportType


@pytest.mark.parametrize("expression", ["imported.operation(3.75)", "imported.helper()"])
def test_lazy_imports_inside_imported_module(lazy_modules, expression):
    namespace = lazy_modules(
        f"lazy import _lazy_import_fixture.nested as imported\ndef callback():\n    return {expression}\n"
    )
    callback = namespace["callback"]
    compiled_result = run_compiled(callback)
    assert compiled_result == callback()
    nested = sys.modules["_lazy_import_fixture.nested"]
    assert type(vars(nested)["_sonolus_missing_lazy_import"]) is types.LazyImportType


@pytest.mark.parametrize("nested", [False, True])
def test_lazy_global_used_by_nested_callback_with_closure(lazy_modules, nested):
    namespace = lazy_modules("""
        lazy from .values import operation
        def factory(offset):
            def callback():
                def inner():
                    return operation(3.75) + offset
                return inner()
            return callback
        def direct_factory(offset):
            def callback():
                return operation(3.75) + offset
            return callback
    """)
    callback = namespace["factory" if nested else "direct_factory"](4)
    assert type(namespace["operation"]) is types.LazyImportType
    compiled_result = run_compiled(callback)
    assert type(namespace["operation"]) is not types.LazyImportType
    assert compiled_result == callback()


@pytest.mark.parametrize("nested", [False, True])
def test_raw_lazy_import_in_closure_stays_unresolved(lazy_modules, nested):
    namespace = lazy_modules("""
        lazy from .values import constant
        def factory(value):
            def direct_callback():
                return value
            def nested_callback():
                def inner():
                    return value
                return inner()
            return direct_callback, nested_callback
    """)
    placeholder = namespace["constant"]
    callback = namespace["factory"](placeholder)[nested]
    # LOAD_DEREF returns a raw placeholder, which is not a supported compiled value.
    with pytest.raises(
        CompilationError, match=re.escape("Unsupported value: <lazy_import '_lazy_import_fixture.values.constant'>")
    ):
        run_compiled(callback)
    assert callback() is placeholder
    assert type(namespace["constant"]) is types.LazyImportType
    assert "_lazy_import_fixture.values" not in sys.modules


@pytest.mark.parametrize("builtins_change", ["unchanged", "replaced", "removed"])
def test_lazy_builtin_uses_original_builtins_and_function_globals(lazy_modules, builtins_change):
    hook_globals = []
    imported_module = types.ModuleType("_lazy_import_fixture.hooked")
    imported_module.constant = 17

    def hooked_import(*args):
        hook_globals.append(args[1])
        return imported_module

    builtin_namespace = dict(vars(builtins), __import__=hooked_import)
    namespace = lazy_modules(
        "lazy from _lazy_import_fixture.hooked import constant\ndef callback():\n    return constant\n",
        builtins_namespace=builtin_namespace,
    )
    placeholder = namespace.pop("constant")
    builtin_namespace["constant"] = placeholder
    if builtins_change == "replaced":
        namespace["__builtins__"] = {}
    elif builtins_change == "removed":
        del namespace["__builtins__"]
    original_keys = set(namespace)
    callback = namespace["callback"]
    compiled_result = run_compiled(callback)
    assert len(hook_globals) == 1
    assert hook_globals[0] is namespace
    assert set(namespace) == original_keys
    assert builtin_namespace["constant"] == imported_module.constant
    hook_globals.clear()
    builtin_namespace["constant"] = placeholder
    assert compiled_result == callback()
    assert len(hook_globals) == 1
    assert hook_globals[0] is namespace
    assert set(namespace) == original_keys


@pytest.mark.parametrize("runtime_checks", list(RuntimeChecks))
def test_deleted_lazy_global_falls_back_to_builtin(lazy_modules, monkeypatch, runtime_checks):
    state = types.ModuleType("_lazy_import_fixture.state")
    monkeypatch.setitem(sys.modules, state.__name__, state)
    source = """
        lazy from .initializing import constant as abs
        def callback():
            before = abs
            return before * 100 + abs(-9)
    """
    namespace = lazy_modules(
        source, modules={"initializing": "from . import state\nconstant = 3\ndel state.namespace['abs']\n"}
    )
    state.namespace = namespace
    compiled_result = run_compiled(namespace["callback"], runtime_checks=runtime_checks)
    del sys.modules["_lazy_import_fixture.initializing"]
    namespace = lazy_modules(source)
    state.namespace = namespace
    assert compiled_result == namespace["callback"]()


def test_lazy_import_in_effectful_builtin_mapping_is_rejected(lazy_modules):
    class MutatingBuiltins(dict):  # noqa: FURB189
        def __getitem__(self, key):
            value = super().__getitem__(key)
            if key == "constant":
                self[key] = 9
            return value

    builtin_namespace = MutatingBuiltins(vars(builtins))
    namespace = lazy_modules(
        "lazy from .values import constant\ndef callback():\n    return constant\n",
        builtins_namespace=builtin_namespace,
    )
    builtin_namespace["constant"] = namespace.pop("constant")
    with pytest.raises(CompilationError, match="Custom global or builtin namespace mappings are not supported"):
        run_compiled(namespace["callback"])
    assert "_lazy_import_fixture.values" not in sys.modules


@pytest.mark.parametrize("use_declaration", [False, True])
def test_lazy_from_import_resolves_only_used_member(lazy_modules, use_declaration):
    declaration = "__lazy_modules__ = {'_lazy_import_fixture.values'}\n" if use_declaration else ""
    prefix = "" if use_declaration else "lazy "
    namespace = lazy_modules(
        declaration
        + f"{prefix}from _lazy_import_fixture.values import constant, missing\n"
        + "def callback():\n    return constant + constant\n"
    )
    assert type(namespace["constant"]) is types.LazyImportType
    assert type(namespace["missing"]) is types.LazyImportType
    callback = namespace["callback"]
    compiled_result = run_compiled(callback)
    assert type(namespace["missing"]) is types.LazyImportType
    assert compiled_result == callback()


@pytest.mark.parametrize(
    ("statement", "exception_type", "message"),
    [
        (
            "lazy import _sonolus_missing_lazy_import as imported",
            ModuleNotFoundError,
            "No module named '_sonolus_missing_lazy_import'",
        ),
        ("lazy from .values import missing as imported", ImportError, "cannot import name 'missing'"),
    ],
)
def test_used_missing_lazy_import_reports_original_error(lazy_modules, statement, exception_type, message):
    namespace = lazy_modules(f"{statement}\ndef callback():\n    return imported\n")
    callback = namespace["callback"]
    with pytest.raises(CompilationError, match=re.escape(message)) as compiled_error:
        run_compiled(callback)
    cause = compiled_error.value
    while isinstance(cause, CompilationError):
        cause = cause.__cause__
    assert type(cause) is exception_type
    assert type(namespace["imported"]) is types.LazyImportType
    with pytest.raises(exception_type, match=re.escape(message)):
        callback()


def test_lazy_import_resolves_again_after_missing_module_becomes_available(lazy_modules, monkeypatch):
    namespace = lazy_modules("""
        lazy import _sonolus_missing_lazy_import as imported
        def callback():
            return imported.constant
    """)
    callback = namespace["callback"]
    with pytest.raises(CompilationError, match="No module named '_sonolus_missing_lazy_import'"):
        run_compiled(callback)
    module = types.ModuleType("_sonolus_missing_lazy_import")
    module.constant = 17
    monkeypatch.setitem(sys.modules, module.__name__, module)
    compiled_result = run_compiled(callback)
    assert compiled_result == callback()


def test_rebinding_lazy_global_before_compilation_keeps_import_deferred(lazy_modules):
    namespace = lazy_modules("""
        lazy from .values import constant
        def callback():
            return constant
    """)
    namespace["constant"] = 19
    callback = namespace["callback"]
    compiled_result = run_compiled(callback)
    assert compiled_result == callback()
    assert "_lazy_import_fixture.values" not in sys.modules


def test_lazy_from_import_keeps_first_resolved_value(lazy_modules):
    namespace = lazy_modules("""
        lazy from .values import constant
        from sonolus.script.internal.meta_fn import meta_fn
        @meta_fn
        def mutate():
            from _lazy_import_fixture import values
            values.constant += 10
        def callback():
            before = constant
            mutate()
            return before * 100 + constant
    """)
    callback = namespace["callback"]
    compiled_result = run_compiled(callback)
    sys.modules["_lazy_import_fixture.values"].constant = 3
    assert compiled_result == callback()


def test_lazy_import_replaces_global_binding_on_first_use(lazy_modules):
    namespace = lazy_modules("""
        lazy from .values import constant
        from sonolus.script.internal.meta_fn import meta_fn
        import types
        @meta_fn
        def resolved():
            return type(globals()['constant']) is not types.LazyImportType
        def callback():
            return constant * 10 + resolved()
    """)
    callback = namespace["callback"]
    compiled_result = run_compiled(callback)
    assert compiled_result == callback()


@pytest.mark.parametrize("mutation", ["state.namespace['constant'] = 9", "del state.namespace['constant']"])
@pytest.mark.parametrize("runtime_checks", list(RuntimeChecks))
def test_lazy_import_matches_binding_changed_by_module_initialization(
    lazy_modules, monkeypatch, mutation, runtime_checks
):
    state = types.ModuleType("_lazy_import_fixture.state")
    monkeypatch.setitem(sys.modules, state.__name__, state)
    source = """
        lazy from .initializing import constant
        def callback():
            before = constant
            return before * 100 + constant
    """
    initializer = f"from . import state\nconstant = 3\n{mutation}\n"
    namespace = lazy_modules(source, modules={"initializing": initializer})
    state.namespace = namespace
    compiled_error = None
    try:
        # Each trace needs fresh globals because the initializer changes them on first use.
        compiled_result = run_compiled(namespace["callback"], runtime_checks=runtime_checks)
    except CompilationError as exc:
        compiled_error = exc
    del sys.modules["_lazy_import_fixture.initializing"]
    namespace = lazy_modules(source)
    state.namespace = namespace
    if compiled_error is not None:
        cause = compiled_error
        while isinstance(cause, CompilationError):
            cause = cause.__cause__
        assert type(cause) is NameError
        assert "constant" in str(cause)
        with pytest.raises(NameError, match="name 'constant' is not defined"):
            namespace["callback"]()
    else:
        assert compiled_result == namespace["callback"]()


def test_lazy_module_initialization_follows_first_use_order(lazy_modules, monkeypatch):
    state = types.ModuleType("_lazy_import_fixture.state")
    state.events = []
    monkeypatch.setitem(sys.modules, state.__name__, state)
    source = """
        lazy from . import first, second
        def callback():
            return second.constant * 100 + first.constant + second.constant
    """
    namespace = lazy_modules(
        source,
        modules={
            name: f"from . import state\nstate.events.append('{name}')\nconstant = {index}\n"
            for index, name in enumerate(("first", "second"), start=1)
        },
    )
    compiled_result = run_compiled(namespace["callback"])
    compiled_events = state.events.copy()
    state.events.clear()
    package = sys.modules["_lazy_import_fixture"]
    for name in ("first", "second"):
        del sys.modules[f"_lazy_import_fixture.{name}"]
        delattr(package, name)
    namespace = lazy_modules(source)
    assert compiled_result == namespace["callback"]()
    assert compiled_events == state.events == ["second", "first"]


@pytest.mark.parametrize("closure", [False, True])
def test_local_or_closure_binding_shadows_unused_lazy_global(lazy_modules, closure):
    namespace = lazy_modules("""
        lazy from .values import constant
        def callback():
            constant = 19
            return constant
        def factory(constant):
            def inner():
                return constant
            return inner
    """)
    callback = namespace["factory"](23) if closure else namespace["callback"]
    compiled_result = run_compiled(callback)
    assert compiled_result == callback()
    assert type(namespace["constant"]) is types.LazyImportType
    assert "_lazy_import_fixture.values" not in sys.modules


def test_rebinding_lazy_global_between_compilations(lazy_modules):
    namespace = lazy_modules("""
        lazy from .values import constant
        def callback():
            return constant
    """)
    callback = namespace["callback"]
    compiled_result = run_compiled(callback)
    assert compiled_result == callback()
    namespace["constant"] = 29
    compiled_result = run_compiled(callback)
    assert compiled_result == callback()
