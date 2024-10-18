from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Self

from sonolus.backend.blocks import BlockData
from sonolus.backend.ir import IRConst, IRStmt
from sonolus.backend.place import Block, BlockPlace, TempBlock
from sonolus.script.internal.value import Value

_compiler_internal_ = True

context_var = ContextVar("context_var", default=None)


class Context:
    statements: list[IRStmt]
    test: IRStmt = IRConst(0)
    outgoing: dict[float | None, Context]
    blocks: type[Block]
    callback: str
    used_names: dict[str, int]
    scope: Scope
    rom: ReadOnlyMemory
    loop_variables: dict[str, Value]
    live: bool

    def __init__(self, blocks: type[Block], callback: str, used_names: dict[str, int] | None = None,
                 scope: Scope | None = None, rom: ReadOnlyMemory | None = None, live: bool = True):
        self.statements = []
        self.outgoing = {}
        self.blocks = blocks
        self.callback = callback
        self.used_names = used_names or {}
        self.scope = scope or Scope()
        self.rom = rom or ReadOnlyMemory(blocks.EngineRom)
        self.loop_variables = {}
        self.live = live

    def check_readable(self, place: BlockPlace):
        if isinstance(place.block, BlockData) and self.callback not in place.block.readable:
            raise RuntimeError(f"Block {place.block} is not readable in {self.callback}")

    def check_writable(self, place: BlockPlace):
        if isinstance(place.block, BlockData) and self.callback not in place.block.writable:
            raise RuntimeError(f"Block {place.block} is not writable in {self.callback}")

    def add_statements(self, *statements: IRStmt):
        self.statements.extend(statements)

    def alloc(self, name: str | None = None, size: int = 1) -> BlockPlace:
        if size == 0:
            return BlockPlace(TempBlock(name or "e", 0), 0)
        name = name or ("v" if size == 1 else "a")
        num = self._get_alloc_number(name)
        return BlockPlace(TempBlock(f"{name}{num}", 1), 0)

    def _get_alloc_number(self, name: str) -> int:
        if name not in self.used_names:
            self.used_names[name] = 0
        self.used_names[name] += 1
        return self.used_names[name]

    def copy_with_scope(self, scope: Scope) -> Context:
        return Context(
            blocks=self.blocks,
            callback=self.callback,
            used_names=self.used_names,
            scope=scope,
            rom=self.rom,
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
                header.scope.set_binding(name, ConflictBinding())
        return header

    def branch_to_loop_header(self, header: Self):
        assert len(self.outgoing) == 0
        self.outgoing[None] = header
        for name, target_value in header.loop_variables.items():
            with using_ctx(self):
                value = header.scope.get_value(name)
                value = type(target_value)._accept_(value)
                target_value._set_(value)

    @classmethod
    def meet(cls, contexts: list[Context]) -> Context:
        contexts = [context for context in contexts if context.live]
        if not contexts:
            raise RuntimeError("Cannot meet empty list of contexts")
        assert not any(context.outgoing for context in contexts)
        assert all(len(context.outgoing) == 0 for context in contexts)
        target = contexts[0].copy_with_scope(Scope())
        Scope.apply_merge(target, contexts)
        for context in contexts:
            context.outgoing[None] = target
        return target


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
    block: Block
    values: list[float]
    indexes: dict[tuple[float, ...], int]

    def __init__(self, block: Block):
        self.block = block
        self.values = []
        self.indexes = {}

    def __getitem__(self, item: tuple[float, ...]) -> BlockPlace:
        if item not in self.indexes:
            index = len(self.values)
            self.indexes[item] = index
            self.values.extend(item)
        else:
            index = self.indexes[item]
        return BlockPlace(self.block, index)


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

    def __init__(self, bindings: dict[str, Binding] = None):
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
                    f"Binding '{name}' has multiple conflicting definitions or may not be guaranteed to be defined")
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
