from sonolus.backend.blocks import BlockData
from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet
from sonolus.backend.optimize.dominance import DominanceFrontiers, dominates
from sonolus.backend.optimize.flow import BasicBlock, FlowEdge, compute_loop_body, traverse_cfg_reverse_postorder
from sonolus.backend.optimize.passes import CompilerPass, OptimizerConfig
from sonolus.backend.place import BlockPlace, SSAPlace


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


class LoopInvariantCodeMotion(CompilerPass):
    """Copy loop-invariant expressions to a preheader block.

    Only hoists expressions that are guaranteed to execute on every iteration
    (dominate all loop latches). The subsequent CSE pass deduplicates the copies
    against the originals inside the loop, effectively hoisting the computation.
    """

    def __init__(self, name: str = "licm"):
        self.name = name

    def requires(self) -> set[CompilerPass]:
        return {DominanceFrontiers()}

    def run(self, entry: BasicBlock, config: OptimizerConfig) -> BasicBlock:
        callback = config.callback
        all_blocks = list(traverse_cfg_reverse_postorder(entry))
        loops = self._find_loops(all_blocks)
        loops.sort(key=lambda loop: loop[0].num, reverse=True)

        next_id = [0]
        for header, latches, body in loops:
            if header is entry:
                continue
            self._process_loop(header, latches, body, callback, next_id)

        return entry

    def _find_loops(self, blocks: list[BasicBlock]) -> list[tuple[BasicBlock, set[BasicBlock], set[BasicBlock]]]:
        loops_by_header: dict[BasicBlock, set[BasicBlock]] = {}
        for block in blocks:
            for edge in block.outgoing:
                if dominates(edge.dst, edge.src):
                    loops_by_header.setdefault(edge.dst, set()).add(edge.src)

        result = []
        for header, latches in loops_by_header.items():
            body: set[BasicBlock] = set()
            for latch in latches:
                body |= compute_loop_body(header, latch)
            result.append((header, latches, body))
        return result

    def _process_loop(
        self,
        header: BasicBlock,
        latches: set[BasicBlock],
        body: set[BasicBlock],
        callback: str | None,
        next_id: list[int],
    ):
        preheader = self._get_or_create_preheader(header, body)
        if preheader is None:
            return

        defs_in_loop = self._collect_defs_in_loop(body)
        guaranteed_blocks = sorted(
            (b for b in body if all(dominates(b, latch) for latch in latches)),
            key=lambda b: b.num,
        )

        hoisted: set[IRPureInstr | IRGet] = set()
        for block in guaranteed_blocks:
            for stmt in block.statements:
                self._scan_and_hoist(stmt, preheader, defs_in_loop, callback, next_id, hoisted)
            self._scan_and_hoist(block.test, preheader, defs_in_loop, callback, next_id, hoisted)

    def _get_or_create_preheader(self, header: BasicBlock, body: set[BasicBlock]) -> BasicBlock | None:
        non_back_edges = [e for e in header.incoming if e.src not in body]
        if not non_back_edges:
            return None

        if len(non_back_edges) == 1:
            pred = non_back_edges[0].src
            if len(pred.outgoing) == 1 and not pred.phis:
                return pred

        preheader = BasicBlock()

        for edge in non_back_edges:
            header.incoming.discard(edge)
            edge.src.outgoing.discard(edge)
            new_edge = FlowEdge(edge.src, preheader, edge.cond)
            edge.src.outgoing.add(new_edge)
            preheader.incoming.add(new_edge)

        preheader.connect_to(header)

        for phi_dst, phi_srcs in header.phis.items():
            preheader_values: dict[BasicBlock, SSAPlace] = {}
            for src_block in list(phi_srcs):
                if src_block not in body:
                    preheader_values[src_block] = phi_srcs.pop(src_block)
            if len(preheader_values) == 1:
                phi_srcs[preheader] = next(iter(preheader_values.values()))
            else:
                preheader.phis[phi_dst] = preheader_values
                phi_srcs[preheader] = phi_dst

        return preheader

    def _collect_defs_in_loop(self, body: set[BasicBlock]) -> set[SSAPlace]:
        defs: set[SSAPlace] = set()
        for block in body:
            for phi_dst in block.phis:
                if isinstance(phi_dst, SSAPlace):
                    defs.add(phi_dst)
            for stmt in block.statements:
                if isinstance(stmt, IRSet) and isinstance(stmt.place, SSAPlace):
                    defs.add(stmt.place)
        return defs

    def _is_loop_invariant(self, expr, defs_in_loop: set[SSAPlace], callback: str | None) -> bool:
        match expr:
            case IRConst():
                return True
            case IRGet(place=SSAPlace() as place):
                return place not in defs_in_loop
            case IRGet(place=BlockPlace(block=block, index=index)):
                if not (isinstance(block, BlockData) and callback not in block.writable):
                    return False
                if isinstance(index, int):
                    return True
                if isinstance(index, SSAPlace):
                    return index not in defs_in_loop
                return self._is_loop_invariant(index, defs_in_loop, callback)
            case IRPureInstr(op=op, args=args):
                return (
                    op.pure
                    and not op.side_effects
                    and all(self._is_loop_invariant(a, defs_in_loop, callback) for a in args)
                )
            case _:
                return False

    def _scan_and_hoist(self, expr, preheader, defs_in_loop, callback, next_id, hoisted):
        match expr:
            case IRPureInstr(args=args) if self._is_loop_invariant(expr, defs_in_loop, callback):
                if _cost(expr) >= 4 and expr not in hoisted:
                    hoisted.add(expr)
                    place = SSAPlace(self.name, next_id[0])
                    next_id[0] += 1
                    preheader.statements.append(IRSet(place, expr))
            case IRPureInstr(args=args) | IRInstr(args=args):
                for arg in args:
                    self._scan_and_hoist(arg, preheader, defs_in_loop, callback, next_id, hoisted)
            case IRGet(place=BlockPlace() as place) if self._is_loop_invariant(expr, defs_in_loop, callback):
                if _cost(expr) >= 4 and expr not in hoisted:
                    hoisted.add(expr)
                    ssa_place = SSAPlace(self.name, next_id[0])
                    next_id[0] += 1
                    preheader.statements.append(IRSet(ssa_place, expr))
            case IRGet(place=BlockPlace(index=index)):
                self._scan_and_hoist(index, preheader, defs_in_loop, callback, next_id, hoisted)
            case IRSet(place=place, value=value):
                self._scan_and_hoist(value, preheader, defs_in_loop, callback, next_id, hoisted)
                if isinstance(place, BlockPlace):
                    self._scan_and_hoist(place.index, preheader, defs_in_loop, callback, next_id, hoisted)
