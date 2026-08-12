import inspect
from abc import ABC
from collections.abc import Sequence
from typing import Annotated, get_origin

_missing = object()


def describe_value(value) -> str:
    """Return a readable description of a value for an error message, with no heap address in it.

    An object inheriting `object.__repr__` prints as `<Cls object at 0xADDRESS>`, which puts an address no reader
    can act on into user-facing text, so its type name stands in for it. An `Annotated` is rebuilt part by part
    because typing's own repr embeds the repr of each metadata value, which would put the address back. A class
    is named rather than shown as `<class '...'>`, so the description reads the way the annotation was written.
    Anything else keeps its repr: it says more than a name would.
    """
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
    globals=None,  # noqa: A002
    locals=None,  # noqa: A002
    eval_str=True,
    included_classes: Sequence[type] | None = None,
):
    """Like inspect.get_annotations, but also turns class attributes into Annotated."""
    if included_classes is not None:
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
