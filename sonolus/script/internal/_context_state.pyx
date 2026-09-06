cdef object _current_context = None


def ctx():
    return _current_context


def set_ctx(value):
    global _current_context
    old_value = _current_context
    _current_context = value
    return old_value
