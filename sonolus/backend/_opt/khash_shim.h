/* Cython-facing klib khash instantiations.
 *
 * khash's generated functions are ``static kh_inline`` (per translation unit),
 * so including this header in several .pyx-generated .cpp files is safe: each
 * gets its own private copy, with no linker conflict. See the vendored khash.h
 * (MIT, upstream-verbatim) and the _khash.pxd Cython declarations.
 *
 * DETERMINISM: khash bucket order is unspecified. Use these tables for lookups
 * only -- never iterate one to produce output (use a vector or sort keys).
 */
#ifndef SONOLUS_OPT_KHASH_SHIM_H
#define SONOLUS_OPT_KHASH_SHIM_H

#include <stdint.h>
#include "khash.h"

/* uint64 key -> int32 value. Const interning keys on the f64 bit pattern; the
 * packed-key lookup maps (GVN availability, SSA cur_def) pack two int32 ids into
 * the 64-bit key. */
KHASH_MAP_INIT_INT64(i64i32, int32_t)

#endif /* SONOLUS_OPT_KHASH_SHIM_H */
