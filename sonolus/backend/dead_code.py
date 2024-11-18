from sonolus.backend.flow import BasicBlock, traverse_cfg_preorder
from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRSet
from sonolus.backend.liveness import LivenessAnalysis, get_live, get_live_phi_targets
from sonolus.backend.passes import CompilerPass
from sonolus.backend.place import BlockPlace, SSAPlace, TempBlock


class UnreachableCodeElimination(CompilerPass):
    def run(self, entry: BasicBlock) -> BasicBlock:
        original_blocks = [*traverse_cfg_preorder(entry)]
        worklist = {entry}
        visited = set()
        while worklist:
            block = worklist.pop()
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
                    for edge in [*block.outgoing]:
                        if edge is not taken_edge:
                            edge.dst.incoming.remove(edge)
                            block.outgoing.remove(edge)
                    if taken_edge:
                        taken_edge.cond = None
                        block.outgoing.add(taken_edge)
                        worklist.add(taken_edge.dst)
                case _:
                    worklist.update(edge.dst for edge in block.outgoing)
        for block in original_blocks:
            if block not in visited:
                for edge in block.outgoing:
                    edge.dst.incoming.remove(edge)
            else:
                for args in block.phis.values():
                    for src_block in [*args]:
                        if src_block not in visited:
                            args.pop(src_block)
        return entry


class DeadCodeElimination(CompilerPass):
    def requires(self) -> set[CompilerPass]:
        return {LivenessAnalysis()}

    def preserves(self) -> set[CompilerPass] | None:
        return {LivenessAnalysis()}

    def run(self, entry: BasicBlock) -> BasicBlock:
        for block in traverse_cfg_preorder(entry):
            live_stmts = []
            for statement in block.statements:
                live = get_live(statement)
                match statement:
                    case IRSet(place=place, value=value):
                        is_live = not (
                            (isinstance(place, SSAPlace) and place not in live)
                            or (
                                isinstance(place, BlockPlace)
                                and isinstance(place.block, TempBlock)
                                and place.block not in live
                            )
                            or (isinstance(value, IRGet) and place == value.place)
                        )
                        if is_live:
                            live_stmts.append(statement)
                        elif isinstance(value, IRInstr) and value.op.side_effects:
                            live_stmts.append(value)
                            value.live = live
                    case other:
                        live_stmts.append(other)
            block.statements = live_stmts
            block.phis = {place: phi for place, phi in block.phis.items() if place in get_live_phi_targets(block)}
        return entry
