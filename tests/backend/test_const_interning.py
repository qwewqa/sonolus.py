"""Arena constant interning: int/float unification, -0.0 and NaN handling.

The const table interns by f64 bit-pattern (so 2 and 2.0 share an id, but -0.0
is distinct from 0.0) with NaN canonicalized to one quiet-NaN.
"""

from __future__ import annotations

import math
import struct

from hypothesis import given
from hypothesis import strategies as st

from sonolus.backend._opt import ir  # noqa: PLC2701
from sonolus.backend.optimize.flow import BasicBlock

_CANON_NAN_BITS = struct.pack("<d", math.nan)


def _fresh():
    # A Func is a valid empty arena; marshalling a trivial CFG keeps __cinit__
    # semantics without depending on marshal internals.
    return ir.marshal_in(BasicBlock(), None, None)


def _bits(x: float) -> bytes:
    return struct.pack("<d", x)


@given(st.floats(allow_nan=True, allow_infinity=True))
def test_intern_readback_bit_exact(value):
    f = _fresh()
    cid = f.intern_const(value)
    back = f.get_const(cid)
    if math.isnan(value):
        # NaN is canonicalized; any NaN reads back as the one canonical NaN.
        assert math.isnan(back)
        assert _bits(back) == _CANON_NAN_BITS
    else:
        assert _bits(back) == _bits(value)


@given(st.integers(min_value=-(2**53), max_value=2**53))
def test_int_float_unification(n):
    f = _fresh()
    assert f.intern_const(n) == f.intern_const(float(n))
    assert f.get_const(f.intern_const(n)) == float(n)


def test_neg_zero_distinct_from_zero():
    f = _fresh()
    pz = f.intern_const(0.0)
    nz = f.intern_const(-0.0)
    assert pz != nz
    assert _bits(f.get_const(pz)) == _bits(0.0)
    assert _bits(f.get_const(nz)) == _bits(-0.0)


def test_all_nans_intern_to_one_id():
    f = _fresh()
    ids = {
        f.intern_const(math.nan),
        f.intern_const(float("nan")),
        f.intern_const(-math.nan),
        f.intern_const(struct.unpack("<d", struct.pack("<Q", 0x7FF8000000000001))[0]),
    }
    assert len(ids) == 1


def test_infinities():
    f = _fresh()
    pi = f.intern_const(math.inf)
    ni = f.intern_const(-math.inf)
    assert pi != ni
    assert f.get_const(pi) == math.inf
    assert f.get_const(ni) == -math.inf
    # Interning the same value twice yields the same id.
    assert f.intern_const(math.inf) == pi


def test_repeated_interning_is_stable():
    f = _fresh()
    a = f.intern_const(3.5)
    b = f.intern_const(3.5)
    c = f.intern_const(7)
    assert a == b
    assert a != c
