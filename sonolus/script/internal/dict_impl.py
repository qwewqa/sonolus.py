from typing import Any, Self

from sonolus.script.internal.impl import meta_fn, validate_value
from sonolus.script.internal.transient import TransientValue


class DictImpl(TransientValue):
    def __init__(self, value: dict):
        self.value = value

    @meta_fn
    def __getitem__(self, item):
        item = validate_value(item)
        if not item._is_py_():
            raise TypeError("Key must be a compile-time constant")
        item = item._as_py_()
        if item not in self.value:
            raise KeyError(item)
        return self.value[item]

    @meta_fn
    def __contains__(self, item):
        item = validate_value(item)
        if not item._is_py_():
            raise TypeError("Key must be a compile-time constant")
        item = item._as_py_()
        return item in self.value

    @meta_fn
    def __len__(self):
        return len(self.value)

    def __eq__(self, other):
        raise TypeError("Comparing dicts is not supported")

    __hash__ = None

    @meta_fn
    def __or__(self, other):
        if not isinstance(other, DictImpl):
            raise TypeError("Only dicts can be merged")
        return DictImpl({**self.value, **other.value})

    @classmethod
    def _accepts_(cls, value: Any) -> bool:
        return isinstance(value, cls | dict)

    @classmethod
    def _accept_(cls, value: Any) -> Self:
        if not cls._accepts_(value):
            raise TypeError(f"Cannot accept {value} as {cls.__name__}")
        if isinstance(value, cls):
            return value
        else:
            return cls({validate_value(k)._as_py_(): validate_value(v) for k, v in value.items()})

    def _is_py_(self) -> bool:
        return all(v._is_py_() for v in self.value.values())

    def _as_py_(self) -> Any:
        return {k: v._as_py_() for k, v in self.value.items()}


DictImpl.__name__ = "dict"
DictImpl.__qualname__ = "dict"
