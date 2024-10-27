from sonolus.backend.ops import Op
from sonolus.script.internal.native import native_function
from sonolus.script.record import Record


class Beat(Record):
    beat: float

    @property
    def bpm(self) -> float:
        """The bpm at this beat."""
        return _beat_to_bpm(self.beat)

    @property
    def time(self) -> float:
        """The time at this beat."""
        return _beat_to_time(self.beat)

    @property
    def start_beat(self) -> float:
        """The beat of the last bpm change."""
        return _beat_to_starting_beat(self.beat)

    @property
    def start_time(self) -> float:
        """The time of the last bpm change."""
        return _beat_to_starting_time(self.beat)


@native_function(Op.BeatToBPM)
def _beat_to_bpm(beat: float) -> float:
    raise NotImplementedError


@native_function(Op.BeatToTime)
def _beat_to_time(beat: float) -> float:
    raise NotImplementedError


@native_function(Op.BeatToStartingBeat)
def _beat_to_starting_beat(beat: float) -> float:
    raise NotImplementedError


@native_function(Op.BeatToStartingTime)
def _beat_to_starting_time(beat: float) -> float:
    raise NotImplementedError
