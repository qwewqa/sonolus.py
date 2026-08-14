---
name: docstrings-and-comments
description: Read before writing a docstring, a prose comment, or a docs/ page in this repo. Docstrings in sonolus/script/*.py and doc_stubs/*.pyi are published API text read by engine authors, and the house rules diverge from Google style: the section order is inverted, Note and Usage come before Args, dunder members render, Raises is never used, __init__ never carries a docstring, and there are three things a published docstring must never do. Also covers the durability bar a # comment must clear and which half of a fact belongs in a comment rather than the docstring. This is the authoring skill: to judge prose that already exists, in a diff or a review, use reviewing-prose.
---

# Docstrings and comments

This skill is for **writing** prose. To judge prose that already exists, see the `reviewing-prose` skill, which
covers what each violation looks like in a diff and which comments must not be deleted.

Docstrings are the highest-consistency surface in the repo. Most top-level `sonolus/script/*.py` modules and every
`doc_stubs/*.pyi` are published by mkdocstrings, so a docstring is product text read by engine authors.
Consistency matters more here than anywhere else, because the output is a generated API listing where
inconsistency is visible side by side.

Keep prose ASCII-only and within 120 columns by hand: see the Prose section of `CLAUDE.md`.

## Where each fact goes

This table is the spine of both halves. Never state both halves of a fact in the docstring.

| Fact | Goes in |
| --- | --- |
| The user-observable contract | the docstring |
| The mechanism that produces it | a `#` comment on the implementing line |
| A restriction the user must obey | the docstring, in prose |
| Why the restriction exists, in internal terms | a comment |
| A long design rationale for internal code | a docstring on a hidden name (see below) |
| Behaviour identical to CPython | nowhere |

The comments read as the private half of the same sentence:

```python
# transform.py: docstring says "For flat stages, this will be 1.0, and this function would simply
# return progress unchanged."  The comment then says:
# Flat stage: the remap interval [1/d_0, 1/d_1] is degenerate (zero width), which would
# divide by zero. Return progress unchanged, as documented.
```

Long design rationale belongs in a docstring only on names mkdocstrings hides: a leading-underscore name, or
anything under `sonolus/script/internal/` or `sonolus/backend/`. Those docstrings behave exactly like comments and
can be as long and as internal as needed. Line-local "why this line" stays a `#` comment even there.

## Summary line

One line, ending in a period. This is universal in the codebase.

**Imperative for functions and methods**: `Return the ...`, `Get ...`, `Create ...`, `Rotate ...`.
Never `Returns the ...`.

```python
def index(self, value, start=0, stop=None):
    """Return the index of the value in the array equal to the given value."""
```

**Noun phrase for classes, properties, and attribute docstrings**: no leading verb.

```python
class Vec2(Record):
    """A 2D vector."""

@property
def length(self) -> float:
    """The length of the interval."""

value: T
"""The value contained in the box."""
```

This split is the one most often got wrong, because "Returns the ..." is the reflex for a property. Properties
describe what a member *is*, not what it does.

## Sections and their order

```
summary
<prose paragraphs>
Note:
Usage:          (or Examples:)
Args:
Returns:  /  Yields:
```

This **inverts standard Google style**, which puts `Note:` and `Examples:` last. Following the Google spec here
produces the wrong order. `archetype.py`'s `callback` is the canonical full form.

- **`Usage:`** is house-specific and outnumbers `Examples:` heavily. It holds a construction or declaration
  template: a fenced `python` block containing a pseudo-signature or a decorator form, not runnable code.
- **`Examples:`** holds a runnable snippet. Only write one you have actually traced against the real API.
- **`Raises:` is not used anywhere in this codebase.** State error behaviour in prose instead.

## Classes

Never write a docstring on `__init__`: no `__init__` in the package has one. Constructor documentation goes on the
class, and which section you use is mechanical, not stylistic:

- The class has a real `__init__` (host-side, build-time, `@dataclass`) -> **`Args:` on the class**.
  `Engine`, `Level`, `Project`, the `*Mode` classes.
- The class is a compiled runtime type whose constructor is generated from field annotations (`Record`, archetype,
  container subclasses) -> **`Usage:` on the class** with a signature template, because there is no real signature
  for mkdocstrings to show.

```python
class Pair[T, U](Record):
    """A pair of values.

    Usage:
        ```python
        Pair[T, U](first: T, second: U)
        ```
    """
```

Document fields with an **attribute docstring**: a bare string literal on the line after the annotation. Never an
`Attributes:` section; there are none in the codebase. mkdocstrings renders the attribute docstring beside the
field with its annotation, so the type is not duplicated.

## Args and Returns

`mkdocs.yml` sets `show_signature_annotations: true`, so **never repeat a type in an `Args:` description**. Skip
`self` and `cls`.

A summary line alone is enough when the function takes at most one non-obvious parameter and has no behaviour a
caller could get wrong. Write the full block once there are multiple parameters, a parameter whose semantics are
not evident from its name, or any clamping, mutation, bounds, or error behaviour to state.

## Three things a published docstring must never do

**Do not name internals.** `SetImpl`, `TupleImpl`, `_MappingIterator`, `ConstantValue`, `_get_readonly_`,
`BlockPlace`, `meta_fn`, `compile_and_call` mean nothing to an engine author. If a fact can only be phrased in
those terms, it belongs in a comment. Compare the two halves of the same fact:

```python
# sonolus/script/internal/builtin_impls.py: internal, can say anything
"""Tuples, dicts, sets, and enum classes are unrolled at compile time and have no runtime
iterator, so they get a compiled generator function instead of going through _MappingIterator.
Either way the result is a lazy iterator, as in Python."""

# doc_stubs/builtins.pyi: published, contract only
"""A `tuple`, `dict`, `set`, or enum class may be used, but every argument must be one of
those, or none may be; mixing them with other iterables raises an error."""
```

**Do not restate Python.** If the behaviour matches CPython, say nothing. `map` and `filter` return lazy iterators
exactly as in Python, so their docstrings say nothing about laziness, only about the library-specific restriction.

**Do not explain what an unsupported case does instead.** State the restriction and the recommendation, and stop.
What actually happens when someone violates it is implementation-dependent, is not something a reader should be
building on, and turns into a stale claim the moment the implementation shifts. The same applies to the Concepts
pages. Give the rule; if the exact behaviour matters, pin it in a test, where it can change without invalidating
prose.

```markdown
No:  A generator stores the value it yields in one location that it reuses for every `yield`, so nesting two
     loops over it logs `2, 2, 4, 4` where plain Python logs `2, 1, 4, 3`, while `zip` over a tuple returns a
     tuple rather than a one-shot iterator and so repeats the sequence instead.

Yes: Treat an iterator as single use. Advancing one that is already being consumed, or consuming one again
     after it is exhausted, is not supported.
```

## Changelog entries

An entry in `docs/changelog/index.md` is the user-observable half of a change, stated once and briefly.

- Say what changed for an engine author, never how the fix works inside. "Fixed a `match` value pattern failing
  to compile against a subject holding runtime values" is complete; adding "the comparison now goes through the
  subject's `__eq__`" is mechanism, and restates standard Python semantics besides.
- Do not restate standard behaviour. That an assertion fires only when runtime checks are enabled is how every
  assertion works; behaviour that simply matches Python needs no confirmation.
- Identify an error, do not transcribe it. Naming the message a user will see is enough; reproducing the
  suggestion text inside it duplicates what the error already tells them.
- Never justify a design choice. "Deliberately not rejected at compile time, since the call may be unreachable"
  is the author reasoning with the reader; users do not care, and it reads as defensive, low-quality writing.
  Describing the previous behaviour is fine ("Previously a tuple was treated as a single entity"); explaining why
  the new behaviour is the right choice is not. If a behaviour needs its reasons stated, that belongs on a
  Concepts page.

## Comments

**The default is no comment.** Half the files under `sonolus/script/` have no prose comments at all, and that is
correct: comment density tracks how much hidden compile-time machinery a reader must reconstruct, not how long or
how clever the code is. The public API layer is deliberately bare and lets docstrings carry it, while
`internal/visitor.py` (where Python semantics are reimplemented) is the most heavily commented file under
`sonolus/script/`.

**The bar is necessity, then durability.** True and relevant is not sufficient: a comment stays only if the code
cannot be correctly understood without it. If the name, the surrounding code, a docstring, or the test that pins
the behaviour already carries the reader, the comment goes, even when it records a real fact. A comment
describing something a refactor would invalidate is a liability besides, because a stale comment is worse than no
comment. Calibration from review rounds here: about half of the comments a careful author keeps still fail this
bar on a second pass, so when in doubt, cut.

**Use the words the codebase already uses.** Grep for a term before naming a concept with it, and grep for the
concept as well: the test is whether the tree already says this word about this thing. `mint` is house vocabulary
because `Context.meet` already mints a pass-through block, while `settle`, `anchor`, and `poison` appear nowhere
and read as a private vocabulary the next person has to learn. `stale` is the subtler case, since `CLAUDE.md` uses
it of a document that has gone out of date, which does not license it for a binding. Prefer the name of the type
or function that already means the thing: a binding is re-bound to a `ConflictBinding`, it is not "settled".

**Name what you are talking about.** A comment whose subject is "the pop" or "the pairing" sends the reader
hunting for the antecedent, and they can pick the wrong one. Write the identifier: `break_ctxs.pop()`,
`loop_binding`. This is what separates a short comment from a vague one; brevity is not the same as omission.

**Every sentence has to carry its own fact.** Length is judged per clause, not per comment. A long comment where
each sentence states something the code cannot is fine. A long comment that reaches length by restating the
purpose in three registers is worse than the two lines it should have been, and being simultaneously vague and
long is the usual result. Cut any clause the surrounding code already establishes.

### Comments that pass the bar

**A deliberate divergence from (or alignment with) CPython, recorded at the site that implements it.** The
reader's default assumption is CPython semantics, so silent divergence is the most expensive surprise in this
codebase.

```python
# A set is deliberately not unwrapped here, unlike in map/filter/zip/enumerate: plain Python rejects
# reversed() on a set too, so accepting one would be a divergence rather than a missing feature.
```

**A fact about the compile-time/runtime split that the local code cannot reveal.** This information exists only in
the compiler's model; a reader cannot recover it from the surrounding lines.

```python
# Whether there is an element to return is a runtime condition, so this is a runtime branch between two
# different values. Only numbers can merge out of a branch, so an element type like a record leaves the two
# branches with conflicting definitions and fails to compile, which is an accepted outcome here.
```

**"This is not the obvious form, and here is what the obvious form would break."** Always name the specific
failure, never "for correctness".

```python
# Rebind instead of using +=: Records get an __iadd__ that copies in place, so += would mutate the
# caller's start value.
```

**A load-bearing guard, so a future refactor does not delete it.** This is the one case where repeating something
the docstring also says is correct.

### Comments that do not

- Restating what the code says.
- A clause the enclosing code already establishes: "reference types only" inside the reference-type branch, or a
  restatement of what the local name was just bound to.
- Praise of the design in place of the fact: "the pairing is self-maintaining", "this keeps things consistent".
- Describing current mechanics that a refactor would invalidate.
- Line numbers, counts, or benchmark figures: they drift silently.
- Naming a choice without its consequence. `# A bit of a hack to allow resetting to the original state` and
  `# Hack to get around circular import issues` both leave the reader unable to tell whether it is safe to change.
- **Spelling out what happens when a contract is broken.** State the contract and stop. `Must not be called if
  the array is full.` is the whole comment. Which check fires, under which build settings, and what gets corrupted
  are all undefined behaviour, and writing them down defines them. This applies to docstrings too.
- **Defending code that already follows a documented convention.** Once the code does the correct and obvious
  thing, a comment arguing against the wrong alternative is noise: it makes a settled call look contested. A
  `meta_fn` reaching subset code through `compile_and_call` needs no comment saying why it is not a direct call;
  that is simply the rule the compiled-subset skill states. Write the comment only where the *right* choice is the
  surprising one.

  ```python
  # No:
  # compile_and_call rather than `self[index]`: a meta_fn body runs as plain Python, so the subscript would
  # evaluate __getitem__ outside the compiler and any runtime value it touches would reach a host-side len().
  return compile_and_call(self.__getitem__, index)

  # Yes:
  return compile_and_call(self.__getitem__, index)
  ```

  This mirrors "not the obvious form, and here is what it would break": that rule applies when code departs from
  what a reader expects, not when it lands on it.

## Deciding whether a member renders at all

Before phrasing a docstring as public or internal, confirm which it is. The global filters are not the last word,
and the mapping is not one page per module. See `references/publishing.md`.
