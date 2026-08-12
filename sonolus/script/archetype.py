from __future__ import annotations

import inspect
from abc import ABCMeta, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum, IntEnum, StrEnum
from types import FunctionType
from typing import Annotated, Any, ClassVar, NamedTuple, Self, TypedDict, get_origin

from sonolus.backend.ir import IRConst, IRExpr, IRInstr, IRPureInstr
from sonolus.backend.mode import Mode
from sonolus.backend.ops import Op
from sonolus.script.bucket import Bucket, Judgment
from sonolus.script.debug import runtime_checks_enabled, static_error
from sonolus.script.internal.builtin_impls import _type_name
from sonolus.script.internal.callbacks import PLAY_CALLBACKS, PREVIEW_CALLBACKS, WATCH_ARCHETYPE_CALLBACKS, CallbackInfo
from sonolus.script.internal.context import ctx
from sonolus.script.internal.descriptor import SonolusDescriptor
from sonolus.script.internal.generic import validate_concrete_type
from sonolus.script.internal.impl import bind_arguments, validate_value
from sonolus.script.internal.introspection import get_field_specifiers
from sonolus.script.internal.meta_fn import meta_fn
from sonolus.script.internal.native import native_call, native_switch_membership
from sonolus.script.internal.value import BackingValue, DataValue, Value
from sonolus.script.internal.visitor import compile_and_call
from sonolus.script.num import Num
from sonolus.script.pointer import _backing_deref, _deref
from sonolus.script.record import Record
from sonolus.script.timing import TimescaleEase
from sonolus.script.values import zeros

_ENTITY_MEMORY_SIZE = 64
_ENTITY_DATA_SIZE = 32
_ENTITY_SHARED_MEMORY_SIZE = 32


class _StorageType(Enum):
    IMPORTED = "imported"
    DATA = "data"
    EXPORTED = "exported"
    MEMORY = "memory"
    SHARED = "shared_memory"


@dataclass
class _ArchetypeFieldInfo(SonolusDescriptor):
    name: str | None
    storage: _StorageType
    default: Value | None = None

    def __get__(self, instance, owner):
        return self

    def __set__(self, instance, value):
        raise TypeError("Archetype fields cannot be set before the archetype's fields are initialized")


class _ExportBackingValue(BackingValue):
    def __init__(self, index: IRExpr):
        self.index = index

    def read(self) -> IRExpr:
        raise NotImplementedError("Exported fields are write-only")

    def write(self, value: IRExpr) -> None:
        ctx().add_statement(IRInstr(Op.ExportValue, [self.index, value]))


class _ArchetypeField(SonolusDescriptor):
    def __init__(
        self,
        name: str,
        data_name: str,
        storage: _StorageType,
        offset: int,
        type_: type[Value],
        default: Value | None = None,
    ):
        self.name = name
        self.data_name = data_name  # name used in level data
        self.storage = storage
        self.offset = offset
        self.type = type_
        self.default = default

    def __get__(self, instance: _BaseArchetype, owner):
        if instance is None:
            return self
        result = None
        match self.storage:
            case _StorageType.IMPORTED | _StorageType.DATA:
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
                        if self.storage is _StorageType.DATA:
                            raise RuntimeError("Entity data fields are not available in level data")
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
            return result._get_readonly_()
        else:
            return result._as_py_()  # type: ignore

    def __set__(self, instance: _BaseArchetype, value):
        if instance is None:
            raise RuntimeError("Cannot set field on class")
        if not self.type._accepts_(value):
            raise TypeError(f"Expected {self.type}, got {type(value)}")
        target = None
        match self.storage:
            case _StorageType.IMPORTED | _StorageType.DATA:
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
                        if self.storage is _StorageType.DATA:
                            raise RuntimeError("Entity data fields are not available in level data")
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


class _NameDescriptor(SonolusDescriptor):
    def __init__(self, name: str):
        self.name = name

    def __get__(self, instance, owner):
        if instance is None:
            return self.name
        elif ctx():
            raise RuntimeError("Cannot access archetype name in from self in a callback, use ArchetypeClass.name")
        else:
            return self.name

    def __set__(self, instance, value):
        raise AttributeError("Archetype name is read-only and cannot be set")


class _IsScoredDescriptor(SonolusDescriptor):
    def __init__(self, value: bool):
        self.value = value

    def __get__(self, instance, owner):
        if instance is None:
            return self.value
        elif ctx():
            return ctx().mode_state.is_scored_by_archetype_id.get_unchecked(instance.id)
        else:
            return self.value

    def __set__(self, instance, value):
        raise AttributeError("is_scored is read-only and cannot be set")


class _IdDescriptor(SonolusDescriptor):
    def __get__(self, instance, owner):
        if not ctx():
            raise RuntimeError("Archetype id is only available during compilation")
        if instance is None:
            result = ctx().mode_state.archetypes.get(owner)
            if result is None or owner in ctx().mode_state.compile_time_only_archetypes:
                raise RuntimeError("Archetype is not registered")
            return result
        else:
            return instance._info.archetype_id

    def __set__(self, instance, value):
        raise AttributeError("Archetype id is read-only and cannot be set")


class _KeyDescriptor(SonolusDescriptor):
    def __init__(self, value: int | float):
        self.value = value

    def __get__(self, instance, owner):
        if instance is not None and ctx():
            return ctx().mode_state.keys_by_archetype_id.get_unchecked(instance.id)
        else:
            return self.value

    def __set__(self, instance, value):
        raise AttributeError("Archetype key is read-only and cannot be set")


class _ArchetypeLifeDescriptor(SonolusDescriptor):
    def __get__(self, instance, owner):
        if not ctx():
            raise RuntimeError("Archetype life is only available during compilation")
        if ctx().mode_state.mode not in {Mode.PLAY, Mode.WATCH}:
            raise RuntimeError(f"Archetype life is not available in mode '{ctx().mode_state.mode.name}'")
        if instance is not None:
            return _deref(ctx().blocks.ArchetypeLife, instance.id * LifeInfo._size_(), LifeInfo)
        else:
            return _deref(ctx().blocks.ArchetypeLife, owner.id * LifeInfo._size_(), LifeInfo)

    def __set__(self, instance, value):
        raise AttributeError("Archetype life is read-only and cannot be set")


class _EntityLifeDescriptor(SonolusDescriptor):
    def __get__(self, instance, owner):
        if not ctx():
            raise RuntimeError("Entity life is only available during compilation")
        if ctx().mode_state.mode not in {Mode.PLAY, Mode.WATCH}:
            raise RuntimeError(f"Entity life is not available in mode '{ctx().mode_state.mode.name}'")
        if instance is None:
            raise RuntimeError("Entity life can only be accessed from an instance")
        match instance._data_:
            case _ArchetypeSelfData():
                return _deref(ctx().blocks.EntityLife, 0, LifeInfo)
            case _ArchetypeReferenceData(index=index):
                return _deref(ctx().blocks.EntityLifeArray, index * LifeInfo._size_(), LifeInfo)
            case _:
                raise RuntimeError("Entity life is not available in level data")

    def __set__(self, instance, value):
        raise AttributeError("Entity life is read-only and cannot be set")


class _ArchetypeScoreMultiplierMetaDescriptor(SonolusDescriptor):
    def __get__(self, instance, owner):
        if instance is None:
            return self
        if not ctx():
            raise RuntimeError("Archetype score multiplier is only available during compilation")
        if ctx().mode_state.mode not in {Mode.PLAY, Mode.WATCH}:
            raise RuntimeError(f"Archetype score multiplier is not available in mode '{ctx().mode_state.mode.name}'")
        return _deref(ctx().blocks.ArchetypeScore, instance.id, Num)

    def __set__(self, instance, value):
        if not ctx():
            raise RuntimeError("Archetype score multiplier is only available during compilation")
        if ctx().mode_state.mode not in {Mode.PLAY, Mode.WATCH}:
            raise RuntimeError(f"Archetype score multiplier is not available in mode '{ctx().mode_state.mode.name}'")
        target = _deref(ctx().blocks.ArchetypeScore, instance.id, Num)
        target._set_(Num._accept_(value))


class _ArchetypeScoreMultiplierDescriptor(SonolusDescriptor):
    def __get__(self, instance, owner):
        if not ctx():
            raise RuntimeError("Archetype score multiplier is only available during compilation")
        if ctx().mode_state.mode not in {Mode.PLAY, Mode.WATCH}:
            raise RuntimeError(f"Archetype score multiplier is not available in mode '{ctx().mode_state.mode.name}'")
        if instance is not None:
            return _deref(ctx().blocks.ArchetypeScore, instance.id, Num)
        else:
            return _deref(ctx().blocks.ArchetypeScore, owner.id, Num)

    def __set__(self, instance, value):
        if instance is None:
            raise RuntimeError("Cannot set archetype score multiplier on None instance")
        if not ctx():
            raise RuntimeError("Archetype score multiplier is only available during compilation")
        if ctx().mode_state.mode not in {Mode.PLAY, Mode.WATCH}:
            raise RuntimeError(f"Archetype score multiplier is not available in mode '{ctx().mode_state.mode.name}'")
        target = _deref(ctx().blocks.ArchetypeScore, instance.id, Num)
        target._set_(Num._accept_(value))


class _EntityScoreMultiplierMetaDescriptor(SonolusDescriptor):
    def __get__(self, instance, owner):
        if instance is None:
            return self
        raise RuntimeError("Entity score multiplier can only be accessed from an instance")

    def __set__(self, instance, value):
        raise RuntimeError("Entity score multiplier can only be set on an instance, not on the class")


class _EntityScoreMultiplierDescriptor(SonolusDescriptor):
    def __get__(self, instance, owner):
        if not ctx():
            raise RuntimeError("Entity score multiplier is only available during compilation")
        if ctx().mode_state.mode not in {Mode.PLAY, Mode.WATCH}:
            raise RuntimeError(f"Entity score multiplier is not available in mode '{ctx().mode_state.mode.name}'")
        if instance is None:
            raise RuntimeError("Entity score multiplier can only be accessed from an instance")
        match instance._data_:
            case _ArchetypeSelfData():
                return _deref(ctx().blocks.EntityScore, 0, Num)
            case _ArchetypeReferenceData(index=index):
                return _deref(ctx().blocks.EntityScoreArray, index, Num)
            case _:
                raise RuntimeError("Entity score multiplier is not available in level data")

    def __set__(self, instance, value):
        if instance is None:
            raise RuntimeError("Entity score multiplier can only be set on an instance")
        if not ctx():
            raise RuntimeError("Entity score multiplier is only available during compilation")
        if ctx().mode_state.mode not in {Mode.PLAY, Mode.WATCH}:
            raise RuntimeError(f"Entity score multiplier is not available in mode '{ctx().mode_state.mode.name}'")
        target = None
        match instance._data_:
            case _ArchetypeSelfData():
                target = _deref(ctx().blocks.EntityScore, 0, Num)
            case _ArchetypeReferenceData(index=index):
                target = _deref(ctx().blocks.EntityScoreArray, index, Num)
            case _:
                raise RuntimeError("Entity score multiplier is not available in level data")
        target._set_(Num._accept_(value))


def imported(*, name: str | None = None, default: Any = None) -> Any:
    """Declare a field as imported.

    Imported fields may be loaded from the level.

    In watch mode, data may also be loaded from a corresponding exported field in play mode.

    Imported fields may only be updated in the [`preprocess`][sonolus.script.archetype.PlayArchetype.preprocess]
    callback, and are read-only in other callbacks.

    Usage:
        ```python
        class MyArchetype(PlayArchetype):
            field: int = imported()
            field_with_explicit_name: int = imported(name="field_name")
            field_with_default: int = imported(default=0)
            compound_field_with_default: Vec2 = imported(default=Vec2(0.0, 0.0))
        ```
    """
    validated_default = None
    if default is not None:
        validated_default = validate_value(default)
    return _ArchetypeFieldInfo(name, _StorageType.IMPORTED, validated_default)


def entity_data() -> Any:
    """Declare a field as entity data.

    Entity data is accessible from other entities, but may only be updated in the
    [`preprocess`][sonolus.script.archetype.PlayArchetype.preprocess] callback
    and is read-only in other callbacks.

    Entity data shares storage with [`imported`][sonolus.script.archetype.imported] fields but is private to the
    engine: it is not part of the archetype schema, may not be set when constructing level data, and is never
    loaded from a level.

    Usage:
        ```python
        class MyArchetype(PlayArchetype):
            field: int = entity_data()
        ```
    """
    return _ArchetypeFieldInfo(None, _StorageType.DATA)


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

    Entity memory exists in play and watch mode.

    Entity memory fields may also be set when an entity is spawned using the
    [`spawn()`][sonolus.script.archetype.PlayArchetype.spawn] method.

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
    ([`preprocess`][sonolus.script.archetype.PlayArchetype.preprocess],
    [`update_sequential`][sonolus.script.archetype.PlayArchetype.update_sequential],
    and [`touch`][sonolus.script.archetype.PlayArchetype.touch]).

    Usage:
        ```python
        class MyArchetype(PlayArchetype):
            field: int = shared_memory()
        ```
    """
    return _ArchetypeFieldInfo(None, _StorageType.SHARED)


_annotation_defaults: dict[Callable, _ArchetypeFieldInfo] = {
    imported: imported(),
    entity_data: entity_data(),
    exported: exported(),
    entity_memory: entity_memory(),
    shared_memory: shared_memory(),
}


def callback[T: Callable](*, order: int = 0) -> Callable[[T], T]:
    """Annotate a callback with its order.

    Callbacks are executed from lowest to highest order. By default, callbacks have an order of 0.
    Order is supported by `preprocess`, `spawn_order`, `update_sequential`, `touch`, `spawn_time`, and
    `despawn_time` callbacks. Setting a nonzero order on other callbacks is unsupported.

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
        func._callback_order_ = order  # type: ignore
        return func

    return decorator


class _ArchetypeSelfData:
    pass


class _ArchetypeReferenceData:
    index: int

    def __init__(self, index: int):
        self.index = index


class _ArchetypeLevelData:
    values: dict[str, Value]

    def __init__(self, values: dict[str, Value]):
        self.values = values


type _ArchetypeData = _ArchetypeSelfData | _ArchetypeReferenceData | _ArchetypeLevelData


class ArchetypeSchema(TypedDict):
    """The schema of an archetype, as returned by its `schema()` method."""

    name: str
    """The archetype name."""

    fields: list[str]
    """The flat names of fields supplied by level data."""

    exports: list[str]
    """The flat names of fields exported by the archetype."""


class ImportInfo(NamedTuple):
    index: int
    default: int | float | None


RESERVED_ARCHETYPE_FIELD_NAMES = {
    "id",
    "key",
    "name",
    "life",
    "archetype_life",
    "entity_life",
    "archetype_score_multiplier",
    "entity_score_multiplier",
    "is_scored",
    "_key_",
    "_is_scored_",
    "_derived_base_",
    "_is_derived_",
    "_default_callbacks_",
    "_callbacks_",
    "_field_init_done",
    "_is_concrete_archetype_",
    "_abc_impl",
}

ALLOWED_RESERVED_ARCHETYPE_FIELD_NAME_OVERRIDES = {
    "key",
    "name",
    "is_scored",
}


def _shadowed_member(cls: type, name: str) -> tuple[type, str] | None:
    """The class and kind of member a field named `name` would replace, or None if it would replace nothing."""
    for entry in cls.mro():
        if name not in entry.__dict__:
            continue
        member = entry.__dict__[name]
        if isinstance(member, _ArchetypeFieldInfo):
            continue
        match member:
            case property():
                return entry, "property"
            case FunctionType() | classmethod() | staticmethod():
                return entry, "method"
            case _:
                return None
    return None


def _declaring_class_name(cls: type, field: _ArchetypeField) -> str:
    """The name of the class in cls's mro that declared the field."""
    for entry in cls.mro():
        if entry.__dict__.get(field.name) is field:
            return entry.__name__
    return cls.__name__


def _duplicate_key_error(cls: type, kind: str, key: str, first: _ArchetypeField, second: _ArchetypeField) -> ValueError:
    """The error for two fields of cls resolving to one import or export name."""
    return ValueError(
        f"Fields '{first.name}' of {_declaring_class_name(cls, first)} and "
        f"'{second.name}' of {_declaring_class_name(cls, second)} both use the {kind} name '{key}'"
    )


class _BaseArchetypeMeta(ABCMeta):
    archetype_score_multiplier = _ArchetypeScoreMultiplierMetaDescriptor()
    entity_score_multiplier = _EntityScoreMultiplierMetaDescriptor()

    def __new__(mcs, name, bases, namespace, **kwargs):
        module = namespace.get("__module__", "")
        is_derived = namespace.get("_is_derived_", False)
        if "is_scored" in namespace and type(namespace["is_scored"]) is not bool:
            raise TypeError(f"is_scored of {name} must be a bool, got {type(namespace['is_scored'])}")
        if module != "sonolus.script.archetype" and not is_derived:
            for field_name in namespace:
                if (
                    field_name in RESERVED_ARCHETYPE_FIELD_NAMES
                    and field_name not in ALLOWED_RESERVED_ARCHETYPE_FIELD_NAME_OVERRIDES
                ):
                    raise TypeError(f"Field '{field_name}' in {name} overrides a reserved archetype field name.")

        return super().__new__(mcs, name, bases, namespace, **kwargs)


class _BaseArchetype(metaclass=_BaseArchetypeMeta):
    _is_comptime_value_ = True

    _removable_prefix: ClassVar[str] = ""

    _supported_callbacks_: ClassVar[dict[str, CallbackInfo]]
    _default_callbacks_: ClassVar[set[Callable]]

    _imported_fields_: ClassVar[dict[str, _ArchetypeField]]
    _data_fields_: ClassVar[dict[str, _ArchetypeField]]
    _exported_fields_: ClassVar[dict[str, _ArchetypeField]]
    _memory_fields_: ClassVar[dict[str, _ArchetypeField]]
    _shared_memory_fields_: ClassVar[dict[str, _ArchetypeField]]

    _imported_keys_: ClassVar[dict[str, ImportInfo]]
    _exported_keys_: ClassVar[dict[str, int]]
    _callbacks_: ClassVar[dict[str, Callable]]
    _data_constructor_signature_: ClassVar[inspect.Signature]
    _spawn_signature_: ClassVar[inspect.Signature]

    _data_: _ArchetypeData

    archetype_score_multiplier = _ArchetypeScoreMultiplierDescriptor()
    entity_score_multiplier = _EntityScoreMultiplierDescriptor()

    id: int = 0
    """The id of the archetype or entity.

    If accessed on an entity, always returns the runtime archetype id of the entity, even if it doesn't match the type
    that was used to access it.

    E.g. if an entity of archetype `A` is accessed via [`EntityRef[B]`][sonolus.script.archetype.EntityRef], the id will
    still be the id of `A`.
    """

    key: int | float = -1
    """An optional key for the archetype.

    May be useful to identify an archetype in an inheritance hierarchy without needing to check id.

    If accessed on an entity, always returns the runtime key of the entity, even if it doesn't match the type
    that was used to access it.

    E.g. if an entity of archetype `A` is accessed via [`EntityRef[B]`][sonolus.script.archetype.EntityRef], the key
    will still be the key of `A`.
    """

    name: ClassVar[str | None] = None
    """The name of the archetype.

    If not set, defaults to the class name with the leading `Play`, `Watch`, or `Preview` prefix for this
    archetype's mode removed, if present.

    The name is used in level data.
    """

    is_scored: ClassVar[bool] = False

    def __init__(self, *args, **kwargs):
        self._init_fields()
        if ctx():
            raise RuntimeError("The Archetype constructor is only for defining level data")
        bound = bind_arguments(self._data_constructor_signature_, type(self).__name__, args, kwargs, partial=True)
        bound.apply_defaults()
        values = {
            field.name: field.type._accept_(
                bound.arguments[field.name] if field.name in bound.arguments else zeros(field.type)
            )._get_()
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
    def _compile_time_id(cls):
        if not ctx():
            raise RuntimeError("Archetype id is only available during compilation")
        result = ctx().mode_state.archetypes.get(cls)
        if result is None:
            raise RuntimeError("Archetype is not registered")
        return result

    @classmethod
    @meta_fn
    def at(cls, index: int, check: bool = True) -> Self:
        """Access the entity of this archetype at the given index.

        Args:
            index: The index of the entity to reference.
            check: If true and runtime checks are enabled, asserts that the entity at the index is of this
                   archetype or of a subclass of this archetype. If false, no validation is performed.

        Returns:
            The entity at the given index.
        """
        if not ctx():
            raise RuntimeError("Archetype.at is only available during compilation")
        compile_and_call(cls._check_is_at, index, check=check)
        result = cls._new()
        result._data_ = _ArchetypeReferenceData(index=Num._accept_(index))
        return result

    @classmethod
    def is_at(cls, index: int, strict: bool = False) -> bool:
        """Return whether the entity at the given index is of this archetype.

        Args:
            index: The index of the entity to check.
            strict: If true, only returns true if the entity is exactly of this archetype. If false, also returns true
                    if the entity is of a subclass of this archetype.

        Returns:
            Whether the entity at the given index is of this archetype.
        """
        if strict:
            return index >= 0 and cls._compile_time_id() == entity_info_at(index).archetype_id
        else:
            return index >= 0 and cls._matches_archetype_id(entity_info_at(index).archetype_id)

    @classmethod
    def _check_is_at(cls, index: int, check: bool):
        if not check or not runtime_checks_enabled():
            return
        # If index is invalid, we'll get 0 as the id.
        assert cls._matches_archetype_id(entity_info_at(index).archetype_id), (
            "Entity at index is not of the expected archetype or index is invalid"
        )

    @classmethod
    @meta_fn
    def _matches_archetype_id(cls, archetype_id: int) -> bool:
        """Check whether the given runtime archetype id is this archetype or a subclass of it."""
        if not ctx():
            raise RuntimeError("Archetype._matches_archetype_id is only available during compilation")
        mode_state = ctx().mode_state
        if cls not in mode_state.archetypes:
            raise RuntimeError("Archetype is not registered")
        # The archetype collection is fixed for a ModeContextState, so repeated checks can reuse this sweep.
        subclass_ids = mode_state.subclass_ids_cache.get(cls)
        if subclass_ids is None:
            subclass_ids = [
                id_
                for archetype, id_ in mode_state.archetypes.items()
                if archetype not in mode_state.compile_time_only_archetypes and issubclass(archetype, cls)
            ]
            mode_state.subclass_ids_cache[cls] = subclass_ids
        return native_switch_membership(archetype_id, subclass_ids)

    @staticmethod
    @meta_fn
    def _get_mro_id_array(archetype_id: int) -> Sequence[int]:
        if not ctx():
            raise RuntimeError("Archetype._get_mro_id_array is only available during compilation")
        return ctx().get_archetype_mro_id_array(archetype_id)

    @classmethod
    @meta_fn
    def spawn(cls, **kwargs: Any) -> None:
        """Spawn an entity of this archetype, injecting the given values into entity memory.

        Available in play and watch mode.

        Entity memory fields not passed as keyword arguments are initialized to zero.

        The arguments initialize entity memory only. They do not initialize imported fields or shared memory.

        Usage:
            ```python
            class MyArchetype(PlayArchetype):
                field: int = entity_memory()

            def f():
                MyArchetype.spawn(field=123)
            ```

        Args:
            **kwargs: Entity memory values to inject by field name as defined in the archetype.
        """
        cls._init_fields()
        if not ctx():
            raise RuntimeError("Spawn is only allowed within a callback")
        if ctx().mode_state.mode not in {Mode.PLAY, Mode.WATCH}:
            raise RuntimeError(
                f"{cls.__name__}.spawn is not available in '{ctx().mode_state.mode.name}' mode, only in PLAY, WATCH"
            )
        archetype_id = cls.id
        bound = bind_arguments(cls._spawn_signature_, f"{cls.__name__}.spawn", (), kwargs, partial=True)
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
        return {
            "name": cls.name or "unnamed",
            "fields": list(cls._imported_keys_),
            "exports": list(cls._exported_keys_),
        }

    def _level_data_entries(self, level_refs: dict[Any, str] | None = None):
        self._init_fields()
        if not isinstance(self._data_, _ArchetypeLevelData):
            raise RuntimeError("Entity is not level data")
        entries = []
        for name, value in self._data_.values.items():
            field_info = self._imported_fields_.get(name)
            try:
                flat = value._to_flat_dict_(field_info.data_name, level_refs)
            except ValueError as e:
                raise ValueError(f"field '{name}': {e}") from e
            for k, v in flat.items():
                if isinstance(v, str):
                    entries.append({"name": k, "ref": v})
                else:
                    entries.append({"name": k, "value": v})
        return entries

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        for base in cls.__bases__:
            if getattr(base, "_is_derived_", False):
                origin = getattr(base, "_derived_base_", base)
                raise TypeError(
                    f"Archetype {cls.__name__} cannot subclass {base.__name__}, which was created by "
                    f"{origin.__name__}.derive(). Subclass {origin.__name__} instead, or derive from it again."
                )
        if cls.__module__ == _BaseArchetype.__module__ and not getattr(cls, "_is_derived_", False):
            if cls._supported_callbacks_ is None:
                raise TypeError("Cannot directly subclass Archetype, use the Archetype subclass for your mode")
            cls._default_callbacks_ = {getattr(cls, cb_info.py_name) for cb_info in cls._supported_callbacks_.values()}
            return
        if cls.name is None or cls.name in {getattr(mro_entry, "name", None) for mro_entry in cls.mro()[1:]}:
            cls.name = cls.__name__.removeprefix(cls._removable_prefix)
        cls._callbacks_ = {}
        for name in cls._supported_callbacks_:
            for mro_entry in cls.mro():
                if name in mro_entry.__dict__:
                    cb = mro_entry.__dict__[name]
                    if isinstance(cb, _ArchetypeFieldInfo | _ArchetypeField):
                        break
                    if isinstance(cb, classmethod | staticmethod):
                        raise TypeError(
                            f"Callback '{name}' of {cls.__name__} is declared as a @{type(cb).__name__}. "
                            "An archetype callback must be a plain method taking self."
                        )
                    if cb in cls._default_callbacks_:
                        if mro_entry.__module__ != _BaseArchetype.__module__:
                            break
                        continue
                    cls._callbacks_[name] = cb
                    break
        # Inspect only cls.__dict__ so callback markers on unrelated mixin methods are not rejected.
        registered = [getattr(cb, "__func__", cb) for cb in cls._callbacks_.values()]
        for name, member in cls.__dict__.items():
            if name in cls._supported_callbacks_:
                continue
            target = getattr(member, "__func__", member)
            marked = hasattr(member, "_callback_order_") or hasattr(target, "_callback_order_")
            if not marked or any(target is entry for entry in registered):
                continue
            raise TypeError(
                f"Method '{name}' of {cls.__name__} is decorated with @callback, but it is not a callback of "
                f"this archetype. The callbacks of {cls.__name__} are: {', '.join(cls._supported_callbacks_)}."
            )
        cls._field_init_done = False
        cls._is_concrete_archetype_ = True
        cls.id = _IdDescriptor()
        cls._key_ = cls.key
        cls.key = _KeyDescriptor(cls.key)
        cls.name = _NameDescriptor(cls.name)
        cls._is_scored_ = cls.is_scored
        cls.is_scored = _IsScoredDescriptor(cls.is_scored)
        cls.life = _ArchetypeLifeDescriptor()
        cls.archetype_life = _ArchetypeLifeDescriptor()
        cls.entity_life = _EntityLifeDescriptor()

    @classmethod
    def _init_fields(cls):
        if cls._field_init_done:
            return
        for mro_entry in cls.mro()[1:]:
            if hasattr(mro_entry, "_field_init_done"):
                mro_entry._init_fields()
        if sum(issubclass(base, _BaseArchetype) for base in cls.__bases__) > 1:
            raise TypeError("Multiple inheritance of Archetypes is not supported")
        archetype_parents = [base for base in cls.__bases__ if issubclass(base, _BaseArchetype)]
        mro_from_archetype_parents = {entry for base in archetype_parents for entry in base.mro()}
        # Archetype parents have initialized their fields; process only cls and mixins outside their MROs.
        mro_excluding_archetype_parents = [entry for entry in cls.mro() if entry not in mro_from_archetype_parents]
        try:
            field_specifiers = get_field_specifiers(
                cls,
                skip=RESERVED_ARCHETYPE_FIELD_NAMES,
                included_classes=mro_excluding_archetype_parents,
            ).items()
        except Exception as e:
            raise TypeError(f"Error while processing fields of {cls.__name__}: {e}") from e
        # Everything below accumulates locally and is set on cls only once every check has passed. A build
        # retraces a failing callback, so it calls _init_fields a second time, and bookkeeping or a descriptor
        # left behind by the first call makes that second call fail somewhere unrelated.
        imported_fields = {**getattr(cls, "_imported_fields_", {})}
        data_fields = {**getattr(cls, "_data_fields_", {})}
        exported_fields = {**getattr(cls, "_exported_fields_", {})}
        memory_fields = {**getattr(cls, "_memory_fields_", {})}
        shared_memory_fields = {**getattr(cls, "_shared_memory_fields_", {})}
        descriptors: list[tuple[str, _ArchetypeField]] = []
        entity_data_offset = sum(field.type._size_() for field in (*imported_fields.values(), *data_fields.values()))
        exported_offset = sum(field.type._size_() for field in exported_fields.values())
        memory_offset = sum(field.type._size_() for field in memory_fields.values())
        shared_memory_offset = sum(field.type._size_() for field in shared_memory_fields.values())
        for name, value in field_specifiers:
            if value is ClassVar or get_origin(value) is ClassVar:
                continue
            shadowed = _shadowed_member(cls, name)
            if shadowed is not None:
                shadowed_owner, shadowed_kind = shadowed
                raise TypeError(
                    f"Field '{name}' of {cls.__name__} shadows the "
                    f"'{name}' {shadowed_kind} of {shadowed_owner.__name__}"
                )
            if get_origin(value) is not Annotated:
                raise TypeError(
                    "Archetype fields must be annotated using imported, entity_data, exported, entity_memory, "
                    "or shared_memory"
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
                                f"Unexpected multiple annotations for field '{name}' of {cls.__name__}, "
                                f"expected exactly one of imported, entity_data, exported, entity_memory, "
                                f"or shared_memory"
                            )
                    else:
                        field_info = metadata
            if field_info is None:
                raise TypeError(
                    f"Missing annotation for '{name}' of {cls.__name__}, "
                    f"expected exactly one of imported, entity_data, exported, entity_memory, or shared_memory"
                )
            if (
                name in imported_fields
                or name in data_fields
                or name in exported_fields
                or name in memory_fields
                or name in shared_memory_fields
            ):
                raise ValueError(f"Field '{name}' is already defined in a superclass")
            try:
                field_type = validate_concrete_type(value.__args__[0])
            except Exception as e:
                raise TypeError(f"Error in field '{name}' of {cls.__name__}: {e}") from e
            match field_info.storage:
                case _StorageType.IMPORTED:
                    imported_fields[name] = _ArchetypeField(
                        name,
                        field_info.name or name,
                        field_info.storage,
                        entity_data_offset,
                        field_type,
                        field_info.default,
                    )
                    entity_data_offset += field_type._size_()
                    if entity_data_offset > _ENTITY_DATA_SIZE:
                        raise ValueError("Imported and entity data fields exceed entity data size")
                    descriptors.append((name, imported_fields[name]))
                case _StorageType.DATA:
                    data_fields[name] = _ArchetypeField(
                        name, field_info.name or name, field_info.storage, entity_data_offset, field_type
                    )
                    entity_data_offset += field_type._size_()
                    if entity_data_offset > _ENTITY_DATA_SIZE:
                        raise ValueError("Imported and entity data fields exceed entity data size")
                    descriptors.append((name, data_fields[name]))
                case _StorageType.EXPORTED:
                    exported_fields[name] = _ArchetypeField(
                        name, field_info.name or name, field_info.storage, exported_offset, field_type
                    )
                    exported_offset += field_type._size_()
                    if exported_offset > _ENTITY_DATA_SIZE:
                        raise ValueError("Exported fields exceed entity data size")
                    descriptors.append((name, exported_fields[name]))
                case _StorageType.MEMORY:
                    memory_fields[name] = _ArchetypeField(
                        name, field_info.name or name, field_info.storage, memory_offset, field_type
                    )
                    memory_offset += field_type._size_()
                    if memory_offset > _ENTITY_MEMORY_SIZE:
                        raise ValueError("Memory fields exceed entity memory size")
                    descriptors.append((name, memory_fields[name]))
                case _StorageType.SHARED:
                    shared_memory_fields[name] = _ArchetypeField(
                        name, field_info.name or name, field_info.storage, shared_memory_offset, field_type
                    )
                    shared_memory_offset += field_type._size_()
                    if shared_memory_offset > _ENTITY_SHARED_MEMORY_SIZE:
                        raise ValueError("Shared memory fields exceed entity shared memory size")
                    descriptors.append((name, shared_memory_fields[name]))
        imported_keys = {}
        imported_key_fields: dict[str, _ArchetypeField] = {}
        for field in imported_fields.values():
            keys = list(field.type._flat_keys_(field.data_name))
            # An import's index is the entity data slot the runtime writes its level value to. Entity data
            # fields occupy slots but contribute no keys, so these indexes need not be contiguous.
            defaults = field.default._to_list_() if field.default is not None else [None] * len(keys)
            if len(defaults) != len(keys):
                raise TypeError(
                    f"Field '{field.name}' of {_declaring_class_name(cls, field)} imports "
                    f"{len(keys)} value{'' if len(keys) == 1 else 's'}, but its default has {len(defaults)}"
                )
            if field.default is not None and not field.type._accepts_(field.default):
                raise TypeError(
                    f"Field '{field.name}' of {_declaring_class_name(cls, field)} has type "
                    f"{field.type.__name__}, but its default has type {_type_name(field.default)}"
                )
            for i, (key, default_value) in enumerate(zip(keys, defaults, strict=True)):
                if key in imported_keys:
                    raise _duplicate_key_error(cls, "import", key, imported_key_fields[key], field)
                imported_keys[key] = ImportInfo(index=field.offset + i, default=default_value)
                imported_key_fields[key] = field
        exported_keys = {}
        exported_key_fields: dict[str, _ArchetypeField] = {}
        for field in exported_fields.values():
            for key in field.type._flat_keys_(field.data_name):
                if key in exported_keys:
                    raise _duplicate_key_error(cls, "export", key, exported_key_fields[key], field)
                exported_keys[key] = len(exported_keys)
                exported_key_fields[key] = field
        cls._post_init_fields(exported_fields, memory_fields)
        cls._imported_fields_ = imported_fields
        cls._data_fields_ = data_fields
        cls._exported_fields_ = exported_fields
        cls._memory_fields_ = memory_fields
        cls._shared_memory_fields_ = shared_memory_fields
        for name, field in descriptors:
            setattr(cls, name, field)
        cls._imported_keys_ = imported_keys
        cls._exported_keys_ = exported_keys
        cls._data_constructor_signature_ = inspect.Signature(
            [inspect.Parameter(name, inspect.Parameter.POSITIONAL_OR_KEYWORD) for name in imported_fields]
        )
        cls._spawn_signature_ = inspect.Signature(
            [inspect.Parameter(name, inspect.Parameter.POSITIONAL_OR_KEYWORD) for name in memory_fields]
        )
        cls._field_init_done = True

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
                return EntityRef[type(self)](index=self.index)  # type: ignore
            case _ArchetypeReferenceData(index=index):
                return EntityRef[type(self)](index=index)  # type: ignore
            case _ArchetypeLevelData():
                result = EntityRef[type(self)](index=-1)  # type: ignore
                result._ref_ = self
                return result
            case _:
                raise RuntimeError("Invalid entity data")

    @classmethod
    def _post_init_fields(
        cls,
        exported_fields: dict[str, _ArchetypeField],
        memory_fields: dict[str, _ArchetypeField],
    ):
        """Reject a field combination this mode does not support, once the fields are computed."""

    @classmethod
    def derive(cls: type[Self], name: str, is_scored: bool, key: int | float | None = None) -> type[Self]:
        """Derive a new archetype class from this archetype.

        Roughly equivalent to returning:
        ```python
        class Derived(cls):
            name = <name>
            is_scored = <is_scored>
            key = <key>  # Only set if key is not None
        ```

        This is used to create a new archetype with the same fields and callbacks, but with a different name and
        whether it is scored. Compared to manually subclassing, this method also enables faster compilation when
        the same base archetype has multiple derived archetypes by compiling callbacks only once for the base archetype.

        This cannot be called on an archetype that was itself created by `derive`, and a derived archetype
        cannot be subclassed.

        Args:
            name: The name of the new archetype.
            is_scored: Whether the new archetype is scored.
            key: A key that can be accessed via the `key` property of the new archetype.

        Returns:
            A new archetype class with the same fields and callbacks as this archetype, but with the given name and
            whether it is scored.
        """
        if getattr(cls, "_is_derived_", False):
            raise RuntimeError("Cannot derive from a derived archetype")
        cls_dict = {
            "name": name,
            "is_scored": is_scored,
            "_is_derived_": True,
            "_derived_base_": cls,
            "__module__": cls.__module__,
        }
        if key is not None:
            if not isinstance(key, (int, float)):
                raise TypeError(f"Key must be an int or float, got {type(key)}")
            cls_dict["key"] = key
        new_cls = type(name, (cls,), cls_dict)
        return new_cls

    @meta_fn
    def _delegate(self, name: str, default: int | float | None = None):
        name = validate_value(name)._as_py_()
        if hasattr(super(), name):
            fn = getattr(super(), name)
            if ctx():
                return compile_and_call(fn)
            else:
                return fn()
        else:
            return default


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
    """Whether entities of this archetype contribute to combo and score."""

    life: ClassVar[LifeInfo]
    """How entities of this archetype contribute to life depending on judgment.

    Alias for [`archetype_life`][sonolus.script.archetype.PlayArchetype.archetype_life], provided for backwards
    compatibility.
    """

    archetype_life: ClassVar[LifeInfo]
    """How entities of this archetype contribute to life depending on judgment."""

    entity_life: LifeInfo
    """How this specific entity contributes to life depending on judgment.

    This is additive with [`archetype_life`][sonolus.script.archetype.PlayArchetype.archetype_life] - the total
    life increment is the sum of both.
    """

    archetype_score_multiplier: ClassVar[float]
    """Score multiplier for entities of this archetype.

    This is additive with other multipliers (except judgment base multipliers).
    """

    entity_score_multiplier: float
    """Score multiplier for this specific entity.

    This is additive with other multipliers (except judgment base multipliers).
    """

    def preprocess(self):
        """Perform upfront processing.

        Runs first when the level is loaded.
        """
        self._delegate("preprocess")

    def spawn_order(self) -> float:
        """Return the spawn order of the entity.

        Runs when the level is loaded after [`preprocess`][sonolus.script.archetype.PlayArchetype.preprocess].
        """
        return self._delegate("spawn_order", default=0.0)

    def should_spawn(self) -> bool:
        """Return whether the entity should be spawned.

        Runs each frame while the entity is the first entity in the spawn queue.
        """
        return self._delegate("should_spawn", default=True)

    def initialize(self):
        """Initialize this entity.

        Runs when this entity is spawned.
        """
        self._delegate("initialize")

    def update_sequential(self):
        """Perform non-parallel actions for this frame.

        Runs first each frame.

        This is where logic affecting shared memory should be placed.
        Other logic should typically be placed in
        [`update_parallel`][sonolus.script.archetype.PlayArchetype.update_parallel]
        for better performance.
        """
        self._delegate("update_sequential")

    def update_parallel(self):
        """Perform parallel actions for this frame.

        Runs after [`touch`][sonolus.script.archetype.PlayArchetype.touch] each frame.

        This is where most gameplay logic should be placed.
        """
        self._delegate("update_parallel")

    def touch(self):
        """Handle user input.

        Runs after [`update_sequential`][sonolus.script.archetype.PlayArchetype.update_sequential] each frame.
        """
        self._delegate("touch")

    def terminate(self):
        """Finalize before despawning.

        Runs when the entity is despawned.
        """
        self._delegate("terminate")

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

    is_scored: ClassVar[bool] = False
    """Whether entities of this archetype contribute to combo and score."""

    life: ClassVar[LifeInfo]
    """How entities of this archetype contribute to life depending on judgment.

    Alias for [`archetype_life`][sonolus.script.archetype.WatchArchetype.archetype_life], provided for backwards
    compatibility.
    """

    archetype_life: ClassVar[LifeInfo]
    """How entities of this archetype contribute to life depending on judgment."""

    entity_life: LifeInfo
    """How this specific entity contributes to life depending on judgment.

    This is additive with [`archetype_life`][sonolus.script.archetype.WatchArchetype.archetype_life] - the total
    life increment is the sum of both.
    """

    archetype_score_multiplier: ClassVar[float]
    """Score multiplier for entities of this archetype.

    This is additive with other multipliers (except judgment base multipliers).
    """

    entity_score_multiplier: float
    """Score multiplier for this specific entity.

    This is additive with other multipliers (except judgment base multipliers).
    """

    def preprocess(self):
        """Perform upfront processing.

        Runs first when the level is loaded.
        """
        self._delegate("preprocess")

    def spawn_time(self) -> float:
        """Return the spawn time of the entity."""
        return self._delegate("spawn_time", default=0.0)

    def despawn_time(self) -> float:
        """Return the despawn time of the entity."""
        return self._delegate("despawn_time", default=0.0)

    def initialize(self):
        """Initialize this entity.

        Runs when this entity is spawned.
        """
        self._delegate("initialize")

    def update_sequential(self):
        """Perform non-parallel actions for this frame.

        Runs first each frame.

        This is where logic affecting shared memory should be placed.
        Other logic should typically be placed in
        [`update_parallel`][sonolus.script.archetype.WatchArchetype.update_parallel] for better performance.
        """
        self._delegate("update_sequential")

    def update_parallel(self):
        """Perform parallel actions for this frame.

        Runs after [`update_sequential`][sonolus.script.archetype.WatchArchetype.update_sequential] each frame.

        This is where most gameplay logic should be placed.
        """
        self._delegate("update_parallel")

    def terminate(self):
        """Finalize before despawning.

        Runs when the entity is despawned.
        """
        self._delegate("terminate")

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
    def _post_init_fields(
        cls,
        exported_fields: dict[str, _ArchetypeField],
        memory_fields: dict[str, _ArchetypeField],
    ):
        if exported_fields:
            raise RuntimeError("Watch archetypes cannot have exported fields")


class PreviewArchetype(_BaseArchetype):
    """Base class for preview mode archetypes.

    Usage:
        ```python
        class MyArchetype(PreviewArchetype):
            imported_field: int = imported()
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
        self._delegate("preprocess")

    def render(self):
        """Render the entity.

        Runs after [`preprocess`][sonolus.script.archetype.PreviewArchetype.preprocess].
        """
        self._delegate("render")

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
    def _post_init_fields(
        cls,
        exported_fields: dict[str, _ArchetypeField],
        memory_fields: dict[str, _ArchetypeField],
    ):
        if exported_fields:
            raise RuntimeError("Preview archetypes cannot have exported fields")
        if memory_fields:
            raise RuntimeError("Preview archetypes cannot have entity memory fields")


type AnyArchetype = PlayArchetype | WatchArchetype | PreviewArchetype
"""Union of all archetype types."""


@meta_fn
def get_archetype_by_name(name: str) -> type[AnyArchetype]:
    """Return the archetype with the given name in the current mode."""
    if not ctx():
        raise RuntimeError("Archetypes by name are only available during compilation.")
    name = validate_value(name)  # type: ignore
    if not name._is_py_():  # type: ignore
        raise TypeError(f"Invalid name: '{name}'")
    name = name._as_py_()  # type: ignore
    if not isinstance(name, str):
        raise TypeError(f"Invalid name: '{name}'")
    archetypes_by_name = ctx().mode_state.archetypes_by_name
    if name not in archetypes_by_name:
        raise KeyError(f"Unknown archetype: '{name}'")
    return archetypes_by_name[name]  # type: ignore


@meta_fn
def entity_info_at(index: int) -> PlayEntityInfo | WatchEntityInfo | PreviewEntityInfo:
    """Retrieve entity info of the entity at the given index.

    Available in play, watch, and preview mode.

    Returns:
        The entity info for the current mode.
    """
    if not ctx():
        raise RuntimeError("Calling entity_info_at is only allowed within a callback")
    match ctx().mode_state.mode:
        case Mode.PLAY:
            return _deref(ctx().blocks.EntityInfoArray, index * PlayEntityInfo._size_(), PlayEntityInfo)
        case Mode.WATCH:
            return _deref(ctx().blocks.EntityInfoArray, index * WatchEntityInfo._size_(), WatchEntityInfo)
        case Mode.PREVIEW:
            return _deref(ctx().blocks.EntityInfoArray, index * PreviewEntityInfo._size_(), PreviewEntityInfo)
        case _:
            raise RuntimeError(f"Entity info is not available in mode '{ctx().mode_state.mode.name}'")


class PlayEntityInfo(Record):
    """Information about a play-mode entity."""

    index: int
    """The entity index."""

    archetype_id: int
    """The runtime ID of the entity's archetype."""

    state: int
    """The entity state."""


class WatchEntityInfo(Record):
    """Information about a watch-mode entity."""

    index: int
    """The entity index."""

    archetype_id: int
    """The runtime ID of the entity's archetype."""

    state: int
    """The entity state."""


class PreviewEntityInfo(Record):
    """Information about a preview-mode entity."""

    index: int
    """The entity index."""

    archetype_id: int
    """The runtime ID of the entity's archetype."""


class LifeInfo(Record):
    """How an entity contributes to life.

    Usage:
        ```python
        LifeInfo(perfect_increment: int, great_increment: int, good_increment: int, miss_increment: int)
        ```
    """

    perfect_increment: int
    """Life increment for a perfect judgment."""

    great_increment: int
    """Life increment for a great judgment."""

    good_increment: int
    """Life increment for a good judgment."""

    miss_increment: int
    """Life increment for a miss judgment."""

    def update(
        self,
        perfect_increment: int | None = None,
        great_increment: int | None = None,
        good_increment: int | None = None,
        miss_increment: int | None = None,
    ):
        """Update the life increments.

        Arguments left as None do not change the corresponding increment.
        """
        if perfect_increment is not None:
            self.perfect_increment = perfect_increment
        if great_increment is not None:
            self.great_increment = great_increment
        if good_increment is not None:
            self.good_increment = good_increment
        if miss_increment is not None:
            self.miss_increment = miss_increment


ArchetypeLife = LifeInfo
"""Alias for [`LifeInfo`][sonolus.script.archetype.LifeInfo], kept for backwards compatibility."""


class HapticType(IntEnum):
    """The type of haptic feedback for a judgment."""

    NONE = 0
    """No haptic feedback."""

    LIGHT = 1
    """Light haptic feedback."""

    MEDIUM = 2
    """Medium haptic feedback."""

    HEAVY = 3
    """Heavy haptic feedback."""

    LONG = 4
    """Long haptic feedback."""


class PlayEntityInput(Record):
    """The judgment result recorded for an entity in play mode.

    Accessed as [`result`][sonolus.script.archetype.PlayArchetype.result] on a scored entity, and written to in
    order to report how the entity was hit.
    """

    judgment: Judgment
    """The [`Judgment`][sonolus.script.bucket.Judgment] recorded for the entity."""

    accuracy: float
    """The accuracy value recorded for the entity."""

    bucket: Bucket
    """The [`Bucket`][sonolus.script.bucket.Bucket] the entity's result is recorded in."""

    bucket_value: float
    """The value recorded in `bucket`, shown with that bucket's unit."""

    haptic: HapticType
    """The [`HapticType`][sonolus.script.archetype.HapticType] of haptic feedback for the entity."""


class WatchEntityInput(Record):
    """The judgment result recorded for an entity in watch mode.

    Accessed as [`result`][sonolus.script.archetype.WatchArchetype.result] on a scored entity.
    """

    target_time: float
    """The target time of the entity's hit."""

    bucket: Bucket
    """The [`Bucket`][sonolus.script.bucket.Bucket] the entity's result is recorded in."""

    bucket_value: float
    """The value recorded in `bucket`, shown with that bucket's unit."""


class EntityRef[A: AnyArchetype](Record):
    """Reference to another entity.

    May be used with `typing.Any` to reference an unknown archetype.

    Usage:
        ```python
        ref = EntityRef[MyArchetype](index=123)

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

    def with_archetype[T: AnyArchetype](self, archetype: type[T]) -> EntityRef[T]:
        """Return a new reference with the given archetype type."""
        result = EntityRef[archetype](index=self.index)
        if hasattr(self, "_ref_"):
            # Preserve the referenced entity so the new reference isn't dangling in level data.
            result._ref_ = self._ref_
        return result

    @meta_fn
    def __eq__(self, other: Any) -> bool:
        if not ctx() and isinstance(other, EntityRef):
            self_has_ref = hasattr(self, "_ref_")
            other_has_ref = hasattr(other, "_ref_")
            if self_has_ref or other_has_ref:
                return self_has_ref and other_has_ref and self._ref_ is other._ref_
        return super().__eq__(other)

    @meta_fn
    def __ne__(self, other: Any) -> bool:
        if not ctx() and isinstance(other, EntityRef):
            self_has_ref = hasattr(self, "_ref_")
            other_has_ref = hasattr(other, "_ref_")
            if self_has_ref or other_has_ref:
                return not (self_has_ref and other_has_ref and self._ref_ is other._ref_)
        return super().__ne__(other)

    def __hash__(self) -> int:
        if not ctx() and hasattr(self, "_ref_"):
            return hash(id(self._ref_))
        return super().__hash__()

    @meta_fn
    def __bool__(self):
        if ctx():
            static_error("EntityRef cannot be used in a boolean context. Check index directly instead.")
        return True

    @meta_fn
    def get(self, *, check: bool = True) -> A:
        """Get the entity this reference points to.

        Args:
            check: If true and runtime checks are enabled, asserts that the referenced entity is of this
                   archetype or of a subclass of this archetype. If false, no validation is performed.

        Returns:
            The entity this reference points to.
        """
        assert self.archetype() != Any, (
            "Cannot get entity of unknown (Any) archetype. Use with_archetype() first or use get_as()."
        )
        if ref := getattr(self, "_ref_", None):
            return ref
        return self.archetype().at(self.index, check=check)

    @meta_fn
    def get_as[T: AnyArchetype](self, archetype: type[T]) -> T:
        """Get the entity as the given archetype type.

        Not supported for a reference created by [`ref`][sonolus.script.archetype.PlayArchetype.ref] while
        building level data.
        """
        if getattr(self, "_ref_", None):
            raise TypeError("Using get_as in level data is not supported.")
        return self.with_archetype(archetype).get()

    def archetype_matches(self, strict: bool = False) -> bool:
        """Check if entity at the index is of this archetype.

        Args:
            strict: If true, only returns true if the entity is exactly of this archetype. If false, also returns true
                    if the entity is of a subclass of this archetype.

        Returns:
            Whether the entity at the given index is of this archetype.
        """
        assert self.archetype() != Any, (
            "Cannot use archetype_matches with unknown (Any) archetype. Use with_archetype() first."
        )
        return self.archetype().is_at(self.index, strict=strict)

    def _to_list_(self, level_refs: dict[Any, str] | None = None) -> list[DataValue | str]:
        ref = getattr(self, "_ref_", None)
        if ref is None:
            return Num._accept_(self.index)._to_list_()
        else:
            if level_refs is None:
                raise RuntimeError("Unexpected missing level_refs")
            if ref not in level_refs:
                raise ValueError(
                    f"Reference to a '{ref.name}' entity that is not in the level's entities; "
                    "add the referenced entity to the level"
                )
            return [level_refs[ref]]

    def _copy_from_(self, value: Any, *, initializing: bool = False):
        super()._copy_from_(value, initializing=initializing)
        if hasattr(value, "_ref_"):
            self._ref_ = value._ref_
        else:
            self.__dict__.pop("_ref_", None)

    def _copy_(self) -> Self:
        result = super()._copy_()
        if hasattr(self, "_ref_"):
            result._ref_ = self._ref_
        return result

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
        if type(value) is cls:
            return value
        return value.with_archetype(cls.archetype())

    @classmethod
    def _validate_parameterized_(cls):
        if not issubclass(cls.archetype(), _BaseArchetype) and cls.archetype() is not Any:
            raise TypeError("EntityRef type parameter must be an Archetype or Any")


class StandardArchetypeName(StrEnum):
    """Standard archetype names."""

    BPM_CHANGE = "#BPM_CHANGE"
    """Bpm change marker"""

    TIMESCALE_CHANGE = "#TIMESCALE_CHANGE"
    """Timescale change marker"""

    TIMESCALE_GROUP = "#TIMESCALE_GROUP"
    """Entity referenced by the timescale changes in a group"""


class StandardImportName:
    """Standard import names for Archetype fields.

    Usage:
        ```python
        class MyArchetype(WatchArchetype):
            judgment: int = imported(name=StandardImportName.JUDGMENT)
        ```
    """

    BEAT = "#BEAT"
    """The beat of the entity."""

    BPM = "#BPM"
    """The bpm, for bpm change markers."""

    TIMESCALE = "#TIMESCALE"
    """The timescale, for timescale change markers."""

    TIMESCALE_SKIP = "#TIMESCALE_SKIP"
    """The scaled time to skip, for timescale change markers."""

    TIMESCALE_GROUP = "#TIMESCALE_GROUP"
    """The timescale group, for timescale change markers."""

    TIMESCALE_EASE = "#TIMESCALE_EASE"
    """The timescale ease type, for timescale change markers."""

    JUDGMENT = "#JUDGMENT"
    """The judgment of the entity.

    Automatically set in watch mode for archetypes with a corresponding scored play mode archetype.
    """

    ACCURACY = "#ACCURACY"
    """The accuracy of the entity.

    Automatically set in watch mode for archetypes with a corresponding scored play mode archetype.
    """


class StandardImport:
    """Standard import annotations for Archetype fields.

    Usage:
        ```python
        class MyArchetype(WatchArchetype):
            judgment: StandardImport.JUDGMENT
        ```
    """

    BEAT = Annotated[float, imported(name=StandardImportName.BEAT)]
    """The beat of the entity."""

    BPM = Annotated[float, imported(name=StandardImportName.BPM)]
    """The bpm, for bpm change markers."""

    TIMESCALE = Annotated[float, imported(name=StandardImportName.TIMESCALE)]
    """The timescale, for timescale change markers."""

    TIMESCALE_SKIP = Annotated[float, imported(name=StandardImportName.TIMESCALE_SKIP)]
    """The scaled time to skip, for timescale change markers."""

    TIMESCALE_GROUP = Annotated[EntityRef[Any], imported(name=StandardImportName.TIMESCALE_GROUP)]
    """The timescale group, for timescale change markers."""

    TIMESCALE_EASE = Annotated[TimescaleEase, imported(name=StandardImportName.TIMESCALE_EASE)]
    """The timescale ease type, for timescale change markers."""

    JUDGMENT = Annotated[Judgment, imported(name=StandardImportName.JUDGMENT)]
    """The judgment of the entity.

    Automatically set in watch mode for archetypes with a corresponding scored play mode archetype.
    """
    ACCURACY = Annotated[float, imported(name=StandardImportName.ACCURACY)]
    """The accuracy of the entity.

    Automatically set in watch mode for archetypes with a corresponding scored play mode archetype.
    """
