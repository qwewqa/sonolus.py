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

PLAY_CALLBACKS = {
    cb.py_name: cb
    for cb in [
        preprocess_callback,
        spawn_order_callback,
        should_spawn_callback,
        initialize_callback,
        update_sequential_callback,
        touch_callback,
        update_parallel_callback,
        terminate_callback,
    ]
}
