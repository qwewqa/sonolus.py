from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from types import FunctionType
from typing import Annotated, Any, ClassVar, Self, get_origin

from sonolus.backend.ir import IRConst, IRInstr
from sonolus.backend.mode import Mode
from sonolus.backend.ops import Op
from sonolus.script.bucket import Bucket, Judgment
from sonolus.script.callbacks import PLAY_CALLBACKS, WATCH_ARCHETYPE_CALLBACKS, CallbackInfo
from sonolus.script.internal.context import ctx
from sonolus.script.internal.descriptor import SonolusDescriptor
from sonolus.script.internal.generic import validate_concrete_type
from sonolus.script.internal.impl import meta_fn, validate_value
from sonolus.script.internal.introspection import get_field_specifiers
from sonolus.script.internal.native import native_call
from sonolus.script.internal.value import Value
from sonolus.script.num import Num
from sonolus.script.pointer import deref
from sonolus.script.record import Record
from sonolus.script.values import zeros

ENTITY_MEMORY_SIZE = 64
ENTITY_DATA_SIZE = 32
ENTITY_SHARED_MEMORY_SIZE = 32


class StorageType(Enum):
    IMPORTED = "imported"
    EXPORTED = "exported"
    MEMORY = "memory"
    SHARED = "shared_memory"


@dataclass
class ArchetypeFieldInfo:
    name: str | None
    storage: StorageType


class ArchetypeField(SonolusDescriptor):
    def __init__(self, name: str, data_name: str, storage: StorageType, offset: int, type_: type[Value]):
        self.name = name
        self.data_name = data_name  # name used in level data
        self.storage = storage
        self.offset = offset
        self.type = type_

    def __get__(self, instance: BaseArchetype, owner):
        if instance is None:
            return self
        result = None
        match self.storage:
            case StorageType.IMPORTED:
                match instance._data_:
                    case ArchetypeSelfData():
                        result = deref(ctx().blocks.EntityData, self.offset, self.type)
                    case ArchetypeReferenceData(index=index):
                        result = deref(ctx().blocks.EntityDataArray, self.offset + index * ENTITY_DATA_SIZE, self.type)
                    case ArchetypeLevelData(values=values):
                        result = values[self.name]
            case StorageType.EXPORTED:
                raise RuntimeError("Exported fields are write-only")
            case StorageType.MEMORY:
                match instance._data_:
                    case ArchetypeSelfData():
                        result = deref(ctx().blocks.EntityMemory, self.offset, self.type)
                    case ArchetypeReferenceData():
                        raise RuntimeError("Entity memory of other entities is not accessible")
                    case ArchetypeLevelData():
                        raise RuntimeError("Entity memory is not available in level data")
            case StorageType.SHARED:
                match instance._data_:
                    case ArchetypeSelfData():
                        result = deref(ctx().blocks.EntitySharedMemory, self.offset, self.type)
                    case ArchetypeReferenceData(index=index):
                        result = deref(
                            ctx().blocks.EntitySharedMemoryArray,
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

    def __set__(self, instance: BaseArchetype, value):
        if instance is None:
            raise RuntimeError("Cannot set field on class")
        if not self.type._accepts_(value):
            raise TypeError(f"Expected {self.type}, got {type(value)}")
        target = None
        match self.storage:
            case StorageType.IMPORTED:
                match instance._data_:
                    case ArchetypeSelfData():
                        target = deref(ctx().blocks.EntityData, self.offset, self.type)
                    case ArchetypeReferenceData(index=index):
                        target = deref(ctx().blocks.EntityDataArray, self.offset + index * ENTITY_DATA_SIZE, self.type)
                    case ArchetypeLevelData(values=values):
                        target = values[self.name]
            case StorageType.EXPORTED:
                match instance._data_:
                    case ArchetypeSelfData():
                        if not isinstance(value, self.type):
                            raise TypeError(f"Expected {self.type}, got {type(value)}")
                        for k, v in value._to_flat_dict_(self.data_name).items():
                            index = instance._exported_keys_[k]
                            ctx().add_statements(IRInstr(Op.ExportValue, [IRConst(index), Num._accept_(v).ir()]))
                        return
                    case ArchetypeReferenceData():
                        raise RuntimeError("Exported fields of other entities are not accessible")
                    case ArchetypeLevelData():
                        raise RuntimeError("Exported fields are not available in level data")
            case StorageType.MEMORY:
                match instance._data_:
                    case ArchetypeSelfData():
                        target = deref(ctx().blocks.EntityMemory, self.offset, self.type)
                    case ArchetypeReferenceData():
                        raise RuntimeError("Entity memory of other entities is not accessible")
                    case ArchetypeLevelData():
                        raise RuntimeError("Entity memory is not available in level data")
            case StorageType.SHARED:
                match instance._data_:
                    case ArchetypeSelfData():
                        target = deref(ctx().blocks.EntitySharedMemory, self.offset, self.type)
                    case ArchetypeReferenceData(index=index):
                        target = deref(
                            ctx().blocks.EntitySharedMemoryArray,
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


def imported(*, name: str | None = None) -> Any:
    return ArchetypeFieldInfo(name, StorageType.IMPORTED)


def exported(*, name: str | None = None) -> Any:
    return ArchetypeFieldInfo(name, StorageType.EXPORTED)


def entity_memory() -> Any:
    return ArchetypeFieldInfo(None, StorageType.MEMORY)


def shared_memory() -> Any:
    return ArchetypeFieldInfo(None, StorageType.SHARED)


_annotation_defaults: dict[Callable, ArchetypeFieldInfo] = {
    imported: imported(),
    exported: exported(),
    entity_memory: entity_memory(),
    shared_memory: shared_memory(),
}


class StandardImport:
    Beat = Annotated[float, imported(name="#BEAT")]
    Bpm = Annotated[float, imported(name="#BPM")]
    Timescale = Annotated[float, imported(name="#TIMESCALE")]
    Judgment = Annotated[int, imported(name="#JUDGMENT")]
    Accuracy = Annotated[float, imported(name="#ACCURACY")]


def callback[T: Callable](order: int) -> Callable[[T], T]:
    def decorator(func: T) -> T:
        func._callback_order_ = order
        return func

    return decorator


class ArchetypeSelfData:
    pass


class ArchetypeReferenceData:
    index: Num

    def __init__(self, index: Num):
        self.index = index


class ArchetypeLevelData:
    values: dict[str, Value]

    def __init__(self, values: dict[str, Value]):
        self.values = values


type ArchetypeData = ArchetypeSelfData | ArchetypeReferenceData | ArchetypeLevelData


class BaseArchetype:
    _is_comptime_value_ = True

    _supported_callbacks_: ClassVar[dict[str, CallbackInfo]]
    _default_callbacks_: ClassVar[set[Callable]]

    _imported_fields_: ClassVar[dict[str, ArchetypeField]]
    _exported_fields_: ClassVar[dict[str, ArchetypeField]]
    _memory_fields_: ClassVar[dict[str, ArchetypeField]]
    _shared_memory_fields_: ClassVar[dict[str, ArchetypeField]]

    _imported_keys_: ClassVar[dict[str, int]]
    _exported_keys_: ClassVar[dict[str, int]]
    _callbacks_: ClassVar[list[Callable]]
    _data_constructor_signature_: ClassVar[inspect.Signature]
    _spawn_signature_: ClassVar[inspect.Signature]

    _data_: ArchetypeData

    name: ClassVar[str | None] = None
    is_scored: ClassVar[bool] = False

    def __init__(self, *args, **kwargs):
        if ctx():
            raise RuntimeError("The Archetype constructor is only for defining level data")
        bound = self._data_constructor_signature_.bind_partial(*args, **kwargs)
        bound.apply_defaults()
        values = {
            field.name: field.type._accept_(bound.arguments.get(field.name) or zeros(field.type))._get_()
            for field in self._imported_fields_.values()
        }
        self._data_ = ArchetypeLevelData(values=values)

    @classmethod
    def _new(cls):
        return object.__new__(cls)

    @classmethod
    def _for_compilation(cls):
        result = cls._new()
        result._data_ = ArchetypeSelfData()
        return result

    @classmethod
    @meta_fn
    def at(cls, index: Num) -> Self:
        result = cls._new()
        result._data_ = ArchetypeReferenceData(index=index)
        return result

    @classmethod
    @meta_fn
    def id(cls):
        if not ctx():
            raise RuntimeError("Archetype id is only available during compilation")
        result = ctx().global_state.archetypes.get(cls)
        if result is None:
            raise RuntimeError("Archetype is not registered")
        return result

    @classmethod
    @meta_fn
    def spawn(cls, **kwargs):
        if not ctx():
            raise RuntimeError("Spawn is only allowed within a callback")
        archetype_id = cls.id()
        bound = cls._spawn_signature_.bind_partial(**kwargs)
        bound.apply_defaults()
        data = []
        for field in cls._memory_fields_.values():
            data.extend(field.type._accept_(bound.arguments[field.name] or zeros(field.type))._to_list_())
        native_call(Op.Spawn, archetype_id, *(Num(x) for x in data))

    def _level_data_entries(self):
        if not isinstance(self._data_, ArchetypeLevelData):
            raise RuntimeError("Entity is not level data")
        entries = []
        for name, value in self._data_.values.items():
            field_info = self._imported_fields_.get(name)
            for k, v in value._to_flat_dict_(field_info.data_name).items():
                entries.append({"name": k, "value": v})
        return entries

    def __init_subclass__(cls, **kwargs):
        if cls.__module__ == BaseArchetype.__module__:
            if cls._supported_callbacks_ is None:
                raise TypeError("Cannot directly subclass Archetype, use the Archetype subclass for your mode")
            cls._default_callbacks_ = {getattr(cls, cb_info.py_name) for cb_info in cls._supported_callbacks_.values()}
            return
        if getattr(cls, "_callbacks_", None) is not None:
            raise TypeError("Cannot subclass Archetypes")
        if cls.name is None:
            cls.name = cls.__name__
        cls._imported_fields_ = {}
        cls._exported_fields_ = {}
        cls._memory_fields_ = {}
        cls._shared_memory_fields_ = {}
        imported_offset = 0
        exported_offset = 0
        memory_offset = 0
        shared_memory_offset = 0
        for name, value in get_field_specifiers(cls).items():
            if value is ClassVar or get_origin(value) is ClassVar:
                continue
            if get_origin(value) is not Annotated:
                raise TypeError(
                    "Archetype fields must be annotated using imported, exported, entity_memory, or shared_memory"
                )
            field_info = None
            for metadata in value.__metadata__:
                if isinstance(metadata, FunctionType):
                    metadata = _annotation_defaults.get(metadata, metadata)
                if isinstance(metadata, ArchetypeFieldInfo):
                    if field_info is not None:
                        raise TypeError(
                            f"Unexpected multiple field annotations for '{name}', "
                            f"expected exactly one of imported, exported, entity_memory, or shared_memory"
                        )
                    field_info = metadata
            if field_info is None:
                raise TypeError(
                    f"Missing field annotation for '{name}', "
                    f"expected exactly one of imported, exported, entity_memory, or shared_memory"
                )
            field_type = validate_concrete_type(value.__args__[0])
            match field_info.storage:
                case StorageType.IMPORTED:
                    cls._imported_fields_[name] = ArchetypeField(
                        name, field_info.name or name, field_info.storage, imported_offset, field_type
                    )
                    imported_offset += field_type._size_()
                    setattr(cls, name, cls._imported_fields_[name])
                case StorageType.EXPORTED:
                    cls._exported_fields_[name] = ArchetypeField(
                        name, field_info.name or name, field_info.storage, exported_offset, field_type
                    )
                    exported_offset += field_type._size_()
                    setattr(cls, name, cls._exported_fields_[name])
                case StorageType.MEMORY:
                    cls._memory_fields_[name] = ArchetypeField(
                        name, field_info.name or name, field_info.storage, memory_offset, field_type
                    )
                    memory_offset += field_type._size_()
                    setattr(cls, name, cls._memory_fields_[name])
                case StorageType.SHARED:
                    cls._shared_memory_fields_[name] = ArchetypeField(
                        name, field_info.name or name, field_info.storage, shared_memory_offset, field_type
                    )
                    shared_memory_offset += field_type._size_()
                    setattr(cls, name, cls._shared_memory_fields_[name])
        cls._imported_keys_ = {
            name: i
            for i, name in enumerate(
                key for field in cls._imported_fields_.values() for key in field.type._flat_keys_(field.data_name)
            )
        }
        cls._exported_keys_ = {
            name: i
            for i, name in enumerate(
                key for field in cls._exported_fields_.values() for key in field.type._flat_keys_(field.data_name)
            )
        }
        cls._data_constructor_signature_ = inspect.Signature(
            [inspect.Parameter(name, inspect.Parameter.POSITIONAL_OR_KEYWORD) for name in cls._imported_fields_]
        )
        cls._spawn_signature_ = inspect.Signature(
            [inspect.Parameter(name, inspect.Parameter.POSITIONAL_OR_KEYWORD) for name in cls._memory_fields_]
        )
        cls._callbacks_ = []
        for name in cls._supported_callbacks_:
            cb = getattr(cls, name)
            if cb in cls._default_callbacks_:
                continue
            cls._callbacks_.append(cb)


class PlayArchetype(BaseArchetype):
    _supported_callbacks_ = PLAY_CALLBACKS

    def preprocess(self):
        pass

    def spawn_order(self) -> float:
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

    @property
    @meta_fn
    def despawn(self):
        if not ctx():
            raise RuntimeError("Calling despawn is only allowed within a callback")
        match self._data_:
            case ArchetypeSelfData():
                return deref(ctx().blocks.EntityDespawn, 0, Num)
            case _:
                raise RuntimeError("Despawn is only accessible from the entity itself")

    @despawn.setter
    @meta_fn
    def despawn(self, value: bool):
        if not ctx():
            raise RuntimeError("Calling despawn is only allowed within a callback")
        match self._data_:
            case ArchetypeSelfData():
                deref(ctx().blocks.EntityDespawn, 0, Num)._set_(value)
            case _:
                raise RuntimeError("Despawn is only accessible from the entity itself")

    @property
    @meta_fn
    def _info(self):
        if not ctx():
            raise RuntimeError("Calling info is only allowed within a callback")
        match self._data_:
            case ArchetypeSelfData():
                return deref(ctx().blocks.EntityInfo, 0, PlayEntityInfo)
            case ArchetypeReferenceData(index=index):
                return deref(ctx().blocks.EntityInfoArray, index * PlayEntityInfo._size_(), PlayEntityInfo)
            case _:
                raise RuntimeError("Info is only accessible from the entity itself")

    @property
    def index(self) -> int:
        return self._info.index

    @property
    def is_waiting(self) -> bool:
        return self._info.state == 0

    @property
    def is_active(self) -> bool:
        return self._info.state == 1

    @property
    def is_despawned(self) -> bool:
        return self._info.state == 2

    @property
    def life(self) -> ArchetypeLife:
        if not ctx():
            raise RuntimeError("Calling life is only allowed within a callback")
        match self._data_:
            case ArchetypeSelfData() | ArchetypeReferenceData():
                return deref(ctx().blocks.ArchetypeLife, self.id() * ArchetypeLife._size_(), ArchetypeLife)
            case _:
                raise RuntimeError("Life is not available in level data")

    @property
    def result(self) -> PlayEntityInput:
        if not ctx():
            raise RuntimeError("Calling result is only allowed within a callback")
        match self._data_:
            case ArchetypeSelfData():
                return deref(ctx().blocks.EntityInput, 0, PlayEntityInput)
            case _:
                raise RuntimeError("Result is only accessible from the entity itself")


class WatchArchetype(BaseArchetype):
    _supported_callbacks_ = WATCH_ARCHETYPE_CALLBACKS

    def preprocess(self):
        pass

    def spawn_time(self) -> float:
        pass

    def despawn_time(self) -> float:
        pass

    def initialize(self):
        pass

    def update_sequential(self):
        pass

    def update_parallel(self):
        pass

    def terminate(self):
        pass

    @property
    @meta_fn
    def _info(self):
        if not ctx():
            raise RuntimeError("Calling info is only allowed within a callback")
        match self._data_:
            case ArchetypeSelfData():
                return deref(ctx().blocks.EntityInfo, 0, WatchEntityInfo)
            case ArchetypeReferenceData(index=index):
                return deref(ctx().blocks.EntityInfoArray, index * WatchEntityInfo._size_(), PlayEntityInfo)
            case _:
                raise RuntimeError("Info is only accessible from the entity itself")

    @property
    def index(self) -> int:
        return self._info.index

    @property
    def is_active(self) -> bool:
        return self._info.state == 1

    @property
    def life(self) -> ArchetypeLife:
        if not ctx():
            raise RuntimeError("Calling life is only allowed within a callback")
        match self._data_:
            case ArchetypeSelfData() | ArchetypeReferenceData():
                return deref(ctx().blocks.ArchetypeLife, self.id() * ArchetypeLife._size_(), ArchetypeLife)
            case _:
                raise RuntimeError("Life is not available in level data")

    @property
    def result(self) -> WatchEntityInput:
        if not ctx():
            raise RuntimeError("Calling result is only allowed within a callback")
        match self._data_:
            case ArchetypeSelfData():
                return deref(ctx().blocks.EntityInput, 0, WatchEntityInput)
            case _:
                raise RuntimeError("Result is only accessible from the entity itself")

    @property
    def target_time(self) -> float:
        return self.result.target_time

    @target_time.setter
    def target_time(self, value: float):
        self.result.target_time = value


@meta_fn
def entity_info_at(index: Num) -> PlayEntityInfo | WatchEntityInfo | PreviewEntityInfo:
    if not ctx():
        raise RuntimeError("Calling entity_info_at is only allowed within a callback")
    match ctx().global_state.mode:
        case Mode.Play:
            return deref(ctx().blocks.EntityInfoArray, index * PlayEntityInfo._size_(), PlayEntityInfo)
        case Mode.Watch:
            return deref(ctx().blocks.EntityInfoArray, index * WatchEntityInfo._size_(), WatchEntityInfo)
        case Mode.Preview:
            return deref(ctx().blocks.EntityInfoArray, index * PreviewEntityInfo._size_(), PreviewEntityInfo)
        case _:
            raise RuntimeError(f"Entity info is not available in mode '{ctx().global_state.mode}'")


@meta_fn
def archetype_life_of(archetype: type[BaseArchetype] | BaseArchetype) -> ArchetypeLife:
    archetype = validate_value(archetype)
    archetype = archetype._as_py_()
    if not ctx():
        raise RuntimeError("Calling archetype_life_of is only allowed within a callback")
    match ctx().global_state.mode:
        case Mode.Play | Mode.Watch:
            return deref(ctx().blocks.ArchetypeLife, archetype.id() * ArchetypeLife._size_(), ArchetypeLife)
        case _:
            raise RuntimeError(f"Archetype life is not available in mode '{ctx().global_state.mode}'")


class PlayEntityInfo(Record):
    index: int
    archetype_id: int
    state: int


class WatchEntityInfo(Record):
    index: int
    archetype_id: int
    state: int


class PreviewEntityInfo(Record):
    index: int
    archetype_id: int


class ArchetypeLife(Record):
    perfect_increment: Num
    great_increment: Num
    good_increment: Num
    miss_increment: Num

    def update(
        self,
        perfect_increment: Num | None = None,
        great_increment: Num | None = None,
        good_increment: Num | None = None,
        miss_increment: Num | None = None,
    ):
        if perfect_increment is not None:
            self.perfect_increment = perfect_increment
        if great_increment is not None:
            self.great_increment = great_increment
        if good_increment is not None:
            self.good_increment = good_increment
        if miss_increment is not None:
            self.miss_increment = miss_increment


class PlayEntityInput(Record):
    judgment: Judgment
    accuracy: float
    bucket: Bucket
    bucket_value: float


class WatchEntityInput(Record):
    target_time: float
    bucket: Bucket
    bucket_value: float


class EntityRef[A: BaseArchetype](Record):
    index: int

    @classmethod
    def archetype(cls) -> type[A]:
        return cls._get_type_arg_(A)

    def get(self) -> A:
        return self.archetype().at(Num(self.index))
