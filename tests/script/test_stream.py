from typing import Annotated, ClassVar, Final

import pytest

from sonolus.backend.blocks import WatchBlock
from sonolus.backend.interpret import Interpreter
from sonolus.backend.mode import Mode
from sonolus.backend.node import EngineNode, FunctionNode
from sonolus.backend.ops import Op
from sonolus.backend.optimize import OptimizerConfig, cfg_to_engine_node, run_passes
from sonolus.build.compile import callback_to_cfg
from sonolus.script.debug import debug_log
from sonolus.script.internal.context import ModeContextState, ProjectContextState, RuntimeChecks
from sonolus.script.internal.visitor import clear_frontend_caches
from sonolus.script.num import Num
from sonolus.script.runtime import time
from sonolus.script.stream import (
    Stream,
    StreamGroup,
    _StreamDataField,  # noqa: PLC2701
    _StreamField,  # noqa: PLC2701
    streams,
)
from sonolus.script.vec import Vec2
from tests.script.conftest import optimization_levels


def test_streams_rejects_final_stream():
    # Final[Stream[...]] normalizes back to Stream[...], which must not be accepted as a data field.
    @streams
    class Strms:
        bad: Final[Stream[int]]

    with pytest.raises(TypeError, match="not supported as a streams data field"):
        type(Strms)._init_()


def test_streams_rejects_final_stream_group():
    @streams
    class Strms:
        bad: Final[StreamGroup[int, 10]]

    with pytest.raises(TypeError, match="not supported as a streams data field"):
        type(Strms)._init_()


def test_streams_reports_a_real_default_value():
    @streams
    class Strms:
        bad: Stream[int] = 5

    with pytest.raises(TypeError, match="Default values are not supported for streams fields"):
        type(Strms)._init_()


def test_streams_does_not_report_annotated_as_a_default_value():
    # A class value arrives as Annotated[...], and so does a user-written Annotated[...] with no default at all.
    @streams
    class Strms:
        bad: Annotated[Stream[int], "doc"]

    with pytest.raises(TypeError, match="not supported as a streams data field"):
        type(Strms)._init_()


@pytest.mark.parametrize(
    ("annotation", "message"),
    [
        (str, "Unsupported type spec"),
        (ClassVar[int], "Unsupported value"),
        (Stream, "Must have type arguments"),
    ],
    ids=["unsupported-type", "classvar", "unparameterized-stream"],
)
def test_streams_errors_name_the_offending_field(annotation, message):
    @streams
    class Strms:
        good: Stream[int]
        offender: annotation

    with pytest.raises(TypeError, match=rf"Error processing streams field 'offender'.*{message}"):
        type(Strms)._init_()


def test_streams_layout_and_descriptors():
    @streams
    class Strms:
        stream: Stream[int]
        group: StreamGroup[int, 10]
        data_field: int
        final_data_field: Final[int]
        vec_field: Vec2

    cls = type(Strms)
    cls._init_()

    assert cls._streams_ == [
        ("stream", 1, Stream[Num]),
        ("group", 2, StreamGroup[Num, 10]),
        ("data_field", 12, Num),
        ("final_data_field", 13, Num),
        ("vec_field", 14, Vec2),
    ]

    descriptors = {name: cls.__dict__[name] for name, _, _ in cls._streams_}
    assert type(descriptors["stream"]) is _StreamField
    assert type(descriptors["group"]) is _StreamField
    assert type(descriptors["data_field"]) is _StreamDataField
    assert type(descriptors["final_data_field"]) is _StreamDataField
    assert type(descriptors["vec_field"]) is _StreamDataField


_STREAM_ID = 1


def _value_of(key: float) -> float:
    return key * 10


class _StreamInterpreter(Interpreter):
    """Interpreter with a single stream backed by a plain dict mapping each key to `_value_of(key)`.

    The base `Interpreter` has no cases for the stream ops, so this class handles them and delegates
    everything else. Each op follows the contract stated on its `_stream_*` wrapper in
    `sonolus/script/stream.py`, except `Op.StreamGetValue`, whose interpolation contract is stated on
    `Stream.__getitem__`.
    """

    def __init__(self, keys):
        super().__init__()
        self.stream = {float(key): _value_of(key) for key in keys}

    def run(self, node: EngineNode) -> float:
        if isinstance(node, FunctionNode):
            match node.func:
                case Op.StreamHas:
                    _, key = self._stream_args(node)
                    return 1.0 if key in self.stream else 0.0
                case Op.StreamGetNextKey:
                    _, key = self._stream_args(node)
                    later = [k for k in sorted(self.stream) if k > key]
                    return later[0] if later else key
                case Op.StreamGetPreviousKey:
                    _, key = self._stream_args(node)
                    earlier = [k for k in sorted(self.stream) if k < key]
                    return earlier[-1] if earlier else key
                case Op.StreamGetValue:
                    _, key = self._stream_args(node)
                    return self._value_at(key)
        return super().run(node)

    def _stream_args(self, node: FunctionNode) -> list[float]:
        return [self.run(arg) for arg in node.args]

    def _value_at(self, key: float) -> float:
        # StreamGetValue is emitted as an IRPureInstr, so the optimizer may speculate the read out from behind
        # the `self.current_key in self.stream` guard and ask for a key the stream does not hold, inf included.
        # Returning the documented interpolation rather than raising keeps a speculated read from failing the test.
        if not self.stream:
            return 0.0
        if key in self.stream:
            return self.stream[key]
        keys = sorted(self.stream)
        earlier = [k for k in keys if k < key]
        later = [k for k in keys if k > key]
        if not earlier:
            return self.stream[later[0]]
        if not later:
            return self.stream[earlier[-1]]
        lo, hi = earlier[-1], later[0]
        return self.stream[lo] + (self.stream[hi] - self.stream[lo]) * (key - lo) / (hi - lo)


def _run_watch_frame(fn, *, keys, prev_time, time) -> list[float]:
    """Compile `fn` as a watch-mode callback, run one frame at every optimization level, and return the log.

    Neither oracle helper fits: stream reads are rejected outside watch mode and conftest's `compile_fn` always
    compiles at `Mode.PLAY`, so the snippet cannot run through `run_and_validate` or `run_compiled`, nor as plain
    Python. Running at every optimization level here keeps the cross-check those helpers provide.
    """
    logs = []
    for passes in optimization_levels:
        clear_frontend_caches()
        project_state = ProjectContextState(runtime_checks=RuntimeChecks.NONE)
        cfg = callback_to_cfg(project_state, ModeContextState(Mode.WATCH), fn, "updateSequential")
        entry = cfg_to_engine_node(run_passes(cfg, passes, OptimizerConfig()))
        interpreter = _StreamInterpreter(keys)
        interpreter.blocks[int(WatchBlock.EngineRom)] = project_state.rom.values
        # WatchBlock.RuntimeUpdate holds time, delta_time, scaled_time, and is_skip in that order.
        # prev_time() is time() - delta_time().
        interpreter.blocks[int(WatchBlock.RuntimeUpdate)] = [time, time - prev_time, time, 0.0]
        interpreter.run(entry)
        logs.append(interpreter.log)
    assert all(log == logs[0] for log in logs), f"Logs differ between optimization levels: {logs}"
    return logs[0]


def _log_keys_since_previous_frame():
    for key in Stream[Num](_STREAM_ID).iter_keys_since_previous_frame():
        debug_log(key)


def _log_values_since_previous_frame():
    for value in Stream[Num](_STREAM_ID).iter_values_since_previous_frame():
        debug_log(value)


def _log_items_since_previous_frame():
    for key, value in Stream[Num](_STREAM_ID).iter_items_since_previous_frame():
        debug_log(key)
        debug_log(value)


_SINCE_PREVIOUS_FRAME_VARIANTS = {
    "keys": (_log_keys_since_previous_frame, lambda keys: [float(key) for key in keys]),
    "values": (_log_values_since_previous_frame, lambda keys: [_value_of(key) for key in keys]),
    "items": (
        _log_items_since_previous_frame,
        lambda keys: [entry for key in keys for entry in (float(key), _value_of(key))],
    ),
}


@pytest.mark.parametrize("variant", list(_SINCE_PREVIOUS_FRAME_VARIANTS))
@pytest.mark.parametrize(
    ("stream_keys", "prev_time", "time", "expected_keys"),
    [
        ((10, 20, 30), 30, 35, ()),
        ((10, 20, 30), 30, 30, ()),
        ((10, 20, 30), 20, 30, (30,)),
        ((10, 20, 30), 15, 20, (20,)),
        ((10, 20, 30), 20, 25, ()),
        ((10, 20, 30), 31, 35, ()),
        ((10, 20, 30), 5, 30, (10, 20, 30)),
        ((), 30, 35, ()),
    ],
    ids=[
        "prev-time-on-last-key",
        "paused-frame-on-last-key",
        "prev-time-on-interior-key",
        "key-at-current-time",
        "next-key-beyond-current-time",
        "prev-time-past-last-key",
        "several-keys-in-window",
        "empty-stream",
    ],
)
def test_since_previous_frame_yields_the_documented_window(variant, stream_keys, prev_time, time, expected_keys):
    # The window documented on all three methods is exclusive at prev_time and inclusive at time.
    fn, expected_log = _SINCE_PREVIOUS_FRAME_VARIANTS[variant]
    assert _run_watch_frame(fn, keys=stream_keys, prev_time=prev_time, time=time) == expected_log(expected_keys)


def test_since_previous_frame_does_not_repeat_the_last_key_on_the_next_frame():
    keys = (10, 20, 30)
    assert _run_watch_frame(_log_keys_since_previous_frame, keys=keys, prev_time=25, time=30) == [30.0]
    assert _run_watch_frame(_log_keys_since_previous_frame, keys=keys, prev_time=30, time=35) == []


def _log_keys_from_desc():
    for key in Stream[Num](_STREAM_ID).iter_keys_from_desc(time()):
        debug_log(key)


def _log_values_from_desc():
    for value in Stream[Num](_STREAM_ID).iter_values_from_desc(time()):
        debug_log(value)


def _log_items_from_desc():
    for key, value in Stream[Num](_STREAM_ID).iter_items_from_desc(time()):
        debug_log(key)
        debug_log(value)


_FROM_DESC_VARIANTS = {
    "keys": (_log_keys_from_desc, lambda keys: [float(key) for key in keys]),
    "values": (_log_values_from_desc, lambda keys: [_value_of(key) for key in keys]),
    "items": (
        _log_items_from_desc,
        lambda keys: [entry for key in keys for entry in (float(key), _value_of(key))],
    ),
}


@pytest.mark.parametrize("variant", list(_FROM_DESC_VARIANTS))
@pytest.mark.parametrize(
    ("stream_keys", "start", "expected_keys"),
    [
        ((10, 20, 30), 35, (30, 20, 10)),
        ((10, 20, 30), 30, (30, 20, 10)),
        ((10, 20, 30), 25, (20, 10)),
        ((10, 20, 30), 20, (20, 10)),
        ((10, 20, 30), 10, (10,)),
        ((10, 20, 30), 5, ()),
        ((), 10, ()),
    ],
    ids=[
        "start-beyond-last-key",
        "start-at-last-key-inclusive",
        "start-strictly-between-keys",
        "start-at-interior-key-inclusive",
        "start-at-first-key-nothing-precedes",
        "start-before-first-key",
        "empty-stream",
    ],
)
def test_iter_from_desc_visits_keys_strictly_descending(variant, stream_keys, start, expected_keys):
    # Iteration begins at previous_key_inclusive(start) and stops once nothing precedes the current key, so it
    # visits every stream key <= start in strictly descending order.
    fn, expected_log = _FROM_DESC_VARIANTS[variant]
    assert _run_watch_frame(fn, keys=stream_keys, prev_time=start, time=start) == expected_log(expected_keys)


def _log_previous_key_or_default():
    debug_log(Stream[Num](_STREAM_ID).previous_key_or_default(time(), -1))


@pytest.mark.parametrize(
    ("stream_keys", "start", "expected"),
    [
        ((10, 20, 30), 35, 30),
        ((10, 20, 30), 20, 10),
        ((10, 20, 30), 10, -1),
        ((10, 20, 30), 5, -1),
        ((), 10, -1),
    ],
    ids=[
        "previous-key-exists",
        "previous-key-exists-at-interior-key",
        "first-key-nothing-precedes",
        "below-first-key-nothing-precedes",
        "empty-stream",
    ],
)
def test_previous_key_or_default_returns_default_when_nothing_precedes(stream_keys, start, expected):
    assert _run_watch_frame(_log_previous_key_or_default, keys=stream_keys, prev_time=start, time=start) == [
        float(expected)
    ]
