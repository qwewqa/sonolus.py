from typing import TypedDict

from sonolus.backend.node import ConstantNode, EngineNode, FunctionNode


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
        if node in self.indexes:
            return self.indexes[node]

        match node:
            case ConstantNode(value):
                index = len(self.nodes)
                self.nodes.append({"value": value})
                self.indexes[node] = index
                return index
            case FunctionNode(func, args):
                arg_indexes = [self.add(arg) for arg in args]
                index = len(self.nodes)
                self.nodes.append({"func": func.value, "args": arg_indexes})
                self.indexes[node] = index
                return index
            case _:
                raise ValueError("Invalid node")

    def get(self):
        return self.nodes
