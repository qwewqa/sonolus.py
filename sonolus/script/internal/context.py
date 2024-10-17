from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

from sonolus.backend.blocks import BlockData
from sonolus.backend.ir import IRConst, IRStmt
from sonolus.backend.place import Block, BlockPlace, TempBlock
from sonolus.script.internal.value import Value

context_var = ContextVar("context_var", default=None)


class ContextEdge:
    target: Context
    condition: float | None
    statements: list[IRStmt]


class Context:
    statements: list[IRStmt]
    test: IRStmt = IRConst(0)
    outgoing: set[ContextEdge]
    blocks: type[Block]
    callback: str
    used_names: dict[str, int]
    # scope: "Scope"
    rom: ReadOnlyMemory
    merged_variables: dict[str, Value]
    live: bool

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


def ctx() -> Context | None:
    return context_var.get()


def set_ctx(value: Context | None):
    return context_var.set(value)


@contextmanager
def with_ctx(value: Context | None):
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
