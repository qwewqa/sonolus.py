import textwrap
from collections import deque
from collections.abc import Iterator
from typing import Self

from sonolus.backend.ir import IRConst, IRExpr, IRStmt
from sonolus.backend.place import SSAPlace, TempBlock


class FlowEdge:
    src: "BasicBlock"
    dst: "BasicBlock"
    cond: float | int | None

    def __init__(self, src: "BasicBlock", dst: "BasicBlock", cond: float | None = None):
        self.src = src
        self.dst = dst
        self.cond = cond


class BasicBlock:
    phis: dict[SSAPlace | TempBlock, dict[Self, SSAPlace]]
    statements: list[IRStmt]
    test: IRExpr
    incoming: set[FlowEdge]
    outgoing: set[FlowEdge]

    def __init__(
        self,
        *,
        phi: dict[SSAPlace, dict[Self, SSAPlace]] | None = None,
        statements: list[IRStmt] | None = None,
        test: IRExpr | None = None,
        incoming: set[FlowEdge] | None = None,
        outgoing: set[FlowEdge] | None = None,
    ):
        self.phis = phi or {}
        self.statements = statements or []
        self.test = test or IRConst(0)
        self.incoming = incoming or set()
        self.outgoing = outgoing or set()

    def connect_to(self, other: "BasicBlock", cond: int | float | None = None):
        edge = FlowEdge(self, other, cond)
        self.outgoing.add(edge)
        other.incoming.add(edge)


def traverse_cfg_preorder(block: BasicBlock) -> Iterator[BasicBlock]:
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


def traverse_cfg_postorder(block: BasicBlock) -> Iterator[BasicBlock]:
    visited = set()

    def dfs(current: BasicBlock):
        if current in visited:
            return
        visited.add(current)
        for edge in sorted(current.outgoing, key=lambda e: (e.cond is None, e.cond)):
            yield from dfs(edge.dst)
        yield current

    yield from dfs(block)


def traverse_cfg_reverse_postorder(block: BasicBlock) -> Iterator[BasicBlock]:
    yield from reversed(list(traverse_cfg_postorder(block)))


def cfg_to_mermaid(entry: BasicBlock):
    def pre(s: str):
        return "\"<pre style='text-align: left;'>" + s.replace("\n", "<br/>") + '</pre>"'

    def fmt(nodes):
        if nodes:
            return "\n".join(str(n) for n in nodes)
        else:
            return "{}"

    block_indexes = {block: i for i, block in enumerate(traverse_cfg_reverse_postorder(entry))}

    lines = ["Entry([Entry]) --> 0"]
    for block, index in block_indexes.items():
        lines.append(
            f"{index}[{
                pre(
                    fmt(
                        [
                            f'#{index}',
                            *(
                                f'{dst} := phi({
                                    ", ".join(
                                        f"{block_indexes.get(src_block, '<dead>')}: {src_place}"
                                        for src_block, src_place in sorted(
                                            phis.items(), key=lambda x: block_indexes.get(x[0])
                                        )
                                    )
                                })'
                                for dst, phis in block.phis.items()
                            ),
                            *block.statements,
                        ]
                    )
                )
            }]"
        )

        outgoing = {edge.cond: edge.dst for edge in block.outgoing}
        match outgoing:
            case {**other} if not other:
                lines.append(f"{index} --> Exit")
            case {None: target, **other} if not other:
                lines.append(f"{index} --> {block_indexes[target]}")
            case {0: f_branch, None: t_branch, **other} if not other:
                lines.append(f"{index}_{{{{{pre(fmt([block.test]))}}}}}")
                lines.append(f"{index} --> {index}_")
                lines.append(f"{index}_ --> |true| {block_indexes[t_branch]}")
                lines.append(f"{index}_ --> |false| {block_indexes[f_branch]}")
            case dict() as tgt:
                lines.append(f"{index}_{{{{{pre(fmt([block.test]))}}}}}")
                lines.append(f"{index} --> {index}_")
                for cond, target in tgt.items():
                    lines.append(
                        f"{index}_ --> |{pre(fmt([cond if cond is not None else 'default']))}| {block_indexes[target]}"
                    )
    lines.append("Exit([Exit])")

    body = textwrap.indent("\n".join(lines), "    ")
    return f"graph\n{body}"
