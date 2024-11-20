from collections import deque

from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet, IRStmt
from sonolus.backend.optimize.flow import BasicBlock, traverse_cfg_preorder
from sonolus.backend.optimize.passes import CompilerPass
from sonolus.backend.place import BlockPlace, SSAPlace, TempBlock

type HasLiveness = SSAPlace | TempBlock


class LivenessAnalysis(CompilerPass):
    def destroys(self) -> set[CompilerPass]:
        return set()

    def run(self, entry: BasicBlock) -> BasicBlock:
        self.preprocess(entry)
        self.process(entry)
        return entry

    def preprocess(self, entry: BasicBlock):
        for block in traverse_cfg_preorder(entry):
            block.live_out = None
            block.live_in = set()
            block.live_phi_targets = set()
            block.array_defs_in = set()
            block.array_defs_out = None
            for statement in block.statements:
                statement.live = set()
                statement.visited = False
                statement.uses = self.get_uses(statement, set())
                statement.defs = self.get_defs(statement)
                statement.is_array_init = False  # True if this may be the first assignment to an array
                statement.array_defs = self.get_array_defs(statement)
            block.test.live = set()
            block.test.uses = self.get_uses(block.test, set())
        self.preprocess_arrays(entry)

    def process(self, entry: BasicBlock):
        queue = deque(self.get_exits(entry))
        if not queue:
            raise ValueError("Infinite loop detected")
        while queue:
            block = queue.popleft()
            updated_blocks = self.process_block(block)
            queue.extend(updated_blocks)

    def preprocess_arrays(self, entry: BasicBlock):
        queue = {entry}
        visited = set()
        while queue:
            block = queue.pop()
            array_defs = block.array_defs_in.copy()
            is_first_visit = block not in visited
            visited.add(block)
            for statement in block.statements:
                if statement.array_defs - array_defs:
                    statement.is_array_init = True
                    array_defs.update(statement.array_defs)
                else:
                    statement.is_array_init = False
            if is_first_visit or array_defs != block.array_defs_out:
                block.array_defs_out = array_defs
                for edge in block.outgoing:
                    queue.add(edge.dst)
                    edge.dst.array_defs_in.update(array_defs)

    def process_block(self, block: BasicBlock) -> list[BasicBlock]:
        if block.live_out is None:
            block.live_out = set()
        live: set[HasLiveness] = {
            place
            for place in block.live_out
            if not (isinstance(place, TempBlock) and place.size > 1 and place not in block.array_defs_out)
        }
        block.test.live.update(live)
        live.update(block.test.uses)
        for statement in reversed(block.statements):
            statement.live.update(live)
            if self.can_skip(statement, live):
                continue
            live.difference_update(statement.defs)
            if statement.is_array_init:
                live.difference_update(statement.array_defs)
            live.update(statement.uses)
        prev_sizes_by_block = {
            edge.src: len(edge.src.live_out) if edge.src.live_out is not None else -1 for edge in block.incoming
        }
        live_phi_targets = set()
        for target, args in block.phis.items():
            if target not in live:
                continue
            live.remove(target)
            for src_block, arg in args.items():
                if src_block.live_out is None:
                    src_block.live_out = set()
                src_block.live_out.add(arg)
            live_phi_targets.add(target)
        block.live_in = live
        block.live_phi_targets = live_phi_targets
        updated_blocks = []
        for edge in block.incoming:
            if edge.src.live_out is None:
                edge.src.live_out = set()
            edge.src.live_out.update(live)
            if len(edge.src.live_out) != prev_sizes_by_block[edge.src]:
                updated_blocks.append(edge.src)
        return updated_blocks

    def get_uses(
        self, stmt: IRStmt | BlockPlace | SSAPlace | TempBlock | int, uses: set[HasLiveness]
    ) -> set[HasLiveness]:
        match stmt:
            case IRPureInstr(op=_, args=args) | IRInstr(op=_, args=args):
                for arg in args:
                    self.get_uses(arg, uses)
            case IRGet(place=place):
                self.get_uses(place, uses)
            case IRSet(place=place, value=value):
                if isinstance(place, BlockPlace):
                    if not isinstance(place.block, TempBlock):
                        self.get_uses(place.block, uses)
                    self.get_uses(place.index, uses)
                self.get_uses(value, uses)
            case IRConst() | int():
                pass
            case BlockPlace(block=block, index=index, offset=_):
                self.get_uses(block, uses)
                self.get_uses(index, uses)
            case SSAPlace() | TempBlock():
                uses.add(stmt)
            case _:
                raise TypeError(f"Unexpected statement type: {type(stmt)}")
        return uses

    def get_defs(self, stmt: IRStmt | BlockPlace | SSAPlace | TempBlock | int) -> set[HasLiveness]:
        match stmt:
            case IRSet(place=place, value=_):
                match place:
                    case SSAPlace():
                        return {place}
                    case BlockPlace(block=TempBlock() as temp_block, index=_, offset=_) if temp_block.size == 1:
                        return {temp_block}
        return set()

    def get_array_defs(self, stmt: IRStmt | BlockPlace | SSAPlace | TempBlock | int) -> set[HasLiveness]:
        match stmt:
            case IRSet(place=place, value=_):
                match place:
                    case BlockPlace(block=TempBlock() as temp_block, index=_, offset=_) if temp_block.size > 1:
                        return {temp_block}
        return set()

    def can_skip(self, stmt: IRStmt, live: set[HasLiveness]) -> bool:
        match stmt:
            case IRSet(place=_, value=value):
                if isinstance(value, IRInstr) and value.op.side_effects:
                    return False
                defs = stmt.defs | stmt.array_defs
                return defs and not (defs & live)
        return False

    def get_exits(self, entry: BasicBlock) -> list[BasicBlock]:
        return [block for block in traverse_cfg_preorder(entry) if not block.outgoing]

    def __eq__(self, other):
        return isinstance(other, LivenessAnalysis)

    def __hash__(self):
        return hash(LivenessAnalysis)


def get_live(stmt: IRStmt) -> set[HasLiveness]:
    return stmt.live


def get_live_phi_targets(block: BasicBlock) -> set[HasLiveness]:
    return block.live_phi_targets
