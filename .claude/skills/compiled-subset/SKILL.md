---
name: compiled-subset
description: Read before adding or changing anything the compiler traces or evaluates: code under sonolus/script/ or sonolus/script/internal/, a @meta_fn, a Value subclass, a builtin or math implementation, an operator or iterator protocol method, or any error raised out of one. Covers the compile-time versus runtime split that new contributors get wrong, when @meta_fn is warranted and when plain Python is better, why a meta_fn must reach subset code through compile_and_call, the _name_ value protocol and which members a value type versus a reference type supports, what merges out of a runtime branch, and how errors must be raised so the visitor can attach a source location.
---

# The compiled subset

Two worlds share one source tree. The same function can run as plain Python (tests, host-side build code, the
`debug` simulation context) or be compiled into IR for the Sonolus runtime. Almost every mistake in this layer is
a confusion about which world a given line runs in.

## The two ways a function reaches the compiler

- **Traced** (the default, no decorator): the visitor reads the function's **source**, walks its AST, and re-emits
  it as IR. The function body never executes as Python during compilation.
- **`@meta_fn`**: the compiler **calls** the Python function at compile time. Its body is ordinary Python, running
  on the host, and whatever `Value` it returns becomes the compiled result.

`ctx()` is the switch. It returns the active compilation `Context` while compiling, and a falsy value otherwise.
A `@meta_fn` body runs in both worlds, so it must handle both:

```python
@meta_fn
def f(x):
    x = validate_value(x)          # accept a plain int/float or an existing Value
    if ctx():
        ...                        # emit IR, return a Value
    return ...                     # plain-Python result
```

That is the canonical shape: validate arguments, then branch on `if ctx():`.

## Prefer plain Python

Write ordinary Python and let the visitor compile it whenever the function is expressible in the compiled subset.
Reach for `@meta_fn` only when the function must inspect compiler state: `ctx()`, `_is_py_()`, `_as_py_()`,
`validate_value`, `_accept_`. A `@meta_fn` written for speed rather than for semantics should be spelled
`perf_meta_fn` (the same decorator, aliased) so the reason is visible at the call site.

`simple_meta_fn` only sets `_meta_fn_ = True`: no wrapper, so no debug-stack entry and no timing. It is what the
internal impl classes use for hot dunders (`TupleImpl.__getitem__`, `ConstantValue.__eq__`) and what `debug.py`
uses for its public helpers.

## Crossing back: compile_and_call

From inside a `@meta_fn`, call user or subset code through `compile_and_call(fn, *args)`. Calling it directly runs
it as plain Python instead of compiling it, which silently evaluates subset code on the host and lets runtime
values reach host-side operations that cannot handle them.

`compile_and_call` dispatches: outside a context it calls `fn` directly; a plain function gets traced; another
`meta_fn` gets called; anything else goes through `generate_fn_impl`. While compiling, the result always comes back
through `validate_value`; outside a context the call's result is returned as is, so do not assume a `Value`.

Do not comment on this choice at the call site. It is the rule, not a surprise. See the docstrings-and-comments
skill on defending code that already follows a convention.

## validate_value

`validate_value(x)` is the boundary function that turns a host object into a `Value`. It accepts `int`/`float`
(-> `Num`), an `Enum` (-> its value), a registered builtin, a type, a `tuple`/`dict`/`set`/`frozenset` (-> the
corresponding impl), typing special forms, the host objects the compiler carries as compile-time constants (a
`str`, a function or method, a module, `None`, `Ellipsis`, `NotImplemented`, a `TypeVar`), and anything already a
`Value`. Anything else raises `TypeError`, `list` included.

## The value protocol

`_name_` (leading **and** trailing underscore) is the compiler/value protocol, defined on
`sonolus/script/internal/value.py`. It is reserved for the framework: never invent a new one outside it, and never
spell a `Record` or `Array` data field `_foo_`. An ordinary private is `_name`, leading underscore only.

The protocol a `Value` subclass implements:

| Member | Purpose |
| --- | --- |
| `_size_()` | Flat slot count |
| `_is_value_type_()` | True if the value behaves immutably and supports `_set_` |
| `_is_concrete_()` | Fully parameterized and instantiable. Defaults to False and is not abstract, so override it |
| `_from_place_(place)` / `_from_list_(values)` / `_from_backing_source_(source)` | Construct from storage |
| `_to_list_()` / `_flat_keys_(prefix)` / `_to_flat_dict_(prefix)` | Flatten for emission |
| `_accepts_(value)` / `_accept_(value)` | Coercion check and coercion |
| `_is_py_()` / `_as_py_()` | Is this a compile-time constant, and what is it |
| `_get_()` / `_get_readonly_()` | Read access; `_get_` copies for value types to preserve immutability |
| `_set_(value)` / `_copy_from_(value)` / `_copy_()` | Assignment (`=`), copy assignment (`@=`), deep copy |
| `_alloc_()` / `_zero_()` | Uninitialized and zero-initialized allocation |
| `_get_merge_target_(values)` | Target for a value merging out of a branch |

`_is_py_()` is the compile-time-constant test and the reason so much of this layer can constant-fold: a `Num`
backed by a literal answers True and `_as_py_()` gives the Python number, so a `meta_fn` can take the host-side
path. A `Num` backed by a `BlockPlace` answers False.

`_set_` must not change the active context: no branching inside it.

## Value types versus reference types

`Num` is the only value type in the public API (`_is_value_type_()` is True). `Record`, `Array`, container types,
`TransientValue`, and constants are reference types.

The consequence that surfaces most often is branch merging. When two code paths bind the same name,
`Context` merges them: identical object identity merges trivially, differing types conflict, and otherwise
`_get_merge_target_` decides. The base implementation allocates a merge target for a value type and returns
`NotImplemented` for a reference type, which becomes a conflicting binding and a compilation error. So a runtime
branch producing two *different* records does not compile, while one producing two numbers does. `Maybe` overrides
`_get_merge_target_` to get around this for its own shape.

This is also why two `return` statements returning the *same object* compile fine while distinct objects of a
reference type do not.

## Errors

- `TypeError` for a wrong or unsupported type, `ValueError` for a right-typed but invalid value,
  `NotImplementedError` for an unsupported Python construct.
- `InternalError` (in `internal/error.py`) for a violated internal invariant: it says "this is a bug in
  sonolus.py", so do not use it for anything a user can trigger by writing ordinary code.
- **Do not raise `CompilationError` from a value or builtin implementation.** Raise the natural exception; the
  visitor wraps it exactly once, at the offending AST node, with the source location attached. The only two sites
  that construct one are that wrapper in `visitor.py` and the compiler-state error in `context.py`, which has no
  AST node to attach to.
- Messages name the offending value and, where there is a known fix, suggest it: `use Num instead`,
  `use set instead`. Mirror CPython's wording where an equivalent error exists.
- To print the type of a compile-time value use `_type_name(value)` from `internal/builtin_impls.py`, not
  `type(x).__name__`: the latter leaks a per-value wrapper class name with a memory address in it.

## Wrapping a single runtime op

`@native_function(Op.X)` from `internal/native.py` turns a Python function into one that emits a single IR
instruction for `Op.X` when compiling and runs the Python body otherwise. Pass `const_eval=True` when the Python
body is a faithful reference implementation, and it will fold at compile time whenever every argument is
`_is_py_()`. The Python body then becomes the definition of the op's semantics, so it must match the runtime: see
the runtime-semantics skill.

`native_call(op, *args)` is the unwrapped form, and `native_switch_membership(value, cases)` emits a
`SwitchWithDefault` membership test over compile-time constant cases.

## Reviewing subset code

The same rules read backwards, as things to check in a diff:

- A `@meta_fn` that does not handle both worlds: no `ctx()` branch and no reason it is context-independent.
- A `@meta_fn` calling user or subset code directly instead of through `compile_and_call`.
- A `@meta_fn` reaching `_is_py_()` or `_as_py_()` without putting the argument through `validate_value` first.
- `@meta_fn` on a function that the visitor could compile as ordinary Python. If it is there for speed, it should
  say so by being spelled `perf_meta_fn`.
- A newly invented `_name_`. The trailing-underscore form is the framework protocol only.
- A `Record` or `Array` data field spelled `_foo_` rather than `_foo`.
- `CompilationError` raised from a value or builtin implementation, rather than the natural exception.
- `type(x).__name__` in an error message instead of `_type_name(value)`.
- A `_set_` implementation that branches, or otherwise changes the active context.
- A runtime branch expected to merge a reference type into a new value. Rebinding the *same* object on both paths
  is fine for any type; producing two different ones only works for `Num`, and for `Maybe` through its override.

## Where things live

- `internal/visitor.py`: the AST visitor. Python semantics are reimplemented here, and it is the most heavily
  commented file under `sonolus/script/` for that reason.
- `internal/context.py`: the compilation `Context`, scopes, bindings, branch merging, allocation, `RuntimeChecks`.
- `internal/value.py`: the protocol above.
- `internal/impl.py`: `validate_value` and the builtin-impl registry.
- `internal/builtin_impls.py`, `math_impls.py`, `random.py`, `range.py`, `dict_impl.py`, `set_impl.py`,
  `tuple_impl.py`: the subset's implementations of Python builtins and container types.
- `internal/generic.py`: PEP 695 generic parameterization for `Record`, `Array`, and friends.
