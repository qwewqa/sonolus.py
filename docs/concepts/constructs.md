# Constructs

Sonolus.py functions as a compiler from Python to Sonolus nodes. While most standard Python constructs are supported,
there are some limitations compared to standard Python. The following sections outline what Sonolus.py supports and 
how it differs from standard Python.

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
- Match patterns cannot match the literals `True` or `False` directly; use `1` and `0` instead.
- Imports may not be done within functions
- The `global` and `nonlocal` keywords are unsupported.
- List literals (`[1, 2, 3]`) are unsupported; use a tuple or [`Array`][sonolus.script.array.Array] instead.
- List, set, and dict comprehensions are unsupported; only generator expressions are supported.
- Exception statements (`try`, `except`, `finally`, `raise`) and `with` statements are unsupported.
- F-strings and slices are unsupported.
- The bitwise operators (`&`, `|`, `^`, `<<`, `>>`, `~`) are not supported for [`Num`](types.md#num), since the
  Sonolus runtime has no bitwise operations. They are available only for types that define them, such as
  [`Interval`][sonolus.script.interval.Interval].
- `is` and `is not` are only supported against `None`.

## Supported Constructs

The following constructs are supported in Sonolus.py:  

- Expressions:
    - Literals:
        - Numbers (excluding complex numbers): `0`, `1`, `1.0`, `1e3`, `0x1`, `0b1`, `0o1`
        - Booleans: `True`, `False`
        - Strings: `'Hello, World!'`, `"Hello, World!"`
        - Tuples: `(1, 2, 3)`
        - Dicts (keys must be compile-time constants): `{1: 'a', 2: 'b'}`
        - Sets (members must be compile-time constants): `{1, 2, 3}`
    - Operators (if supported by the operands):
        - Unary: `+`, `-`, `not`, and `~` for types implementing it
        - Binary: `+`, `-`, `*`, `/`, `//`, `%`, `**`, and `&`, `|`, `^`, `<<`, `>>` for types implementing them
        - Comparison: `==`, `!=`, `>`, `<`, `>=`, `<=`, `in`, `not in`, and `is`/`is not` against `None`
        - Logical: `and`, `or` (for [`Num`](types.md#num) arguments only)
        - Ternary: `a if <condition> else b`
        - Attribute: `a.b`
        - Indexing: `a[b]`
        - Call: `f(a, b, c)`
    - Variables: `a`, `b`, `c`
    - Lambda: `lambda a, b: a + b`
    - Assignment Expression: `(a := b)` (not supported inside a generator expression)
    - Generator Expression: `(x for x in iterable if condition)`
- Statements:
    - Simple Statements:
        - Assignments:
            - Simple assignment: `a = b`
            - Augmented assignment: `a += b`
            - Attribute assignment: `a.b = c`
            - Index assignment: `a[b] = c`
            - Destructuring assignment: `a, b = b, a`
            - Multiple assignment: `a = b = c = 1`
            - Annotated assignment: `a: int = 1` (a bare annotation, `a: int`, binds nothing, though for a target
              like `a.b` or `a[i]` the target expression is still evaluated, as in standard Python)
        - Assert: `assert <condition>, <message>`
        - Delete: `del a[b]` (subscript targets only)
        - Pass: `pass`
        - Break: `break`
        - Continue: `continue`
        - Return: `return <value>`
        - Yield: `yield <value>`, `yield from <iterable>`
        - Import: `import <module>`, `from <module> import <name>` (only outside of functions)
    - Compound Statements:
        - If: `if <condition>:`, `elif <condition>:`, `else:`
        - While: `while <condition>:`, `else:`
        - For: `for <target> in <iterable>:`, `else:`
        - Match: `match <value>:`, `case <pattern>:`, `case <pattern> if <guard>:`
        - Function Definition: `def <name>(<parameters>):`
        - Class Definition: `class <name>:` (only outside of functions)

## Compile Time Evaluation

Some expressions can be evaluated at compile time:

- Numeric literals: `1`, `2.5`, `True`, `False`, ...
- None: `None`
- Basic arithmetic: for compile-time constant operands: `a + b`, `a - b`, `a * b`, `a / b`, ...
- Is/Is Not None: for any left-hand operand, `a is None`, `a is not None`
- Type checks: `isinstance(a, t)` for any value, and `issubclass(a, t)` where both arguments are compile-time types
- Boolean operations:
    - Negation: `not a`
    - And
        - Both operands are compile-time constants: `a and b`
        - The left operand is known to be False: `False and a`
    - Or
        - Both operands are compile-time constants: `a or b`
        - The left operand is known to be True: `True or a`
- Comparison: for compile-time constant operands: `a == b`, `a != b`, `a > b`, `a < b`, `a >= b`, `a <= b`, ...
- Variables assigned to compile-time constants: `a = 1`, `b = a + 1`, ...

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

Unlike vanilla Python, non-num variables must have a single live definition when used.
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

`int`, `float`, `bool`, `str`, `tuple`, `dict`, and `set` literals are supported. `dict` keys and `set` members
must be compile-time constants:

```python
a = 1
b = 1.0
c = True
d = 'Hello, World!'
e = (1, 2, 3)
f = {1: 'a', 2: 'b'}
g = {1, 2, 3}
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

The ternary operator is supported for any type, but if the operands are not [`Num`](types.md#num) values, the
two branches must produce the same object, or a type that supports merging such as
[`Maybe`][sonolus.script.maybe.Maybe]. Otherwise the condition must be a compile-time constant, or this is
considered an error:

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

Most assignment types are supported. A destructuring assignment accepts a tuple, a dict (which unpacks its
keys, as in Python), or an enum class as the value. The targets may be written in tuple or list form and
nested to any depth, but a starred target is not supported.

```python
# Ok
a = 1
b += 2
c.x = 3
d[0] = 4
(e, f), g = (1, 2), 3
[h, i] = 1, 2
j, k = {1: 'a', 2: 'b'}  # Unpacks the keys, as in Python

# Not ok
p, *q = 1, 2, 3  # Starred targets are not supported
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
are supported except mapping patterns, sequences with the `*` operator, and the singleton patterns `case True:`
and `case False:` (use `case 1:` and `case 0:` instead).
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

Tuples can be iterated over and result in an unrolled loop. This is useful for iterating over objects of different
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

#### Closures

Nested functions and lambdas can read variables from an enclosing function. Unlike default argument values, which
are evaluated once when the function is defined, a captured variable is looked up using the enclosing scope's
current value each time the closure runs:

```python
def f():
    x = 1

    def g():
        return x

    debug_log(g())  # 1
    x = 2
    debug_log(g())  # 2
```

Since the `global` and `nonlocal` keywords are unsupported, a closure cannot rebind a name from an enclosing
scope, but it can mutate a captured [`Record`][sonolus.script.record.Record] in place:

```python
def f():
    p = Pair(1, 2)

    def increment():
        p.first += 1

    increment()
    debug_log(p.first)  # 2
```

A captured variable is subject to the same single-live-definition rule as any other variable.

#### Return Values

Function returns follow the same rules as variable access. If a function returns a non-num value, it must only
return that value. If the function always returns a num, it may have any number of returns. Similarly, if a function
always returns None (`return None` or just `return`), it may have any number of returns. 
The [`Maybe`][sonolus.script.maybe.Maybe] type is also an exception; see the
[`Maybe` documentation](../reference/sonolus.script.maybe.md) for details.

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

#### Generators

A function containing `yield` is a generator function. Calling it returns an iterator that can be used with `for`
or other functions that accept an iterator:

```python
def gen():
    yield 1
    yield 2
    yield 3

for x in gen():
    debug_log(x)
```

Generators are lazy: code before the first `yield` does not run until the first value is requested. Yielded
values follow the same single-live-definition rule as function return values, and a generator function's `return`
statements must not return a value.

##### Reusing iterators

Treating an iterator as single use is recommended: consume it once, and build a fresh one if the values are needed
again.

Advancing an iterator that is already being consumed, by nesting two loops over it or mixing `next` with a `for`
loop, is not supported. Neither is consuming one a second time after it has
been exhausted. Use [`copy`][sonolus.script.values.copy] if a value taken from an iterator needs to outlive the
next advance. This is an area which diverges from normal Python behavior. Otherwise, values obtained previously from
an iterator may unexpectedly change when the iterator is advanced.

### Classes

Classes are supported at the module level. User defined classes should subclass
[`Record`][sonolus.script.record.Record] or have a supported
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

Assertions are supported. Assertion failures terminate the current callback.

When runtime checks are disabled (the default in production builds), an assertion on a runtime-dependent
condition is skipped and must not be relied upon. An assertion on a condition that is a compile-time constant
known to be false, such as `assert False`, is always kept and still terminates the callback, so it can be used
to mark unreachable code.

```python
assert a > 0, 'a must be positive'
```

### del

`del a[b]` is supported for types implementing `__delitem__`, such as
[`VarArray`][sonolus.script.containers.VarArray] and [`ArrayMap`][sonolus.script.containers.ArrayMap].
Deleting a variable (`del a`) or an attribute (`del a.b`) is not supported.

### pass

The `pass` statement is supported.
