# cython: language_level=3
"""EngineNode emission from the arena IR (milestone M1, section 7.6).

Behaviour-preserving port of ``sonolus/backend/finalize.py`` operating on the
flat ``Func`` arena instead of the Python ``BasicBlock`` graph, plus the one
deliberate section 7.6 addition (idempotent re-flattening of associative left
spines during emission).

``emit_func`` is the ``cdef`` entry the future fused ``optimize_and_finalize``
path calls directly on an already-lowered arena; ``emit_cfg`` (a ``def`` in the
``.pyx``) is the marshal-in + emit convenience used by tests/goldens.
"""

from sonolus.backend._opt.ir cimport Func

cdef object emit_func(Func func)
