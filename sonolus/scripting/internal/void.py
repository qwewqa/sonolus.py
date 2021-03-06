from __future__ import annotations

from sonolus.backend.ir import IRNode
from sonolus.scripting.internal.statement import Statement
from sonolus.backend.evaluation import Scope


class Void(Statement):
    def __init__(self, node: IRNode | None = None):
        super().__init__()
        self.node = node

    def _evaluate_(self, scope: Scope):
        if self.node is not None:
            scope.add(self.node)
        return scope

    @classmethod
    def from_statement(cls, statement) -> Void:
        return cls()._set_parent_(statement)
