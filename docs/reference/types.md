# Types
Sonolus.py compes with support for a number of Python built-in types and some custom types.

## Standard Types

### Num

`Num` is the numeric and boolean type in Sonolus.py. It is interchangeable with `int`, `float`, and `bool`.
Sonolus.py will treat any of these types as `Num`, but it's recommended to use what's appropriate for clarity.

Sonolus uses 32-bit floating-point numbers for all numeric values, so precision may be lower compared to Python
when running on Sonolus.

Infinity, NaN, and values outside the range of 32-bit floating-point numbers are not supported.

You can import `Num` from `sonolus.script.num`:

```python
from sonolus.script.num import Num
```

#### Declaration
Instances of `Num` can be declared using standard Python syntax.

```python
a = 1
b = 2.5
c = True
```

#### Operations
`Num` supports most of the standard Python operations:

- Comparison operators: `==`, `!=`, `<`, `>`, `<=`, `>=`
- Arithmetic operators: `+`, `-`, `*`, `/`, `//`, `%`, `**`
- Unary operators: `+`, `-`

`Num` is also the only supported type for boolean operations and control flow conditions.
Any nonzero value is considered `True`, and `0` is considered `False`.

- Logical operators: `and`, `or`, `not`
- Ternary expressions: `... if <condition> else ...`
- If statements: `if <condition>:`, `elif <condition>:`
- While loops: `while <condition>:`
- Case guards: `case ... if <condition>:`

#### Instance Checks
Since `Num` is interchangeable with `int`, `float`, and `bool`, only `Num` is supported for type checks.

```python
from sonolus.script.num import Num


x = ...

# Ok:
isinstance(x, Num)

match x:
    case Num(value):
        ...

# Not ok:
isinstance(x, int)
isinstance(x, float)
isinstance(x, bool)

match x:
    case int(value):
        ...
    case float(value):
        ...
    case bool(value):
        ...
```

#### Conversion
Calling `int`, `float`, or `bool` is only supported for an argument of type `Num`.

Details:

- `int`: Equivalent to `math.trunc`.
- `float`: Validates that the value is a `Num` and returns it as is.
- `bool`: Validates that the value is a `Num` and returns `1` for `True` and `0` for `False`.

### Array

### Record

## Transient Types
