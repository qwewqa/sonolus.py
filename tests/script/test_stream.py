from typing import Annotated, ClassVar, Final

import pytest

from sonolus.backend.blocks import PlayBlock, WatchBlock
from sonolus.backend.interpret import Interpreter
from sonolus.backend.mode import Mode
from sonolus.backend.node import EngineNode, FunctionNode
from sonolus.backend.ops import Op
from sonolus.backend.optimize import OptimizerConfig, cfg_to_engine_node, run_passes
from sonolus.build.compile import callback_to_cfg
from sonolus.script.debug import debug_log
from sonolus.script.internal.context import ModeContextState, ProjectContextState, RuntimeChecks
from sonolus.script.internal.error import CompilationError
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


def _init_through_build_path(strms):
    """Initialize `strms` the way an engine author reaches it: by compiling a callback that touches it.

    `callback_to_cfg` traces a failing callback a second time with `no_eval=False` and reports the second trace's
    error, so a build initializes a streams class twice whenever the first attempt fails.
    """

    def fn():
        strms.good[0] = 1

    clear_frontend_caches()
    project_state = ProjectContextState(runtime_checks=RuntimeChecks.NONE)
    callback_to_cfg(project_state, ModeContextState(Mode.PLAY), fn, "updateSequential")


def _assert_streams_rejected(strms, field, message):
    """Assert that a repeated `_init_()` and a build both blame `field` for `message`, not just the first `_init_()`.

    The offending field must follow a well-formed `good: Stream[int]`, which is also what the callback writes to.
    A mistake in the first field is reported before any descriptor is set on the class, so it would satisfy these
    assertions without a repeat call ever reaching one.
    """
    pattern = rf"Error processing streams field '{field}'.*{message}"
    for _ in range(2):
        with pytest.raises(TypeError, match=pattern):
            type(strms)._init_()
    with pytest.raises(CompilationError, match=pattern):
        _init_through_build_path(strms)


def test_streams_rejects_final_stream():
    # Final[Stream[...]] normalizes back to Stream[...], which must not be accepted as a data field.
    @streams
    class Strms:
        good: Stream[int]
        bad: Final[Stream[int]]

    _assert_streams_rejected(Strms, "bad", "not supported as a streams data field")


def test_streams_rejects_final_stream_group():
    @streams
    class Strms:
        good: Stream[int]
        bad: Final[StreamGroup[int, 10]]

    _assert_streams_rejected(Strms, "bad", "not supported as a streams data field")


def test_streams_reports_a_real_default_value():
    @streams
    class Strms:
        good: Stream[int]
        bad: Stream[int] = 5

    _assert_streams_rejected(Strms, "bad", "Default values are not supported for streams fields")


def test_streams_does_not_report_annotated_as_a_default_value():
    # A class value arrives as Annotated[...], and so does a user-written Annotated[...] with no default at all.
    @streams
    class Strms:
        good: Stream[int]
        bad: Annotated[Stream[int], "doc"]

    _assert_streams_rejected(Strms, "bad", "not supported as a streams data field")


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

    _assert_streams_rejected(Strms, "offender", message)


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


def test_streams_init_is_idempotent():
    @streams
    class Strms:
        stream: Stream[int]
        data: int

    cls = type(Strms)
    cls._init_()
    first = list(cls._streams_)
    cls._init_()
    cls._init_()

    assert cls._streams_ == first


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


def _log_keys_from():
    for key in Stream[Num](_STREAM_ID).iter_keys_from(time()):
        debug_log(key)


def _log_values_from():
    for value in Stream[Num](_STREAM_ID).iter_values_from(time()):
        debug_log(value)


def _log_items_from():
    for key, value in Stream[Num](_STREAM_ID).iter_items_from(time()):
        debug_log(key)
        debug_log(value)


_FROM_ASC_VARIANTS = {
    "keys": (_log_keys_from, lambda keys: [float(key) for key in keys]),
    "values": (_log_values_from, lambda keys: [_value_of(key) for key in keys]),
    "items": (
        _log_items_from,
        lambda keys: [entry for key in keys for entry in (float(key), _value_of(key))],
    ),
}


@pytest.mark.parametrize("variant", list(_FROM_ASC_VARIANTS))
@pytest.mark.parametrize(
    ("stream_keys", "start", "expected_keys"),
    [
        ((10, 20, 30), 5, (10, 20, 30)),
        ((10, 20, 30), 10, (10, 20, 30)),
        ((10, 20, 30), 15, (20, 30)),
        ((10, 20, 30), 20, (20, 30)),
        ((10, 20, 30), 30, (30,)),
        ((10, 20, 30), 35, ()),
        ((), 10, ()),
    ],
    ids=[
        "start-before-first-key",
        "start-at-first-key-inclusive",
        "start-strictly-between-keys",
        "start-at-interior-key-inclusive",
        "start-at-last-key-nothing-follows",
        "start-beyond-last-key",
        "empty-stream",
    ],
)
def test_iter_from_visits_keys_strictly_ascending(variant, stream_keys, start, expected_keys):
    # The ascending mirror of the descending tests above: iteration begins at next_key_inclusive(start) and
    # stops once nothing follows the current key, so it visits every stream key >= start in ascending order.
    fn, expected_log = _FROM_ASC_VARIANTS[variant]
    assert _run_watch_frame(fn, keys=stream_keys, prev_time=start, time=start) == expected_log(expected_keys)


def _log_ascending_queries():
    s = Stream[Num](_STREAM_ID)
    debug_log(s.has_next_key(5))
    debug_log(s.has_next_key(30))
    debug_log(s.has_previous_key(30))
    debug_log(s.has_previous_key(10))
    debug_log(s.next_key_inclusive(15))
    debug_log(s.next_key_inclusive(20))
    debug_log(s.get_next(15))
    debug_log(s.get_next(30))
    debug_log(s.get_previous(15))
    debug_log(s.get_previous(10))
    debug_log(s.get_next_inclusive(20))
    debug_log(s.get_next_inclusive(15))
    debug_log(s.get_previous_inclusive(20))
    debug_log(s.get_previous_inclusive(15))


def test_ascending_queries_against_hand_computed_expectations():
    # Stream keys 10/20/30 with value 10*key. Each pair of lines is a member and its boundary case, expected
    # by hand so that an inverted comparison direction or a next/previous swap fails by name:
    # has_next_key(5)=1 / has_next_key(30)=0; has_previous_key(30)=1 / has_previous_key(10)=0;
    # next_key_inclusive(15)=20 / (20)=20; get_next(15)=200 / (30)=300 (no next key returns the key's value);
    # get_previous(15)=100 / (10)=100; get_next_inclusive(20)=200 / (15)=200;
    # get_previous_inclusive(20)=200 / (15)=100.
    assert _run_watch_frame(_log_ascending_queries, keys=(10, 20, 30), prev_time=0, time=0) == [
        1.0,
        0.0,
        1.0,
        0.0,
        20.0,
        20.0,
        200.0,
        300.0,
        100.0,
        100.0,
        200.0,
        200.0,
        200.0,
        100.0,
    ]


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


class _MultiStreamInterpreter(Interpreter):
    """Interpreter backed by {stream_id: {key: value}}, modeling all five stream ops.

    `_StreamInterpreter` above holds a single stream with derived values; stream groups and @streams data
    fields spread across several stream ids, and the data-field tests replay recorded writes, so this class
    keys by id and models `Op.StreamSet` as well. Op contracts are the same as `_StreamInterpreter`'s.
    """

    def __init__(self, streams_data=None):
        super().__init__()
        self.streams = {int(sid): dict(entries) for sid, entries in (streams_data or {}).items()}
        self.writes = []

    def run(self, node: EngineNode) -> float:
        if isinstance(node, FunctionNode):
            match node.func:
                case Op.StreamSet:
                    sid, key, value = (self.run(arg) for arg in node.args)
                    self.writes.append((sid, key, value))
                    self.streams.setdefault(int(sid), {})[key] = value
                    return 0.0
                case Op.StreamHas:
                    sid, key = (self.run(arg) for arg in node.args)
                    return 1.0 if key in self.streams.get(int(sid), {}) else 0.0
                case Op.StreamGetNextKey:
                    sid, key = (self.run(arg) for arg in node.args)
                    later = [k for k in sorted(self.streams.get(int(sid), {})) if k > key]
                    return later[0] if later else key
                case Op.StreamGetPreviousKey:
                    sid, key = (self.run(arg) for arg in node.args)
                    earlier = [k for k in sorted(self.streams.get(int(sid), {})) if k < key]
                    return earlier[-1] if earlier else key
                case Op.StreamGetValue:
                    sid, key = (self.run(arg) for arg in node.args)
                    return self._value_at(int(sid), key)
        return super().run(node)

    def _value_at(self, sid: int, key: float) -> float:
        data = self.streams.get(sid, {})
        if not data:
            return 0.0
        if key in data:
            return data[key]
        keys = sorted(data)
        earlier = [k for k in keys if k < key]
        later = [k for k in keys if k > key]
        if not earlier:
            return data[later[0]]
        if not later:
            return data[earlier[-1]]
        lo, hi = earlier[-1], later[0]
        return data[lo] + (data[hi] - data[lo]) * (key - lo) / (hi - lo)


def _run_frame(fn, mode, *, streams_data=None):
    """Compile `fn` as a callback of `mode`, run one frame at every optimization level, and return the oracle.

    The `_run_watch_frame` rationale applies (stream access needs the mode's compile context, so neither
    conftest oracle fits); this variant also runs in play mode and returns (log, writes, streams) so write
    tests can assert the emitted `StreamSet` triples and replay them into a later frame.
    """
    results = []
    block = PlayBlock if mode is Mode.PLAY else WatchBlock
    for passes in optimization_levels:
        clear_frontend_caches()
        project_state = ProjectContextState(runtime_checks=RuntimeChecks.NONE)
        cfg = callback_to_cfg(project_state, ModeContextState(mode), fn, "updateSequential")
        entry = cfg_to_engine_node(run_passes(cfg, passes, OptimizerConfig()))
        interpreter = _MultiStreamInterpreter(streams_data)
        interpreter.blocks[int(block.EngineRom)] = project_state.rom.values
        interpreter.blocks[int(block.RuntimeUpdate)] = [0.0, 0.0, 0.0, 0.0]
        interpreter.run(entry)
        results.append((interpreter.log, interpreter.writes, interpreter.streams))
    assert all(result == results[0] for result in results), f"Levels disagree: {results}"
    return results[0]


@streams
class _DataStreams:
    events: Stream[int]  # backing stream 1
    group: StreamGroup[int, 3]  # backing streams 2-4
    flag: int  # data stream 5, element keys 0, +/-0.5 sentinels
    where: Vec2  # data stream 6, element keys 0 and 1
    trio: Vec2  # data stream 7, never written


type(_DataStreams)._init_()


def _write_data_fields():
    _DataStreams.flag = 7
    _DataStreams.where = Vec2(1.5, -2.5)


def _read_data_fields():
    debug_log(_DataStreams.flag)
    debug_log(_DataStreams.where.x)
    debug_log(_DataStreams.where.y)
    debug_log(_DataStreams.trio.x)
    debug_log(_DataStreams.trio.y)


def test_data_field_write_emits_value_and_sentinel_triples():
    # One stream per data field, element i at key i, and each element write brackets its key with zeros at
    # i - 0.5 and i + 0.5. The sentinels are what keep an unwritten element of the same field reading 0
    # instead of interpolating to its written neighbour, so the shape is asserted, not just the round trip.
    _, writes, _ = _run_frame(_write_data_fields, Mode.PLAY)
    assert writes == [
        (5.0, 0.0, 7.0),
        (5.0, -0.5, 0.0),
        (5.0, 0.5, 0.0),
        (6.0, 0.0, 1.5),
        (6.0, -0.5, 0.0),
        (6.0, 0.5, 0.0),
        (6.0, 1.0, -2.5),
        (6.0, 0.5, 0.0),
        (6.0, 1.5, 0.0),
    ]


def test_data_fields_round_trip_from_play_to_watch():
    _, _, written_streams = _run_frame(_write_data_fields, Mode.PLAY)
    log, _, _ = _run_frame(_read_data_fields, Mode.WATCH, streams_data=written_streams)
    # flag and where read back as written; the never-written trio reads zeros because its stream is empty.
    assert log == [7.0, 1.5, -2.5, 0.0, 0.0]


def test_data_field_unwritten_element_reads_zero_because_of_the_sentinels():
    # A field stream holding only element 0 (its value key plus the two sentinel zeros): the state a write
    # of a one-element field leaves, replayed onto `where`'s stream so element 1 exists but was never
    # written. StreamGetValue interpolates between surrounding keys, so without the sentinels the read at
    # key 1 would extrapolate element 0's value; the flanking zeros are what make it read 0.
    element_zero_only = {6: {0.0: 1.5, -0.5: 0.0, 0.5: 0.0}}

    def read_where():
        debug_log(_DataStreams.where.x)
        debug_log(_DataStreams.where.y)

    log, _, _ = _run_frame(read_where, Mode.WATCH, streams_data=element_zero_only)
    assert log == [1.5, 0.0]

    # Detection-power guard: with the sentinels stripped from the same state, the model extrapolates, so the
    # 0.0 above is genuinely the sentinels' doing rather than the model defaulting.
    log, _, _ = _run_frame(read_where, Mode.WATCH, streams_data={6: {0.0: 1.5}})
    assert log == [1.5, 1.5]


def _write_element_through_getitem():
    Stream[Vec2](1)[10].y = 5.0


def test_stream_element_write_through_getitem_uses_the_backing_stream():
    # Stream.__setitem__ emits StreamSet directly; mutating the value __getitem__ hands back is the only
    # route through the backing's write path. A Vec2 stream at offset 1 backs x with stream 1 and y with
    # stream 2, so the write must land in stream 2 at the key, with no sentinel writes.
    _, writes, _ = _run_frame(_write_element_through_getitem, Mode.PLAY)
    assert writes == [(2.0, 10.0, 5.0)]


def _log_group_contains():
    group = StreamGroup[Num, 3](1)
    debug_log(-1 in group)
    debug_log(0 in group)
    debug_log(2 in group)
    debug_log(3 in group)


def test_stream_group_contains_boundaries():
    log, _, _ = _run_frame(_log_group_contains, Mode.WATCH)
    assert log == [0.0, 1.0, 1.0, 0.0]


def _log_group_getitem_num():
    group = StreamGroup[Num, 3](1)
    debug_log(group[0][20])
    debug_log(group[1][20])
    debug_log(group[2][20])


def test_stream_group_getitem_maps_each_index_to_its_own_stream():
    # A Num group at offset 1 backs index i with stream i + 1; distinct seeded values make a wrong stride or
    # base visible in the number itself.
    seeded = {1: {20.0: 111.0}, 2: {20.0: 222.0}, 3: {20.0: 333.0}}
    log, _, _ = _run_frame(_log_group_getitem_num, Mode.WATCH, streams_data=seeded)
    assert log == [111.0, 222.0, 333.0]


def _log_group_getitem_vec():
    group = StreamGroup[Vec2, 2](5)
    debug_log(group[1][20].x)
    debug_log(group[1][20].y)


def test_stream_group_getitem_strides_by_element_size():
    # A Vec2 group at offset 5 backs index i with streams 2 * i + 5 and 2 * i + 6, so index 1 reads x from
    # stream 7 and y from stream 8.
    seeded = {7: {20.0: 1.5}, 8: {20.0: -2.5}}
    log, _, _ = _run_frame(_log_group_getitem_vec, Mode.WATCH, streams_data=seeded)
    assert log == [1.5, -2.5]
