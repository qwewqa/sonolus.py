from sonolus.backend.ir import IRConst, IRGet, IRPureInstr, IRSet
from sonolus.backend.ops import Op
from sonolus.backend.optimize.flow import BasicBlock, traverse_cfg_preorder
from sonolus.backend.optimize.passes import CompilerPass


class CoalesceFlow(CompilerPass):
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
                if not block.statements and not block.phis and not next_block.phis:
                    for edge in block.incoming:
                        edge.dst = next_block
                        next_block.incoming.add(edge)
                    for edge in block.outgoing:  # There should be exactly one
                        next_block.incoming.remove(edge)
                    if block is entry:
                        entry = next_block
                continue
            for p, args in next_block.phis.items():
                if block not in args:
                    continue
                block.statements.append(IRSet(p, IRGet(args[block])))
            block.statements.extend(next_block.statements)
            block.test = next_block.test
            block.outgoing = next_block.outgoing
            for edge in block.outgoing:
                edge.src = block
                dst = edge.dst
                for args in dst.phis.values():
                    if next_block in args:
                        args[block] = args.pop(next_block)
            processed.add(next_block)
            queue.extend(edge.dst for edge in block.outgoing)
            processed.remove(block)
            queue.append(block)
        return entry


class RewriteToSwitch(CompilerPass):
    """Rewrite if-else chains to switch statements.

    Note that this needs inlining (and dead code elimination) to be run first to really do anything useful.
    """

    def run(self, entry: BasicBlock) -> BasicBlock:
        self.ifs_to_switch(entry)
        self.combine_blocks(entry)
        return entry

    def ifs_to_switch(self, entry: BasicBlock):
        for block in traverse_cfg_preorder(entry):
            if len(block.outgoing) != 2 or {edge.cond for edge in block.outgoing} != {None, 0}:
                continue
            test = block.test
            if not isinstance(test, IRPureInstr) or test.op != Op.Equal:
                continue
            assert len(test.args) == 2
            if isinstance(test.args[0], IRConst):
                const, other = test.args
            elif isinstance(test.args[1], IRConst):
                other, const = test.args
            else:
                continue
            block.test = other
            for edge in block.outgoing:
                if edge.cond is None:
                    edge.cond = const.value
                else:
                    edge.cond = None

    def combine_blocks(self, entry: BasicBlock):
        queue = [entry]
        processed = set()
        while queue:
            block = queue.pop()
            if block in processed:
                continue
            processed.add(block)
            queue.extend(edge.dst for edge in block.outgoing)

            default_edge = next((edge for edge in block.outgoing if edge.cond is None), None)
            if default_edge is None:
                continue

            next_block = default_edge.dst
            if (
                len(next_block.incoming) > 1
                or next_block.statements
                or next_block.phis
                or block.test != next_block.test
                or block is next_block
                or next_block is entry
            ):
                continue

            outgoing_by_cond = {edge.cond: edge for edge in block.outgoing}
            assert len(outgoing_by_cond) == len(block.outgoing)
            outgoing_by_cond.pop(None)
            for edge in next_block.outgoing:
                if edge.cond in outgoing_by_cond:
                    # In practice this edge should be unreachable
                    continue
                outgoing_by_cond[edge.cond] = edge
                edge.src = block
                for args in edge.dst.phis.values():
                    if next_block in args:
                        args[block] = args.pop(next_block)
            block.outgoing = set(outgoing_by_cond.values())
            processed.add(next_block)
            queue.append(block)
            processed.remove(block)
