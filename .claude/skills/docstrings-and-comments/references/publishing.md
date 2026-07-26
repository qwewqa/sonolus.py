# What actually renders

Read this when deciding whether a docstring is published (and so must follow the contract-only rules) or hidden
(and so may be as internal as it needs to be).

## Global filters

`mkdocs.yml` sets the mkdocstrings filters to `!^_`, `^__`, `!^_.+_`, `^__.+__`. Read in order, that means:

- **Dunder members are published**: `__init__`, `__getitem__`, `__len__`, `__iter__`.
- **Single-underscore members are not**: `_helper`, `_MappingIterator`.
- **The `_name_` value protocol is not**: `_size_`, `_accept_`, `_get_`.

Anything under `sonolus/script/internal/` has no docs page at all, so nothing in it renders regardless of naming.
The same holds for `sonolus/backend/**`.

## Per-page overrides

The mapping is not one page per module, and a page can narrow or widen the global filters. Check
`docs/reference/<module>.md` before assuming a member renders.

- **A module with no page publishes nothing.** `sonolus/script/pointer.py` is the only current example: every
  other `sonolus/script/*.py` module has a `docs/reference/sonolus.script.<name>.md`.
- **A page may narrow.** `sonolus.script.record.md` sets `members: []` on the module, then re-declares
  `Record` with an explicit `members:` list holding only `type_var_value`. So most of `Record` does not render
  however it is named.
- **A page may widen.** `archetype.md`, `array.md`, and `containers.md` set `inherited_members: true`, so a
  docstring inherited from a base class renders on the subclass too. A docstring written once on
  `ArrayLike` is published on every container that inherits it, and must read correctly for all of them.

`docs/reference/builtins.md`, `math.md`, `random.md`, `typing.md`, and, despite its name, `sonolus.script.num.md`
render the `doc_stubs/*.pyi` files rather than a `sonolus` module.

## Verifying

`mkdocs build --strict` fails on a broken cross-reference and, because `validation.anchors: warn` is set, on a
broken heading anchor. It does not tell you whether a member rendered. To check that, build and look at
`site/reference/<page>/index.html`, or grep the built page for the member name.
