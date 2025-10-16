from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet
from sonolus.backend.ops import Op
from sonolus.backend.optimize.flow import BasicBlock, FlowEdge, traverse_cfg_preorder
from sonolus.backend.optimize.passes import CompilerPass, OptimizerConfig
from sonolus.backend.place import BlockPlace, SSAPlace, TempBlock


class CoalesceFlow(CompilerPass):
    def run(self, entry: BasicBlock, config: OptimizerConfig) -> BasicBlock:
        queue = [entry]
        processed = set()
        while queue:
            block = queue.pop()
            if block in processed:
                continue
            processed.add(block)
            for edge in block.outgoing:
                while True:
                    dst = edge.dst
                    if dst.phis or dst.statements or len(dst.outgoing) != 1 or dst is block or dst is entry:
                        break
                    next_dst = next(iter(dst.outgoing)).dst
                    if next_dst.phis:
                        break
                    dst.incoming.remove(edge)
                    if not dst.incoming:
                        for dst_edge in dst.outgoing:
                            dst_edge.dst.incoming.remove(dst_edge)
                        processed.add(dst)
                    edge.dst = next_dst
                    next_dst.incoming.add(edge)
                    if dst is edge.dst:
                        break
            default_edge = next((edge for edge in block.outgoing if edge.cond is None), None)
            if default_edge is not None:
                for edge in [*block.outgoing]:
                    if edge is default_edge:
                        continue
                    if edge.dst is default_edge.dst:
                        block.outgoing.remove(edge)
                        edge.dst.incoming.remove(edge)
            if len(block.outgoing) != 1:
                queue.extend(edge.dst for edge in block.outgoing)
                continue
            next_block = next(iter(block.outgoing)).dst
            if next_block is block or next_block is entry:
                continue
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
                    # This is the only predecessor to the block, so it must be a phi argument
                    raise ValueError("Missing phi argument")
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


class CombineExitBlocks(CompilerPass):
    def run(self, entry: BasicBlock, config: OptimizerConfig) -> BasicBlock:
        first_exit_block = None
        for block in traverse_cfg_preorder(entry):
            if not block.outgoing and not block.phis and not block.statements:
                if first_exit_block is None:
                    first_exit_block = block
                else:
                    for edge in [*block.incoming]:
                        edge.dst = first_exit_block
                        first_exit_block.incoming.add(edge)
                    block.incoming.clear()
        return entry


class CoalesceSmallConditionalBlocks(CompilerPass):
    def run(self, entry: BasicBlock, config: OptimizerConfig) -> BasicBlock:
        queue = [entry]
        processed = set()
        while queue:
            block = queue.pop()
            if block.phis:
                raise RuntimeError("SSA form is not supported in this pass")
            if block in processed:
                continue
            processed.add(block)
            while len(block.outgoing) == 1:
                next_edge = next(iter(block.outgoing))
                next_block = next_edge.dst
                if len(next_block.statements) <= 1:
                    next_block.incoming.remove(next_edge)
                    block.test = next_block.test
                    block.outgoing = {FlowEdge(src=block, dst=edge.dst, cond=edge.cond) for edge in next_block.outgoing}
                    block.statements.extend(next_block.statements)
                    for edge in block.outgoing:
                        edge.dst.incoming.add(edge)
                else:
                    break
            queue.extend(
                edge.dst
                for edge in sorted(block.outgoing, key=lambda e: (e.cond is not None, e.cond))
                if edge.dst not in processed
            )

        reachable_blocks = set(traverse_cfg_preorder(entry))
        for block in traverse_cfg_preorder(entry):
            block.incoming = {edge for edge in block.incoming if edge.src in reachable_blocks}
        return entry


class RewriteToSwitch(CompilerPass):
    """Rewrite if-else chains to switch statements.

    Note that this needs inlining (and dead code elimination) to be run first to really do anything useful.
    """

    def run(self, entry: BasicBlock, config: OptimizerConfig) -> BasicBlock:
        self.ifs_to_switch(entry)
        self.combine_blocks(entry)
        self.remove_unreachable(entry)
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
                    # This edge is unreachable since an equivalent edge would have been taken
                    edge.dst.incoming.remove(edge)
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

    def remove_unreachable(self, entry: BasicBlock):
        reachable = {*traverse_cfg_preorder(entry)}
        for block in traverse_cfg_preorder(entry):
            block.incoming = {edge for edge in block.incoming if edge.src in reachable}
            block.outgoing = {edge for edge in block.outgoing if edge.dst in reachable}


class NormalizeSwitch(CompilerPass):
    """Normalize branches like cond -> case a, case a + b, case a + 2b to ((cond - a) / b) -> case 0, case 1, case 2."""

    def run(self, entry: BasicBlock, config: OptimizerConfig) -> BasicBlock:
        for block in traverse_cfg_preorder(entry):
            cases = {edge.cond for edge in block.outgoing}
            if len(cases) <= 2:
                continue
            assert None in cases, "Non-terminal blocks should always have a default edge"
            cases.remove(None)
            offset, stride = self.get_offset_stride(cases)
            if offset is None or (offset == 0 and stride == 1):
                continue
            for edge in block.outgoing:
                if edge.cond is None:
                    continue
                edge.cond = (edge.cond - offset) // stride
            if offset != 0:
                block.test = IRPureInstr(Op.Subtract, [block.test, IRConst(offset)])
            if stride != 1:
                block.test = IRPureInstr(Op.Divide, [block.test, IRConst(stride)])
        return entry

    def get_offset_stride(self, cases: set[int]) -> tuple[int | None, int | None]:
        cases = sorted(cases)
        offset = cases[0]
        stride = cases[1] - offset
        if int(offset) != offset or int(stride) != stride:
            return None, None
        for i, case in enumerate(cases[2:], 2):
            if case != offset + i * stride:
                return None, None
        return offset, stride


class RemoveRedundantArguments(CompilerPass):
    def run(self, entry: BasicBlock, config: OptimizerConfig) -> BasicBlock:
        for block in traverse_cfg_preorder(entry):
            block.statements = [self.update_statement(stmt) for stmt in block.statements]
            block.test = self.update_statement(block.test)
        return entry

    def update_statement(self, stmt):
        match stmt:
            case IRPureInstr() | IRPureInstr():
                op = stmt.op
                args = stmt.args
                match op:
                    case Op.Add:
                        args = [arg for arg in args if not (isinstance(arg, IRConst) and arg.value == 0)]
                        if len(args) == 1:
                            return args[0]
                    case Op.Subtract:
                        args = [
                            args[0],
                            *(arg for arg in args[1:] if not (isinstance(arg, IRConst) and arg.value == 0)),
                        ]
                        if len(args) == 1:
                            return args[0]
                    case Op.Multiply:
                        args = [arg for arg in args if not (isinstance(arg, IRConst) and arg.value == 1)]
                        if len(args) == 1:
                            return args[0]
                    case Op.Divide:
                        args = [
                            args[0],
                            *(arg for arg in args[1:] if not (isinstance(arg, IRConst) and arg.value == 1)),
                        ]
                        if len(args) == 1:
                            return args[0]
                return type(stmt)(op=op, args=[self.update_statement(arg) for arg in args])
            case IRSet(place=place, value=value):
                return IRSet(place=place, value=self.update_statement(value))
            case _:
                return stmt


class RenumberVars(CompilerPass):
    def run(self, entry: BasicBlock, config: OptimizerConfig) -> BasicBlock:
        numbers = {}
        for block in traverse_cfg_preorder(entry):
            for stmt in block.statements:
                if (
                    isinstance(stmt, IRSet)
                    and isinstance(stmt.place, BlockPlace)
                    and isinstance(stmt.place.block, TempBlock)
                    and stmt.place.block.size == 1
                    and stmt.place.block not in numbers
                ):
                    numbers[stmt.place.block] = len(numbers) + 1
        for block in traverse_cfg_preorder(entry):
            block.statements = [self.update_statement(stmt, numbers) for stmt in block.statements]
            block.test = self.update_statement(block.test, numbers)
        return entry

    def update_statement(self, stmt, numbers: dict[TempBlock, int]):
        match stmt:
            case IRConst():
                return stmt
            case IRPureInstr(op=op, args=args):
                return IRPureInstr(op=op, args=[self.update_statement(arg, numbers) for arg in args])
            case IRInstr(op=op, args=args):
                return IRInstr(op=op, args=[self.update_statement(arg, numbers) for arg in args])
            case IRGet(place=place):
                return IRGet(place=self.update_statement(place, numbers))
            case IRSet(place=SSAPlace() as place, value=value):
                return IRSet(place=place, value=self.update_statement(value, numbers))
            case IRSet(place=place, value=value):
                return IRSet(place=self.update_statement(place, numbers), value=self.update_statement(value, numbers))
            case BlockPlace(block=block, index=index, offset=offset):
                return BlockPlace(
                    block=self.update_statement(block, numbers),
                    index=self.update_statement(index, numbers),
                    offset=offset,
                )
            case SSAPlace():
                return stmt
            case TempBlock() as b if b in numbers:
                return TempBlock(f"v{numbers[b]}", size=1)
            case int() | float() | TempBlock():
                return stmt
            case _:
                raise TypeError(f"Unexpected statement: {stmt}")
