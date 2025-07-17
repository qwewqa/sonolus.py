# Overview

Sonolus.py is a Python library for creating Sonolus engines. This page provides an overview of the key functionality 
available in the library. For more detailed information, see the [Concepts](concepts/index.md) and 
[Reference](reference/index.md) sections.

## Features
Sonolus.py functions by compiling Python code into Sonolus nodes. As such, it supports a subset of Python including
most syntax and a portion of the standard library. Additionally, Sonolus.py provides its own library of types and
functions that are specifically designed for use in Sonolus engines.

### Language

#### Syntax
Most Python syntax is supported, but there are a few limitations:

- Destructuring assignment with the `*` operator is unsupported.
- Sequence (list and array) `match` patterns with the `*` operator are unsupported.
- Mapping (dict) `match` patterns are unsupported.
- Within functions, `import` statements are unsupported.
- The `global` and `nonlocal` keywords are unsupported.
- Exception related statements (`try`, `except`, `finally`, `raise`) are unsupported.

#### Compile Time Evaluation
Sonolus.py will evaluate some expressions at compile time such as basic arithmetic operations on constants,
boolean logical operations (`and`, `or`, `not`) on constants, and type checks (`isinstance`, `issubclass`).

In control flow constructs like `if` and `match`, Sonolus.py may determine some branches to be unreachable at compile 
and eliminate them without evaluating them. This allows code like the following to compile successfully:

```python
a = 1
if isinstance(a, Vec2):
    # This branch is eliminated at compile time.
    # If it were not, compilation would fail because `a` has no attribute `x`.
    debug_log(a.x)
else:
    debug_log(a)
```

#### Variables
Numeric (`int`, `float`, `bool`) variables are fully supported and can be freely assigned and modified.

All other variables have the restriction that if the compiler finds multiple possible values for a variable, it may
not be accessed. For example, the following code will not compile:

```python
if random() < 0.5:
    a = Vec2(1, 2)
else:
    a = Vec2(3, 4)
# This will not compile because `a` could have been defined in either branch.
debug_log(a.x)
```

#### Function Returns
Similar to variables, functions returning `int`, `float`, or `bool` can have any number of return statements. Functions
returning `None` may also have any number of `return` or `return None` statements.

Functions returning any other type must have exactly one `return` statement, and it must be the only exit point of the 
function. It is ok, however, for a function to have other `return` statements that are eliminated at compile time. 
For example, the following code will compile successfully:

```python
def fn(a: int | Vec2):
    if isinstance(a, Vec2):
        return a.x + a.y
    else:
        return a * 2

fn(123)
```

### Types

#### Numbers
Sonolus.py supports `int`, `float`, and `bool` types and most of the standard operations such as mathematical operations
(`+`, `-`, `*`, `/`, `//`, `%`), comparisons (`<`, `<=`, `>`, `>=`, `==`, `!=`), and boolean operations 
(`and`, `or`, `not`).

#### Record
[`Record`](reference/sonolus.script.record.md) is the main way to define custom types in Sonolus.py. It functions similarly to a data class and
provides a way to define a type with named fields:

```python
class MyRecord(Record):
    a: int
    b: float

my_record = MyRecord(1, b=2.5)
my_zero_record = +MyRecord  # Short for MyRecord(0, 0.0)
my_record_copy = +my_record  # Create a copy of my_record with the same values
my_record.a = 123  # Modify a field of the record
```

Records may also be generic:

```python
class MyGenericRecord[T](Record):
    value: T

my_generic_record = MyGenericRecord[int](42)
my_generic_record_2 = MyGenericRecord(MyRecord(1, 3.5))  # Type arguments are inferred
```

#### Array
[`Array`](reference/sonolus.script.array.md) is a type that represents a fixed-size array of elements of a specific type.

```python
my_array = Array[int, 3](1, 2, 3)
my_array_2 = Array(4, 5, 6)  # Type arguments are inferred
my_zero_array = +Array[int, 3]  # Short for Array[int, 3](0, 0, 0)
my_array_copy = +my_array  # Create a copy of my_array with the same values
my_array[2] = 10  # Modify an element of the array
```

#### Other Types
Sonolus.py has limited support for other types of values such as strings, tuples, and functions. These have restrictions
such as not being valid as Record field types or Array element types.

### Modules
Sonolus.py provides a number of built-in modules that can be used in Sonolus engines. These include:

- Project
    - [Project](reference/sonolus.script.project.md): Configuration for a Sonolus.py project.
    - [Engine](reference/sonolus.script.engine.md): Configuration for a Sonolus.py engine.
    - [Level](reference/sonolus.script.level.md): Configuration for a Sonolus.py level.
    - [Archetype](reference/sonolus.script.archetype.md): Engine archetypes and their configuration.
- Core Types
    - [Array](reference/sonolus.script.array.md): Fixed-size arrays.
    - [Num](reference/sonolus.script.num.md): Numeric values (int, float, bool).
    - [Record](reference/sonolus.script.record.md): User-defined types with named fields.
- Engine Resources
    - [Bucket](reference/sonolus.script.bucket.md): Judgment buckets.
    - [Effect](reference/sonolus.script.effect.md): Sound effects.
    - [Instruction](reference/sonolus.script.instruction.md): Tutorial instructions.
    - [Options](reference/sonolus.script.options.md): Engine options.
    - [Particle](reference/sonolus.script.particle.md): Particle effects.
    - [Sprite](reference/sonolus.script.sprite.md): Sprites and skins.
    - [UI](reference/sonolus.script.ui.md): Engine ui configuration.
- Sonolus Runtime
    - [Globals](reference/sonolus.script.globals.md): Level data and level memory definition.
    - [Runtime](reference/sonolus.script.runtime.md): Runtime functions like time and ui configuration.
    - [Stream](reference/sonolus.script.stream.md): Data streams recorded in play mode and used in watch mode.
    - [Text](reference/sonolus.script.text.md): Standard Sonolus text constants.
    - [Timing](reference/sonolus.script.timing.md): Beat and timescale related functions.
- Python Builtins
    - [builtins](reference/builtins.md): Supported Python builtins.
    - [math](reference/math.md): Supported math functions.
    - [random](reference/random.md): Supported random functions.
- Utilities
    - [ArrayLike](reference/sonolus.script.array_like.md): Mixin for array functionality.
    - [Containers](reference/sonolus.script.containers.md): Additional container types like `VarArray` and `ArrayMap`.
    - [Debug](reference/sonolus.script.debug.md): Debugging utilities.
    - [Easing](reference/sonolus.script.easing.md): Easing functions for animations.
    - [Interval](reference/sonolus.script.interval.md): Mathematical intervals.
    - [Iterator](reference/sonolus.script.iterator.md): Iterators over collections.
    - [Printing](reference/sonolus.script.printing.md): Preview mode number printing.
    - [Quad](reference/sonolus.script.quad.md): Quadrilaterals.
    - [Transform](reference/sonolus.script.transform.md): Transformations like translation, rotation, and scaling.
    - [Values](reference/sonolus.script.values.md): Generic utilities for working with values.
    - [Vec](reference/sonolus.script.vec.md): The Vec2 type and related functions.

For more details, see the [Reference](reference/index.md) section.

## Getting Started

Before making a new Sonolus.py engine, it may be helpful to look at some existing projects to see how they use
the library:

- [pydori](https://github.com/qwewqa/pydori): A Bandori-like engine designed to be an example project for Sonolus.py.

If you're starting a new project, you'll probably want to use the
[new project template](https://github.com/qwewqa/sonolus.py-template-project).
