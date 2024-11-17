from sonolus.backend.dominance import DominanceFrontiers, get_df, get_dom_children
from sonolus.backend.flow import BasicBlock, traverse_cfg_preorder
from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet, IRStmt
from sonolus.backend.passes import CompilerPass
from sonolus.backend.place import BlockPlace, SSAPlace, TempBlock


class ToSSA(CompilerPass):
    def requires(self) -> set[CompilerPass]:
        return {DominanceFrontiers()}

    def run(self, entry: BasicBlock) -> BasicBlock:
        defs = self.defs_to_blocks(entry)
        self.insert_phis(entry, defs)
        self.rename(entry, defs, {var: [] for var in defs}, {})
        return entry

    def rename(
        self,
        block: BasicBlock,
        defs: dict[TempBlock, set[BasicBlock]],
        ssa_places: dict[TempBlock, list[SSAPlace]],
        used: dict[str, int],
    ):
        original_ssa_place_lens = {var: len(ssa_places[var]) for var in defs}
        for var, args in [*block.phis.items()]:
            if isinstance(var, SSAPlace):
                continue
            ssa_places[var].append(self.get_new_ssa_place(var.name, used))
            block.phis[ssa_places[var][-1]] = args
        block.statements = [self.rename_stmt(stmt, ssa_places, used) for stmt in block.statements]
        for edge in block.outgoing:
            dst = edge.dst
            for var, args in dst.phis.items():
                if isinstance(var, SSAPlace):
                    continue
                if ssa_places[var]:
                    args[block] = ssa_places[var][-1]
        block.test = self.rename_stmt(block.test, ssa_places, used)
        for dom_child in get_dom_children(block):
            self.rename(dom_child, defs, ssa_places, used)
        for var, length in original_ssa_place_lens.items():
            ssa_places[var] = ssa_places[var][:length]

    def remove_original_phis(self, entry: BasicBlock):
        for block in traverse_cfg_preorder(entry):
            block.phis = {var: args for var, args in block.phis.items() if isinstance(var, SSAPlace)}

    def rename_stmt(self, stmt: IRStmt, ssa_places: dict[TempBlock, list[SSAPlace]], used: dict[str, int]):
        match stmt:
            case IRConst():
                return stmt
            case IRPureInstr(op=op, args=args):
                return IRPureInstr(op=op, args=[self.rename_stmt(arg, ssa_places, used) for arg in args])
            case IRInstr(op=op, args=args):
                return IRInstr(op=op, args=[self.rename_stmt(arg, ssa_places, used) for arg in args])
            case IRGet(place=place):
                return IRGet(place=self.rename_stmt(place, ssa_places, used))
            case IRSet(place=place, value=value):
                value = self.rename_stmt(value, ssa_places, used)
                if isinstance(place, BlockPlace) and isinstance(place.block, TempBlock) and place.block.size == 1:
                    ssa_places[place.block].append(self.get_new_ssa_place(place.block.name, used))
                place = self.rename_stmt(place, ssa_places, used)
                return IRSet(place=place, value=value)
            case SSAPlace():
                return stmt
            case TempBlock():
                return ssa_places[stmt][-1]
            case int():
                return stmt
            case BlockPlace(block=block, index=index, offset=offset):
                if isinstance(block, TempBlock) and block.size == 1:
                    return self.rename_stmt(block, ssa_places, used)
                return BlockPlace(
                    block=self.rename_stmt(block, ssa_places, used),
                    index=self.rename_stmt(index, ssa_places, used),
                    offset=self.rename_stmt(offset, ssa_places, used),
                )
            case _:
                raise NotImplementedError

    def insert_phis(self, entry: BasicBlock, defs: dict[TempBlock, set[BasicBlock]]):
        for var, blocks in defs.items():
            df = self.get_iterated_df(blocks)
            for block in df:
                block.phis[var] = {}

    def defs_to_blocks(self, entry: BasicBlock) -> dict[TempBlock, set[BasicBlock]]:
        result = {}
        for block in traverse_cfg_preorder(entry):
            for stmt in block.statements:
                def_block = self.get_stmt_def(stmt)
                if def_block is not None:
                    result.setdefault(def_block, set()).add(block)
        return result

    def get_stmt_def(self, stmt: IRStmt) -> TempBlock:
        if (
            isinstance(stmt, IRSet)
            and isinstance(stmt.place, BlockPlace)
            and isinstance(stmt.place.block, TempBlock)
            and stmt.place.block.size == 1
        ):
            return stmt.place.block
        return None

    def get_iterated_df(self, blocks: set[BasicBlock]) -> set[BasicBlock]:
        df = set()
        worklist = set(blocks)
        while worklist:
            block = worklist.pop()
            new_df = get_df(block) - df
            if new_df:
                df.update(new_df)
                worklist.update(new_df)
        return df

    def get_new_ssa_place(self, name: str, used: dict[str, int]) -> SSAPlace:
        if name not in used:
            used[name] = 0
        used[name] += 1
        return SSAPlace(name, used[name])


class FromSSA(CompilerPass):
    def run(self, entry: BasicBlock) -> BasicBlock:
        pass
