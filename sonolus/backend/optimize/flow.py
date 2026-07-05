from __future__ import annotations

import textwrap
from collections import deque
from collections.abc import Iterator

from sonolus.backend.ir import IRConst, IRExpr, IRStmt
from sonolus.backend.place import SSAPlace, TempBlock


class FlowEdge:
    src: BasicBlock
    dst: BasicBlock
    cond: float | int | None

    def __init__(self, src: BasicBlock, dst: BasicBlock, cond: float | None = None):
        self.src = src
        self.dst = dst
        self.cond = cond


class BasicBlock:
    phis: dict[SSAPlace | TempBlock, dict[BasicBlock, SSAPlace]]
    statements: list[IRStmt]
    test: IRExpr
    incoming: set[FlowEdge]
    outgoing: set[FlowEdge]

    def __init__(
        self,
        *,
        phi: dict[SSAPlace, dict[BasicBlock, SSAPlace]] | None = None,
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

    def connect_to(self, other: BasicBlock, cond: int | float | None = None):
        edge = FlowEdge(self, other, cond)
        self.outgoing.add(edge)
        other.incoming.add(edge)


def _edge_sort_key(edge: FlowEdge):
    return (edge.cond is None, edge.cond)


def _ordered_edges(outgoing):
    # Sorting a 0/1-element set is a no-op on order but still allocates a list and
    # invokes the key; ~90% of pre-cleanup blocks have a single successor, so skip
    # the sort for them.
    if len(outgoing) <= 1:
        return outgoing
    return sorted(outgoing, key=_edge_sort_key)


def traverse_cfg_preorder(block: BasicBlock) -> Iterator[BasicBlock]:
    visited = set()
    queue = deque([block])
    while queue:
        block = queue.popleft()
        if block in visited:
            continue
        visited.add(block)
        yield block
        for edge in _ordered_edges(block.outgoing):
            queue.append(edge.dst)


def traverse_cfg_postorder(block: BasicBlock) -> Iterator[BasicBlock]:
    # Iterative DFS postorder (an explicit stack, so arbitrarily deep CFGs -- e.g.
    # unrolled large-container code -- never overflow the Python call stack). Order
    # contract (goldens and block numbering depend on it): children visited in
    # ``(cond is None, cond)`` sorted edge order, each node yielded after its
    # subtrees, leaves yielded on discovery, and successors marked visited at
    # discovery time (``block`` pre-marked) so shared successors resolve
    # deterministically.
    visited = {block}
    stack = [(block, iter(_ordered_edges(block.outgoing)))]
    while stack:
        node, edges = stack[-1]
        descended = False
        for edge in edges:
            dst = edge.dst
            if dst in visited:
                continue
            visited.add(dst)
            if dst.outgoing:
                stack.append((dst, iter(_ordered_edges(dst.outgoing))))
                descended = True
                break
            else:
                yield dst
        if not descended:
            yield node
            stack.pop()


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
                for cond, target in sorted(tgt.items(), key=lambda x: (x[0] is None, x[0])):
                    lines.append(
                        f"{index}_ --> |{pre(fmt([cond if cond is not None else 'default']))}| {block_indexes[target]}"
                    )
    lines.append("Exit([Exit])")

    body = textwrap.indent("\n".join(lines), "    ")
    return f"graph\n{body}"


def cfg_to_text(entry: BasicBlock) -> str:
    def indent(iterable, prefix="  "):
        for line in iterable:
            yield f"{prefix}{line}"

    block_indexes = {block: i for i, block in enumerate(traverse_cfg_reverse_postorder(entry))}

    def format_phis(phis):
        for dst, phi_srcs in phis.items():
            srcs = ", ".join(
                f"{block_indexes.get(src_block, '<dead>')}: {src_place}"
                for src_block, src_place in sorted(phi_srcs.items(), key=lambda x: block_indexes.get(x[0]))
            )
            yield f"{dst} := phi({srcs})\n"

    def format_statements(statements):
        for stmt in statements:
            yield f"{stmt}\n"

    def format_outgoing(outgoing_edges, test, indexes):
        outgoing = {edge.cond: edge.dst for edge in outgoing_edges}
        match outgoing:
            case {**other} if not other:
                yield "goto exit\n"
            case {None: target, **other} if not other:
                yield f"goto {indexes[target]}\n"
            case {0: f_branch, None: t_branch, **other} if not other:
                yield f"goto {indexes[t_branch]} if {test} else {indexes[f_branch]}\n"
            case dict() as tgt:
                yield f"goto when {test}\n"
                yield from indent(
                    f"{('default' if cond is None else str(cond))} -> {indexes[target]}\n"
                    for cond, target in sorted(tgt.items(), key=lambda x: (x[0] is None, x[0]))
                )

    def format_blocks():
        for block, index in block_indexes.items():
            yield f"{index}:\n"
            yield from indent(format_phis(block.phis))
            yield from indent(format_statements(block.statements))
            yield from indent(format_outgoing(block.outgoing, block.test, block_indexes))

    return "".join(format_blocks())
