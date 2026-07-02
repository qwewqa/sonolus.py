# cython: language_level=3
"""Optimizer driver: level dispatch and the from-Func pipeline.

Keeps the M0 toolchain-proof helpers (``compiled`` / ``nogil_sum``) that
``tests/backend/test_opt_toolchain.py`` exercises, and wires the milestone-M3
pipelines described in OPTIMIZER_REWRITE.md 5/7.0/11.

Levels (7.0), as of M3 (``standard`` is now the full pipeline: LICM +
rewrite_switch via ``midend_standard``, then if-conversion, then packing):

    minimal  (-O0): cfg_cleanup -> bump allocation  (mid-end bypassed)
    fast     (-O1): cfg_cleanup -> build_ssa -> midend_round(allow_repeat=False)
                    -> lower_from_ssa -> try-bump allocation
    standard (-O2): cfg_cleanup -> build_ssa -> midend_standard -> if_convert
                    -> lower_from_ssa -> interference (packing) allocation

``midend_round`` runs SCCP -> simplify/GVN -> DCE over the SSA arena (repeating
once when ``allow_repeat`` and something changed). ``midend_standard`` runs that
core plus LICM and rewrite_switch, repeating the core once on change (7.2.7).
``if_convert`` (standard only) folds diamonds/triangles/{VALUE C, NONE} blocks
into ``If`` expression selects over SSA while phis still exist (7.3), relying on
must-fold flags that ``lower_from_ssa`` honors. ``lower_from_ssa`` is the real
out-of-SSA + treeify + coalesce + phi-free cleanup + normalize_switch that
supersedes the naive ``out_of_ssa``, producing a 3-legal non-SSA arena ready for
``allocate_func`` + emission.

The thin Python shim ``sonolus/backend/optimize/__init__.py`` (``run_passes`` /
``optimize_and_finalize`` / ``cfg_to_engine_node``) calls the ``*_cfg`` entry
points below with a level *name*; ``compile_mode`` uses ``optimize_and_finalize``.
``allocate=False`` (visualize_cfg) stops after ``lower_from_ssa`` (or after
``cfg_cleanup`` at minimal), leaving temp places unallocated.

This module also populates the phase registry consulted by ``ir.debug_run`` (the
10 debug API), registering ``cfg_cleanup``, ``lower``, and the three allocators so
``debug_run(cfg, phases=["cfg_cleanup"])`` runs a single named phase.
"""

from sonolus.backend._opt.ir cimport Func
from sonolus.backend._opt.midend cimport build_ssa, cfg_cleanup, midend_round, midend_standard, out_of_ssa
from sonolus.backend._opt.analysis cimport compute_dominators
from sonolus.backend._opt.lower cimport (
    ALLOC_BUMP,
    ALLOC_PACKING,
    ALLOC_TRY_BUMP,
    allocate_func,
    if_convert,
    lower_from_ssa,
)
from sonolus.backend._opt.emit cimport emit_func

from sonolus.backend._opt.ir import marshal_in, register_phase, to_basic_blocks


# --------------------------------------------------------------------------
# M0 toolchain proof (kept -- used by test_opt_toolchain.py).
# --------------------------------------------------------------------------

def compiled() -> bool:
    """Return True from compiled code, proving the extension loaded."""
    return True


cdef long _triangular(long n) noexcept nogil:
    """Sum ``0 .. n-1`` in a GIL-free region."""
    cdef long total = 0
    cdef long i
    for i in range(n):
        total += i
    return total


def nogil_sum(long n) -> int:
    """Compute ``sum(range(n))`` inside a ``with nogil:`` block."""
    cdef long result
    with nogil:
        result = _triangular(n)
    return result


# --------------------------------------------------------------------------
# Level dispatch.
# --------------------------------------------------------------------------

cdef enum:
    LEVEL_MINIMAL = 0
    LEVEL_FAST = 1
    LEVEL_STANDARD = 2


cdef int _level_code(object level) except -1:
    if level == "minimal":
        return LEVEL_MINIMAL
    if level == "fast":
        return LEVEL_FAST
    if level == "standard":
        return LEVEL_STANDARD
    raise ValueError(
        f"Unknown optimization level {level!r} (expected 'minimal', 'fast', or 'standard')"
    )


cdef Func _pipeline(Func func, int level, bint allocate):
    """Run the level-``level`` pipeline over ``func``, returning a fresh arena.

    ``minimal`` bypasses the mid-end (cfg_cleanup -> bump). ``fast`` and
    ``standard`` share the cfg_cleanup -> build_ssa front, then diverge (7.0):

        fast     (-O1): midend_round(allow_repeat=False) -> lower_from_ssa
                        -> try-bump allocation. No LICM / rewrite_switch /
                        if-conversion (dev default; iteration speed first).
        standard (-O2): midend_standard (core -> LICM -> rewrite_switch, core
                        repeated once on change) -> if_convert (SSA in / SSA out,
                        while phis exist) -> lower_from_ssa -> packing allocation.

    Each stage returns a fresh ``Func``; the allocator (when requested) rewrites
    the final arena in place.

    ``allocate=False`` returns the pre-allocation form: post-cfg_cleanup for
    minimal, post-lower_from_ssa (temps unallocated) for fast/standard.
    """
    cdef Func cleaned
    cdef Func ssa
    cdef Func opt
    cdef Func conv
    cdef Func lowered

    if level == LEVEL_MINIMAL:
        cleaned = cfg_cleanup(func, False)
        if allocate:
            allocate_func(cleaned, ALLOC_BUMP)
        return cleaned

    cleaned = cfg_cleanup(func, False)
    ssa = build_ssa(cleaned)

    if level == LEVEL_FAST:
        # fast (-O1): one mid-end round, cheap allocation, no LICM/switch/if-conv.
        opt = midend_round(ssa, False)
        lowered = lower_from_ssa(opt)
        if allocate:
            allocate_func(lowered, ALLOC_TRY_BUMP)
        return lowered

    # standard (-O2): full mid-end (LICM + rewrite_switch) then if-conversion
    # over SSA (while phis still exist), then lowering + packing allocation.
    opt = midend_standard(ssa)
    conv = if_convert(opt)
    lowered = lower_from_ssa(conv)
    if allocate:
        allocate_func(lowered, ALLOC_PACKING)
    return lowered


# --------------------------------------------------------------------------
# Python-callable entry points (used by the optimize/__init__ shim).
# --------------------------------------------------------------------------

def run_pipeline_cfg(entry, level, mode=None, callback=None, allocate=True):
    """marshal_in -> level pipeline (7.0) -> (allocate) -> to_basic_blocks.

    ``allocate=False`` stops before allocation (for ``visualize_cfg``), leaving
    temp places unallocated (post-lower_from_ssa for fast/standard, post-cleanup
    for minimal).
    """
    cdef int lvl = _level_code(level)
    cdef Func func = <Func>marshal_in(entry, mode, callback)
    cdef Func result = _pipeline(func, lvl, allocate)
    return to_basic_blocks(result)


def optimize_and_finalize_cfg(entry, level, mode=None, callback=None):
    """marshal_in -> level pipeline (7.0) -> allocate -> emit (fused; no export)."""
    cdef int lvl = _level_code(level)
    cdef Func func = <Func>marshal_in(entry, mode, callback)
    cdef Func result = _pipeline(func, lvl, True)
    return emit_func(result)


# --------------------------------------------------------------------------
# Debug phase registry (consulted by ir.debug_run).
# --------------------------------------------------------------------------

def _phase_cfg_cleanup(func):
    return cfg_cleanup(<Func>func, False)


def _phase_ssa(func):
    return build_ssa(<Func>func)


def _phase_unssa(func):
    return out_of_ssa(<Func>func)


def _phase_ifconv(func):
    return if_convert(<Func>func)


def _phase_lower(func):
    return lower_from_ssa(<Func>func)


def _phase_dominators(func):
    compute_dominators(<Func>func)
    return func


def _phase_bump(func):
    allocate_func(<Func>func, ALLOC_BUMP)
    return func


def _phase_packing(func):
    allocate_func(<Func>func, ALLOC_PACKING)
    return func


def _phase_try_bump(func):
    allocate_func(<Func>func, ALLOC_TRY_BUMP)
    return func


register_phase("cfg_cleanup", _phase_cfg_cleanup)
register_phase("ssa", _phase_ssa)
register_phase("unssa", _phase_unssa)
register_phase("ifconv", _phase_ifconv)
register_phase("lower", _phase_lower)
register_phase("dominators", _phase_dominators)
register_phase("bump", _phase_bump)
register_phase("packing", _phase_packing)
register_phase("try_bump", _phase_try_bump)
