"""Tests for the simulation context, which backs level memory with host storage outside of compilation."""

import builtins
import importlib
import sys
import textwrap

import pytest

from sonolus.script.array import Array
from sonolus.script.debug import simulation_context
from sonolus.script.globals import _GlobalPlaceholder, level_memory  # ruff: ignore[import-private-name]
from sonolus.script.internal.simulation_context import SimulationContext, sim_ctx
from tests.script.conftest import run_and_validate


@level_memory
class _Memory:
    counter: int
    values: Array[int, 3]


_module_memory = level_memory(Array[int, 4])

_this_module = sys.modules[__name__]


@pytest.fixture(autouse=True)
def _restore_process_state():
    # A simulation context mutates process-wide state, so a test that fails partway through would otherwise leave
    # the guard set or a module global substituted and take every later test in this worker down with it.
    original_import = builtins.__import__
    original_module_memory = _module_memory
    yield
    SimulationContext._active_context = None
    builtins.__import__ = original_import
    _this_module._module_memory = original_module_memory


def test_level_memory_class_fields_round_trip_inside_the_context():
    with simulation_context():
        assert _Memory.counter == 0
        assert list(_Memory.values) == [0, 0, 0]

        _Memory.counter = 41
        _Memory.values[1] = 7

        assert _Memory.counter == 41
        assert list(_Memory.values) == [0, 7, 0]


def test_level_memory_starts_zeroed_in_each_context():
    with simulation_context():
        _Memory.counter = 41

    with simulation_context():
        assert _Memory.counter == 0


def test_module_level_placeholder_is_substituted_and_restored():
    original = _module_memory
    assert isinstance(original, _GlobalPlaceholder)

    with simulation_context():
        assert _module_memory is not original
        assert list(_module_memory) == [0, 0, 0, 0]
        _module_memory[0] = 5
        assert list(_module_memory) == [5, 0, 0, 0]

    assert _module_memory is original


def test_module_globals_and_guard_are_restored_when_the_body_raises():
    original = _module_memory
    original_import = builtins.__import__

    with pytest.raises(ValueError, match="boom"), simulation_context():
        raise ValueError("boom")

    assert sim_ctx() is None
    assert _module_memory is original
    assert builtins.__import__ is original_import


def test_entering_a_second_context_is_rejected():
    with simulation_context():
        nested = simulation_context()
        with pytest.raises(RuntimeError, match="SimulationContext is already active"), nested:
            pass


def test_import_hook_substitutes_a_module_imported_inside_the_context(tmp_path):
    module_name = "_sonolus_simulation_context_probe"
    (tmp_path / f"{module_name}.py").write_text(
        textwrap.dedent(
            """
            from sonolus.script.array import Array
            from sonolus.script.globals import level_memory

            probe_memory = level_memory(Array[int, 2])
            """
        )
    )
    sys.path.insert(0, str(tmp_path))
    importlib.invalidate_caches()
    # The module must not be loaded before the context starts, or __enter__ would substitute it and the import
    # hook would never run.
    assert module_name not in sys.modules

    try:
        with simulation_context():
            module = __import__(module_name)
            module.probe_memory[0] = 3
            assert list(module.probe_memory) == [3, 0]

        assert isinstance(sys.modules[module_name].probe_memory, _GlobalPlaceholder)
    finally:
        sys.modules.pop(module_name, None)
        sys.path.remove(str(tmp_path))


def test_import_hook_substitutes_a_dotted_module_imported_inside_the_context(tmp_path):
    package_name = "_sonolus_simulation_context_package"
    submodule_name = f"{package_name}.probe"
    package = tmp_path / package_name
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "probe.py").write_text(
        textwrap.dedent(
            """
            from sonolus.script.array import Array
            from sonolus.script.globals import level_memory

            probe_memory = level_memory(Array[int, 2])
            """
        )
    )
    sys.path.insert(0, str(tmp_path))
    importlib.invalidate_caches()
    assert package_name not in sys.modules
    assert submodule_name not in sys.modules

    try:
        with simulation_context():
            package_module = __import__(submodule_name)
            package_module.probe.probe_memory[0] = 3
            assert list(package_module.probe.probe_memory) == [3, 0]

        assert isinstance(sys.modules[submodule_name].probe_memory, _GlobalPlaceholder)
    finally:
        sys.modules.pop(submodule_name, None)
        sys.modules.pop(package_name, None)
        sys.path.remove(str(tmp_path))


def test_abandoned_construction_does_not_block_a_later_context():
    # Regression: the guard used to be claimed in __init__, so a context that was built but never entered blocked
    # every later context in the process.
    simulation_context()

    with simulation_context() as context:
        assert sim_ctx() is context
        _Memory.counter = 41
        assert _Memory.counter == 41


def test_an_exited_context_can_be_entered_again():
    # Regression: __exit__ released a guard that __enter__ never claimed, so a second entry ran unprotected.
    context = simulation_context()

    with context:
        assert sim_ctx() is context
        _Memory.counter = 41

    assert sim_ctx() is None

    with context:
        assert sim_ctx() is context
        assert _Memory.counter == 41


def test_run_and_validate_agrees_with_the_simulation_context():
    # The plain-Python arm runs inside a simulation context and the compiled arm runs against real level memory,
    # so this pins the two against each other.
    def fn():
        _Memory.counter = 14
        _Memory.values[2] = 3
        return _Memory.counter + _Memory.values[2]

    assert run_and_validate(fn, use_simulation_context=True) == 17
