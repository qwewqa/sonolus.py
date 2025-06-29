import textwrap
from dataclasses import dataclass, field

from sonolus.backend.ops import Op

type EngineNode = ConstantNode | FunctionNode


@dataclass(slots=True)
class ConstantNode:
    value: float
    _hash: int = field(init=False, repr=False)

    def __post_init__(self):
        self._hash = hash(self.value)

    def __hash__(self):
        return hash(self.value)


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
                    textwrap.indent('\n'.join(format_engine_node(arg) for arg in node.args), '  ')
                }\n)"
    else:
        raise ValueError(f"Invalid engine node: {node}")
