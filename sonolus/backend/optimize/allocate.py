from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet
from sonolus.backend.optimize.flow import BasicBlock, traverse_cfg_preorder
from sonolus.backend.optimize.liveness import LivenessAnalysis, get_live
from sonolus.backend.optimize.passes import CompilerPass
from sonolus.backend.place import BlockPlace, TempBlock

TEMP_SIZE = 4096


class AllocateBasic(CompilerPass):
    """Allocate temporary memory for temporary variables without considering lifetimes."""

    def run(self, entry: BasicBlock):
        offsets = {}
        index = 0

        def process(stmt):
            nonlocal index
            match stmt:
                case int():
                    return stmt
                case IRConst():
                    return stmt
                case IRPureInstr(op=op, args=args):
                    return IRPureInstr(
                        op=op,
                        args=[process(arg) for arg in args],
                    )
                case IRInstr(op=op, args=args):
                    return IRInstr(
                        op=op,
                        args=[process(arg) for arg in args],
                    )
                case IRGet(place=place):
                    return IRGet(place=process(place))
                case IRSet(place=place, value=value):
                    return IRSet(place=process(place), value=process(value))
                case BlockPlace() as place:
                    if isinstance(place.block, TempBlock):
                        if place.block not in offsets:
                            offsets[place.block] = index
                            index += place.block.size
                            if index >= TEMP_SIZE:
                                raise ValueError("Temporary memory limit exceeded")
                        return BlockPlace(10000, process(place.index), place.offset + offsets[place.block])
                    return BlockPlace(
                        process(place.block) if isinstance(place.block, BlockPlace) else place.block,
                        process(place.index),
                        process(place.offset),
                    )
                case _:
                    raise TypeError(f"Unsupported statement: {stmt}")

        for block in traverse_cfg_preorder(entry):
            block.statements = [process(statement) for statement in block.statements]
            block.test = process(block.test)

        return entry


class Allocate(CompilerPass):
    def requires(self) -> set[CompilerPass]:
        return {LivenessAnalysis()}

    def run(self, entry: BasicBlock):
        mapping = self.get_mapping(entry)
        for block in traverse_cfg_preorder(entry):
            block.statements = [self.update_stmt(statement, mapping) for statement in block.statements]
            block.test = self.update_stmt(block.test, mapping)
        return entry

    def update_stmt(self, stmt, mapping: dict[TempBlock, int]):
        match stmt:
            case int() | float() | IRConst():
                return stmt
            case IRInstr(op=op, args=args):
                return IRInstr(op=op, args=[self.update_stmt(arg, mapping) for arg in args])
            case IRPureInstr(op=op, args=args):
                return IRPureInstr(op=op, args=[self.update_stmt(arg, mapping) for arg in args])
            case IRGet(place=place):
                return IRGet(place=self.update_stmt(place, mapping))
            case IRSet(place=place, value=value):
                return IRSet(place=self.update_stmt(place, mapping), value=self.update_stmt(value, mapping))
            case BlockPlace(block=block, index=index, offset=offset):
                if isinstance(block, TempBlock):
                    if block.size == 0:
                        offset = -1
                    else:
                        assert block in mapping, f"Missing mapping for {block}, dead variable not removed?"
                        offset = mapping[block] + offset
                    return BlockPlace(block=10000, index=self.update_stmt(index, mapping), offset=offset)
                return BlockPlace(
                    block=self.update_stmt(block, mapping),
                    index=self.update_stmt(index, mapping),
                    offset=self.update_stmt(offset, mapping),
                )
            case _:
                raise TypeError(f"Unsupported statement: {stmt}")

    def get_mapping(self, entry: BasicBlock) -> dict[TempBlock, int]:
        interference = self.get_interference(entry)
        offsets: dict[TempBlock, int] = {}

        for block, others in sorted(interference.items(), key=lambda x: -x[0].size):
            size = block.size
            offset = 0
            for other in sorted(others, key=lambda x: offsets.get(x, 0) + x.size):
                if other not in offsets:
                    continue
                other_offset = offsets[other]
                if offset + size > other_offset or other_offset + other.size > offset:
                    offset = other_offset + other.size
            if offset + size > TEMP_SIZE:
                raise ValueError("Temporary memory limit exceeded")
            offsets[block] = offset

        return offsets

    def get_interference(self, entry: BasicBlock) -> dict[TempBlock, set[TempBlock]]:
        result = {}
        for block in traverse_cfg_preorder(entry):
            for stmt in [*block.statements, block.test]:
                live = {p for p in get_live(stmt) if isinstance(p, TempBlock) and p.size > 0}
                for place in live:
                    result.setdefault(place, set()).update(live - {place})
        return result
