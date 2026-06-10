"""Root test conftest: corpus-capture instrumentation (PORT.md task T0.5).

Everything here is gated on the ``SONOLUS_CAPTURE_CORPUS`` environment variable;
when it is unset (the default) this module defines nothing and test runs are
byte-identical in behavior to a tree without it.

When set, ``sonolus.build.compile.callback_to_cfg`` is wrapped (runtime
monkeypatching from test infrastructure only -- the frozen Python backend source is
untouched) so that every frontend CFG compiled anywhere in the suite (tests/script
via its conftest helpers, tests/regressions pydori builds across all modes,
tests/build engine/dev-server builds, sonolus.script.debug) is encoded and stored in
the capture directory. This conftest is loaded before any per-directory conftest or
test module, so their ``from sonolus.build.compile import callback_to_cfg`` imports
bind the wrapper. Behavioral I/O vectors are captured separately by
``tests/script/conftest.py``, which is where the legacy interpreter actually runs
compiled callbacks with known inputs and observable outputs.

The runtest hooks record the current test id and whether it is hypothesis-driven
(``tools/gen_corpus.py`` excludes hypothesis-derived cases when curating the
checked-in mini-corpus).
"""

import os

if os.environ.get("SONOLUS_CAPTURE_CORPUS") and os.environ.get("SONOLUS_BACKEND") == "rust":
    # Corpus capture instruments the legacy Python backend pipeline, which the
    # rust lane (PORT.md T1.4) bypasses entirely; the combination is meaningless.
    raise RuntimeError("SONOLUS_CAPTURE_CORPUS and SONOLUS_BACKEND=rust are mutually exclusive")

if os.environ.get("SONOLUS_CAPTURE_CORPUS"):
    import functools

    import sonolus.build.compile as _compile_module
    from tests.corpus_capture import get_capture

    _capture = get_capture()
    _original_callback_to_cfg = _compile_module.callback_to_cfg

    @functools.wraps(_original_callback_to_cfg)
    def _capturing_callback_to_cfg(project_state, mode_state, callback, name, archetype=None):
        cfg = _original_callback_to_cfg(project_state, mode_state, callback, name, archetype)
        _capture.store_cfg(
            cfg,
            callback_name=name,
            mode=mode_state.mode.name.lower(),
            archetype=archetype.__name__ if archetype is not None else None,
        )
        return cfg

    _compile_module.callback_to_cfg = _capturing_callback_to_cfg

    def _is_hypothesis_item(item) -> bool:
        if item.get_closest_marker("hypothesis") is not None:
            return True
        return hasattr(getattr(item, "obj", None), "hypothesis")

    def pytest_runtest_setup(item):
        _capture.set_current_test(item.nodeid, _is_hypothesis_item(item))

    def pytest_runtest_teardown(item):
        _capture.clear_current_test()
