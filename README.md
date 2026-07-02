# Sonolus.py
Sonolus engine development in Python. See [docs](https://sonolus.py.qwewqa.xyz) for more information.

## Development

The optimizer core (`sonolus/backend/_opt`) is written in Cython, so a working C compiler is required to
build from source:

- **Windows:** MSVC (install the [Visual Studio Build Tools](https://visualstudio.microsoft.com/downloads/)
  with the "Desktop development with C++" workload).
- **macOS:** the Xcode Command Line Tools (`xcode-select --install`).
- **Linux:** a standard C toolchain (`gcc`/`clang`).

`uv sync` builds the extension automatically via PEP 517 and rebuilds it whenever the `.pyx`/`.pxd`
sources (or `setup.py`/`pyproject.toml`) change, thanks to the configured `[tool.uv] cache-keys`. Users
installing from PyPI get prebuilt wheels and do not need a compiler.

### Debugging the optimizer

Two build/run-time environment variables help when working on the optimizer:

- `SONOLUS_OPT_DEBUG_BUILD=1` (set **at build time**, e.g. `SONOLUS_OPT_DEBUG_BUILD=1 uv sync`) compiles a
  debug build that keeps Cython bounds/wraparound checks and C `assert`s, so the optimizer's internal
  `verify()` invariants fire.
- `SONOLUS_OPT_TRACE=1` (set **at run time**) dumps the CFG (`cfg_to_text`) after each optimizer pass
  during a build, so you can watch the IR evolve.

For focused testing, `sonolus.backend._opt.ir.debug_run(cfg, phases=[...])` runs an explicit list of
named optimizer phases (`cfg_cleanup`, `ssa`, `sccp`, `gvn`, `dce`, `midend`, `licm`, `rewrite_switch`,
`unssa`, `lower`, `packing`, ...) and exports the result as a `BasicBlock` CFG.
