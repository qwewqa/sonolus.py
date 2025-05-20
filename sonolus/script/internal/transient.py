from collections.abc import Iterable
from typing import Any, Self

from sonolus.backend.place import BlockPlace
from sonolus.script.internal.value import DataValue, Value


class TransientValue(Value):
    @classmethod
    def _is_concrete_(cls) -> bool:
        return True

    @classmethod
    def _size_(cls) -> int:
        return 0

    @classmethod
    def _is_value_type_(cls) -> bool:
        return False

    @classmethod
    def _from_place_(cls, place: BlockPlace) -> Self:
        raise TypeError(f"{cls.__name__} cannot be dereferenced")

    @classmethod
    def _from_list_(cls, values: Iterable[DataValue]) -> Self:
        raise TypeError(f"{cls.__name__} cannot be constructed from list")

    def _to_list_(self, level_refs: dict[Any, str] | None = None) -> list[DataValue | str]:
        raise TypeError(f"{type(self).__name__} cannot be deconstructed to list")

    @classmethod
    def _flat_keys_(cls, prefix: str) -> list[str]:
        raise TypeError(f"{cls.__name__} cannot be flattened")

    def _get_(self) -> Self:
        return self

    def _set_(self, value: Self) -> None:
        if value is not self:
            raise TypeError(f"{type(self).__name__} is immutable")

    def _copy_from_(self, value: Self):
        raise TypeError(f"{type(self).__name__} is immutable")

    def _copy_(self) -> Self:
        raise TypeError(f"{type(self).__name__} cannot be copied")

    @classmethod
    def _alloc_(cls) -> Self:
        raise TypeError(f"{cls.__name__} is not allocatable")

    @classmethod
    def _zero_(cls) -> Self:
        raise TypeError(f"{cls.__name__} does not have a zero value")
