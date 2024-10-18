class InternalError(RuntimeError):
    """Represents an error occurring due to a violation of an internal invariant.

    This indicates there is a bug in sonolus.py, or that internal details have been used incorrectly.
    """

    def __init__(self, message: str):
        super().__init__(message)


class CompilationError(RuntimeError):
    """Represents an error occurring during the compilation of a script."""

    def __init__(self, message: str):
        super().__init__(message)
