# cython: language_level=3
"""Cython declarations for the vendored klib khash instantiations (khash_shim.h).

Int-keyed open-addressing hash tables back the optimizer's hot-path lookup maps
(const interning, GVN availability, SSA cur_def) -- flat arrays with far less
per-element overhead than a Python dict, and error codes instead of C++
``bad_alloc`` so the callers stay ``noexcept`` and raise ``MemoryError`` on OOM.

Usage notes (see the project_m9_klib_khash memory):

* ``kh_get_*`` returns an iterator; the key is absent when it equals
  ``h.n_buckets`` (read the struct field directly rather than the ``kh_end``
  macro, which cannot be typed per-table in Cython).
* ``kh_put_*`` sets ``ret``: -1 on OOM, 0 if the key was already present, and
  1/2 for a freshly inserted key. Read/write the slot via ``h.vals[it]``.
* DETERMINISM: bucket order is unspecified -- lookups only, never iterate one to
  produce output.
"""
from libc.stdint cimport int32_t, uint64_t


cdef extern from "khash_shim.h" nogil:
    ctypedef unsigned int khint_t

    # uint64 key -> int32 value. Only the fields read by the passes are declared.
    ctypedef struct kh_i64i32_t:
        khint_t n_buckets
        khint_t size
        uint64_t *keys
        int32_t *vals

    kh_i64i32_t* kh_init_i64i32()
    void kh_destroy_i64i32(kh_i64i32_t* h)
    void kh_clear_i64i32(kh_i64i32_t* h)
    khint_t kh_get_i64i32(kh_i64i32_t* h, uint64_t key)
    khint_t kh_put_i64i32(kh_i64i32_t* h, uint64_t key, int* ret)
    void kh_del_i64i32(kh_i64i32_t* h, khint_t x)
    int kh_resize_i64i32(kh_i64i32_t* h, khint_t n_buckets)
