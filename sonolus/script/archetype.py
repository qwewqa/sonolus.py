from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, StrEnum
from types import FunctionType
from typing import Annotated, Any, ClassVar, Self, TypedDict, get_origin

from sonolus.backend.ir import IRConst, IRInstr
from sonolus.backend.mode import Mode
from sonolus.backend.ops import Op
from sonolus.backend.place import BlockPlace
from sonolus.script.bucket import Bucket, Judgment
from sonolus.script.internal.callbacks import PLAY_CALLBACKS, PREVIEW_CALLBACKS, WATCH_ARCHETYPE_CALLBACKS, CallbackInfo
from sonolus.script.internal.context import ctx
from sonolus.script.internal.descriptor import SonolusDescriptor
from sonolus.script.internal.generic import validate_concrete_type
from sonolus.script.internal.impl import meta_fn, validate_value
from sonolus.script.internal.introspection import get_field_specifiers
from sonolus.script.internal.native import native_call
from sonolus.script.internal.value import Value
from sonolus.script.num import Num
from sonolus.script.pointer import _deref
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
                            ctx().blocks.EntityDataArray, self.offset + index * _ENTITY_DATA_SIZE, self.type
                        )
                    case _ArchetypeLevelData(values=values):
                        result = values[self.name]
            case _StorageType.EXPORTED:
                raise RuntimeError("Exported fields are write-only")
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
                            ctx().blocks.EntityDataArray, self.offset + index * _ENTITY_DATA_SIZE, self.type
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
                            ctx().add_statements(IRInstr(Op.ExportValue, [IRConst(index), Num._accept_(v).ir()]))
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

    Imported fields may be loaded from the level data.

    In watch mode, data may also be loaded from a corresponding exported field in play mode.

    Imported fields may only be updated in the `preprocess` callback, and are read-only in other callbacks.

    Usage:
        ```
        class MyArchetype(PlayArchetype):
            field: int = imported()
            field_with_explicit_name: int = imported(name="field_name")
        ```
    """
    return _ArchetypeFieldInfo(name, _StorageType.IMPORTED)


def exported(*, name: str | None = None) -> Any:
    """Declare a field as exported.

    This is only usable in play mode to export data to be loaded in watch mode.

    Exported fields are write-only.

    Usage:
        ```
        class MyArchetype(PlayArchetype):
            field: int = exported()
            field_with_explicit_name: int = exported(name="#FIELD")
        ```
    """
    return _ArchetypeFieldInfo(name, _StorageType.EXPORTED)


def entity_memory() -> Any:
    """Declare a field as entity memory.

    Entity memory is private to the entity and is not accessible from other entities.

    Entity memory fields may also be set when an entity is spawned using the `spawn()` method.

    Usage:
        ```
        class MyArchetype(PlayArchetype):
            field: int = entity_memory()

        ```
    """
    return _ArchetypeFieldInfo(None, _StorageType.MEMORY)


def shared_memory() -> Any:
    """Declare a field as shared memory.

    Shared memory is accessible from other entities.

    Shared memory may only be updated by sequential callbacks such as `preprocess`, `update_sequential`, and `touch`.

    Usage:
        ```
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
        ```
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
        ```
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
        return object.__new__(cls)

    @classmethod
    def _for_compilation(cls):
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
            ```
            class MyArchetype(PlayArchetype):
                field: int = entity_memory()

            def f():
                MyArchetype.spawn(field=123)
            ```

        Args:
            **kwargs: Entity memory values to inject by field name as defined in the Archetype.
        """
        if not ctx():
            raise RuntimeError("Spawn is only allowed within a callback")
        archetype_id = cls.id()
        bound = cls._spawn_signature_.bind_partial(**kwargs)
        bound.apply_defaults()
        data = []
        for field in cls._memory_fields_.values():
            data.extend(field.type._accept_(bound.arguments[field.name] or zeros(field.type))._to_list_())
        native_call(Op.Spawn, archetype_id, *(Num(x) for x in data))

    @classmethod
    def schema(cls) -> ArchetypeSchema:
        return {"name": cls.name or "unnamed", "fields": list(cls._imported_fields_)}

    def _level_data_entries(self, level_refs: dict[Any, int] | None = None):
        if not isinstance(self._data_, _ArchetypeLevelData):
            raise RuntimeError("Entity is not level data")
        entries = []
        for name, value in self._data_.values.items():
            field_info = self._imported_fields_.get(name)
            for k, v in value._to_flat_dict_(field_info.data_name, level_refs).items():
                entries.append({"name": k, "value": v})
        return entries

    def __init_subclass__(cls, **kwargs):
        if cls.__module__ == _BaseArchetype.__module__:
            if cls._supported_callbacks_ is None:
                raise TypeError("Cannot directly subclass Archetype, use the Archetype subclass for your mode")
            cls._default_callbacks_ = {getattr(cls, cb_info.py_name) for cb_info in cls._supported_callbacks_.values()}
            return
        if getattr(cls, "_callbacks_", None) is not None:
            raise TypeError("Cannot subclass Archetypes")
        if cls.name is None:
            cls.name = cls.__name__
        field_specifiers = get_field_specifiers(cls, skip={"name", "is_scored"}).items()
        cls._imported_fields_ = {}
        cls._exported_fields_ = {}
        cls._memory_fields_ = {}
        cls._shared_memory_fields_ = {}
        imported_offset = 0
        exported_offset = 0
        memory_offset = 0
        shared_memory_offset = 0
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
                case _StorageType.IMPORTED:
                    cls._imported_fields_[name] = _ArchetypeField(
                        name, field_info.name or name, field_info.storage, imported_offset, field_type
                    )
                    imported_offset += field_type._size_()
                    setattr(cls, name, cls._imported_fields_[name])
                case _StorageType.EXPORTED:
                    cls._exported_fields_[name] = _ArchetypeField(
                        name, field_info.name or name, field_info.storage, exported_offset, field_type
                    )
                    exported_offset += field_type._size_()
                    setattr(cls, name, cls._exported_fields_[name])
                case _StorageType.MEMORY:
                    cls._memory_fields_[name] = _ArchetypeField(
                        name, field_info.name or name, field_info.storage, memory_offset, field_type
                    )
                    memory_offset += field_type._size_()
                    setattr(cls, name, cls._memory_fields_[name])
                case _StorageType.SHARED:
                    cls._shared_memory_fields_[name] = _ArchetypeField(
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


class PlayArchetype(_BaseArchetype):
    """Base class for play mode archetypes.

    Usage:
        ```
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

        Runs when this entity is first in the spawn queue.
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

    def ref(self):
        """Get a reference to this entity for creating level data.

        Not valid elsewhere.
        """
        if not isinstance(self._data_, _ArchetypeLevelData):
            raise RuntimeError("Entity is not level data")
        result = EntityRef[type(self)](index=-1)
        result._ref_ = self
        return result


class WatchArchetype(_BaseArchetype):
    """Base class for watch mode archetypes.

    Usage:
        ```
        class MyArchetype(WatchArchetype):
            imported_field: int = imported()
            entity_memory_field: int = entity_memory()
            shared_memory_field: int = shared_memory()

            @callback(order=1)
            def update_sequential(self):
                ...
        ```
    """

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
                return _deref(ctx().blocks.EntityInfoArray, index * WatchEntityInfo._size_(), PlayEntityInfo)
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

    @property
    def target_time(self) -> float:
        """The target time of this entity.

        Only meaningful for scored entities. Determines when combo and score are updated.

        Alias of `result.target_time`.
        """
        return self.result.target_time

    @target_time.setter
    def target_time(self, value: float):
        self.result.target_time = value


class PreviewArchetype(_BaseArchetype):
    """Base class for preview mode archetypes.

    Usage:
        ```
        class MyArchetype(PreviewArchetype):
            imported_field: int = imported()
            entity_memory_field: int = entity_memory()
            shared_memory_field: int = shared_memory()

            @callback(order=1)
            def preprocess(self):
                ...
        ```
    """

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
    """Reference to another entity."""

    index: int

    @classmethod
    def archetype(cls) -> type[A]:
        return cls.type_var_value(A)

    def get(self) -> A:
        return self.archetype().at(self.index)

    def _to_list_(self, level_refs: dict[Any, int] | None = None) -> list[float | BlockPlace]:
        ref = getattr(self, "_ref_", None)
        if ref is None:
            return [self.index]
        else:
            if ref not in level_refs:
                raise KeyError("Reference to entity not in level data")
            return [level_refs[ref]]


class StandardArchetypeName(StrEnum):
    """Standard archetype names."""

    BPM_CHANGE = "#BPM_CHANGE"
    """Bpm change marker"""

    TIMESCALE_CHANGE = "#TIMESCALE_CHANGE"
    """Timescale change marker"""
