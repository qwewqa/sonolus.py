# Rust Backend Port — Execution Runbook

Operator guide for the autonomous run. The three documents divide cleanly:

- **[PORT.md](PORT.md)** — state and rules: protocol, task table, invariants, decisions,
  deviation log, worklog. *To find out where things stand, read this one.*
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — stable design reference for the backend.
- **This file** — how to start the run, what happens while it runs, and what you do at
  the end.

---

## 1. One-time setup

1. **Toolchain**: Rust installed (`rustup`, stable); `cargo --version` works. Python side
   is managed by uv (`uv sync`). From T0.1 onward, `maturin develop` installs the
   extension into the venv.
2. **Permissions**: already configured in `.claude/settings.json` — allows cargo,
   maturin develop, uv run pytest/python, uv sync, git add/commit/checkout/rebase/
   worktree/read-only, and `git push origin rust-port` (exactly that branch); denies
   pushes to `master`, force pushes, and `git tag`. Commit this file with the `rust/`
   docs.
3. Optional: branch protection on `master`.

---

## 2. Start (or resume) the run

In a fresh session, type **`/port`** — or simply point Claude at the ledger ("follow
`rust/PORT.md`"). The §0 entry point at the top of that file contains the full
orchestrator instructions and always starts from the ledger's current state, so the same
action starts a new run and resumes an interrupted one — there is no separate resume
procedure.

---

## 3. While it runs

Nothing is required from you. The §4 policies in PORT.md replace mid-run human decisions,
and every applied policy is recorded in the Deviation log for your review at the end.

If you want to peek: the PORT.md worklog (one entry per task), the Deviation log, and CI
on the `rust-port` branch are the live views.

The run stops early only on hard-abort conditions (PORT.md §4): frontend goldens can't be
kept byte-identical, the behavioral suite can't pass at minimal/standard after budget, or
the repo is corrupted beyond git recovery. If that happens — or the run is interrupted
for any reason — just type `/port` again: the entry point handles stale-worktree
reconciliation, and the ledger is consistent to within one task. If you suspect the
ledger and the repo disagree, add: "First audit the task table against actual repo state
and correct it."

---

## 4. When it finishes

The only human steps in the whole workflow:

1. Read the **completion report** and **Deviation log** in PORT.md. An empty deviation
   log means a clean run; `review-before-merge` and `blocks-merge` entries are your
   reading list, in that order.
2. Spot-check the big-ticket diffs: T6.2 (golden regeneration — the frontend-golden diff
   must be empty), T7.1 (Python backend deletion), T7.2 (workflows).
3. Confirm CI is green on `rust-port` across the full matrix; run
   `uv run pytest tests -n 32` locally once yourself.
4. Merge to `master`, tag, flip the publish workflow live.

---

## 5. Done means

PORT.md status reads done; `sonolus/backend/optimize/` no longer exists; the behavioral
suite passes at all levels; quality metrics ≥ the Python `standard` baseline; a wheel
installs and runs `sonolus-py build` on `test_projects/pydori` with no Rust toolchain
present; dev defaults to standard passes; one release ships the switch.
