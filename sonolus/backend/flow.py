import textwrap
from collections import deque
from collections.abc import Iterator

from sonolus.backend.ir import IRConst, IRExpr, IRStmt


class FlowEdge:
    src: "BasicBlock"
    dst: "BasicBlock"
    cond: IRExpr | None

    def __init__(self, src: "BasicBlock", dst: "BasicBlock", cond: IRExpr | None = None):
        self.src = src
        self.dst = dst
        self.cond = cond


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


def cfg_to_mermaid(block: BasicBlock):
    def pre(s: str):
        return '"<pre style=\'text-align: left;\'>' + s.replace("\n", "<br/>") + '</pre>"'

    def fmt(nodes):
        if nodes:
            return "\n".join(str(n) for n in nodes)
        else:
            return "{}"

    block_indexes = {block: i for i, block in enumerate(traverse_preorder(block))}

    lines = ["Entry([Entry]) --> 0"]
    for block in traverse_preorder(block):
        index = block_indexes[block]
        lines.append(f"{index}[{pre(fmt([f'#{index}'] + block.statements))}]")

        outgoing = {edge.cond: edge.dst for edge in block.outgoing}
        match outgoing:
            case {**other} if not other:
                lines.append(f"{index} --> Exit")
            case {None: target, **other} if not other:
                lines.append(f"{index} --> {block_indexes[target]}")
            case {0: f_branch, None: t_branch, **other} if not other:
                lines.append(f"{index}_{{{pre(fmt([block.test]))}}}")
                lines.append(f"{index} --> {index}_")
                lines.append(f"{index}_ --> |true| {block_indexes[t_branch]}")
                lines.append(f"{index}_ --> |false| {block_indexes[f_branch]}")
            case dict() as tgt:
                lines.append(f"{index}_{{{{{pre(fmt([block.test]))}}}}}")
                lines.append(f"{index} --> {index}_")
                for cond, target in tgt.items():
                    if cond is None:
                        cond = "default"
                    lines.append(f"{index}_ --> |{pre(fmt([cond]))}| {block_indexes[target]}")
    lines.append("Exit([Exit])")

    body = textwrap.indent("\n".join(lines), "    ")
    return f"graph\n{body}"
