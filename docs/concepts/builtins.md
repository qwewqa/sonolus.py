# Builtins
Sonolus.py comes with support for a number of built-in functions.

- `abs(x)`
- `bool(object)`
- `callable(object)`
- `enumerate(iterable, start=0)`
- `filter(function, iterable)`
- `float(x)` (for a num argument)
- `int(x)` (for a num argument)
- `isinstance(object, classinfo)`
- `issubclass(class, classinfo)`
- `len(s)`
- `map(function, iterable)` (note: may differ from standard Python behavior, see 
  [`map`](../reference/builtins.md#doc_stubs.builtins.map))
- `max(iterable, *, key=None)`, `max(arg1, arg2, *args, key=None)`
- `min(iterable, *, key=None)`, `min(arg1, arg2, *args, key=None)`
- `range(stop)`, `range(start, stop[, step])`
- `reversed(seq)`
- `round(number[, ndigits])`
- `zip(*iterables)`

## Standard library modules
Sonolus.py also comes with support for some standard library modules.

### math
- `sin(x)`
- `cos(x)`
- `tan(x)`
- `asin(x)`
- `acos(x)`
- `atan(x)`
- `atan2(y, x)`
- `sinh(x)`
- `cosh(x)`
- `tanh(x)`
- `floor(x)`
- `ceil(x)`
- `trunc(x)`
- `log(x[, base])`
- `pi`
- `e`
- `tau`
- `inf`

### random
- `randrange(stop)`, `random.randrange(start, stop[, step])`
- `randint(a, b)`
- `choice(seq)`
- `shuffle(seq)`
- `random()` (does not include 1)
- `uniform(a, b)` (may include `b` where Python normally doesn't)

Creating `Random` instances is not supported.
