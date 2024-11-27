from sonolus.backend.ops import Op
from sonolus.script.internal.native import native_function


@native_function(Op.BeatToBPM)
def beat_to_bpm(beat: float) -> float:
    """Get the bpm at the given beat.

    Args:
        beat: The beat to get the bpm at.

    Returns:
        The bpm at the given beat.
    """
    raise NotImplementedError


@native_function(Op.BeatToTime)
def beat_to_time(beat: float) -> float:
    """Get the time at the given beat.

    Args:
        beat: The beat to get the time at.

    Returns:
        The time at the given beat.
    """
    raise NotImplementedError


@native_function(Op.BeatToStartingBeat)
def beat_to_starting_beat(beat: float) -> float:
    """Get the starting beat of the bpm section at the given beat.

    I.e. the beat of the bpm change at or immediately before the given beat.

    Args:
        beat: The beat to get the starting beat of the bpm section at.

    Returns:
        The starting beat of the bpm section at the given beat.
    """
    raise NotImplementedError


@native_function(Op.BeatToStartingTime)
def beat_to_starting_time(beat: float) -> float:
    """Get the starting time of the bpm section at the given beat.

    I.e. the time of the bpm change at or immediately before the given beat.

    Args:
        beat: The beat to get the starting time of the bpm section at.

    Returns:
        The starting time of the bpm section at the given beat.
    """
    raise NotImplementedError


@native_function(Op.TimeToScaledTime)
def time_to_scaled_time(time: float) -> float:
    """Get the scaled (timescale adjusted) time at the given time.

    Args:
        time: The time to get the scaled time at.

    Returns:
        The scaled (timescale adjusted) time at the given time.
    """
    raise NotImplementedError


@native_function(Op.TimeToStartingScaledTime)
def time_to_starting_scaled_time(time: float) -> float:
    """Get the starting scaled (timescale adjusted) time at the given time.

    I.e. the scaled time of the timescale change at or immediately before the given time.

    Args:
        time: The time to get the starting scaled time at.

    Returns:
        The starting scaled time at the given time.
    """
    raise NotImplementedError


@native_function(Op.TimeToStartingTime)
def time_to_starting_time(time: float) -> float:
    """Get the starting time of the timescale section at the given time.

    I.e. the time of the timescale change at or immediately before the given time.

    Args:
        time: The time to get the starting time of the timescale section at.

    Returns:
        The starting time of the timescale section at the given time.
    """
    raise NotImplementedError


@native_function(Op.TimeToTimeScale)
def time_to_timescale(time: float) -> float:
    """Get the timescale at the given time.

    Args:
        time: The time to get the timescale at.

    Returns:
        The timescale at the given time.
    """
    raise NotImplementedError
