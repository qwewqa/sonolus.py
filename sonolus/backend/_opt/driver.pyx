# cython: language_level=3
"""M1 optimizer driver: level dispatch and the from-Func pipeline.

Keeps the M0 toolchain-proof helpers (``compiled`` / ``nogil_sum``) that
``tests/backend/test_opt_toolchain.py`` exercises, and adds the milestone-M1
pipeline described in OPTIMIZER_REWRITE.md 5/7.0/11:

    marshal_in -> cfg_cleanup -> allocate -> (export back | emit)

Levels (7.0), with the 11-M1 *temporary* aliasing -- the real mid-end (SSA /
SCCP / GVN / DCE / LICM / switch passes) lands in M2/M3, so at M1 every level is
``cfg_cleanup`` + an allocator:

    minimal  (-O0): cfg_cleanup -> bump allocation
    fast     (-O1): cfg_cleanup -> try-bump (bump, fall back to packing on overflow)
    standard (-O2): cfg_cleanup -> interference (packing) allocation

The thin Python shim ``sonolus/backend/optimize/__init__.py`` (``run_passes`` /
``optimize_and_finalize`` / ``cfg_to_engine_node``) calls the ``*_cfg`` entry
points below with a level *name*; ``compile_mode`` uses ``optimize_and_finalize``.

This module also populates the phase registry consulted by ``ir.debug_run`` (the
10 debug API), registering ``cfg_cleanup`` and the three allocators so
``debug_run(cfg, phases=["cfg_cleanup"])`` runs a single named phase.
"""

from sonolus.backend._opt.ir cimport Func
from sonolus.backend._opt.midend cimport build_ssa, cfg_cleanup, out_of_ssa
from sonolus.backend._opt.analysis cimport compute_dominators
from sonolus.backend._opt.lower cimport (
    ALLOC_BUMP,
    ALLOC_PACKING,
    ALLOC_TRY_BUMP,
    allocate_func,
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
    """Run the M1 pipeline over ``func``, returning a fresh cleaned arena.

    ``cfg_cleanup`` returns a new ``Func``; the allocator (when requested)
    rewrites that arena in place.
    """
    cdef Func cleaned = cfg_cleanup(func, False)
    if allocate:
        if level == LEVEL_MINIMAL:
            allocate_func(cleaned, ALLOC_BUMP)
        elif level == LEVEL_FAST:
            allocate_func(cleaned, ALLOC_TRY_BUMP)
        else:
            allocate_func(cleaned, ALLOC_PACKING)
    return cleaned


# --------------------------------------------------------------------------
# Python-callable entry points (used by the optimize/__init__ shim).
# --------------------------------------------------------------------------

def run_pipeline_cfg(entry, level, mode=None, callback=None, allocate=True):
    """marshal_in -> cfg_cleanup -> (allocate) -> to_basic_blocks.

    ``allocate=False`` stops before allocation (for ``visualize_cfg``), leaving
    temp places unallocated.
    """
    cdef int lvl = _level_code(level)
    cdef Func func = <Func>marshal_in(entry, mode, callback)
    cdef Func result = _pipeline(func, lvl, allocate)
    return to_basic_blocks(result)


def optimize_and_finalize_cfg(entry, level, mode=None, callback=None):
    """marshal_in -> cfg_cleanup -> allocate -> emit (fused; no BasicBlock export)."""
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
register_phase("dominators", _phase_dominators)
register_phase("bump", _phase_bump)
register_phase("packing", _phase_packing)
register_phase("try_bump", _phase_try_bump)
