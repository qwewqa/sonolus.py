def simple_meta_fn[T](fn: T) -> T:
    fn._meta_fn_ = True
    return fn
