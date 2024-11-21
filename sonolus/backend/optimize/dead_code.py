from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet, IRStmt
from sonolus.backend.optimize.flow import BasicBlock, traverse_cfg_preorder
from sonolus.backend.optimize.liveness import HasLiveness, LivenessAnalysis, get_live, get_live_phi_targets
from sonolus.backend.optimize.passes import CompilerPass
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
    def run(self, entry: BasicBlock) -> BasicBlock:
        uses = set()
        defs = {}
        for block in traverse_cfg_preorder(entry):
            for statement in block.statements:
                self.handle_statement(statement, uses, defs)
            for target, args in block.phis.items():
                if target not in defs:
                    defs[target] = []
                defs[target].append(tuple(args.values()))
            self.update_uses(block.test, uses)

        queue = [*uses]
        while queue:
            val = queue.pop()
            if val not in defs:
                continue
            for stmt in defs[val]:
                if isinstance(stmt, tuple):
                    stmt_uses = stmt
                else:
                    stmt_uses = self.update_uses(stmt, set())
                for use in stmt_uses:
                    if use not in uses:
                        uses.add(use)
                        queue.append(use)

        for block in traverse_cfg_preorder(entry):
            live_stmts = []
            for statement in block.statements:
                match statement:
                    case IRSet(place=place, value=value):
                        is_live = not (
                            (isinstance(place, SSAPlace) and place not in uses)
                            or (
                                isinstance(place, BlockPlace)
                                and isinstance(place.block, TempBlock)
                                and place.block not in uses
                            )
                            or (isinstance(value, IRGet) and place == value.place)
                        )
                        if is_live:
                            live_stmts.append(statement)
                        elif isinstance(value, IRInstr) and value.op.side_effects:
                            live_stmts.append(value)
                    case other:
                        live_stmts.append(other)
            block.statements = live_stmts
            block.phis = {place: phi for place, phi in block.phis.items() if place in uses}
        return entry

    def handle_statement(
        self,
        stmt: IRStmt | BlockPlace | SSAPlace | TempBlock | int,
        uses: set[HasLiveness],
        defs: dict[HasLiveness, list[IRStmt | tuple[HasLiveness]]],
    ):
        if isinstance(stmt, IRSet):
            place = stmt.place
            value = stmt.value
            if isinstance(place, SSAPlace):
                if place not in defs:
                    defs[place] = []
                defs[place].append(stmt)
                if isinstance(value, IRInstr) and value.op.side_effects:
                    self.update_uses(value, uses)
            elif isinstance(place, BlockPlace) and isinstance(place.block, TempBlock):
                if place.block not in defs:
                    defs[place.block] = []
                defs[place.block].append(stmt)
                if isinstance(value, IRInstr) and value.op.side_effects:
                    self.update_uses(value, uses)
            else:
                self.update_uses(place, uses)
                self.update_uses(value, uses)
        else:
            self.update_uses(stmt, uses)

    def update_uses(
        self, stmt: IRStmt | BlockPlace | SSAPlace | TempBlock | int, uses: set[HasLiveness]
    ) -> set[HasLiveness]:
        match stmt:
            case IRPureInstr(op=_, args=args) | IRInstr(op=_, args=args):
                for arg in args:
                    self.update_uses(arg, uses)
            case IRGet(place=place):
                self.update_uses(place, uses)
            case IRSet(place=place, value=value):
                if isinstance(place, BlockPlace):
                    if not isinstance(place.block, TempBlock):
                        self.update_uses(place.block, uses)
                    self.update_uses(place.index, uses)
                self.update_uses(value, uses)
            case IRConst() | int():
                pass
            case BlockPlace(block=block, index=index, offset=_):
                self.update_uses(block, uses)
                self.update_uses(index, uses)
            case TempBlock() | SSAPlace():
                uses.add(stmt)
            case _:
                raise TypeError(f"Unexpected statement type: {type(stmt)}")
        return uses


class AdvancedDeadCodeElimination(CompilerPass):
    """Slower than regular DeadCodeElimination but can handle cases like definitions after the last use and so on."""

    def requires(self) -> set[CompilerPass]:
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
