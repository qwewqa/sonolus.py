"""LICM tests.

Loop-invariant code motion on SSA form: hoist pure / effectively-pure
(non-writable static real-block reads), loop-invariant, guaranteed-to-execute
(def block dominates every latch) values whose EFFECTIVE cost is >= 4 into a
preheader. Runtime-constant subtrees have effective cost 1, so they NEVER hoist --
a dedicated temp would defeat the runtime's own constant folding.

Three layers:

* structural -- inspect the ``["cfg_cleanup","ssa","gvn","dce","licm"]`` SSA
  exports (a hoisted value moves out of the phi-carrying loop block into a
  no-phi preheader; verify() runs after every phase inside ``debug_run``);
* semantic parity -- interpret hand-built loops through the full standard
  pipeline (``...,"midend_standard","lower","packing"``) vs the MINIMAL
  reference, matching log + observable memory;
* corpus + random-CFG -- covered by test_rewrite_switch.py, whose corpus and
  random-CFG differentials run the shared standard mid-end path (LICM +
  rewrite_switch).
"""

from __future__ import annotations

import math
import re

from sonolus.backend._opt import ir  # noqa: PLC2701
from sonolus.backend.blocks import PlayBlock
from sonolus.backend.interpret import Interpreter
from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet
from sonolus.backend.mode import Mode
from sonolus.backend.ops import Op
from sonolus.backend.optimize import MINIMAL_PASSES, OptimizerConfig, cfg_to_engine_node, run_passes
from sonolus.backend.optimize.flow import BasicBlock, cfg_to_text
from sonolus.backend.place import BlockPlace, TempBlock

RU = PlayBlock.RuntimeUpdate  # read-only under any callback, NOT runtime-constant
ROM_CONST = PlayBlock.LevelData  # read-only + runtime-constant (RUNTIME_CONSTANT_BLOCKS)
WBLOCK = 20  # raw int -> conservatively writable

_ROM = [float("nan"), float("inf"), float("-inf")]

_SSA_PRE = ["cfg_cleanup", "ssa", "gvn", "dce"]
_SSA_LICM = ["cfg_cleanup", "ssa", "gvn", "dce", "licm"]
_STD = ["cfg_cleanup", "ssa", "midend_standard", "lower", "packing"]


def _sc(name: str) -> BlockPlace:
    return BlockPlace(TempBlock(name, 1), 0, 0)


def _rd(name: str) -> IRGet:
    return IRGet(_sc(name))


def _ru(i: int) -> IRGet:
    return IRGet(BlockPlace(RU, i))


def _rc(i: int) -> IRGet:
    return IRGet(BlockPlace(ROM_CONST, i))


def _w(i: int) -> IRGet:
    return IRGet(BlockPlace(WBLOCK, i))


def _log(v) -> IRInstr:
    return IRInstr(Op.DebugLog, [v if not isinstance(v, (int, float)) else IRConst(v)])


def _text(build, phases, mode=Mode.PLAY, cb=None) -> str:
    return cfg_to_text(ir.debug_run(build(), mode, cb, phases=phases))


def _parse_sections(text: str) -> dict[int, str]:
    sections: dict[int, str] = {}
    cur = None
    buf: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^(\d+):$", line)
        if m:
            if cur is not None:
                sections[cur] = "\n".join(buf)
            cur = int(m.group(1))
            buf = []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        sections[cur] = "\n".join(buf)
    return sections


def _phi_sections(text: str) -> list[str]:
    return [s for s in _parse_sections(text).values() if "phi(" in s]


def _run_node(node, seed: dict | None) -> Interpreter:
    it = Interpreter()
    it.blocks[3000] = list(_ROM)
    for blk, vals in (seed or {}).items():
        it.blocks[blk] = list(vals)
    it.run(node)
    return it


def _min_it(build, mode, cb, seed) -> Interpreter:
    cfg = run_passes(build(), MINIMAL_PASSES, OptimizerConfig(mode=mode, callback=cb))
    return _run_node(cfg_to_engine_node(cfg), seed)


def _std_it(build, mode, cb, seed) -> Interpreter:
    cfg = ir.debug_run(build(), mode, cb, phases=_STD)
    return _run_node(cfg_to_engine_node(cfg), seed)


def _assert_semantics(build, mode=Mode.PLAY, cb=None, seed=None):
    """MINIMAL reference vs full standard-with-LICM path: same log + memory."""
    ref = _min_it(build, mode, cb, seed)
    std = _std_it(build, mode, cb, seed)
    assert ref.log == std.log, f"log mismatch: {ref.log} vs {std.log}"
    blocks = sorted((set(ref.blocks) | set(std.blocks)) - {10000, 3000})
    for blk in blocks:
        for i in range(32):
            a = ref.get(blk, i)
            b = std.get(blk, i)
            assert a == b, f"memory mismatch block {blk}[{i}]: {a} vs {b}"
    return ref, std


# ==========================================================================
# Structural: what moves, what stays.
# ==========================================================================


def _self_loop(body_expr, mode_write=None):
    """i=0; while i<10: acc += <body_expr>; i+=1; log acc.

    cfg_cleanup merges the header+body into one self-loop block (phis at top).
    ``mode_write`` (a place index) additionally stores into a writable block each
    iteration, to exercise read motion across writes to OTHER blocks.
    """
    b0 = BasicBlock(statements=[IRSet(_sc("i"), IRConst(0)), IRSet(_sc("acc"), IRConst(0))])
    stmts = [
        IRSet(_sc("acc"), IRPureInstr(Op.Add, [_rd("acc"), body_expr])),
        IRSet(_sc("i"), IRPureInstr(Op.Add, [_rd("i"), IRConst(1)])),
    ]
    if mode_write is not None:
        stmts.insert(0, IRSet(BlockPlace(WBLOCK, mode_write), _rd("i")))
    head = BasicBlock(statements=stmts, test=IRPureInstr(Op.Less, [_rd("i"), IRConst(10)]))
    ex = BasicBlock(statements=[_log(_rd("acc"))])
    b0.connect_to(head, None)
    head.connect_to(head, None)
    head.connect_to(ex, 0)
    return b0


def test_invariant_expensive_expr_hoisted_once():
    # RuntimeUpdate[0] * RuntimeUpdate[1] -- invariant, non-runtime-const,
    # effective cost 1+3+3 = 7 >= 4 -> hoisted into a preheader exactly once.
    expr = IRPureInstr(Op.Multiply, [_ru(0), _ru(1)])
    build = lambda: _self_loop(expr)  # noqa: E731
    before = _text(build, _SSA_PRE)
    after = _text(build, _SSA_LICM)
    assert "RuntimeUpdate[0]" in before
    assert "RuntimeUpdate[1]" in before
    # exactly one product survives (moved, not duplicated).
    assert after.count(" * ") == 1
    # the multiply and both reads left the phi-carrying loop block.
    for phi_sec in _phi_sections(after):
        assert " * " not in phi_sec
        assert "RuntimeUpdate[" not in phi_sec
    # ... and now live in a no-phi block (the preheader).
    assert " * " in after
    _assert_semantics(build, seed={RU.value: [3.0, 5.0]})


def test_cheap_expr_not_hoisted():
    # A single read has effective cost 1 (block push) + 1 (const index) + 1 = 3 < 4:
    # not worth a temp -> stays in the loop.
    build = lambda: _self_loop(_ru(0))  # noqa: E731
    after = _text(build, _SSA_LICM)
    phi_secs = _phi_sections(after)
    assert any("RuntimeUpdate[0]" in s for s in phi_secs), "cheap read must stay in the loop"
    _assert_semantics(build, seed={RU.value: [4.0]})


def test_runtime_constant_tree_not_hoisted():
    # LevelData is a RUNTIME_CONSTANT_BLOCK: LevelData[0]*LevelData[1] is a
    # runtime-constant subtree (effective cost 1), so LICM must NOT hoist it --
    # a temp would defeat the runtime's own constant folding.
    expr = IRPureInstr(Op.Multiply, [_rc(0), _rc(1)])
    build = lambda: _self_loop(expr)  # noqa: E731
    after = _text(build, _SSA_LICM)
    phi_secs = _phi_sections(after)
    assert any(" * " in s for s in phi_secs), "runtime-constant tree must stay in the loop"
    _assert_semantics(build, seed={ROM_CONST.value: [3.0, 5.0]})


def test_writable_read_never_hoisted():
    # A read of a writable (raw-int) block is never loop-invariant, even inside an
    # expensive tree -> never hoisted.
    expr = IRPureInstr(Op.Multiply, [_w(0), _w(1)])
    build = lambda: _self_loop(expr)  # noqa: E731
    after = _text(build, _SSA_LICM)
    phi_secs = _phi_sections(after)
    assert any(f"{WBLOCK}[0]" in s for s in phi_secs), "writable read must stay in the loop"
    _assert_semantics(build, seed={WBLOCK: [2.0, 7.0]})


def test_non_writable_read_hoisted_across_writes_to_other_blocks():
    # The loop writes block 20 (writable) each iteration; the invariant expensive
    # RuntimeUpdate tree (a DIFFERENT, non-writable block) still hoists past those
    # writes.
    expr = IRPureInstr(Op.Multiply, [_ru(0), _ru(1)])
    build = lambda: _self_loop(expr, mode_write=5)  # noqa: E731
    after = _text(build, _SSA_LICM)
    for phi_sec in _phi_sections(after):
        assert "RuntimeUpdate[" not in phi_sec, "non-writable read should hoist past writes to other blocks"
    assert "RuntimeUpdate[" in after
    _assert_semantics(build, seed={RU.value: [3.0, 5.0]})


def test_conditionally_executed_not_hoisted():
    # The invariant expensive tree is computed on only one arm of an if inside the
    # loop -> its def block does not dominate the latch -> NOT hoisted.
    def build():
        b0 = BasicBlock(statements=[IRSet(_sc("i"), IRConst(0)), IRSet(_sc("acc"), IRConst(0))])
        head = BasicBlock(test=IRPureInstr(Op.Less, [_rd("i"), IRConst(10)]))
        # branch on i % 2 (approx via i itself for a two-way)
        br = BasicBlock(test=_rd("i"))
        then = BasicBlock(
            statements=[
                IRSet(_sc("acc"), IRPureInstr(Op.Add, [_rd("acc"), IRPureInstr(Op.Multiply, [_ru(0), _ru(1)])]))
            ]
        )
        merge = BasicBlock(statements=[IRSet(_sc("i"), IRPureInstr(Op.Add, [_rd("i"), IRConst(1)]))])
        ex = BasicBlock(statements=[_log(_rd("acc"))])
        b0.connect_to(head, None)
        head.connect_to(br, None)
        head.connect_to(ex, 0)
        br.connect_to(merge, 0)  # else: skip
        br.connect_to(then, None)  # then: compute
        then.connect_to(merge, None)
        merge.connect_to(head, None)
        return b0

    # The guarded product's def block (the `then` arm) does not dominate the latch,
    # so LICM must NOT hoist it: adding the licm phase is a pure no-op (cf.
    # test_licm_only_runs_once_no_hoist_is_stable). A speculative hoist would move
    # the product into a preheader and change this text -- ` * ` in after alone
    # would not catch that (the product text survives either way).
    assert _text(build, _SSA_PRE) == _text(build, _SSA_LICM)
    _assert_semantics(build, seed={RU.value: [3.0, 5.0]})


def test_unguarded_invariant_divide_hoists():
    # Companion to test_conditionally_executed_faulting_op_not_speculated: an
    # UNGUARDED loop-invariant Divide (guaranteed to execute every iteration) IS
    # hoisted -- so it is the guard, not the op, that blocks speculation below.
    expr = IRPureInstr(Op.Divide, [_ru(0), _ru(1)])
    build = lambda: _self_loop(expr)  # noqa: E731
    after = _text(build, _SSA_LICM)
    # the divide left the phi-carrying loop block for a no-phi preheader.
    for phi_sec in _phi_sections(after):
        assert " / " not in phi_sec
    assert " / " in after
    _assert_semantics(build, seed={RU.value: [6.0, 3.0]})


def test_conditionally_executed_faulting_op_not_speculated():
    # A loop-invariant, cost-eligible division RU[0]/RU[2] guarded by `if RU[2]`
    # (the unguarded form DOES hoist -- see test_unguarded_invariant_divide_hoists),
    # with RU[2] seeded 0. The guaranteed-to-execute rule must keep it in the guarded
    # arm: speculating it into the preheader would divide by zero unconditionally and
    # the oracle would raise.
    def build():
        b0 = BasicBlock(statements=[IRSet(_sc("i"), IRConst(0)), IRSet(_sc("acc"), IRConst(0))])
        head = BasicBlock(test=IRPureInstr(Op.Less, [_rd("i"), IRConst(10)]))
        br = BasicBlock(test=_ru(2))  # guard: take the then-arm only when RU[2] != 0
        then = BasicBlock(
            statements=[IRSet(_sc("acc"), IRPureInstr(Op.Add, [_rd("acc"), IRPureInstr(Op.Divide, [_ru(0), _ru(2)])]))]
        )
        merge = BasicBlock(statements=[IRSet(_sc("i"), IRPureInstr(Op.Add, [_rd("i"), IRConst(1)]))])
        ex = BasicBlock(statements=[_log(_rd("acc"))])
        b0.connect_to(head, None)
        head.connect_to(br, None)
        head.connect_to(ex, 0)
        br.connect_to(merge, 0)  # RU[2] == 0: skip the division
        br.connect_to(then, None)  # RU[2] != 0: divide
        then.connect_to(merge, None)
        merge.connect_to(head, None)
        return b0

    # LICM must be a no-op (the division stays guarded, not speculated).
    assert _text(build, _SSA_PRE) == _text(build, _SSA_LICM)
    # RU[2] == 0 -> the guard is always false -> the oracle never divides. A wrong
    # speculative hoist would divide by zero in the preheader and raise here.
    _assert_semantics(build, seed={RU.value: [3.0, 5.0, 0.0]})


def test_nested_loops_hoist_past_both():
    # for i in 0..3: for j in 0..3: acc += RuntimeUpdate[0]*RuntimeUpdate[1]
    # The invariant expensive tree is invariant in BOTH loops -> LICM (inner-first,
    # to fixpoint) lifts it all the way to the outermost preheader.
    def build():
        entry = BasicBlock(statements=[IRSet(_sc("i"), IRConst(0)), IRSet(_sc("acc"), IRConst(0))])
        outer = BasicBlock(statements=[IRSet(_sc("j"), IRConst(0))], test=IRPureInstr(Op.Less, [_rd("i"), IRConst(3)]))
        inner = BasicBlock(
            statements=[
                IRSet(_sc("acc"), IRPureInstr(Op.Add, [_rd("acc"), IRPureInstr(Op.Multiply, [_ru(0), _ru(1)])])),
                IRSet(_sc("j"), IRPureInstr(Op.Add, [_rd("j"), IRConst(1)])),
            ],
            test=IRPureInstr(Op.Less, [_rd("j"), IRConst(3)]),
        )
        after_inner = BasicBlock(statements=[IRSet(_sc("i"), IRPureInstr(Op.Add, [_rd("i"), IRConst(1)]))])
        ex = BasicBlock(statements=[_log(_rd("acc"))])
        entry.connect_to(outer, None)
        outer.connect_to(inner, None)
        outer.connect_to(ex, 0)
        inner.connect_to(inner, None)
        inner.connect_to(after_inner, 0)
        after_inner.connect_to(outer, None)
        return entry

    after = _text(build, _SSA_LICM)
    # exactly one product survives, and it is in no phi-carrying (loop) block.
    assert after.count(" * ") == 1
    for phi_sec in _phi_sections(after):
        assert " * " not in phi_sec, "invariant tree must be hoisted out of both loops"
    _assert_semantics(build, seed={RU.value: [2.0, 3.0]})


def test_preheader_creation_with_phi_splitting():
    # A loop header reached by TWO distinct entry edges (a pre-loop diamond) plus a
    # back edge: LICM must build a preheader, merge the two entry operands of each
    # header phi into a NEW preheader phi, and hoist there. verify() (per-edge phi
    # arity + dominance) runs after the licm phase.
    def build():
        entry = BasicBlock(test=_w(9))  # nondeterministic-ish two-way selector
        pa = BasicBlock(statements=[IRSet(_sc("acc"), IRConst(10))])
        pb = BasicBlock(statements=[IRSet(_sc("acc"), IRConst(20))])
        head = BasicBlock(
            statements=[
                IRSet(_sc("acc"), IRPureInstr(Op.Add, [_rd("acc"), IRPureInstr(Op.Multiply, [_ru(0), _ru(1)])])),
                IRSet(_sc("i"), IRPureInstr(Op.Add, [_rd("i"), IRConst(1)])),
            ],
            test=IRPureInstr(Op.Less, [_rd("i"), IRConst(5)]),
        )
        ex = BasicBlock(statements=[_log(_rd("acc"))])
        entry.connect_to(pa, 0)
        entry.connect_to(pb, None)
        pa.connect_to(head, None)
        pb.connect_to(head, None)
        head.connect_to(head, None)
        head.connect_to(ex, 0)
        # i must be defined before the loop on both entry paths.
        pa.statements.insert(0, IRSet(_sc("i"), IRConst(0)))
        pb.statements.insert(0, IRSet(_sc("i"), IRConst(0)))
        return entry

    after = _text(build, _SSA_LICM)
    # Exactly one product survives, and it has moved into a *preheader* phi block:
    # one that merges the two entry `acc` operands (contains `phi(`) but is NOT the
    # loop header (which carries the `< 5` latch test). A failure to hoist would
    # leave the product in the `< 5` header block; a missing preheader would
    # falsify the `phi(` check.
    assert after.count(" * ") == 1
    sections = _parse_sections(after)
    star = next(s for s in sections.values() if " * " in s)
    assert "< 5" not in star, "product must not stay in the loop-header block"
    assert "phi(" in star, "product must move into the merged preheader phi block"
    _assert_semantics(build, mode=Mode.PLAY, cb=None, seed={RU.value: [3.0, 4.0], WBLOCK: [0.0] * 16})


def test_hoisted_value_semantics_deep():
    # A deeper invariant tree: (RU0*RU1) + (RU2 - RU3), cost >> 4, hoisted whole.
    def build():
        expr = IRPureInstr(
            Op.Add,
            [IRPureInstr(Op.Multiply, [_ru(0), _ru(1)]), IRPureInstr(Op.Subtract, [_ru(2), _ru(3)])],
        )
        return _self_loop(expr)

    after = _text(build, _SSA_LICM)
    for phi_sec in _phi_sections(after):
        assert "RuntimeUpdate[" not in phi_sec
    ref, _ = _assert_semantics(build, seed={RU.value: [10.0, 2.0, 7.0, 3.0]})
    assert ref.log == [10 * (10.0 * 2.0 + (7.0 - 3.0))]
    assert not math.isnan(ref.log[0])


def test_licm_only_runs_once_no_hoist_is_stable():
    # A loop with nothing to hoist: LICM is a no-op and the SSA text is unchanged
    # by adding the licm phase.
    build = lambda: _self_loop(_rd("acc"))  # noqa: E731  (uses the loop-carried value: not invariant)
    assert _text(build, _SSA_PRE) == _text(build, _SSA_LICM)
