# cython: language_level=3
"""EngineNode emission from the arena IR.

Emits the EngineNode tree directly from the flat ``Func`` arena (see emit.pyx
for the full contract), including idempotent re-flattening of associative left
spines during emission.

``emit_func`` is the ``cdef`` entry the fused ``optimize_and_finalize`` path
calls directly on an already-lowered arena; ``emit_cfg`` (a ``def`` in the
``.pyx``) is the marshal-in + emit convenience used by tests/goldens.
"""

from sonolus.backend._opt.ir cimport Func

cdef object emit_func(Func func)
