from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from threading import Lock
from typing import Any, Self

from sonolus.backend.blocks import BlockData, PlayBlock
from sonolus.backend.ir import IRConst, IRStmt
from sonolus.backend.mode import Mode
from sonolus.backend.optimize.flow import BasicBlock, FlowEdge
from sonolus.backend.place import Block, BlockPlace, TempBlock
from sonolus.script.globals import _GlobalInfo, _GlobalPlaceholder
from sonolus.script.internal.value import Value

_compiler_internal_ = True

context_var = ContextVar("context_var", default=None)


@dataclass(frozen=True)
class DebugConfig:
    unchecked_reads: bool
    unchecked_writes: bool


_full_debug_config = DebugConfig(
    unchecked_reads=True,
    unchecked_writes=True,
)

_disabled_debug_config = DebugConfig(
    unchecked_reads=False,
    unchecked_writes=False,
)

debug_var = ContextVar("debug_var", default=_disabled_debug_config)


class GlobalContextState:
    archetypes: dict[type, int]
    rom: ReadOnlyMemory
    const_mappings: dict[Any, int]
    environment_mappings: dict[_GlobalInfo, int]
    environment_offsets: dict[Block, int]
    mode: Mode
    lock: Lock

    def __init__(self, mode: Mode, archetypes: dict[type, int] | None = None, rom: ReadOnlyMemory | None = None):
        self.archetypes = archetypes or {}
        self.rom = ReadOnlyMemory() if rom is None else rom
        self.const_mappings = {}
        self.environment_mappings = {}
        self.environment_offsets = {}
        self.mode = mode
        self.lock = Lock()


class CallbackContextState:
    callback: str
    used_names: dict[str, int]

    def __init__(self, callback: str):
        self.callback = callback
        self.used_names = {}


class Context:
    global_state: GlobalContextState
    callback_state: CallbackContextState
    statements: list[IRStmt]
    test: IRStmt
    outgoing: dict[float | None, Context]
    scope: Scope
    loop_variables: dict[str, Value]
    live: bool

    def __init__(
        self,
        global_state: GlobalContextState,
        callback_state: CallbackContextState,
        scope: Scope | None = None,
        live: bool = True,
        foldable_constants: dict[TempBlock, list[float | None]] | None = None,
    ):
        self.global_state = global_state
        self.callback_state = callback_state
        self.statements = []
        self.test = IRConst(0)
        self.outgoing = {}
        self.scope = scope if scope is not None else Scope()
        self.loop_variables = {}
        self.live = live
        self.foldable_constants = foldable_constants if foldable_constants is not None else {}

    @property
    def rom(self) -> ReadOnlyMemory:
        return self.global_state.rom

    @property
    def blocks(self) -> type[Block]:
        return self.global_state.mode.blocks

    @property
    def callback(self) -> str:
        return self.callback_state.callback

    @property
    def used_names(self) -> dict[str, int]:
        return self.callback_state.used_names

    def check_readable(self, place: BlockPlace):
        if debug_config().unchecked_reads:
            return
        if not self.callback:
            return
        if isinstance(place.block, BlockData) and self.callback not in self.blocks(place.block).readable:
            raise RuntimeError(f"Block {place.block} is not readable in {self.callback}")

    def check_writable(self, place: BlockPlace):
        if debug_config().unchecked_writes:
            return
        if not self.callback:
            return
        if isinstance(place.block, BlockData) and self.callback not in self.blocks(place.block).writable:
            raise RuntimeError(f"Block {place.block} is not writable in {self.callback}")

    def add_statement(self, statement: IRStmt):
        if not self.live:
            return
        self.statements.append(statement)

    def add_statements(self, *statements: IRStmt):
        for statement in statements:
            self.add_statement(statement)

    def alloc(self, name: str | None = None, size: int = 1) -> BlockPlace:
        if size == 0:
            return BlockPlace(TempBlock(name or "e", 0), 0)
        name = name or ("v" if size == 1 else "a")
        num = self._get_alloc_number(name)
        return BlockPlace(TempBlock(f"{name}{num}", size), 0)

    def _get_alloc_number(self, name: str) -> int:
        if name not in self.used_names:
            self.used_names[name] = 0
        self.used_names[name] += 1
        return self.used_names[name]

    def copy_with_scope(self, scope: Scope) -> Context:
        return Context(
            global_state=self.global_state,
            callback_state=self.callback_state,
            scope=scope,
            live=self.live,
            foldable_constants={**self.foldable_constants},  # We want this to persist even if the scope changes
        )

    def branch(self, condition: float | None):
        assert condition not in self.outgoing
        result = self.copy_with_scope(self.scope.copy())
        self.outgoing[condition] = result
        return result

    def branch_with_scope(self, condition: float | None, scope: Scope):
        assert condition not in self.outgoing
        result = self.copy_with_scope(scope)
        self.outgoing[condition] = result
        return result

    def into_dead(self):
        """Create a new context for code that is unreachable, like after a return statement."""
        result = self.copy_with_scope(self.scope.copy())
        result.live = False
        return result

    def prepare_loop_header(self, to_merge: set[str]) -> Self:
        # to_merge is the set of bindings set anywhere in the loop
        # we need to invalidate them in the header if they're reference types
        # or merge them if they're value types
        # structure is self -> intermediate -> header -> body (continue -> header) | exit
        assert len(self.outgoing) == 0
        header = self.branch(None)
        for name in to_merge:
            binding = self.scope.get_binding(name)
            if not isinstance(binding, ValueBinding):
                continue
            value = binding.value
            type_ = type(value)
            if type_._is_value_type_():
                target_value = type_._from_place_(header.alloc(size=type_._size_()))
                with using_ctx(self):
                    target_value._set_(value)
                header.scope.set_value(name, target_value)
                header.loop_variables[name] = target_value
            else:
                header.scope.set_value(name, value)
                header.loop_variables[name] = value
        return header

    def branch_to_loop_header(self, header: Self):
        if not self.live:
            return
        assert len(self.outgoing) == 0
        self.outgoing[None] = header
        values = {}
        # First do a pass through and copy every value
        for name, target_value in header.loop_variables.items():
            with using_ctx(self):
                if type(target_value)._is_value_type_():
                    value = self.scope.get_value(name)
                    values[name] = value._get_()  # _get_() will make a copy on value types
        # Then actually set them
        for name, target_value in header.loop_variables.items():
            with using_ctx(self):
                if type(target_value)._is_value_type_():
                    value = values[name]
                    value = type(target_value)._accept_(value)
                    target_value._set_(value)
                else:
                    value = self.scope.get_value(name)
                    if target_value is not value:
                        raise RuntimeError(
                            f"Variable '{name}' may have conflicting definitions between loop iterations"
                        )

    def map_constant(self, value: Any) -> int:
        with self.global_state.lock:
            const_mappings = self.global_state.const_mappings
            if value not in const_mappings:
                const_mappings[value] = len(const_mappings)
            return const_mappings[value]

    def get_global_base(self, value: _GlobalInfo | _GlobalPlaceholder) -> BlockPlace:
        with self.global_state.lock:
            block = value.blocks.get(self.global_state.mode)
            if block is None:
                raise RuntimeError(f"Global {value.name} is not available in '{self.global_state.mode.name}' mode")
            if value not in self.global_state.environment_mappings:
                if value.offset is None:
                    offset = self.global_state.environment_offsets.get(block, 0)
                    self.global_state.environment_mappings[value] = offset
                    self.global_state.environment_offsets[block] = offset + value.size
                else:
                    self.global_state.environment_mappings[value] = value.offset
            return BlockPlace(block, self.global_state.environment_mappings[value])

    @classmethod
    def meet(cls, contexts: list[Context]) -> Context:
        if not contexts:
            raise RuntimeError("Cannot meet empty list of contexts")
        if not any(context.live for context in contexts):
            return contexts[0].into_dead()
        contexts = [context for context in contexts if context.live]
        assert not any(context.outgoing for context in contexts)
        assert all(len(context.outgoing) == 0 for context in contexts)
        target = contexts[0].copy_with_scope(Scope())
        Scope.apply_merge(target, contexts)
        for context in contexts:
            context.outgoing[None] = target
        return target

    def register_archetype(self, type_: type) -> int:
        with self.global_state.lock:
            if type_ not in self.global_state.archetypes:
                self.global_state.archetypes[type_] = len(self.global_state.archetypes)
            return self.global_state.archetypes[type_]


def ctx() -> Context | None:
    return context_var.get()


def set_ctx(value: Context | None):
    return context_var.set(value)


@contextmanager
def using_ctx(value: Context | None):
    token = context_var.set(value)
    try:
        yield
    finally:
        context_var.reset(token)


class ReadOnlyMemory:
    values: list[float]
    indexes: dict[tuple[float, ...], int]
    _lock: Lock

    def __init__(self):
        self.values = [
            float("nan"),
            float("inf"),
            float("-inf"),
        ]
        self.indexes = {}
        self._lock = Lock()

    def __getitem__(self, item: tuple[float, ...]) -> BlockPlace:
        with self._lock:
            if item not in self.indexes:
                index = len(self.values)
                self.indexes[item] = index
                self.values.extend(item)
            else:
                index = self.indexes[item]
            return BlockPlace(self.block, index)

    @property
    def block(self) -> Block:
        if ctx():
            return ctx().blocks.EngineRom
        else:
            return PlayBlock.EngineRom

    def get_value(self, index: int) -> float:
        with self._lock:
            if index < 0 or index >= len(self.values):
                raise IndexError(f"Index {index} out of bounds for ReadOnlyMemory")
            return self.values[index]


@contextmanager
def enable_debug(config: DebugConfig | None = None):
    if config is None:
        config = _full_debug_config
    token = debug_var.set(config)
    try:
        yield
    finally:
        debug_var.reset(token)


def debug_config() -> DebugConfig:
    return debug_var.get()


@dataclass
class ValueBinding:
    value: Value


@dataclass
class ConflictBinding:
    pass


@dataclass
class EmptyBinding:
    pass


Binding = ValueBinding | ConflictBinding | EmptyBinding


class Scope:
    bindings: dict[str, Binding]

    def __init__(self, bindings: dict[str, Binding] | None = None):
        self.bindings = bindings or {}

    def get_binding(self, name: str) -> Binding:
        return self.bindings.get(name, EmptyBinding())

    def set_binding(self, name: str, binding: Binding):
        self.bindings[name] = binding

    def get_value(self, name: str) -> Value:
        binding = self.get_binding(name)
        match binding:
            case ValueBinding(value):
                # we don't need to call _get_() here because _set_() is never called where it could be a problem
                return value
            case ConflictBinding():
                raise RuntimeError(
                    f"Binding '{name}' has multiple conflicting definitions or may not be guaranteed to be defined"
                )
            case EmptyBinding():
                raise RuntimeError(f"Binding '{name}' is not defined")

    def set_value(self, name: str, value: Value):
        from sonolus.script.internal.impl import validate_value

        self.bindings[name] = ValueBinding(validate_value(value))

    def delete_binding(self, name: str):
        del self.bindings[name]

    def copy(self) -> Scope:
        return Scope(self.bindings.copy())

    @classmethod
    def apply_merge(cls, target: Context, incoming: list[Context]):
        if not incoming:
            return
        assert all(len(inc.outgoing) == 0 for inc in incoming)
        sources = [context.scope for context in incoming]
        keys = {key for source in sources for key in source.bindings}
        for key in keys:
            bindings = [source.get_binding(key) for source in sources]
            if not all(isinstance(binding, ValueBinding) for binding in bindings):
                target.scope.set_binding(key, ConflictBinding())
                continue
            values = [binding.value for binding in bindings]
            if len({id(value) for value in values}) == 1:
                target.scope.set_binding(key, ValueBinding(values[0]))
                continue
            types = {type(value) for value in values}
            if len(types) > 1:
                target.scope.set_binding(key, ConflictBinding())
                continue
            common_type: type[Value] = types.pop()
            if common_type._is_value_type_():
                target_value = common_type._from_place_(target.alloc(size=common_type._size_()))
                for inc in incoming:
                    with using_ctx(inc):
                        target_value._set_(inc.scope.get_value(key))
                target.scope.set_value(key, target_value)
                continue
            else:
                target.scope.set_binding(key, ConflictBinding())
                continue


def iter_contexts(context: Context):
    seen = set()
    queue = [context]
    while queue:
        current = queue.pop()
        if current in seen:
            continue
        seen.add(current)
        yield current
        queue.extend(current.outgoing.values())


def context_to_cfg(context: Context) -> BasicBlock:
    blocks = {context: BasicBlock(statements=context.statements, test=context.test)}
    for current in iter_contexts(context):
        for condition, target in current.outgoing.items():
            if target not in blocks:
                blocks[target] = BasicBlock(statements=target.statements, test=target.test)
            edge = FlowEdge(src=blocks[current], dst=blocks[target], cond=condition)
            blocks[current].outgoing.add(edge)
            blocks[target].incoming.add(edge)
    return blocks[context]
