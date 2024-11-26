# Constructs
Most standard Python constructs are supported in Sonolus.py

## Summary

- Expressions:
  - Literals:
    - Numbers (excluding complex numbers): `0`, `1`, `1.0`, `1e3`, `0x1`, `0b1`, `0o1`
    - Booleans: `True`, `False`
    - Strings: `'Hello, World!'`, `"Hello, World!"`
    - Tuples: `(1, 2, 3)`
    - Dictionaries: `{1: 'a', 2: 'b'}`
  - Variables: `a`, `b`, `c`
  - Operators (if supported by the operands):
    - Unary: `+`, `-`, `not`, `~`
    - Binary: `+`, `-`, `*`, `/`, `//`, `%`, `**`, `&`, `|`, `^`, `<<`, `>>`
    - Comparison: `==`, `!=`, `>`, `<`, `>=`, `<=`, `is`, `is not`, `in`, `not in`
    - Logical: `and`, `or` (for `Num` arguments only)
    - Ternary: `a if <condition> else b` (for `Num` conditions only)
    - Attribute: `a.b`
    - Indexing: `a[b]`
    - Call: `f(a, b, c)`
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

## Details

### Variables
Variables can be assigned and used like in vanilla Python.

```python
a = 1
b = 2
c = a + b
```

Unlike vanilla Python, non-num variables must have a single unambiguous definition when used:

```python
v = Vec2(1, 2)  # Definition 1
v = Vec2(3, 4)  # Definition 2
debug_log(v.x + v.y)  # 'v' is valid because only definition 2 is active
if random() < 0.5:
    v = Vec2(5, 6)  # Definition 3
# Can't use 'v' here because both definitions 2 and 3 are active
# Not ok: debug_log(v.x + v.y)
```

This is particularly important in loops, where previous iterations are considered:

```python
v = Vec2(1, 2)  # Definition 1
while condition():
    # Can't use 'v' here because both definitions 1 and 2 are active
    # Definition 2 is active because it may have been reached in a previous iteration of the loop
    # Not ok: debug_log(v.x + v.y)
    v = Vec2(3, 4)  # Definition 2
```

The copy-from (`@=`) operator can be used to update non-num variables without creating a new definition:

```python
v = Vec2(1, 2)
while condition():
    debug_log(v.x, v.y)  # Ok
    v @= Vec2(3, 4)  # Updates the value of 'v' without redefining it
```
