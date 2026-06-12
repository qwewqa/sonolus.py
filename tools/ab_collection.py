"""T5.3 A/B: legacy vs Rust-backed Collection over identical builds.

Builds the same inputs through both ``Collection`` implementations — selected
exactly the way production does, via the ``SONOLUS_BACKEND`` environment
variable consumed by :func:`sonolus.build.collection.make_collection` — and
compares the written site trees structurally:

1. **pydori full build**: the real site-tree path
   (``sonolus.build.cli.build_collection`` -> ``build_project_to_collection``)
   over ``test_projects/pydori``. pydori ships no ``resources/`` tree, but the
   engine references skin/background/particle items by name and needs a
   default effect, so the tool fabricates the minimal resource items it needs
   (exercising the source loader incl. ``.json`` gzip resources) and drops in
   the T5.2 fixture scp (exercising the scp loader plus resource-level engine
   override and linking through the production path).
2. **synthetic resources fixture**: ``load_resources_files_to_collection``
   over the T5.2 test fixtures (``fixture_scp`` + ``source_tree`` imported
   from ``tests.build.test_collection`` — single source of truth) -> write.
3. **CLI smoke**: ``sonolus-py build`` on ``test_projects/pydori`` in both
   backend modes (subprocesses). Note ``build`` writes ``dist/engine`` +
   ``dist/levels``; the *site tree* is only produced by the dev-server path
   (``build_collection``), which step 1 drives directly.

Equivalence levels, per file (everything not byte-identical is listed):

- ``byte``  — byte-identical. Required for ``sonolus/repository/*``: blobs
  are content-addressed (file name = SHA1 of the bytes), so anything other
  than byte equality there is a structural diff by definition.
- ``json``  — JSON structurally equal, key order differs. Allowed only for
  non-repository JSON files: the documented ``load_from_source`` divergence
  (legacy iterates OS enumeration order, Rust name-sorts) reaches written
  item JSON key order (see ``sonolus/build/rust_collection.py``).
- ``gzip`` / ``gzip+json`` — gzip members compared decompressed. Site trees
  only contain gzip bytes under ``repository/`` (where byte equality is
  mandatory), so these levels appearing at all is itself a finding; they are
  accepted as non-structural only outside ``repository/`` and reported
  loudly.

If the pydori A/B finds structural diffs, the tool automatically reruns the
legacy build a second time and byte-compares legacy-vs-legacy to distinguish
backend divergence from in-process nondeterminism of the frozen build path
(which must be reported, not papered over).

Exit code 0 iff both A/Bs have zero structural diffs and both CLI smokes
succeed.

Usage::

    uv run python tools/ab_collection.py [--passes fast|standard|minimal]
        [--skip-cli-smoke] [--keep DIR]
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zlib
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))  # for `tests.*` fixture imports
sys.path.insert(0, str(REPO_ROOT / "test_projects"))  # for `pydori`

from sonolus.build.collection import Collection  # noqa: E402
from sonolus.build.project import load_resources_files_to_collection  # noqa: E402

GZIP_MAGIC = b"\x1f\x8b"
REPOSITORY_PREFIX = "sonolus/repository/"


# ---------------------------------------------------------------------------
# Backend selection (the same env var production reads in make_collection)
# ---------------------------------------------------------------------------


@contextmanager
def backend_env(backend: str):
    """Temporarily select a collection backend like production does.

    ``python`` unsets ``SONOLUS_BACKEND`` (the production default lane);
    ``rust`` sets it.
    """
    old = os.environ.get("SONOLUS_BACKEND")
    if backend == "python":
        os.environ.pop("SONOLUS_BACKEND", None)
    else:
        os.environ["SONOLUS_BACKEND"] = backend
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("SONOLUS_BACKEND", None)
        else:
            os.environ["SONOLUS_BACKEND"] = old


def expected_collection_type(backend: str) -> type:
    if backend == "rust":
        from sonolus.build.rust_collection import RustCollection

        return RustCollection
    return Collection


# ---------------------------------------------------------------------------
# Site-tree comparison
# ---------------------------------------------------------------------------


@dataclass
class TreeComparison:
    label: str
    total: int = 0
    byte_identical: int = 0
    # (path, level) for files that passed at a level other than `byte`
    non_byte_passes: list[tuple[str, str]] = field(default_factory=list)
    # human-readable structural diff descriptions
    diffs: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.diffs

    def report(self) -> str:
        lines = [f"--- {self.label} ---"]
        lines.append(
            f"files compared: {self.total}; byte-identical: {self.byte_identical}; "
            f"non-byte equivalences: {len(self.non_byte_passes)}; structural diffs: {len(self.diffs)}"
        )
        for path, level in self.non_byte_passes:
            lines.append(f"  [equivalent:{level}] {path}")
        for diff in self.diffs:
            lines.append(f"  [DIFF] {diff}")
        return "\n".join(lines)


def walk_files(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def try_gunzip(data: bytes) -> bytes | None:
    if not data.startswith(GZIP_MAGIC):
        return None
    try:
        return gzip.decompress(data)
    except (OSError, EOFError, zlib.error):
        return None


def try_json(data: bytes):
    try:
        return json.loads(data)
    except (ValueError, UnicodeDecodeError):
        return None


def compare_site_trees(label: str, legacy_dir: Path, rust_dir: Path) -> TreeComparison:
    result = TreeComparison(label)
    legacy_files = walk_files(legacy_dir)
    rust_files = walk_files(rust_dir)
    for path in sorted(set(legacy_files) - set(rust_files)):
        result.diffs.append(f"{path}: only in legacy tree")
    for path in sorted(set(rust_files) - set(legacy_files)):
        result.diffs.append(f"{path}: only in rust tree")

    for path in sorted(set(legacy_files) & set(rust_files)):
        result.total += 1
        legacy_data = legacy_files[path]
        rust_data = rust_files[path]
        if legacy_data == rust_data:
            result.byte_identical += 1
            continue

        # Diagnostics shared by all non-byte-equal cases.
        legacy_gz = try_gunzip(legacy_data)
        rust_gz = try_gunzip(rust_data)

        if path.startswith(REPOSITORY_PREFIX):
            # Content-addressed: byte equality is the only acceptable level.
            detail = "repository blob differs (content-addressed; must be byte-identical)"
            if legacy_gz is not None and rust_gz is not None and legacy_gz == rust_gz:
                detail += "; NOTE: gzip-decompressed contents ARE identical (container-level divergence)"
            result.diffs.append(f"{path}: {detail}")
            continue

        legacy_json = try_json(legacy_data)
        rust_json = try_json(rust_data)
        if legacy_json is not None and rust_json is not None:
            if legacy_json == rust_json:
                result.non_byte_passes.append((path, "json"))
            else:
                result.diffs.append(f"{path}: JSON differs structurally")
            continue

        if legacy_gz is not None and rust_gz is not None:
            if legacy_gz == rust_gz:
                result.non_byte_passes.append((path, "gzip"))
                continue
            inner_legacy = try_json(legacy_gz)
            inner_rust = try_json(rust_gz)
            if inner_legacy is not None and inner_rust is not None and inner_legacy == inner_rust:
                result.non_byte_passes.append((path, "gzip+json"))
                continue
            result.diffs.append(f"{path}: gzip members differ decompressed")
            continue

        result.diffs.append(f"{path}: bytes differ (non-JSON, non-gzip)")

    return result


def compare_trees_bytewise(legacy_dir: Path, rust_dir: Path) -> list[str]:
    """Strict byte comparison used for the legacy-vs-legacy determinism control."""
    a = walk_files(legacy_dir)
    b = walk_files(rust_dir)
    diffs = [f"{path}: only in first tree" for path in sorted(set(a) - set(b))]
    diffs += [f"{path}: only in second tree" for path in sorted(set(b) - set(a))]
    diffs += [f"{path}: bytes differ" for path in sorted(set(a) & set(b)) if a[path] != b[path]]
    return diffs


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def fixture_builders():
    """The T5.2 fixture builders, imported from the parity tests (one source of truth)."""
    from tests.build.test_collection import fixture_scp, source_tree

    return fixture_scp, source_tree


def make_pydori_resources(root: Path) -> None:
    """Minimal resources tree satisfying the pydori engine's references.

    The pydori engine declares ``skin="pixel"``, ``background="darkblue"``,
    ``particle="pixel"`` and no effect (so ``get_default_item("effects")``
    needs at least one effects item). Each item carries localized fields plus
    a binary and a ``.json`` resource (gzip path). The T5.2 fixture scp is
    dropped in as well so the full build exercises the scp loader and the
    resource-level engine override + linking.
    """
    fixture_scp, _ = fixture_builders()
    items = [
        ("skins", "pixel", "Pixel Skin"),
        ("backgrounds", "darkblue", "Dark Blue"),
        ("effects", "stub", "Stub SFX"),
        ("particles", "pixel", "Pixel Particles"),
    ]
    for category, name, title in items:
        item_dir = root / category / name
        item_dir.mkdir(parents=True)
        item_dir.joinpath("item.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "title": {"en": title, "ja": f"{title} 日本語"},
                    "subtitle": {"fr": "st-fr", "de": "st-de"},
                    "author": "A/B 作者",
                    "tags": [{"title": {"en": "t1"}, "icon": "i"}],
                    "meta": {"private": True},
                }
            ),
            encoding="utf-8",
        )
        item_dir.joinpath("thumbnail.png").write_bytes(b"\x89PNG-not-really-" + name.encode())
        item_dir.joinpath("data.json").write_bytes(json.dumps({"category": category, "name": name}).encode())
    root.joinpath("fixture.scp").write_bytes(fixture_scp())


def make_fixture_resources(root: Path) -> None:
    """The T5.2 synthetic fixture: scp + source tree from the parity tests."""
    fixture_scp, source_tree = fixture_builders()
    root.joinpath("fixture.scp").write_bytes(fixture_scp())
    source_tree(root)


# ---------------------------------------------------------------------------
# Builds
# ---------------------------------------------------------------------------


def load_pydori_project():
    import pydori.project

    return pydori.project.project


def build_pydori_site(backend: str, project, resources: Path, build_dir: Path, passes) -> Path:
    """Drives the production site-tree path (cli.build_collection) for one backend."""
    from sonolus.build.cli import build_collection
    from sonolus.script.project import BuildConfig

    project.resources = resources
    with backend_env(backend):
        collection = build_collection(project, build_dir, BuildConfig(passes=passes))
    actual, expected = type(collection), expected_collection_type(backend)
    if actual is not expected:
        raise RuntimeError(f"backend selection failed: got {actual.__name__}, expected {expected.__name__}")
    return build_dir / "site"


def build_fixture_site(backend: str, resources: Path, build_dir: Path) -> Path:
    """Drives the production resource-loading entry point for one backend."""
    with backend_env(backend):
        collection = load_resources_files_to_collection(resources)
        actual, expected = type(collection), expected_collection_type(backend)
        if actual is not expected:
            raise RuntimeError(f"backend selection failed: got {actual.__name__}, expected {expected.__name__}")
        collection.name = "AB Fixture"
        site_dir = build_dir / "site"
        site_dir.mkdir(parents=True, exist_ok=True)
        collection.write(site_dir)
    return site_dir


def run_pydori_ab(work: Path, passes) -> TreeComparison:
    resources = work / "resources"
    resources.mkdir(parents=True)
    make_pydori_resources(resources)
    project = load_pydori_project()

    legacy_site = build_pydori_site("python", project, resources, work / "legacy", passes)
    rust_site = build_pydori_site("rust", project, resources, work / "rust", passes)
    result = compare_site_trees("pydori site-tree A/B (legacy vs rust)", legacy_site, rust_site)

    if not result.ok:
        # Distinguish backend divergence from nondeterminism of the frozen
        # build path: rebuild legacy and byte-compare legacy-vs-legacy.
        control_site = build_pydori_site("python", project, resources, work / "legacy_control", passes)
        control_diffs = compare_trees_bytewise(legacy_site, control_site)
        if control_diffs:
            result.diffs.append(
                "DETERMINISM CONTROL FAILED: legacy-vs-legacy rebuild differs "
                f"({len(control_diffs)} files) - the frozen build path is nondeterministic in-process; "
                "the diffs above may not be backend divergences: " + "; ".join(control_diffs[:10])
            )
        else:
            result.diffs.append(
                "determinism control passed: legacy-vs-legacy rebuild byte-identical; "
                "the diffs above are real backend divergences"
            )
    return result


def run_fixture_ab(work: Path) -> TreeComparison:
    resources = work / "resources"
    resources.mkdir(parents=True)
    make_fixture_resources(resources)

    legacy_site = build_fixture_site("python", resources, work / "legacy")
    rust_site = build_fixture_site("rust", resources, work / "rust")
    return compare_site_trees("synthetic resources fixture A/B (legacy vs rust)", legacy_site, rust_site)


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


def run_cli_smoke(work: Path) -> tuple[bool, str]:
    """``sonolus-py build`` on test_projects/pydori in both backend modes."""
    lines = ["--- CLI smoke: sonolus-py build pydori (both backend modes) ---"]
    ok = True
    dist_dirs: dict[str, Path] = {}
    exe = shutil.which("sonolus-py")
    for backend in ("python", "rust"):
        build_dir = work / f"cli_{backend}"
        if exe:
            cmd = [exe, "build", "pydori", "--build-dir", str(build_dir)]
        else:  # cli.py has no __main__ guard; invoke the entry point directly
            cmd = [
                sys.executable,
                "-c",
                "from sonolus.build.cli import main; main()",
                "build",
                "pydori",
                "--build-dir",
                str(build_dir),
            ]
        env = os.environ.copy()
        if backend == "python":
            env.pop("SONOLUS_BACKEND", None)
        else:
            env["SONOLUS_BACKEND"] = backend
        proc = subprocess.run(
            cmd,
            cwd=REPO_ROOT / "test_projects",
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        dist = build_dir / "dist"
        produced = sorted(p.relative_to(dist).as_posix() for p in dist.rglob("*") if p.is_file()) if dist.is_dir() else []
        engine_ok = (dist / "engine").is_dir() and (dist / "levels" / "pydori_level").is_file()
        if proc.returncode != 0 or not engine_ok:
            ok = False
            lines.append(f"  {backend}: FAILED (exit {proc.returncode}, engine/levels present: {engine_ok})")
            lines.append("    stdout: " + proc.stdout.strip().replace("\n", "\n    "))
            lines.append("    stderr: " + proc.stderr.strip().replace("\n", "\n    "))
        else:
            dist_dirs[backend] = dist
            summary = next((line for line in proc.stdout.splitlines() if "built successfully" in line), "")
            lines.append(f"  {backend}: OK - {len(produced)} files under {dist} ({summary.strip()})")
            lines.append(f"    files: {', '.join(produced)}")
    if len(dist_dirs) == 2:
        dist_diffs = compare_trees_bytewise(dist_dirs["python"], dist_dirs["rust"])
        if dist_diffs:
            # `build` never touches a Collection, so cross-mode dist diffs are
            # cross-process nondeterminism of the frozen build, not a backend
            # divergence. Informational, but worth surfacing.
            lines.append(f"  note: dist trees differ across modes ({len(dist_diffs)} files; informational):")
            lines.extend(f"    {d}" for d in dist_diffs[:10])
        else:
            lines.append("  dist trees byte-identical across modes")
    lines.append(
        "  note: `build` writes dist/engine + dist/levels; the site tree is the dev-server "
        "path (build_collection), exercised directly by the pydori A/B above"
    )
    return ok, "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--passes",
        choices=["minimal", "fast", "standard"],
        default="fast",
        help="optimization passes for the pydori engine compile (frozen Python backend in both lanes; "
        "default: fast, the dev-server default for the site-tree path)",
    )
    parser.add_argument("--skip-cli-smoke", action="store_true", help="skip the sonolus-py build subprocess smoke")
    parser.add_argument(
        "--keep",
        type=Path,
        default=None,
        metavar="DIR",
        help="build under DIR and keep the outputs (default: temp dir, removed afterwards)",
    )
    args = parser.parse_args()

    from sonolus.script.project import BuildConfig

    passes = {
        "minimal": BuildConfig.MINIMAL_PASSES,
        "fast": BuildConfig.FAST_PASSES,
        "standard": BuildConfig.STANDARD_PASSES,
    }[args.passes]

    if args.keep is not None:
        args.keep.mkdir(parents=True, exist_ok=True)
        work_root = args.keep
        cleanup = None
    else:
        cleanup = tempfile.TemporaryDirectory(prefix="ab_collection_")
        work_root = Path(cleanup.name)

    failed = False
    try:
        print(f"work dir: {work_root}")

        pydori_result = run_pydori_ab(work_root / "pydori", passes)
        print(pydori_result.report())
        failed |= not pydori_result.ok

        fixture_result = run_fixture_ab(work_root / "fixture")
        print(fixture_result.report())
        failed |= not fixture_result.ok

        if not args.skip_cli_smoke:
            smoke_ok, smoke_report = run_cli_smoke(work_root / "cli")
            print(smoke_report)
            failed |= not smoke_ok

        print("OVERALL:", "FAIL" if failed else "PASS (zero structural diffs)")
    finally:
        if cleanup is not None:
            cleanup.cleanup()

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
