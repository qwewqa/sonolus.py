# cython: language_level=3
"""Temp-memory allocation / lowering over the arena IR (milestone M1, §7.5).

Three allocation strategies rewrite size-1/size>1/size-0 temp places to
``BlockPlace(10000, ...)`` real-block places, with dead-store elimination for
the liveness-based strategies. See ``lower.pyx`` for the contract.
"""

from libc.stdint cimport int32_t

from sonolus.backend._opt.ir cimport Func


cdef enum:
    ALLOC_BUMP = 0
    ALLOC_PACKING = 1
    ALLOC_TRY_BUMP = 2


# Block id all temps are packed into (matches finalize / old allocate.py).
cdef enum:
    TEMP_BLOCK = 10000
    TEMP_SIZE = 4096


# Rewrite ``func`` in place: allocate temps and lower temp places to real-block
# places, doing dead-store elimination for the liveness-based strategies.
cdef void allocate_func(Func func, int32_t strategy) except *
