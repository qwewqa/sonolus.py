from typing import Never

def assert_never(arg: Never, /) -> Never:
    """Ask a static type checker to confirm that a line of code is unreachable."""
