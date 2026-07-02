"""M4 metrics gate (OPTIMIZER_REWRITE.md §10 / §11-M4).

Recomputes the pydori ``standard``-level node metrics with the *current* optimizer via
``tools/metrics.py``'s library API and gates them against the committed M0 baseline
(``baseline_v0.16_metrics.json``, captured with the OLD optimizer at v0.16.0):

* aggregate ``effective_node_count`` must be <= the baseline aggregate -- the hard M4
  gate. ``effective`` counts each maximal runtime-constant subtree as 1 (models the
  runtime's own constant folding, §2), so deliberate duplication of runtime-constant
  trees (which raises *raw* counts by design) does not read as a regression here.
* per callback, current ``effective`` must be within ``1.02x`` of baseline OR within
  ``+8`` nodes absolute (a small-callback noise floor). Any callback that fails both is
  a regression and must be listed in ``EXCEPTIONS`` with a rationale -- the dict starts
  empty; nothing should fall out at M3.5/M3.6.
* the per-op sanity invariant ``sum(per_op_counts) == function_node_count`` holds for
  every callback (keeps the counting tool honest).

Counts are deterministic across repeats (``measure_callback`` asserts this), so the
baseline's ``repeat=3`` numbers and this test's ``repeat=1`` numbers are directly
comparable. One full ``standard`` metrics pass is a full trace+optimize+emit of every
pydori callback (a few seconds); left unmarked per the plan.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BASELINE_PATH = Path(__file__).parent / "data" / "baseline_v0.16_metrics.json"

# Per-callback tolerance: effective may grow to 1.02x baseline, OR by up to +8 nodes
# absolute (whichever is looser), before it counts as a regression.
_REL_TOLERANCE = 1.02
_ABS_TOLERANCE = 8

# Callbacks allowed to exceed the per-callback tolerance, each with a rationale. Start
# empty: M3.5/M3.6 cleared M3's lone ~1.014x callback, and the worst current per-callback
# effective delta is +1 node (ratio <= 1.007), inside the noise floor. Keep empty unless a
# real, justified regression appears -- and prefer fixing it upstream over listing it here.
EXCEPTIONS: dict[str, str] = {}

_LEVEL = "standard"


def _load_metrics_module():
    """Load tools/metrics.py as a standalone module (tools/ is not a package)."""
    spec = importlib.util.spec_from_file_location("_optimizer_metrics_tool", _REPO_ROOT / "tools" / "metrics.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def baseline_metrics() -> dict:
    with _BASELINE_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    return data["levels"][_LEVEL]


@pytest.fixture(scope="module")
def current_metrics() -> dict:
    # sys.recursionlimit: the metrics tool raises it at import; the frontend trace needs it.
    old_limit = sys.getrecursionlimit()
    metrics = _load_metrics_module()
    try:
        data = metrics.run_metrics("pydori", [_LEVEL], repeat=1, limit=None)
    finally:
        sys.setrecursionlimit(old_limit)
    return data["levels"][_LEVEL]


def test_aggregate_effective_not_worse_than_baseline(baseline_metrics, current_metrics):
    """Hard M4 gate: aggregate standard effective node count <= baseline aggregate."""
    baseline_total = baseline_metrics["totals"]["effective_node_count"]
    current_total = current_metrics["totals"]["effective_node_count"]
    assert current_total <= baseline_total, (
        f"aggregate effective_node_count regressed: baseline={baseline_total} current={current_total} "
        f"(delta {current_total - baseline_total:+})"
    )


def test_per_callback_effective_within_tolerance(baseline_metrics, current_metrics):
    """Every callback stays within 1.02x OR +8 nodes of baseline (else must be an EXCEPTION)."""
    baseline_cbs = baseline_metrics["callbacks"]
    current_cbs = current_metrics["callbacks"]

    # The callback set must match exactly -- a missing/extra callback is itself a regression
    # signal (renamed callback, dropped archetype, enumeration drift).
    assert set(current_cbs) == set(baseline_cbs), (
        f"callback set drifted from baseline: "
        f"only in baseline={sorted(set(baseline_cbs) - set(current_cbs))} "
        f"only in current={sorted(set(current_cbs) - set(baseline_cbs))}"
    )

    regressions: list[str] = []
    for key in sorted(baseline_cbs):
        base_eff = baseline_cbs[key]["effective_node_count"]
        cur_eff = current_cbs[key]["effective_node_count"]
        within_rel = cur_eff <= math.floor(base_eff * _REL_TOLERANCE)
        within_abs = (cur_eff - base_eff) <= _ABS_TOLERANCE
        if within_rel or within_abs:
            continue
        ratio = cur_eff / base_eff if base_eff else float("inf")
        message = (
            f"{key}: effective {base_eff} -> {cur_eff} (delta {cur_eff - base_eff:+}, ratio {ratio:.4f}) "
            f"exceeds {_REL_TOLERANCE:g}x and +{_ABS_TOLERANCE}"
        )
        if key in EXCEPTIONS:
            continue
        regressions.append(message)

    assert not regressions, "per-callback effective regressions not covered by EXCEPTIONS:\n  " + "\n  ".join(regressions)

    # Guard against stale EXCEPTIONS: an entry that no longer regresses should be removed.
    stale = []
    for key in EXCEPTIONS:
        if key not in baseline_cbs:
            stale.append(f"{key} (not in baseline)")
            continue
        base_eff = baseline_cbs[key]["effective_node_count"]
        cur_eff = current_cbs[key]["effective_node_count"]
        if cur_eff <= math.floor(base_eff * _REL_TOLERANCE) or (cur_eff - base_eff) <= _ABS_TOLERANCE:
            stale.append(f"{key} (now within tolerance: {base_eff} -> {cur_eff})")
    assert not stale, "EXCEPTIONS entries no longer needed; remove them:\n  " + "\n  ".join(stale)


def test_per_op_counts_sum_to_function_node_count(current_metrics):
    """Tool-honesty invariant: per-op counts partition the function nodes exactly."""
    mismatches: list[str] = []
    for key, result in current_metrics["callbacks"].items():
        per_op_total = sum(result["per_op_counts"].values())
        fn_count = result["function_node_count"]
        if per_op_total != fn_count:
            mismatches.append(f"{key}: sum(per_op)={per_op_total} != function_node_count={fn_count}")
    assert not mismatches, "per-op totals do not match function node counts:\n  " + "\n  ".join(mismatches)
