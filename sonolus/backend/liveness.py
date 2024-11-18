from collections import deque

from sonolus.backend.flow import BasicBlock, traverse_cfg_preorder
from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet, IRStmt
from sonolus.backend.passes import CompilerPass
from sonolus.backend.place import BlockPlace, SSAPlace, TempBlock

type HasLiveness = SSAPlace | TempBlock


class LivenessAnalysis(CompilerPass):
    def destroys(self) -> set[CompilerPass]:
        return set()

    def run(self, entry: BasicBlock) -> BasicBlock:
        self.preprocess(entry)
        self.process(entry)
        self.process_arrays(entry)
        return entry

    def process(self, entry: BasicBlock):
        queue = deque(self.get_exits(entry))
        if not queue:
            raise ValueError("Infinite loop detected")
        while queue:
            block = queue.popleft()
            updated_blocks = self.process_block(block)
            queue.extend(updated_blocks)

    def process_arrays(self, entry: BasicBlock):
        # With arrays, we can't assume that an assignment will render previous assignments dead.
        # Before this function is run, arrays are treated as live at a statement as long as they are read from
        # at some future point.
        # This function will mark arrays as dead if they could not have been assigned to yet.
        queue = deque([entry])
        while queue:
            block = queue.popleft()
            if block.live_arrays_in is None:
                block.live_arrays_in = set()
            live_arrays_in = block.live_arrays_in.copy()
            for statement in block.statements:
                live_arrays_in.update(self.get_array_defs(statement))
            updated_blocks = []
            for edge in block.outgoing:
                if edge.dst.live_arrays_in is None:
                    prev_size = -1
                    edge.dst.live_arrays_in = set()
                else:
                    prev_size = len(edge.dst.live_arrays_in)
                edge.dst.live_arrays_in.update(live_arrays_in)
                if len(edge.dst.live_arrays_in) != prev_size:
                    updated_blocks.append(edge.dst)
            queue.extend(updated_blocks)

        for block in traverse_cfg_preorder(entry):
            live_arrays_in = block.live_arrays_in
            for statement in block.statements:
                if not self.can_skip(statement, statement.live):
                    live_arrays_in.update(self.get_array_defs(statement))
                statement.live = {
                    place
                    for place in statement.live
                    if not (isinstance(place, TempBlock) and place.size != 1 and place not in live_arrays_in)
                }

    def preprocess(self, entry: BasicBlock):
        for block in traverse_cfg_preorder(entry):
            block.live_out = None
            block.live_in = None
            block.live_phi_targets = None
            block.live_arrays_in = None

    def process_block(self, block: BasicBlock) -> list[BasicBlock]:
        if block.live_out is None:
            block.live_out = set()
        live: set[HasLiveness] = block.live_out.copy()
        block.test.live = live.copy()
        live.update(self.get_uses(block.test))
        for statement in reversed(block.statements):
            statement.live = live.copy()
            if self.can_skip(statement, live):
                continue
            live.difference_update(self.get_defs(statement))
            live.update(self.get_uses(statement))
        live_phi_targets = set()
        for target, args in block.phis.items():
            if target not in live:
                continue
            live.difference_update({target})
            live.update(args.values())
            live_phi_targets.add(target)
        block.live_in = live.copy()
        block.live_phi_targets = live_phi_targets
        updated_blocks = []
        for edge in block.incoming:
            if edge.src.live_out is None:
                prev_size = -1
                edge.src.live_out = set()
            else:
                prev_size = len(edge.src.live_out)
            edge.src.live_out.update(live)
            if len(edge.src.live_out) != prev_size:
                updated_blocks.append(edge.src)
        return updated_blocks

    def get_uses(self, stmt: IRStmt | BlockPlace | SSAPlace | TempBlock | int) -> set[HasLiveness]:
        uses = set()
        match stmt:
            case IRPureInstr(op=_, args=args) | IRInstr(op=_, args=args):
                for arg in args:
                    uses.update(self.get_uses(arg))
            case IRGet(place=place):
                uses.update(self.get_uses(place))
            case IRSet(place=place, value=value):
                if isinstance(place, BlockPlace):
                    if not isinstance(place.block, TempBlock):
                        uses.update(self.get_uses(place.block))
                    uses.update(self.get_uses(place.index))
                uses.update(self.get_uses(value))
            case IRConst() | int():
                pass
            case BlockPlace(block=block, index=index, offset=_):
                uses.update(self.get_uses(block))
                uses.update(self.get_uses(index))
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
                defs = self.get_defs(stmt) | self.get_array_defs(stmt)
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
