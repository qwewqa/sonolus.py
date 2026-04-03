from sonolus.backend.blocks import BlockData
from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet
from sonolus.backend.ops import Op
from sonolus.backend.optimize.dominance import DominanceFrontiers
from sonolus.backend.optimize.flow import BasicBlock, traverse_cfg_preorder
from sonolus.backend.optimize.passes import CompilerPass, OptimizerConfig
from sonolus.backend.place import BlockPlace, SSAPlace

# Limited in what we can do here since floating-point math is not commutative
COMMUTATIVE_OPS = frozenset({Op.Equal, Op.NotEqual, Op.Max, Op.Min})


def _sort_key(expr):
    match expr:
        case IRConst(value=value):
            return (0, value)
        case IRGet(place=SSAPlace(name=name, num=num)):
            return (1, 0, name, num)
        case IRGet(place=BlockPlace(block=block, index=index, offset=offset)):
            index_key = (
                _sort_key(index)
                if isinstance(index, IRConst | IRPureInstr | IRGet)
                else ((index.name, index.num) if isinstance(index, SSAPlace) else (index,))
            )
            return (1, 1, int(block) if isinstance(block, int) else 0, index_key, offset)
        case IRPureInstr(op=op, args=args):
            return (2, op.value, tuple(_sort_key(arg) for arg in args))
        case _:
            return (3,)


def _cost(expr) -> int:
    match expr:
        case IRConst():
            return 1
        case IRGet(place=SSAPlace()):
            return 3
        case IRGet(place=BlockPlace(block=block, index=index)):
            return 1 + _cost(block) + _cost(index)
        case IRPureInstr(args=args):
            return 1 + sum(_cost(arg) for arg in args)
        case int():
            return 1
        case SSAPlace():
            return 3
        case _:
            return 1


class CommonSubexpressionElimination(CompilerPass):
    """Global CSE using the dominator tree with subexpression extraction.

    Walks the dominator tree in preorder. Every CSE-candidate pure expression
    with cost >= 4 is extracted into an SSA variable on first sight. Duplicates
    are replaced with a reference to the existing variable. A later InlineVars
    pass cleans up any single-use extractions.

    For top-level IRSet(SSAPlace, expr), the existing variable is reused (free).

    Also canonicalizes commutative operations by sorting their arguments.
    """

    def requires(self) -> set[CompilerPass]:
        return {DominanceFrontiers()}

    def run(self, entry: BasicBlock, config: OptimizerConfig) -> BasicBlock:
        callback = config.callback

        # Phase 1: Canonicalize all expressions
        for block in traverse_cfg_preorder(entry):
            block.statements = [self._canonicalize_stmt(stmt, callback) for stmt in block.statements]
            block.test = self._canonicalize_stmt(block.test, callback)

        # Phase 2: Rewrite with extraction
        next_id = [0]
        self._process_block(entry, {}, callback, next_id)
        return entry

    def _is_cse_candidate(self, expr, callback: str | None) -> bool:
        match expr:
            case IRConst():
                return True
            case IRGet(place=SSAPlace()):
                return True
            case IRGet(place=BlockPlace(block=block, index=index)):
                return (isinstance(block, BlockData) and callback not in block.writable) and (
                    isinstance(index, int | SSAPlace) or self._is_cse_candidate(index, callback)
                )
            case IRPureInstr(op=op, args=args):
                return op.pure and not op.side_effects and all(self._is_cse_candidate(arg, callback) for arg in args)
            case _:
                return False

    def _canonicalize(self, expr, callback: str | None):
        if not self._is_cse_candidate(expr, callback):
            return expr
        match expr:
            case IRPureInstr(op=op, args=args):
                args = [self._canonicalize(arg, callback) for arg in args]
                if op in COMMUTATIVE_OPS:
                    args = sorted(args, key=lambda x: tuple(str(k) for k in _sort_key(x)))
                return IRPureInstr(op=op, args=args)
            case IRGet(place=BlockPlace(block=block, index=index, offset=offset)):
                new_block = (
                    self._canonicalize(block, callback) if isinstance(block, IRConst | IRPureInstr | IRGet) else block
                )
                new_index = (
                    self._canonicalize(index, callback) if isinstance(index, IRConst | IRPureInstr | IRGet) else index
                )
                if new_block is not block or new_index is not index:
                    return IRGet(BlockPlace(new_block, new_index, offset))
                return expr
            case _:
                return expr

    def _canonicalize_stmt(self, stmt, callback: str | None):
        match stmt:
            case IRPureInstr():
                return self._canonicalize(stmt, callback)
            case IRInstr(op=op, args=args):
                return IRInstr(op=op, args=[self._canonicalize_stmt(arg, callback) for arg in args])
            case IRGet():
                return self._canonicalize(stmt, callback)
            case IRSet(place=place, value=value):
                new_place = self._canonicalize_stmt(place, callback)
                return IRSet(place=new_place, value=self._canonicalize_stmt(value, callback))
            case BlockPlace(block=block, index=index, offset=offset):
                new_block = self._canonicalize_stmt(block, callback)
                new_index = self._canonicalize_stmt(index, callback)
                if new_block is not block or new_index is not index:
                    return BlockPlace(new_block, new_index, offset)
                return stmt
            case _:
                return stmt

    def _process_block(
        self,
        block: BasicBlock,
        available: dict[IRPureInstr, SSAPlace],
        callback: str | None,
        next_id: list[int],
    ):
        added: list[IRPureInstr] = []

        new_statements: list = []
        for stmt in block.statements:
            pre_stmts: list[IRSet] = []
            stmt = self._process_stmt(stmt, available, callback, pre_stmts, next_id, added)
            new_statements.extend(pre_stmts)
            new_statements.append(stmt)

        pre_stmts = []
        block.test = self._process_expr(block.test, available, callback, pre_stmts, next_id, added)
        new_statements.extend(pre_stmts)

        block.statements = new_statements

        for child in block.dom_children:
            self._process_block(child, available, callback, next_id)

        for expr in added:
            del available[expr]

    def _process_stmt(self, stmt, available, callback, pre_stmts, next_id, added):
        match stmt:
            case IRSet(place=SSAPlace() as place, value=IRPureInstr() as value) if self._is_cse_candidate(
                value, callback
            ):
                new_place = place
                new_args = [
                    self._process_expr(arg, available, callback, pre_stmts, next_id, added) for arg in value.args
                ]
                new_value = IRPureInstr(op=value.op, args=new_args)
                if isinstance(new_place, SSAPlace) and self._is_cse_candidate(new_value, callback):
                    if new_value not in available:
                        available[new_value] = new_place
                        added.append(new_value)
                    if value not in available:
                        available[value] = new_place
                        added.append(value)
                return IRSet(new_place, new_value)
            case IRSet(place=place, value=value):
                new_place = self._process_expr(place, available, callback, pre_stmts, next_id, added)
                new_value = self._process_expr(value, available, callback, pre_stmts, next_id, added)
                if isinstance(new_place, SSAPlace) and self._is_cse_candidate(new_value, callback):
                    if new_value not in available:
                        available[new_value] = new_place
                        added.append(new_value)
                    if value not in available:
                        available[value] = new_place
                        added.append(value)
                return IRSet(new_place, new_value)
            case _:
                return self._process_expr(stmt, available, callback, pre_stmts, next_id, added)

    def _process_expr(self, expr, available, callback, pre_stmts, next_id, added):
        if expr in available:
            return IRGet(available[expr])
        match expr:
            case IRPureInstr(op=op, args=args) if self._is_cse_candidate(expr, callback):
                new_args = [self._process_expr(arg, available, callback, pre_stmts, next_id, added) for arg in args]
                new_expr = IRPureInstr(op=op, args=new_args)
                if new_expr in available:
                    return IRGet(available[new_expr])
                if _cost(new_expr) >= 4:
                    new_place = SSAPlace("_cse", next_id[0])
                    next_id[0] += 1
                    pre_stmts.append(IRSet(new_place, new_expr))
                    available[new_expr] = new_place
                    added.append(new_expr)
                    if expr != new_expr:
                        available[expr] = new_place
                        added.append(expr)
                    return IRGet(new_place)
                return new_expr
            case IRPureInstr(op=op, args=args) | IRInstr(op=op, args=args):
                new_args = [self._process_expr(arg, available, callback, pre_stmts, next_id, added) for arg in args]
                new_expr = type(expr)(op=op, args=new_args)
                if new_expr in available:
                    return IRGet(available[new_expr])
                return new_expr
            case IRGet(place=place):
                new_place = self._process_expr(place, available, callback, pre_stmts, next_id, added)
                if new_place is not place:
                    new_expr = IRGet(new_place)
                    if new_expr in available:
                        return IRGet(available[new_expr])
                    return new_expr
                return expr
            case BlockPlace(block=block, index=index, offset=offset):
                new_block = self._process_expr(block, available, callback, pre_stmts, next_id, added)
                new_index = self._process_expr(index, available, callback, pre_stmts, next_id, added)
                if new_block is not block or new_index is not index:
                    return BlockPlace(new_block, new_index, offset)
                return expr
            case _:
                return expr
