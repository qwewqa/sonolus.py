# cython: language_level=3
"""cdef API for the mid-end passes (milestone M1: cfg_cleanup).

Other optimizer modules cimport ``cfg_cleanup`` to run the CFG-cleanup pass
over an arena ``Func`` (see OPTIMIZER_REWRITE.md section 7.1). The Python-visible
``run_cfg_cleanup`` wrapper (marshal_in -> pass -> to_basic_blocks) lives in
``midend.pyx`` for tests/debugging.
"""

from sonolus.backend._opt.ir cimport Func


cdef Func cfg_cleanup(Func func, bint phi_safe)
cdef Func build_ssa(Func func)
cdef Func out_of_ssa(Func func)
