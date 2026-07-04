"""Smoke tests for the compiled optimizer core (`sonolus.backend._opt`).

These prove the Cython toolchain is wired up: the extension builds, imports,
and can run a ``with nogil:`` region.
"""

from sonolus.backend._opt import driver  # noqa: PLC2701  (first-party private optimizer core)


def test_extension_is_compiled():
    # A .py fallback could define compiled() too, but the extension is a
    # compiled module -- check both the return value and that it's an extension.
    assert driver.compiled() is True
    assert driver.__file__.endswith((".pyd", ".so"))


def test_nogil_region_runs():
    assert driver.nogil_sum(0) == 0
    assert driver.nogil_sum(5) == 10  # 0 + 1 + 2 + 3 + 4
    assert driver.nogil_sum(100) == 4950
