"""Regression for finding C1: out-of-SSA must not place phi copies before a live test.

Pre-fix, both out-of-SSA paths (production ``_Lower`` and debug ``_UnSSA``) emitted
a single-distinct-successor block's phi copies at the end of the block, before its
live test, so a test reading (or resolving to) a successor phi observed the *next*
iteration's value and the loop exited one iteration early.

Repro: the latch ``b`` copies the loop-carried phi ``p`` into ``q`` before ``p`` is
redefined, then tests ``q`` with a default-less ``{0: h, 1: h}`` multiway (single
distinct successor, live test; miss -> exit). ``h`` always takes its default edge
(A[0] reads -1.0)::

    E : p <- 0                          -> h
    h : log(p); log(999)   test A[0]    {0: m1, 1: x, None: m2}
    m1: q <- p; p <- 5; log(101)        -> b
    m2: q <- p; p <- 5; log(102)        -> b
    b : log(7); log(8)     test q       {0: h, 1: h}   (miss -> exit)
    x : (exit)

Correct log at every level: ``[0, 999, 102, 7, 8, 5, 999, 102, 7, 8]``; pre-fix
FAST/STANDARD and ``unssa`` exited after one iteration. The exit block ``x`` keeps
the CFG terminating (a closed cycle is rejected as an infinite loop per the C2
decision).
"""

from __future__ import annotations

from sonolus.backend.ir import IRConst, IRGet, IRPureInstr, IRSet
from sonolus.backend.ops import Op
from sonolus.backend.optimize import (
    FAST_PASSES,
    MINIMAL_PASSES,
    STANDARD_PASSES,
    OptimizerConfig,
)
from sonolus.backend.optimize.flow import BasicBlock
from sonolus.backend.place import BlockPlace
from tests.backend.test_random_cfg import A, _assert_levels_agree, _interpret, _log, _rd, _sc
from tests.backend.test_ssa import _assert_semantics_preserved

# The header always takes its default edge because A[0] is never written (reads
# -1.0 at runtime), yet the optimizer cannot prove it, so the loop survives.
EXPECTED_LOG = [0.0, 999.0, 102.0, 7.0, 8.0, 5.0, 999.0, 102.0, 7.0, 8.0]


def _build_repro() -> BasicBlock:
    """The C1 repro CFG; regenerated fresh per call (the pipeline is non-destructive)."""
    e = BasicBlock(statements=[IRSet(_sc("p"), IRConst(0))])
    h = BasicBlock(statements=[_log(_rd("p")), _log(999)], test=IRGet(BlockPlace(A, 0)))
    m1 = BasicBlock(statements=[IRSet(_sc("q"), _rd("p")), IRSet(_sc("p"), IRConst(5)), _log(101)])
    m2 = BasicBlock(statements=[IRSet(_sc("q"), _rd("p")), IRSet(_sc("p"), IRConst(5)), _log(102)])
    b = BasicBlock(statements=[_log(7), _log(8)], test=_rd("q"))
    x = BasicBlock()  # a real exit block elsewhere: keeps the CFG terminating (not closed-cycle)
    e.connect_to(h, None)
    h.connect_to(m1, 0)
    h.connect_to(x, 1)
    h.connect_to(m2, None)
    m1.connect_to(b, None)
    m2.connect_to(b, None)
    b.connect_to(h, 0)  # default-less multiway: both case edges target h, a phi block
    b.connect_to(h, 1)
    return e


def test_production_pipeline_no_copies_before_test():
    # Every level must agree with the MINIMAL reference (which bypasses SSA), and
    # the shared result must be the correct two-iteration log. Before the C1 fix,
    # FAST/STANDARD lowered the b->h phi copy before b's test and exited early.
    ref_it, _ = _assert_levels_agree(_build_repro)
    assert ref_it.log == EXPECTED_LOG

    # Assert the log explicitly at each level as well, independent of the helper's
    # cross-level comparison.
    config = OptimizerConfig()
    for level in (MINIMAL_PASSES, FAST_PASSES, STANDARD_PASSES):
        it, _ = _interpret(_build_repro, level, config)
        assert it.log == EXPECTED_LOG, f"level {level!r} miscompiled the latch test"


def test_debug_unssa_no_copies_before_test():
    # The debug/inspection unssa path (_UnSSA) has the same defect and the same fix.
    # ssa->unssa must preserve the original (non-SSA) semantics.
    orig, rt = _assert_semantics_preserved(_build_repro)
    assert orig.log == EXPECTED_LOG
    assert rt.log == EXPECTED_LOG


# ==========================================================================
# Directed variants: additional shapes the same C1 condition change fixes.
# Each has a real exit block (so the C2 liveness guard passes), is checked
# tri-level against a hand-computed log, and re-checked through ``unssa``.
# ==========================================================================


def _add(a, b):
    return IRPureInstr(Op.Add, [a, b])


def _assert_variant(build, expected):
    """Assert a variant's hand-computed log at every level and through ssa->unssa."""
    ref_it, _ = _assert_levels_agree(build)
    assert ref_it.log == expected
    config = OptimizerConfig()
    for level in (MINIMAL_PASSES, FAST_PASSES, STANDARD_PASSES):
        it, _ = _interpret(build, level, config)
        assert it.log == expected, f"level {level!r} miscompiled the latch test"

    orig, rt = _assert_semantics_preserved(build)
    assert orig.log == expected
    assert rt.log == expected


def _build_self_loop_tests_own_phi() -> BasicBlock:
    """(a) A tested self-loop whose test resolves to the block's own loop-carried phi.

    ``b`` is its own single distinct successor yet carries a live default-less test;
    pre-fix ``_Lower`` emitted the back-edge ``p`` phi copy before ``b``'s ``GET`` of
    ``q``. (Pre-fix ``_UnSSA`` already split self-edges, so the debug-path assertions
    are parity coverage, not a regression guard.) ``e`` branches to a real exit ``y``
    (never taken; A[0] reads -1.0) to keep the CFG terminating for the C2 guard::

        e : p <- 0                        test A[0]   {1: y, None: b}
        b : log(p); q <- p; p <- 5        test q      {0: b, 1: b}   (miss -> exit)
        y : log(11)   (real exit, never reached at runtime)

    Runtime: b(p=0) tests q=0 -> case 0 -> b(p=5) tests q=5 -> miss -> exit. Log
    ``[0, 5]``.
    """
    e = BasicBlock(statements=[IRSet(_sc("p"), IRConst(0))], test=IRGet(BlockPlace(A, 0)))
    y = BasicBlock(statements=[_log(11)])
    b = BasicBlock(
        statements=[_log(_rd("p")), IRSet(_sc("q"), _rd("p")), IRSet(_sc("p"), IRConst(5))],
        test=_rd("q"),
    )
    e.connect_to(y, 1)
    e.connect_to(b, None)
    b.connect_to(b, 0)  # self-loop into its own phi block
    b.connect_to(b, 1)  # miss (q == 5) -> exit
    return e


EXPECTED_SELF_LOOP = [0.0, 5.0]


def _build_phi_unrelated_latch() -> BasicBlock:
    """(b) A tested single-distinct-successor latch with a memory-derived, phi-unrelated test.

    The latch ``b`` tests ``u`` (A[1] + 1 == 0 at runtime), NOT a successor phi, so
    the split must still deliver the loop's back-edge copies (``p``, ``i``). Both of
    ``b``'s parallel edges target the phi header ``h`` with non-contiguous conds
    ``{0, 5}``. ``h``'s second statement (``log(999)``) keeps tail-duplication from
    dissolving the tested-latch conjunction before out-of-SSA; ``h`` dispatches on
    the counter ``i`` and falls to exit ``x`` once ``i`` leaves ``{0, 1}``::

        e : p <- 0; i <- 0
        h : log(p); log(999)                test i   {0: m1, 1: m2, None: x}
        m1: p <- p + 10; log(101)           -> b
        m2: p <- p + 20; log(102)           -> b
        x : log(9)   (exit)
        b : u <- A[1] + 1; i <- i + 1; log(7)   test u   {0: h, 5: h}   (miss -> exit)

    Runtime (A[1] reads -1.0, so u == 0 every iteration -> case 0 back to h):
    iter 1 (p=0,i=0) h logs 0, 999 -> m1 log 101 -> b log 7; iter 2 (p=10,i=1)
    h logs 10, 999 -> m2 log 102 -> b log 7; iter 3 (p=30,i=2) h logs 30, 999
    -> default -> x log 9. Log ``[0, 999, 101, 7, 10, 999, 102, 7, 30, 999, 9]``.
    """
    e = BasicBlock(statements=[IRSet(_sc("p"), IRConst(0)), IRSet(_sc("i"), IRConst(0))])
    # The second statement keeps h ineligible for tail-duplication, preserving the
    # tested-latch-into-phi-header conjunction this variant exists to exercise.
    h = BasicBlock(statements=[_log(_rd("p")), _log(999)], test=_rd("i"))
    m1 = BasicBlock(statements=[IRSet(_sc("p"), _add(_rd("p"), IRConst(10))), _log(101)])
    m2 = BasicBlock(statements=[IRSet(_sc("p"), _add(_rd("p"), IRConst(20))), _log(102)])
    x = BasicBlock(statements=[_log(9)])
    b = BasicBlock(
        statements=[
            IRSet(_sc("u"), _add(IRGet(BlockPlace(A, 1)), IRConst(1))),  # -1 + 1 == 0 at runtime
            IRSet(_sc("i"), _add(_rd("i"), IRConst(1))),
            _log(7),
        ],
        test=_rd("u"),
    )
    e.connect_to(h, None)
    h.connect_to(m1, 0)
    h.connect_to(m2, 1)
    h.connect_to(x, None)
    m1.connect_to(b, None)
    m2.connect_to(b, None)
    b.connect_to(h, 0)
    b.connect_to(h, 5)  # non-contiguous conds; miss (u not in {0, 5}) -> exit
    return e


EXPECTED_PHI_UNRELATED = [0.0, 999.0, 101.0, 7.0, 10.0, 999.0, 102.0, 7.0, 30.0, 999.0, 9.0]


def _build_two_hop_copy_chain() -> BasicBlock:
    """(c) The base repro, but the latch test reaches the successor phi via a two-hop copy chain.

    ``b`` tests ``r``, with ``r <- q`` and ``q <- p`` (the header phi). Identical to
    ``_build_repro`` except ``b`` interposes ``r <- q`` before its test, so the
    copies-before-test defect must be caught across a copy chain, not only on a
    direct phi read::

        e : p <- 0                          -> h
        h : log(p); log(999)   test A[0]    {0: m1, 1: x, None: m2}
        m1: q <- p; p <- 5; log(101)        -> b
        m2: q <- p; p <- 5; log(102)        -> b
        b : log(7); r <- q; log(8)   test r   {0: h, 1: h}   (miss -> exit)
        x : (exit)

    Runtime (A[0] == -1.0 -> default -> m2 each time): iter 1 (p=0) tests r=0 ->
    back to h; iter 2 (p=5) tests r=5 -> miss -> exit.
    Log ``[0, 999, 102, 7, 8, 5, 999, 102, 7, 8]``.
    """
    e = BasicBlock(statements=[IRSet(_sc("p"), IRConst(0))])
    h = BasicBlock(statements=[_log(_rd("p")), _log(999)], test=IRGet(BlockPlace(A, 0)))
    m1 = BasicBlock(statements=[IRSet(_sc("q"), _rd("p")), IRSet(_sc("p"), IRConst(5)), _log(101)])
    m2 = BasicBlock(statements=[IRSet(_sc("q"), _rd("p")), IRSet(_sc("p"), IRConst(5)), _log(102)])
    b = BasicBlock(statements=[_log(7), IRSet(_sc("r"), _rd("q")), _log(8)], test=_rd("r"))
    x = BasicBlock()
    e.connect_to(h, None)
    h.connect_to(m1, 0)
    h.connect_to(x, 1)
    h.connect_to(m2, None)
    m1.connect_to(b, None)
    m2.connect_to(b, None)
    b.connect_to(h, 0)
    b.connect_to(h, 1)
    return e


EXPECTED_TWO_HOP = [0.0, 999.0, 102.0, 7.0, 8.0, 5.0, 999.0, 102.0, 7.0, 8.0]


def test_self_loop_tests_own_phi():
    # (a) A default-less self-loop (d == b arm) with a live test that resolves to
    # its own loop-carried phi: the back-edge copy must land after the test.
    _assert_variant(_build_self_loop_tests_own_phi, EXPECTED_SELF_LOOP)


def test_phi_unrelated_memory_test_latch():
    # (b) A tested single-distinct-successor latch whose test is memory-derived and
    # phi-unrelated, with non-contiguous conds {0, 5}: the split must still deliver
    # the back-edge copies even though the test reads no phi.
    _assert_variant(_build_phi_unrelated_latch, EXPECTED_PHI_UNRELATED)


def test_test_reaches_phi_through_two_hop_copy_chain():
    # (c) The repro shape with the test value reaching the successor phi through a
    # two-hop copy chain (r <- q <- p).
    _assert_variant(_build_two_hop_copy_chain, EXPECTED_TWO_HOP)
