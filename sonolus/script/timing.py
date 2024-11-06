from sonolus.backend.ops import Op
from sonolus.script.internal.native import native_function


@native_function(Op.BeatToBPM)
def beat_to_bpm(beat: float) -> float:
    raise NotImplementedError


@native_function(Op.BeatToTime)
def beat_to_time(beat: float) -> float:
    raise NotImplementedError


@native_function(Op.BeatToStartingBeat)
def beat_to_starting_beat(beat: float) -> float:
    raise NotImplementedError


@native_function(Op.BeatToStartingTime)
def beat_to_starting_time(beat: float) -> float:
    raise NotImplementedError


@native_function(Op.TimeToScaledTime)
def time_to_scaled_time(time: float) -> float:
    raise NotImplementedError


@native_function(Op.TimeToStartingScaledTime)
def time_to_starting_scaled_time(time: float) -> float:
    raise NotImplementedError


@native_function(Op.TimeToStartingTime)
def time_to_starting_time(time: float) -> float:
    raise NotImplementedError


@native_function(Op.TimeToTimeScale)
def time_to_timescale(time: float) -> float:
    raise NotImplementedError
