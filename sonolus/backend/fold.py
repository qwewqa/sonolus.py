from sonolus.backend.flow import BasicBlock, traverse_cfg_preorder
from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet, IRStmt
from sonolus.backend.ops import Op
from sonolus.backend.passes import CompilerPass
from sonolus.backend.place import BlockPlace, SSAPlace, TempBlock


class FoldConstants(CompilerPass):
    def run(self, entry: BasicBlock) -> BasicBlock:
        _counts, values = self.scan(entry)
        self.simplify_values(values)

        for block in traverse_cfg_preorder(entry):
            block.statements = [self.simplify_stmt(stmt, values) for stmt in block.statements]
            block.test = self.simplify_stmt(block.test, values)

        return entry

    def simplify_values(self, values: dict[SSAPlace, IRStmt]):
        dependencies = {}
        for place, value in values.items():
            dependencies[place] = set()
            self.get_dependencies(value, dependencies[place])
        dependents = {}
        for place, deps in dependencies.items():
            for dep in deps:
                dependents.setdefault(dep, set()).add(place)
        queue = [*values]
        visited = set()
        while queue:
            place = queue.pop(0)
            if place in visited:
                continue
            visited.add(place)
            value = values[place]
            if isinstance(value, tuple):
                distinct = {*value}
                if len(distinct) == 1:
                    values[place] = IRGet(next(iter(distinct)))
                continue
            updated_value = self.simplify_stmt(value, values)
            if updated_value != value:
                values[place] = updated_value
                for dependent in dependents.get(place, []):
                    queue.append(dependent)
                    visited.discard(dependent)

    def simplify_stmt(self, stmt, values: dict[SSAPlace, IRStmt]):
        match stmt:
            case IRConst():
                return stmt
            case IRPureInstr(op=op, args=args) | IRInstr(op=op, args=args):
                return self.simplify_fn(op, args, values)
            case IRGet(place=place):
                if place in values:
                    if not self.is_safe_to_inline(values[place]):
                        return stmt
                    return values[place]
                return stmt
            case IRSet(place=place, value=value):
                return IRSet(place=self.simplify_stmt(place, values), value=self.simplify_stmt(value, values))
            case SSAPlace():
                return stmt
            case TempBlock():
                return stmt
            case int():
                return stmt
            case BlockPlace(block=block, index=index, offset=offset):
                return BlockPlace(block=block, index=self.simplify_stmt(index, values), offset=offset)
            case _:
                raise TypeError(f"Unexpected statement: {stmt}")

    def is_safe_to_inline(self, stmt):
        match stmt:
            case tuple():
                return False
            case IRConst():
                return True
            case IRPureInstr(op=_, args=args):
                return all(self.is_safe_to_inline(arg) for arg in args)
            case IRInstr(op=op, args=args):
                return op.pure and all(self.is_safe_to_inline(arg) for arg in args)
            case IRGet(place=place):
                return isinstance(place, SSAPlace)
            case IRSet():
                return False
            case _:
                raise TypeError(f"Unexpected statement: {stmt}")

    def simplify_fn(self, op, args, values):
        args = [self.simplify_stmt(arg, values) for arg in args]
        all_constant = all(isinstance(arg, IRConst) for arg in args)
        match op:
            case Op.Greater if all_constant:
                assert len(args) == 2
                return IRConst(args[0].value > args[1].value)
            case Op.GreaterOr if all_constant:
                assert len(args) == 2
                return IRConst(args[0].value >= args[1].value)
            case Op.Less if all_constant:
                assert len(args) == 2
                return IRConst(args[0].value < args[1].value)
            case Op.LessOr if all_constant:
                assert len(args) == 2
                return IRConst(args[0].value <= args[1].value)
            case Op.Equal if all_constant:
                assert len(args) == 2
                return IRConst(args[0].value == args[1].value)
            case Op.NotEqual if all_constant:
                assert len(args) == 2
                return IRConst(args[0].value != args[1].value)
            case Op.Not if all_constant:
                assert len(args) == 1
                return IRConst(not args[0].value)
            case Op.And:
                # We don't use And for control flow, so we can assume all arguments are pure
                if all_constant:
                    return IRConst(all(arg.value for arg in args))
                if any(isinstance(arg, IRConst) and not arg.value for arg in args):
                    return IRConst(False)
                return IRPureInstr(op=op, args=[arg for arg in args if not isinstance(arg, IRConst)])
            case Op.Or:
                # We don't use Or for control flow, so we can assume all arguments are pure
                if all_constant:
                    return IRConst(any(arg.value for arg in args))
                if any(isinstance(arg, IRConst) and arg.value for arg in args):
                    return IRConst(True)
                return IRPureInstr(op=op, args=[arg for arg in args if not isinstance(arg, IRConst)])
            case Op.Add:
                const_args = [arg for arg in args if isinstance(arg, IRConst)]
                const_sum = sum(arg.value for arg in const_args)
                other_args = [arg for arg in args if not isinstance(arg, IRConst)]
                if not other_args:
                    return IRConst(const_sum)
                if const_sum == 0:
                    return IRPureInstr(op=op, args=other_args)
                return IRPureInstr(op=op, args=[IRConst(const_sum), *other_args])
            case Op.Subtract:
                initial_arg = args[0]
                const_args = [arg for arg in args[1:] if isinstance(arg, IRConst)]
                other_args = [arg for arg in args[1:] if not isinstance(arg, IRConst)]
                const_sum = sum(arg.value for arg in const_args)
                if not other_args:
                    if const_sum == 0:
                        return initial_arg
                    if isinstance(initial_arg, IRConst):
                        return IRConst(initial_arg.value - const_sum)
                    return IRPureInstr(op=op, args=[initial_arg, IRConst(const_sum)])
                else:
                    if const_sum == 0:
                        return IRPureInstr(op=op, args=[initial_arg, *other_args])
                    if isinstance(initial_arg, IRConst):
                        return IRPureInstr(op=op, args=[IRConst(initial_arg.value - const_sum), *other_args])
                    return IRPureInstr(op=op, args=[initial_arg, IRConst(const_sum), *other_args])
            case Op.Multiply:
                const_args = [arg for arg in args if isinstance(arg, IRConst)]
                other_args = [arg for arg in args if not isinstance(arg, IRConst)]
                const_product = 1
                for arg in const_args:
                    const_product *= arg.value
                if not other_args:
                    return IRConst(const_product)
                if const_product == 0:
                    return IRConst(0)
                if const_product == 1:
                    return IRPureInstr(op=op, args=other_args)
                return IRPureInstr(op=op, args=[IRConst(const_product), *other_args])
            case Op.Divide:
                numerator = args[0]
                denominators = args[1:]
                const_denoms = [arg for arg in denominators if isinstance(arg, IRConst)]
                other_denoms = [arg for arg in denominators if not isinstance(arg, IRConst)]
                const_product = 1
                for arg in const_denoms:
                    if arg.value == 0:  # Avoid division by zero
                        return IRPureInstr(op=op, args=args)
                    const_product *= arg.value
                if not other_denoms:
                    if isinstance(numerator, IRConst):
                        assert const_product != 0
                        return IRConst(numerator.value / const_product)
                    if const_product == 1:
                        return numerator
                    return IRPureInstr(op=op, args=[numerator, IRConst(const_product)])
                else:
                    if const_product == 1:
                        return IRPureInstr(op=op, args=[numerator, *other_denoms])
                    return IRPureInstr(op=op, args=[numerator, IRConst(const_product), *other_denoms])
            case other_op:
                return (IRPureInstr if other_op.pure else IRInstr)(op=op, args=args)

    def scan(self, entry: BasicBlock) -> tuple[dict[SSAPlace, int], dict[SSAPlace, IRStmt | tuple[SSAPlace]]]:
        counts = {}
        values = {}
        for block in traverse_cfg_preorder(entry):
            for var, args in block.phis.items():
                if not isinstance(var, SSAPlace):
                    continue
                for arg in args.values():
                    counts[arg] = counts.get(arg, 0) + 1
                values[var] = tuple(args.values())
            for stmt in block.statements:
                self.scan_stmt(stmt, counts, values)
            self.scan_stmt(block.test, counts, values)
        return counts, values

    def get_dependencies(self, stmt: IRStmt, dependencies: set[SSAPlace]):
        match stmt:
            case IRConst():
                return
            case IRPureInstr(op=_, args=args) | IRInstr(op=_, args=args):
                for arg in args:
                    self.get_dependencies(arg, dependencies)
            case IRGet(place=place):
                self.get_dependencies(place, dependencies)
            case IRSet(place=place, value=value):
                if not isinstance(place, SSAPlace):
                    self.get_dependencies(value, dependencies)
                else:
                    dependencies.add(place)
                self.get_dependencies(value, dependencies)
            case SSAPlace():
                dependencies.add(stmt)
            case TempBlock():
                pass
            case int():
                return stmt
            case BlockPlace(block=block, index=index, offset=_):
                self.get_dependencies(block, dependencies)
                self.get_dependencies(index, dependencies)
            case tuple():
                for value in stmt:
                    self.get_dependencies(value, dependencies)
            case _:
                raise TypeError(f"Unexpected statement: {stmt}")

    def scan_stmt(self, stmt, counts: dict[SSAPlace, int], values: dict[SSAPlace, IRStmt]):
        match stmt:
            case IRConst():
                return
            case IRPureInstr(op=_, args=args) | IRInstr(op=_, args=args):
                for arg in args:
                    self.scan_stmt(arg, counts, values)
            case IRGet(place=place):
                self.scan_stmt(place, counts, values)
            case IRSet(place=place, value=value):
                if not isinstance(place, SSAPlace):
                    self.scan_stmt(value, counts, values)
                else:
                    counts[place] = counts.get(place, 0) + 1
                    values[place] = value
                self.scan_stmt(value, counts, values)
            case SSAPlace():
                counts[stmt] = counts.get(stmt, 0) + 1
            case TempBlock():
                pass
            case int():
                return stmt
            case BlockPlace(block=block, index=index, offset=_):
                self.scan_stmt(block, counts, values)
                self.scan_stmt(index, counts, values)
            case _:
                raise TypeError(f"Unexpected statement: {stmt}")
