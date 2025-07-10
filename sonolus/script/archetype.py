from __future__ import annotations

import inspect
from abc import abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, StrEnum
from types import FunctionType
from typing import Annotated, Any, ClassVar, Self, TypedDict, get_origin

from sonolus.backend.ir import IRConst, IRExpr, IRInstr, IRPureInstr, IRStmt
from sonolus.backend.mode import Mode
from sonolus.backend.ops import Op
from sonolus.script.bucket import Bucket, Judgment
from sonolus.script.internal.callbacks import PLAY_CALLBACKS, PREVIEW_CALLBACKS, WATCH_ARCHETYPE_CALLBACKS, CallbackInfo
from sonolus.script.internal.context import ctx
from sonolus.script.internal.descriptor import SonolusDescriptor
from sonolus.script.internal.generic import validate_concrete_type
from sonolus.script.internal.impl import meta_fn, validate_value
from sonolus.script.internal.introspection import get_field_specifiers
from sonolus.script.internal.native import native_call
from sonolus.script.internal.value import BackingValue, DataValue, Value
from sonolus.script.num import Num
from sonolus.script.pointer import _backing_deref, _deref
from sonolus.script.record import Record
from sonolus.script.values import zeros

_ENTITY_MEMORY_SIZE = 64
_ENTITY_DATA_SIZE = 32
_ENTITY_SHARED_MEMORY_SIZE = 32


class _StorageType(Enum):
    IMPORTED = "imported"
    EXPORTED = "exported"
    MEMORY = "memory"
    SHARED = "shared_memory"


@dataclass
class _ArchetypeFieldInfo:
    name: str | None
    storage: _StorageType


class _ExportBackingValue(BackingValue):
    def __init__(self, index: IRExpr):
        self.index = index

    def read(self) -> IRExpr:
        raise NotImplementedError("Exported fields are write-only")

    def write(self, value: IRExpr) -> IRStmt:
        return IRInstr(Op.ExportValue, [self.index, value])


class _ArchetypeField(SonolusDescriptor):
    def __init__(self, name: str, data_name: str, storage: _StorageType, offset: int, type_: type[Value]):
        self.name = name
        self.data_name = data_name  # name used in level data
        self.storage = storage
        self.offset = offset
        self.type = type_

    def __get__(self, instance: _BaseArchetype, owner):
        if instance is None:
            return self
        result = None
        match self.storage:
            case _StorageType.IMPORTED:
                match instance._data_:
                    case _ArchetypeSelfData():
                        result = _deref(ctx().blocks.EntityData, self.offset, self.type)
                    case _ArchetypeReferenceData(index=index):
                        result = _deref(
                            ctx().blocks.EntityDataArray,
                            Num._accept_(self.offset) + index * _ENTITY_DATA_SIZE,
                            self.type,
                        )
                    case _ArchetypeLevelData(values=values):
                        result = values[self.name]
            case _StorageType.EXPORTED:
                match instance._data_:
                    case _ArchetypeSelfData():

                        def backing_source(i: IRExpr):
                            return _ExportBackingValue(IRPureInstr(Op.Add, [i, IRConst(self.offset)]))

                        result = _backing_deref(
                            backing_source,
                            self.type,
                        )
                    case _ArchetypeReferenceData():
                        raise RuntimeError("Exported fields of other entities are not accessible")
                    case _ArchetypeLevelData():
                        raise RuntimeError("Exported fields are not available in level data")
            case _StorageType.MEMORY:
                match instance._data_:
                    case _ArchetypeSelfData():
                        result = _deref(ctx().blocks.EntityMemory, self.offset, self.type)
                    case _ArchetypeReferenceData():
                        raise RuntimeError("Entity memory of other entities is not accessible")
                    case _ArchetypeLevelData():
                        raise RuntimeError("Entity memory is not available in level data")
            case _StorageType.SHARED:
                match instance._data_:
                    case _ArchetypeSelfData():
                        result = _deref(ctx().blocks.EntitySharedMemory, self.offset, self.type)
                    case _ArchetypeReferenceData(index=index):
                        result = _deref(
                            ctx().blocks.EntitySharedMemoryArray,
                            Num._accept_(self.offset) + index * _ENTITY_SHARED_MEMORY_SIZE,
                            self.type,
                        )
                    case _ArchetypeLevelData():
                        raise RuntimeError("Entity shared memory is not available in level data")
        if result is None:
            raise RuntimeError("Invalid storage type")
        if ctx():
            return result._get_()
        else:
            return result._as_py_()

    def __set__(self, instance: _BaseArchetype, value):
        if instance is None:
            raise RuntimeError("Cannot set field on class")
        if not self.type._accepts_(value):
            raise TypeError(f"Expected {self.type}, got {type(value)}")
        target = None
        match self.storage:
            case _StorageType.IMPORTED:
                match instance._data_:
                    case _ArchetypeSelfData():
                        target = _deref(ctx().blocks.EntityData, self.offset, self.type)
                    case _ArchetypeReferenceData(index=index):
                        target = _deref(
                            ctx().blocks.EntityDataArray,
                            Num._accept_(self.offset) + index * _ENTITY_DATA_SIZE,
                            self.type,
                        )
                    case _ArchetypeLevelData(values=values):
                        target = values[self.name]
            case _StorageType.EXPORTED:
                match instance._data_:
                    case _ArchetypeSelfData():
                        if not isinstance(value, self.type):
                            raise TypeError(f"Expected {self.type}, got {type(value)}")
                        for k, v in value._to_flat_dict_(self.data_name).items():
                            index = instance._exported_keys_[k]
                            ctx().add_statements(IRInstr(Op.ExportValue, [IRConst(index), Num(v).ir()]))
                        return
                    case _ArchetypeReferenceData():
                        raise RuntimeError("Exported fields of other entities are not accessible")
                    case _ArchetypeLevelData():
                        raise RuntimeError("Exported fields are not available in level data")
            case _StorageType.MEMORY:
                match instance._data_:
                    case _ArchetypeSelfData():
                        target = _deref(ctx().blocks.EntityMemory, self.offset, self.type)
                    case _ArchetypeReferenceData():
                        raise RuntimeError("Entity memory of other entities is not accessible")
                    case _ArchetypeLevelData():
                        raise RuntimeError("Entity memory is not available in level data")
            case _StorageType.SHARED:
                match instance._data_:
                    case _ArchetypeSelfData():
                        target = _deref(ctx().blocks.EntitySharedMemory, self.offset, self.type)
                    case _ArchetypeReferenceData(index=index):
                        target = _deref(
                            ctx().blocks.EntitySharedMemoryArray,
                            Num._accept_(self.offset) + index * _ENTITY_SHARED_MEMORY_SIZE,
                            self.type,
                        )
                    case _ArchetypeLevelData():
                        raise RuntimeError("Entity shared memory is not available in level data")
        if target is None:
            raise RuntimeError("Invalid storage type")
        value = self.type._accept_(value)
        if self.type._is_value_type_():
            target._set_(value)
        else:
            target._copy_from_(value)


def imported(*, name: str | None = None) -> Any:
    """Declare a field as imported.

    Imported fields may be loaded from the level.

    In watch mode, data may also be loaded from a corresponding exported field in play mode.

    Imported fields may only be updated in the `preprocess` callback, and are read-only in other callbacks.

    Usage:
        ```python
        class MyArchetype(PlayArchetype):
            field: int = imported()
            field_with_explicit_name: int = imported(name="field_name")
        ```
    """
    return _ArchetypeFieldInfo(name, _StorageType.IMPORTED)


def entity_data() -> Any:
    """Declare a field as entity data.

    Entity data is accessible from other entities, but may only be updated in the `preprocess` callback
    and is read-only in other callbacks.

    It functions like `imported` and shares the same underlying storage, except that it is not loaded from a level.

    Usage:
        ```python
        class MyArchetype(PlayArchetype):
            field: int = entity_data()
        ```
    """
    return _ArchetypeFieldInfo(None, _StorageType.IMPORTED)


def exported(*, name: str | None = None) -> Any:
    """Declare a field as exported.

    This is only usable in play mode to export data to be loaded in watch mode.

    Exported fields are write-only.

    Usage:
        ```python
        class MyArchetype(PlayArchetype):
            field: int = exported()
            field_with_explicit_name: int = exported(name="#FIELD")
        ```
    """
    return _ArchetypeFieldInfo(name, _StorageType.EXPORTED)


def entity_memory() -> Any:
    """Declare a field as entity memory.

    Entity memory is private to the entity and is not accessible from other entities. It may be read or updated in any
    callback associated with the entity.

    Entity memory fields may also be set when an entity is spawned using the `spawn()` method.

    Usage:
        ```python
        class MyArchetype(PlayArchetype):
            field: int = entity_memory()

        ```
    """
    return _ArchetypeFieldInfo(None, _StorageType.MEMORY)


def shared_memory() -> Any:
    """Declare a field as shared memory.

    Shared memory is accessible from other entities.

    Shared memory may be read in any callback, but may only be updated by sequential callbacks
    (`preprocess`, `update_sequential`, and `touch`).

    Usage:
        ```python
        class MyArchetype(PlayArchetype):
            field: int = shared_memory()
        ```
    """
    return _ArchetypeFieldInfo(None, _StorageType.SHARED)


_annotation_defaults: dict[Callable, _ArchetypeFieldInfo] = {
    imported: imported(),
    exported: exported(),
    entity_memory: entity_memory(),
    shared_memory: shared_memory(),
}


class StandardImport:
    """Standard import annotations for Archetype fields.

    Usage:
        ```python
        class MyArchetype(WatchArchetype):
            judgment: StandardImport.JUDGMENT
        ```
    """

    BEAT = Annotated[float, imported(name="#BEAT")]
    """The beat of the entity."""

    BPM = Annotated[float, imported(name="#BPM")]
    """The bpm, for bpm change markers."""

    TIMESCALE = Annotated[float, imported(name="#TIMESCALE")]
    """The timescale, for timescale change markers."""

    JUDGMENT = Annotated[int, imported(name="#JUDGMENT")]
    """The judgment of the entity.

    Automatically supported in watch mode for archetypes with a corresponding scored play mode archetype.
    """
    ACCURACY = Annotated[float, imported(name="#ACCURACY")]
    """The accuracy of the entity.

    Automatically supported in watch mode for archetypes with a corresponding scored play mode archetype.
    """


def callback[T: Callable](*, order: int = 0) -> Callable[[T], T]:
    """Annotate a callback with its order.

    Callbacks are execute from lowest to highest order. By default, callbacks have an order of 0.

    Usage:
        ```python
        class MyArchetype(PlayArchetype):
            @callback(order=1)
            def update_sequential(self):
                pass
        ```

    Args:
        order: The order of the callback. Lower values are executed first.
    """

    def decorator(func: T) -> T:
        func._callback_order_ = order
        return func

    return decorator


class _ArchetypeSelfData:
    pass


class _ArchetypeReferenceData:
    index: Num

    def __init__(self, index: Num):
        self.index = index


class _ArchetypeLevelData:
    values: dict[str, Value]

    def __init__(self, values: dict[str, Value]):
        self.values = values


type _ArchetypeData = _ArchetypeSelfData | _ArchetypeReferenceData | _ArchetypeLevelData


class ArchetypeSchema(TypedDict):
    name: str
    fields: list[str]


class _BaseArchetype:
    _is_comptime_value_ = True

    _removable_prefix: ClassVar[str] = ""

    _supported_callbacks_: ClassVar[dict[str, CallbackInfo]]
    _default_callbacks_: ClassVar[set[Callable]]

    _imported_fields_: ClassVar[dict[str, _ArchetypeField]]
    _exported_fields_: ClassVar[dict[str, _ArchetypeField]]
    _memory_fields_: ClassVar[dict[str, _ArchetypeField]]
    _shared_memory_fields_: ClassVar[dict[str, _ArchetypeField]]

    _imported_keys_: ClassVar[dict[str, int]]
    _exported_keys_: ClassVar[dict[str, int]]
    _callbacks_: ClassVar[list[Callable]]
    _data_constructor_signature_: ClassVar[inspect.Signature]
    _spawn_signature_: ClassVar[inspect.Signature]

    _data_: _ArchetypeData

    name: ClassVar[str | None] = None
    """The name of the archetype.

    If not set, the name will be the class name.

    The name is used in level data.
    """

    is_scored: ClassVar[bool] = False

    def __init__(self, *args, **kwargs):
        self._init_fields()
        if ctx():
            raise RuntimeError("The Archetype constructor is only for defining level data")
        bound = self._data_constructor_signature_.bind_partial(*args, **kwargs)
        bound.apply_defaults()
        values = {
            field.name: field.type._accept_(bound.arguments.get(field.name) or zeros(field.type))._get_()
            for field in self._imported_fields_.values()
        }
        self._data_ = _ArchetypeLevelData(values=values)

    @classmethod
    def _new(cls):
        cls._init_fields()
        return object.__new__(cls)

    @classmethod
    def _for_compilation(cls):
        cls._init_fields()
        result = cls._new()
        result._data_ = _ArchetypeSelfData()
        return result

    @classmethod
    @meta_fn
    def at(cls, index: Num) -> Self:
        result = cls._new()
        result._data_ = _ArchetypeReferenceData(index=Num._accept_(index))
        return result

    @classmethod
    @meta_fn
    def is_at(cls, index: Num) -> bool:
        if not ctx():
            raise RuntimeError("is_at is only available during compilation")
        return entity_info_at(index).archetype_id == cls.id()

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
    def spawn(cls, **kwargs: Any) -> None:
        """Spawn an entity of this archetype, injecting the given values into entity memory.

        Usage:
            ```python
            class MyArchetype(PlayArchetype):
                field: int = entity_memory()

            def f():
                MyArchetype.spawn(field=123)
            ```

        Args:
            **kwargs: Entity memory values to inject by field name as defined in the Archetype.
        """
        cls._init_fields()
        if not ctx():
            raise RuntimeError("Spawn is only allowed within a callback")
        archetype_id = cls.id()
        bound = cls._spawn_signature_.bind_partial(**kwargs)
        bound.apply_defaults()
        data = []
        for field in cls._memory_fields_.values():
            data.extend(
                field.type._accept_(
                    bound.arguments[field.name] if field.name in bound.arguments else zeros(field.type)
                )._to_list_()
            )
        native_call(Op.Spawn, archetype_id, *(Num(x) for x in data))

    @classmethod
    def schema(cls) -> ArchetypeSchema:
        cls._init_fields()
        return {"name": cls.name or "unnamed", "fields": list(cls._imported_fields_)}

    def _level_data_entries(self, level_refs: dict[Any, str] | None = None):
        self._init_fields()
        if not isinstance(self._data_, _ArchetypeLevelData):
            raise RuntimeError("Entity is not level data")
        entries = []
        for name, value in self._data_.values.items():
            field_info = self._imported_fields_.get(name)
            for k, v in value._to_flat_dict_(field_info.data_name, level_refs).items():
                if isinstance(v, str):
                    entries.append({"name": k, "ref": v})
                else:
                    entries.append({"name": k, "value": v})
        return entries

    def __init_subclass__(cls, **kwargs):
        if cls.__module__ == _BaseArchetype.__module__:
            if cls._supported_callbacks_ is None:
                raise TypeError("Cannot directly subclass Archetype, use the Archetype subclass for your mode")
            cls._default_callbacks_ = {getattr(cls, cb_info.py_name) for cb_info in cls._supported_callbacks_.values()}
            return
        if cls.name is None or cls.name in {getattr(mro_entry, "name", None) for mro_entry in cls.mro()[1:]}:
            cls.name = cls.__name__.removeprefix(cls._removable_prefix)
        cls._callbacks_ = []
        for name in cls._supported_callbacks_:
            cb = getattr(cls, name)
            if cb in cls._default_callbacks_:
                continue
            cls._callbacks_.append(cb)
        cls._field_init_done = False

    @classmethod
    def _init_fields(cls):
        if cls._field_init_done:
            return
        cls._field_init_done = True
        for mro_entry in cls.mro()[1:]:
            if hasattr(mro_entry, "_field_init_done"):
                mro_entry._init_fields()
        field_specifiers = get_field_specifiers(
            cls, skip={"name", "is_scored", "_callbacks_", "_field_init_done"}
        ).items()
        if not hasattr(cls, "_imported_fields_"):
            cls._imported_fields_ = {}
        else:
            cls._imported_fields_ = {**cls._imported_fields_}
        if not hasattr(cls, "_exported_fields_"):
            cls._exported_fields_ = {}
        else:
            cls._exported_fields_ = {**cls._exported_fields_}
        if not hasattr(cls, "_memory_fields_"):
            cls._memory_fields_ = {}
        else:
            cls._memory_fields_ = {**cls._memory_fields_}
        if not hasattr(cls, "_shared_memory_fields_"):
            cls._shared_memory_fields_ = {}
        else:
            cls._shared_memory_fields_ = {**cls._shared_memory_fields_}
        imported_offset = sum(field.type._size_() for field in cls._imported_fields_.values())
        exported_offset = sum(field.type._size_() for field in cls._exported_fields_.values())
        memory_offset = sum(field.type._size_() for field in cls._memory_fields_.values())
        shared_memory_offset = sum(field.type._size_() for field in cls._shared_memory_fields_.values())
        for name, value in field_specifiers:
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
                if isinstance(metadata, _ArchetypeFieldInfo):
                    if field_info is not None:
                        if field_info.storage == metadata.storage and field_info.name is None:
                            field_info = metadata
                        elif field_info.storage == metadata.storage and (
                            metadata.name is None or field_info.name == metadata.name
                        ):
                            pass
                        else:
                            raise TypeError(
                                f"Unexpected multiple field annotations for '{name}', "
                                f"expected exactly one of imported, exported, entity_memory, or shared_memory"
                            )
                    else:
                        field_info = metadata
            if field_info is None:
                raise TypeError(
                    f"Missing field annotation for '{name}', "
                    f"expected exactly one of imported, exported, entity_memory, or shared_memory"
                )
            field_type = validate_concrete_type(value.__args__[0])
            match field_info.storage:
                case _StorageType.IMPORTED:
                    cls._imported_fields_[name] = _ArchetypeField(
                        name, field_info.name or name, field_info.storage, imported_offset, field_type
                    )
                    imported_offset += field_type._size_()
                    if imported_offset > _ENTITY_DATA_SIZE:
                        raise ValueError("Imported fields exceed entity data size")
                    setattr(cls, name, cls._imported_fields_[name])
                case _StorageType.EXPORTED:
                    cls._exported_fields_[name] = _ArchetypeField(
                        name, field_info.name or name, field_info.storage, exported_offset, field_type
                    )
                    exported_offset += field_type._size_()
                    if exported_offset > _ENTITY_DATA_SIZE:
                        raise ValueError("Exported fields exceed entity data size")
                    setattr(cls, name, cls._exported_fields_[name])
                case _StorageType.MEMORY:
                    cls._memory_fields_[name] = _ArchetypeField(
                        name, field_info.name or name, field_info.storage, memory_offset, field_type
                    )
                    memory_offset += field_type._size_()
                    if memory_offset > _ENTITY_MEMORY_SIZE:
                        raise ValueError("Memory fields exceed entity memory size")
                    setattr(cls, name, cls._memory_fields_[name])
                case _StorageType.SHARED:
                    cls._shared_memory_fields_[name] = _ArchetypeField(
                        name, field_info.name or name, field_info.storage, shared_memory_offset, field_type
                    )
                    shared_memory_offset += field_type._size_()
                    if shared_memory_offset > _ENTITY_SHARED_MEMORY_SIZE:
                        raise ValueError("Shared memory fields exceed entity shared memory size")
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
        cls._post_init_fields()

    @property
    @abstractmethod
    def index(self) -> int:
        """The index of this entity."""
        raise NotImplementedError

    @meta_fn
    def ref(self) -> EntityRef[Self]:
        """Get a reference to this entity.

        Valid both in level data and in callbacks.
        """
        match self._data_:
            case _ArchetypeSelfData():
                return EntityRef[type(self)](index=self.index)
            case _ArchetypeReferenceData(index=index):
                return EntityRef[type(self)](index=index)
            case _ArchetypeLevelData():
                result = EntityRef[type(self)](index=-1)
                result._ref_ = self
                return result
            case _:
                raise RuntimeError("Invalid entity data")

    @classmethod
    def _post_init_fields(cls):
        pass


class PlayArchetype(_BaseArchetype):
    """Base class for play mode archetypes.

    Usage:
        ```python
        class MyArchetype(PlayArchetype):
            # Set to True if the entity is a note and contributes to combo and score
            # Default is False
            is_scored: bool = True

            imported_field: int = imported()
            exported_field: int = exported()
            entity_memory_field: int = entity_memory()
            shared_memory_field: int = shared_memory()

            @callback(order=1)
            def preprocess(self):
                ...
        ```
    """

    _removable_prefix: ClassVar[str] = "Play"

    _supported_callbacks_ = PLAY_CALLBACKS

    is_scored: ClassVar[bool] = False
    """Whether the entity contributes to combo and score."""

    def preprocess(self):
        """Perform upfront processing.

        Runs first when the level is loaded.
        """

    def spawn_order(self) -> float:
        """Return the spawn order of the entity.

        Runs when the level is loaded after `preprocess`.
        """

    def should_spawn(self) -> bool:
        """Return whether the entity should be spawned.

        Runs each frame while the entity is the first entity in the spawn queue.
        """

    def initialize(self):
        """Initialize this entity.

        Runs when this entity is spawned.
        """

    def update_sequential(self):
        """Perform non-parallel actions for this frame.

        Runs first each frame.

        This is where logic affecting shared memory should be placed.
        Other logic should be placed in `update_parallel` for better performance.
        """

    def update_parallel(self):
        """Perform parallel actions for this frame.

        Runs after `touch` each frame.

        This is where most gameplay logic should be placed.
        """

    def touch(self):
        """Handle user input.

        Runs after `update_sequential` each frame.
        """

    def terminate(self):
        """Finalize before despawning.

        Runs when the entity is despawned.
        """

    @property
    @meta_fn
    def despawn(self):
        """Whether the entity should be despawned after this frame.

        Setting this to True will despawn the entity.
        """
        if not ctx():
            raise RuntimeError("Calling despawn is only allowed within a callback")
        match self._data_:
            case _ArchetypeSelfData():
                return _deref(ctx().blocks.EntityDespawn, 0, Num)
            case _:
                raise RuntimeError("Despawn is only accessible from the entity itself")

    @despawn.setter
    @meta_fn
    def despawn(self, value: bool):
        if not ctx():
            raise RuntimeError("Calling despawn is only allowed within a callback")
        match self._data_:
            case _ArchetypeSelfData():
                _deref(ctx().blocks.EntityDespawn, 0, Num)._set_(value)
            case _:
                raise RuntimeError("Despawn is only accessible from the entity itself")

    @property
    @meta_fn
    def _info(self):
        if not ctx():
            raise RuntimeError("Calling info is only allowed within a callback")
        match self._data_:
            case _ArchetypeSelfData():
                return _deref(ctx().blocks.EntityInfo, 0, PlayEntityInfo)
            case _ArchetypeReferenceData(index=index):
                return _deref(ctx().blocks.EntityInfoArray, index * PlayEntityInfo._size_(), PlayEntityInfo)
            case _:
                raise RuntimeError("Info is only accessible from the entity itself")

    @property
    def index(self) -> int:
        """The index of this entity."""
        return self._info.index

    @property
    def is_waiting(self) -> bool:
        """Whether this entity is waiting to be spawned."""
        return self._info.state == 0

    @property
    def is_active(self) -> bool:
        """Whether this entity is active."""
        return self._info.state == 1

    @property
    def is_despawned(self) -> bool:
        """Whether this entity is despawned."""
        return self._info.state == 2

    @property
    @meta_fn
    def life(self) -> ArchetypeLife:
        """How this entity contributes to life."""
        if not ctx():
            raise RuntimeError("Calling life is only allowed within a callback")
        match self._data_:
            case _ArchetypeSelfData() | _ArchetypeReferenceData():
                return _deref(ctx().blocks.ArchetypeLife, self.id() * ArchetypeLife._size_(), ArchetypeLife)
            case _:
                raise RuntimeError("Life is not available in level data")

    @property
    @meta_fn
    def result(self) -> PlayEntityInput:
        """The result of this entity.

        Only meaningful for scored entities.
        """
        if not ctx():
            raise RuntimeError("Calling result is only allowed within a callback")
        match self._data_:
            case _ArchetypeSelfData():
                return _deref(ctx().blocks.EntityInput, 0, PlayEntityInput)
            case _:
                raise RuntimeError("Result is only accessible from the entity itself")


class WatchArchetype(_BaseArchetype):
    """Base class for watch mode archetypes.

    Usage:
        ```python
        class MyArchetype(WatchArchetype):
            imported_field: int = imported()
            entity_memory_field: int = entity_memory()
            shared_memory_field: int = shared_memory()

            @callback(order=1)
            def update_sequential(self):
                ...
        ```
    """

    _removable_prefix: ClassVar[str] = "Watch"

    _supported_callbacks_ = WATCH_ARCHETYPE_CALLBACKS

    def preprocess(self):
        """Perform upfront processing.

        Runs first when the level is loaded.
        """

    def spawn_time(self) -> float:
        """Return the spawn time of the entity."""

    def despawn_time(self) -> float:
        """Return the despawn time of the entity."""

    def initialize(self):
        """Initialize this entity.

        Runs when this entity is spawned.
        """

    def update_sequential(self):
        """Perform non-parallel actions for this frame.

        Runs first each frame.

        This is where logic affecting shared memory should be placed.
        Other logic should be placed in `update_parallel` for better performance.
        """

    def update_parallel(self):
        """Parallel update callback.

        Runs after `touch` each frame.

        This is where most gameplay logic should be placed.
        """

    def terminate(self):
        """Finalize before despawning.

        Runs when the entity is despawned.
        """

    @property
    @meta_fn
    def _info(self):
        if not ctx():
            raise RuntimeError("Calling info is only allowed within a callback")
        match self._data_:
            case _ArchetypeSelfData():
                return _deref(ctx().blocks.EntityInfo, 0, WatchEntityInfo)
            case _ArchetypeReferenceData(index=index):
                return _deref(ctx().blocks.EntityInfoArray, index * WatchEntityInfo._size_(), WatchEntityInfo)
            case _:
                raise RuntimeError("Info is only accessible from the entity itself")

    @property
    def index(self) -> int:
        """The index of this entity."""
        return self._info.index

    @property
    def is_active(self) -> bool:
        """Whether this entity is active."""
        return self._info.state == 1

    @property
    @meta_fn
    def life(self) -> ArchetypeLife:
        """How this entity contributes to life."""
        if not ctx():
            raise RuntimeError("Calling life is only allowed within a callback")
        match self._data_:
            case _ArchetypeSelfData() | _ArchetypeReferenceData():
                return _deref(ctx().blocks.ArchetypeLife, self.id() * ArchetypeLife._size_(), ArchetypeLife)
            case _:
                raise RuntimeError("Life is not available in level data")

    @property
    @meta_fn
    def result(self) -> WatchEntityInput:
        """The result of this entity.

        Only meaningful for scored entities.
        """
        if not ctx():
            raise RuntimeError("Calling result is only allowed within a callback")
        match self._data_:
            case _ArchetypeSelfData():
                return _deref(ctx().blocks.EntityInput, 0, WatchEntityInput)
            case _:
                raise RuntimeError("Result is only accessible from the entity itself")

    @classmethod
    def _post_init_fields(cls):
        if cls._exported_fields_:
            raise RuntimeError("Watch archetypes cannot have exported fields")


class PreviewArchetype(_BaseArchetype):
    """Base class for preview mode archetypes.

    Usage:
        ```python
        class MyArchetype(PreviewArchetype):
            imported_field: int = imported()
            entity_memory_field: int = entity_memory()
            shared_memory_field: int = shared_memory()

            @callback(order=1)
            def preprocess(self):
                ...
        ```
    """

    _removable_prefix: ClassVar[str] = "Preview"

    _supported_callbacks_ = PREVIEW_CALLBACKS

    def preprocess(self):
        """Perform upfront processing.

        Runs first when the level is loaded.
        """

    def render(self):
        """Render the entity.

        Runs after `preprocess`.
        """

    @property
    @meta_fn
    def _info(self) -> PreviewEntityInfo:
        if not ctx():
            raise RuntimeError("Calling info is only allowed within a callback")
        match self._data_:
            case _ArchetypeSelfData():
                return _deref(ctx().blocks.EntityInfo, 0, PreviewEntityInfo)
            case _ArchetypeReferenceData(index=index):
                return _deref(ctx().blocks.EntityInfoArray, index * PreviewEntityInfo._size_(), PreviewEntityInfo)
            case _:
                raise RuntimeError("Info is only accessible from the entity itself")

    @property
    def index(self) -> int:
        """The index of this entity."""
        return self._info.index

    @classmethod
    def _post_init_fields(cls):
        if cls._exported_fields_:
            raise RuntimeError("Preview archetypes cannot have exported fields")


@meta_fn
def entity_info_at(index: Num) -> PlayEntityInfo | WatchEntityInfo | PreviewEntityInfo:
    """Retrieve entity info of the entity at the given index.

    Available in play, watch, and preview mode.
    """
    if not ctx():
        raise RuntimeError("Calling entity_info_at is only allowed within a callback")
    match ctx().global_state.mode:
        case Mode.PLAY:
            return _deref(ctx().blocks.EntityInfoArray, index * PlayEntityInfo._size_(), PlayEntityInfo)
        case Mode.WATCH:
            return _deref(ctx().blocks.EntityInfoArray, index * WatchEntityInfo._size_(), WatchEntityInfo)
        case Mode.PREVIEW:
            return _deref(ctx().blocks.EntityInfoArray, index * PreviewEntityInfo._size_(), PreviewEntityInfo)
        case _:
            raise RuntimeError(f"Entity info is not available in mode '{ctx().global_state.mode}'")


@meta_fn
def archetype_life_of(archetype: type[_BaseArchetype] | _BaseArchetype) -> ArchetypeLife:
    """Retrieve the archetype life of the given archetype.

    Available in play and watch mode.
    """
    archetype = validate_value(archetype)
    archetype = archetype._as_py_()
    if not ctx():
        raise RuntimeError("Calling archetype_life_of is only allowed within a callback")
    match ctx().global_state.mode:
        case Mode.PLAY | Mode.WATCH:
            return _deref(ctx().blocks.ArchetypeLife, archetype.id() * ArchetypeLife._size_(), ArchetypeLife)
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
    """How an entity contributes to life."""

    perfect_increment: Num
    """Life increment for a perfect judgment."""

    great_increment: Num
    """Life increment for a great judgment."""

    good_increment: Num
    """Life increment for a good judgment."""

    miss_increment: Num
    """Life increment for a miss judgment."""

    def update(
        self,
        perfect_increment: Num | None = None,
        great_increment: Num | None = None,
        good_increment: Num | None = None,
        miss_increment: Num | None = None,
    ):
        """Update the life increments."""
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


class EntityRef[A: _BaseArchetype](Record):
    """Reference to another entity.

    May be used with `Any` to reference an unknown archetype.

    Usage:
        ```python
        class MyArchetype(PlayArchetype):
            ref_1: EntityRef[OtherArchetype] = imported()
            ref_2: EntityRef[Any] = imported()
        ```
    """

    index: int

    @classmethod
    def archetype(cls) -> type[A]:
        """Get the archetype type."""
        return cls.type_var_value(A)

    def with_archetype(self, archetype: type[A]) -> EntityRef[A]:
        """Return a new reference with the given archetype type."""
        return EntityRef[archetype](index=self.index)

    @meta_fn
    def get(self) -> A:
        """Get the entity."""
        if ref := getattr(self, "_ref_", None):
            return ref
        return self.archetype().at(self.index)

    @meta_fn
    def get_as(self, archetype: type[_BaseArchetype]) -> _BaseArchetype:
        """Get the entity as the given archetype type."""
        if getattr(archetype, "_ref_", None):
            raise TypeError("Using get_as in level data is not supported.")
        return self.with_archetype(archetype).get()

    def archetype_matches(self) -> bool:
        """Check if entity at the index is precisely of the archetype."""
        return self.index >= 0 and self.archetype().is_at(self.index)

    def _to_list_(self, level_refs: dict[Any, str] | None = None) -> list[DataValue | str]:
        ref = getattr(self, "_ref_", None)
        if ref is None:
            return Num._accept_(self.index)._to_list_()
        else:
            if ref not in level_refs:
                raise KeyError("Reference to entity not in level data")
            return [level_refs[ref]]

    def _copy_from_(self, value: Self):
        super()._copy_from_(value)
        if hasattr(value, "_ref_"):
            self._ref_ = value._ref_

    @classmethod
    def _accepts_(cls, value: Any) -> bool:
        return (
            super()._accepts_(value)
            or (cls._type_args_ and cls.archetype() is Any and isinstance(value, EntityRef))
            or (issubclass(type(value), EntityRef) and issubclass(value.archetype(), cls.archetype()))
        )

    @classmethod
    def _accept_(cls, value: Any) -> Self:
        if not cls._accepts_(value):
            raise TypeError(f"Expected {cls}, got {type(value)}")
        result = value.with_archetype(cls.archetype())
        if hasattr(value, "_ref_"):
            result._ref_ = value._ref_
        return result


class StandardArchetypeName(StrEnum):
    """Standard archetype names."""

    BPM_CHANGE = "#BPM_CHANGE"
    """Bpm change marker"""

    TIMESCALE_CHANGE = "#TIMESCALE_CHANGE"
    """Timescale change marker"""
