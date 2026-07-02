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

# M2 mid-end round (OPTIMIZER_REWRITE.md 7.2.2-7.2.7): SCCP -> simplify/GVN ->
# DCE over the SSA arena, repeated once more when ``allow_repeat`` and anything
# changed. Consumes and returns an SSA-form ``Func`` (verify()-green); the
# orchestrator sandwiches it between ``build_ssa`` and ``out_of_ssa``. Used at the
# ``fast`` level.
cdef Func midend_round(Func func, bint allow_repeat)

# M3 standard (-O2) mid-end: core round (SCCP/GVN/DCE) -> LICM -> rewrite_switch,
# then repeat the core once if anything changed (7.2.5-7.2.7). SSA in / SSA out;
# the ``standard``-level entry the driver sandwiches between ``build_ssa`` and
# ``out_of_ssa``.
cdef Func midend_standard(Func func)

# Individual passes (SSA in / SSA out) -- exposed for the orchestrator and for
# focused debug phases. ``sccp``/``dce`` return fresh compacted arenas; ``gvn``
# rewrites in place and returns the same ``Func``. ``licm`` and ``rewrite_switch``
# return fresh rebuilt arenas (fresh RPO + dominators).
cdef Func sccp(Func func)
cdef Func gvn(Func func)
cdef Func dce(Func func)
cdef Func licm(Func func)
cdef Func rewrite_switch(Func func)
