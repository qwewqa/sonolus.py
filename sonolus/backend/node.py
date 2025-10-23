import textwrap
from typing import NamedTuple

from sonolus.backend.ops import Op

type EngineNode = int | float | FunctionNode


class FunctionNode(NamedTuple):
    func: Op
    args: tuple[EngineNode, ...]


def format_engine_node(node: EngineNode) -> str:
    if isinstance(node, FunctionNode):
        match len(node.args):
            case 0:
                return f"{node.func.name}()"
            case 1:
                return f"{node.func.name}({format_engine_node(node.args[0])})"
            case _:
                return f"{node.func.name}(\n{
                    textwrap.indent('\n'.join(format_engine_node(arg) for arg in node.args), '  ')
                }\n)"
    else:
        return str(node)
