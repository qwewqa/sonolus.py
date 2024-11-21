from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet, IRStmt
from sonolus.backend.optimize.flow import BasicBlock, traverse_cfg_preorder
from sonolus.backend.optimize.passes import CompilerPass
from sonolus.backend.place import BlockPlace, SSAPlace, TempBlock


class InlineVars(CompilerPass):
    def run(self, entry: BasicBlock) -> BasicBlock:
        use_counts: dict[SSAPlace, int] = {}
        definitions: dict[SSAPlace, IRStmt] = {}

        for block in traverse_cfg_preorder(entry):
            for stmt in block.statements:
                self.count_uses(stmt, use_counts)
                if isinstance(stmt, IRSet) and isinstance(stmt.place, SSAPlace):
                    definitions[stmt.place] = stmt.value
            self.count_uses(block.test, use_counts)

        for p, defn in definitions.items():
            while True:
                if isinstance(defn, IRGet) and isinstance(defn.place, SSAPlace) and defn.place in definitions:
                    inside_defn = definitions[defn.place]
                    if not self.is_inlinable(inside_defn):
                        break
                    defn = inside_defn
                    continue
                inlinable_uses = self.get_inlinable_uses(defn, set())
                subs = {}
                for inside_p in inlinable_uses:
                    if inside_p not in definitions:
                        continue
                    inside_defn = definitions[inside_p]
                    if not self.is_inlinable(inside_defn):
                        continue
                    if (isinstance(inside_defn, IRGet) and isinstance(inside_defn.place, SSAPlace)) or use_counts[
                        inside_p
                    ] == 1:
                        subs[inside_p] = inside_defn
                if not subs:
                    break
                defn = self.substitute(defn, subs)
            definitions[p] = defn

        valid = {p for p in definitions if self.is_inlinable(definitions[p]) and use_counts.get(p, 0) <= 1}

        for block in traverse_cfg_preorder(entry):
            new_statements = []
            for stmt in [*block.statements, block.test]:
                inlinable_uses = self.get_inlinable_uses(stmt, set())
                subs = {}
                for p in inlinable_uses:
                    if p not in valid:
                        continue
                    definition = definitions[p]
                    subs[p] = definition

                if subs:
                    new_statements.append(self.substitute(stmt, subs))
                else:
                    new_statements.append(stmt)

            block.statements = new_statements[:-1]
            block.test = new_statements[-1]

        return entry

    def substitute(self, stmt, subs):
        match stmt:
            case IRConst():
                return stmt
            case IRInstr(op=op, args=args):
                return IRInstr(op=op, args=[self.substitute(arg, subs) for arg in args])
            case IRPureInstr(op=op, args=args):
                return IRPureInstr(op=op, args=[self.substitute(arg, subs) for arg in args])
            case IRGet(place=place):
                if place in subs:
                    return subs[place]
                return stmt
            case IRSet(place=place, value=value):
                return IRSet(place=place, value=self.substitute(value, subs))
            case _:
                raise TypeError(f"Unexpected statement: {stmt}")

    def count_uses(self, stmt, use_counts):
        match stmt:
            case IRConst():
                pass
            case IRInstr(op=_, args=args) | IRPureInstr(op=_, args=args):
                for arg in args:
                    self.count_uses(arg, use_counts)
            case IRGet(place=place):
                self.count_uses(place, use_counts)
            case IRSet(place=place, value=value):
                if not isinstance(place, SSAPlace):  # We don't want to count the definition itself
                    self.count_uses(place, use_counts)
                self.count_uses(value, use_counts)
            case SSAPlace():
                use_counts[stmt] = use_counts.get(stmt, 0) + 1
            case BlockPlace(block=block, index=index, offset=_):
                self.count_uses(block, use_counts)
                self.count_uses(index, use_counts)
            case int() | float():
                pass
            case TempBlock():
                pass
            case _:
                raise TypeError(f"Unexpected statement: {stmt}")
        return use_counts

    def get_inlinable_uses(self, stmt, uses):
        match stmt:
            case IRConst():
                pass
            case IRInstr(op=_, args=args) | IRPureInstr(op=_, args=args):
                for arg in args:
                    self.get_inlinable_uses(arg, uses)
            case IRGet(place=place):
                if isinstance(place, SSAPlace):
                    uses.add(place)
            case IRSet(place=_, value=value):
                self.get_inlinable_uses(value, uses)
            case _:
                raise TypeError(f"Unexpected statement: {stmt}")
        return uses

    def is_inlinable(self, stmt):
        match stmt:
            case IRConst():
                return True
            case IRInstr(op=op, args=args) | IRPureInstr(op=op, args=args):
                return not op.side_effects and op.pure and all(self.is_inlinable(arg) for arg in args)
            case IRGet():
                return isinstance(stmt.place, SSAPlace)
            case IRSet():
                return False
            case _:
                raise TypeError(f"Unexpected statement: {stmt}")
