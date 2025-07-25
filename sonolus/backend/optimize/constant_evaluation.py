# ruff: noqa: PLR1702
import functools
import math
import operator
from collections import defaultdict
from typing import ClassVar

import sonolus.script.internal.math_impls as smath
from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet, IRStmt
from sonolus.backend.ops import Op
from sonolus.backend.optimize.flow import BasicBlock, FlowEdge, traverse_cfg_preorder
from sonolus.backend.optimize.passes import CompilerPass, OptimizerConfig
from sonolus.backend.place import BlockPlace, SSAPlace, TempBlock


class Undefined:
    pass


class NotAConstant:
    pass


UNDEF = Undefined()
NAC = NotAConstant()


type Value = float | set[float] | Undefined | NotAConstant


def is_constant(value: Value) -> bool:
    return isinstance(value, (int, float))


class SparseConditionalConstantPropagation(CompilerPass):
    SUPPORTED_OPS: ClassVar[set[Op]] = {
        Op.Equal,
        Op.NotEqual,
        Op.Greater,
        Op.GreaterOr,
        Op.Less,
        Op.LessOr,
        Op.Not,
        Op.And,
        Op.Or,
        Op.Negate,
        Op.Add,
        Op.Subtract,
        Op.Multiply,
        Op.Divide,
        Op.Power,
        Op.Log,
        Op.Ceil,
        Op.Floor,
        Op.Round,
        Op.Frac,
        Op.Mod,
        Op.Rem,
        Op.Sin,
        Op.Cos,
        Op.Tan,
        Op.Sinh,
        Op.Cosh,
        Op.Tanh,
        Op.Arcsin,
        Op.Arccos,
        Op.Arctan,
        Op.Arctan2,
        Op.Max,
        Op.Min,
    }

    def run(self, entry: BasicBlock, config: OptimizerConfig) -> BasicBlock:
        ssa_edges: dict[SSAPlace, set[SSAPlace | BasicBlock]] = {}
        executable_edges: set[FlowEdge] = set()

        # BasicBlock key means the block's test
        values: dict[SSAPlace | BasicBlock, Value] = defaultdict(lambda: UNDEF)
        defs: dict[SSAPlace | BasicBlock, IRStmt | dict[FlowEdge, SSAPlace]] = {}
        places_to_blocks: dict[SSAPlace, BasicBlock] = {}
        reachable_blocks: set[BasicBlock] = set()

        for block in traverse_cfg_preorder(entry):
            incoming_by_src = {}
            for edge in block.incoming:
                incoming_by_src.setdefault(edge.src, []).append(edge)
            for p, args in block.phis.items():
                if not isinstance(p, SSAPlace):
                    raise TypeError(f"Unexpected phi place: {p}")
                defs[p] = {}
                for b, v in args.items():
                    for incoming in incoming_by_src.get(b, []):
                        defs[p][incoming] = v
                values[p] = UNDEF
                for arg in args.values():
                    ssa_edges.setdefault(arg, set()).add(p)
            for stmt in block.statements:
                if isinstance(stmt, IRSet) and isinstance(stmt.place, SSAPlace):
                    defs[stmt.place] = stmt.value
                    places_to_blocks[stmt.place] = block
                    values[stmt.place] = UNDEF
                    for dep in self.get_dependencies(stmt.value, set()):
                        ssa_edges.setdefault(dep, set()).add(stmt.place)
            defs[block] = block.test
            values[block] = UNDEF
            for dep in self.get_dependencies(block.test, set()):
                ssa_edges.setdefault(dep, set()).add(block)

        def visit_phi(p):
            arg_values = [values[v] if e in executable_edges else UNDEF for e, v in defs[p].items()]
            distinct_defined_arg_values = {arg for arg in arg_values if arg is not UNDEF}
            value = values[p]
            if len(distinct_defined_arg_values) == 1:
                new_value = distinct_defined_arg_values.pop()
            elif len(distinct_defined_arg_values) > 1:
                if any(arg is NAC for arg in distinct_defined_arg_values):
                    new_value = NAC
                else:
                    new_values = set()
                    for arg in distinct_defined_arg_values:
                        if isinstance(arg, frozenset):
                            new_values.update(arg)
                        else:
                            new_values.add(arg)
                    if len(new_values) == 1:
                        new_value = next(iter(new_values))
                    else:
                        new_value = frozenset(new_values)
            else:
                new_value = UNDEF
            if new_value != value:
                values[p] = new_value
                ssa_worklist.update(ssa_edges.get(p, set()))

        flow_worklist: set[FlowEdge] = {FlowEdge(entry, entry, None)}
        ssa_worklist: set[SSAPlace | BasicBlock] = set()
        while flow_worklist or ssa_worklist:
            while flow_worklist:
                edge = flow_worklist.pop()
                if edge in executable_edges:
                    continue
                executable_edges.add(edge)
                block: BasicBlock = edge.dst
                for p in block.phis:
                    visit_phi(p)
                is_first_visit = sum(edge in executable_edges for edge in block.incoming) <= 1
                if is_first_visit:
                    for stmt in block.statements:
                        if not (isinstance(stmt, IRSet) and isinstance(stmt.place, SSAPlace)):
                            continue
                        value = values[stmt.place]
                        new_value = self.evaluate_stmt(stmt.value, values)
                        if new_value != value:
                            values[stmt.place] = new_value
                            ssa_worklist.update(ssa_edges.get(stmt.place, set()))
                    test_value = values[block]
                    new_test_value = self.evaluate_stmt(block.test, values)
                    if new_test_value != test_value:
                        assert new_test_value is not UNDEF
                        values[block] = new_test_value
                        if new_test_value is NAC:
                            flow_worklist.update(block.outgoing)
                            reachable_blocks.update(e.dst for e in block.outgoing)
                        elif block.outgoing:
                            outgoing_by_cond = {edge.cond: edge for edge in block.outgoing}
                            if is_constant(new_test_value):
                                taken_edge = outgoing_by_cond.get(new_test_value, outgoing_by_cond.get(None))
                                if taken_edge is None:
                                    raise ValueError("Unexpected missing edge")
                                taken_edges = {taken_edge}
                            else:
                                taken_edges = set()
                                for v in new_test_value:
                                    taken_edge = outgoing_by_cond.get(v, outgoing_by_cond.get(None))
                                    if taken_edge:
                                        taken_edges.add(taken_edge)
                                    else:
                                        raise ValueError("Unexpected missing edge")
                            for taken_edge in taken_edges:
                                flow_worklist.add(taken_edge)
                                reachable_blocks.add(taken_edge.dst)
                    elif len(block.outgoing) == 1 and next(iter(block.outgoing)).cond is None:
                        flow_worklist.update(block.outgoing)
                        reachable_blocks.update(e.dst for e in block.outgoing)
            while ssa_worklist:
                p = ssa_worklist.pop()
                defn = defs[p]
                if isinstance(defn, dict):
                    # This is a phi
                    visit_phi(p)
                elif isinstance(p, BasicBlock):
                    # This is the block's test
                    test_value = values[p]
                    new_test_value = self.evaluate_stmt(defn, values)
                    if new_test_value != test_value:
                        assert new_test_value is not UNDEF
                        values[p] = new_test_value
                        if new_test_value is NAC:
                            flow_worklist.update(p.outgoing)
                            reachable_blocks.update(e.dst for e in p.outgoing)
                        else:
                            outgoing_by_cond = {edge.cond: edge for edge in p.outgoing}
                            if is_constant(new_test_value):
                                taken_edge = outgoing_by_cond.get(new_test_value, outgoing_by_cond.get(None))
                                if taken_edge is None:
                                    raise ValueError("Unexpected missing edge")
                                taken_edges = {taken_edge}
                            else:
                                taken_edges = set()
                                for v in new_test_value:
                                    taken_edge = outgoing_by_cond.get(v, outgoing_by_cond.get(None))
                                    if taken_edge:
                                        taken_edges.add(taken_edge)
                                    else:
                                        raise ValueError("Unexpected missing edge")
                            for taken_edge in taken_edges:
                                flow_worklist.add(taken_edge)
                                reachable_blocks.add(taken_edge.dst)
                else:
                    # This is a regular SSA assignment
                    if places_to_blocks[p] not in reachable_blocks:
                        continue
                    value = values[p]
                    new_value = self.evaluate_stmt(defn, values)
                    if new_value != value:
                        values[p] = new_value
                        ssa_worklist.update(ssa_edges.get(p, set()))

        for block in traverse_cfg_preorder(entry):
            block.statements = [self.substitute_constants(stmt, values) for stmt in block.statements]
            block.test = self.substitute_constants(block.test, values)
            if isinstance(block.test, IRGet) and block.test.place in values:
                test_value = values[block.test.place]
                if isinstance(test_value, frozenset):
                    new_outgoing = set()
                    outgoing_by_cond = {edge.cond: edge for edge in block.outgoing}
                    for v in test_value:
                        if v in outgoing_by_cond:
                            new_outgoing.add(outgoing_by_cond[v])
                        elif None in outgoing_by_cond:
                            new_outgoing.add(outgoing_by_cond[None])
                        else:
                            raise ValueError("Unexpected missing edge")
                    removed_edges = set(block.outgoing) - new_outgoing
                    for edge in removed_edges:
                        edge.dst.incoming.remove(edge)
                    if not any(edge.cond is None for edge in new_outgoing):
                        default_edge = max(new_outgoing, key=lambda e: e.cond)
                        default_edge.cond = None
                    block.outgoing = new_outgoing

        reachable_blocks = set(traverse_cfg_preorder(entry))
        for block in traverse_cfg_preorder(entry):
            block.incoming = {edge for edge in block.incoming if edge.src in reachable_blocks}
            incoming_blocks = {edge.src for edge in block.incoming}
            for v in block.phis:
                block.phis[v] = {k: v for k, v in block.phis[v].items() if k in incoming_blocks}

        queue = set(traverse_cfg_preorder(entry))
        while queue:
            block = queue.pop()
            dead_phis = {k for k, v in block.phis.items() if not v}
            for edge in block.outgoing:
                for v in edge.dst.phis.values():
                    if block in v and v[block] in dead_phis:
                        del v[block]
                        queue.add(edge.dst)
            block.phis = {k: v for k, v in block.phis.items() if v}

        for block in traverse_cfg_preorder(entry):
            block.phis = {
                k: {b: arg for b, arg in args.items() if values.get(arg, UNDEF) is not UNDEF}
                for k, args in block.phis.items()
            }
            block.statements = [
                stmt
                for stmt in block.statements
                # Note that if this is a set with a side effect, it will never be undef
                if not (
                    isinstance(stmt, IRSet)
                    and isinstance(stmt.place, SSAPlace)
                    and values.get(stmt.place, UNDEF) is UNDEF
                )
            ]

        return entry

    def get_dependencies(self, stmt, dependencies: set[SSAPlace]):
        match stmt:
            case IRConst():
                pass
            case IRPureInstr(op=_, args=args) | IRInstr(op=_, args=args):
                for arg in args:
                    self.get_dependencies(arg, dependencies)
            case IRGet(place=SSAPlace() as place):
                dependencies.add(place)
            case IRGet(place=BlockPlace() as place):
                self.get_dependencies(place.block, dependencies)
            case BlockPlace(block=block, index=index, offset=_):
                self.get_dependencies(block, dependencies)
                self.get_dependencies(index, dependencies)
            case SSAPlace():
                dependencies.add(stmt)
            case int() | float() | TempBlock():
                pass
            case _:
                raise TypeError(f"Unexpected statement: {stmt}")
        return dependencies

    def substitute_constants(self, stmt, values: dict[SSAPlace, Value]):
        match stmt:
            case IRConst():
                return stmt
            case IRPureInstr(op=op, args=args):
                return IRPureInstr(op=op, args=[self.substitute_constants(arg, values) for arg in args])
            case IRInstr(op=op, args=args):
                return IRInstr(op=op, args=[self.substitute_constants(arg, values) for arg in args])
            case IRGet(place=SSAPlace() as place):
                value = values[place]
                if isinstance(value, int | float):
                    return IRConst(value)
                return stmt
            case IRGet(place=place):
                return IRGet(place=self.substitute_constants(place, values))
            case IRSet(place=SSAPlace() as place, value=value):
                return IRSet(place=place, value=self.substitute_constants(value, values))
            case IRSet(place=place, value=value):
                return IRSet(
                    place=self.substitute_constants(place, values), value=self.substitute_constants(value, values)
                )
            case BlockPlace(block=block, index=index, offset=offset):
                return BlockPlace(
                    block=self.substitute_constants(block, values),
                    index=self.substitute_constants(index, values),
                    offset=offset,
                )
            case SSAPlace():
                value = values[stmt]
                if isinstance(value, int | float):
                    return IRConst(value)
                return stmt
            case int() | float() | TempBlock():
                return stmt
            case _:
                raise TypeError(f"Unexpected statement: {stmt}")

    def evaluate_stmt(self, stmt, values: dict[SSAPlace, Value]) -> Value:
        match stmt:
            case IRConst(value=value):
                return value
            case IRPureInstr(op=op, args=args) | IRInstr(op=op, args=args):
                if op not in self.SUPPORTED_OPS:
                    return NAC
                args = [self.evaluate_stmt(arg, values) for arg in args]
                match op:
                    case Op.And:
                        if any(arg == 0 for arg in args):
                            return 0
                    case Op.Or:
                        if any(arg == 1 for arg in args):
                            return 1
                    case Op.Multiply:
                        if any(arg == 0 for arg in args):
                            return 0
                if any(arg is NAC or isinstance(arg, frozenset) for arg in args):
                    return NAC
                if any(arg is UNDEF for arg in args):
                    return UNDEF
                match op:
                    case Op.Equal:
                        assert len(args) == 2
                        return args[0] == args[1]
                    case Op.NotEqual:
                        assert len(args) == 2
                        return args[0] != args[1]
                    case Op.Greater:
                        assert len(args) == 2
                        return args[0] > args[1]
                    case Op.GreaterOr:
                        assert len(args) == 2
                        return args[0] >= args[1]
                    case Op.Less:
                        assert len(args) == 2
                        return args[0] < args[1]
                    case Op.LessOr:
                        assert len(args) == 2
                        return args[0] <= args[1]
                    case Op.Not:
                        assert len(args) == 1
                        return int(not args[0])
                    case Op.And:
                        return all(args)
                    case Op.Or:
                        return any(args)
                    case Op.Negate:
                        assert len(args) == 1
                        return -args[0]
                    case Op.Add:
                        return sum(args)
                    case Op.Subtract:
                        if len(args) == 0:
                            return 0
                        return args[0] - sum(args[1:])
                    case Op.Multiply:
                        if len(args) == 0:
                            return 1
                        return functools.reduce(operator.mul, args, 1)
                    case Op.Divide:
                        if len(args) == 0:
                            return 1
                        return args[0] / functools.reduce(operator.mul, args[1:], 1)
                    case Op.Power:
                        if len(args) == 0:
                            return 1
                        return functools.reduce(operator.pow, args)
                    case Op.Log:
                        assert len(args) == 1
                        return math.log(args[0])
                    case Op.Ceil:
                        assert len(args) == 1
                        return math.ceil(args[0])
                    case Op.Floor:
                        assert len(args) == 1
                        return math.floor(args[0])
                    case Op.Round:
                        assert len(args) == 1
                        # This is round half to even in both Python and Sonolus
                        return round(args[0])
                    case Op.Frac:
                        assert len(args) == 1
                        return smath.frac(args[0])
                    case Op.Mod:
                        assert len(args) == 2
                        return args[0] % args[1]
                    case Op.Rem:
                        assert len(args) == 2
                        return smath.remainder(args[0], args[1])
                    case Op.Sin:
                        assert len(args) == 1
                        return math.sin(args[0])
                    case Op.Cos:
                        assert len(args) == 1
                        return math.cos(args[0])
                    case Op.Tan:
                        assert len(args) == 1
                        return math.tan(args[0])
                    case Op.Sinh:
                        assert len(args) == 1
                        return math.sinh(args[0])
                    case Op.Cosh:
                        assert len(args) == 1
                        return math.cosh(args[0])
                    case Op.Tanh:
                        assert len(args) == 1
                        return math.tanh(args[0])
                    case Op.Arcsin:
                        assert len(args) == 1
                        return math.asin(args[0])
                    case Op.Arccos:
                        assert len(args) == 1
                        return math.acos(args[0])
                    case Op.Arctan:
                        assert len(args) == 1
                        return math.atan(args[0])
                    case Op.Arctan2:
                        assert len(args) == 2
                        return math.atan2(args[0], args[1])
                    case Op.Max:
                        assert len(args) == 2
                        return max(args)
                    case Op.Min:
                        assert len(args) == 2
                        return min(args)
            case IRGet(place=SSAPlace() as place):
                return values[place]
            case IRGet():
                return NAC
            case IRSet() | _:
                raise TypeError(f"Unexpected statement: {stmt}")
