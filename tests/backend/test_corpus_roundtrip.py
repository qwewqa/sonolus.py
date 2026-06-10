"""Round-trip and integrity tests over the checked-in mini-corpus (T0.5).

For every corpus CFG, the Rust decoder's canonical dump of the encoded bytes must be
byte-identical to the Python-side canonical dump stored at capture time (see
``rust/testdata/README.md`` for why the dump is stored rather than recomputed).
Skipped when the ``sonolus_backend`` extension is not installed.
"""

import hashlib
import json
import math
import struct
from pathlib import Path

import pytest

sonolus_backend = pytest.importorskip("sonolus_backend")

TESTDATA = Path(__file__).resolve().parents[2] / "rust" / "testdata"
MANIFEST = json.loads((TESTDATA / "manifest.json").read_bytes().decode("utf-8"))
ENTRIES = MANIFEST["entries"]


def entry_id(entry: dict) -> str:
    return entry["hash"][:12]


def test_manifest_schema_and_totals():
    assert MANIFEST["schema"] == 1
    assert MANIFEST["encoding_version"] == 1
    assert MANIFEST["count"] == len(ENTRIES) > 0
    assert MANIFEST["cfg_bytes"] == sum(e["cfg_size"] for e in ENTRIES)
    assert MANIFEST["dump_bytes"] == sum(e["dump_size"] for e in ENTRIES)
    assert MANIFEST["vector_bytes"] == sum(e["vector_size"] for e in ENTRIES)
    assert MANIFEST["vector_total"] == sum(e["vectors"] for e in ENTRIES)
    assert MANIFEST["total_bytes"] == MANIFEST["cfg_bytes"] + MANIFEST["dump_bytes"] + MANIFEST["vector_bytes"]
    assert MANIFEST["total_bytes"] <= 5_000_000


def test_manifest_matches_directory_contents():
    hashes = [e["hash"] for e in ENTRIES]
    assert len(set(hashes)) == len(hashes)
    assert sorted(p.name for p in (TESTDATA / "cfgs").iterdir()) == sorted(f"{h}.scfg" for h in hashes)
    assert sorted(p.name for p in (TESTDATA / "dumps").iterdir()) == sorted(f"{h}.txt" for h in hashes)
    assert sorted(p.name for p in (TESTDATA / "vectors").iterdir()) == sorted(
        f"{e['hash']}.json" for e in ENTRIES if e["vectors"]
    )


@pytest.mark.parametrize("entry", ENTRIES, ids=entry_id)
def test_corpus_cfg_roundtrips(entry: dict):
    data = (TESTDATA / "cfgs" / f"{entry['hash']}.scfg").read_bytes()
    assert len(data) == entry["cfg_size"]
    assert hashlib.sha256(data).hexdigest() == entry["hash"]
    expected_dump = (TESTDATA / "dumps" / f"{entry['hash']}.txt").read_bytes()
    assert len(expected_dump) == entry["dump_size"]
    # The stored Python-side canonical dump must match the Rust decoder's dump of
    # the encoded bytes, byte for byte (ENCODING.md §5).
    assert sonolus_backend.decode_cfg_canonical_dump(data) == expected_dump.decode("utf-8")


def assert_valid_value(value):
    if isinstance(value, str):
        # Non-finite values are stored as raw IEEE-754 bits.
        assert len(value) == 18
        assert value.startswith("0x")
        assert not math.isfinite(struct.unpack("<d", struct.pack("<Q", int(value, 16)))[0])
    else:
        assert isinstance(value, int | float)
        assert not isinstance(value, bool)
        assert math.isfinite(value)


@pytest.mark.parametrize("entry", [e for e in ENTRIES if e["vectors"]], ids=entry_id)
def test_corpus_vectors_match_schema(entry: dict):
    data = (TESTDATA / "vectors" / f"{entry['hash']}.json").read_bytes()
    assert len(data) == entry["vector_size"]
    payload = json.loads(data.decode("utf-8"))
    assert payload["schema"] == 1
    assert payload["cfg"] == entry["hash"]
    vectors = payload["vectors"]
    assert len(vectors) == entry["vectors"] > 0
    for vector in vectors:
        assert vector["level"] in {"minimal", "fast", "standard", "custom"}
        assert vector["runtime_checks"] in {"none", "terminate", "notify_and_terminate"}
        assert isinstance(vector["temp_memory_block"], int)
        for block, values in vector["inputs"]:
            assert isinstance(block, int)
            for value in values:
                assert_valid_value(value)
        for kind, lo, hi, value in vector["rng"]:
            assert kind in {"uniform", "randrange"}
            assert_valid_value(lo)
            assert_valid_value(hi)
            assert_valid_value(value)
        assert_valid_value(vector["result"])
        for value in vector["log"]:
            assert_valid_value(value)
        for block, index, value in vector["writes"]:
            assert isinstance(block, int)
            assert isinstance(index, int)
            assert_valid_value(value)


def test_perturbed_cfg_is_rejected_or_detected():
    """Python-side counterpart of the Rust negative test, on one corpus entry."""
    entry = min(ENTRIES, key=lambda e: (e["cfg_size"], e["hash"]))
    data = (TESTDATA / "cfgs" / f"{entry['hash']}.scfg").read_bytes()
    expected_dump = (TESTDATA / "dumps" / f"{entry['hash']}.txt").read_bytes().decode("utf-8")
    undetected = []
    for i in range(len(data)):
        corrupted = bytearray(data)
        corrupted[i] ^= 0xFF
        try:
            dump = sonolus_backend.decode_cfg_canonical_dump(bytes(corrupted))
        except ValueError:
            continue
        if dump == expected_dump:
            undetected.append(i)
    assert not undetected
