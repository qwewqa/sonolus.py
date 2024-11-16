from sonolus.backend.flow import BasicBlock, traverse_cfg_preorder
from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet
from sonolus.backend.passes import CompilerPass
from sonolus.backend.place import BlockPlace, TempBlock

TEMP_SIZE = 4096


class AllocateBasic(CompilerPass):
    def run(self, entry: BasicBlock):
        offsets = {}
        index = 16

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
                    raise NotImplementedError

        for block in traverse_cfg_preorder(entry):
            block.statements = [process(statement) for statement in block.statements]
            block.test = process(block.test)

        return entry
