import textwrap
from dataclasses import dataclass

from sonolus.backend.ops import Op

type EngineNode = ConstantNode | FunctionNode


@dataclass
class ConstantNode:
    value: float

    def __hash__(self):
        return hash(self.value)


@dataclass
class FunctionNode:
    func: Op
    args: list[EngineNode]

    def __hash__(self):
        return hash((self.func, tuple(self.args)))


def format_engine_node(node: EngineNode) -> str:
    if isinstance(node, ConstantNode):
        return str(node.value)
    elif isinstance(node, FunctionNode):
        match len(node.args):
            case 0:
                return f"{node.func.name}()"
            case 1:
                return f"{node.func.name}({format_engine_node(node.args[0])})"
            case _:
                return f"{node.func.name}(\n{
                    textwrap.indent("\n".join(format_engine_node(arg) for arg in node.args), "  ")
                }\n)"
    else:
        raise ValueError(f"Invalid engine node: {node}")
