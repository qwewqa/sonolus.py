from __future__ import annotations

import warnings
from dataclasses import dataclass
from enum import Enum
from typing import TypeVar, Type, Callable, ClassVar, get_type_hints

from sonolus.backend.callback import CallbackType, CALLBACK_TYPES
from sonolus.backend.evaluation import CompilationInfo
from sonolus.backend.ir import (
    Location,
    MemoryBlock,
    IRFunc,
    IRGet,
    TempRef,
    IRConst,
    IRValueType,
)
from sonolus.scripting.internal.control_flow import ExecuteVoid
from sonolus.scripting.internal.primitive import Num
from sonolus.scripting.internal.sls_func import sls_func
from sonolus.scripting.internal.statement import Statement
from sonolus.scripting.internal.struct import Empty, Struct
from sonolus.scripting.internal.value import Value, convert_value
from sonolus.scripting.internal.void import Void

SHARED_MEMORY_SIZE = 32
ENTITY_INFO_SIZE = 3

TScript = TypeVar("TScript", bound="Script")


class Script(Statement):
    memory: Empty
    shared_memory: Empty
    data: Empty
    info: EntityInfo
    input: EntityInput

    _metadata_: ClassVar[ScriptMetadata]

    def __init__(self):
        raise TypeError("Script may not be instantiated directly.")

    def __init_subclass__(cls, input: bool | None = None, **kwargs):
        if input is None:
            if hasattr(cls, "_metadata_"):
                input = cls._metadata_.input
            else:
                input = False

        hints = get_type_hints(cls)
        callbacks = {}
        for name, value in CALLBACK_TYPES.items():
            callback = getattr(cls, name)
            if callback is getattr(Script, name):
                continue
            callbacks[value] = callback
            callback._script_ = cls
        meta = ScriptMetadata(
            callbacks=callbacks,
            memory_type=hints["memory"],
            shared_memory_type=hints["shared_memory"],
            data_type=hints["data"],
            input=input,
        )
        meta.validate()
        cls._metadata_ = meta

    def preprocess(self):
        pass

    def spawn_order(self):
        pass

    def should_spawn(self):
        pass

    def initialize(self):
        pass

    def update_sequential(self):
        pass

    def touch(self):
        pass

    def update_parallel(self):
        pass

    def terminate(self):
        pass

    @property
    @sls_func(ast=False)
    def life(cls) -> ScriptLifeStruct:
        offset = Num._create_(cls._get_archetype_id()) * Num(4)
        return ScriptLifeStruct._create_(
            Location(MemoryBlock.ARCHETYPE_LIFE, offset.ir(), 0, None)
        )._set_parent_(offset)

    @life.setter
    def life(cls, value: ScriptLifeStruct):
        if value is not cls.life:
            raise ValueError("Cannot set life of script.")

    life = classmethod(life)

    @classmethod
    def create_for_evaluation(cls):
        meta = cls._metadata_
        result = cls.__new__(cls)
        Statement.__init__(result)
        result.memory = meta.memory_type._create_(
            Location(MemoryBlock.ENTITY_MEMORY, IRConst(0), 0, 1)
        )._set_static_()
        result.shared_memory = meta.shared_memory_type._create_(
            Location(MemoryBlock.ENTITY_SHARED_MEMORY, IRConst(0), 0, 1)
        )._set_static_()
        result.data = meta.data_type._create_(
            Location(MemoryBlock.ENTITY_DATA, IRConst(0), 0, 1)
        )._set_static_()
        result.info = EntityInfo._create_(
            Location(MemoryBlock.ENTITY_INFO, IRConst(0), 0, 1)
        )._set_static_()
        result.input = EntityInput._create_(
            Location(MemoryBlock.ENTITY_INPUT, IRConst(0), 0, 1)
        )._set_static_()
        result._attributes_.is_static = True
        return result

    @classmethod
    def spawn(cls, data) -> Void:
        data = convert_value(data, cls._metadata_.memory_type)
        node = IRFunc("Spawn", [cls._get_archetype_id(), *data._flatten_()])
        return Void(node)._set_parent_(data)

    @classmethod
    def at(cls: Type[TScript], index: Num) -> TScript:
        meta = cls._metadata_
        index: Num = convert_value(index, Num)
        offset = index * SHARED_MEMORY_SIZE
        ir_offset = offset.ir()
        info_offset = index * ENTITY_INFO_SIZE
        ir_info_offset = info_offset.ir()
        result = cls.__new__(cls)
        Statement.__init__(result)
        result.shared_memory = meta.shared_memory_type._create_(
            Location(MemoryBlock.ENTITY_SHARED_MEMORY_ARRAY, ir_offset, 0, None),
        )._set_parent_(result)
        result.data = meta.data_type._create_(
            Location(MemoryBlock.ENTITY_DATA_ARRAY, ir_offset, 0, None),
        )._set_parent_(result)
        result.info = EntityInfo._create_(
            Location(MemoryBlock.ENTITY_INFO_ARRAY, ir_info_offset, 0, None),
        )._set_parent_(result)
        result._set_parent_(
            ExecuteVoid(
                offset, info_offset, result.shared_memory, result.data, result.info
            )
        )
        return result

    @classmethod
    def _get_archetype_id(cls) -> IRValueType:
        compilation_info = CompilationInfo.get()
        if compilation_info.callback.name == "_debug_":
            return IRGet(
                Location(TempRef(f"ScriptIndex${cls.__name__}", 1), IRConst(0), 0, 1)
            )
        else:
            if cls not in compilation_info.script_ids:
                raise KeyError(
                    f"Script {cls.__name__} is not part of the current compilation."
                )
            return IRConst(compilation_info.script_ids[cls])


def callback_function(fn=None, /, *, order: int = 0, preprocessor=sls_func):
    def wrap(fn):
        if fn.__name__ not in CALLBACK_TYPES:
            raise ValueError(f"Invalid callback name: {fn.__name}.")
        fn._callback_order_ = order
        if preprocessor is not None and not hasattr(fn, "__wrapped__"):
            fn = preprocessor(fn)
        return fn

    if fn is None:
        return wrap

    return wrap(fn)


@dataclass
class ScriptMetadata:
    callbacks: dict[CallbackType, Callable]
    memory_type: Type[Value]
    shared_memory_type: Type[Value]
    data_type: Type[Value]
    input: bool

    def validate(self):
        if not Value.is_value_class(self.memory_type):
            raise TypeError("Expected memory to be a Value subclass.")
        if not Value.is_value_class(self.shared_memory_type):
            raise TypeError("Expected shared_memory to be a Value subclass.")
        if not Value.is_value_class(self.data_type):
            raise TypeError("Expected data to be a Value subclass.")
        if self.memory_type._size_ > 64:
            warnings.warn(
                f"Type {self.memory_type} may be too large for entity memory."
            )
        if self.shared_memory_type._size_ > 32:
            warnings.warn(
                f"Type {self.memory_type} may be too large for entity shared memory."
            )
        if self.data_type._size_ > 32:
            warnings.warn(f"Type {self.memory_type} may be too large for entity data.")


class EntityInfo(Struct):
    index: Num
    archetype: Num
    state: Num

    @property
    @sls_func(ast=False)
    def is_waiting(self):
        return self.state == EntityState.WAITING

    @property
    @sls_func(ast=False)
    def is_spawned(self):
        return self.state == EntityState.SPAWNED

    @property
    @sls_func(ast=False)
    def is_despawned(self):
        return self.state == EntityState.DESPAWNED


class EntityState(int, Enum):
    WAITING = 0
    SPAWNED = 1
    DESPAWNED = 2


class EntityInput(Struct):
    judgement: Num
    accuracy: Num
    bucket: Num
    bucket_value: Num


class ScriptLifeStruct(Struct):
    perfect_life_increment: Num
    great_life_increment: Num
    good_life_increment: Num
    miss_life_increment: Num


@dataclass
class LevelScriptData:
    script: Type[Script]
    data: Value
