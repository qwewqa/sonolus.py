import os

from Cython.Build import cythonize
from setuptools import Extension, setup

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
        language="c++",
        include_dirs=["sonolus/backend/_opt"],
        undef_macros=["NDEBUG"] if DEBUG_BUILD else [],
    ),
    Extension(
        "sonolus.script.internal._context_state",
        ["sonolus/script/internal/_context_state.pyx"],
        language="c++",
        undef_macros=["NDEBUG"] if DEBUG_BUILD else [],
    ),
]

setup(
    ext_modules=cythonize(
        extensions,
        compiler_directives=compiler_directives,
    ),
)
