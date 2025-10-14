from sonolus.backend.blocks import BlockData
from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet, IRStmt
from sonolus.backend.optimize.flow import BasicBlock, traverse_cfg_preorder
from sonolus.backend.optimize.passes import CompilerPass, OptimizerConfig
from sonolus.backend.place import BlockPlace, SSAPlace, TempBlock

RUNTIME_CONSTANT_BLOCKS = {
    "RuntimeEnvironment",
    "RuntimeUI",
    "RuntimeUIConfiguration",
    "LevelData",
    "LevelOption",
    "LevelBucket",
    "LevelScore",
    "LevelLife",
    "EngineRom",
    "ArchetypeLife",
    "RuntimeCanvas",
    "PreviewData",
    "PreviewOption",
    "TutorialData",
}


class InlineVars(CompilerPass):
    def run(self, entry: BasicBlock, config: OptimizerConfig) -> BasicBlock:
        use_counts: dict[SSAPlace, int] = {}
        definitions: dict[SSAPlace, IRStmt] = {}

        for block in traverse_cfg_preorder(entry):
            for stmt in block.statements:
                self.count_uses(stmt, use_counts)
                if isinstance(stmt, IRSet) and isinstance(stmt.place, SSAPlace):
                    definitions[stmt.place] = stmt.value
            self.count_uses(block.test, use_counts)
            for tgt, args in block.phis.items():
                for arg in args.values():
                    self.count_uses(arg, use_counts)
                if len(args) == 1:
                    arg = next(iter(args.values()))
                    definitions[tgt] = IRGet(place=arg)

        for defn in definitions.values():
            if isinstance(defn, IRGet) and isinstance(defn.place, SSAPlace):
                use_counts[defn.place] -= 1

        canonical_definitions: dict[SSAPlace, IRStmt] = {}
        for p, defn in definitions.items():
            canonical_definitions[p] = defn
            # Update the definition if it's a Get from another SSAPlace until we reach a definition that is not a Get
            while defn and isinstance(defn, IRGet) and isinstance(defn.place, SSAPlace):
                canonical_definitions[p] = defn
                defn = definitions.get(defn.place)  # Can be None if it's a phi
            canonical_defn = canonical_definitions[p]
            if (
                use_counts.get(p, 0) > 0
                and isinstance(canonical_defn, IRGet)
                and isinstance(canonical_defn.place, SSAPlace)
            ):
                use_counts[canonical_defn.place] = use_counts.get(canonical_defn.place, 0) + 1

        for p, defn in canonical_definitions.items():
            if isinstance(defn, IRGet) and isinstance(defn.place, SSAPlace):
                inner_p = defn.place
                inner_defn = canonical_definitions.get(inner_p)
                if (
                    inner_defn
                    and self.is_inlinable(inner_defn, config.callback)
                    and (use_counts.get(inner_p, 0) <= 1 or self.is_free_to_inline(inner_defn, config.callback))
                ):
                    canonical_definitions[p] = inner_defn

        inlined_definitions = {**canonical_definitions}
        for p, defn in canonical_definitions.items():
            while True:
                inlinable_uses = self.get_inlinable_uses(defn, set())
                subs = {}
                for inside_p in inlinable_uses:
                    if inside_p not in canonical_definitions:
                        continue
                    inside_defn = canonical_definitions[inside_p]
                    if not self.is_inlinable(inside_defn, config.callback):
                        continue
                    if (
                        (isinstance(inside_defn, IRGet) and isinstance(inside_defn.place, SSAPlace))
                        or use_counts[inside_p] == 1
                        or self.is_free_to_inline(inside_defn, config.callback)
                    ):
                        subs[inside_p] = inside_defn
                if not subs:
                    break
                defn = self.substitute(defn, subs)
            inlined_definitions[p] = defn

        valid = {
            p
            for p in inlined_definitions
            if self.is_inlinable(inlined_definitions[p], config.callback)
            and (use_counts.get(p, 0) <= 1 or self.is_free_to_inline(inlined_definitions[p], config.callback))
        }

        for block in traverse_cfg_preorder(entry):
            new_statements = []
            for stmt in [*block.statements, block.test]:
                if (
                    isinstance(stmt, IRSet)
                    and isinstance(stmt.place, SSAPlace)
                    and isinstance(stmt.value, IRGet)
                    and isinstance(stmt.value.place, SSAPlace)
                ):
                    # Don't bother inlining a direct alias since it can get optimized away later and
                    # reordering can reduce optimality since we don't have many other code motion optimizations.
                    new_statements.append(stmt)
                    continue
                while True:
                    inlinable_uses = self.get_inlinable_uses(stmt, set())
                    subs = {}
                    for p in inlinable_uses:
                        if p in valid:
                            subs[p] = inlined_definitions[p]

                    if subs:
                        stmt = self.substitute(stmt, subs)
                    else:
                        new_statements.append(stmt)
                        break

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

    def is_inlinable(self, stmt, callback: str):
        match stmt:
            case IRConst():
                return True
            case IRInstr(op=op, args=args) | IRPureInstr(op=op, args=args):
                return not op.side_effects and op.pure and all(self.is_inlinable(arg, callback) for arg in args)
            case IRGet():
                return isinstance(stmt.place, SSAPlace) or (
                    isinstance(stmt.place, BlockPlace)
                    and isinstance(stmt.place.block, BlockData)
                    and callback not in stmt.place.block.writable
                    and isinstance(stmt.place.index, int | SSAPlace)
                )
            case IRSet():
                return False
            case _:
                raise TypeError(f"Unexpected statement: {stmt}")

    def is_free_to_inline(self, stmt: IRStmt, callback: str) -> bool:
        match stmt:
            case IRConst():
                return True
            case IRInstr() | IRPureInstr():
                return self.is_runtime_constant(stmt, callback)
            case IRGet():
                return isinstance(stmt.place, SSAPlace) or (
                    isinstance(stmt.place, BlockPlace)
                    and isinstance(stmt.place.block, float | int)
                    and isinstance(stmt.place.index, float | int)
                )
            case IRSet():
                return False
            case _:
                raise TypeError(f"Unexpected statement: {stmt}")

    def is_runtime_constant(self, stmt: IRStmt, callback: str) -> bool:
        match stmt:
            case IRConst():
                return True
            case IRInstr(op=op, args=args) | IRPureInstr(op=op, args=args):
                return not op.side_effects and op.pure and all(self.is_runtime_constant(arg, callback) for arg in args)
            case IRGet():
                return (
                    isinstance(stmt.place, BlockPlace)
                    and isinstance(stmt.place.block, BlockData)
                    and callback not in stmt.place.block.writable
                    and stmt.place.block.name in RUNTIME_CONSTANT_BLOCKS
                    and isinstance(stmt.place.index, int | SSAPlace)
                )
            case IRSet():
                return False
            case _:
                raise TypeError(f"Unexpected statement: {stmt}")
