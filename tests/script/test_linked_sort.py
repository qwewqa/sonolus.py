from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from sonolus.script.array import Array
from sonolus.script.containers import _merge_sort_linked_list_nodes  # noqa: PLC2701
from sonolus.script.record import Record
from tests.script.conftest import run_and_validate


class Node[T](Record):
    value: T
    next_index: int


class NodeRef[T, Size](Record):
    arr: Array[Node[T], Size]
    index: int

    def get_value(self) -> T:
        return self.arr[self.index].value

    def get_next(self) -> NodeRef:
        next_index = self.arr[self.index].next_index
        return NodeRef(self.arr, next_index)

    def set_next(self, next_node: NodeRef) -> None:
        self.arr[self.index].next_index = next_node.index

    def set_prev(self, prev_node: NodeRef) -> None:
        pass  # Not needed

    def is_present(self) -> bool:
        return self.index != -1

    def set(self, other: NodeRef) -> None:
        self.index = other.index

    def copy(self) -> NodeRef:
        return NodeRef(self.arr, self.index)

    def empty(self) -> NodeRef:
        return NodeRef(self.arr, -1)


@given(
    elts=st.lists(st.integers(min_value=-9999, max_value=9999), min_size=0, max_size=100),
)
def test_merge_sort_linked_list_nodes(elts: list[int]):
    orig_values = Array[Node[int], len(elts)](
        *(Node(value=elt, next_index=i + 1 if i + 1 < len(elts) else -1) for i, elt in enumerate(elts))
    )

    size = len(elts)

    def fn():
        values = +orig_values
        head_ref = NodeRef(values, 0 if size > 0 else -1)

        sorted_head_ref = _merge_sort_linked_list_nodes(head_ref)

        results = +Array[int, size]
        current_ref = sorted_head_ref.copy()
        idx = 0
        while current_ref.is_present():
            results[idx] = current_ref.get_value()
            current_ref.set(current_ref.get_next())
            idx += 1
        return results

    assert list(run_and_validate(fn)) == sorted(elts)
