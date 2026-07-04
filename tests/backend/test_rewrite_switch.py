"""rewrite_switch tests.

Covers the two rewrite steps:

* ``ifs_to_switch`` -- a two-way block ``{VALUE 0 -> false, NONE -> true}`` whose
  test is ``Equal(x, C)`` (C an OPX_CONST) becomes ``test = x`` with the true edge
  carrying ``cond = C`` and the false edge becoming the NONE default;
* ``combine_blocks`` -- while the default target is an empty single-pred block with
  the SAME test, splice its cases up (dropping duplicate conds) and let it die.

Also covers the standard-mid-end integration (this pass + LICM run inside
``midend_standard``), the downstream ``normalize_switch`` interaction that turns an
arithmetic-progression switch into ``SwitchIntegerWithDefault``, the full pydori
corpus, and the random-CFG differential property vs the MINIMAL reference.

Layers mirror test_licm.py: structural SSA-text checks, semantic interpretation,
and corpus/random coverage of the shared ``midend_standard`` path.
"""

from __future__ import annotations

import re

import pytest
from hypothesis import HealthCheck, given, settings

from sonolus.backend._opt import ir  # noqa: PLC2701
from sonolus.backend.blocks import PlayBlock
from sonolus.backend.interpret import Interpreter
from sonolus.backend.ir import IRConst, IRGet, IRInstr, IRPureInstr, IRSet
from sonolus.backend.mode import Mode
from sonolus.backend.node import FunctionNode
from sonolus.backend.ops import Op
from sonolus.backend.optimize import MINIMAL_PASSES, OptimizerConfig, cfg_to_engine_node, run_passes
from sonolus.backend.optimize.flow import BasicBlock, cfg_to_text
from sonolus.backend.place import BlockPlace, TempBlock
from tests.backend._cfg_gen import OBS_BLOCKS, OBS_CAPTURE_LEN, build_cfg, programs
from tests.backend._corpus import MODE_SETUP, iter_callbacks
from tests.backend.test_random_cfg import _ROM, _observe

SEL = PlayBlock.RuntimeUpdate  # read-only selector source (callback=None)

_SSA_PRE = ["cfg_cleanup", "ssa", "gvn", "dce"]
_SSA_RSW = ["cfg_cleanup", "ssa", "gvn", "dce", "rewrite_switch"]
_STD = ["cfg_cleanup", "ssa", "midend_standard", "lower", "packing"]


def _sc(name: str) -> BlockPlace:
    return BlockPlace(TempBlock(name, 1), 0, 0)


def _rd(name: str) -> IRGet:
    return IRGet(_sc(name))


def _sel(i: int = 0) -> IRGet:
    return IRGet(BlockPlace(SEL, i))


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


def _switch_case_lines(section: str) -> list[str]:
    return [ln.strip() for ln in section.splitlines() if "->" in ln]


def _run_node(node, seed):
    it = Interpreter()
    it.blocks[3000] = list(_ROM)
    for blk, vals in (seed or {}).items():
        it.blocks[blk] = list(vals)
    it.run(node)
    return it


def _assert_semantics(build, mode=Mode.PLAY, cb=None, seed=None):
    ref = _run_node(
        cfg_to_engine_node(run_passes(build(), MINIMAL_PASSES, OptimizerConfig(mode=mode, callback=cb))), seed
    )
    std = _run_node(cfg_to_engine_node(ir.debug_run(build(), mode, cb, phases=_STD)), seed)
    assert ref.log == std.log, f"log mismatch: {ref.log} vs {std.log}"
    blocks = sorted((set(ref.blocks) | set(std.blocks)) - {10000, 3000})
    for blk in blocks:
        for i in range(32):
            a, b = ref.get(blk, i), std.get(blk, i)
            assert a == b, f"memory mismatch {blk}[{i}]: {a} vs {b}"
    return ref, std


def _find_node(root, func):
    seen = set()
    stack = [root]
    while stack:
        n = stack.pop()
        if not isinstance(n, FunctionNode) or id(n) in seen:
            continue
        seen.add(id(n))
        if n.func is func:
            return n
        stack.extend(n.args)
    return None


# ==========================================================================
# ifs_to_switch + combine_blocks structure.
# ==========================================================================


def _eq_chain(consts, tails=None, mid_stmt=None):
    """Build ``if x==c0 -> A0 elif x==c1 -> A1 ... else D``; each arm logs 100+i (or ``tails``).

    ``mid_stmt`` (index) inserts a statement into that chain block, making it
    non-empty so splicing stops there.
    """
    x = _sel(0)
    end = BasicBlock()
    heads = []
    arms = []
    for i, c in enumerate(consts):
        h = BasicBlock(test=IRPureInstr(Op.Equal, [x, IRConst(c)]))
        if mid_stmt == i:
            h.statements = [_log(-i - 1)]
        a = BasicBlock(statements=[_log(100 + i)])
        a.connect_to(end, None)
        heads.append(h)
        arms.append(a)
    dflt = BasicBlock(statements=[_log(199)])
    dflt.connect_to(end, None)
    for i, h in enumerate(heads):
        h.connect_to(arms[i], None)  # true -> arm
        h.connect_to(heads[i + 1] if i + 1 < len(heads) else dflt, 0)  # false -> next / default
    return heads[0]


def test_equal_chain_of_three_becomes_one_multiway():
    build = lambda: _eq_chain([1, 2, 3])  # noqa: E731
    before = _text(build, _SSA_PRE)
    assert before.count("goto when") == 0  # all two-way ifs before
    after = _text(build, _SSA_RSW)
    secs = _parse_sections(after)
    switches = [s for s in secs.values() if "goto when" in s]
    assert len(switches) == 1, "the whole chain collapses into ONE multiway block"
    cases = _switch_case_lines(switches[0])
    assert sorted(c.split(" -> ")[0] for c in cases) == ["1", "2", "3", "default"]
    _assert_semantics(build, seed={SEL.value: [2.0]})
    _assert_semantics(build, seed={SEL.value: [7.0]})  # default arm


def test_single_equal_two_way_rewrites_edge_conds():
    # Just ifs_to_switch on one block: Equal(x, 5) -> test x, true edge cond 5,
    # false edge becomes default (still a two-way, printed as an if by cfg_to_text
    # since it is {5, default}). Semantics preserved.
    build = lambda: _eq_chain([5])  # noqa: E731
    after = _text(build, _SSA_RSW)
    # the Equal is gone from the terminator (test is the bare selector value now).
    head = _parse_sections(after)[0]
    assert "== 5" not in head.split("goto")[-1]
    _assert_semantics(build, seed={SEL.value: [5.0]})
    _assert_semantics(build, seed={SEL.value: [9.0]})


def test_non_empty_chain_block_stops_splicing():
    # The middle chain block carries a statement -> not empty -> splicing halts:
    # the head becomes a partial switch whose default still leads to a two-way.
    build = lambda: _eq_chain([1, 2, 3], mid_stmt=1)  # noqa: E731
    after = _text(build, _SSA_RSW)
    secs = _parse_sections(after)
    switches = [s for s in secs.values() if "goto when" in s]
    # first block splices only c==1 (block 2 is non-empty), so at least one switch
    # remains a proper multiway but not the full 3-case one.
    assert switches, "the first Equal still becomes a (partial) switch"
    assert all(len(_switch_case_lines(s)) < 4 for s in switches), "splicing stopped at the non-empty block"
    _assert_semantics(build, seed={SEL.value: [1.0]})
    _assert_semantics(build, seed={SEL.value: [2.0]})
    _assert_semantics(build, seed={SEL.value: [3.0]})
    _assert_semantics(build, seed={SEL.value: [8.0]})


def test_duplicate_cond_edge_dropped():
    # x==1 -> A elif x==1 -> B else C: the second (duplicate) case is unreachable
    # and dropped; only one case-1 edge survives.
    build = lambda: _eq_chain([1, 1])  # noqa: E731
    after = _text(build, _SSA_RSW)
    # exactly one edge with cond 1 across the head block.
    head = _parse_sections(after)[0]
    ones = [c for c in _switch_case_lines(head) if c.startswith("1 ->")]
    assert len(ones) <= 1
    # B (log 101) is unreachable -> gone.
    assert "DebugLog(101)" not in after
    _assert_semantics(build, seed={SEL.value: [1.0]})
    _assert_semantics(build, seed={SEL.value: [5.0]})


def test_phis_at_targets_preserved():
    # Each arm assigns a distinct value to r; the join reads r via a phi. After
    # rewrite_switch the per-edge phi operands must still line up (verify() runs
    # after the phase, and interpretation confirms the value).
    def build():
        x = _sel(0)
        h1 = BasicBlock(test=IRPureInstr(Op.Equal, [x, IRConst(1)]))
        h2 = BasicBlock(test=IRPureInstr(Op.Equal, [x, IRConst(2)]))
        a1 = BasicBlock(statements=[IRSet(_sc("r"), IRConst(11))])
        a2 = BasicBlock(statements=[IRSet(_sc("r"), IRConst(22))])
        d = BasicBlock(statements=[IRSet(_sc("r"), IRConst(99))])
        # two statements so cfg_cleanup does not tail-duplicate the join into the
        # arms (which would eliminate the phi we are checking survives).
        join = BasicBlock(statements=[_log(_rd("r")), IRInstr(Op.DebugPause, [_rd("r")])])
        h1.connect_to(a1, None)
        h1.connect_to(h2, 0)
        h2.connect_to(a2, None)
        h2.connect_to(d, 0)
        for arm in (a1, a2, d):
            arm.connect_to(join, None)
        return h1

    after = _text(build, _SSA_RSW)
    assert "goto when" in after
    assert "phi(" in after  # the join phi survives
    std = None
    for c in (1.0, 2.0, 3.0):
        _, std = _assert_semantics(build, seed={SEL.value: [c]})
    assert std.log == [99.0]  # last run: default arm


def test_combine_blocks_refuses_splice_with_escaping_const_phi():
    # combine_blocks would splice the same-test empty default block ``nxt`` up into
    # the head, but ``nxt`` shares successor A with the head and A's phi carries a
    # DISTINCT operand on the nxt->A edge (r == 5, defined in nxt) vs the head->A
    # edge (r == 7). Splicing would give the head two parallel edges to A with
    # unequal phi operands (an SSA-invariant violation _rsw_splice_safe forbids), so
    # the default block must stay un-spliced: BOTH switch blocks survive. Neutralizing
    # the guard (forcing splice) makes verify() fail (unequal parallel operands).
    def build():
        x = _sel(0)
        b = BasicBlock(statements=[IRSet(_sc("r"), IRConst(7))], test=IRPureInstr(Op.Equal, [x, IRConst(1)]))
        nxt = BasicBlock(statements=[IRSet(_sc("r"), IRConst(5))], test=IRPureInstr(Op.Equal, [x, IRConst(2)]))
        a = BasicBlock(statements=[_log(_rd("r")), IRInstr(Op.DebugPause, [_rd("r")])])
        d = BasicBlock(statements=[_log(199)])
        end = BasicBlock()
        b.connect_to(a, None)  # x == 1 -> A
        b.connect_to(nxt, 0)  # else -> nxt (default)
        nxt.connect_to(a, None)  # x == 2 -> A (shared successor)
        nxt.connect_to(d, 0)  # else -> D
        a.connect_to(end, None)
        d.connect_to(end, None)
        return b

    after = _text(build, _SSA_RSW)  # verify() runs inside debug_run after the phase
    assert after.count("goto when") == 2, "the escaping-const default block must NOT be spliced"
    assert "phi(" in after  # the shared-successor phi with distinct per-edge operands survives
    _assert_semantics(build, seed={SEL.value: [1.0]})
    _assert_semantics(build, seed={SEL.value: [2.0]})
    _assert_semantics(build, seed={SEL.value: [3.0]})


def test_normalize_switch_downstream_emits_switch_integer_with_default():
    # An arithmetic-progression Equal chain (x==10, 20, 30) collapses to a multiway
    # with cases {10, 20, 30}; downstream normalize_switch (in lower_from_ssa)
    # rewrites those to 0..2 with test (x-10)/10, so emission produces a
    # SwitchIntegerWithDefault node (the runtime's fast dispatch form).
    build = lambda: _eq_chain([10, 20, 30])  # noqa: E731
    cfg = ir.debug_run(build(), Mode.PLAY, None, phases=_STD)
    node = cfg_to_engine_node(cfg)
    assert _find_node(node, Op.SwitchIntegerWithDefault) is not None, "expected contiguous integer switch"
    assert _find_node(node, Op.SwitchWithDefault) is None
    for c in (10.0, 20.0, 30.0, 5.0):
        _assert_semantics(build, seed={SEL.value: [c]})


def test_rewrite_switch_noop_when_no_equal_chain():
    # A plain two-way (test is not Equal-of-const) is untouched.
    def build():
        b = BasicBlock(test=_sel(0))
        t = BasicBlock(statements=[_log(1)])
        f = BasicBlock(statements=[_log(0)])
        b.connect_to(f, 0)
        b.connect_to(t, None)
        t.connect_to(BasicBlock(), None)
        f.connect_to(BasicBlock(), None)
        return b

    assert _text(build, _SSA_PRE) == _text(build, _SSA_RSW)


# ==========================================================================
# Corpus: the whole pydori callback set through the standard-with-LICM+
# rewrite_switch mid-end, then lower_from_ssa + allocate + emit.
# ==========================================================================


def _effective_count(node, mode, cb) -> int:
    from tools.metrics import analyze_node

    return analyze_node(node, mode, cb)["effective_node_count"]


@pytest.mark.parametrize("mode", list(MODE_SETUP))
def test_corpus_standard_midend_pipeline(mode):
    total = changed = 0
    eff_fast_sum = eff_std_sum = 0
    regressions = []
    for label, cb, factory in iter_callbacks(mode):
        # fast baseline vs standard (+ LICM + rewrite_switch). verify()
        # runs after every phase inside debug_run, so a green run == valid SSA/CFG
        # at each stage for both paths.
        fast_cfg = ir.debug_run(factory(), mode, cb, phases=["cfg_cleanup", "ssa", "midend", "lower", "packing"])
        std_cfg = ir.debug_run(factory(), mode, cb, phases=_STD)
        node_fast = cfg_to_engine_node(fast_cfg)
        node_std = cfg_to_engine_node(std_cfg)  # emit must succeed
        total += 1
        if cfg_to_text(fast_cfg) != cfg_to_text(std_cfg):
            changed += 1
        ef = _effective_count(node_fast, mode, cb)
        es = _effective_count(node_std, mode, cb)
        eff_fast_sum += ef
        eff_std_sum += es
        if es > ef:
            regressions.append((label, ef, es))
    assert total > 0
    print(
        f"\n[rewrite_switch+LICM corpus/{mode.name}] callbacks={total} changed={changed} "
        f"eff_fast={eff_fast_sum} eff_std={eff_std_sum} delta={eff_std_sum - eff_fast_sum} "
        f"regressed={len(regressions)}"
    )
    # rewrite_switch removes branch blocks (fewer nodes); LICM materialises a few
    # invariant temps. On the switch/loop-heavy corpus the net effective count must
    # not regress overall.
    assert eff_std_sum <= eff_fast_sum, f"{mode.name}: effective node count regressed: {regressions[:5]}"


# ==========================================================================
# Random-CFG differential: standard-with-LICM+rewrite_switch vs MINIMAL.
# ==========================================================================


def _std_observe(build, config, blocks=OBS_BLOCKS, length=OBS_CAPTURE_LEN):
    cfg = ir.debug_run(build(), config.mode, config.callback, phases=_STD)
    node = cfg_to_engine_node(cfg)
    it = Interpreter()
    it.blocks[3000] = list(_ROM)
    ret = it.run(node)
    return _observe(it, ret, blocks, length)


def _min_observe(build, config, blocks=OBS_BLOCKS, length=OBS_CAPTURE_LEN):
    node = cfg_to_engine_node(run_passes(build(), MINIMAL_PASSES, config))
    it = Interpreter()
    it.blocks[3000] = list(_ROM)
    ret = it.run(node)
    return _observe(it, ret, blocks, length)


@settings(max_examples=250, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(program=programs())
def test_random_cfg_standard_matches_minimal(program):
    build = lambda: build_cfg(program)  # noqa: E731
    config = OptimizerConfig()
    ref = _min_observe(build, config)
    got = _std_observe(build, config)
    assert got == ref, "standard-with-LICM+rewrite_switch diverged from the MINIMAL reference"


@settings(max_examples=120, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(program=programs(max_depth=4))
def test_random_cfg_standard_matches_minimal_deep(program):
    build = lambda: build_cfg(program)  # noqa: E731
    config = OptimizerConfig()
    assert _std_observe(build, config) == _min_observe(build, config)
