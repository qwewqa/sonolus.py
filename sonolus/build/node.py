from typing import TypedDict

from sonolus.backend.node import EngineNode, FunctionNode
from sonolus.backend.ops import Op

# Precomputed Op -> emitted name: Op.value routes through enum's DynamicClassAttribute
# descriptor on every access, which is measurable at the ~165k accesses of a large build.
_OP_VALUE = {op: op.value for op in Op}


class ValueOutputNode(TypedDict):
    value: float


class FunctionOutputNode(TypedDict):
    func: str
    args: list[int]


class OutputNodeGenerator:
    nodes: list[ValueOutputNode | FunctionOutputNode]
    indexes: dict[EngineNode, int]

    def __init__(self):
        self.nodes = []
        self.indexes = {}

    def add(self, node: EngineNode):
        existing = self.indexes.get(node)
        if existing is not None:
            return existing

        if type(node) is FunctionNode:
            arg_indexes = [self.add(arg) for arg in node.args]
            index = len(self.nodes)
            self.nodes.append({"func": _OP_VALUE[node.func], "args": arg_indexes})
            self.indexes[node] = index
            return index
        index = len(self.nodes)
        self.nodes.append({"value": node})
        self.indexes[node] = index
        return index

    def get(self):
        return self.nodes
