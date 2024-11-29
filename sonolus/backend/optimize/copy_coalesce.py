from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet
from sonolus.backend.optimize.flow import BasicBlock, traverse_cfg_preorder
from sonolus.backend.optimize.liveness import LivenessAnalysis, get_live
from sonolus.backend.optimize.passes import CompilerPass
from sonolus.backend.place import BlockPlace, SSAPlace, TempBlock


class CopyCoalesce(CompilerPass):
    def requires(self) -> set[CompilerPass]:
        return {LivenessAnalysis()}

    def run(self, entry: BasicBlock) -> BasicBlock:
        mapping = self.get_mapping(entry)
        for block in traverse_cfg_preorder(entry):
            block.statements = [self.apply_to_stmt(stmt, mapping) for stmt in block.statements]
            block.test = self.apply_to_stmt(block.test, mapping)
        return entry

    def apply_to_stmt(self, stmt, mapping: dict[TempBlock, TempBlock]):
        match stmt:
            case IRConst():
                return stmt
            case IRSet(place=place, value=value):
                return IRSet(self.apply_to_stmt(place, mapping), self.apply_to_stmt(value, mapping))
            case IRGet(place=place):
                return IRGet(self.apply_to_stmt(place, mapping))
            case IRPureInstr(op=op, args=args):
                return IRPureInstr(op, [self.apply_to_stmt(arg, mapping) for arg in args])
            case IRInstr(op=op, args=args):
                return IRInstr(op, [self.apply_to_stmt(arg, mapping) for arg in args])
            case BlockPlace(block=block, index=index, offset=offset):
                return BlockPlace(self.apply_to_stmt(block, mapping), self.apply_to_stmt(index, mapping), offset)
            case TempBlock():
                return mapping.get(stmt, stmt)
            case SSAPlace() | int() | float():
                return stmt

    def get_mapping(self, entry: BasicBlock) -> dict[TempBlock, TempBlock]:
        interference = self.get_interference(entry)
        copies = self.get_copies(entry)

        mapping: dict[TempBlock, set[TempBlock]] = {}

        for target, sources in copies.items():
            for source in sources:
                if source in interference.get(target, set()):
                    continue
                combined_mapping = mapping.get(target, {target}) | mapping.get(source, {source})
                combined_interference = interference.get(target, set()) | interference.get(source, set())
                for place in combined_mapping:
                    mapping[place] = combined_mapping
                    interference[place] = combined_interference
                for place in combined_interference:
                    interference[place].update(combined_mapping)

        canonical_mapping = {}
        for place, group in mapping.items():
            if place in canonical_mapping:
                continue
            canonical = min(group)
            for member in group:
                canonical_mapping[member] = canonical

        return canonical_mapping

    def get_interference(self, entry: BasicBlock) -> dict[TempBlock, set[TempBlock]]:
        result = {}
        for block in traverse_cfg_preorder(entry):
            for stmt in [*block.statements, block.test]:
                live = {p for p in get_live(stmt) if isinstance(p, TempBlock) and p.size == 1}
                for place in live:
                    result.setdefault(place, set()).update(live - {place})
        return result

    def get_copies(self, entry: BasicBlock) -> dict[TempBlock, set[TempBlock]]:
        result = {}
        for block in traverse_cfg_preorder(entry):
            for stmt in block.statements:
                if (
                    not isinstance(stmt, IRSet)
                    or not isinstance(stmt.place, BlockPlace)
                    or not isinstance(stmt.place.block, TempBlock)
                    or stmt.place.block.size != 1
                    or not isinstance(stmt.value, IRGet)
                    or not isinstance(stmt.value.place, BlockPlace)
                    or not isinstance(stmt.value.place.block, TempBlock)
                    or stmt.value.place.block.size != 1
                ):
                    continue
                target = stmt.place.block
                source = stmt.value.place.block
                result.setdefault(target, set()).add(source)
                result.setdefault(source, set()).add(target)
        return result
