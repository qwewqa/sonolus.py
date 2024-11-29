import inspect
from typing import Annotated

_missing = object()


def get_field_specifiers(cls, *, skip: set[str] = frozenset(), globals=None, locals=None, eval_str=True):  # noqa: A002
    """Like inspect.get_annotations, but also turns class attributes into Annotated."""
    results = inspect.get_annotations(cls, globals=globals, locals=locals, eval_str=eval_str)
    for key, value in results.items():
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
        ):
            raise ValueError(f"Missing annotation for {cls.__name__}.{key}")
    return results
