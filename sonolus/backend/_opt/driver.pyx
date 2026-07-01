# cython: language_level=3
"""Trivial driver proving the Cython toolchain (milestone M0).

This grows into the real optimizer driver (levels, ``run_passes``,
``optimize_and_finalize``, ``compile_mode``) in later milestones; for now it
only proves that the extension compiles, imports, and can enter a ``nogil``
region -- the property the whole rewrite depends on (§8 threading model).
"""


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
    """Compute ``sum(range(n))`` inside a ``with nogil:`` block.

    Exercises releasing and re-acquiring the GIL so the toolchain proof covers
    the ``nogil`` regions the optimizer passes will run in.
    """
    cdef long result
    with nogil:
        result = _triangular(n)
    return result
