from collections import deque
from collections.abc import Iterator

from sonolus.backend.ir import IRConst, IRExpr, IRStmt


class FlowEdge:
    src: "BasicBlock"
    dst: "BasicBlock"
    cond: IRExpr | None
    statements: list[IRStmt]

    def __init__(
        self, src: "BasicBlock", dst: "BasicBlock", cond: IRExpr | None = None, statements: list[IRStmt] | None = None
    ):
        self.src = src
        self.dst = dst
        self.cond = cond
        self.statements = statements or []


class BasicBlock:
    statements: list[IRStmt]
    test: IRExpr
    incoming: set[FlowEdge]
    outgoing: set[FlowEdge]

    def __init__(
        self,
        statements: list[IRStmt] | None = None,
        test: IRExpr | None = None,
        incoming: set[FlowEdge] | None = None,
        outgoing: set[FlowEdge] | None = None,
    ):
        self.statements = statements or []
        self.test = test or IRConst(1)
        self.incoming = incoming or set()
        self.outgoing = outgoing or set()

    def connect_to(self, other: "BasicBlock", cond: IRExpr | None = None):
        edge = FlowEdge(self, other, cond)
        self.outgoing.add(edge)
        other.incoming.add(edge)


def traverse_preorder(block: BasicBlock) -> Iterator[BasicBlock]:
    visited = set()
    queue = deque([block])
    while queue:
        block = queue.popleft()
        if block in visited:
            continue
        visited.add(block)
        yield block
        for edge in sorted(block.outgoing, key=lambda e: (e.cond is None, e.cond)):
            queue.append(edge.dst)
