"""Pins the watch-mode permission tables for the current-entity blocks (4000-4006) against the Sonolus spec.

The tables in sonolus/backend/blocks.py are hand transcriptions of the spec's per-block Callback/Read/Write
tables, so the expectations here are written out from the spec rather than read back off the enum. `readable`
gates compile-time reads (sonolus/script/internal/context.py check_readable) and `writable` gates writes, so a
transcription slip in either direction silently changes which programs compile.
"""

import pytest

from sonolus.backend.blocks import WatchBlock

# The seven per-entity callbacks an EngineWatchDataArchetype declares.
ARCHETYPE_CALLBACKS = frozenset(
    {
        "preprocess",
        "spawnTime",
        "despawnTime",
        "initialize",
        "updateSequential",
        "updateParallel",
        "terminate",
    }
)

# updateSpawn is a top-level watch callback rather than a per-archetype one, and the spec marks it read-yes /
# write-no on every current-entity block, including the two whose other rows are write-yes throughout.
CURRENT_ENTITY_READABLE = ARCHETYPE_CALLBACKS | {"updateSpawn"}

# (block, id, writable) straight off the spec's Write column, one row per current-entity block.
CURRENT_ENTITY_BLOCKS = [
    (WatchBlock.EntityMemory, 4000, ARCHETYPE_CALLBACKS),
    (WatchBlock.EntityData, 4001, frozenset({"preprocess"})),
    (WatchBlock.EntitySharedMemory, 4002, frozenset({"preprocess", "updateSequential"})),
    (WatchBlock.EntityInfo, 4003, frozenset()),
    (WatchBlock.EntityInput, 4004, ARCHETYPE_CALLBACKS),
    (WatchBlock.EntityScore, 4005, frozenset({"preprocess"})),
    (WatchBlock.EntityLife, 4006, frozenset({"preprocess"})),
]


@pytest.mark.parametrize(
    ("block", "expected_id", "expected_writable"),
    CURRENT_ENTITY_BLOCKS,
    ids=[block.name for block, _, _ in CURRENT_ENTITY_BLOCKS],
)
def test_watch_current_entity_block_permissions(block, expected_id, expected_writable):
    # set() normalizes the empty writable entries, which blocks.py spells as an empty dict.
    assert int(block) == expected_id
    assert set(block.readable) == set(CURRENT_ENTITY_READABLE)
    assert set(block.writable) == set(expected_writable)
