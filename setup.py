"""Build script for the Cython optimizer core (``sonolus.backend._opt``).

All package metadata lives in ``pyproject.toml``; this file exists solely to
compile the Cython extension modules. See OPTIMIZER_REWRITE.md sections 8 and 9.
"""

import os

from Cython.Build import cythonize
from setuptools import Extension, setup

# A debug build (env ``SONOLUS_OPT_DEBUG_BUILD=1``) keeps Cython bounds/wraparound
# checks and C-level asserts (NDEBUG left undefined) so the optimizer's internal
# ``verify()`` fires; release builds strip them for speed. See §8.
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
