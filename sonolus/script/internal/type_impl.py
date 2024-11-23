from sonolus.script.internal.constant import ConstantValue
from sonolus.script.internal.impl import meta_fn
from sonolus.script.internal.value import Value


class TypeImpl(ConstantValue):
    @meta_fn
    def __call__(self, *args, **kwargs):
        if not issubclass(self.value, Value):
            raise TypeError(f"Instantiating {self.value.__name__} is not supported")
        if not self.value._is_concrete_():
            raise TypeError(f"Instantiating abstract type {self.value.__name__} is not supported")
        return self.value(*args, **kwargs)
