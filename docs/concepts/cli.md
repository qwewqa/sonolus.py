# Command Line
The `sonolus-py` command line tool builds, serves, and validates a Sonolus.py project.

## The project module

When a command is run, the current directory is scanned for a single `*/project.py` or `*/__init__.py` to use as
the project module.

It's also possible to provide an explicit `module` argument giving the dotted path to the [project's](project.md)
package or module, e.g. `sonolus-py build my_engine`.

## `sonolus-py build`
Compiles the project and writes the engine and level packages to disk.

```bash
sonolus-py build [module] [--build-dir DIR]
```

- `--build-dir DIR`: directory to write output to. Defaults to `./build`.

## `sonolus-py dev`
Builds the project once, then serves it over HTTP and starts an interactive CLI for the server.

```bash
sonolus-py dev [module] [--build-dir DIR] [--port PORT]
```

- `--build-dir DIR`: defaults to `./build`.
- `--port PORT`: defaults to `8000`.

## `sonolus-py check`
Validates the project without writing engine or level output.

```bash
sonolus-py check [module]
```

`check` accepts the [shared options](#shared-options) below, except `--build-dir`.

## `sonolus-py schema`
Prints the project's archetype schema as JSON to stdout.

```bash
sonolus-py schema [module]
```

## Shared options
`build`, `dev`, and `check` all accept the following:

| Option | Default | Effect |
|---|---|---|
| `-O0`, `--optimize-minimal` | | Use the minimal optimization passes. |
| `-O1`, `--optimize-fast` | | Use the fast optimization passes. |
| `-O2`, `--optimize-standard` | used if none of `-O0`/`-O1`/`-O2` is given | Use the standard optimization passes. |
| `--runtime-checks {none,terminate,notify}` | `none` for `build`/`check`, `notify` for `dev` | `none` disables runtime checks; `terminate` stops the callback on error; `notify` additionally logs and pauses before terminating. |
| `--gc` | off | Enable the Python garbage collector during compilation. |
| `--no-gc` | on | Disable the Python garbage collector during compilation. |
| `--play`, `--watch`, `--preview`, `--tutorial` | all enabled if none are given | Restrict which components are built (or, for `check`, validated). Passing any one of the four narrows the set to just those given. |
| `-v`, `--verbose` | off | Print the full traceback for a compilation error instead of a simplified summary when one is available. |
| `--profile` | off | Print a per-stage compile timing summary to stderr. Not available for `dev`. |
| `--profile-json PATH` | | Write per-stage compile timings as JSON to `PATH`, confirming with a line on stderr. Also enables profiling, so the `--profile` summary is printed to stderr as well. Not available for `dev`. |

`check` takes the `-O` options for compatibility with `build` and `dev`, but ignores them.

## Programmatic usage
Most of the same functionality is available as methods on [`Project`][sonolus.script.project.Project]:
[`Project.build`][sonolus.script.project.Project.build], [`Project.dev`][sonolus.script.project.Project.dev], and
[`Project.schema`][sonolus.script.project.Project.schema].
