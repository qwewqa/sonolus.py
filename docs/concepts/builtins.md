# Builtins
Sonolus.py comes with support for a number of built-in functions.
The supported functions and parameters are listed below.

- `abs(x)`
- `bool(object)`
- `callable(object)`
- `enumerate(iterable, start=0)`
- `filter(function, iterable)`
- `float(x)` (for a num argument)
- `int(x)` (for a num argument)
- `isinstance(object, classinfo)`
- `issubclass(class, classinfo)`
- `iter(iterable)`
- `len(s)`
- `map(function, iterable)`
- `max(iterable, *, default=..., key=None)`, `max(arg1, arg2, *args, key=None)`
- `min(iterable, *, default=..., key=None)`, `min(arg1, arg2, *args, key=None)`
- `next(iterator)`
- `sum(iterable, start=0)`
- `range(stop)`, `range(start, stop[, step])`
- `reversed(seq)`
- `round(number[, ndigits])`
- `super(type[, object-or-type])`
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
- `sqrt(x)`
- `degrees(x)`
- `radians(x)`
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

### typing
- `assert_never(arg, /)`
