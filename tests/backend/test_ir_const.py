"""IRConst construction: the small-integer cache is shared, so it must never be mutated.

A later construction of the same number must not reach through and change what an earlier caller holds, or
cfg text and repr become order-dependent. -0.0 is integral, so it lands on the cached zero, a collapse that
emit.pyx and midend.pyx both rely on.
"""

from __future__ import annotations

import math

from sonolus.backend.ir import IRConst


def test_later_construction_does_not_mutate_a_shared_instance():
    values = [0, 1, 3, 256, 1_000_000]
    firsts = [IRConst(v) for v in values]
    before = [(c.value, str(c), repr(c)) for c in firsts]

    for v in values:
        IRConst(float(v))

    assert [(c.value, str(c), repr(c)) for c in firsts] == before


def test_cached_instances_are_shared_across_int_and_float_spellings():
    assert IRConst(3) is IRConst(3.0)


def test_negative_zero_collapses_to_positive_zero():
    negative_zero = IRConst(-0.0)

    assert negative_zero is IRConst(0)
    assert negative_zero.value == 0
    assert math.copysign(1.0, negative_zero.value) == 1.0


def test_values_off_the_cached_path_keep_their_value():
    assert IRConst(2.5).value == 2.5
    assert IRConst(-2.5).value == -2.5
    assert IRConst(1_000_000).value == 1_000_000
    assert IRConst(2.5) == IRConst(2.5)
