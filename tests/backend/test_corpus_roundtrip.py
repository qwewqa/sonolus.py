"""Marshal round-trip over the whole pydori callback corpus.

For every callback CFG (all modes, dev + non-dev), both raw (frontend output)
and optimized (``STANDARD_PASSES`` output) forms must:

* round-trip faithfully modulo the exporter's temp renumbering (``canon_text``),
  and
* be export/import idempotent byte-for-byte.

Mirrors tests/regressions/test_project.py's enumeration.
"""

from __future__ import annotations

import pytest

from sonolus.backend.optimize import STANDARD_PASSES, OptimizerConfig, cfg_to_engine_node, run_passes
from sonolus.backend.optimize.flow import cfg_to_text
from tests.backend._corpus import MODE_SETUP, iter_callbacks
from tests.backend._roundtrip_helpers import canon_text, roundtrip


def _check_callback(label, callback_name, factory, mode):
    cfg = factory()

    # Raw form: faithful (binary already) + idempotent.
    raw_rt = roundtrip(cfg, mode, callback_name)
    assert canon_text(raw_rt) == canon_text(cfg), f"{label}: raw not faithful"
    raw_rt2 = roundtrip(raw_rt, mode, callback_name)
    assert cfg_to_text(raw_rt) == cfg_to_text(raw_rt2), f"{label}: raw not idempotent"

    # Optimized form: the pipeline output (allocated, non-destructive on cfg) must
    # be round-trip idempotent. STANDARD output contains n-ary associative ops,
    # which marshal-in binarizes -- so ``opt`` itself is NOT text-faithful under
    # round-trip (see _roundtrip_helpers.assert_faithful's documented rule). The
    # meaningful faithfulness invariant is that emission agrees across the
    # binarization (re-flatten makes the fused and export->reimport paths converge
    # byte-for-byte).
    opt = run_passes(cfg, STANDARD_PASSES, OptimizerConfig(mode=mode, callback=callback_name))
    opt_rt = roundtrip(opt, mode, callback_name)
    opt_rt2 = roundtrip(opt_rt, mode, callback_name)
    assert cfg_to_text(opt_rt) == cfg_to_text(opt_rt2), f"{label}: optimized not idempotent"
    assert cfg_to_engine_node(opt) == cfg_to_engine_node(opt_rt), f"{label}: emit not stable across round-trip"


@pytest.mark.parametrize("mode", list(MODE_SETUP))
def test_corpus_roundtrip(mode):
    count = 0
    for label, callback_name, factory in iter_callbacks(mode):
        _check_callback(label, callback_name, factory, mode)
        count += 1
    assert count > 0, f"no callbacks enumerated for {mode}"
