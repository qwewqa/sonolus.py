import sys

import pytest


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
