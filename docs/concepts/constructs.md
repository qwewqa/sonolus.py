# Constructs

Most standard Python constructs are supported in Sonolus.py.

## Key Differences

- Non-num variables must have a single live definition.
    - If there are multiple definitions `var = ...` for a variable, the compiler must be able to determine that a single
      one is active whenever the variable is used.
- Conditional branches may be eliminated if they are determined to be unreachable
- Functions with non-num return types may not return multiple distinct objects
    - Most functions returning a non-num value should have a single return at the end
- Destructuring assignment does not support the `*` operator.
- Sequence `match` patterns do not support the `*` operator.
- Mapping `match` patterns are unsupported.
- Imports may not be done within functions
- The `global` and `nonlocal` keywords are unsupported.

## Overview

The following constructs are supported in Sonolus.py:  

- Expressions:
    - Literals:
        - Numbers (excluding complex numbers): `0`, `1`, `1.0`, `1e3`, `0x1`, `0b1`, `0o1`
        - Booleans: `True`, `False`
        - Strings: `'Hello, World!'`, `"Hello, World!"`
        - Tuples: `(1, 2, 3)`
    - Operators (if supported by the operands):
        - Unary: `+`, `-`, `not`, `~`
        - Binary: `+`, `-`, `*`, `/`, `//`, `%`, `**`, `&`, `|`, `^`, `<<`, `>>`
        - Comparison: `==`, `!=`, `>`, `<`, `>=`, `<=`, `is`, `is not`, `in`, `not in`
        - Logical: `and`, `or` (for [`Num`](types.md#num) arguments only)
        - Ternary: `a if <condition> else b` (for [`Num`](types.md#num) conditions only)
        - Attribute: `a.b`
        - Indexing: `a[b]`
        - Call: `f(a, b, c)`
    - Variables: `a`, `b`, `c`
    - Lambda: `lambda a, b: a + b`
    - Assignment Expression: `(a := b)`
- Statements:
    - Simple Statements:
        - Assignments:
            - Simple assignment: `a = b`
            - Augmented assignment: `a += b`
            - Attribute assignment: `a.b = c`
            - Index assignment: `a[b] = c`
            - Destructuring assignment: `a, b = b, a`
            - Multiple assignment: `a = b = c = 1`
            - Annotated assignment: `a: int = 1`
        - Assert: `assert <condition>, <message>`
        - Pass: `pass`
        - Break: `break`
        - Continue: `continue`
        - Return: `return <value>`
        - Import: `import <module>`, `from <module> import <name>` (only outside of functions)
    - Compound Statements:
        - If: `if <condition>:`, `elif <condition>:`, `else:`
        - While: `while <condition>:`, `else:`
        - For: `for <target> in <iterable>:`, `else:`
        - Match: `match <value>:`, `case <pattern>:`
        - Function Definition: `def <name>(<parameters>):`
        - Class Definition: `class <name>:` (only outside of functions)

## Compile Time Evaluation

Some expressions can be evaluated at compile time:

- Numeric literals: `1`, `2.5`, `True`, `False`, ...
- None: `None`
- Basic arithmetic: for compile time constant operands: `a + b`, `a - b`, `a * b`, `a / b`, ...
- Is/Is Not None: for any left-hand operand, `a is None`, `a is not None`
- Type checks: for any value, `isinstance(a, t)`, `issubclass(a, t)`
- Boolean operations:
    - Negation: `not a`
    - And
        - Both operands are compile time constants: `a and b`
        - One operand is known to be False: `False and a`, `a and False`
    - Or
        - Both operands are compile time constants: `a or b`
        - One operand is known to be True: `True or a`, `a or True`
- Comparison: for compile time constant operands: `a == b`, `a != b`, `a > b`, `a < b`, `a >= b`, `a <= b`, ...
- Variables assigned to compile time constants: `a = 1`, `b = a + 1`, ...

Some values like array sizes must be compile-time constants.

The compiler will eliminate branches known to be unreachable at compile time:

```python
def f(a):
    if isinstance(a, Num):
        debug_log(a)
    else:
        debug_log(a.x + a.y)

# This works because `isinstance` is evaluated at compile time and only the first (if) branch is reachable.
# The second (else) branch is eliminated, so we don't get an error that a does not have 'x' and 'y' attributes.
f(123)
```

## Variables

Variables can be assigned and used like in vanilla Python.

```python
a = 1
b = 2
c = a + b
```

Unlike vanilla Python, non-num variables must have a single unambiguous definition when used.
Nums have no such restriction.

The following are allowed:

```python
v = Vec2(1, 2)  # (1)
v = Vec2(3, 4)  # (2)
debug_log(v.x + v.y)  # 'v' is valid because (2) is the only active definition
```

```python
v = 1  # (1)
v = Vec2(3, 4)  # (2)
debug_log(v.x + v.y)  # 'v' is valid because (2) is the only active definition
```

```python
v = Vec2(1, 2)  # (1)
while condition():
    v = Vec2(3, 4)  # (2)
    debug_log(v.x + v.y)  # 'v' is valid because (2) is the only active definition
```

```python
v = Vec2(1, 2)  # (1)
if random() < 0.5:
    v @= Vec2(3, 4)  # Updates 'v' in-place without redefining it
debug_log(v.x + v.y)  # 'v' is valid because (1) is the only active definition
```

The following are not allowed:

```python
v = Vec2(1, 2)  # (1)
if random() < 0.5:
    v = Vec2(3, 4)  # (2)
debug_log(v.x + v.y)  # 'v' is invalid because both (1) and (2) are active
```

```python
v = Vec2(1, 2)  # (1)
while condition():
    debug_log(v.x + v.y)  # 'v' is invalid because (1) and (2) are active
    v = Vec2(3, 4)  # (2) redefines 'v' for future iterations
```

## Expressions

### Literals

`int`, `float`, `bool`, `str`, and `tuple` literals are supported:

```python
a = 1
b = 1.0
c = True
d = 'Hello, World!'
e = (1, 2, 3)
```

### Operators

All standard operators are supported for types implementing them. `@=` is reserved as the copy-from operator.

```python
a = 1 + 2
b = 3 - 4
c = 5 * 6
d = 7 / 8
e = Vec2(1, 2)
f = e.x + e.y
g = Array(1, 2, 3)
h = g[0] + g[1] + g[2]
(i := 1)
```

The ternary operator is supported for, but the condition must be a [`Num`](types.md#num). If the operands are not nums,
the condition must be a compile-time constant or this will be considered an error:

```python
# Ok
a = 1 if random() < 0.5 else 2
b = Vec2(1, 2) if b is None else b

# Not ok
c = Vec2(1, 2) if random() < 0.5 else Vec2(3, 4)  # Multiple definitions
```

If the condition is a compile-time constant, then the ternary operator will be evaluated at compile time:

```python
e = Vec2(0, 0) if e is None else e  # Ok, evaluated at compile time
```

## Statements

### Assignment

Most assignment types are supported. Destructuring assignment is supported only for tuples, and the `*`
operator is not supported.

```python
# Ok
a = 1
b += 2
c.x = 3
d[0] = 4
(e, f), g = (1, 2), 3

# Not ok
h, *i = 1, 2, 3  # Not supported
```

```python
if a > 0:
    pass
```

### Conditional Statements

The standard conditional statements are supported.

#### if / elif / else

```python
if a > 0:
    ...
elif a < 0:
    ...
else:
    ...
```

When the condition is a compile-time constant, the compiler will remove the unreachable branches:

<div class="grid" markdown>

```python title="Code"
v = None
if v is None:
    v = Vec2(1, 2)
debug_log(v.x + v.y)
```

```python title="Equivalent"
v = None
# The 'if' branch is always taken
v = Vec2(1, 2)
debug_log(v.x + v.y)
```

</div>

This is useful for handling optional arguments and supporting multiple argument types:

```python
def f(a: Vec2 | None = None):
    if a is None:
        a = Vec2(1, 2)
    debug_log(a.x + a.y)
```

```python
def f(a: Vec2 | int):
    if isinstance(a, Vec2):
        debug_log(a.x + a.y)
    else:
        debug_log(a)
```

#### match / case

The `match` statement is supported for matching values against patterns. All patterns, including subpatterns,
except mapping patterns and sequences with the `*` operator are supported. 
Records have a `__match_args__` attribute defined automatically, so they can be used with positional subpatterns.

```python
match x:
    case 1:
        ...
    case 2 | 3:
        ...
    case Vec2() as v:
        ...
    case (a, b):
        ...
    case Num(a):
        ...
    case _:
        ...
```

As with `if` statements, the compiler will remove unreachable branches when the value is a compile-time constant:

<div class="grid" markdown>

```python title="Code"
v = 1
match v:
    case Vec2(a, b):
        debug_log(a + b)
    case Num():
        debug_log(v)
    case _:
        debug_log(-1)
```

```python title="Equivalent"
v = 1
# 'case Num()' is always taken
debug_log(v)
```

</div>

### Loops

#### while / else

While loops are fully supported, including the `else` clause and the `break` and `continue` statements.

```python
while a > 0:
    if ...:
        break
    if ...:
        continue
    ...
else:
    ...
```

#### for / else

For loops are supported, including the `else` clause and the `break` and `continue` statements.
Custom iterators must subclass [SonolusIterator][sonolus.script.iterator.SonolusIterator].

```python
for i in range(10):
    if ...:
        break
    if ...:
        continue
    ...
else:
    ...
```

Tuples can be iterated over and result in an unrolled loop. This can be useful for iterating of objects of different,
types, but care should be taken since it results in more code being generated compared to a normal loop:

<div class="grid" markdown>

```python title="Code"
for i in (1, 2, 3):
    debug_log(i)
```

```python title="Equivalent"
debug_log(1)
debug_log(2)
debug_log(3)
```

</div>

### Functions

Functions and lambdas are supported, including within other functions:

```python
def f(a, b):
    return a + b


def g(a):
    return lambda b: f(a, b)
```

Function returns follow the same rules as variable access. If a function returns a non-num value, it most only
return that value. If the function always returns a num, it may have any number of returns. Similarly, if a function
always returns None (`return None` or just `return`), it may have any number of returns.

The following are allowed:

```python
def f():
    return Vec2(1, 2)
```

```python
def g(x):
    # Only one return is reachable since isinstance is evaluated at compile time
    if isinstance(x, Vec2):
        return Vec2(x.y, x.x)
    else:
        return x
```

```python
def h(x):
    # Both returns return the exact same value
    x = Vec2(1, 2)
    if random() < 0.5:
        debug_log(123)
        return x
    else:
        return x
```

```python
def i(x):
    # All return values are nums
    if random() < 0.5:
        return 1
    return 2
```

The following are not allowed:

```python
def j():
    # Either return is reachable and return different values
    if random() < 0.5:
        return Vec2(1, 2)
    return Vec2(3, 4)
```

```python
def k():
    # Both the return and an implicit 'return None' are reachable
    if random() < 0.5:
        return Vec2(1, 2)
```

Outside of functions returning `None` or a num, most functions should have a single `return` statement at the end.

### Classes

Classes are supported at the module level. User defined classes should subclass [`Record`][sonolus.script.record.Record] or have a supported
Sonolus.py decorator such as `@level_memory`.

Methods may have the `@staticmethod`, `@classmethod`, or `@property` decorators.

```python
class MyRecord(Record):
    x: int
    y: int

    def regular_method(self):
        ...

    @staticmethod
    def static_method():
        ...

    @classmethod
    def class_method(cls):
        ...

    @property
    def property(self):
        ...
```

### Imports

Imports are supported at the module level, but not within functions.

### assert

Assertions are supported. Assertion failures cannot be handled and will terminate the current
callback when running in the Sonolus app. In debug mode, the game will also pause to indicate the error.

```python
assert a > 0, 'a must be positive'
```

### pass

The `pass` statement is supported.
