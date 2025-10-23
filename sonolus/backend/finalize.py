from math import isfinite, isinf, isnan

from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet
from sonolus.backend.node import EngineNode, FunctionNode
from sonolus.backend.ops import Op
from sonolus.backend.optimize.flow import BasicBlock, traverse_cfg_reverse_postorder
from sonolus.backend.place import BlockPlace


def cfg_to_engine_node(entry: BasicBlock):
    block_indexes = {block: i for i, block in enumerate(traverse_cfg_reverse_postorder(entry))}
    block_statements = []
    for block in block_indexes:
        statements = []
        statements.extend(ir_to_engine_node(stmt) for stmt in block.statements)
        outgoing = {
            edge.cond: edge.dst for edge in sorted(block.outgoing, key=lambda edge: (edge.cond is None, edge.cond))
        }
        match outgoing:
            case {**other} if not other:
                statements.append(len(block_indexes))
            case {None: target, **other} if not other:
                statements.append(block_indexes[target])
            case {0: f_branch, None: t_branch, **other} if not other:
                statements.append(
                    FunctionNode(
                        func=Op.If,
                        args=(
                            ir_to_engine_node(block.test),
                            block_indexes[t_branch],
                            block_indexes[f_branch],
                        ),
                    )
                )
            case {None: default_branch, **other} if len(other) == 1:
                cond, cond_branch = next(iter(other.items()))
                statements.append(
                    FunctionNode(
                        func=Op.If,
                        args=(
                            ir_to_engine_node(IRPureInstr(Op.Equal, args=[block.test, IRConst(cond)])),
                            block_indexes[cond_branch],
                            block_indexes[default_branch],
                        ),
                    )
                )
            case dict() as targets:
                args = [ir_to_engine_node(block.test)]
                default = len(block_indexes)
                conds = [cond for cond in targets if cond is not None]
                if min(conds) == 0 and max(conds) == len(conds) - 1 and all(int(cond) == cond for cond in conds):
                    args.extend(block_indexes[targets[cond]] for cond in range(len(conds)))
                    if None in targets:
                        default = block_indexes[targets[None]]
                    args.append(default)
                    statements.append(FunctionNode(Op.SwitchIntegerWithDefault, tuple(args)))
                else:
                    for cond, target in targets.items():
                        if cond is None:
                            default = block_indexes[target]
                            continue
                        args.append(cond)
                        args.append(block_indexes[target])
                    args.append(default)
                    statements.append(FunctionNode(Op.SwitchWithDefault, tuple(args)))
        block_statements.append(FunctionNode(Op.Execute, tuple(statements)))
    block_statements.append(0)
    result = FunctionNode(Op.Block, (FunctionNode(Op.JumpLoop, tuple(block_statements)),))
    for block in block_indexes:
        # Clean up without relying on gc
        del block.incoming
        del block.outgoing
        del block.phis
        del block.statements
        del block.test
    return result


def ir_to_engine_node(stmt) -> EngineNode:
    match stmt:
        case int(value) | float(value) | IRConst(value=int(value) | float(value)):
            value = float(value)
            if value.is_integer():
                return int(value)
            elif isfinite(value):
                return value
            elif isinf(value):
                # Read values from ROM
                return FunctionNode(Op.Get, args=(3000, 1 if value > 0 else 2))
            elif isnan(value):
                # Read value from ROM
                return FunctionNode(Op.Get, args=(3000, 0))
            else:
                raise ValueError(f"Invalid constant value: {value}")
        case IRPureInstr(op=op, args=args) | IRInstr(op=op, args=args):
            return FunctionNode(func=op, args=tuple(ir_to_engine_node(arg) for arg in args))
        case IRGet(place=place):
            return ir_to_engine_node(place)
        case BlockPlace() as place:
            if place.offset == 0:
                index = ir_to_engine_node(place.index)
            elif place.index == 0:
                index = place.offset
            else:
                index = FunctionNode(func=Op.Add, args=(ir_to_engine_node(place.index), place.offset))
            return FunctionNode(func=Op.Get, args=(ir_to_engine_node(place.block), index))
        case IRSet(place=place, value=value):
            return FunctionNode(func=Op.Set, args=(*ir_to_engine_node(place).args, ir_to_engine_node(value)))
        case _:
            raise TypeError(f"Unsupported IR statement: {stmt}")
