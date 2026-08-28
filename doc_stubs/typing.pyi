from typing import Any, Never, overload

@overload
def cast[T](typ: type[T], val: Any) -> T: ...
@overload
def cast(typ: str, val: Any) -> Any: ...
@overload
def cast(typ: object, val: Any) -> Any: ...
def cast(typ: object, val: Any) -> Any:
    """Cast a value to a type."""

def assert_type[T](val: T, typ: Any, /) -> T:
    """Ask a type checker to verify the inferred type of a value."""

def assert_never(arg: Never, /) -> Never:
    """Assert that this line of code is unreachable."""
