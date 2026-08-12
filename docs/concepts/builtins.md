# Builtins
Sonolus.py comes with support for a number of built-in functions.
The supported functions and parameters are listed below; see the [builtins](../reference/builtins.md) reference for
full signatures.

- `abs(x)`
- `all(iterable)`
- `any(iterable)`
- `bool(x=False)`
- `callable(object)`
- `dict()`, `dict(source, **kwargs)`, `dict(**kwargs)` (source must be a dict or tuple of key-value pairs; keys must
  be compile-time constants)
- `enumerate(iterable, start=0)`
- `filter(function, iterable)`
- `float(x=0.0)` (for a num argument)
- `getattr(object, name[, default])`
- `hasattr(object, name)`
- `int(x=0)` (for a num argument)
- `isinstance(object, classinfo)`
- `issubclass(cls, classinfo)`
- `iter(iterable)` (not supported for a `tuple`, `dict`, `set`, or enum class)
- `len(s)`
- `map(function, iterable, *iterables)`
- `max(iterable, *, default=..., key=None)`, `max(arg1, arg2, *args, key=None)`
- `min(iterable, *, default=..., key=None)`, `min(arg1, arg2, *args, key=None)`
- `next(iterator)` (see [reusing iterators](constructs.md#reusing-iterators))
- `range(stop)`, `range(start, stop[, step])`
- `reversed(seq)`
- `round(number[, ndigits])`
- `set()`, `set(source)` (source must be a tuple, dict, enum class, or set; members must be compile-time constants)
- `setattr(object, name, value)`
- `sum(iterable, start=0)` (numeric values only)
- `super(type[, object-or-type])`
- `type(object)`
- `zip(*iterables, strict=False)` (`strict=True` is not supported)

## Standard library modules
Sonolus.py also comes with support for some standard library modules. Each has a corresponding reference page with
full signatures: [math](../reference/math.md), [random](../reference/random.md), and
[typing](../reference/typing.md).

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
- `randrange(stop)`, `randrange(start, stop[, step])`
- `randint(a, b)`
- `choice(seq)`
- `shuffle(seq)`
- `random()` (does not include 1)
- `uniform(a, b)` (may include `b` where Python normally doesn't)

Creating `Random` instances is not supported.

### typing
- `assert_never(arg, /)`
