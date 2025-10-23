from threading import Lock
from typing import TypedDict

from sonolus.backend.node import EngineNode, FunctionNode


class ValueOutputNode(TypedDict):
    value: float


class FunctionOutputNode(TypedDict):
    func: str
    args: list[int]


class OutputNodeGenerator:
    nodes: list[ValueOutputNode | FunctionOutputNode]
    indexes: dict[EngineNode, int]
    lock: Lock

    def __init__(self):
        self.nodes = []
        self.indexes = {}
        self.lock = Lock()

    def add(self, node: EngineNode):
        with self.lock:
            return self._add(node)

    def _add(self, node: EngineNode):
        if node in self.indexes:
            return self.indexes[node]

        match node:
            case FunctionNode(func, args):
                arg_indexes = [self._add(arg) for arg in args]
                index = len(self.nodes)
                self.nodes.append({"func": func.value, "args": arg_indexes})
                self.indexes[node] = index
                return index
            case constant:
                index = len(self.nodes)
                self.nodes.append({"value": constant})
                self.indexes[node] = index
                return index

    def get(self):
        return self.nodes
