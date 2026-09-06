"""Focused regressions for the two-source ``Scope.apply_merge`` path."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar

from sonolus.backend.mode import Mode
from sonolus.backend.place import BlockPlace
from sonolus.script.internal.context import (
    CallbackContextState,
    ConflictBinding,
    Context,
    ModeContextState,
    ProjectContextState,
    Scope,
    ValueBinding,
    _binding_was_read,
    ctx,
)
from sonolus.script.internal.value import DataValue, Value


class MergeProbe(Value):
    """Minimal value-protocol implementation that records merge-hook calls."""

    __slots__ = ("assignments", "label")

    events: ClassVar[list[tuple[str, Context, Any]]] = []
    on_merge: ClassVar[Callable[[], None] | None] = None
    last_target: ClassVar[MergeProbe | None] = None

    def __init__(self, label: str):
        self.label = label
        self.assignments = []

    @classmethod
    def reset(cls, on_merge: Callable[[], None] | None = None):
        cls.events = []
        cls.on_merge = on_merge
        cls.last_target = None

    @classmethod
    def _size_(cls) -> int:
        return 0

    @classmethod
    def _is_value_type_(cls) -> bool:
        return True

    @classmethod
    def _from_place_(cls, _place: BlockPlace) -> MergeProbe:
        return cls("place")

    @classmethod
    def _accepts_(cls, value: Any) -> bool:
        return type(value) is cls

    @classmethod
    def _accept_(cls, value: Any) -> MergeProbe:
        if type(value) is not cls:
            raise TypeError
        return value

    def _is_py_(self) -> bool:
        return True

    def _as_py_(self) -> str:
        return self.label

    @classmethod
    def _from_list_(cls, _values) -> MergeProbe:
        return cls("list")

    def _to_list_(self, level_refs: dict[Any, str] | None = None) -> list[DataValue | str]:
        return []

    @classmethod
    def _flat_keys_(cls, _prefix: str) -> list[str]:
        return []

    def _get_(self) -> MergeProbe:
        return self

    def _set_(self, value: Any):
        self.events.append(("set", ctx(), value))
        self.assignments.append(value)

    def _copy_from_(self, value: Any, *, initializing: bool = False):
        raise AssertionError("merge probe must not be copied")

    def _copy_(self) -> MergeProbe:
        return type(self)(self.label)

    @classmethod
    def _alloc_(cls) -> MergeProbe:
        return cls("alloc")

    @classmethod
    def _zero_(cls) -> MergeProbe:
        return cls("zero")

    @classmethod
    def _get_merge_target_(cls, values: list[Any]) -> MergeProbe:
        cls.events.append(("target", ctx(), values))
        if cls.on_merge is not None:
            cls.on_merge()
        cls.last_target = cls("merged")
        return cls.last_target


class ReferenceProbe(MergeProbe):
    """Reference-valued probe used to exercise ``merged_into`` bookkeeping."""

    __slots__ = ()

    @classmethod
    def _is_value_type_(cls) -> bool:
        return False


def _context(bindings: dict[str, ValueBinding] | None = None) -> Context:
    return Context(
        ProjectContextState(),
        ModeContextState(Mode.PLAY),
        CallbackContextState("update"),
        Scope(bindings),
    )


def test_identical_binding_object_is_preserved_with_its_read_count():
    value = ReferenceProbe("shared")
    binding = ValueBinding(value, read_count=3)
    first = _context({"item": binding})
    second = _context({"item": binding})
    target = _context()

    Scope.apply_merge(target, [first, second])

    assert target.scope.get_binding("item") is binding
    assert target.scope.get_value("item") is value
    assert binding.read_count == 4


def test_distinct_bindings_of_same_reference_get_fresh_directed_read_tracking():
    value = ReferenceProbe("shared")
    first_binding = ValueBinding(value)
    second_binding = ValueBinding(value)
    first = _context({"item": first_binding})
    second = _context({"item": second_binding})
    target = _context()

    Scope.apply_merge(target, [first, second])

    merged = target.scope.get_binding("item")
    assert isinstance(merged, ValueBinding)
    assert merged is not first_binding
    assert merged is not second_binding
    assert merged.value is value
    assert first_binding.merged_into == [merged]
    assert second_binding.merged_into == [merged]
    assert merged.merged_into is None

    assert first.scope.get_value("item") is value
    assert first_binding.read_count == 1
    assert merged.read_count == 0
    assert not _binding_was_read(merged)

    assert target.scope.get_value("item") is value
    assert merged.read_count == 1
    assert first_binding.read_count == 1
    assert second_binding.read_count == 0
    assert _binding_was_read(second_binding)


def test_merge_hook_uses_snapshot_key_order_and_observes_mutated_later_binding():
    first_value = MergeProbe("first")
    second_value = MergeProbe("second")
    later_value = ReferenceProbe("later")
    later_binding = ValueBinding(later_value, read_count=7)
    added_binding = ValueBinding(ReferenceProbe("added"))
    removed_binding = ValueBinding(ReferenceProbe("removed"))
    first = _context({"trigger": ValueBinding(first_value), "removed": removed_binding})
    second = _context({"trigger": ValueBinding(second_value), "later": later_binding})
    target = _context()

    def mutate_first_source():
        first.scope.set_binding("later", later_binding)
        first.scope.set_binding("added_during_hook", added_binding)
        first.scope.delete_binding("removed")

    MergeProbe.reset(mutate_first_source)
    Scope.apply_merge(target, [first, second])

    assert list(target.scope.bindings) == ["trigger", "removed", "later"]
    assert isinstance(target.scope.get_binding("removed"), ConflictBinding)
    assert target.scope.get_binding("later") is later_binding
    assert "added_during_hook" not in target.scope.bindings

    target_event, first_set, second_set = MergeProbe.events
    assert target_event[0] == "target"
    assert target_event[1] is target
    assert type(target_event[2]) is list
    assert target_event[2] == [first_value, second_value]
    assert first_set == ("set", first, first_value)
    assert second_set == ("set", second, second_value)

    merged = target.scope.get_binding("trigger")
    assert isinstance(merged, ValueBinding)
    assert merged.value is MergeProbe.last_target
    assert MergeProbe.last_target.assignments == [first_value, second_value]
    assert later_binding.read_count == 7
