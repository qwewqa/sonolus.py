"""Tests for sonolus.build.project build helpers."""

from types import SimpleNamespace

from sonolus.build.collection import Collection


def test_build_project_accepts_none_config(monkeypatch):
    from sonolus.build import project as project_mod

    monkeypatch.setattr(project_mod, "add_engine_to_collection", lambda *a, **k: None)
    stub_project = SimpleNamespace(converters={}, engine=SimpleNamespace(name="stub"), levels=[])

    project_mod.build_project_to_existing_collection(stub_project, Collection(), None)
