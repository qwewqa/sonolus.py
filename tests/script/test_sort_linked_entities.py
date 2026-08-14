"""Tests for sonolus.script.containers.sort_linked_entities and the _EntityNodeRef adapter behind it.

sort_linked_entities reaches every field through `archetype.at(index)`, which is only available inside a
compile context, so there is no plain-Python reference to run it against and run_and_validate does not apply.
These tests use the `_compile_and_run` idiom of tests/script/test_entity_refs.py instead: build a Play-mode
compile context, compile at every optimization level, pre-populate the interpreter's EntityInfoArray and
EntityDataArray the way the real engine would, interpret, and read the linked list back out of the data block.

Entity index 0 is the absent sentinel (`_EntityNodeRef.is_present` is `index > 0`), which is also how
test_projects/pydori spells its own present-check, so the entities here start at index 1.
"""

from typing import NamedTuple

from sonolus.backend.blocks import PlayBlock
from sonolus.backend.interpret import Interpreter
from sonolus.backend.mode import Mode
from sonolus.backend.optimize import OptimizerConfig, cfg_to_engine_node, run_passes
from sonolus.backend.place import BlockPlace
from sonolus.build.compile import callback_to_cfg
from sonolus.script.archetype import (
    _ENTITY_DATA_SIZE,
    EntityRef,
    PlayArchetype,
    imported,
)
from sonolus.script.containers import sort_linked_entities
from sonolus.script.internal.context import ModeContextState, ProjectContextState, RuntimeChecks
from sonolus.script.internal.meta_fn import meta_fn
from sonolus.script.internal.visitor import clear_frontend_caches, compile_and_call
from sonolus.script.num import Num
from tests.script.conftest import optimization_levels


class LinkNote(PlayArchetype):
    name = "LinkNote"

    sort_key: float = imported()
    # Quoted so the self-reference is a forward reference: before Python 3.14, a class annotation is
    # evaluated as the class body runs, when the name is not yet bound. test_projects/pydori gets the same
    # effect from a module-level `from __future__ import annotations`.
    next_ref: "EntityRef[LinkNote]" = imported()
    prev_ref: "EntityRef[LinkNote]" = imported()


ARCHETYPES = [LinkNote]
NOTE_ID = ModeContextState(Mode.PLAY, ARCHETYPES).archetypes[LinkNote]


def _field_offset(name: str) -> int:
    """Offset of an imported field within an entity's EntityData row.

    An archetype lays its fields out lazily, on first use, so this forces that pass before reading the offset.
    Reading the real offsets rather than hardcoding them keeps the block seeding correct if the layout moves;
    it is harness plumbing, and the expectations below still come from sorted().
    """
    LinkNote._init_fields()
    return LinkNote._imported_fields_[name].offset


KEY_OFFSET = _field_offset("sort_key")
NEXT_OFFSET = _field_offset("next_ref")
PREV_OFFSET = _field_offset("prev_ref")


def _sort_forward_only(head_index: int) -> int:
    return sort_linked_entities(
        EntityRef[LinkNote](head_index),
        get_value=lambda e: e.sort_key,
        get_next_ref=lambda e: e.next_ref,
    ).index


def _sort_with_prev(head_index: int) -> int:
    return sort_linked_entities(
        EntityRef[LinkNote](head_index),
        get_value=lambda e: e.sort_key,
        get_next_ref=lambda e: e.next_ref,
        get_prev_ref=lambda e: e.prev_ref,
    ).index


class Run(NamedTuple):
    """What one compile-and-interpret pass produced, in a form comparable across optimization levels."""

    head: int
    forward: tuple[tuple[int, float], ...]
    backward: tuple[int, ...]
    prev_links: tuple[tuple[int, int], ...]
    terminated: bool


def _read(interpreter, index, offset):
    return interpreter.get(PlayBlock.EntityDataArray, index * _ENTITY_DATA_SIZE + offset)


def _walk(interpreter, start_index, link_offset, limit):
    """Follow `link_offset` links from `start_index` until the 0 sentinel, giving up after `limit` steps."""
    indices = []
    current = int(start_index)
    while current > 0:
        assert len(indices) < limit, f"chain from {start_index} did not reach the 0 sentinel within {limit} steps"
        indices.append(current)
        current = int(_read(interpreter, current, link_offset))
    return indices


def _compile_and_run(fn, head_index, entities) -> Run:
    """Compile `fn(head_index)`, seed the entity blocks from `entities`, interpret, and read the list back.

    `entities` maps an entity index to `(sort_key, next_index, prev_index)`. The returned `Run` holds the sorted
    head `fn` returned, the forward walk from it as `(index, key)` pairs, the backward walk from the last
    forward node as plain indices, every entity's prev link, and whether a runtime check terminated the callback
    before place (-1, 0) could be set to 1 (the sentinel trick tests/script/conftest.py uses).
    """
    runs = []
    limit = len(entities) + 1
    for passes in optimization_levels:
        clear_frontend_caches()
        project_state = ProjectContextState(runtime_checks=RuntimeChecks.NONE)
        mode_state = ModeContextState(Mode.PLAY, ARCHETYPES)

        @meta_fn
        def wrapper(_fn=fn, _head=head_index):
            value = compile_and_call(_fn, _head)
            Num._from_place_(BlockPlace(-2, 0))._set_(value)
            Num._from_place_(BlockPlace(-1, 0))._set_(Num(1))
            return 0

        cfg = callback_to_cfg(project_state, mode_state, wrapper, "")
        cfg = run_passes(cfg, passes, OptimizerConfig())
        entry = cfg_to_engine_node(cfg)
        interpreter = Interpreter()
        interpreter.blocks[PlayBlock.EngineRom] = project_state.rom.values
        for index, (key, next_index, prev_index) in entities.items():
            interpreter.set(PlayBlock.EntityInfoArray, index * 3, index)
            interpreter.set(PlayBlock.EntityInfoArray, index * 3 + 1, NOTE_ID)
            interpreter.set(PlayBlock.EntityInfoArray, index * 3 + 2, 0)
            interpreter.set(PlayBlock.EntityDataArray, index * _ENTITY_DATA_SIZE + KEY_OFFSET, key)
            interpreter.set(PlayBlock.EntityDataArray, index * _ENTITY_DATA_SIZE + NEXT_OFFSET, next_index)
            interpreter.set(PlayBlock.EntityDataArray, index * _ENTITY_DATA_SIZE + PREV_OFFSET, prev_index)
        interpreter.run(entry)

        head = int(interpreter.get(-2, 0))
        forward_indices = _walk(interpreter, head, NEXT_OFFSET, limit)
        forward = tuple((index, _read(interpreter, index, KEY_OFFSET)) for index in forward_indices)
        backward = tuple(_walk(interpreter, forward_indices[-1], PREV_OFFSET, limit)) if forward_indices else ()
        prev_links = tuple(sorted((index, int(_read(interpreter, index, PREV_OFFSET))) for index in entities))
        runs.append(Run(head, forward, backward, prev_links, interpreter.get(-1, 0) != 1))
    assert all(run == runs[0] for run in runs), f"Optimization levels disagree: {runs}"
    return runs[0]


def _chain(keys, start_index=1):
    """Chain `len(keys)` entities from `start_index` upwards in declaration order, carrying `keys` as sort keys."""
    entities = {}
    for i, key in enumerate(keys):
        index = start_index + i
        entities[index] = (key, index + 1 if i + 1 < len(keys) else 0, index - 1 if i > 0 else 0)
    return entities


# The duplicate 20.0 is deliberate: which of two equal-keyed entities lands first is stability, a property of the
# merge rather than a documented guarantee, so only the key sequence is asserted.
SCRAMBLED_KEYS = [50.0, 10.0, 40.0, 20.0, 30.0, 20.0]


def test_sort_orders_the_forward_chain():
    entities = _chain(SCRAMBLED_KEYS)
    run = _compile_and_run(_sort_forward_only, 1, entities)

    assert not run.terminated, "the callback terminated instead of returning a sorted head"
    assert [key for _, key in run.forward] == sorted(SCRAMBLED_KEYS)
    assert {index for index, _ in run.forward} == set(entities), "the sorted chain lost or repeated an entity"


def test_sort_without_get_prev_ref_leaves_backward_links_untouched():
    entities = _chain(SCRAMBLED_KEYS)
    run = _compile_and_run(_sort_forward_only, 1, entities)

    assert not run.terminated, "the callback terminated instead of returning a sorted head"
    assert [key for _, key in run.forward] == sorted(SCRAMBLED_KEYS)
    seeded = tuple(sorted((index, prev) for index, (_, _, prev) in entities.items()))
    assert run.prev_links == seeded, "prev links were rewritten even though get_prev_ref was not passed"


def test_sort_with_get_prev_ref_rebuilds_the_backward_chain():
    entities = _chain(SCRAMBLED_KEYS)
    run = _compile_and_run(_sort_with_prev, 1, entities)

    assert not run.terminated, "the callback terminated instead of returning a sorted head"
    assert [key for _, key in run.forward] == sorted(SCRAMBLED_KEYS)
    # The seeded prev links are 0, 1, 2, ... in declaration order and sorting permutes the chain, so a set_prev
    # that never fired would leave a backward walk that is not the reverse of the forward one.
    assert run.backward == tuple(index for index, _ in reversed(run.forward))
    assert (run.head, 0) in run.prev_links, "the sorted head's prev link is not the absent sentinel"


def test_sort_of_a_single_node_clears_its_stale_prev_link():
    # A one-element list takes the trivial-case early return, so the fixup loop is the only thing that can
    # replace the seeded prev link with the absent sentinel.
    entities = {1: (5.0, 0, 7)}
    run = _compile_and_run(_sort_with_prev, 1, entities)

    assert not run.terminated, "the callback terminated instead of returning a sorted head"
    assert run.head == 1
    assert run.forward == ((1, 5.0),)
    assert run.prev_links == ((1, 0),)


def test_sort_of_an_absent_head_returns_the_absent_sentinel():
    run = _compile_and_run(_sort_with_prev, 0, {1: (5.0, 0, 0)})

    assert not run.terminated, "the callback terminated instead of returning a sorted head"
    assert run.head == 0
    assert run.forward == ()
