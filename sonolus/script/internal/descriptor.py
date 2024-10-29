from abc import abstractmethod


class SonolusDescriptor:
    """Base class for Sonolus descriptors.

    The compiler checks if a descriptor is an instance of a subclass of this class,
    so it knows that it's a supported descriptor.
    """

    @abstractmethod
    def __get__(self, instance, owner):
        pass

    @abstractmethod
    def __set__(self, instance, value):
        pass
