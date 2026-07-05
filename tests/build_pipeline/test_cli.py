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
