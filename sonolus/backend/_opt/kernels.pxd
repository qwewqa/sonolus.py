# cython: language_level=3
"""cdef API for the constant-fold kernels.

SCCP (``midend.pyx``) cimports :c:func:`fold_op` to evaluate an
all-constant-operand instance of a foldable op at compile time:

    cdef double out
    if fold_op(op_id, args, nargs, &out) == FOLD_OK:
        ...  # `out` is the folded constant
    else:
        ...  # FOLD_NOT_CONSTANT: leave the op as-is

``fold_op`` is ``noexcept nogil`` -- callable from the nogil pass regions. It
never allocates, never touches Python, and returns a status rather than raising.

See ``kernels.pyx`` for the numeric contract (a literal transcription of
``sonolus/backend/interpret.py`` and ``math_impls``/``easing.py``/``bucket.py``).
"""

from libc.stdint cimport uint16_t


cdef enum:
    FOLD_OK = 0            # *out was set to the folded double
    FOLD_NOT_CONSTANT = 1  # not a compile-time constant (would raise / complex /
    #                        division-by-zero / unsupported op / wrong arity)


cdef int fold_op(uint16_t op, const double* args, int nargs, double* out) noexcept nogil
