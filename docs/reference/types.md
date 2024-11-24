# Types
Sonolus.py compes with support for a number of Python built-in types and some custom types.

## Num

`Num` is the numeric and boolean type in Sonolus.py. It is interchangeable with `int`, `float`, and `bool`.
Sonolus.py will treat any of these types as `Num`, but it's recommended to use what's appropriate for clarity.

The Sonolus app uses 32-bit floating-point numbers for all numeric values, so precision may be lower compared to Python
when running on Sonolus.

Infinity, NaN, and values outside the range of 32-bit floating-point numbers are not supported.

You can import `Num` from `sonolus.script.num`:

```python
from sonolus.script.num import Num
```

### Declaration
Instances of `Num` can be declared using standard Python syntax.

```python
a = 1
b = 2.5
c = True
```

### Operations
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

### Instance Checks
Since `Num` is interchangeable with `int`, `float`, and `bool`, only `Num` is supported for type checks.

```python
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

### Conversion
Calling `int`, `float`, or `bool` is only supported for an argument of type `Num`.

Details:

- `int`: Equivalent to `math.trunc`.
- `float`: Validates that the value is a `Num` and returns it as is.
- `bool`: Validates that the value is a `Num` and returns `1` for `True` and `0` for `False`.

## Array

`Array[T, Size]` stores a fixed number of elements of the same type.

It has two type parameters:
- `T`: The type of the elements.
- `Size`: The number of elements.

You can import `Array` from `sonolus.script.array`:

```python
from sonolus.script.array import Array
```

### Declaration

Arrays can be created using its constructor:

```python
a1 = Array[int, 3](1, 2, 3)
a2 = Array[int, 0]()
```

If at least one element is provided, the element type and size can be inferred:

```python
a3 = Array(1, 2, 3)
```

The element type must be concrete (not generic) and the size must be a non-negative integer:

```python
# Ok
a4 = Array[Array[int, 3], 2](Array(1, 2, 3), Array(4, 5, 6))

# Not ok:
a5 = Array[int, 0.5]()  # The size must be a non-negative integer
a6 = Array[Array, 2](Array(1, 2, 3), Array(4, 5, 6))  # The element type must be concrete (not generic)
```

Copies are made of any values provided to the constructor:

```python
pair = Pair(1, 2)
a = Array[Pair, 1](pair)
assert a[0] == Pair(1, 2)

pair.x = 3
assert a[0] == Pair(1, 2)  # The value in the array is independent of the original value
```

### Operations

Copying the value from one array to another using the copy from operator (`@=`)[^1]:

```python
source_array = Array(1, 2, 3)
destination_array = Array(0, 0, 0)

destination_array @= source_array
assert destination_array == Array(1, 2, 3)
```

Comparing arrays for equality:

```python
assert Array(1, 2, 3) == Array(1, 2, 3)
assert Array(1, 2, 3) != Array(4, 5, 6)
```

Accessing elements:

```python
a = Array(1, 2, 3)
assert a[0] == 1
assert a[1] == 2
assert a[2] == 3
```

Updating elements:

```python
a = Array(1, 2, 3)
a[0] = 4
assert a == Array(4, 2, 3)
```

!!! warning
    If a value in an array is not a `Num`, updating it will copy the given value into the corresponding element
    of the array. However, that element remains independent of the original value.

    ```python
    pair = Pair(1, 2)
    a = Array(Pair(0, 0))
    
    a[0] = pair  # or equivalently: a[0] @= pair
    assert a[0] == Pair(1, 2)

    pair.x = 3
    assert a[0] == Pair(1, 2)  # The value in the array is independent of the original value
    ```
    
    For clarity, it's recommended to use the copy from operator (`@=`) when updating elements that are known to be
    of some type other than `Num`.

    ```python
    a[0] @= pair
    ```

Getting the length of an array:

```python
assert len(Array(1, 2, 3)) == 3
```

Iterating over elements:

```python
a = Array(1, 2, 3)

for element in a:
    debug_log(element)
```

Other functionality:

Array inherits from [ArrayLike][sonolus.script.array_like.ArrayLike] and supports all of its methods.

### Instance Checks

Any array is considered an instance of the generic `Array` type.

```python
a = Array(1, 2, 3)
assert isinstance(a, Array)
```

Only an array with the exact element type and size is considered an instance of a concrete `Array[T, Size]` type.

```python
a = Array(1, 2, 3)
assert isinstance(a, Array[int, 3])
assert not isinstance(a, Array[int, 2])
assert not isinstance(a, Array[Pair, 3])
```

## Record

## Transient Types
In addition to the standard types, the following transient types are available.
Compared to the standard types, these types come with the restriction that they cannot be used as type parameters
or as a Record field's type.

[^1]:
    The copy from operator (`@=`) is officially the in-place matrix multiplication operator in Python,
    but it has been repurposed in Sonolus.py for copying Arrays and Records.
