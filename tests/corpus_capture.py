"""Corpus capture for the Rust backend port (PORT.md task T0.5).

When the ``SONOLUS_CAPTURE_CORPUS`` environment variable is set to a directory, a
pytest run captures, for every frontend CFG compiled anywhere in the suite:

- the encoded CFG bytes (``rust/ENCODING.md`` v1, via ``sonolus.backend.encode``),
- the Python-side canonical structural dump (so the Rust decoder's dump can be
  compared bit-exactly without re-deriving it from the live CFG objects), and
- behavioral I/O vectors (initial block memory, RNG draw tape, return value, debug
  log, written memory) observed when the legacy Python interpreter runs the
  compiled callback in ``tests/script/conftest.py``.

Everything is content-addressed (sha256 of the encoded CFG / vector payload) and
written atomically (temp file + ``os.replace``), so concurrent pytest-xdist workers
dedup identical CFGs without corrupting anything. Per-worker JSONL event logs record
provenance (test id, hypothesis flag, callback/mode names) and encode rejections.

Capture directory layout::

    <dir>/cfgs/<sha256>.scfg          encoded CFG bytes (write-once)
    <dir>/dumps/<sha256>.txt          Python canonical dump (write-once)
    <dir>/vectors/<cfg>.<vec>.json    one I/O vector (write-once; <vec> = sha256 of payload)
    <dir>/events/<worker>-<pid>.jsonl provenance/reject events (single writer, append)

The I/O vector JSON schema is documented in ``rust/testdata/README.md`` (the curated
mini-corpus produced from a capture directory by ``tools/gen_corpus.py`` uses the
same vector payloads).

This module is only imported when the environment variable is set; default test runs
never touch it (zero overhead, zero behavior change).
"""

from __future__ import annotations

import functools
import hashlib
import json
import math
import os
import random
import struct
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from sonolus.backend.encode import CfgEncodeError, cfg_canonical_dump, encode_cfg
from sonolus.backend.interpret import Interpreter

if TYPE_CHECKING:
    from sonolus.backend.node import EngineNode
    from sonolus.backend.optimize.flow import BasicBlock

VECTOR_SCHEMA_VERSION = 1


@functools.cache
def get_capture() -> CorpusCapture:
    """Returns the process-wide capture instance (requires SONOLUS_CAPTURE_CORPUS)."""
    target = os.environ.get("SONOLUS_CAPTURE_CORPUS")
    if not target:
        raise RuntimeError("SONOLUS_CAPTURE_CORPUS is not set")
    return CorpusCapture(Path(target))


def encode_value(value: float) -> float | str:
    """Encodes a runtime value for the vector JSON.

    Finite values are stored as JSON numbers (Python's float repr round-trips
    bit-exactly, and so does serde_json's parser). Non-finite values (NaN with any
    payload, +/-inf) are not valid JSON numbers and are stored as the raw IEEE-754
    bits in the form ``"0x%016x"``.
    """
    value = float(value)
    if math.isfinite(value):
        return value
    return "0x{:016x}".format(struct.unpack("<Q", struct.pack("<d", value))[0])


class RecordingInterpreter(Interpreter):
    """Legacy interpreter that records program writes (last-write-wins)."""

    def __init__(self):
        super().__init__()
        self.writes: dict[tuple[int, int], float] = {}

    def set(self, block: float, index: float, value: float):
        result = super().set(block, index, value)
        self.writes[int(block), int(index)] = value
        return result


class CorpusCapture:
    def __init__(self, root: Path):
        self.root = root
        self.cfg_dir = root / "cfgs"
        self.dump_dir = root / "dumps"
        self.vector_dir = root / "vectors"
        self.events_dir = root / "events"
        for directory in (self.cfg_dir, self.dump_dir, self.vector_dir, self.events_dir):
            directory.mkdir(parents=True, exist_ok=True)
        worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
        self._events_path = self.events_dir / f"{worker}-{os.getpid()}.jsonl"
        self._events_lock = threading.Lock()
        self._events_file = None
        self._known_cfgs: set[str] = set()
        self._current_test: tuple[str, bool] = ("<session>", False)

    # --- test provenance (set by the tests/conftest.py hooks) ------------------------

    def set_current_test(self, nodeid: str, is_hypothesis: bool) -> None:
        self._current_test = (nodeid, is_hypothesis)

    def clear_current_test(self) -> None:
        self._current_test = ("<session>", False)

    # --- CFG capture ------------------------------------------------------------------

    def store_cfg(
        self,
        cfg: BasicBlock,
        *,
        callback_name: str = "",
        mode: str = "",
        archetype: str | None = None,
    ) -> str | None:
        """Encodes and stores a frontend CFG, recording a provenance event.

        Returns the sha256 hex digest of the encoded bytes, or None if the CFG is
        outside the encoding's documented domain (a reject event is recorded with
        the verbatim error message).
        """
        nodeid, is_hypothesis = self._current_test
        try:
            digest = self._ensure_stored(cfg)
        except CfgEncodeError as e:
            self._event(
                {
                    "type": "reject",
                    "error": str(e),
                    "test": nodeid,
                    "hypothesis": is_hypothesis,
                    "callback": callback_name,
                    "mode": mode,
                    "archetype": archetype,
                }
            )
            return None
        self._event(
            {
                "type": "cfg",
                "hash": digest,
                "test": nodeid,
                "hypothesis": is_hypothesis,
                "callback": callback_name,
                "mode": mode,
                "archetype": archetype,
            }
        )
        return digest

    def cfg_ref(self, cfg: BasicBlock) -> str | None:
        """Returns the content hash of a frontend CFG, storing it if necessary.

        Unlike store_cfg this records no event (the callback_to_cfg instrumentation
        in tests/conftest.py already recorded one for this CFG). Returns None if the
        CFG cannot be encoded.
        """
        try:
            return self._ensure_stored(cfg)
        except CfgEncodeError:
            return None

    def _ensure_stored(self, cfg: BasicBlock) -> str:
        data = encode_cfg(cfg)
        digest = hashlib.sha256(data).hexdigest()
        if digest not in self._known_cfgs:
            cfg_path = self.cfg_dir / f"{digest}.scfg"
            if not cfg_path.exists():
                dump = cfg_canonical_dump(cfg)
                # The dump is written first so that the presence of the .scfg file
                # implies the dump is present too (readers key off the .scfg files).
                _write_atomic(self.dump_dir / f"{digest}.txt", dump.encode("utf-8"))
                _write_atomic(cfg_path, data)
            self._known_cfgs.add(digest)
        return digest

    # --- behavioral I/O vectors --------------------------------------------------------

    def make_interpreter(self) -> RecordingInterpreter:
        return RecordingInterpreter()

    def run_and_record(
        self,
        interpreter: RecordingInterpreter,
        entry: EngineNode,
        cfg_hash: str,
        *,
        level: str,
        runtime_checks: str,
        temp_memory_block: int,
    ) -> float:
        """Runs the legacy interpreter, capturing an I/O vector for ``cfg_hash``.

        The initial block memory is snapshotted before the run; RNG draws made by
        Op.Random/Op.RandomInteger are recorded as a tape (args + drawn value) by
        temporarily wrapping ``random.uniform``/``random.randrange`` (delegating to
        the originals, so observable behavior is unchanged); writes are recorded by
        the RecordingInterpreter. The vector is only stored if the run completes.
        """
        inputs = [
            [int(block), [encode_value(v) for v in values]]
            for block, values in sorted((int(b), list(vs)) for b, vs in interpreter.blocks.items())
        ]
        rng_tape: list[list[object]] = []
        orig_uniform = random.uniform
        orig_randrange = random.randrange

        def recording_uniform(a, b):
            value = orig_uniform(a, b)
            rng_tape.append(["uniform", encode_value(a), encode_value(b), encode_value(value)])
            return value

        def recording_randrange(a, b):
            value = orig_randrange(a, b)
            rng_tape.append(["randrange", encode_value(a), encode_value(b), encode_value(value)])
            return value

        random.uniform = recording_uniform
        random.randrange = recording_randrange
        try:
            result = interpreter.run(entry)
        finally:
            random.uniform = orig_uniform
            random.randrange = orig_randrange

        vector = {
            "level": level,
            "runtime_checks": runtime_checks,
            "temp_memory_block": temp_memory_block,
            "inputs": inputs,
            "rng": rng_tape,
            "result": encode_value(result),
            "log": [encode_value(v) for v in interpreter.log],
            "writes": [
                [block, index, encode_value(value)] for (block, index), value in sorted(interpreter.writes.items())
            ],
        }
        payload = json.dumps(vector, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("utf-8")
        vector_hash = hashlib.sha256(payload).hexdigest()
        vector_path = self.vector_dir / f"{cfg_hash}.{vector_hash}.json"
        if not vector_path.exists():
            _write_atomic(vector_path, payload)
        nodeid, is_hypothesis = self._current_test
        self._event(
            {
                "type": "vector",
                "cfg": cfg_hash,
                "vector": vector_hash,
                "test": nodeid,
                "hypothesis": is_hypothesis,
            }
        )
        return result

    # --- events -------------------------------------------------------------------------

    def _event(self, event: dict) -> None:
        line = json.dumps(event, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
        with self._events_lock:
            if self._events_file is None:
                self._events_file = self._events_path.open("a", encoding="utf-8", buffering=1)
            self._events_file.write(line + "\n")


def _write_atomic(path: Path, data: bytes) -> None:
    """Writes ``data`` to ``path`` atomically; concurrent identical writes are safe."""
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{threading.get_ident()}")
    try:
        tmp.write_bytes(data)
        tmp.replace(path)
    except OSError:
        # Another process won the race (e.g. the target is momentarily locked on
        # Windows). The content is identical by construction, so losing is fine.
        if path.exists():
            tmp.unlink(missing_ok=True)
            return
        raise
