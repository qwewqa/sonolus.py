from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, ClassVar, get_origin

from scripts.out.blocks import PlayBlock
from sonolus.backend.ir import IRConst, IRInstr
from sonolus.backend.ops import Op
from sonolus.script.callbacks import PLAY_CALLBACKS, CallbackInfo
from sonolus.script.internal.context import ctx
from sonolus.script.internal.generic import validate_concrete_type
from sonolus.script.internal.value import Value
from sonolus.script.num import Num
from sonolus.script.pointer import deref

ENTITY_MEMORY_SIZE = 64
ENTITY_DATA_SIZE = 32
ENTITY_SHARED_MEMORY_SIZE = 32


class StorageType(StrEnum):
    Imported = "imported"
    Exported = "exported"
    Memory = "memory"
    Shared = "shared_memory"


@dataclass
class ArchetypeFieldInfo:
    name: str | None
    storage: StorageType


class ArchetypeField:
    def __init__(self, name: str | None, storage: StorageType, offset: int, type_: type[Value]):
        self.name = name
        self.storage = storage
        self.offset = offset
        self.type = type_

    def __get__(self, instance: Archetype, owner):
        if instance is None:
            return self
        result = None
        match self.storage:
            case StorageType.Imported:
                match instance._data_:
                    case ArchetypeSelfData():
                        result = deref(PlayBlock.EntityData, self.offset, self.type)
                    case ArchetypeReferenceData(index=index):
                        result = deref(PlayBlock.EntityDataArray, self.offset + index * ENTITY_DATA_SIZE, self.type)
                    case ArchetypeLevelData(values=values):
                        result = values[self.name]
            case StorageType.Exported:
                raise RuntimeError("Exported fields are write-only")
            case StorageType.Memory:
                match instance._data_:
                    case ArchetypeSelfData():
                        result = deref(PlayBlock.EntityMemory, self.offset, self.type)
                    case ArchetypeReferenceData():
                        raise RuntimeError("Entity memory of other entities is not accessible")
                    case ArchetypeLevelData():
                        raise RuntimeError("Entity memory is not available in level data")
            case StorageType.Shared:
                match instance._data_:
                    case ArchetypeSelfData():
                        result = deref(PlayBlock.EntitySharedMemory, self.offset, self.type)
                    case ArchetypeReferenceData(index=index):
                        result = deref(
                            PlayBlock.EntitySharedMemoryArray,
                            self.offset + index * ENTITY_SHARED_MEMORY_SIZE,
                            self.type,
                        )
                    case ArchetypeLevelData():
                        raise RuntimeError("Entity shared memory is not available in level data")
        if result is None:
            raise RuntimeError("Invalid storage type")
        if ctx():
            return result._get_()
        else:
            return result._as_py_()

    def __set__(self, instance: Archetype, value):
        if instance is None:
            raise RuntimeError("Cannot set field on class")
        if not self.type._accepts_(value):
            raise TypeError(f"Expected {self.type}, got {type(value)}")
        target = None
        match self.storage:
            case StorageType.Imported:
                match instance._data_:
                    case ArchetypeSelfData():
                        target = deref(PlayBlock.EntityData, self.offset, self.type)
                    case ArchetypeReferenceData(index=index):
                        target = deref(PlayBlock.EntityDataArray, self.offset + index * ENTITY_DATA_SIZE, self.type)
                    case ArchetypeLevelData(values=values):
                        target = values[self.name]
            case StorageType.Exported:
                match instance._data_:
                    case ArchetypeSelfData():
                        if not isinstance(value, self.type):
                            raise TypeError(f"Expected {self.type}, got {type(value)}")
                        for k, v in value._to_flat_dict_(self.name).items():
                            index = instance._exported_keys_[k]
                            ctx().add_statements(IRInstr(Op.ExportValue, [IRConst(index), Num._accept_(v).ir()]))
                        return
                    case ArchetypeReferenceData():
                        raise RuntimeError("Exported fields of other entities are not accessible")
                    case ArchetypeLevelData():
                        raise RuntimeError("Exported fields are not available in level data")
            case StorageType.Memory:
                match instance._data_:
                    case ArchetypeSelfData():
                        target = deref(PlayBlock.EntityMemory, self.offset, self.type)
                    case ArchetypeReferenceData():
                        raise RuntimeError("Entity memory of other entities is not accessible")
                    case ArchetypeLevelData():
                        raise RuntimeError("Entity memory is not available in level data")
            case StorageType.Shared:
                match instance._data_:
                    case ArchetypeSelfData():
                        target = deref(PlayBlock.EntitySharedMemory, self.offset, self.type)
                    case ArchetypeReferenceData(index=index):
                        target = deref(
                            PlayBlock.EntitySharedMemoryArray,
                            self.offset + index * ENTITY_SHARED_MEMORY_SIZE,
                            self.type,
                        )
                    case ArchetypeLevelData():
                        raise RuntimeError("Entity shared memory is not available in level data")
        if target is None:
            raise RuntimeError("Invalid storage type")
        value = self.type._accept_(value)
        if self.type._is_value_type_():
            target._set_(value)
        else:
            target._copy_from_(value)


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


class ArchetypeLevelData:
    values: dict[str, Value]


type ArchetypeData = ArchetypeSelfData | ArchetypeReferenceData | ArchetypeLevelData


class Archetype:
    _supported_callbacks_: ClassVar[dict[str, CallbackInfo]]
    _default_callbacks_: ClassVar[set[Callable]]

    _imported_fields_: ClassVar[dict[str, ArchetypeField]]
    _exported_fields_: ClassVar[dict[str, ArchetypeField]]
    _memory_fields_: ClassVar[dict[str, ArchetypeField]]
    _shared_memory_fields_: ClassVar[dict[str, ArchetypeField]]

    _imported_keys_: ClassVar[dict[str, int]]
    _exported_keys_: ClassVar[dict[str, int]]
    _callbacks_: ClassVar[list[Callable]]

    _data_: ArchetypeData

    def __init_subclass__(cls, **kwargs):
        if cls.__module__ == Archetype.__module__:
            if cls._supported_callbacks_ is None:
                raise TypeError("Cannot directly subclass Archetype, use the Archetype subclass for your mode")
            cls._default_callbacks_ = {getattr(cls, cb_info.py_name) for cb_info in cls._supported_callbacks_.values()}
            return
        if getattr(cls, "_callbacks_", None) is not None:
            raise TypeError("Cannot subclass Archetypes")
        annotations = inspect.get_annotations(cls, eval_str=True)
        cls._imported_fields_ = {}
        cls._exported_fields_ = {}
        cls._memory_fields_ = {}
        cls._shared_memory_fields_ = {}
        imported_offset = 0
        exported_offset = 0
        memory_offset = 0
        shared_memory_offset = 0
        for name, value in annotations.items():
            if value is ClassVar or get_origin(value) is ClassVar:
                continue
            if get_origin(value) is not Annotated:
                raise TypeError(
                    "Archetype fields must be annotated using imported, exported, entity_memory, or shared_memory"
                )
            field_info_list = [a for a in value.__metadata__ if isinstance(a, ArchetypeFieldInfo)]
            if len(field_info_list) != 1:
                raise TypeError(
                    "Archetype fields must be annotated using imported, exported, entity_memory, or shared_memory once"
                )
            field_info = field_info_list[0]
            field_type = validate_concrete_type(value.__origin__)
            match field_info.storage:
                case StorageType.Imported:
                    cls._imported_fields_[name] = ArchetypeField(name, field_info.storage, imported_offset, field_type)
                    imported_offset += field_type._size_()
                case StorageType.Exported:
                    cls._exported_fields_[name] = ArchetypeField(name, field_info.storage, exported_offset, field_type)
                    exported_offset += field_type._size_()
                case StorageType.Memory:
                    cls._memory_fields_[name] = ArchetypeField(name, field_info.storage, memory_offset, field_type)
                    memory_offset += field_type._size_()
                case StorageType.Shared:
                    cls._shared_memory_fields_[name] = ArchetypeField(
                        name, field_info.storage, shared_memory_offset, field_type
                    )
                    shared_memory_offset += field_type._size_()
        cls._imported_keys_ = {
            name: i
            for i, name in enumerate(
                key for field in cls._imported_fields_.values() for key in field.type._flat_keys_(field.name)
            )
        }
        cls._exported_keys_ = {
            name: i
            for i, name in enumerate(
                key for field in cls._exported_fields_.values() for key in field.type._flat_keys_(field.name)
            )
        }


class PlayArchetype(Archetype):
    _supported_callbacks_ = PLAY_CALLBACKS

    def preprocess(self):
        pass

    def spawn_order(self) -> int:
        pass

    def should_spawn(self) -> bool:
        pass

    def initialize(self):
        pass

    def update_sequential(self):
        pass

    def update_parallel(self):
        pass

    def touch(self):
        pass

    def terminate(self):
        pass
