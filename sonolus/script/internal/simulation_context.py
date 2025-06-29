import builtins
import sys
from collections.abc import Callable
from types import MethodType, ModuleType
from typing import Any, ClassVar, Self

from sonolus.script.internal.impl import validate_value

_MISSING = object()


class SimulationContext:
    """Context manager to simulate additional Sonolus runtime features like level memory."""

    _active_context: ClassVar[Self | None] = None
    additional_replacements: dict[Any, Any]
    values: dict[Any, Any]
    _original_values: dict[str, dict[str, Any]]  # module_name: {var_name: original_value}
    _original_import: Callable[[str, Any, Any, Any, Any], Any] | None

    def __init__(self, *, additional_replacements: dict[Any, Any] | None = None):
        if SimulationContext._active_context is not None:
            raise RuntimeError("SimulationContext is already active")
        SimulationContext._active_context = self
        self.additional_replacements = additional_replacements or {}
        self.values = {}
        self._original_values = {}
        self._original_import = None

    def get_or_put_value(self, key: Any, factory: Callable[[], Any]) -> Any:
        if key not in self.values:
            self.values[key] = validate_value(factory())
        return self.values[key]._get_()._as_py_()

    def set_or_put_value(self, key: Any, factory: Callable[[], Any], value: Any):
        if key not in self.values:
            self.values[key] = validate_value(factory())
        existing_value = self.values[key]
        existing_type = type(existing_value)
        value = existing_type._accept_(validate_value(value))
        if existing_type._is_value_type_():
            existing_value._set_(value)
        else:
            existing_value._copy_from_(value)

    def _get_replacement(self, value: Any) -> Any:
        try:
            if value in self.additional_replacements:
                return self.additional_replacements[value]
        except TypeError:
            pass
        if hasattr(value, "_get_sim_replacement_") and isinstance(value._get_sim_replacement_, MethodType):
            return value._get_sim_replacement_()
        return _MISSING

    def _substitute_module_variables(self, module) -> None:
        if not isinstance(module, ModuleType):
            return

        module_name = module.__name__

        if module_name in self._original_values:
            return

        original_values = {}

        for var_name, var_value in list(module.__dict__.items()):
            replacement = self._get_replacement(var_value)
            if replacement is not _MISSING:
                original_values[var_name] = var_value
                setattr(module, var_name, replacement)

        if original_values:
            self._original_values[module_name] = original_values

    def _update_loaded_modules(self) -> None:
        loaded_modules = list(sys.modules.values())

        for module in loaded_modules:
            if module is not None:
                self._substitute_module_variables(module)

    def _create_import_hook(self):
        original_import = builtins.__import__

        def hooked_import(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: A002
            module = original_import(name, globals, locals, fromlist, level)
            if isinstance(module, ModuleType):
                self._substitute_module_variables(module)

            if fromlist:
                for item in fromlist:
                    if hasattr(module, item):
                        attr = getattr(module, item)
                        if isinstance(attr, ModuleType):
                            self._substitute_module_variables(attr)

            return module

        return hooked_import

    def __enter__(self) -> Self:
        self._update_loaded_modules()

        self._original_import = builtins.__import__
        builtins.__import__ = self._create_import_hook()

        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any):
        try:
            if self._original_import is not None:
                builtins.__import__ = self._original_import
                self._original_import = None

            for module_name, original_values in self._original_values.items():
                if module_name in sys.modules:
                    module = sys.modules[module_name]
                    if isinstance(module, ModuleType):
                        for var_name, original_value in original_values.items():
                            setattr(module, var_name, original_value)

            self._original_values.clear()

        finally:
            SimulationContext._active_context = None


def sim_ctx() -> SimulationContext | None:
    """Get the current simulation context, or None if not active."""
    return SimulationContext._active_context
