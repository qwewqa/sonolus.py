from collections import deque

from sonolus.backend.flow import BasicBlock, traverse_cfg_preorder
from sonolus.backend.ir import IRConst
from sonolus.backend.passes import CompilerPass


class UnreachableCodeElimination(CompilerPass):
    def run(self, entry: BasicBlock) -> BasicBlock:
        original_blocks = [*traverse_cfg_preorder(entry)]
        queue = deque([entry])
        visited = set()
        while queue:
            block = queue.popleft()
            if block in visited:
                continue
            visited.add(block)
            match block.test:
                case IRConst(value=value):
                    block.test = IRConst(0)
                    taken_edge = next(
                        (edge for edge in block.outgoing if edge.cond == value),
                        None,
                    ) or next((edge for edge in block.outgoing if edge.cond is None), None)
                    assert not block.outgoing or taken_edge
                    block.outgoing.clear()
                    if taken_edge:
                        block.outgoing.add(taken_edge)
                        queue.append(taken_edge.dst)
                case _:
                    for edge in block.outgoing:
                        queue.append(edge.dst)
        for block in original_blocks:
            if block not in visited:
                for edge in block.outgoing:
                    edge.dst.incoming.remove(edge)
        return entry
