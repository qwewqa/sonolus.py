from abc import abstractmethod


class SonolusDescriptor:
    """Base class for Sonolus descriptors.

    The compiler checks if a descriptor is an instance of a subclass of this class,
    so it knows that it's a supported descriptor.

    `__get__` must not raise `AttributeError`. The compiler cannot pass that error to a traced
    `__getattr__` method because the descriptor has already run in host Python.
    """

    @abstractmethod
    def __get__(self, instance, owner):
        """Return the descriptor value."""
        raise NotImplementedError

    @abstractmethod
    def __set__(self, instance, value):
        """Set the descriptor value."""
        raise NotImplementedError
