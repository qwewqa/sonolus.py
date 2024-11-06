from sonolus.backend.flow import BasicBlock, traverse_cfg_preorder
from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet
from sonolus.backend.node import ConstantNode, EngineNode, FunctionNode
from sonolus.backend.ops import Op
from sonolus.backend.place import BlockPlace


def cfg_to_engine_node(entry: BasicBlock):
    block_indexes = {block: i for i, block in enumerate(traverse_cfg_preorder(entry))}
    block_statements = []
    for block in block_indexes:
        statements = []
        statements.extend(ir_to_engine_node(stmt) for stmt in block.statements)
        outgoing = {edge.cond: edge.dst for edge in block.outgoing}
        match outgoing:
            case {**other} if not other:
                statements.append(ConstantNode(value=len(block_indexes)))
            case {None: target, **other} if not other:
                statements.append(ConstantNode(value=block_indexes[target]))
            case {0: f_branch, None: t_branch, **other} if not other:
                statements.append(
                    FunctionNode(
                        func=Op.If,
                        args=[
                            ir_to_engine_node(block.test),
                            ConstantNode(value=block_indexes[t_branch]),
                            ConstantNode(value=block_indexes[f_branch]),
                        ],
                    )
                )
            case dict() as tgt:
                args = [ir_to_engine_node(block.test)]
                default = len(block_indexes)
                for cond, target in tgt.items():
                    if cond is None:
                        default = block_indexes[target]
                    args.append(ConstantNode(value=cond))
                    args.append(ConstantNode(value=block_indexes[target]))
                args.append(ConstantNode(value=default))
                statements.append(FunctionNode(Op.SwitchWithDefault, args))
        block_statements.append(FunctionNode(Op.Execute, statements))
    block_statements.append(ConstantNode(value=0))
    return FunctionNode(Op.Block, [FunctionNode(Op.JumpLoop, block_statements)])


def ir_to_engine_node(stmt) -> EngineNode:
    match stmt:
        case int() | float():
            return ConstantNode(value=float(stmt))
        case IRConst(value=value):
            return ConstantNode(value=value)
        case IRPureInstr(op=op, args=args) | IRInstr(op=op, args=args):
            return FunctionNode(func=op, args=[ir_to_engine_node(arg) for arg in args])
        case IRGet(place=place):
            return ir_to_engine_node(place)
        case BlockPlace() as place:
            if place.offset == 0:
                index = ir_to_engine_node(place.index)
            elif place.index == 0:
                index = ConstantNode(value=place.offset)
            else:
                index = FunctionNode(
                    func=Op.Add, args=[ir_to_engine_node(place.index), ConstantNode(value=place.offset)]
                )
            return FunctionNode(func=Op.Get, args=[ir_to_engine_node(place.block), index])
        case IRSet(place=place, value=value):
            return FunctionNode(func=Op.Set, args=[*ir_to_engine_node(place).args, ir_to_engine_node(value)])
        case _:
            raise TypeError(f"Unsupported IR statement: {stmt}")
