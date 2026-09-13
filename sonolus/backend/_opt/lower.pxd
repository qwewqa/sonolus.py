# cython: language_level=3
"""Temp-memory allocation / lowering over the arena IR.

Three allocation strategies rewrite size-1/size>1/size-0 temp places to
``BlockPlace(10000, ...)`` real-block places, with dead-store elimination for
the liveness-based strategies. See ``lower.pyx`` for the contract.
"""

from libc.stdint cimport int32_t, int64_t

from sonolus.backend._opt.ir cimport Func


cdef enum:
    ALLOC_BUMP = 0
    ALLOC_PACKING = 1
    ALLOC_TRY_BUMP = 2


# Block id all temps are packed into (part of the emitted-node contract).
cdef enum:
    TEMP_BLOCK = 10000
    TEMP_SIZE = 4096


# Rewrite ``func`` in place: allocate temps and lower temp places to real-block
# places, doing dead-store elimination for the liveness-based strategies.
cdef void allocate_func(Func func, int32_t strategy) except *


# Fused read-modify-write peephole (place-based), plus the self-copy drop.
# Requires allocated places. Rewrites, in place, every statement-root
# ``OPX_SET(p, BinOp(OPX_GET(p), w))`` into the fused runtime op ``Set<BinOp>``
# (carrying the place in ``aux`` and ``w`` as its sole operand), collapsing
# ``+1``/``-1`` to ``IncrementPost``/``DecrementPost``, and drops every statement-root
# ``OPX_SET(p, OPX_GET(p))``. The wire point is the driver ``_pipeline`` for
# fast + standard; minimal stays un-fused and keeps its self-copies.
cdef void fuse_rmw(Func func) except *


# Requires allocated, non-SSA places. Copy results must not feed other values.
cdef void fuse_copy(Func func) except *


# Requires allocated non-SSA places, after Copy fusion. Return eliminated read cost.
cdef int64_t fuse_store_results(Func func) except -1


# Out-of-SSA + treeify: consume a value-based SSA ``Func`` (from
# ``midend.build_ssa``) and return a fresh, legal non-SSA arena ready for
# ``allocate_func`` + emission. This is the production de-SSA path;
# ``midend.out_of_ssa`` is the naive/debug variant. The driver slots this between
# the mid-end and allocation (see lower.pyx run_lower).
cdef Func lower_from_ssa(Func func)


# If-conversion (standard level only): consume a value-based SSA ``Func`` (AFTER
# the mid-end, while phis exist) and fold diamonds/triangles/{VALUE C, NONE}
# two-way blocks into ``If`` EXPRESSION selects feeding the join phis, returning
# a fresh SSA ``Func`` (verify()-green). Runs to a local fixpoint. The driver
# runs this between ``midend_standard`` and ``lower_from_ssa`` (tests drive it via
# ``run_ifconv`` in lower.pyx).
cdef Func if_convert(Func func)
