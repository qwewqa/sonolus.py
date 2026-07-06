# cython: language_level=3
"""Constant-fold kernels: C-double evaluation of every foldable op.

Owns the numeric semantics SCCP folds with.
The single dispatcher `fold_op` evaluates one all-constant-operand
instance of a foldable op and returns ``FOLD_OK`` (with ``*out`` set) or
``FOLD_NOT_CONSTANT``.

================================================================================
CONTRACT (read before touching a kernel)
================================================================================

* **Literal transcription of the oracle** ``sonolus/backend/interpret.py``
  (and the reference bodies it mirrors: ``math_impls`` for ``Rem``/``Frac``,
  ``easing.py`` for the 36 ``Ease*``, ``bucket.py`` for ``Judge``/``JudgeSimple``,
  JS ``Math.sign`` for ``Sign``). The differential tests
  (``tests/backend/test_fold_kernels.py``) assert bit-for-bit equality with the
  oracle over Hypothesis-generated operands, so drift is caught.

* **NOT_CONSTANT iff the oracle would not yield a finite-domain real double.**
  If ``Interpreter().run(...)`` would raise ``ZeroDivisionError`` /
  ``ValueError`` / ``OverflowError`` (division-by-zero, ``Log``/``Arcsin``/...
  domain errors, ``sinh`` overflow, ``ceil``/``round`` of inf/nan) or would
  return a *complex* (``Power(-2, 0.5)``), ``fold_op`` returns
  ``FOLD_NOT_CONSTANT`` rather than folding or trapping. Otherwise it reproduces
  the oracle's double exactly, **including -0.0 and NaN payloads** (per IEEE;
  the differential tests treat any NaN bit-pattern as equal, matching the const
  table's NaN canonicalization). One exception to the "iff": the ``If``/``Switch*``
  selects also return ``FOLD_NOT_CONSTANT`` when the test or an examined key is
  not f32-roundtrip-exact (``_f32_exact``), so compile-time f64 selection matches
  the 32-bit runtime exactly.

* **Arity is fixed/exact.** The mid-end IR is *binary* for the associative ops
  (``Add``/``Subtract``/``Multiply``/``Divide``/``Power``/``Mod``/``Rem`` take
  exactly 2 operands here -- n-ary fusion happens only at emission, after SCCP),
  and most other ops have a fixed arity (unary; ``If``/``Clamp``/``Lerp``/... 3;
  ``Remap``/``JudgeSimple`` 5; ``Judge`` 8; the 36 ``Ease*`` 1). Variadic:
  ``And``/``Or`` (any arity; short-circuit value fold) and the four ``Switch*``
  selects (``Switch`` odd n >= 1, ``SwitchWithDefault`` even n >= 2,
  ``SwitchInteger`` n >= 1, ``SwitchIntegerWithDefault`` n >= 2). A wrong
  arity/parity yields ``FOLD_NOT_CONSTANT``.

* **Value semantics only -- SCCP layers its policy exceptions separately.**
  Those exceptions (``Multiply`` by a *known* 0 -> 0 with the other
  operand unknown; ``And``-with-0 / ``Or``-with-1 short-circuits assuming
  boolean) are *SCCP-lattice* decisions about partially-unknown operands. This
  file only ever sees all-constant operands, so it folds ``And``/``Or``/
  ``Multiply`` by strict value semantics (``Multiply(0, inf) = nan``,
  ``And(2, 3) = 3``, ``Or(0, 5) = 5``). SCCP can then apply its exceptions on
  top without contradicting the kernel.

The Python-visible `fold` wrapper (``None`` == NOT_CONSTANT) and the
foldability-table readers exist for the tests; the compiled hot path is
``fold_op``.
"""

from libc.stdint cimport uint16_t
from libc.math cimport (
    M_PI,
    acos,
    asin,
    atan,
    atan2,
    ceil,
    copysign,
    cos,
    cosh,
    fabs,
    floor,
    fmod,
    isfinite,
    isinf,
    isnan,
    log,
    sin,
    sinh,
    sqrt,
    tan,
    tanh,
)
from libc.math cimport pow as c_pow
from libc.math cimport round as c_round
from libc.math cimport trunc as c_trunc
from libc.stdlib cimport free, malloc

from sonolus.backend._opt._ops_gen cimport *  # OP_* ids, OP_TABLE_SIZE, SONOLUS_OP_FOLDABLE


# ==========================================================================
# Small numeric helpers (each a literal mirror of a Python builtin / libm rule)
# ==========================================================================

cdef inline bint _is_odd_int(double x) noexcept nogil:
    # CPython's DOUBLE_IS_ODD_INTEGER.
    return fmod(fabs(x), 2.0) == 1.0


cdef inline double _pymin2(double a, double b) noexcept nogil:
    # Python ``min(a, b)``: keep the first unless the second is strictly less
    # (so min(1, nan) == 1, min(0.0, -0.0) == 0.0).
    return b if b < a else a


cdef inline double _pymax2(double a, double b) noexcept nogil:
    # Python ``max(a, b)``: keep the first unless the second is strictly greater.
    return b if b > a else a


cdef inline double _clamp01(double x) noexcept nogil:
    # ``max(0, min(1, x))`` == clamp(x, 0, 1) with Python min/max semantics.
    # NaN -> 1.0 (min(1, nan)=1 then max(0, 1)=1); -0.0 -> +0.0.
    cdef double m = x if x < 1.0 else 1.0
    return m if m > 0.0 else 0.0


cdef inline bint _f32_exact(double v) noexcept nogil:
    # Fold guard for the If/Switch* test and keys: fold only when `v` survives an
    # f32 roundtrip, so compile-time f64 selection agrees exactly with the 32-bit
    # runtime. NaN folds through (truthiness and `==` key-miss agree in both widths).
    return isnan(v) or v == <double><float>v


cdef inline double _pymod(double a, double b) noexcept nogil:
    # CPython float_rem (``a % b``); caller must ensure b != 0. Remainder takes
    # the sign of the divisor; zero remainder -> copysign(0, b).
    cdef double mod = fmod(a, b)
    if mod != 0.0:
        if (b < 0.0) != (mod < 0.0):
            mod += b
    else:
        mod = copysign(0.0, b)
    return mod


cdef inline int _m1(double r, double x, double* out) noexcept nogil:
    # CPython ``math_1`` error rule: domain error (nan out from non-nan in) and
    # singularity/overflow (inf out from finite in) both raise -> NOT_CONSTANT.
    if isnan(r) and not isnan(x):
        return FOLD_NOT_CONSTANT
    if isinf(r) and isfinite(x):
        return FOLD_NOT_CONSTANT
    out[0] = r
    return FOLD_OK


cdef inline int _m2(double r, double x, double y, double* out) noexcept nogil:
    # CPython ``math_2`` error rule (atan2).
    if isnan(r) and not isnan(x) and not isnan(y):
        return FOLD_NOT_CONSTANT
    if isinf(r) and isfinite(x) and isfinite(y):
        return FOLD_NOT_CONSTANT
    out[0] = r
    return FOLD_OK


cdef int _float_pow(double iv, double iw, double* out) noexcept nogil:
    """CPython ``float.__pow__`` (``operator.pow``) semantics for ``Op.Power``.

    Returns FOLD_NOT_CONSTANT exactly where the oracle would not yield a real
    double: negative base with non-integer exponent (complex), ``0 ** negative``
    (ZeroDivisionError), and overflow (OverflowError).
    """
    cdef double ix
    cdef double aiv
    cdef bint negate
    if iw == 0.0:            # v ** 0 == 1, even nan/0/inf ** 0
        out[0] = 1.0
        return FOLD_OK
    if isnan(iv):            # nan ** w == nan (w != 0)
        out[0] = iv
        return FOLD_OK
    if isnan(iw):            # v ** nan == nan, except 1 ** nan == 1
        out[0] = 1.0 if iv == 1.0 else iw
        return FOLD_OK
    if isinf(iw):
        aiv = fabs(iv)
        if aiv == 1.0:
            out[0] = 1.0
        elif (iw > 0.0) == (aiv > 1.0):
            out[0] = fabs(iw)   # +inf
        else:
            out[0] = 0.0
        return FOLD_OK
    if isinf(iv):
        if iw > 0.0:
            out[0] = iv if _is_odd_int(iw) else fabs(iv)
        else:
            out[0] = copysign(0.0, iv) if _is_odd_int(iw) else 0.0
        return FOLD_OK
    if iv == 0.0:
        if iw < 0.0:
            return FOLD_NOT_CONSTANT   # 0 ** negative -> ZeroDivisionError
        out[0] = iv if _is_odd_int(iw) else 0.0
        return FOLD_OK
    negate = False
    if iv < 0.0:
        if iw != floor(iw):
            return FOLD_NOT_CONSTANT   # negative ** fractional -> complex
        iv = -iv
        negate = _is_odd_int(iw)
    ix = c_pow(iv, iw)
    if isinf(ix):
        return FOLD_NOT_CONSTANT       # finite ** finite -> inf == overflow
    out[0] = -ix if negate else ix
    return FOLD_OK


cdef inline double _fpow(double iv, double iw) noexcept nogil:
    """``float.__pow__`` restricted to the cases the ``Ease*`` bodies produce.

    Easing operands are finite and never hit the complex / 0**negative /
    overflow paths (bases in [-2, 2], exponents small integers or base-2 with a
    bounded exponent), so this returns the double directly. It still replicates
    float_pow's sign handling so ``(x-1)**3`` matches the oracle bit-for-bit.
    """
    cdef double ix
    cdef bint negate
    if iw == 0.0:
        return 1.0
    if iv == 0.0:
        return iv if _is_odd_int(iw) else 0.0
    negate = False
    if iv < 0.0:
        iv = -iv
        negate = _is_odd_int(iw)
    ix = c_pow(iv, iw)
    return -ix if negate else ix


cdef inline double _judge(
    double diff,
    double perfect_min,
    double perfect_max,
    double great_min,
    double great_max,
    double good_min,
    double good_max,
) noexcept nogil:
    # bucket._judge: first inclusive window wins; else 0 (miss).
    if perfect_min <= diff <= perfect_max:
        return 1.0
    if great_min <= diff <= great_max:
        return 2.0
    if good_min <= diff <= good_max:
        return 3.0
    return 0.0


# ==========================================================================
# Ease* dispatch (36 ops). Each mirrors interpret.py's _EASE_FUNCS body, which
# in turn mirrors easing.py. Input is clamped to [0, 1] first.
# ==========================================================================

cdef int _fold_ease(uint16_t op, double x, double* out) noexcept nogil:
    cdef double c1, c2, c3, c4, c5, arg
    x = _clamp01(x)

    # ---- Back ----
    if op == OP_EaseInBack:
        c1 = 1.70158
        c3 = c1 + 1.0
        out[0] = c3 * _fpow(x, 3.0) - c1 * _fpow(x, 2.0)
        return FOLD_OK
    if op == OP_EaseOutBack:
        c1 = 1.70158
        c3 = c1 + 1.0
        out[0] = 1.0 + c3 * _fpow(x - 1.0, 3.0) + c1 * _fpow(x - 1.0, 2.0)
        return FOLD_OK
    if op == OP_EaseInOutBack:
        c1 = 1.70158
        c2 = c1 * 1.525
        if x < 0.5:
            out[0] = (_fpow(2.0 * x, 2.0) * ((c2 + 1.0) * 2.0 * x - c2)) / 2.0
        else:
            out[0] = (_fpow(2.0 * x - 2.0, 2.0) * ((c2 + 1.0) * (2.0 * x - 2.0) + c2) + 2.0) / 2.0
        return FOLD_OK
    if op == OP_EaseOutInBack:
        c1 = 1.70158
        c3 = c1 + 1.0
        if x < 0.5:
            out[0] = (1.0 + c3 * _fpow(2.0 * x - 1.0, 3.0) + c1 * _fpow(2.0 * x - 1.0, 2.0)) / 2.0
        else:
            out[0] = (c3 * _fpow(2.0 * x - 1.0, 3.0) - c1 * _fpow(2.0 * x - 1.0, 2.0)) / 2.0 + 0.5
        return FOLD_OK

    # ---- Circ (sqrt of a value proven >= 0; guard mirrors math.sqrt raising) ----
    if op == OP_EaseInCirc:
        arg = 1.0 - _fpow(x, 2.0)
        if arg < 0.0:
            return FOLD_NOT_CONSTANT
        out[0] = 1.0 - sqrt(arg)
        return FOLD_OK
    if op == OP_EaseOutCirc:
        arg = 1.0 - _fpow(x - 1.0, 2.0)
        if arg < 0.0:
            return FOLD_NOT_CONSTANT
        out[0] = sqrt(arg)
        return FOLD_OK
    if op == OP_EaseInOutCirc:
        if x < 0.5:
            arg = 1.0 - _fpow(2.0 * x, 2.0)
            if arg < 0.0:
                return FOLD_NOT_CONSTANT
            out[0] = (1.0 - sqrt(arg)) / 2.0
        else:
            arg = 1.0 - _fpow(2.0 * x - 2.0, 2.0)
            if arg < 0.0:
                return FOLD_NOT_CONSTANT
            out[0] = (sqrt(arg) + 1.0) / 2.0
        return FOLD_OK
    if op == OP_EaseOutInCirc:
        arg = 1.0 - _fpow(2.0 * x - 1.0, 2.0)
        if arg < 0.0:
            return FOLD_NOT_CONSTANT
        if x < 0.5:
            out[0] = sqrt(arg) / 2.0
        else:
            out[0] = (1.0 - sqrt(arg)) / 2.0 + 0.5
        return FOLD_OK

    # ---- Cubic ----
    if op == OP_EaseInCubic:
        out[0] = _fpow(x, 3.0)
        return FOLD_OK
    if op == OP_EaseOutCubic:
        out[0] = 1.0 - _fpow(1.0 - x, 3.0)
        return FOLD_OK
    if op == OP_EaseInOutCubic:
        if x < 0.5:
            out[0] = 4.0 * _fpow(x, 3.0)
        else:
            out[0] = 1.0 - _fpow(-2.0 * x + 2.0, 3.0) / 2.0
        return FOLD_OK
    if op == OP_EaseOutInCubic:
        if x < 0.5:
            out[0] = (1.0 - _fpow(1.0 - 2.0 * x, 3.0)) / 2.0
        else:
            out[0] = (_fpow(2.0 * x - 1.0, 3.0)) / 2.0 + 0.5
        return FOLD_OK

    # ---- Elastic ----
    if op == OP_EaseInElastic:
        c4 = (2.0 * M_PI) / 3.0
        if x == 0.0 or x == 1.0:
            out[0] = x
        else:
            out[0] = -_fpow(2.0, 10.0 * x - 10.0) * sin((x * 10.0 - 10.75) * c4)
        return FOLD_OK
    if op == OP_EaseOutElastic:
        c4 = (2.0 * M_PI) / 3.0
        if x == 0.0 or x == 1.0:
            out[0] = x
        else:
            out[0] = _fpow(2.0, -10.0 * x) * sin((x * 10.0 - 0.75) * c4) + 1.0
        return FOLD_OK
    if op == OP_EaseInOutElastic:
        c5 = (2.0 * M_PI) / 4.5
        if x == 0.0 or x == 1.0:
            out[0] = x
        elif x < 0.5:
            out[0] = -(_fpow(2.0, 20.0 * x - 10.0) * sin((20.0 * x - 11.125) * c5)) / 2.0
        else:
            out[0] = (_fpow(2.0, -20.0 * x + 10.0) * sin((20.0 * x - 11.125) * c5)) / 2.0 + 1.0
        return FOLD_OK
    if op == OP_EaseOutInElastic:
        c4 = (2.0 * M_PI) / 3.0
        if x < 0.5:
            if x == 0.0:
                out[0] = 0.0
            else:
                out[0] = (_fpow(2.0, -20.0 * x) * sin((20.0 * x - 0.75) * c4)) / 2.0 + 0.5
        elif x == 1.0:
            out[0] = 1.0
        else:
            out[0] = (-_fpow(2.0, 10.0 * (2.0 * x - 1.0) - 10.0) * sin((20.0 * x - 20.75) * c4)) / 2.0 + 0.5
        return FOLD_OK

    # ---- Expo ----
    if op == OP_EaseInExpo:
        out[0] = 0.0 if x == 0.0 else _fpow(2.0, 10.0 * x - 10.0)
        return FOLD_OK
    if op == OP_EaseOutExpo:
        out[0] = 1.0 if x == 1.0 else 1.0 - _fpow(2.0, -10.0 * x)
        return FOLD_OK
    if op == OP_EaseInOutExpo:
        if x == 0.0 or x == 1.0:
            out[0] = x
        elif x < 0.5:
            out[0] = _fpow(2.0, 20.0 * x - 10.0) / 2.0
        else:
            out[0] = (2.0 - _fpow(2.0, -20.0 * x + 10.0)) / 2.0
        return FOLD_OK
    if op == OP_EaseOutInExpo:
        if x == 0.0 or x == 1.0:
            out[0] = x
        elif x < 0.5:
            out[0] = (1.0 - _fpow(2.0, -20.0 * x)) / 2.0
        else:
            out[0] = (_fpow(2.0, 20.0 * x - 20.0)) / 2.0 + 0.5
        return FOLD_OK

    # ---- Quad ----
    if op == OP_EaseInQuad:
        out[0] = _fpow(x, 2.0)
        return FOLD_OK
    if op == OP_EaseOutQuad:
        out[0] = 1.0 - _fpow(1.0 - x, 2.0)
        return FOLD_OK
    if op == OP_EaseInOutQuad:
        if x < 0.5:
            out[0] = 2.0 * _fpow(x, 2.0)
        else:
            out[0] = 1.0 - _fpow(-2.0 * x + 2.0, 2.0) / 2.0
        return FOLD_OK
    if op == OP_EaseOutInQuad:
        if x < 0.5:
            out[0] = (1.0 - _fpow(1.0 - 2.0 * x, 2.0)) / 2.0
        else:
            out[0] = (_fpow(2.0 * x - 1.0, 2.0)) / 2.0 + 0.5
        return FOLD_OK

    # ---- Quart ----
    if op == OP_EaseInQuart:
        out[0] = _fpow(x, 4.0)
        return FOLD_OK
    if op == OP_EaseOutQuart:
        out[0] = 1.0 - _fpow(1.0 - x, 4.0)
        return FOLD_OK
    if op == OP_EaseInOutQuart:
        if x < 0.5:
            out[0] = 8.0 * _fpow(x, 4.0)
        else:
            out[0] = 1.0 - _fpow(-2.0 * x + 2.0, 4.0) / 2.0
        return FOLD_OK
    if op == OP_EaseOutInQuart:
        if x < 0.5:
            out[0] = (1.0 - _fpow(1.0 - 2.0 * x, 4.0)) / 2.0
        else:
            out[0] = (_fpow(2.0 * x - 1.0, 4.0)) / 2.0 + 0.5
        return FOLD_OK

    # ---- Quint ----
    if op == OP_EaseInQuint:
        out[0] = _fpow(x, 5.0)
        return FOLD_OK
    if op == OP_EaseOutQuint:
        out[0] = 1.0 - _fpow(1.0 - x, 5.0)
        return FOLD_OK
    if op == OP_EaseInOutQuint:
        if x < 0.5:
            out[0] = 16.0 * _fpow(x, 5.0)
        else:
            out[0] = 1.0 - _fpow(-2.0 * x + 2.0, 5.0) / 2.0
        return FOLD_OK
    if op == OP_EaseOutInQuint:
        if x < 0.5:
            out[0] = (1.0 - _fpow(1.0 - 2.0 * x, 5.0)) / 2.0
        else:
            out[0] = (_fpow(2.0 * x - 1.0, 5.0)) / 2.0 + 0.5
        return FOLD_OK

    # ---- Sine ----
    if op == OP_EaseInSine:
        out[0] = 1.0 - cos((x * M_PI) / 2.0)
        return FOLD_OK
    if op == OP_EaseOutSine:
        out[0] = sin((x * M_PI) / 2.0)
        return FOLD_OK
    if op == OP_EaseInOutSine:
        out[0] = -(cos(M_PI * x) - 1.0) / 2.0
        return FOLD_OK
    if op == OP_EaseOutInSine:
        if x < 0.5:
            out[0] = sin(M_PI * x) / 2.0
        else:
            out[0] = 1.0 - sin(M_PI * x) / 2.0
        return FOLD_OK

    return FOLD_NOT_CONSTANT


# ==========================================================================
# Main dispatcher
# ==========================================================================

cdef int fold_op(uint16_t op, const double* a, int n, double* out) noexcept nogil:
    cdef double x, denom, r
    cdef int i

    # ------------------------- unary -------------------------
    if op == OP_Not:
        if n != 1:
            return FOLD_NOT_CONSTANT
        out[0] = 1.0 if a[0] == 0.0 else 0.0
        return FOLD_OK
    if op == OP_Negate:
        if n != 1:
            return FOLD_NOT_CONSTANT
        out[0] = -a[0]
        return FOLD_OK
    if op == OP_Abs:
        if n != 1:
            return FOLD_NOT_CONSTANT
        out[0] = fabs(a[0])
        return FOLD_OK
    if op == OP_Sign:
        if n != 1:
            return FOLD_NOT_CONSTANT
        x = a[0]
        if isnan(x):
            out[0] = x
        elif x > 0.0:
            out[0] = 1.0
        elif x < 0.0:
            out[0] = -1.0
        else:
            out[0] = x  # +-0.0 preserved
        return FOLD_OK
    if op == OP_Ceil:
        if n != 1:
            return FOLD_NOT_CONSTANT
        x = a[0]
        if isnan(x) or isinf(x):
            return FOLD_NOT_CONSTANT  # math.ceil(inf/nan) raises
        out[0] = ceil(x) + 0.0       # +0.0: oracle returns a Python int (no -0.0)
        return FOLD_OK
    if op == OP_Floor:
        if n != 1:
            return FOLD_NOT_CONSTANT
        x = a[0]
        if isnan(x) or isinf(x):
            return FOLD_NOT_CONSTANT
        out[0] = floor(x) + 0.0
        return FOLD_OK
    if op == OP_Trunc:
        if n != 1:
            return FOLD_NOT_CONSTANT
        x = a[0]
        if isnan(x) or isinf(x):
            return FOLD_NOT_CONSTANT
        out[0] = c_trunc(x) + 0.0
        return FOLD_OK
    if op == OP_Round:
        if n != 1:
            return FOLD_NOT_CONSTANT
        x = a[0]
        if isnan(x) or isinf(x):
            return FOLD_NOT_CONSTANT
        # CPython float.__round__(None): round-half-away then fix up ties to even.
        r = c_round(x)
        if fabs(x - r) == 0.5:
            r = 2.0 * c_round(x / 2.0)
        out[0] = r + 0.0
        return FOLD_OK
    if op == OP_Frac:
        if n != 1:
            return FOLD_NOT_CONSTANT
        # interpret.py: r = x % 1; return r if r >= 0 else r + 1 (r in [0,1); nan->nan).
        r = _pymod(a[0], 1.0)
        out[0] = r if r >= 0.0 else r + 1.0
        return FOLD_OK
    if op == OP_Sin:
        if n != 1:
            return FOLD_NOT_CONSTANT
        return _m1(sin(a[0]), a[0], out)
    if op == OP_Cos:
        if n != 1:
            return FOLD_NOT_CONSTANT
        return _m1(cos(a[0]), a[0], out)
    if op == OP_Tan:
        if n != 1:
            return FOLD_NOT_CONSTANT
        return _m1(tan(a[0]), a[0], out)
    if op == OP_Sinh:
        if n != 1:
            return FOLD_NOT_CONSTANT
        return _m1(sinh(a[0]), a[0], out)
    if op == OP_Cosh:
        if n != 1:
            return FOLD_NOT_CONSTANT
        return _m1(cosh(a[0]), a[0], out)
    if op == OP_Tanh:
        if n != 1:
            return FOLD_NOT_CONSTANT
        return _m1(tanh(a[0]), a[0], out)
    if op == OP_Arcsin:
        if n != 1:
            return FOLD_NOT_CONSTANT
        return _m1(asin(a[0]), a[0], out)
    if op == OP_Arccos:
        if n != 1:
            return FOLD_NOT_CONSTANT
        return _m1(acos(a[0]), a[0], out)
    if op == OP_Arctan:
        if n != 1:
            return FOLD_NOT_CONSTANT
        return _m1(atan(a[0]), a[0], out)
    if op == OP_Log:
        if n != 1:
            return FOLD_NOT_CONSTANT
        return _m1(log(a[0]), a[0], out)
    if op == OP_Degree:
        if n != 1:
            return FOLD_NOT_CONSTANT
        # math.degrees/radians are a bare multiply -- they never raise (overflow
        # to +-inf is returned, not an error), so no math_1 guard.
        out[0] = a[0] * (180.0 / M_PI)
        return FOLD_OK
    if op == OP_Radian:
        if n != 1:
            return FOLD_NOT_CONSTANT
        out[0] = a[0] * (M_PI / 180.0)
        return FOLD_OK

    # ------------------------- comparisons (binary) -------------------------
    if op == OP_Equal:
        if n != 2:
            return FOLD_NOT_CONSTANT
        out[0] = 1.0 if a[0] == a[1] else 0.0
        return FOLD_OK
    if op == OP_NotEqual:
        if n != 2:
            return FOLD_NOT_CONSTANT
        out[0] = 1.0 if a[0] != a[1] else 0.0
        return FOLD_OK
    if op == OP_Greater:
        if n != 2:
            return FOLD_NOT_CONSTANT
        out[0] = 1.0 if a[0] > a[1] else 0.0
        return FOLD_OK
    if op == OP_GreaterOr:
        if n != 2:
            return FOLD_NOT_CONSTANT
        out[0] = 1.0 if a[0] >= a[1] else 0.0
        return FOLD_OK
    if op == OP_Less:
        if n != 2:
            return FOLD_NOT_CONSTANT
        out[0] = 1.0 if a[0] < a[1] else 0.0
        return FOLD_OK
    if op == OP_LessOr:
        if n != 2:
            return FOLD_NOT_CONSTANT
        out[0] = 1.0 if a[0] <= a[1] else 0.0
        return FOLD_OK

    # ------------------------- And / Or (value semantics, any arity) --------
    if op == OP_And:
        # First zero arg, else the last; empty -> 0.0 (matches the oracle loop).
        r = 0.0
        for i in range(n):
            r = a[i]
            if r == 0.0:
                break
        out[0] = r
        return FOLD_OK
    if op == OP_Or:
        r = 0.0
        for i in range(n):
            r = a[i]
            if r != 0.0:
                break
        out[0] = r
        return FOLD_OK

    # -------------------- If / Switch* (strict value selects) ----------------
    # As IR values these are strict selects: a total function of the operands
    # (runtime branch laziness is perf, not a semantic guard -- see ops.py). The
    # f32 guard (_f32_exact) applies to the test and each examined key only;
    # arm/key *values* are returned verbatim and need no guard.
    if op == OP_If:
        if n != 3:
            return FOLD_NOT_CONSTANT
        if not _f32_exact(a[0]):
            return FOLD_NOT_CONSTANT
        out[0] = a[1] if a[0] != 0.0 else a[2]  # NaN test is truthy (NaN != 0.0)
        return FOLD_OK
    if op == OP_Switch:
        # a[0] test, then (key, value) pairs; first f64 match wins, else 0.0.
        if n < 1 or (n & 1) == 0:
            return FOLD_NOT_CONSTANT
        if not _f32_exact(a[0]):
            return FOLD_NOT_CONSTANT
        i = 1
        while i < n:
            if not _f32_exact(a[i]):
                return FOLD_NOT_CONSTANT
            if a[0] == a[i]:  # NaN key never matches; -0.0 key matches 0 test
                out[0] = a[i + 1]
                return FOLD_OK
            i += 2
        out[0] = 0.0
        return FOLD_OK
    if op == OP_SwitchWithDefault:
        # a[0] test, (key, value) pairs, trailing default a[n-1].
        if n < 2 or (n & 1) != 0:
            return FOLD_NOT_CONSTANT
        if not _f32_exact(a[0]):
            return FOLD_NOT_CONSTANT
        i = 1
        while i < n - 1:
            if not _f32_exact(a[i]):
                return FOLD_NOT_CONSTANT
            if a[0] == a[i]:
                out[0] = a[i + 1]
                return FOLD_OK
            i += 2
        out[0] = a[n - 1]
        return FOLD_OK
    if op == OP_SwitchInteger:
        # a[0] selects branch a[1 + int(t)] iff 0 <= t < (n-1) and t integral (exact
        # equals-to-n dispatch, NOT truncation); else 0.0. -0.0 selects branch 0.
        if n < 1:
            return FOLD_NOT_CONSTANT
        if not _f32_exact(a[0]):
            return FOLD_NOT_CONSTANT
        x = a[0]
        if x >= 0.0 and x < <double>(n - 1) and c_trunc(x) == x:
            out[0] = a[1 + <int>x]
            return FOLD_OK
        out[0] = 0.0
        return FOLD_OK
    if op == OP_SwitchIntegerWithDefault:
        # like SwitchInteger over (n-2) branches, with trailing default a[n-1].
        if n < 2:
            return FOLD_NOT_CONSTANT
        if not _f32_exact(a[0]):
            return FOLD_NOT_CONSTANT
        x = a[0]
        if x >= 0.0 and x < <double>(n - 2) and c_trunc(x) == x:
            out[0] = a[1 + <int>x]
            return FOLD_OK
        out[0] = a[n - 1]
        return FOLD_OK

    # ------------------------- arithmetic (binary) -------------------------
    if op == OP_Add:
        if n != 2:
            return FOLD_NOT_CONSTANT
        out[0] = a[0] + a[1]
        return FOLD_OK
    if op == OP_Subtract:
        if n != 2:
            return FOLD_NOT_CONSTANT
        out[0] = a[0] - a[1]
        return FOLD_OK
    if op == OP_Multiply:
        if n != 2:
            return FOLD_NOT_CONSTANT
        out[0] = a[0] * a[1]
        return FOLD_OK
    if op == OP_Divide:
        if n != 2:
            return FOLD_NOT_CONSTANT
        if a[1] == 0.0:
            return FOLD_NOT_CONSTANT  # ZeroDivisionError
        out[0] = a[0] / a[1]
        return FOLD_OK
    if op == OP_Mod:
        if n != 2:
            return FOLD_NOT_CONSTANT
        if a[1] == 0.0:
            return FOLD_NOT_CONSTANT
        out[0] = _pymod(a[0], a[1])
        return FOLD_OK
    if op == OP_Rem:
        if n != 2:
            return FOLD_NOT_CONSTANT
        if a[1] == 0.0:
            return FOLD_NOT_CONSTANT  # abs(a) % abs(b) with b==0 -> ZeroDivisionError
        out[0] = copysign(fmod(fabs(a[0]), fabs(a[1])), a[0])
        return FOLD_OK
    if op == OP_Power:
        if n != 2:
            return FOLD_NOT_CONSTANT
        return _float_pow(a[0], a[1], out)

    # ------------------------- Max / Min / Arctan2 (binary) -----------------
    if op == OP_Max:
        if n != 2:
            return FOLD_NOT_CONSTANT
        out[0] = _pymax2(a[0], a[1])
        return FOLD_OK
    if op == OP_Min:
        if n != 2:
            return FOLD_NOT_CONSTANT
        out[0] = _pymin2(a[0], a[1])
        return FOLD_OK
    if op == OP_Arctan2:
        if n != 2:
            return FOLD_NOT_CONSTANT
        return _m2(atan2(a[0], a[1]), a[0], a[1], out)

    # ------------------------- ternary -------------------------
    if op == OP_Clamp:
        if n != 3:
            return FOLD_NOT_CONSTANT
        # max(a, min(b, x)) with x=a[0], a=a[1], b=a[2].
        out[0] = _pymax2(a[1], _pymin2(a[2], a[0]))
        return FOLD_OK
    if op == OP_Lerp:
        if n != 3:
            return FOLD_NOT_CONSTANT
        # x + (y - x) * s
        out[0] = a[0] + (a[1] - a[0]) * a[2]
        return FOLD_OK
    if op == OP_LerpClamped:
        if n != 3:
            return FOLD_NOT_CONSTANT
        out[0] = a[0] + (a[1] - a[0]) * _clamp01(a[2])
        return FOLD_OK
    if op == OP_Unlerp:
        if n != 3:
            return FOLD_NOT_CONSTANT
        denom = a[1] - a[0]  # hi - lo
        if denom == 0.0:
            return FOLD_NOT_CONSTANT
        out[0] = (a[2] - a[0]) / denom
        return FOLD_OK
    if op == OP_UnlerpClamped:
        if n != 3:
            return FOLD_NOT_CONSTANT
        denom = a[1] - a[0]
        if denom == 0.0:
            return FOLD_NOT_CONSTANT
        out[0] = _clamp01((a[2] - a[0]) / denom)
        return FOLD_OK

    # ------------------------- 5-ary -------------------------
    if op == OP_Remap:
        if n != 5:
            return FOLD_NOT_CONSTANT
        # from_min a0, from_max a1, to_min a2, to_max a3, value a4
        denom = a[1] - a[0]
        if denom == 0.0:
            return FOLD_NOT_CONSTANT
        out[0] = a[2] + (a[3] - a[2]) * (a[4] - a[0]) / denom
        return FOLD_OK
    if op == OP_RemapClamped:
        if n != 5:
            return FOLD_NOT_CONSTANT
        denom = a[1] - a[0]
        if denom == 0.0:
            return FOLD_NOT_CONSTANT
        out[0] = a[2] + (a[3] - a[2]) * _clamp01((a[4] - a[0]) / denom)
        return FOLD_OK
    if op == OP_JudgeSimple:
        if n != 5:
            return FOLD_NOT_CONSTANT
        # source a0, target a1, max_perfect a2, max_great a3, max_good a4
        out[0] = _judge(a[0] - a[1], -a[2], a[2], -a[3], a[3], -a[4], a[4])
        return FOLD_OK

    # ------------------------- 8-ary -------------------------
    if op == OP_Judge:
        if n != 8:
            return FOLD_NOT_CONSTANT
        # source a0, target a1, then perfect/great/good min/max a2..a7
        out[0] = _judge(a[0] - a[1], a[2], a[3], a[4], a[5], a[6], a[7])
        return FOLD_OK

    # ------------------------- Ease* (unary) -------------------------
    if OP_EaseInBack <= op <= OP_EaseOutSine:
        if n != 1:
            return FOLD_NOT_CONSTANT
        return _fold_ease(op, a[0], out)

    return FOLD_NOT_CONSTANT


# ==========================================================================
# Python-visible helpers (tests only; hot path is the cdef fold_op above)
# ==========================================================================

from sonolus.backend.ops import Op as _Op

_OP_IDS = {op.value: i for i, op in enumerate(_Op)}


def fold(op, args):
    """Fold ``op`` over constant ``args``; return the double or ``None``.

    ``op`` may be an `Op`, its name ``str``, or the
    integer op id. ``None`` == FOLD_NOT_CONSTANT.
    """
    cdef int op_id
    cdef int n
    cdef int i
    cdef int status
    cdef double stackbuf[16]
    cdef double* buf = stackbuf
    cdef double result = 0.0
    if isinstance(op, str):          # Op is a StrEnum, so this catches Op too
        op_id = _OP_IDS[op]
    elif isinstance(op, int):
        op_id = op
    else:
        op_id = _OP_IDS[op.value]
    n = len(args)
    # 16-slot stack fast path; heap-fallback for wider variadic ops (large Switch*)
    # so >16-arg folds are exercisable here just as in the SCCP call site.
    if n > 16:
        buf = <double*>malloc(<size_t>n * sizeof(double))
        if buf == NULL:
            raise MemoryError
    try:
        for i in range(n):
            buf[i] = <double>args[i]
        with nogil:
            status = fold_op(<uint16_t>op_id, buf, n, &result)
    finally:
        if n > 16:
            free(buf)
    if status == FOLD_OK:
        return result
    return None


def is_op_foldable(int op_id):
    """Whether op id ``op_id`` is marked FOLDABLE in the generated table."""
    if op_id < 0 or op_id >= OP_TABLE_SIZE:
        raise IndexError("Op id out of range")
    return SONOLUS_OP_FOLDABLE[op_id] != 0


def foldable_op_ids():
    """Sorted list of op ids marked FOLDABLE in the compiled static table."""
    cdef int i
    result = []
    for i in range(OP_TABLE_SIZE):
        if SONOLUS_OP_FOLDABLE[i] != 0:
            result.append(i)
    return result
