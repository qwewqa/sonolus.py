---
name: writing-tests
description: Read before adding or changing a test in tests/, or when a golden file in tests/regressions/data needs regenerating. Covers why run_and_validate is the oracle and when run_compiled is the right tool instead, how to express an expectation without deriving it from the implementation, how to force a genuinely runtime value that the optimizer will not constant-fold, the PYTEST_DONT_REWRITE requirement for modules that compile assert statements, the delete-and-rerun recipe for regenerating goldens (never hand-edit one), and the metrics gate that hard-fails on a node-count regression.
---

# Tests

`pytest -n 32` for the whole suite, which spans four directories:

- `tests/script/`: the compiled subset and the frontend. Uses the oracle helpers below.
- `tests/backend/`: the IR, the optimizer core, and `interpret.py`. Asserts on semantics directly, no goldens.
- `tests/regressions/`: golden comparison over `test_projects/pydori`, plus the metrics gate.
- `tests/build_pipeline/`: CLI, collection, dev server, project assembly.

## run_and_validate is the oracle

`run_and_validate(fn, *args)` from `tests/script/conftest.py` runs `fn` **both** as plain Python and compiled, and
asserts they agree. Specifically it:

- runs `fn` as plain Python, capturing its result and every `debug_log` entry in order;
- traces each closure variant at `RuntimeChecks.NONE` to prove it compiles there, then at
  `RuntimeChecks.TERMINATE`; the variants read the closure normally or rewrite its cells into ROM;
- reuses each enabled-checks CFG across optimization levels (`MINIMAL_PASSES`, `FAST_PASSES`, `STANDARD_PASSES`),
  optimizing, emitting, and interpreting it at each level;
- asserts the compiled result equals the Python result **and** that the interpreter's log matches the Python log
  entry for entry;
- if the Python run raised, asserts the compiled run raises the same exception type and message, or terminates.

The expected result and log come from the independent Python execution. Use this helper whenever plain Python is
a valid reference. The CI reduction in optimization levels is described under Hypothesis below.

Use `run_compiled` only when the two deliberately disagree (pinning an accepted divergence) or when the snippet
cannot run as plain Python. Say which in the test. `run_compiled` still cross-checks the three optimization levels
and, unless you pin one, all three `RuntimeChecks` values, and raises if their results or logs differ.

**A test asserting a value derived from the implementation rather than from Python's behaviour is worse than no
test.** If you cannot express the expectation independently, that is a signal about the design.

## Forcing a runtime value

A literal may be constant-folded instead of exercising the runtime path. Make the input opaque to the optimizer:

- Accumulating in a loop can keep a value runtime-dependent, but a loop alone does not guarantee opacity. Check
  that the optimized CFG retains the operation being tested when relying on this approach.
- Route it through a black-box helper. `tests/script/test_flow.py` defines `black_box()` as
  `random.randrange(0, 1) == 0`, which always holds but is opaque to the optimizer, plus `black_box_value` and
  `black_box_log` built on it. `tests/script/test_dict.py` defines `bb(*x)`, a `meta_fn` that adds
  `floor(random())` to a `Num` (a no-op at runtime, opaque at compile time) and recurses into tuples, so it can
  make a key of any shape runtime-valued.

## Errors

`pytest.raises(CompilationError, match=...)` with enough of the message to pin *which* guard fired, not just that
something failed.

The check is concrete: grep the pattern against the messages the code can raise. If two guards both match it, it
is too loose, however specific it looks. `match="conflicting definitions"` reads precise and is not, because both
"Variable 'x' may have conflicting definitions between loop iterations" and "Binding 'x' has multiple conflicting
definitions or may not be guaranteed to be defined" satisfy it, so a test meaning the first passes on the second.
Include the part that differs, and the variable name where one appears. When in doubt about which message a test
actually gets, run it with the exception printed rather than guessing from the source.

## Compiling assert statements

Add `PYTEST_DONT_REWRITE` to the module docstring of any test module that compiles Python `assert` statements, so
pytest's assertion rewriting does not reshape the AST the visitor is meant to see. `tests/script/conftest.py`,
`test_assert.py`, `test_builtins.py`, and `test_flow.py` all do this.

## Goldens

`tests/regressions/test_project.py` compiles every callback of every `pydori` archetype in every mode, at the fast
and standard levels, in dev and non-dev, and compares the CFG text and the emitted node tree against files in
`tests/regressions/data/` through `compare_with_reference`. That is around 1500 files. Minimal is excluded because
some callbacks run out of temporary memory there.

`compare_with_reference` asserts when the file exists and **writes it when it does not**. So the regeneration
recipe is:

1. Delete the affected files under `tests/regressions/data/` (one name, or a glob for a batch).
2. Re-run `pytest tests/regressions/test_project.py`.
3. Inspect `git diff` on the regenerated files and confirm every change is one you intended.

Never hand-edit a golden. Names are built from
`{project}_{mode}_{archetype}_{callback}[_{level}][_dev]_{cfg|optimized_cfg|nodes}`, so a targeted glob is usually
enough.

## The metrics gate

`tests/regressions/test_metrics_gate.py` recomputes the `pydori` `standard`-level metrics with the current
optimizer and hard-fails on a regression against the committed baseline:

- aggregate `effective_node_count` must be at or below the baseline aggregate;
- per callback, within `1.02x` of baseline **or** within `+8` nodes absolute;
- `sum(per_op_counts) == function_node_count` for every callback.

A callback that fails both per-callback tolerances must be listed in `EXCEPTIONS` with a rationale. The dict starts
empty, and fixing the regression upstream is strongly preferred to adding an entry. Run this test whenever you
touch anything on a compiled hot path. The runtime cost model behind `effective_node_count` is in the
runtime-semantics skill.

The baseline is `baseline_metrics.json`, and its `meta` records the version and revision it was captured at.
Re-capture it with `uv run python tools/metrics.py --full-baseline --repeat 3 --output <path>` after a change that
legitimately moves the numbers, so the gate keeps measuring against something current: a baseline left far above
the real aggregate passes sizable regressions in silence.

One blind spot is structural rather than stale. The gate measures `standard` in a non-dev build, so code reachable
only under runtime checks is invisible to it. Use the `*_dev_*` regression goldens for that.

## Hypothesis

Profiles are registered in `tests/script/conftest.py`: 100 examples locally, 40 under `CI=true`, ten-second
deadline in both. Under CI on a Python older than the primary version the oracle also drops to `STANDARD_PASSES`
only, so a level-specific failure can reproduce locally while passing on those legs.

## Ruff

When ruff fights a test's intent, silence it with a targeted `# noqa`, or a file-level `# ruff: noqa: ...` where a
whole module needs it, rather than rewriting the test.

## Reviewing tests

The same rules read backwards, as things to check in a diff:

- An expected value that could only have come from running the implementation. This is the important one: such a
  test passes forever and pins nothing.
- `run_compiled` where `run_and_validate` would work, or `run_compiled` without a stated reason.
- A literal standing in for a value the test means to exercise at runtime. It will be folded, and the test will
  pass without touching the path it names.
- `pytest.raises(CompilationError)` with no `match=`, or a `match=` that more than one of the messages the code
  can raise would satisfy. Check it against the messages, not against how specific the string looks.
- A module that compiles `assert` statements without `PYTEST_DONT_REWRITE` in its docstring.
- A changed file under `tests/regressions/data/` with no code change that explains it, or a golden diff larger than
  the code change would produce. Goldens are regenerated, never edited.
- A change on a compiled hot path with no mention of the metrics gate.
- A `run_compiled` test where ordering is part of what the change affects but no `log_callback` is passed.
  `run_and_validate` checks the log for you; `run_compiled` only surfaces it if you ask.
