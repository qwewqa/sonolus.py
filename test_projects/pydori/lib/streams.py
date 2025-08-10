from sonolus.script.array import Dim
from sonolus.script.containers import ArraySet
from sonolus.script.stream import Stream, StreamGroup, streams


@streams
class Streams:
    # Records the set of lanes at each time when the empty tap lane effect was played.
    effect_lanes: Stream[ArraySet[float, Dim[16]]]

    # Records whether a hold is active at a given time.
    # Keyed by the hold head's index.
    hold_activity: StreamGroup[bool, Dim[99999]]
