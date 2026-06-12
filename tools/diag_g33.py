"""G3.3 diagnostic (orchestrator scratch tool, not part of any DoD).

Compares the optimized output node trees for a single pydori callback between
the frozen Python backend (STANDARD_PASSES) and the Rust pipeline (standard):
op histograms of the static tree, top differences, and tree sizes. Run:

    uv run python tools/diag_g33.py preview PreviewStage render
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "test_projects"))

from metrics import STANDARD_PASSES, compile_legacy, iter_pydori_callbacks  # noqa: E402

import sonolus_backend  # noqa: E402

from sonolus.backend.encode import encode_cfg  # noqa: E402

OP_TOKEN = re.compile(r"\b([A-Z][A-Za-z0-9]*)\(")


def histogram(formatted: str) -> Counter[str]:
    return Counter(OP_TOKEN.findall(formatted))


def main() -> None:
    mode_name, archetype_name, cb_name = sys.argv[1:4]
    label = f"{mode_name}/{archetype_name}.{cb_name}"
    spec = next(s for s in iter_pydori_callbacks() if s.label == label)

    legacy_nodes, legacy_stats, _ = compile_legacy(spec, STANDARD_PASSES)
    legacy_text = legacy_nodes.format()

    data = encode_cfg(spec.trace())
    rust_nodes, rust_stats = sonolus_backend.run_pipeline_stats(data, "standard")
    rust_text = rust_nodes.format()

    print(f"== {label} ==")
    print(f"legacy: static={legacy_stats['static_nodes']} dag={legacy_stats['dag_size']}")
    print(f"rust:   static={rust_stats['static_nodes']} dag={rust_stats['dag_size']}")

    legacy_hist = histogram(legacy_text)
    rust_hist = histogram(rust_text)
    all_ops = set(legacy_hist) | set(rust_hist)
    rows = sorted(all_ops, key=lambda op: abs(rust_hist[op] - legacy_hist[op]), reverse=True)
    print(f"{'op':<28}{'legacy':>10}{'rust':>10}{'delta':>10}")
    for op in rows[:25]:
        delta = rust_hist[op] - legacy_hist[op]
        print(f"{op:<28}{legacy_hist[op]:>10}{rust_hist[op]:>10}{delta:>+10}")
    print(f"{'TOTAL':<28}{sum(legacy_hist.values()):>10}{sum(rust_hist.values()):>10}")


if __name__ == "__main__":
    main()
