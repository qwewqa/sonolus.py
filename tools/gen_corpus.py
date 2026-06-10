"""Curates the checked-in Rust-backend mini-corpus from a corpus capture directory.

Usage:
    uv run python tools/gen_corpus.py <capture-dir> [--out rust/testdata]
        [--budget 5000000] [--no-verify] [--stats-only]

The capture directory is produced by a pytest run with ``SONOLUS_CAPTURE_CORPUS``
set (see ``tests/corpus_capture.py``). This tool:

1. aggregates the per-worker event logs (provenance, hypothesis flags, encode
   rejects),
2. verifies that every captured CFG — frontend and post-pass — round-trips
   bit-exactly through the Rust decoder
   (``sonolus_backend.decode_cfg_canonical_dump(bytes)`` must equal the
   Python-side canonical dump stored at capture time) unless ``--no-verify``,
3. deterministically selects a diverse subset (loops, switches, dynamic indexing,
   float conds, deep nesting, RNG use, every mode/callback pair seen, the biggest
   real callbacks, plus a hash-ordered fill) within the byte budget, excluding
   every CFG whose only provenance is hypothesis-driven tests, and
4. writes the mini-corpus (CFG bytes, canonical dumps, I/O vectors, the post-pass
   CFGs referenced by the selected vectors, manifest) to ``rust/testdata/``.

Post-pass CFG bytes (``post_cfgs/``) are included for the Rust emitter's corpus
replay (PORT.md T1.2); their canonical dumps are *not* shipped (round-trip
verification already happened against the capture directory).

The output is a pure function of the capture directory contents: running the tool
twice on the same capture produces byte-identical output.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sonolus.backend.ops import Op

MANIFEST_SCHEMA_VERSION = 2
VECTOR_SCHEMA_VERSION = 2
MAX_VECTORS_PER_CFG = 4
MAX_TOTAL_ENTRIES = 500
MAX_MANIFEST_SOURCES = 3

_OP_IDS = {op.name: i for i, op in enumerate(Op)}
_RNG_OP_IDS = {_OP_IDS["Random"], _OP_IDS["RandomInteger"]}
_NODE_OP_RE = re.compile(r"\((?:pure|instr) (\d+)")


@dataclass
class Entry:
    digest: str
    cfg_size: int
    dump_size: int
    features: dict
    sources: list[str] = field(default_factory=list)  # non-hypothesis test ids, sorted
    callbacks: list[str] = field(default_factory=list)  # "mode/callback" pairs, sorted
    modes: list[str] = field(default_factory=list)
    hypothesis_only: bool = True
    vector_hashes: list[str] = field(default_factory=list)
    vector_blob: bytes = b""
    post_cfgs: list[str] = field(default_factory=list)  # post-pass CFG digests, sorted
    post_cfg_size: int = 0  # total size of this entry's post-pass CFG files

    @property
    def cost(self) -> int:
        # post_cfg_size may double-count a post-pass CFG shared between entries
        # (conservative for budget purposes; manifest totals use the union).
        return self.cfg_size + self.dump_size + len(self.vector_blob) + self.post_cfg_size


def load_events(capture_dir: Path) -> list[dict]:
    events = []
    for path in sorted((capture_dir / "events").glob("*.jsonl")):
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
    return events


def features_from_dump(dump: str) -> dict:
    """Extracts selection features from a canonical dump (see rust/ENCODING.md §5)."""
    lines = dump.split("\n")
    n_blocks = 0
    n_stmts = 0
    max_edges = 0
    max_depth = 0
    ops: set[int] = set()
    has_loop = False
    has_float_cond = False
    has_dynamic_index = False
    has_nested_block = False
    current_block = -1
    edges_in_block = 0
    in_blocks = False
    for line in lines:
        if not in_blocks:
            # Header region (strings/temps tables); its lines are indented, the
            # "blocks N" separator is not.
            if line.startswith("blocks "):
                in_blocks = True
            continue
        if line.startswith("block "):
            max_edges = max(max_edges, edges_in_block)
            edges_in_block = 0
            current_block = int(line[6:])
            n_blocks += 1
        elif line.startswith("    edge "):
            edges_in_block += 1
            body = line[9:]
            cond, _, target = body.partition(" -> ")
            if cond.startswith("f:"):
                has_float_cond = True
            if int(target) <= current_block:
                has_loop = True
        elif line.startswith(("    (", "  test ")):
            if line.startswith("    ("):
                n_stmts += 1
            ops.update(int(m) for m in _NODE_OP_RE.findall(line))
            if " i=(place" in line:
                has_dynamic_index = True
            if "b=(place" in line:
                has_nested_block = True
            depth = 0
            for ch in line:
                if ch == "(":
                    depth += 1
                    max_depth = max(max_depth, depth)
                elif ch == ")":
                    depth -= 1
    max_edges = max(max_edges, edges_in_block)
    return {
        "blocks": n_blocks,
        "stmts": n_stmts,
        "max_edges": max_edges,
        "depth": max_depth,
        "unique_ops": len(ops),
        "loop": has_loop,
        "switch": max_edges >= 3,
        "dynamic_index": has_dynamic_index,
        "nested_block": has_nested_block,
        "float_cond": has_float_cond,
        "rng": bool(ops & _RNG_OP_IDS),
    }


def build_entries(capture_dir: Path, events: list[dict]) -> tuple[dict[str, Entry], int]:
    """Returns (entries by digest, count of unattributed CFG files)."""
    cfg_events: dict[str, list[dict]] = {}
    vector_events: dict[str, set[str]] = {}
    for event in events:
        if event["type"] == "cfg":
            cfg_events.setdefault(event["hash"], []).append(event)
        elif event["type"] == "vector" and not event["hypothesis"]:
            vector_events.setdefault(event["cfg"], set()).add(event["vector"])

    entries: dict[str, Entry] = {}
    unattributed = 0
    for cfg_path in sorted((capture_dir / "cfgs").glob("*.scfg")):
        digest = cfg_path.stem
        dump_path = capture_dir / "dumps" / f"{digest}.txt"
        entry = Entry(
            digest=digest,
            cfg_size=cfg_path.stat().st_size,
            dump_size=dump_path.stat().st_size,
            features=features_from_dump(dump_path.read_bytes().decode("utf-8")),
        )
        evs = cfg_events.get(digest, [])
        if not evs:
            unattributed += 1
            continue
        entry.hypothesis_only = all(e["hypothesis"] for e in evs)
        entry.sources = sorted({e["test"] for e in evs if not e["hypothesis"]})
        pairs = {(e["mode"], e["callback"]) for e in evs if not e["hypothesis"]}
        entry.modes = sorted({mode for mode, _ in pairs})
        entry.callbacks = sorted(f"{mode}/{cb}" for mode, cb in pairs if cb)
        if not entry.hypothesis_only:
            entry.vector_hashes = sorted(vector_events.get(digest, set()))[:MAX_VECTORS_PER_CFG]
            entry.vector_blob, payloads = combine_vectors(capture_dir, digest, entry.vector_hashes)
            post_hashes = sorted(
                {payload["post_cfg"] for payload in payloads if payload.get("post_cfg") is not None}
            )
            entry.post_cfgs = post_hashes
            for post_hash in post_hashes:
                post_path = capture_dir / "post_cfgs" / f"{post_hash}.scfg"
                if not post_path.is_file():
                    sys.exit(f"error: vector for {digest} references missing post-pass CFG {post_hash}")
                entry.post_cfg_size += post_path.stat().st_size
        entries[digest] = entry
    return entries, unattributed


def combine_vectors(capture_dir: Path, digest: str, vector_hashes: list[str]) -> tuple[bytes, list[dict]]:
    """Returns the combined vector-file bytes and the parsed vector payloads."""
    if not vector_hashes:
        return b"", []
    payloads = [
        (capture_dir / "vectors" / f"{digest}.{vector_hash}.json").read_bytes() for vector_hash in vector_hashes
    ]
    head = f'{{"schema":{VECTOR_SCHEMA_VERSION},"cfg":"{digest}","vectors":['.encode("ascii")
    return head + b",".join(payloads) + b"]}\n", [json.loads(p) for p in payloads]


def verify_capture(capture_dir: Path, digests: list[str], post_digests: list[str]) -> None:
    try:
        import sonolus_backend
    except ImportError:
        sys.exit(
            "error: the sonolus_backend extension is required for round-trip verification "
            "(run `uv run maturin develop -m rust/sonolus-backend-py/Cargo.toml`), "
            "or pass --no-verify"
        )
    failures = []

    def check(kind: str, cfg_dir: str, dump_dir: str, digest: str) -> None:
        data = (capture_dir / cfg_dir / f"{digest}.scfg").read_bytes()
        expected = (capture_dir / dump_dir / f"{digest}.txt").read_bytes().decode("utf-8")
        try:
            actual = sonolus_backend.decode_cfg_canonical_dump(data)
        except ValueError as e:
            failures.append(f"{kind} {digest}: decode failed: {e}")
            return
        if actual != expected:
            failures.append(f"{kind} {digest}: canonical dump mismatch (Python != Rust)")

    for digest in digests:
        check("cfg", "cfgs", "dumps", digest)
    for digest in post_digests:
        check("post-cfg", "post_cfgs", "post_dumps", digest)
    if failures:
        for failure in failures:
            print(f"ROUND-TRIP FAILURE: {failure}", file=sys.stderr)
        sys.exit(
            f"error: {len(failures)} of {len(digests) + len(post_digests)} captured CFGs "
            f"failed round-trip verification"
        )
    print(
        f"verified: all {len(digests)} captured CFGs and {len(post_digests)} post-pass CFGs "
        f"round-trip clean (Python dump == Rust dump)"
    )


def select_entries(entries: dict[str, Entry], budget: int) -> list[Entry]:
    """Deterministic diversity-first selection within the byte budget."""
    candidates = sorted(
        (e for e in entries.values() if not e.hypothesis_only),
        key=lambda e: e.digest,
    )
    selected: dict[str, Entry] = {}
    total = 0

    def add(entry: Entry) -> None:
        nonlocal total
        selected[entry.digest] = entry
        total += entry.cost

    def fill_bucket(pred, quota: int, *, entry_cap: int, limit: int, order: list[Entry] | None = None) -> None:
        count = sum(1 for e in selected.values() if pred(e))
        for entry in order if order is not None else candidates:
            if count >= quota:
                break
            if entry.digest in selected or not pred(entry):
                continue
            if entry.cost > entry_cap or total + entry.cost > limit:
                continue
            add(entry)
            count += 1

    # Stage 1: the biggest real callbacks (within a per-entry cap and a budget share).
    fill_bucket(
        lambda e: True,
        8,
        entry_cap=1_200_000,
        limit=int(budget * 0.40),
        order=sorted(candidates, key=lambda e: (-e.cfg_size, e.digest)),
    )

    # Stage 2a: every (mode, named-callback) pair observed gets representation, and
    # every mode a minimum share (real engine callbacks; these can be mid-sized).
    pair_cap = 200_000
    pair_limit = int(budget * 0.65)
    all_pairs = sorted({pair for e in candidates for pair in e.callbacks})
    for pair in all_pairs:
        fill_bucket(lambda e, p=pair: p in e.callbacks, 2, entry_cap=pair_cap, limit=pair_limit)
    for mode in ("play", "watch", "preview", "tutorial"):
        fill_bucket(lambda e, m=mode: m in e.modes, 12, entry_cap=pair_cap, limit=pair_limit)

    # Stage 2b: feature buckets (mostly small script-test CFGs; tight per-entry cap
    # so no single bucket can starve the others).
    feature_cap = 60_000
    feature_limit = int(budget * 0.90)
    fill_bucket(lambda e: e.features["loop"], 40, entry_cap=feature_cap, limit=feature_limit)
    fill_bucket(lambda e: e.features["switch"], 40, entry_cap=feature_cap, limit=feature_limit)
    fill_bucket(lambda e: e.features["dynamic_index"], 40, entry_cap=feature_cap, limit=feature_limit)
    fill_bucket(lambda e: e.features["nested_block"], 30, entry_cap=feature_cap, limit=feature_limit)
    fill_bucket(lambda e: e.features["float_cond"], 30, entry_cap=feature_cap, limit=feature_limit)
    fill_bucket(lambda e: e.features["rng"], 30, entry_cap=feature_cap, limit=feature_limit)
    # The most deeply nested expressions seen, whatever their absolute depth.
    deepest = sorted(candidates, key=lambda e: (-e.features["depth"], e.digest))
    fill_bucket(lambda e: True, len(selected) + 20, entry_cap=feature_cap, limit=feature_limit, order=deepest)
    fill_bucket(lambda e: bool(e.vector_blob), 60, entry_cap=feature_cap, limit=feature_limit)

    # Stage 3: hash-ordered fill to the budget (diversity already guaranteed above).
    for entry in candidates:
        if len(selected) >= MAX_TOTAL_ENTRIES:
            break
        if entry.digest in selected:
            continue
        if total + entry.cost <= budget:
            add(entry)

    return sorted(selected.values(), key=lambda e: e.digest)


def summarize_rejects(events: list[dict], event_type: str = "reject") -> list[dict]:
    grouped: dict[str, dict] = {}
    for event in events:
        if event["type"] != event_type:
            continue
        info = grouped.setdefault(event["error"], {"error": event["error"], "count": 0, "tests": set()})
        info["count"] += 1
        info["tests"].add(event["test"])
    return [
        {"error": error, "count": info["count"], "tests": sorted(info["tests"])[:MAX_MANIFEST_SOURCES]}
        for error, info in sorted(grouped.items())
    ]


def write_corpus(
    capture_dir: Path,
    out_dir: Path,
    selected: list[Entry],
    capture_stats: dict,
) -> None:
    for sub in ("cfgs", "dumps", "post_cfgs", "vectors"):
        target = out_dir / sub
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
    manifest_path = out_dir / "manifest.json"
    manifest_path.unlink(missing_ok=True)

    manifest_entries = []
    post_sizes: dict[str, int] = {}  # union of selected post-pass CFGs
    for entry in selected:
        cfg_bytes = (capture_dir / "cfgs" / f"{entry.digest}.scfg").read_bytes()
        dump_bytes = (capture_dir / "dumps" / f"{entry.digest}.txt").read_bytes()
        (out_dir / "cfgs" / f"{entry.digest}.scfg").write_bytes(cfg_bytes)
        (out_dir / "dumps" / f"{entry.digest}.txt").write_bytes(dump_bytes)
        if entry.vector_blob:
            (out_dir / "vectors" / f"{entry.digest}.json").write_bytes(entry.vector_blob)
        for post_hash in entry.post_cfgs:
            if post_hash not in post_sizes:
                post_bytes = (capture_dir / "post_cfgs" / f"{post_hash}.scfg").read_bytes()
                (out_dir / "post_cfgs" / f"{post_hash}.scfg").write_bytes(post_bytes)
                post_sizes[post_hash] = len(post_bytes)
        manifest_entries.append(
            {
                "hash": entry.digest,
                "cfg_size": len(cfg_bytes),
                "dump_size": len(dump_bytes),
                "vector_size": len(entry.vector_blob),
                "vectors": len(entry.vector_hashes),
                "post_cfgs": entry.post_cfgs,
                "post_cfg_size": entry.post_cfg_size,
                "sources": entry.sources[:MAX_MANIFEST_SOURCES],
                "callbacks": entry.callbacks[:MAX_MANIFEST_SOURCES],
                "features": entry.features,
            }
        )

    manifest = {
        "schema": MANIFEST_SCHEMA_VERSION,
        "encoding_version": 1,
        "generated_by": "tools/gen_corpus.py",
        "count": len(selected),
        "vector_total": sum(len(e.vector_hashes) for e in selected),
        "cfg_bytes": sum(e.cfg_size for e in selected),
        "dump_bytes": sum(e.dump_size for e in selected),
        "vector_bytes": sum(len(e.vector_blob) for e in selected),
        # The union of post-pass CFG files (per-entry post_cfg_size double-counts
        # files shared between entries).
        "post_cfg_count": len(post_sizes),
        "post_cfg_bytes": sum(post_sizes.values()),
        "total_bytes": sum(e.cfg_size + e.dump_size + len(e.vector_blob) for e in selected)
        + sum(post_sizes.values()),
        "capture_stats": capture_stats,
        "entries": manifest_entries,
    }
    data = json.dumps(manifest, ensure_ascii=True, allow_nan=False, indent=1).encode("utf-8") + b"\n"
    manifest_path.write_bytes(data)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("capture_dir", type=Path, help="capture directory (SONOLUS_CAPTURE_CORPUS output)")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "rust" / "testdata")
    # Data budget; leaves headroom for manifest.json + README.md so the whole
    # rust/testdata/ tree stays within the ~5MB target (enforced by the Rust test).
    parser.add_argument("--budget", type=int, default=4_900_000, help="total byte budget for the mini-corpus")
    parser.add_argument("--no-verify", action="store_true", help="skip Rust round-trip verification of the capture")
    parser.add_argument("--stats-only", action="store_true", help="print capture statistics without writing output")
    args = parser.parse_args()

    capture_dir = args.capture_dir
    if not (capture_dir / "cfgs").is_dir():
        sys.exit(f"error: {capture_dir} does not look like a capture directory (no cfgs/ subdirectory)")

    events = load_events(capture_dir)
    entries, unattributed = build_entries(capture_dir, events)
    all_digests = sorted(p.stem for p in (capture_dir / "cfgs").glob("*.scfg"))
    post_dir = capture_dir / "post_cfgs"
    all_post_digests = sorted(p.stem for p in post_dir.glob("*.scfg")) if post_dir.is_dir() else []
    rejects = summarize_rejects(events)
    post_rejects = summarize_rejects(events, "post_reject")
    hypothesis_only = sum(1 for e in entries.values() if e.hypothesis_only)

    print(f"capture: {len(all_digests)} unique CFGs, {sum(1 for e in events if e['type'] == 'cfg')} cfg events")
    print(f"  post-pass CFGs: {len(all_post_digests)}")
    print(f"  hypothesis-only CFGs (excluded from curation): {hypothesis_only}")
    if unattributed:
        print(f"  unattributed CFG files (no events; excluded): {unattributed}")
    for label, reject_list in (("encode rejects", rejects), ("post-pass encode rejects", post_rejects)):
        if reject_list:
            print(f"  {label} ({sum(r['count'] for r in reject_list)} events):")
            for reject in reject_list:
                print(f"    [{reject['count']}x] {reject['error']} (e.g. {', '.join(reject['tests'])})")
        else:
            print(f"  {label}: none")

    if not args.no_verify:
        verify_capture(capture_dir, all_digests, all_post_digests)

    selected = select_entries(entries, args.budget)
    total = sum(e.cost for e in selected)
    with_vectors = sum(1 for e in selected if e.vector_blob)
    selected_posts = {h for e in selected for h in e.post_cfgs}
    print(
        f"selected: {len(selected)} CFGs ({with_vectors} with I/O vectors, "
        f"{sum(len(e.vector_hashes) for e in selected)} vectors, "
        f"{len(selected_posts)} post-pass CFGs), {total} bytes (budget {args.budget})"
    )
    if args.stats_only:
        return

    capture_stats = {
        "unique_cfgs": len(all_digests),
        "post_cfgs": len(all_post_digests),
        "hypothesis_only_cfgs": hypothesis_only,
        "unattributed_cfgs": unattributed,
        "rejects": rejects,
        "post_rejects": post_rejects,
    }
    write_corpus(capture_dir, args.out, selected, capture_stats)
    print(f"wrote mini-corpus to {args.out}")


if __name__ == "__main__":
    main()
