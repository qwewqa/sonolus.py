import functools
from typing import Tuple

from sonolus.backend.cfg import CFG
from sonolus.backend.cfg_traversal import traverse_cfg
from sonolus.backend.ir import IRConst, IRFunc, IRValueType
from sonolus.backend.ir_visitor import IRTransformer
from sonolus.backend.optimization.optimization_pass import OptimizationPass


class ArithmeticSimplification(OptimizationPass):
    def run(self, cfg: CFG):
        transformer = ArithmeticSimplificationTransformer()
        for cfg_node in [*traverse_cfg(cfg)]:
            cfg.replace_node(cfg_node, transformer.visit(cfg_node))


class ArithmeticSimplificationTransformer(IRTransformer):
    def visit_IRFunc(self, node):
        node = super().visit_IRFunc(node)
        if not node.args:
            # Empty args
            return node
        match node.name:
            case "Add":
                args = []
                for arg in node.args:
                    if isinstance(arg, IRFunc) and arg.name == "Add":
                        args += arg.args
                    else:
                        args.append(arg)
                other, const = self.get_commutative_const_args(args)
                const_sum = sum(const)
                if const_sum != 0:
                    args = [IRConst(const_sum), *other]
                else:
                    args = other
                if len(args) == 1:
                    return args[0]
                else:
                    return IRFunc("Add", args)
            case "Subtract":
                base, other, const = self.get_semicommutative_const_args(node.args)
                const_sum = sum(const)
                if const_sum != 0:
                    args = [base, IRConst(const_sum), *other]
                else:
                    args = [base, *other]
                if len(args) == 1:
                    return args[0]
                else:
                    return IRFunc("Subtract", args)
            case "Multiply":
                args = []
                for arg in node.args:
                    if isinstance(arg, IRFunc) and arg.name == "Multiply":
                        args += arg.args
                    else:
                        args.append(arg)
                other, const = self.get_commutative_const_args(args)
                const_prod = functools.reduce(lambda x, y: x * y, const, 1)
                match const_prod:
                    case 0:
                        args = [IRConst(0)]
                    case 1:
                        args = other
                    case _:
                        args = [IRConst(const_prod), *other]
                if len(args) == 1:
                    return args[0]
                else:
                    return IRFunc("Multiply", args)
            case "Divide":
                base, other, const = self.get_semicommutative_const_args(node.args)
                const_prod = functools.reduce(lambda x, y: x * y, const, 1)
                if const_prod == 1:
                    args = [base, *other]
                else:
                    args = [base, IRConst(const_prod), *other]
                if len(args) == 1:
                    return args[0]
                else:
                    return IRFunc("Divide", args)
            case "And":
                args = []
                for arg in node.args:
                    if isinstance(arg, IRFunc) and arg.name == "And":
                        args += arg.args
                    else:
                        args.append(arg)
                other, const = self.get_commutative_const_args(args)
                if any(not x for x in const):
                    return IRConst(0)
                else:
                    match other:
                        case []:
                            return IRConst(1)
                        case [single]:
                            return single
                        case _:
                            return IRFunc("And", other)
            case "Or":
                args = []
                for arg in node.args:
                    if isinstance(arg, IRFunc) and arg.name == "Or":
                        args += arg.args
                    else:
                        args.append(arg)
                other, const = self.get_commutative_const_args(args)
                if any(x for x in const):
                    return IRConst(1)
                else:
                    match other:
                        case []:
                            return IRConst(0)
                        case [single]:
                            return single
                        case _:
                            return IRFunc("Or", other)
            case _:
                return node

    def get_commutative_const_args(
        self, args: list[IRValueType]
    ) -> Tuple[list[IRValueType], list[float]]:
        other = []
        const = []
        for arg in args:
            if arg.constant() is not None:
                const.append(arg.constant())
            else:
                other.append(arg)
        return other, const

    def get_semicommutative_const_args(
        self, args: list[IRValueType]
    ) -> Tuple[IRValueType, list[IRValueType], list[float]]:
        base = args[0]
        args = args[1:]
        other = []
        const = []
        for arg in args:
            if arg.constant() is not None:
                const.append(arg.constant())
            else:
                other.append(arg)
        return base, other, const
