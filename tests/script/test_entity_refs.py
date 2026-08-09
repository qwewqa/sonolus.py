"""Tests for _BaseArchetype.is_at, EntityRef.get_as / archetype_matches, and EntityRef acceptance.

is_at, get_as, and archetype_matches all read entity_info_at(index), which is only available inside a compile
context, so there is no plain-Python reference to run them against. Those tests build a Play-mode compile context
with a base, a subclass, and an unrelated archetype registered, pre-populate the interpreter's EntityInfoArray
block the way the real engine would, and interpret the compiled result, following
tests/script/test_global_memory.py and tests/backend/test_block_access.py.

The acceptance tests need none of that and go through run_and_validate.
"""

from typing import Any

import pytest

from sonolus.backend.blocks import PlayBlock
from sonolus.backend.interpret import Interpreter
from sonolus.backend.mode import Mode
from sonolus.backend.optimize import OptimizerConfig, cfg_to_engine_node, run_passes
from sonolus.backend.place import BlockPlace
from sonolus.build.compile import callback_to_cfg
from sonolus.script.archetype import EntityRef, PlayArchetype
from sonolus.script.internal.context import ModeContextState, ProjectContextState, RuntimeChecks
from sonolus.script.internal.meta_fn import meta_fn
from sonolus.script.internal.visitor import clear_frontend_caches, compile_and_call
from sonolus.script.num import Num
from sonolus.script.record import Record
from tests.script.conftest import optimization_levels, run_and_validate


class RefBase(PlayArchetype):
    name = "RefBase"


class RefSub(RefBase):
    name = "RefSub"


class RefOther(PlayArchetype):
    name = "RefOther"


class BaseRefHolder(Record):
    ref: EntityRef[RefBase]


class AnyRefHolder(Record):
    ref: EntityRef[Any]


ARCHETYPES = [RefBase, RefSub, RefOther]
# Ids are assigned in ARCHETYPES order, so this matches every fresh ModeContextState _compile_and_run builds.
SUB_ID = ModeContextState(Mode.PLAY, ARCHETYPES).archetypes[RefSub]


def _compile_and_run(fn, entity_rows, runtime_checks=RuntimeChecks.NONE):
    """Compile `wrapper` (which calls `fn()` and records its result) and interpret it.

    `entity_rows` maps an entity index to the archetype id EntityInfoArray should report for it.
    Returns (result, terminated): `result` is whatever `fn()` returned (written to place (-2, 0));
    `terminated` is True if a runtime check aborted the callback before place (-1, 0) could be set to
    1. This mirrors the sentinel-place trick run_and_validate uses in tests/script/conftest.py to
    detect an early terminate().
    """
    results = []
    terminations = []
    for passes in optimization_levels:
        clear_frontend_caches()
        project_state = ProjectContextState(runtime_checks=runtime_checks)
        mode_state = ModeContextState(Mode.PLAY, ARCHETYPES)

        @meta_fn
        def wrapper(_fn=fn):
            value = compile_and_call(_fn)
            Num._from_place_(BlockPlace(-2, 0))._set_(value)
            Num._from_place_(BlockPlace(-1, 0))._set_(Num(1))
            return 0

        cfg = callback_to_cfg(project_state, mode_state, wrapper, "")
        cfg = run_passes(cfg, passes, OptimizerConfig())
        entry = cfg_to_engine_node(cfg)
        interpreter = Interpreter()
        interpreter.blocks[PlayBlock.EngineRom] = project_state.rom.values
        for index, archetype_id in entity_rows.items():
            interpreter.set(PlayBlock.EntityInfoArray, index * 3, index)
            interpreter.set(PlayBlock.EntityInfoArray, index * 3 + 1, archetype_id)
            interpreter.set(PlayBlock.EntityInfoArray, index * 3 + 2, 0)
        interpreter.run(entry)
        terminations.append(interpreter.get(-1, 0) != 1)
        results.append(interpreter.get(-2, 0))
    assert len(set(results)) == 1, f"Result differs between optimization levels: {results}"
    assert len(set(terminations)) == 1, f"Termination differs between optimization levels: {terminations}"
    return results[0], terminations[0]


def test_is_at_strict_true_for_exact_archetype():
    result, terminated = _compile_and_run(lambda: RefSub.is_at(0, strict=True), {0: SUB_ID})
    assert not terminated
    assert result == 1


def test_is_at_strict_false_for_subclass_instance():
    result, terminated = _compile_and_run(lambda: RefBase.is_at(0, strict=True), {0: SUB_ID})
    assert not terminated
    assert result == 0


def test_is_at_non_strict_true_for_subclass_instance():
    result, terminated = _compile_and_run(lambda: RefBase.is_at(0, strict=False), {0: SUB_ID})
    assert not terminated
    assert result == 1


def test_is_at_false_for_unrelated_archetype():
    result, terminated = _compile_and_run(lambda: RefOther.is_at(0, strict=False), {0: SUB_ID})
    assert not terminated
    assert result == 0


def test_is_at_false_for_negative_index():
    result, terminated = _compile_and_run(lambda: RefBase.is_at(-1, strict=False), {0: SUB_ID})
    assert not terminated
    assert result == 0


def test_archetype_matches_true_for_matching_subclass_target():
    result, terminated = _compile_and_run(lambda: EntityRef[RefBase](index=0).archetype_matches(), {0: SUB_ID})
    assert not terminated
    assert result == 1


def test_archetype_matches_false_for_unrelated_target():
    result, terminated = _compile_and_run(lambda: EntityRef[RefOther](index=0).archetype_matches(), {0: SUB_ID})
    assert not terminated
    assert result == 0


def test_archetype_matches_strict_false_for_subclass_target():
    result, terminated = _compile_and_run(
        lambda: EntityRef[RefBase](index=0).archetype_matches(strict=True), {0: SUB_ID}
    )
    assert not terminated
    assert result == 0


def test_archetype_matches_strict_true_for_exact_target():
    result, terminated = _compile_and_run(
        lambda: EntityRef[RefSub](index=0).archetype_matches(strict=True), {0: SUB_ID}
    )
    assert not terminated
    assert result == 1


@pytest.mark.parametrize(
    ("archetype", "expect_terminated"),
    [
        # get_as's check is non-strict, like is_at(strict=False), so the base archetype also passes for a RefSub.
        pytest.param(RefBase, False, id="right-base"),
        pytest.param(RefSub, False, id="right-exact"),
        pytest.param(RefOther, True, id="wrong-unrelated"),
    ],
)
def test_get_as_right_and_wrong_archetype_targets(archetype, expect_terminated):
    def fn(_archetype=archetype):
        entity = EntityRef[Any](index=0).get_as(_archetype)
        return entity.index

    # get_as's check is skipped entirely under RuntimeChecks.NONE, which would report not-terminated everywhere.
    result, terminated = _compile_and_run(fn, {0: SUB_ID}, runtime_checks=RuntimeChecks.TERMINATE)
    assert terminated is expect_terminated
    if not terminated:
        assert result == 0


def test_entity_ref_record_field_aliases_constructor_argument():
    def fn():
        ref = EntityRef[RefBase](index=1)
        holder = BaseRefHolder(ref)
        holder.ref.index = 7
        return ref.index

    assert run_and_validate(fn) == 7


def test_entity_ref_frozen_record_field_aliases_constructor_argument():
    def fn():
        ref = EntityRef[RefBase](index=1)
        holder = BaseRefHolder.frozen(ref)
        ref.index = 7
        return holder.ref.index

    assert run_and_validate(fn) == 7


def test_entity_ref_record_field_converts_to_an_any_parameterization():
    assert AnyRefHolder(EntityRef[RefSub](index=3)).ref.archetype() is Any
    assert run_and_validate(lambda: AnyRefHolder(EntityRef[RefSub](index=3)).ref.index) == 3


def test_entity_ref_record_field_converts_to_a_base_parameterization():
    assert BaseRefHolder(EntityRef[RefSub](index=3)).ref.archetype() is RefBase
    assert run_and_validate(lambda: BaseRefHolder(EntityRef[RefSub](index=3)).ref.index) == 3
