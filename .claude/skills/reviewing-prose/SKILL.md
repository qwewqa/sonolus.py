---
name: reviewing-prose
description: Read when judging docstrings, comments, or docs/ pages that already exist rather than writing new ones: reviewing a diff or a PR, doing a self-review pass before declaring work done, following up on /code-review or /simplify, deciding whether a comment is worth keeping, or trimming comments that do not meet this repo's bar. Gives the observable signature of each violation as it appears in a diff, the list of comments that read as redundant but are load-bearing and must not be deleted, and the report-then-trim order that keeps a review pass from silently under-reporting.
---

# Reviewing prose

The rules themselves are in the `docstrings-and-comments` skill. This skill is only about applying them to prose
that already exists: how a violation looks in a diff, what must survive the pass, and in what order to work.

Trimming is in scope. A comment that does not clear the bar should be deleted, not annotated, and that is an
expected outcome of a review here rather than an overstep.

## Report first, trim second

Do not filter while reading. Scan the whole diff and collect every candidate, then make the delete or keep call in
a second pass against the protected list below.

Working in one pass looks more efficient and reliably under-reports: judging each comment at the moment you read it
biases toward leaving it, because the local context always makes a comment look motivated. Separating the passes is
what makes the protected list actually get consulted.

The bar is necessity, not truth: a comment that is accurate, relevant, and durable is still removed unless the
code cannot be correctly understood without it. Review rounds here have cut about half of the comments a careful
first pass kept, so expect the trim list to be long, and treat the protected classes below as the only reliable
exceptions.

## Observable signatures

What to look for, one line each. The reasoning behind each is in the authoring skill.

**Comments**

- Text that paraphrases the line below it.
- A subject with no antecedent in the code: "the pop", "the pairing", "that call". Grep the identifier it must
  mean; if you cannot find it in a few seconds, neither can the next reader.
- A word for a repo concept that the tree does not otherwise use. Grep the term across `sonolus/`: a zero hit
  count outside the diff means the author invented it. Beware the inverse error, though, since a term can look
  invented and be established: `mint` reads like jargon and is already in `Context.meet`.
- A clause the enclosing branch, the local name, or the signature already establishes.
- A comment that is long and vague at once. Check it clause by clause: each has to state something the code
  cannot. Length alone is not a finding, and neither is brevity; unearned length is.
- A comment describing current mechanics: which pass runs, what a helper currently returns, how a loop is
  structured.
- A line number, a count, a benchmark figure, or a file size.
- The words "hack", "for correctness", "for safety", or "to be safe" with no named consequence.
- A comment naming a choice with no statement of what the alternative would break.
- Prose spelling out what happens when a documented contract is violated.
- A comment justifying code that follows a convention the skills already state.
- A comment restating what the docstring above it already says, outside the load-bearing-guard case.
- Anything that reads as a note to a reviewer, a changelog entry, or a record of what used to be there.

**Docstrings**

- A property, class, or attribute docstring starting with a verb: `Returns the ...`, `Gets ...`.
- A function or method docstring starting with `Returns` rather than `Return`.
- A summary line spanning more than one line, or not ending in a period.
- `Note:` or `Examples:` placed after `Args:` or `Returns:`.
- A `Raises:` section.
- An `Attributes:` section.
- A docstring on `__init__`.
- An `Args:` description repeating a type that is already in the signature.
- A published docstring naming a symbol an engine author cannot see: one with a leading underscore, or one that
  lives under `sonolus/script/internal/` or `sonolus/backend/`. Grepping `docs/reference/` does not answer this;
  those pages hold only mkdocstrings directives, not member names.
- A published docstring explaining what an unsupported case does instead of stating the restriction.
- An `Examples:` block you cannot trace against the real API.
- A `Usage:` block holding runnable code, or an `Examples:` block holding a pseudo-signature.

**Docs pages**

- Non-ASCII punctuation, an em dash, or a ` -- ` where a colon or parentheses would read better.
- A heading rename in the diff: reference pages deep-link into `concepts/types.md`, so check inbound anchors.
- A claim about behaviour that no test pins.
- A new page with no `mkdocs.yml` nav entry, or with no inbound link from any other page. Orphan pages have been a
  real problem here: `concepts/cli.md` and `concepts/builtins.md` each once had zero inbound links.

## Checking a factual claim

`CLAUDE.md` and the files under `.claude/skills/` are prose about the codebase and go out of date exactly as a
comment does. `CLAUDE.md` says so itself: where a rule and the code disagree, the code is probably right and the
file is stale. Nothing in the gate checks either, so a review is the only thing that will. Order the work by what
one command can answer:

- **Universals and negatives first.** "only", "never", "every", "none", "no ... currently", "nowhere". One grep
  decides each, and one counterexample is enough to sink it. These are worth checking before anything else because
  they are both the most checkable and the most often wrong.
- **Enumerations rot.** A list of registered phases, of stub files, of `noqa` sites. Re-derive each from the
  command that produces it rather than reading it and nodding: a list that was complete when written is the most
  likely thing in the file to be wrong now. Prefer a phrasing that survives a new entry.
- **Quoted code and docstrings.** Grep a distinctive fragment. A quotation attributed to a file has to appear in
  that file; a paraphrase presented as a quotation is a finding.
- **Figures drift.** "around 1500 files", "10 to 15 percent larger". Re-measure, or rewrite so the approximation
  stays true. The rule against counts applies to a line-local `#` comment; an operational figure in a skill can
  earn its place, but only if someone checked it.

## Do not delete

Deleting a good prose comment is the asymmetric failure here. It breaks nothing a test can catch, so it ships
silently, and the information is usually unrecoverable without redoing the analysis that produced it. These classes
stay unless you have positive evidence they are wrong:

- **Anything that is not prose.** A comment can be load-bearing syntax, and none of the rules above apply to it:
  `# cython:` directives (the first line of every `.pyx` and `.pxd`), `# noqa` and file-level `# ruff: noqa`,
  `# type: ignore`, `# noinspection`, and the `PYTEST_DONT_REWRITE` module docstring. Deleting one of these breaks
  the build or the lint rather than losing information. Establish that a comment is prose before judging it.
- **A recorded divergence from, or deliberate alignment with, CPython.** `kernels.pyx` is almost entirely this: each
  comment pins the exact CPython semantic a kernel mirrors, which is the non-obvious constraint on the code. A
  prior review round examined these specifically and found them load-bearing.
- **A compile-time versus runtime fact.** If the statement exists only in the compiler's model and a reader cannot
  recover it from the surrounding lines, it stays.
- **A load-bearing guard.** A comment whose job is to stop a future refactor from deleting a check. This is the one
  case where repeating the docstring is correct, so "redundant with the docstring" is not grounds for cutting it.
- **A stated contract, in a comment or a docstring.** `Must not be called if the array is full.` (`containers.py`,
  `VarArray.append`) is doing work.
- **An explanation of why the code is not in the obvious form.** Check that it names the specific failure. If it
  does, keep it; if it only gestures ("for correctness"), that is a rewrite, not a deletion.
- **A design-rationale docstring on a hidden name** (leading underscore, or anything under
  `sonolus/script/internal/` or `sonolus/backend/`). Those are allowed to be long and internal. Length is not a
  finding there.

The operable rule: **if you cannot articulate why the comment was written, that is not evidence it is
unnecessary.** Work out what it is protecting first. If you still cannot, leave it and say so.

## Delete or rewrite

- The fact documents a name the library does not support -> check before deleting. Documented-but-unsupported
  usually means the implementation exists and is merely unwired, so grep the internal impl modules for a matching
  private function and the dispatch table for its absence. If it is there, register it and keep the doc; deleting
  silently shrinks the public API. A name with no implementation anywhere is still fair to remove.
- The fact is wrong or has gone stale -> delete.
- The fact is true but belongs elsewhere (a contract sitting in a comment, a mechanism sitting in a docstring) ->
  move it, do not duplicate it. The authoring skill's fact-routing table decides which way.
- The fact is true and in the right place but under-specified ("for correctness") -> rewrite to name the
  consequence, or delete it if you cannot determine the consequence.
- The comment is correct and durable -> leave it alone, including when it is long, provided every sentence earns
  its place. Long and load-bearing is fine; long because the same point is made three ways is a rewrite.

## After trimming

Deleting a prose comment cannot break a test, so the suite will not tell you whether the pass was sound. What does
check something:

- `ruff format` then `ruff check --fix`, then hand-fix anything left and rerun until both are clean. This is also
  what catches a `# noqa` you removed by mistake, and removing a comment can leave a line that reflows or an
  import that is now unused.
- `zensical build --strict` if you touched a docstring or a `docs/` page: deleting a cross-reference target or
  renaming a heading breaks the build, and a docstring edit can silently change what renders.
- Re-read your own diff for deletions you cannot justify in one sentence, and restore those.
