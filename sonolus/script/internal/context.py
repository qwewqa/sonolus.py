from __future__ import annotations

from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from threading import Lock
from typing import TYPE_CHECKING, Any, Literal, Self

from sonolus.backend.blocks import BLOCK_MEMORY_SIZES, BlockData, PlayBlock
from sonolus.backend.ir import IRConst, IRExpr, IRStmt
from sonolus.backend.mode import Mode
from sonolus.backend.optimize.flow import BasicBlock, FlowEdge
from sonolus.backend.place import (
    PREALLOCATED_TEMP_PLACE_COUNT,
    Block,
    BlockPlace,
    TempBlock,
    preallocated_temp_block_places,
)
from sonolus.script.internal.error import CompilationError
from sonolus.script.internal.value import Value

if TYPE_CHECKING:
    from sonolus.script.globals import _GlobalInfo, _GlobalPlaceholder
    from sonolus.script.project import BuildConfig

_compiler_internal_ = True

_context: Context | None = None

_validate_value = None  # Lazily initialized to avoid a circular import


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

_debug_config: DebugConfig = _disabled_debug_config


class RuntimeChecks(Enum):
    """Runtime error checking modes."""

    NONE = "none"
    """No runtime checks."""

    TERMINATE = "terminate"
    """Terminate on errors."""

    NOTIFY_AND_TERMINATE = "notify_and_terminate"
    """Log, debug pause, and terminate on errors."""


@dataclass
class FunctionVisitStatistics:
    total_time: int = 0
    own_time: int = 0
    call_count: int = 0


class ProjectContextState:
    rom: ReadOnlyMemory
    const_mappings: dict[Any, int]
    debug_str_mappings: dict[str, int]
    lock: Lock
    runtime_checks: RuntimeChecks
    visit_stats: dict[str, FunctionVisitStatistics]

    def __init__(
        self,
        rom: ReadOnlyMemory | None = None,
        const_mappings: dict[Any, int] | None = None,
        debug_str_mappings: dict[str, int] | None = None,
        runtime_checks: RuntimeChecks = RuntimeChecks.NONE,
    ):
        self.rom = ReadOnlyMemory() if rom is None else rom
        self.const_mappings = {} if const_mappings is None else const_mappings
        self.debug_str_mappings = {} if debug_str_mappings is None else debug_str_mappings
        self.lock = Lock()
        self.runtime_checks = runtime_checks
        self.visit_stats = {}

    @classmethod
    def from_build_config(
        cls,
        config: BuildConfig,
        rom: ReadOnlyMemory | None = None,
        const_mappings: dict[Any, int] | None = None,
        debug_str_mappings: dict[str, int] | None = None,
    ) -> Self:
        return cls(
            rom=rom,
            const_mappings=const_mappings,
            debug_str_mappings=debug_str_mappings,
            runtime_checks=config.runtime_checks,
        )


class ModeContextState:
    archetypes: dict[type, int]
    compile_time_only_archetypes: set[type]
    archetypes_by_name: dict[str, type]
    subclass_ids_cache: dict[type, list[int]]
    keys_by_archetype_id: Sequence[int]
    is_scored_by_archetype_id: Sequence[bool]
    archetype_mro_id_array_rom_indexes: Sequence[int] | None = None
    environment_mappings: dict[_GlobalInfo, int]
    environment_offsets: dict[Block, int]
    mode: Mode
    lock: Lock

    def __init__(self, mode: Mode, archetypes: list[type] | None = None):
        from sonolus.script.array import Array

        archetypes = [*archetypes] if archetypes is not None else []
        seen_archetypes = {*archetypes}
        compile_time_only_archetypes = set()
        for type_ in [*archetypes]:
            for entry in type_.mro():
                if getattr(entry, "_is_concrete_archetype_", False) and entry not in seen_archetypes:
                    archetypes.append(entry)
                    seen_archetypes.add(entry)
                    compile_time_only_archetypes.add(entry)
        self.archetypes = {type_: idx for idx, type_ in enumerate(archetypes)}
        self.compile_time_only_archetypes = compile_time_only_archetypes
        self.archetypes_by_name = {type_.name: type_ for type_, _ in self.archetypes.items()}  # type: ignore
        self.subclass_ids_cache = {}
        self.keys_by_archetype_id = (
            Array(*((getattr(a, "_key_", -1)) for a in archetypes)) if archetypes else Array[int, Literal[0]]()
        )
        self.is_scored_by_archetype_id = (
            Array(*((getattr(a, "_is_scored_", False)) for a in archetypes))
            if archetypes
            else Array[bool, Literal[0]]()
        )
        self.environment_mappings = {}
        self.environment_offsets = {}
        self.mode = mode
        self.lock = Lock()

    def _init_archetype_mro_info(self, rom: ReadOnlyMemory):
        from sonolus.script.array import Array
        from sonolus.script.num import Num

        with self.lock:
            if self.archetype_mro_id_array_rom_indexes is not None:
                return
            archetype_mro_id_values = []
            archetype_mro_id_offsets = []
            for type_ in self.archetypes:
                mro_ids = [self.archetypes[entry] for entry in type_.mro() if entry in self.archetypes]
                archetype_mro_id_offsets.append(len(archetype_mro_id_values))
                archetype_mro_id_values.append(len(mro_ids))
                archetype_mro_id_values.extend(mro_ids)
            archetype_mro_id_array_place = rom[tuple(archetype_mro_id_values)]

            archetype_mro_id_rom_indexes = Array[int, len(archetype_mro_id_offsets)]._with_value(
                [Num._accept_(offset + archetype_mro_id_array_place.index) for offset in archetype_mro_id_offsets]
            )
            self.archetype_mro_id_array_rom_indexes = archetype_mro_id_rom_indexes


class CallbackContextState:
    callback: str
    used_names: dict[str, int]
    debug_stack: list[str]
    no_eval: bool
    visitor_own_time: int
    is_in_generator: bool

    def __init__(self, callback: str, no_eval: bool = False):
        self.callback = callback
        self.used_names = {}
        self.debug_stack = []
        self.no_eval = no_eval
        self.visitor_own_time = 0
        self.is_in_generator = False


class Context:
    project_state: ProjectContextState
    mode_state: ModeContextState
    callback_state: CallbackContextState
    statements: list[IRStmt]
    test: IRExpr
    outgoing: dict[float | None, Context]
    scope: Scope
    loop_variables: dict[str, ValueBinding]
    live: bool

    def __init__(
        self,
        project_state: ProjectContextState,
        mode_state: ModeContextState,
        callback_state: CallbackContextState,
        scope: Scope | None = None,
        live: bool = True,
    ):
        self.project_state = project_state
        self.mode_state = mode_state
        self.callback_state = callback_state
        self.statements = []
        self.test = IRConst(0)
        self.outgoing = {}
        self.scope = scope if scope is not None else Scope()
        self.loop_variables = {}
        self.live = live

    @property
    def rom(self) -> ReadOnlyMemory:
        return self.project_state.rom

    @property
    def blocks(self) -> type[Block]:
        return self.mode_state.mode.blocks

    @property
    def callback(self) -> str:
        return self.callback_state.callback

    @property
    def used_names(self) -> dict[str, int]:
        return self.callback_state.used_names

    @property
    def no_eval(self) -> bool:
        return self.callback_state.no_eval

    def _is_block_accessible(self, block: Block, callback: str, writable: bool) -> bool:
        """Whether `callback` may read (or write, if `writable`) `block` in the current mode."""
        block_perms = self.mode_state.mode.blocks(block)
        return callback in (block_perms.writable if writable else block_perms.readable)

    def check_readable(self, place: BlockPlace):
        if _debug_config.unchecked_reads:
            return
        callback = self.callback_state.callback
        if not callback:
            return
        block = place.block
        if isinstance(block, BlockData) and not self._is_block_accessible(block, callback, False):
            raise RuntimeError(f"Block {block} is not readable in {callback}")

    def check_writable(self, place: BlockPlace):
        if _debug_config.unchecked_writes:
            return
        callback = self.callback_state.callback
        if not callback:
            return
        block = place.block
        if isinstance(block, BlockData) and not self._is_block_accessible(block, callback, True):
            raise RuntimeError(f"Block {block} is not writable in {callback}")

    def is_readable(self, place: BlockPlace) -> bool:
        if debug_config().unchecked_reads:
            return True
        callback = self.callback
        return callback and self._is_block_accessible(place.block, callback, False)

    def is_writable(self, place: BlockPlace) -> bool:
        if debug_config().unchecked_writes:
            return True
        callback = self.callback
        return callback and self._is_block_accessible(place.block, callback, True)

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
        if name == "v" and num < PREALLOCATED_TEMP_PLACE_COUNT:
            return preallocated_temp_block_places[num]
        return BlockPlace(TempBlock(f"{name}{num}", size), 0)

    def _get_alloc_number(self, name: str) -> int:
        used_names = self.callback_state.used_names
        num = used_names.get(name, 0) + 1
        used_names[name] = num
        return num

    def save_alloc_state(self) -> dict[str, int]:
        return self.used_names.copy()

    def restore_alloc_state(self, state: dict[str, int]):
        self.callback_state.used_names = state.copy()

    def copy_with_scope(self, scope: Scope) -> Context:
        return Context(
            project_state=self.project_state,
            mode_state=self.mode_state,
            callback_state=self.callback_state,
            scope=scope,
            live=self.live,
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

    def new_disconnected(self):
        return self.copy_with_scope(self.scope.copy())

    def new_empty_disconnected(self):
        return self.copy_with_scope(Scope())

    def into_dead(self):
        """Create a new context for code that is unreachable, like after a return statement."""
        result = self.copy_with_scope(self.scope.copy())
        result.live = False
        return result

    def prepare_loop_header(self, to_merge: set[str]) -> Context:
        # to_merge is the set of bindings set anywhere in the loop
        # we need to invalidate them in the header if they're reference types
        # or merge them if they're value types
        # structure is self -> intermediate -> header -> body (continue -> header) | exit
        assert len(self.outgoing) == 0
        header = self.branch(None)
        for name in sorted(to_merge):
            binding = self.scope.get_binding(name)
            if not isinstance(binding, ValueBinding):
                continue
            value = binding.value
            type_ = type(value)
            if type_._is_value_type_():
                target_value = type_._from_place_(header.alloc(size=type_._size_()))
                with using_ctx(self):
                    target_value._set_(value)
                loop_binding = ValueBinding(target_value)
                header.scope.set_binding(name, loop_binding)
                header.loop_variables[name] = loop_binding
            else:
                loop_binding = ValueBinding(value)
                header.scope.set_binding(name, loop_binding)
                header.loop_variables[name] = loop_binding
        return header

    def branch_to_loop_header(self, header: Self):
        if not self.live:
            return
        assert len(self.outgoing) == 0
        self.outgoing[None] = header
        values = {}
        # First do a pass through and get every value
        for name, binding in header.loop_variables.items():
            target_value = binding.value
            with using_ctx(self):
                if type(target_value)._is_value_type_():
                    value = self.scope.get_value(name)
                    # We make this call to _get_readonly_() to ensure that we're reading the value at this
                    # point in time specifically, since _get_readonly_ will make a copy if the value is
                    # e.g. a Num backed by a TempBlock which could be mutated.
                    values[name] = value._get_readonly_()
        # Then actually set them
        for name, binding in header.loop_variables.items():
            target_value = binding.value
            with using_ctx(self):
                if type(target_value)._is_value_type_():
                    value = values[name]
                    value = type(target_value)._accept_(value)
                    target_value._set_(value)
                else:
                    value = self.scope.get_value(name)
                    if target_value is not value and binding.read_count > 0:
                        raise RuntimeError(
                            f"Variable '{name}' may have conflicting definitions between loop iterations"
                        )

    def map_constant(self, value: Any) -> int:
        with self.project_state.lock:
            const_mappings = self.project_state.const_mappings
            if value not in const_mappings:
                const_mappings[value] = len(const_mappings)
            return const_mappings[value]

    def map_debug_message(self, message: str) -> int:
        with self.project_state.lock:
            message_with_trace = "\n".join([*self.callback_state.debug_stack, message])
            debug_str_mappings = self.project_state.debug_str_mappings
            if message_with_trace not in debug_str_mappings:
                debug_str_mappings[message_with_trace] = len(debug_str_mappings) + 1
            return debug_str_mappings[message_with_trace]

    def get_global_base(self, value: _GlobalInfo | _GlobalPlaceholder) -> BlockPlace:
        with self.mode_state.lock:
            block = value.blocks.get(self.mode_state.mode)
            if block is None:
                raise RuntimeError(f"Global {value} is not available in '{self.mode_state.mode.name}' mode")
            if value not in self.mode_state.environment_mappings:
                if value.offset is None:
                    offset = self.mode_state.environment_offsets.get(block, 0)
                    new_size = offset + value.size
                    capacity = BLOCK_MEMORY_SIZES.get(block)
                    if capacity is not None and new_size > capacity:
                        raise CompilationError(
                            f"The {block} memory block exceeded its maximum size in "
                            f"{self.mode_state.mode.name} mode: {new_size} values are used, but the maximum "
                            f"is {capacity}. Reduce the amount of data stored in {block}."
                        )
                    self.mode_state.environment_mappings[value] = offset
                    self.mode_state.environment_offsets[block] = new_size
                else:
                    self.mode_state.environment_mappings[value] = value.offset
            return BlockPlace(block, self.mode_state.environment_mappings[value])

    @classmethod
    def meet(cls, contexts: list[Context]) -> Context:
        if not contexts:
            raise RuntimeError("Cannot meet empty list of contexts")
        if not any(context.live for context in contexts):
            return contexts[0].into_dead()
        contexts = [context for context in contexts if context.live]
        assert not any(context.outgoing for context in contexts)
        if len(contexts) == 1:
            # A single live predecessor has nothing to merge; continue directly in it
            # instead of minting an empty pass-through block + edge that the optimizer
            # would collapse anyway.
            return contexts[0]
        target = contexts[0].copy_with_scope(Scope())
        Scope.apply_merge(target, contexts)
        for context in contexts:
            context.outgoing[None] = target
        return target

    def register_archetype(self, type_: type) -> int:
        with self.mode_state.lock:
            if type_ not in self.mode_state.archetypes:
                self.mode_state.archetypes[type_] = len(self.mode_state.archetypes)
                self.mode_state.subclass_ids_cache.clear()
            return self.mode_state.archetypes[type_]

    def get_archetype_mro_id_array(self, archetype_id: int) -> Sequence[int]:
        from sonolus.script.containers import ArrayPointer
        from sonolus.script.num import Num
        from sonolus.script.pointer import _deref

        self.mode_state._init_archetype_mro_info(self.rom)
        rom_index = self.mode_state.archetype_mro_id_array_rom_indexes[archetype_id]
        return ArrayPointer[int](_deref(self.blocks.EngineRom, rom_index, Num), self.blocks.EngineRom, rom_index + 1)


def ctx() -> Context | Any:  # Using Any to silence type checker warnings if it's None
    return _context


def set_ctx(value: Context | None):
    global _context  # noqa: PLW0603
    old_value = _context
    _context = value
    return old_value


@contextmanager
def using_ctx(value: Context | None):
    global _context  # noqa: PLW0603
    old_value = _context
    _context = value
    try:
        yield
    finally:
        _context = old_value


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
        context = _context
        if context:
            return context.blocks.EngineRom
        else:
            return PlayBlock.EngineRom

    def get_value(self, index: int) -> float:
        with self._lock:
            if index < 0 or index >= len(self.values):
                raise IndexError(f"Index {index} out of bounds for ReadOnlyMemory")
            return self.values[index]


@contextmanager
def enable_debug(config: DebugConfig | None = None):
    global _debug_config  # noqa: PLW0603
    if config is None:
        config = _full_debug_config
    old_config = _debug_config
    _debug_config = config
    try:
        yield
    finally:
        _debug_config = old_config


def debug_config() -> DebugConfig:
    return _debug_config


@dataclass(slots=True)
class ValueBinding:
    value: Value
    read_count: int = 0


@dataclass(slots=True)
class ConflictBinding:
    pass


@dataclass(slots=True)
class EmptyBinding:
    pass


Binding = ValueBinding | ConflictBinding | EmptyBinding

_EMPTY_BINDING = EmptyBinding()


class Scope:
    bindings: dict[str, Binding]

    def __init__(self, bindings: dict[str, Binding] | None = None):
        self.bindings = bindings or {}

    def get_binding(self, name: str) -> Binding:
        return self.bindings.get(name, _EMPTY_BINDING)

    def set_binding(self, name: str, binding: Binding):
        self.bindings[name] = binding

    def get_value(self, name: str) -> Value | Any:
        binding = self.get_binding(name)
        match binding:
            case ValueBinding() as b:
                # we don't need to call _get_() here because _set_() is never called where it could be a problem
                b.read_count += 1
                return b.value
            case ConflictBinding():
                raise RuntimeError(
                    f"Binding '{name}' has multiple conflicting definitions or may not be guaranteed to be defined"
                )
            case EmptyBinding():
                raise RuntimeError(f"Binding '{name}' is not defined")

    def set_value(self, name: str, value: Value):
        global _validate_value  # noqa: PLW0603
        if _validate_value is None:
            from sonolus.script.internal.impl import validate_value

            _validate_value = validate_value
        self.bindings[name] = ValueBinding(_validate_value(value))

    def delete_binding(self, name: str):
        del self.bindings[name]

    def copy(self) -> Scope:
        return Scope(self.bindings.copy())

    @classmethod
    def apply_merge(cls, target: Context, incoming: list[Context]):
        if not incoming:
            return
        bindings_by_source = [context.scope.bindings for context in incoming]
        first_bindings = bindings_by_source[0]
        rest_bindings = bindings_by_source[1:]
        target_bindings = target.scope.bindings
        # Keys in first-seen order across sources, matching
        # unique(key for source in sources for key in source.bindings).
        keys: dict[str, Binding] = {}
        for bindings in bindings_by_source:
            keys.update(bindings)
        for key in keys:
            first = first_bindings.get(key, _EMPTY_BINDING)
            for bindings in rest_bindings:
                if bindings.get(key, _EMPTY_BINDING) is not first:
                    break
            else:
                # Fast path: every source holds the same binding object, so the merge
                # result is that binding itself. Keeping the object (not a copy) is
                # load-bearing: the loop-header read-before-rebind check relies on
                # identity with header.loop_variables and on read counts accrued
                # through merges.
                if isinstance(first, ValueBinding):
                    target_bindings[key] = first
                else:
                    target_bindings[key] = ConflictBinding()
                continue
            bindings = [source.get(key, _EMPTY_BINDING) for source in bindings_by_source]
            if not all(isinstance(binding, ValueBinding) for binding in bindings):
                target_bindings[key] = ConflictBinding()
                continue
            values = [binding.value for binding in bindings]
            if len({id(value) for value in values}) == 1:
                target_bindings[key] = ValueBinding(values[0])
                continue
            types = {type(value) for value in values}
            if len(types) > 1:
                target_bindings[key] = ConflictBinding()
                continue
            common_type: type[Value] = types.pop()
            with using_ctx(target):
                target_value = common_type._get_merge_target_(values)
            if target_value is not NotImplemented:
                for inc in incoming:
                    with using_ctx(inc):
                        target_value._set_(inc.scope.get_value(key))
                target.scope.set_value(key, target_value)
                continue
            else:
                target_bindings[key] = ConflictBinding()
                continue


def _new_cfg_block(statements, test) -> BasicBlock:
    # Fast constructor for the transient blocks context_to_cfg feeds straight to the
    # optimizer: bypass BasicBlock.__init__'s keyword handling and per-block
    # ``x or default`` allocations. ``incoming`` is left as None: this path's
    # consumers (marshal-in and the CFG traversals) only read
    # outgoing/statements/test/phis, and these blocks never reach connect_to.
    block = BasicBlock.__new__(BasicBlock)
    block.phis = {}
    block.statements = statements
    block.test = test
    block.incoming = None
    block.outgoing = set()
    return block


def context_to_cfg(context: Context) -> BasicBlock:
    result = _new_cfg_block(context.statements, context.test)
    blocks = {context: result}
    seen = set()
    visited = []
    queue = [context]
    while queue:
        current = queue.pop()
        if current in seen:
            continue
        seen.add(current)
        visited.append(current)
        current_block = blocks[current]
        current_outgoing = current_block.outgoing
        for condition, target in current.outgoing.items():
            target_block = blocks.get(target)
            if target_block is None:
                target_block = _new_cfg_block(target.statements, target.test)
                blocks[target] = target_block
            current_outgoing.add(FlowEdge(src=current_block, dst=target_block, cond=condition))
            queue.append(target)
    for current in visited:
        # Break cycles so memory can be cleaned without gc
        del current.outgoing
    return result
