from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from functools import total_ordering
from typing import Any

from sonolus.backend.ir import IRNode, SSARef


@dataclass(eq=False)
class CFG:
    entry_node: CFGNode = None
    exit_node: CFGNode = None
    edges_by_from: dict[CFGNode, set[CFGEdge]] = field(
        default_factory=lambda: defaultdict(set)
    )
    edges_by_to: dict[CFGNode, set[CFGEdge]] = field(
        default_factory=lambda: defaultdict(set)
    )

    def add_edge(self, edge: CFGEdge, /):
        self.edges_by_from[edge.from_node].add(edge)
        self.edges_by_to[edge.to_node].add(edge)

    def remove_edge(self, edge: CFGEdge):
        self.edges_by_from[edge.from_node].discard(edge)
        self.edges_by_to[edge.to_node].discard(edge)

    def clear_to_edges(self, node: CFGNode):
        for edge in [*self.edges_by_to[node]]:
            self.remove_edge(edge)

    def clear_from_edges(self, node: CFGNode):
        for edge in [*self.edges_by_from[node]]:
            self.remove_edge(edge)

    def remove_node(self, node: CFGNode):
        self.clear_from_edges(node)
        self.clear_to_edges(node)

    def replace_node(self, old_node: CFGNode, new_node: CFGNode, /):
        for edge in [*self.edges_by_from[old_node]]:
            self.remove_edge(edge)
            self.add_edge(CFGEdge(new_node, edge.to_node, edge.condition))
            for phi in edge.to_node.phi:
                if old_node in phi.values:
                    phi.values[new_node] = phi.values[old_node]
                    del phi.values[old_node]
        for edge in [*self.edges_by_to[old_node]]:
            self.remove_edge(edge)
            self.add_edge(CFGEdge(edge.from_node, new_node, edge.condition))
        if old_node is self.entry_node:
            self.entry_node = new_node
            new_node.is_entry = True
        if old_node is self.exit_node:
            self.exit_node = new_node
            new_node.is_exit = True

    def remove_dead_nodes(self):
        live = set()
        queue = [self.entry_node]
        while queue:
            node = queue.pop()
            if node in live:
                continue
            live.add(node)
            for edge in self.edges_by_from[node]:
                queue.append(edge.to_node)
        for node in [*self.edges_by_from]:
            if node not in live and not node.is_entry and not node.is_exit:
                self.remove_node(node)


@dataclass(eq=False)
class CFGNode:
    body: list[IRNode]
    test: IRNode | None
    annotations: dict[Any, Any] = field(default_factory=dict)
    phi: list[Phi] = field(default_factory=list)
    is_entry: bool = False
    is_exit: bool = False


@dataclass
class Phi:
    target: SSARef
    values: dict[CFGNode, SSARef]

    def __str__(self):
        return (
            f"{self.target} <- PHI({', '.join(f'{v}' for v in self.values.values())})"
        )


@total_ordering
@dataclass(frozen=True)
class CFGEdge:
    from_node: CFGNode
    to_node: CFGNode
    condition: float | None = None

    def __lt__(self, other: CFGEdge) -> bool:
        if self.condition is None:
            return other.condition is not None
        if other.condition is None:
            return False
        return self.condition < other.condition
