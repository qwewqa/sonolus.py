from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet, IRStmt
from sonolus.backend.optimize.dominance import DominanceFrontiers, get_df, get_dom_children
from sonolus.backend.optimize.flow import BasicBlock, FlowEdge, traverse_cfg_preorder
from sonolus.backend.optimize.passes import CompilerPass
from sonolus.backend.place import BlockPlace, SSAPlace, TempBlock


class ToSSA(CompilerPass):
    def requires(self) -> set[CompilerPass]:
        return {DominanceFrontiers()}

    def run(self, entry: BasicBlock) -> BasicBlock:
        defs = self.defs_to_blocks(entry)
        self.insert_phis(defs)
        self.rename(entry, defs, {var: [] for var in defs}, {})
        self.remove_placeholder_phis(entry)
        return entry

    def rename(
        self,
        block: BasicBlock,
        defs: dict[TempBlock, set[BasicBlock]],
        ssa_places: dict[TempBlock, list[SSAPlace]],
        used: dict[str, int],
    ):
        to_pop = []
        for var, args in [*block.phis.items()]:
            if isinstance(var, SSAPlace):
                continue
            ssa_places[var].append(self.get_new_ssa_place(var.name, used))
            to_pop.append(var)
            block.phis[ssa_places[var][-1]] = args
        block.statements = [self.rename_stmt(stmt, ssa_places, used, to_pop) for stmt in block.statements]
        for edge in block.outgoing:
            dst = edge.dst
            for var, args in dst.phis.items():
                if isinstance(var, SSAPlace):
                    continue
                if ssa_places[var]:
                    args[block] = ssa_places[var][-1]
        block.test = self.rename_stmt(block.test, ssa_places, used, to_pop)
        for dom_child in get_dom_children(block):
            self.rename(dom_child, defs, ssa_places, used)
        for var in to_pop:
            ssa_places[var].pop()

    def remove_placeholder_phis(self, entry: BasicBlock):
        for block in traverse_cfg_preorder(entry):
            block.phis = {var: args for var, args in block.phis.items() if isinstance(var, SSAPlace)}

    def rename_stmt(
        self, stmt: IRStmt, ssa_places: dict[TempBlock, list[SSAPlace]], used: dict[str, int], to_pop: list[SSAPlace]
    ):
        match stmt:
            case IRConst():
                return stmt
            case IRPureInstr(op=op, args=args):
                return IRPureInstr(op=op, args=[self.rename_stmt(arg, ssa_places, used, to_pop) for arg in args])
            case IRInstr(op=op, args=args):
                return IRInstr(op=op, args=[self.rename_stmt(arg, ssa_places, used, to_pop) for arg in args])
            case IRGet(place=place):
                return IRGet(place=self.rename_stmt(place, ssa_places, used, to_pop))
            case IRSet(place=place, value=value):
                value = self.rename_stmt(value, ssa_places, used, to_pop)
                if isinstance(place, BlockPlace) and isinstance(place.block, TempBlock) and place.block.size == 1:
                    ssa_places[place.block].append(self.get_new_ssa_place(place.block.name, used))
                    to_pop.append(place.block)
                place = self.rename_stmt(place, ssa_places, used, to_pop)
                return IRSet(place=place, value=value)
            case SSAPlace():
                return stmt
            case TempBlock() if stmt.size == 1:
                if stmt not in ssa_places or not ssa_places[stmt]:
                    # This is an access to a definitely undefined variable
                    # But it might not be reachable in reality, so we should allow it
                    # Maybe there should be an error if this still happens after optimization,
                    # but recovering the location of the error in the original code is hard.
                    # This can happen in places like matching a VarArray[Num, 1] which was just created.
                    # IR generation won't immediately fold a check that size > 0 to false, so here we
                    # might see an access to uninitialized memory even though it's not reachable in reality.
                    return SSAPlace("err", 0)
                return ssa_places[stmt][-1]
            case TempBlock():
                return stmt
            case int():
                return stmt
            case BlockPlace(block=block, index=index, offset=offset):
                if isinstance(block, TempBlock) and block.size == 1:
                    return self.rename_stmt(block, ssa_places, used, to_pop)
                return BlockPlace(
                    block=self.rename_stmt(block, ssa_places, used, to_pop),
                    index=self.rename_stmt(index, ssa_places, used, to_pop),
                    offset=self.rename_stmt(offset, ssa_places, used, to_pop),
                )
            case _:
                raise TypeError(f"Unexpected statement: {stmt}")

    def insert_phis(self, defs: dict[TempBlock, set[BasicBlock]]):
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
        for block in [*traverse_cfg_preorder(entry)]:
            self.process_block(block)
        return entry

    def process_block(self, block: BasicBlock):
        incoming = [*block.incoming]
        block.incoming.clear()
        for edge in incoming:
            between_block = BasicBlock()
            edge.dst = between_block
            between_block.incoming.add(edge)
            next_edge = FlowEdge(between_block, block, None)
            block.incoming.add(next_edge)
            between_block.outgoing.add(next_edge)
            for args in block.phis.values():
                if edge.src in args:
                    args[between_block] = args.pop(edge.src)
        for var, args in block.phis.items():
            for src, arg in args.items():
                src.statements.append(
                    IRSet(place=self.place_from_ssa_place(var), value=IRGet(place=self.place_from_ssa_place(arg)))
                )
        block.phis = {}
        block.statements = [self.process_stmt(stmt) for stmt in block.statements]
        block.test = self.process_stmt(block.test)

    def process_stmt(self, stmt: IRStmt):
        match stmt:
            case IRConst():
                return stmt
            case IRPureInstr(op=op, args=args):
                return IRPureInstr(op=op, args=[self.process_stmt(arg) for arg in args])
            case IRInstr(op=op, args=args):
                return IRInstr(op=op, args=[self.process_stmt(arg) for arg in args])
            case IRGet(place=place):
                return IRGet(place=self.process_stmt(place))
            case IRSet(place=place, value=value):
                return IRSet(place=self.process_stmt(place), value=self.process_stmt(value))
            case SSAPlace():
                return self.place_from_ssa_place(stmt)
            case TempBlock():
                return stmt
            case int():
                return stmt
            case BlockPlace(block=block, index=index, offset=offset):
                return BlockPlace(
                    block=self.process_stmt(block),
                    index=self.process_stmt(index),
                    offset=self.process_stmt(offset),
                )
            case _:
                raise TypeError(f"Unexpected statement: {stmt}")

    def temp_block_from_ssa_place(self, ssa_place: SSAPlace) -> TempBlock:
        return TempBlock(f"{ssa_place.name}.{ssa_place.num}")

    def place_from_ssa_place(self, ssa_place: SSAPlace) -> BlockPlace:
        return BlockPlace(block=self.temp_block_from_ssa_place(ssa_place), index=0, offset=0)
