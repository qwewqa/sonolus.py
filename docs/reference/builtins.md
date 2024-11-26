# Builtins
Sonolus.py comes with support for a number of built-in functions.

- `abs(x)`
- `bool(object)` for a num argument
- `callable(object)`
- `enumerate(iterable, start=0)`
- `filter(function, iterable)`
- `float(x)` for a num argument
- `int(x)` for a num argument
- `isinstance(object, classinfo)`
- `issubclass(class, classinfo)`
- `len(s)`
- `map(function, iterable)`
- `max(iterable, *, key=None)`, `max(arg1, arg2, *args, key=None)`
- `min(iterable, *, key=None)`, `min(arg1, arg2, *args, key=None)`
- `range(stop)`, `range(start, stop[, step])`
- `reversed(seq)`
- `round(number[, ndigits])`
- `zip(*iterables)`

## Standard library modules
Sonolus.py also comes with support for some standard library modules.

### math
- `math.sin(x)`
- `math.cos(x)`
- `math.tan(x)`
- `math.asin(x)`
- `math.acos(x)`
- `math.atan(x)`
- `math.atan2(y, x)`
- `math.sinh(x)`
- `math.cosh(x)`
- `math.tanh(x)`
- `math.floor(x)`
- `math.ceil(x)`
- `math.trunc(x)`
- `math.log(x[, base])`

### random
- `random.randrange(stop)`, `random.randrange(start, stop[, step])`
- `random.randint(a, b)`
- `random.choice(seq)`
- `random.shuffle(seq)`
- `random.random()` (does not include 1)
- `random.uniform(a, b)` (may include `b` where Python normally doesn't)
