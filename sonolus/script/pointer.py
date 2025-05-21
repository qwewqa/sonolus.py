from sonolus.backend.place import BlockPlace
from sonolus.script.internal.context import ctx
from sonolus.script.internal.impl import meta_fn, validate_value
from sonolus.script.internal.value import BackingSource, Value
from sonolus.script.num import Num, _is_num


@meta_fn
def _deref[T: Value](block: Num, offset: Num, type_: type[T]) -> T:
    block = Num._accept_(block)
    offset = Num._accept_(offset)
    type_ = validate_value(type_)._as_py_()
    if block._is_py_():
        block = block._as_py_()
        if not isinstance(block, int):
            raise TypeError("block must be an integer")
        block = ctx().blocks(block)
    else:
        if not _is_num(block):
            raise TypeError("block must be a Num")
        block = block.index()
    if offset._is_py_():
        offset = offset._as_py_()
        if not isinstance(offset, int):
            raise TypeError("offset must be an integer")
    else:
        if not _is_num(offset):
            raise TypeError("offset must be a Num")
        offset = offset.index()
    if not (isinstance(type_, type) and issubclass(type_, Value)):
        raise TypeError("type_ must be a Value")
    return type_._from_place_(BlockPlace(block, offset))


@meta_fn
def _backing_deref[T: Value](source: BackingSource, type_: type[T]) -> T:
    type_ = validate_value(type_)._as_py_()
    if not isinstance(type_, type) or not issubclass(type_, Value):
        raise TypeError("type_ must be a Value")
    return type_._from_backing_source_(source)
