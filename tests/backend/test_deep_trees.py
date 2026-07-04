"""Deep expression-chain regressions (finding C3).

A compile-time-unrolled ``s = s - 1`` accumulation is promoted by mem2reg into an
N-deep single-use chain that treeify folds into ONE expression tree, descended by
every recursive tree walker downstream. Pre-fix this crashed the *process* with a
C-stack overflow (no catchable Python exception), so these tests assert only the
POST-fix behavior: an N=4000 chain lowers, emits, and interprets to a structurally
sane, semantically correct node at both STANDARD and FAST.

Three sibling shapes cover the walkers the straight single-use chain misses:

* multi-use top: sends phase 2 down the ``_tree_cost`` pricing walk (pre-fix:
  crash at both FAST and STANDARD);
* deep runtime-constant if-conversion arm: used to price as cost 1 regardless of
  depth and emit an unbounded FLAG_MUST_FOLD tree past the cap (pre-fix: STANDARD
  crash) -- now ``_IfConv``'s depth-bounded walks reject the conversion;
* ``if_convert`` alone: its ``_is_rtc``/``_arm_cost`` recursion itself used to
  overflow before any conversion decision.

The cap keeps the deepest emitted tree ~_MAX_FOLD_DEPTH deep -- still past
CPython's default recursion limit and the ~1 MB Windows main-thread C stack, so
interpretation runs in a large-stack worker thread with a raised recursion limit.
"""

from __future__ import annotations

import sys
import threading

import pytest

from sonolus.backend._opt import lower  # noqa: PLC2701
from sonolus.backend.blocks import PlayBlock
from sonolus.backend.interpret import Interpreter
from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet
from sonolus.backend.mode import Mode
from sonolus.backend.node import FunctionNode
from sonolus.backend.ops import Op
from sonolus.backend.optimize import (
    FAST_PASSES,
    MINIMAL_PASSES,
    STANDARD_PASSES,
    OptimizerConfig,
    cfg_to_engine_node,
    run_passes,
)
from sonolus.backend.optimize.flow import BasicBlock
from sonolus.backend.place import BlockPlace, TempBlock

# Deep enough to cross the ~2200-frame Windows crash threshold several times over
# (and the _MAX_FOLD_DEPTH=1000 cap ~4 times) while staying quick to build/run.
_N = 4000

# Plain int memory block for the (non-constant) chain seed -- reading it keeps the
# whole chain out of the compile-time constant lattice so it actually survives to
# lowering as a deep tree instead of folding to a single constant.
_SEED_BLOCK = 20

# EngineRom's block id (PLACE_RUNTIME_CONST under PLAY): reads of it seed the
# runtime-constant chains below and are preset in the interpreter for parity runs.
_ROM_BLOCK = 3000

# Mode/callback context under which PlayBlock.RuntimeUpdate is readable but not
# writable (an inlinable, non-runtime-constant seed) and EngineRom is readable.
_PLAY_CFG = OptimizerConfig(Mode.PLAY, "updateSequential")


def _build_chain(n: int) -> BasicBlock:
    """A single block: seed a scalar temp from memory, then ``t = t - 1`` n times.

    Subtract is neither associative nor n-ary-flattened, so the mem2reg-promoted
    SSA chain reaches treeify as a genuinely n-deep single-use nest (no
    reassociation collapses it, and the non-constant seed blocks constant folding).
    The trailing DebugLog keeps the final value live and makes it observable.
    """
    acc = BlockPlace(TempBlock("acc", 1), 0, 0)
    statements = [IRSet(acc, IRGet(BlockPlace(_SEED_BLOCK, 0, 0)))]
    statements += [IRSet(acc, IRPureInstr(Op.Subtract, [IRGet(acc), IRConst(1)])) for _ in range(n)]
    statements.append(IRInstr(Op.DebugLog, [IRGet(acc)]))
    return BasicBlock(statements=statements)


def _build_multiuse_top_chain(n: int) -> BasicBlock:
    """Like ``_build_chain`` but the chain is INLINABLE and its top value has TWO uses.

    Under PLAY/updateSequential, ``RuntimeUpdate`` is readable, non-writable, and
    not runtime-constant, so the whole chain is inlinable-anywhere; the two
    DebugLogs make the top value multi-use, sending phase 2 of ``_Lower._analyze``
    down the ``_tree_cost`` pricing walk.
    """
    acc = BlockPlace(TempBlock("acc", 1), 0, 0)
    statements = [IRSet(acc, IRGet(BlockPlace(PlayBlock.RuntimeUpdate, 0, 0)))]
    statements += [IRSet(acc, IRPureInstr(Op.Subtract, [IRGet(acc), IRConst(1)])) for _ in range(n)]
    statements.append(IRInstr(Op.DebugLog, [IRGet(acc)]))
    statements.append(IRInstr(Op.DebugLog, [IRGet(acc)]))
    return BasicBlock(statements=statements)


def _build_rtc_arm_diamond(n: int) -> BasicBlock:
    """A diamond whose true arm is an n-deep RUNTIME-CONSTANT pure chain.

    The arm chain subtracts from a head-hoisted EngineRom (PLACE_RUNTIME_CONST)
    read, so every arm value is a legal, single-use, pure, runtime-constant op --
    pre-fix it priced at cost 1 regardless of depth, converted, and emitted an
    unbounded FLAG_MUST_FOLD tree. The two-statement join keeps a real join phi
    (cfg_cleanup does not dissolve it), and both DebugLogs keep the merged value
    observable.
    """
    a = BlockPlace(TempBlock("a", 1), 0, 0)
    r = BlockPlace(TempBlock("r", 1), 0, 0)
    head = BasicBlock(
        statements=[IRSet(a, IRGet(BlockPlace(PlayBlock.EngineRom, 0, 0)))],
        test=IRGet(BlockPlace(_SEED_BLOCK, 3, 0)),
    )
    t_statements = [IRSet(r, IRGet(a))]
    t_statements += [IRSet(r, IRPureInstr(Op.Subtract, [IRGet(r), IRConst(1)])) for _ in range(n)]
    t = BasicBlock(statements=t_statements)
    f = BasicBlock(statements=[IRSet(r, IRConst(0))])
    j = BasicBlock(
        statements=[
            IRInstr(Op.DebugLog, [IRGet(r)]),
            IRInstr(Op.DebugLog, [IRPureInstr(Op.Add, [IRGet(r), IRConst(1)])]),
        ]
    )
    head.connect_to(f, 0)
    head.connect_to(t, None)
    t.connect_to(j, None)
    f.connect_to(j, None)
    return head


def _count_ops(node) -> tuple[int, int, bool]:
    """(total FunctionNodes, Subtract nodes, has DebugLog) via an EXPLICIT stack.

    Iterative on purpose: the emitted tree is ~1000 deep, so a recursive walk here
    would itself risk the very C-stack overflow under test.
    """
    total = 0
    subtracts = 0
    has_debug_log = False
    stack = [node]
    while stack:
        cur = stack.pop()
        if not isinstance(cur, FunctionNode):
            continue
        total += 1
        if cur.func == Op.Subtract:
            subtracts += 1
        elif cur.func == Op.DebugLog:
            has_debug_log = True
        stack.extend(cur.args)
    return total, subtracts, has_debug_log


def _interpret_deep(node, mem: dict[int, list[float]] | None = None):
    """Run the recursive interpreter on a deep node in a large-stack thread.

    Windows' ~1 MB main-thread stack (and CPython's 1000-frame default recursion
    limit) cannot hold the ~1000-deep recursive walk; a 128 MB worker thread with a
    raised limit can. ``mem`` presets memory blocks (block id -> values).
    Returns the interpreter's log.
    """
    box: dict[str, object] = {}

    def _target():
        old_limit = sys.getrecursionlimit()
        sys.setrecursionlimit(200000)
        try:
            it = Interpreter()
            for block, values in (mem or {}).items():
                it.blocks[block] = list(values)
            it.run(node)
            box["log"] = list(it.log)
        except BaseException as exc:  # surface the failure on the main thread
            box["error"] = exc
        finally:
            # The recursion limit is interpreter-wide (only the depth COUNTER is
            # per-thread), so restore it here or the raised limit would leak into
            # every later test in this worker process.
            sys.setrecursionlimit(old_limit)

    old_size = threading.stack_size(128 * 1024 * 1024)
    try:
        worker = threading.Thread(target=_target)
        worker.start()
        worker.join()
    finally:
        threading.stack_size(old_size)

    if "error" in box:
        raise box["error"]  # type: ignore[misc]
    return box["log"]


def _assert_structurally_sane(node) -> None:
    node_count, subtract_count, has_debug_log = _count_ops(node)
    assert isinstance(node, FunctionNode), node
    # Every Subtract survives lowering/emission (none dropped, merged, or
    # reassociated away): the chain arrived intact, just split across the temps the
    # depth cap materializes.
    assert subtract_count == _N, subtract_count
    assert has_debug_log
    assert node_count > _N


def test_deep_chain_standard_lowers_emits_and_interprets():
    entry = _build_chain(_N)
    lowered = run_passes(entry, STANDARD_PASSES)  # (pre-fix: process crash here)
    node = cfg_to_engine_node(lowered)
    _assert_structurally_sane(node)

    # Semantic parity: the depth cap materializes temps mid-chain, which must not
    # change the computed value. MINIMAL bypasses treeify (shallow, iterative), so
    # it is an independent reference for the folded STANDARD result.
    ref_log = _interpret_deep(cfg_to_engine_node(run_passes(entry, MINIMAL_PASSES)))
    std_log = _interpret_deep(node)
    assert len(std_log) == 1
    assert std_log == ref_log


def test_deep_chain_fast_lowers_emits_and_interprets():
    entry = _build_chain(_N)
    lowered = run_passes(entry, FAST_PASSES)  # (pre-fix: process crash here)
    node = cfg_to_engine_node(lowered)
    _assert_structurally_sane(node)

    ref_log = _interpret_deep(cfg_to_engine_node(run_passes(entry, MINIMAL_PASSES)))
    fast_log = _interpret_deep(node)
    assert len(fast_log) == 1
    assert fast_log == ref_log


@pytest.mark.parametrize("level", [FAST_PASSES, STANDARD_PASSES], ids=["fast", "standard"])
def test_deep_chain_multi_use_top_lowers_emits_and_interprets(level):
    entry = _build_multiuse_top_chain(_N)
    lowered = run_passes(entry, level, _PLAY_CFG)  # (pre-fix: process crash here)
    node = cfg_to_engine_node(lowered)
    node_count, subtract_count, has_debug_log = _count_ops(node)
    assert subtract_count == _N, subtract_count
    assert has_debug_log
    assert node_count > _N

    ref_log = _interpret_deep(cfg_to_engine_node(run_passes(entry, MINIMAL_PASSES, _PLAY_CFG)))
    opt_log = _interpret_deep(node)
    assert len(opt_log) == 2
    assert opt_log == ref_log


def test_deep_runtime_constant_arm_rejected_and_pipeline_bounded():
    # The deep runtime-constant arm must NOT if-convert (its cost walk saturates
    # past the budget)...
    _cfg, conversions = lower.run_ifconv_counted(_build_rtc_arm_diamond(_N), Mode.PLAY, "updateSequential")
    assert conversions == 0
    # ...while a shallow runtime-constant arm still converts exactly as before
    # the depth bounds (below-cap decisions must be unchanged).
    _cfg, conversions = lower.run_ifconv_counted(_build_rtc_arm_diamond(40), Mode.PLAY, "updateSequential")
    assert conversions == 1

    entry = _build_rtc_arm_diamond(_N)
    lowered = run_passes(entry, STANDARD_PASSES, _PLAY_CFG)  # (pre-fix: process crash here)
    node = cfg_to_engine_node(lowered)
    node_count, subtract_count, has_debug_log = _count_ops(node)
    assert subtract_count == _N, subtract_count
    assert has_debug_log
    assert node_count > _N

    # Semantic parity on BOTH branch directions vs the MINIMAL reference.
    for test_value in (0.0, 1.0):
        mem = {_SEED_BLOCK: [0.0, 0.0, 0.0, test_value], _ROM_BLOCK: [7.0]}
        ref_log = _interpret_deep(cfg_to_engine_node(run_passes(entry, MINIMAL_PASSES, _PLAY_CFG)), mem)
        std_log = _interpret_deep(node, mem)
        assert len(std_log) == 2
        assert std_log == ref_log


def test_if_convert_walks_alone_are_depth_bounded():
    # if_convert's own _is_rtc/_arm_cost recursion used to overflow the C stack at
    # arm depth ~8000 even before any conversion decision; probe it above that.
    _cfg, conversions = lower.run_ifconv_counted(_build_rtc_arm_diamond(9000), Mode.PLAY, "updateSequential")
    assert conversions == 0
