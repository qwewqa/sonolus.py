from typing import Any

from sonolus.script.internal.dict_impl import DictImpl
from sonolus.script.internal.impl import validate_value
from sonolus.script.internal.meta_fn import meta_fn
from sonolus.script.record import Record


class SetImpl[Keys, OrderedKeys, Values](Record):
    _dict: DictImpl[Keys, OrderedKeys, Values]

    def __len__(self) -> int:
        return len(self._dict)

    def __contains__(self, item):
        return item in self._dict

    @meta_fn
    def __eq__(self, other: Any):
        raise TypeError("Set equality comparison is not supported")

    __hash__ = None

    @meta_fn
    def __or__(self, other):
        if not isinstance(other, SetImpl):
            raise TypeError("Unsupported type for '|' operator")
        return SetImpl(self._dict | other._dict)

    @staticmethod
    def from_set(s):
        values = [validate_value(v) for v in s]
        d = DictImpl.from_dict(dict.fromkeys(values))
        return SetImpl(d)
