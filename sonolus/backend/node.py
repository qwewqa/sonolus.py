import textwrap
from dataclasses import dataclass, field

from sonolus.backend.ops import Op

type EngineNode = int | float | FunctionNode


@dataclass(slots=True)
class FunctionNode:
    func: Op
    args: list[EngineNode]
    _hash: int = field(init=False, repr=False)

    def __post_init__(self):
        self._hash = hash((self.func, tuple(self.args)))

    def __hash__(self):
        return self._hash


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
