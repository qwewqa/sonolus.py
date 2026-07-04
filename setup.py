"""Build script for the Cython optimizer core (``sonolus.backend._opt``).

All package metadata lives in ``pyproject.toml``; this file exists solely to
compile the Cython extension modules.
"""

import os

from Cython.Build import cythonize
from setuptools import Extension, setup

# A debug build (env ``SONOLUS_OPT_DEBUG_BUILD=1``) keeps Cython bounds/wraparound
# checks and C-level asserts (NDEBUG left undefined) so the optimizer's internal
# ``verify()`` fires; release builds strip them for speed.
DEBUG_BUILD = os.environ.get("SONOLUS_OPT_DEBUG_BUILD") == "1"

compiler_directives = {
    "language_level": "3",
    "cdivision": True,
    "boundscheck": DEBUG_BUILD,
    "wraparound": DEBUG_BUILD,
}

extensions = [
    Extension(
        "sonolus.backend._opt.*",
        ["sonolus/backend/_opt/*.pyx"],
        # Built as C++ (established build configuration; no libcpp containers are
        # used -- the int-keyed hot maps use the vendored C klib khash).
        # ``khash.h`` and the generated ``_ops_gen.h`` are plain C headers, valid
        # under C++.
        language="c++",
        # The generated ``_ops_gen.h`` (static op-metadata table) is included via
        # ``cdef extern from "_ops_gen.h"`` in ``_ops_gen.pxd``; make it findable.
        include_dirs=["sonolus/backend/_opt"],
        # In a debug build, actively undefine NDEBUG so C ``assert``s fire even
        # though the toolchain defines it for release extension builds.
        undef_macros=["NDEBUG"] if DEBUG_BUILD else [],
    )
]

setup(
    ext_modules=cythonize(
        extensions,
        compiler_directives=compiler_directives,
    ),
)
