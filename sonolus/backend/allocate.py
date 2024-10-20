from sonolus.backend.blocks import PlayBlock
from sonolus.backend.flow import BasicBlock, traverse_cfg_preorder
from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet
from sonolus.backend.passes import CompilerPass
from sonolus.backend.place import BlockPlace, TempBlock


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
                        return BlockPlace(
                            PlayBlock.TemporaryMemory, process(place.index), place.offset + offsets[place.block]
                        )
                    return place
                case _:
                    raise NotImplementedError

        for block in traverse_cfg_preorder(entry):
            block.statements = [process(statement) for statement in block.statements]
            block.test = process(block.test)

        return entry
