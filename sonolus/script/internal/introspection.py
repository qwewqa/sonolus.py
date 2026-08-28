import inspect
from abc import ABC
from collections.abc import Sequence
from typing import Annotated, get_origin

_missing = object()


def describe_value(value) -> str:
    """Return a readable description of a value for an error message, with no heap address in it."""
    if get_origin(value) is Annotated:
        parts = ", ".join(describe_value(part) for part in (value.__args__[0], *value.__metadata__))
        return f"Annotated[{parts}]"
    if isinstance(value, type):
        return value.__name__
    if type(value).__repr__ is object.__repr__:
        return f"{type(value).__name__} object"
    return repr(value)


def get_field_specifiers(
    cls,
    *,
    skip: frozenset[str] | set[str] = frozenset(),
    globals=None,  # ruff: ignore[builtin-argument-shadowing]
    locals=None,  # ruff: ignore[builtin-argument-shadowing]
    eval_str=True,
    included_classes: Sequence[type] | None = None,
    evaluated_annotations: dict | None = None,
):
    """Like inspect.get_annotations, but also turns class attributes into Annotated."""
    if evaluated_annotations is not None:
        results = dict(evaluated_annotations)
    elif included_classes is not None:
        results = {}
        for entry in reversed(included_classes):
            results.update(inspect.get_annotations(entry, eval_str=eval_str))
    else:
        results = inspect.get_annotations(cls, globals=globals, locals=locals, eval_str=eval_str)
    for key, value in results.items():
        if key in skip:
            continue
        class_value = getattr(cls, key, _missing)
        if class_value is not _missing and key not in skip:
            results[key] = Annotated[value, class_value]
    for key, value in cls.__dict__.items():
        if (
            key not in results
            and key not in skip
            and not key.startswith("__")
            and not callable(value)
            and not hasattr(value, "__func__")
            and not isinstance(value, property)
            and not (issubclass(cls, ABC) and (hasattr(ABC, key)))
        ):
            raise ValueError(f"Missing annotation for {cls.__name__}.{key}")
    for skipped_key in skip:
        if skipped_key in results:
            del results[skipped_key]
    return results
