from collections.abc import Iterator

from sonolus.script.array import Dim
from sonolus.script.containers import ArraySet
from sonolus.script.globals import level_memory
from sonolus.script.runtime import Touch, touches


@level_memory
class InputState:
    claimed_touches: ArraySet[int, Dim[16]]


def refresh_input_state():
    """Refresh the input data at the start of each frame."""
    InputState.claimed_touches.clear()


def claim_touch(touch_id: int) -> None:
    InputState.claimed_touches.add(touch_id)


def is_touch_claimed(touch_id: int) -> bool:
    return touch_id in InputState.claimed_touches


def unclaimed_taps() -> Iterator[Touch]:
    for touch in touches():
        if touch.started and not is_touch_claimed(touch.id):
            yield touch


def unclaimed_touches() -> Iterator[Touch]:
    for touch in touches():
        if not is_touch_claimed(touch.id):
            yield touch
