from sonolus.backend.flow import BasicBlock
from sonolus.optimize.passes import OptimizationPass


class CoalesceFlow(OptimizationPass):
    def run(self, entry: BasicBlock) -> BasicBlock:
        queue = [entry]
        processed = set()
        while queue:
            block = queue.pop()
            if block in processed:
                continue
            processed.add(block)
            if len(block.outgoing) != 1:
                queue.extend(edge.dst for edge in block.outgoing)
                continue
            next_block = next(iter(block.outgoing)).dst
            if len(next_block.incoming) != 1:
                queue.append(next_block)
                continue
            block.statements.extend(next_block.statements)
            block.test = next_block.test
            for edge in next_block.outgoing:
                edge.src = block
            block.outgoing = next_block.outgoing
            processed.add(next_block)
            queue.extend(edge.dst for edge in block.outgoing)
            processed.remove(block)
            queue.append(block)
        return entry
