# cython: language_level=3
"""cdef API for the mid-end passes.

Other optimizer modules cimport these to run the mid-end over an arena ``Func``:
``cfg_cleanup`` (structural CFG cleanup), ``build_ssa`` / ``out_of_ssa`` (SSA
construction and naive de-SSA), and the ``midend_round`` / ``midend_standard``
orchestrators plus the individual SSA passes. The Python-visible ``run_*``
wrappers (marshal_in -> passes -> to_basic_blocks) live in ``midend.pyx`` for
tests/debugging.
"""

from sonolus.backend._opt.ir cimport Func


# ``func`` MUST be non-SSA (no OPX_PHI): cfg_cleanup is not phi-aware and raises
# ValueError on an SSA-form arena. ``phi_safe`` only disables tail-duplication
# (used by the post-phi-elimination lower_from_ssa layout pass), NOT phi handling.
cdef Func cfg_cleanup(Func func, bint phi_safe)
cdef Func build_ssa(Func func)
cdef Func out_of_ssa(Func func)

# Mid-end core round: SCCP -> simplify/GVN ->
# DCE over the SSA arena, repeated once more when ``allow_repeat`` and anything
# changed. Consumes and returns an SSA-form ``Func`` (verify()-green); the
# orchestrator sandwiches it between ``build_ssa`` and ``out_of_ssa``. Used at the
# ``fast`` level.
cdef Func midend_round(Func func, bint allow_repeat)

# Standard (-O2) mid-end: core round (SCCP/GVN/DCE) -> LICM -> rewrite_switch,
# then repeat the core once if anything changed. SSA in / SSA out;
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
