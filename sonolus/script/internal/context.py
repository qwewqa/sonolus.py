from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

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
    # rom: "ReadOnlyMemory"
    merged_variables: dict[str, Value]
    live: bool

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
