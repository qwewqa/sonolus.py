class Num(int, bool, float):
    """Common type for numbers and booleans.

    `int`, `float`, and `bool` are all treated as `Num` and are interchangeable. Prefer the more specific built-in
    type for clarity in most code; `Num` is mainly needed for instance checks (`isinstance` or `match` patterns),
    where it is the only supported way to check for a numeric or boolean value.

    The Sonolus app uses 32-bit floating-point numbers for all numeric values, so precision may be lower than in
    Python. NaN and values outside the range of 32-bit floating-point numbers are not supported.

    See the relevant [concepts page](../concepts/types.md#num) for more information.
    """
