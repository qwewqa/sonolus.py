import inspect
from typing import Annotated

_missing = object()


def get_field_specifiers(cls, *, globals=None, locals=None, eval_str=True):  # noqa: A002
    """Like inspect.get_annotations, but also turns class attributes into Annotated."""
    results = inspect.get_annotations(cls, globals=globals, locals=locals, eval_str=eval_str)
    for key, value in results.items():
        class_value = getattr(cls, key, _missing)
        if class_value is not _missing:
            results[key] = Annotated[value, class_value]
    return results
