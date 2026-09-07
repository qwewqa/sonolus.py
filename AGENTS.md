# Sonolus.py

Compiles a restricted subset of Python into Sonolus engine node trees. Engine authors write archetypes and
callbacks against `sonolus.script`; this package traces that source, optimizes the resulting CFG, and emits the
engine data a Sonolus level ships. `test_projects/pydori` is a complete engine that doubles as the regression and
benchmark corpus.

Conventions below are derived from what the code already does. Where a rule and the code disagree, the code is
probably right and this file is stale: fix the file.

## Layers

| Path | Role | Published to the docs site |
| --- | --- | --- |
| `sonolus/script/` | Public API and the compiled subset | yes, as the API reference |
| `sonolus/script/internal/` | Compiler frontend: AST visitor, value protocol, builtin implementations | no |
| `sonolus/backend/` | IR, ops, memory blocks, and `interpret.py`, the reference interpreter | no |
| `sonolus/backend/_opt/` | Optimizer core, in Cython, compiled as C++ | no |
| `sonolus/backend/optimize/` | Python shim over `_opt`: three entry points and the level sentinels | no |
| `sonolus/build/` | Project build, CLI, dev server | no |
| `doc_stubs/*.pyi` | Published signatures for `builtins`, `math`, `random`, `typing`, and `Num` | yes |

Whether a file is published decides how its docstrings must read, so the boundary matters more than it looks:
`script/internal/` and `backend/_opt/` have no docs pages at all, and their docstrings can be as internal as they
need to be. Nothing in CI checks a `doc_stubs` signature against the implementation it shadows, so those drift
silently.

## Prose

Keep prose ASCII-only in docstrings, comments, `docs/`, `.agents/skills/`, and the markdown at the repo root.
Write `...` for an ellipsis, `->` and `<->` for arrows, and `section 2` rather than a section sign. Non-ASCII
punctuation is awkward to type and to search for, which makes later edits harder. Genuinely non-ASCII content
stays: `Bezier`'s accent, the `pi` in `vec.py`'s angle ranges, the localized strings in `metadata.py`'s examples.

Reach for ` -- ` last, and never for an em dash. A dash introducing an explanation wants a colon; a dash wrapping
an aside wants parentheses or commas. Two sentences are often better than either.

`E501` is ignored, so nothing fails on a long line, and `ruff format` wraps code but never reflows a comment or a
docstring. Keep those within 120 columns by hand.

## Code style

Ruff enforces the mechanical layer (import order, Google docstring convention, a broad lint set). These are the
judgement calls it cannot make:

- Absolute `from sonolus....` imports everywhere, never relative. Break a cycle with a function-local import or an
  import block at the bottom of the module: `E402` and `PLC0415` are ignored for exactly this.
- PEP 695 generics only: `class Foo[T]`, `def f[T]`, `type Alias = ...`. No `TypeVar(...)` assignments, no
  `typing.Generic[...]`.
- An abstract method gets `@abstractmethod`, a docstring, and a `raise NotImplementedError` body, not `...`. The
  exception is a pure interface never called through the base, such as `ArrayLike`'s `__getitem__`.
- Suffix a parameter with `_` to avoid shadowing a builtin (`type_`, `id_`) rather than adding `# noqa: A002`.
  Where the unsuffixed spelling is part of a public or mirrored API the `noqa` is correct instead: `printing.py`'s
  `format`, the `globals`/`locals` of `introspection.py` and of `simulation_context.py`'s `hooked_import`, and the
  file-level suppression in `options.py`.

## Checks

After changing Python, run `ruff format` then `ruff check --fix` on the touched files. Hand-fix whatever they
leave, then rerun both until they are clean. Keep automatic fixes out of unrelated files and preserve existing
uncommitted work.

For development changes, run tests covering the affected behavior. Changes on a compiled hot path also need the
metrics gate described in writing-tests; docstring or `docs/` changes need the strict docs build. Instruction-only
edits need factual, link, and skill validation, without rebuilding the extension or running the application suite.
Broaden checks when failures, remaining uncertainty, or the change's reach warrant it. Once the relevant checks
pass, repeat them only after further changes or new evidence.

For release validation, an explicitly requested full validation, or changes broad enough to require it, use the
full gate:

```
pytest -n 32
ruff check
ruff format --check
zensical build --strict
```

`zensical build --strict` catches broken cross-references and, because `validation.anchors: warn` is set, broken
heading anchors too. Heading text is load-bearing: reference pages deep-link into `concepts/types.md`.

CI runs `uv run tox`, which is the suite against Python 3.12, 3.13, 3.14, and 3.15. `ty` is configured in
`pyproject.toml` but nothing runs it, and there is no pre-commit config despite the dev dependency.

Editing a `.pyx`, `.pxd`, or `.h` needs no special rebuild flag: `[tool.uv] cache-keys` covers those inputs along
with `setup.py`, `pyproject.toml`, and `SONOLUS_OPT_DEBUG_BUILD`, so a plain `uv sync` recompiles the extension in
place in about ten seconds. `uv sync --reinstall-package sonolus-py` forces the rebuild if you suspect the loaded
extension no longer matches the source, and comparing the `.pyd` mtime against the source edit is the cheap way to
confirm a rebuild actually happened before trusting a result that depends on it.

To test a modified tree without rebuilding at all, work in a `git worktree` and reuse the extension you already
have: `uv sync --no-install-project --no-group docs` there, copy the `*.pyd` across from the main checkout, and
run from the worktree root so the source shadows the installed package. The copy is what makes this work.
`PYTHONPATH` cannot substitute for it: the main venv installs the project through a setuptools meta-path editable
finder, which resolves ahead of `PYTHONPATH` and would keep importing the main checkout.

## Running agents in parallel

- Preserve existing work in the shared tree. Do not use checkout, restore, or reset to discard changes for a
  counterfactual test; use an isolated worktree or copy containing the intended test state. Give agents explicit
  file ownership when they edit. Record `git status --short` and `git diff HEAD --numstat` before parallel work,
  re-check afterward, and inspect unexpected changes rather than relying on counts alone.
- Do not edit a `.pyx`, `.pxd`, or `.h` while agents are running tests. Those are `cache-keys` inputs, so the edit
  triggers a rebuild underneath them and their results stop meaning anything.

## Skills

Convention detail lives in skills rather than here, so each one loads only for the task it governs:

- **docstrings-and-comments**: writing a docstring, a prose comment, or a `docs/` page.
- **reviewing-prose**: judging prose that already exists, including trimming comments that do not earn their place.
- **compiled-subset**: anything the compiler traces or evaluates, including `@meta_fn` and the value protocol.
- **writing-tests**: adding or changing a test, or regenerating a golden.
- **optimizer-core**: working in `sonolus/backend/_opt`.
- **runtime-semantics**: what the real Sonolus runtime does, including its 32-bit floats and per-op semantics.

Rules live once. `compiled-subset` and `writing-tests` carry their own review checklists inline.
