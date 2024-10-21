from dataclasses import dataclass
from enum import Enum
from typing import Annotated, Callable, ClassVar

from sonolus.script.internal.value import Value
from sonolus.script.num import Num


class StorageType(str, Enum):
    Imported = "imported"
    Exported = "exported"
    Memory = "memory"
    Shared = "shared_memory"


@dataclass
class ArchetypeFieldInfo:
    name: str | None
    storage: StorageType


class ArchetypeField:
    name: str | None
    storage: StorageType
    offset: int
    type: type[Value]


def imported[T: type](t: T, *, name: str | None = None) -> T:
    return Annotated[t, ArchetypeFieldInfo(name, StorageType.Imported)]


def exported[T: type](t: T, *, name: str | None = None) -> T:
    return Annotated[t, ArchetypeFieldInfo(name, StorageType.Exported)]


def entity_memory[T: type](t: T) -> T:
    return Annotated[t, ArchetypeFieldInfo(None, StorageType.Memory)]


def shared_memory[T: type](t: T) -> T:
    return Annotated[t, ArchetypeFieldInfo(None, StorageType.Shared)]


def callback[T: Callable](order: int) -> Callable[[T], T]:
    def decorator(func: T) -> T:
        func._callback_order_ = order
        return func

    return decorator


class ArchetypeSelfData:
    pass


class ArchetypeReferenceData:
    index: Num


class Archetype:
    _imported_fields_: ClassVar[dict[str, ArchetypeField]]
    _exported_fields_: ClassVar[dict[str, ArchetypeField]]
    _memory_fields_: ClassVar[dict[str, ArchetypeField]]
    _shared_memory_fields_: ClassVar[dict[str, ArchetypeField]]

    _imported_keys_: ClassVar[dict[str, int]]
    _exported_keys_: ClassVar[dict[str, int]]
    _callbacks_: ClassVar[list[Callable]]
