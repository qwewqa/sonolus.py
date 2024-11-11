from dataclasses import dataclass


@dataclass
class CallbackInfo:
    name: str
    py_name: str
    supports_order: bool
    returns_value: bool


preprocess_callback = CallbackInfo(
    name="preprocess",
    py_name="preprocess",
    supports_order=True,
    returns_value=False,
)
spawn_order_callback = CallbackInfo(
    name="spawnOrder",
    py_name="spawn_order",
    supports_order=True,
    returns_value=True,
)
should_spawn_callback = CallbackInfo(
    name="shouldSpawn",
    py_name="should_spawn",
    supports_order=False,
    returns_value=True,
)
initialize_callback = CallbackInfo(
    name="initialize",
    py_name="initialize",
    supports_order=False,
    returns_value=False,
)
update_sequential_callback = CallbackInfo(
    name="updateSequential",
    py_name="update_sequential",
    supports_order=True,
    returns_value=False,
)
touch_callback = CallbackInfo(
    name="touch",
    py_name="touch",
    supports_order=True,
    returns_value=False,
)
update_parallel_callback = CallbackInfo(
    name="updateParallel",
    py_name="update_parallel",
    supports_order=False,
    returns_value=False,
)
terminate_callback = CallbackInfo(
    name="terminate",
    py_name="terminate",
    supports_order=False,
    returns_value=False,
)
spawn_time_callback = CallbackInfo(
    name="spawnTime",
    py_name="spawn_time",
    supports_order=False,
    returns_value=True,
)
despawn_time_callback = CallbackInfo(
    name="despawnTime",
    py_name="despawn_time",
    supports_order=False,
    returns_value=True,
)
update_spawn_callback = CallbackInfo(
    name="updateSpawn",
    py_name="update_spawn",
    supports_order=False,
    returns_value=True,
)
render_callback = CallbackInfo(
    name="render",
    py_name="render",
    supports_order=False,
    returns_value=False,
)
navigate_callback = CallbackInfo(
    name="navigate",
    py_name="navigate",
    supports_order=False,
    returns_value=False,
)
update_callback = CallbackInfo(
    name="update",
    py_name="update",
    supports_order=False,
    returns_value=False,
)


def _by_name(*callbacks: CallbackInfo) -> dict[str, CallbackInfo]:
    return {cb.py_name: cb for cb in callbacks}


PLAY_CALLBACKS = _by_name(
    preprocess_callback,
    spawn_order_callback,
    should_spawn_callback,
    initialize_callback,
    update_sequential_callback,
    touch_callback,
    update_parallel_callback,
    terminate_callback,
)
WATCH_ARCHETYPE_CALLBACKS = _by_name(
    preprocess_callback,
    spawn_time_callback,
    despawn_time_callback,
    initialize_callback,
    update_sequential_callback,
    update_parallel_callback,
    terminate_callback,
)
WATCH_GLOBAL_CALLBACKS = _by_name(
    update_spawn_callback,
)
PREVIEW_CALLBACKS = _by_name(
    preprocess_callback,
    render_callback,
)
