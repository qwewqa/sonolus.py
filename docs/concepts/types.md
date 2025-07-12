# Types
Sonolus.py has 3 core types: [`Num`](#num), [`Array`](#array), and [`Record`](#record). representing numeric values, fixed-size arrays, 
and custom data structures, respectively. Arrays and records can be nested within each other to create complex data
structures.

Additionally, Sonolus.py supports the built-in types `tuple`, `dict`, `str`, classes and functions, and
the constants `None`, `Ellipsis`, and `NotImplemented`.

## Num

`Num` is the numeric and boolean type in Sonolus.py. It is interchangeable with `int`, `float`, and `bool`.
Sonolus.py will treat any of these types as `Num`, but it's recommended to use what's appropriate for clarity.

The Sonolus app uses 32-bit floating-point numbers for all numeric values, so precision may be lower compared to Python
when running on Sonolus.

NaN and values outside the range of 32-bit floating-point numbers are not supported.

You can import `Num` from `sonolus.script.num`:

```python
from sonolus.script.num import Num
```

### Declaration
Nums can be declared using standard Python syntax.

```python
a = 1
b = 2.5
c = True
```

### Operations
Nums support most of the standard Python operations:

- Comparison operators: `==`, `!=`, `<`, `>`, `<=`, `>=`
- Arithmetic operators: `+`, `-`, `*`, `/`, `//`, `%`, `**`
- Unary operators: `+`, `-`

!!! note
    Floating point precision may be lower when running on Sonolus compared to Python.
    Care should be taken when performing precision-sensitive operations.

As in regular Python, `0` is considered `False`, while any non-zero value is considered `True`.

Objects with an explicit `__bool__` method may also be used in `if`, `while`, `case ... if` expressions as well as with
the `not` operator. However, the operands of the `and` and `or` operators must be of type `Num`.

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

[`Array[T, Size]`][sonolus.script.array.Array] stores a fixed number of elements of the same type.

It has two type parameters:

- `T`: The type of the elements.
- `Size`: The number of elements.

You can import [`Array`][sonolus.script.array.Array] from `sonolus.script.array`:

```python
from sonolus.script.array import Array
```

### Declaration

Arrays can be created using its constructor or the unary `+` operator.

```python
a1 = Array[int, 3](1, 2, 3)
a2 = Array[int, 0]()
a3 = +Array[int, 3]  # Create a zero-initialized array
```

If at least one element is provided, the element type and size can be inferred:

```python
a3 = Array(1, 2, 3)
```

Since [`Array`][sonolus.script.array.Array] takes type parameters, it is considered a generic type. A version of [`Array`][sonolus.script.array.Array] with type parameters provided
is considered a concrete type.

```python
Array  # The Generic Array type
Array[int, 3]  # A concrete Array type
```

The element type of an array must be concrete (not generic) and the size must be a non-negative compile-time 
constant integer:

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

An array can be copied with the unary `+` operator, which creates a new array with the same elements:

```python
a = Array(1, 2, 3)
b = +a
assert b == Array(1, 2, 3)
```

The value of an array can be copied from another array using the copy from operator (`@=`)[^1]:

```python
source_array = Array(1, 2, 3)
destination_array = Array(0, 0, 0)

destination_array @= source_array
assert destination_array == Array(1, 2, 3)
```

Arrays can be compared for equality and inequality:

```python
assert Array(1, 2, 3) == Array(1, 2, 3)
assert Array(1, 2, 3) != Array(4, 5, 6)
```

Elements can be accessed by index:

```python
a = Array(1, 2, 3)
assert a[0] == 1
assert a[1] == 2
assert a[2] == 3
```

Elements can be updated by index, copying the given value into the corresponding element of the array:

```python
a = Array(1, 2, 3)
a[0] = 4
assert a == Array(4, 2, 3)
```

!!! warning
    If a value in an array is not a [`Num`](#num), updating it will copy the given value into the corresponding element
    of the array. However, that element remains independent of the original value, which may lead to unexpected
    results when updating either value.

    ```python
    pair = Pair(1, 2)
    a = Array(Pair(0, 0))
    
    a[0] = pair  # or equivalently: a[0] @= pair
    assert a[0] == Pair(1, 2)

    pair.x = 3
    assert a[0] == Pair(1, 2)  # The value in the array is independent of the original value
    ```
    
    For clarity, it's recommended to use the copy from operator (`@=`) when updating elements that are known to be
    an array or record.

    ```python
    a[0] @= pair
    ```

The length of an array can be accessed using the `len()` function:

```python
assert len(Array(1, 2, 3)) == 3
```

Arrays can be iterated over using a for loop:

```python
a = Array(1, 2, 3)

for element in a:
    debug_log(element)
```

Other functionality:

[`Array`][sonolus.script.array.Array] inherits from [`ArrayLike`][sonolus.script.array_like.ArrayLike] and supports all of its methods.

### Instance Checks

Any array is considered an instance of the generic [`Array`][sonolus.script.array.Array] type.

```python
a = Array(1, 2, 3)
assert isinstance(a, Array)
```

Only an array with the exact element type and size is considered an instance of a concrete [`Array[T, Size]`][sonolus.script.array.Array] type.

```python
a = Array(1, 2, 3)
assert isinstance(a, Array[int, 3])
assert not isinstance(a, Array[int, 2])
assert not isinstance(a, Array[Pair, 3])
```

### Enums

There is limited support for enums containing [`Num`](#num) values. Methods on enums are not supported. 
When used as a type, any enum class is treated as [`Num`](#num) and no enforcement is done on the values.

```python
class MyEnum(IntEnum):
    A = 1
    B = 2
    
a = Array[MyEnum, 2](MyEnum.A, MyEnum.B)
b = Array[MyEnum, 2](1, 2)
```

## Record

[`Record`][sonolus.script.record.Record] is the base class for user-defined types in Sonolus.py. It functions similarly to dataclasses.

You can import [`Record`][sonolus.script.record.Record] from `sonolus.script.record`:

```python
from sonolus.script.record import Record
```

### Declaration

A record can be defined by inheriting from [`Record`][sonolus.script.record.Record] and defining zero or more fields as class attributes:

```python
class MyPair(Record):
    first: int
    second: int
```

Fields must be annotated by [`Num`](#num) (or equivalently `int`, `float`, or `bool`), 
a concrete array type, or a concrete record type.

```python
# Not ok:
class MyRecord(Record):
    array: Array  # Array is not concrete since it has unspecified type parameters
```

A [`Record`][sonolus.script.record.Record] subclass cannot be further subclassed.

```python
# Not ok:
class MyPairSubclass(MyPair):
    third: int
```

### Instantiation

A constructor is automatically generated for the [`Record`][sonolus.script.record.Record] class and the unary `+` 
operator can also be used to create a zero-initialized record.

```python
pair_1 = MyPair(1, 2)
pair_2 = MyPair(first=1, second=2)
pair_3 = +MyPair  # Create a zero-initialized record
```

### Generics

[`Record`][sonolus.script.record.Record] supports generics. If at least one type parameter is provided in the class definition, a generic 
record type is created.

```python
class MyGenericPair[T, U](Record):
    first: T
    second: U

class ContainsArray[T, Size](Record):
    array: Array[T, Size]
```

Generic type parameters can be specified explicitly when instantiating a generic or inferred from the provided values:

```python
pair_1 = MyGenericPair[int, int](1, 2)
pair_2 = MyGenericPair(1, 2)
```

The value of a type parameter can be accessed via the [`type_var_value()`][sonolus.script.record.Record.type_var_value] classmethod.
    
```python
class MyGenericRecord[T](Record):
    value: T
    
    def my_type(self) -> type:
        return self.type_var_value(T)

    
assert MyGenericRecord(1).my_type() == Num
```

### Operations

A record can be copied with the unary `+` operator, which creates a new record with the same field values:

```python
pair = MyPair(1, 2)
copy_pair = +pair
assert copy_pair == MyPair(1, 2)
```

The value of a record can be copied from another record using the copy from operator (`@=`)[^1]:

```python
source_record = MyPair(1, 2)
destination_record = MyPair(0, 0)

destination_record @= source_record
assert destination_record == MyPair(1, 2)
```

Records can be compared for equality and inequality:

```python
assert MyPair(1, 2) == MyPair(1, 2)
assert MyPair(1, 2) != MyPair(3, 4)
```

Dunder methods can be implemented to define custom behavior for records:

```python
class MyAddablePair(Record):
    first: int
    second: int
    
    def __add__(self, other: MyAddablePair) -> MyAddablePair:
        return MyAddablePair(self.first + other.first, self.second + other.second)
```

If a dunder method has an in-place variant and the in-place method is not explicitly implemented
(e.g. `__iadd__` is the in-place variant of `__add__`), [`Record`][sonolus.script.record.Record] will automatically generate one that 
modifies the instance in place:

```python
pair = MyAddablePair(1, 2)
reference = pair
pair += MyAddablePair(3, 4)
assert pair == reference == MyAddablePair(4, 6)  # The instance is modified in place
```

Regular methods, properties, classmethods, and staticmethods can also be defined in a [`Record`][sonolus.script.record.Record] subclass.

```python
class MyRecord(Record):
    def my_method(self):
        ...

    @property
    def my_property(self):
        ...

    @property.setter
    def my_property(self, value):
        ...

    @classmethod
    def my_classmethod(cls):
        ...

    @staticmethod
    def my_staticmethod():
        ...
```

Fields can be accessed and updated using the dot operator:

```python
pair = MyPair(1, 2)
assert pair.first == 1
assert pair.second == 2

pair.first = 3
assert pair == MyPair(3, 2)
```

!!! warning
    If a value in a record is not a [`Num`](#num), updating it will copy the given value into the corresponding field
    of the record. However, that field remains independent of the original value.

    ```python
    array = Array(1, 2, 3)
    record = MyRecord(array)
    
    record.array = Array(4, 5, 6)  # or equivalently: record.array @= Array(4, 5, 6)
    assert record.array == Array(4, 5, 6)

    array[0] = 7
    assert record.array == Array(4, 5, 6)  # The value in the record is independent of the original
    ```
    
    For clarity, it's recommended to use the copy from operator (`@=`) when updating fields that are known to be
    an array or record.

    ```python
    record.array @= array
    ```

### Instance Checks

Any record is considered an instance of the generic [`Record`][sonolus.script.record.Record] type:

```python
pair = MyPair(1, 2)
assert isinstance(pair, Record)
```

If a record is generic, any instance of it is considered an instance of the generic type:

```python
pair = MyGenericPair[int, int](1, 2)
assert isinstance(pair, MyGenericPair)
```

Only an instance of a record with the exact field types is considered an instance of a concrete [`Record`][sonolus.script.record.Record] type:

```python
pair = MyPair(1, 2)
assert isinstance(pair, MyPair[int, int])
assert not isinstance(pair, MyPair[int, Array[int, 2]])
```

## Transient Types
In addition to the core types, the following transient types are available.
There are some restrictions on how they can be used:

- They cannot be used as type arguments:
    ```python
    # Not ok:
    Array[str, 3]
    ```
- They cannot be used as a field types:
    ```python
    # Not ok:
    class MyRecord(Record):
        field: str
  
    # Not ok:
    class MyArchetype(PlayArchetype):
        field: str = imported()
    ```

### tuple

The built-in `tuple` type can be declared and destructured as usual:

```python
t = (1, (2, 3))
a, (b, c) = t
```

Tuples may be indexed, but the given index must be a compile-time constant:

```python
t = (1, 2, 3)

# Ok
debug_log(t[0])

# Not ok:
debug_log(t[random_integer(0, 2)])
```

They may also be created as an \*args argument to a function and unpacked as an argument to a function:

```python
def f1(a, b, c):
    return a + b + c

def f2(*args):
    return f1(*args)
```

Iterating over a tuple is also supported, but they are expanded at compile time, so iterating over large tuples may
significantly increase the size of the compiled engine and slow down compilation:

```python
t = (1, 2, 3)
for x in t:
    debug_log(x)
```

### dict

Dicts can be created by the \*\*kwargs syntax and unpacked as arguments to a function:

```python
def f1(a, b):
    return a + b
    
def f2(**kwargs):
    return f1(**kwargs)
```

### str

Strings can be created and compared for equality and inequality:

```python
s1 = 'abc'
s2 = 'def'

assert s1 == 'abc'
assert s1 != s2
```

### Special Constants

The built-in `None`, `Ellipsis`, and `NotImplemented` constants are supported.

`None` is the only supported right-side operand for the `is` and `is not` operators.
    
```python
a = None
b = 1

# Ok
a is None
b is not None

# Not ok:
b is b
```

### Other types

Classes themselves are considered instances of `type`. They may be used as arguments to functions, but annotating
a record field as `type` or declaring an array with element type `type` is not supported.

Functions or methods may be used as arguments to functions, but annotating a record field or setting
an array element type to `Callable` is not supported.

### Storing Instances of Transient Types in Records

!!! warning
    The following is advanced usage and is unnecessary for most use cases.

While transient types cannot be used as type parameters or as a Record field's type, it is possible to store them
in a generic record in a field annotated by a type parameter. Type arguments must not be explicitly provided when
doing so. If multiple fields are annotated by the same type parameter, all such fields may be required to hold the exact
same value in some cases.

For example, a version of the `filter` function can be implemented as follows (see [Iterables][sonolus.script.iterator] 
for more information on iterators):

```python
class _FilteringIterator[T, Fn](Record, SonolusIterator):
    fn: Fn
    iterator: T

    def has_next(self) -> bool:
        while self.iterator.has_next():
            if self.fn(self.iterator.get()):
                return True
            self.iterator.advance()
        return False

    def get(self) -> Any:
        return self.iterator.get()

    def advance(self):
        self.iterator.advance()


def my_filter[T, Fn](iterable: T, fn: Fn) -> T:
    return _FilteringIterator(fn, iterable.__iter__())
```


[^1]:
    The copy from operator (`@=`) is officially the in-place matrix multiplication operator in Python,
    but it has been repurposed in Sonolus.py for copying Arrays and Records.
