# Sonolus.py
Sonolus engine development in Python. See [docs](https://sonolus.py.qwewqa.xyz) for more information.

## Development

The optimizer core (`sonolus/backend/_opt`) is written in Cython, so a working C compiler is required to
build from source:

- **Windows:** MSVC (install the [Visual Studio Build Tools](https://visualstudio.microsoft.com/downloads/)
  with the "Desktop development with C++" workload).
- **macOS:** the Xcode Command Line Tools (`xcode-select --install`).
- **Linux:** a standard C toolchain (`gcc`/`clang`).
