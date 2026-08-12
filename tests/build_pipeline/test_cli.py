import argparse
import json
import sys
from types import SimpleNamespace

import pytest

from sonolus.build.cli import build_project, get_config, get_runtime_checks, main
from sonolus.script.internal.context import RuntimeChecks


def test_import_project_non_package_module_falls_through(tmp_path, monkeypatch, capsys):
    # A top-level (non-package) module that imports fine but has no `project` attribute and
    # no `.project` submodule must produce the clean "No Project instance found" message,
    # not a raw ModuleNotFoundError ("... is not a package") traceback.
    (tmp_path / "lonemod_xyz.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delitem(sys.modules, "lonemod_xyz", raising=False)

    from sonolus.build.cli import import_project

    result = import_project("lonemod_xyz")
    assert result == (None, None, None)
    assert "No Project instance found" in capsys.readouterr().out


def test_import_project_reraises_real_inner_import_error(tmp_path, monkeypatch):
    # A genuine broken import inside the target's .project submodule must still propagate,
    # not be swallowed by the "submodule not found" fall-through.
    pkg = tmp_path / "pkg_bad_xyz"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "project.py").write_text("import definitely_missing_dep_xyz\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.chdir(tmp_path)
    for mod in ("pkg_bad_xyz", "pkg_bad_xyz.project"):
        monkeypatch.delitem(sys.modules, mod, raising=False)

    from sonolus.build.cli import import_project

    with pytest.raises(ModuleNotFoundError) as exc_info:
        import_project("pkg_bad_xyz")
    assert exc_info.value.name == "definitely_missing_dep_xyz"


def test_import_project_absent_top_module_reports_gracefully(capsys):
    from sonolus.build.cli import import_project

    result = import_project("definitely_not_a_real_module_xyztest")

    assert result == (None, None, None)
    assert "No Project instance found" in capsys.readouterr().out


def test_import_project_dotted_path_through_non_package_reports_gracefully(tmp_path, monkeypatch, capsys):
    (tmp_path / "gamemod_xyz.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.chdir(tmp_path)
    for mod in ("gamemod_xyz", "gamemod_xyz.sub", "gamemod_xyz.sub.project"):
        monkeypatch.delitem(sys.modules, mod, raising=False)

    from sonolus.build.cli import import_project

    result = import_project("gamemod_xyz.sub")
    assert result == (None, None, None)
    assert "No Project instance found" in capsys.readouterr().out


def test_import_project_rejects_a_non_project_value(tmp_path, monkeypatch):
    (tmp_path / "notaproject_xyz.py").write_text("project = 42\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delitem(sys.modules, "notaproject_xyz", raising=False)

    from sonolus.build.cli import import_project

    with pytest.raises(TypeError, match="Expected project in module notaproject_xyz to be a Project instance, got int"):
        import_project("notaproject_xyz")


def test_import_project_plain_import_error_in_project_submodule_propagates(tmp_path, monkeypatch):
    pkg = tmp_path / "pkg_circ_xyz"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "project.py").write_text(
        'raise ImportError("simulated circular import", name="pkg_circ_xyz.project")\n',
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.chdir(tmp_path)
    for mod in ("pkg_circ_xyz", "pkg_circ_xyz.project"):
        monkeypatch.delitem(sys.modules, mod, raising=False)

    from sonolus.build.cli import import_project

    with pytest.raises(ImportError) as exc_info:
        import_project("pkg_circ_xyz")
    assert not isinstance(exc_info.value, ModuleNotFoundError)
    assert exc_info.value.name == "pkg_circ_xyz.project"


SCHEMA_PROJECT_MODULE = """
from sonolus.script.archetype import PlayArchetype, imported
from sonolus.script.engine import Engine, EngineData, PlayMode
from sonolus.script.project import Project


class Note(PlayArchetype):
    beat: float = imported(name="#BEAT")


project = Project(engine=Engine(name="test", data=EngineData(play=PlayMode(archetypes=[Note]))))
"""


def test_schema_command_stdout_parses_as_json(tmp_path, monkeypatch, capsys):
    (tmp_path / "schemamod_xyz.py").write_text(SCHEMA_PROJECT_MODULE, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delitem(sys.modules, "schemamod_xyz", raising=False)
    monkeypatch.setattr(sys, "argv", ["sonolus-py", "schema", "schemamod_xyz"])

    limit = sys.getrecursionlimit()
    try:
        main()
    finally:
        # main() raises the limit for the compiler; keep that out of the rest of the session.
        sys.setrecursionlimit(limit)

    captured = capsys.readouterr()
    # The schema's shape is out of scope here: that stdout parses at all is the whole assertion.
    json.loads(captured.out)
    assert "Project imported in" in captured.err


def _stub_project(*level_names: str, engine_name: str = "engine") -> SimpleNamespace:
    return SimpleNamespace(
        engine=SimpleNamespace(name=engine_name),
        levels=[SimpleNamespace(name=name) for name in level_names],
    )


def test_build_project_rejects_a_level_name_with_a_separator(tmp_path):
    with pytest.raises(ValueError, match="Level name 'sub/level' is not a usable filename"):
        build_project(_stub_project("sub/level"), tmp_path, None)


def test_build_project_rejects_a_reserved_level_name(tmp_path):
    with pytest.raises(ValueError, match="Level name 'info' is reserved"):
        build_project(_stub_project("info"), tmp_path, None)


def test_build_project_rejects_a_bad_level_name_before_clearing_dist(tmp_path):
    previous = tmp_path / "dist" / "engine"
    previous.parent.mkdir(parents=True)
    previous.write_bytes(b"previous build")

    with pytest.raises(ValueError, match=r"Level name '\.\.' is not a usable filename"):
        build_project(_stub_project("good", ".."), tmp_path, None)

    assert previous.read_bytes() == b"previous build"


# The engine name is a filename on the collection path that `dev` serves, so build rejecting it there too keeps
# the two commands agreeing, ahead of the compile the collection path only fails after.
@pytest.mark.parametrize(
    ("engine_name", "message"),
    [
        ("info", "Engine name 'info' is reserved"),
        ("list", "Engine name 'list' is reserved"),
        ("sub/engine", "Engine name 'sub/engine' is not a usable filename"),
        ("..", r"Engine name '\.\.' is not a usable filename"),
    ],
)
def test_build_project_rejects_a_bad_engine_name(engine_name: str, message: str, tmp_path):
    with pytest.raises(ValueError, match=message):
        build_project(_stub_project(engine_name=engine_name), tmp_path, None)


def test_build_project_rejects_a_bad_engine_name_before_clearing_dist(tmp_path):
    previous = tmp_path / "dist" / "engine"
    previous.parent.mkdir(parents=True)
    previous.write_bytes(b"previous build")

    with pytest.raises(ValueError, match="Engine name 'info' is reserved"):
        build_project(_stub_project("good", engine_name="info"), tmp_path, None)

    assert previous.read_bytes() == b"previous build"


def _args(command: str, **overrides) -> argparse.Namespace:
    """Build the namespace argparse produces for the given subcommand when no flag is passed."""
    defaults = {
        "command": command,
        "optimize_minimal": False,
        "optimize_fast": False,
        "optimize_standard": False,
        "runtime_checks": None,
        "play": False,
        "watch": False,
        "preview": False,
        "tutorial": False,
        "verbose": False,
    }
    return argparse.Namespace(**(defaults | overrides))


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("dev", RuntimeChecks.NOTIFY_AND_TERMINATE),
        ("build", RuntimeChecks.NONE),
        ("check", RuntimeChecks.NONE),
    ],
)
def test_get_runtime_checks_default_per_command(command, expected):
    assert get_runtime_checks(_args(command)) == expected


def test_get_runtime_checks_flag_overrides_the_dev_default():
    assert get_runtime_checks(_args("dev", runtime_checks="none")) == RuntimeChecks.NONE


def test_get_config_builds_every_component_when_none_is_requested():
    config = get_config(_args("build"))

    assert (config.build_play, config.build_watch, config.build_preview, config.build_tutorial) == (
        True,
        True,
        True,
        True,
    )


def test_get_config_narrows_to_the_requested_components():
    config = get_config(_args("build", play=True, preview=True))

    assert (config.build_play, config.build_watch, config.build_preview, config.build_tutorial) == (
        True,
        False,
        True,
        False,
    )


def test_get_config_takes_runtime_checks_from_the_command():
    assert get_config(_args("dev")).runtime_checks == RuntimeChecks.NOTIFY_AND_TERMINATE
    assert get_config(_args("build")).runtime_checks == RuntimeChecks.NONE
