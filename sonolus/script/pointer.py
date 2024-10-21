from sonolus.backend.place import BlockPlace
from sonolus.script.internal.value import Value


def static_deref[T: Value](block: int, offset: int, type_: type[T]) -> T:
    return type_._from_place_(BlockPlace(block, offset))
