import copy
import json
import sys
import types
from pathlib import Path
from time import time

import pytest
from hypothesis import given
from hypothesis import strategies as st

from sonolus.build.collection import Collection
from sonolus.build.dev_server import (
    DecodeCommand,
    ExitCommand,
    HelpCommand,
    RebuildCommand,
    ServerState,
    parse_dev_command,
)
from sonolus.build.level import package_level_data
from sonolus.build.project import load_resources_files_to_collection
from sonolus.script.engine import Engine, EngineData
from sonolus.script.internal.context import ProjectContextState
from sonolus.script.internal.error import CompilationError
from sonolus.script.level import ExternalLevelData, LevelData
from sonolus.script.project import BuildConfig, Project


def test_parse_rebuild_command_full():
    result = parse_dev_command("rebuild")
    assert isinstance(result, RebuildCommand)


def test_parse_rebuild_command_alias():
    result = parse_dev_command("r")
    assert isinstance(result, RebuildCommand)


def test_parse_rebuild_command_with_extra_args_invalid():
    result = parse_dev_command("rebuild extra")
    assert result is None


def test_parse_rebuild_command_with_extra_args_invalid_alias():
    result = parse_dev_command("r extra")
    assert result is None


def test_parse_decode_command_full():
    result = parse_dev_command("decode 42")
    assert isinstance(result, DecodeCommand)
    assert result.message_code == 42


def test_parse_decode_command_alias():
    result = parse_dev_command("d 123")
    assert isinstance(result, DecodeCommand)
    assert result.message_code == 123


def test_parse_decode_command_zero():
    result = parse_dev_command("decode 0")
    assert isinstance(result, DecodeCommand)
    assert result.message_code == 0


def test_parse_decode_command_negative():
    result = parse_dev_command("decode -5")
    assert isinstance(result, DecodeCommand)
    assert result.message_code == -5


def test_parse_decode_command_missing_arg():
    result = parse_dev_command("decode")
    assert result is None


def test_parse_decode_command_missing_arg_alias():
    result = parse_dev_command("d")
    assert result is None


def test_parse_decode_command_non_int_arg():
    result = parse_dev_command("decode abc")
    assert result is None


def test_parse_decode_command_non_int_arg_alias():
    result = parse_dev_command("d xyz")
    assert result is None


def test_parse_decode_command_extra_args():
    result = parse_dev_command("decode 42 extra")
    assert result is None


def test_parse_help_command_full():
    result = parse_dev_command("help")
    assert isinstance(result, HelpCommand)


def test_parse_help_command_alias():
    result = parse_dev_command("h")
    assert isinstance(result, HelpCommand)


def test_parse_help_command_with_extra_args_invalid():
    result = parse_dev_command("help extra")
    assert result is None


def test_parse_help_command_with_extra_args_invalid_alias():
    result = parse_dev_command("h extra")
    assert result is None


def test_parse_quit_command_full():
    result = parse_dev_command("quit")
    assert isinstance(result, ExitCommand)


def test_parse_quit_command_alias():
    result = parse_dev_command("q")
    assert isinstance(result, ExitCommand)


def test_parse_quit_command_with_extra_args_invalid():
    result = parse_dev_command("quit extra")
    assert result is None


def test_parse_quit_command_with_extra_args_invalid_alias():
    result = parse_dev_command("q extra")
    assert result is None


def test_parse_unknown_command():
    result = parse_dev_command("unknown")
    assert result is None


def test_parse_empty_command():
    result = parse_dev_command("")
    assert result is None


def test_parse_whitespace_only():
    result = parse_dev_command("   ")
    assert result is None


def test_parse_invalid_shell_syntax_unclosed_quote():
    result = parse_dev_command('decode "unclosed')
    assert result is None


def test_parse_invalid_shell_syntax_unclosed_single_quote():
    result = parse_dev_command("decode 'unclosed")
    assert result is None


@given(st.text())
def test_parse_dev_command_arbitrary_strings(command_line):
    # Should never throw
    result = parse_dev_command(command_line)
    assert result is None or hasattr(result, "execute")


ORIGINAL_BGM_OFFSET = 1.0
FIXTURE_MODULE_NAME = "dev_server_fixture_project"
FIXTURE_LEVEL_NAME = "resource_level"


def _write_resource_level(resources: Path) -> None:
    level_dir = resources / "levels" / FIXTURE_LEVEL_NAME
    level_dir.mkdir(parents=True)
    # link() reads the engine and the useSkin family off every level, so a level that omits them cannot survive
    # write_collection.
    level_dir.joinpath("item.json").write_text(
        json.dumps(
            {
                "version": 1,
                "rating": 1,
                "title": FIXTURE_LEVEL_NAME,
                "artists": "Unknown",
                "author": "Unknown",
                "tags": [],
                "engine": "some_other_engine",
                "useSkin": {"useDefault": True},
                "useBackground": {"useDefault": True},
                "useEffect": {"useDefault": True},
                "useParticle": {"useDefault": True},
            }
        ),
        encoding="utf-8",
    )
    # No suffix, so load_from_source stores these bytes as-is rather than gzipping them again.
    level_dir.joinpath("data").write_bytes(package_level_data(LevelData(bgm_offset=ORIGINAL_BGM_OFFSET, entities=[])))


def test_rebuild_feeds_converters_pristine_resource_levels(tmp_path, monkeypatch):
    resources = tmp_path / "resources"
    resources.mkdir()
    _write_resource_level(resources)

    seen_bgm_offsets = []

    def converter(data: ExternalLevelData) -> LevelData:
        seen_bgm_offsets.append(data.bgm_offset)
        return LevelData(bgm_offset=data.bgm_offset + 1.0, entities=[])

    def stub_add_engine_to_collection(collection, project, engine, config, project_state=None):
        # Skips compiling the engine, but still adds the item link() resolves each level's engine name against.
        collection.add_item("engines", engine.name, {"name": engine.name, "version": engine.version})

    monkeypatch.setattr("sonolus.build.project.add_engine_to_collection", stub_add_engine_to_collection)

    config = BuildConfig()
    project = Project(
        engine=Engine(name="dev_server_test_engine", data=EngineData()),
        resources=resources,
        converters={None: converter},
    )

    project_module = types.ModuleType(FIXTURE_MODULE_NAME)
    project_module.project = project
    monkeypatch.setitem(sys.modules, FIXTURE_MODULE_NAME, project_module)

    base_collection = load_resources_files_to_collection(resources)
    original_hash = base_collection.categories["levels"][FIXTURE_LEVEL_NAME]["item"]["data"]["hash"]

    server_state = ServerState(
        project=project,
        project_module_name=FIXTURE_MODULE_NAME,
        core_module_names=set(sys.modules),
        build_dir=tmp_path / "build",
        config=config,
        project_state=ProjectContextState.from_build_config(config),
        base_collection=base_collection,
        collection=copy.deepcopy(base_collection),
        last_build_time=time(),
    )

    rebuild_count = 4
    for i in range(rebuild_count):
        # Re-snapshot so the module purge stays a no-op. last_build_time then decides the resources mtime gate:
        # ahead of now for a dev loop where only Python sources changed, and 0 on the last pass to exercise the
        # reload branch too.
        server_state.core_module_names = set(sys.modules)
        server_state.last_build_time = 0 if i == rebuild_count - 1 else time() + 3600
        RebuildCommand().execute(server_state)

    assert seen_bgm_offsets == [ORIGINAL_BGM_OFFSET] * rebuild_count
    assert base_collection.categories["levels"][FIXTURE_LEVEL_NAME]["item"]["data"]["hash"] == original_hash
    assert "engines" not in base_collection.categories
    # The last pass opened the gate, so the reload has to have landed on base_collection.
    assert server_state.base_collection is not base_collection
    reloaded_level = server_state.base_collection.categories["levels"][FIXTURE_LEVEL_NAME]["item"]
    assert reloaded_level["data"]["hash"] == original_hash
    assert "engines" not in server_state.base_collection.categories
    assert server_state.collection.categories["levels"][FIXTURE_LEVEL_NAME]["item"]["data"]["hash"] != original_hash


def test_rebuild_reloads_resources_when_the_project_resource_path_changes(tmp_path, monkeypatch):
    old_resources = tmp_path / "old_resources"
    new_resources = tmp_path / "new_resources"
    old_resources.mkdir()
    new_resources.mkdir()

    def make_project(resources):
        return Project(engine=Engine(name="dev_server_test_engine", data=EngineData()), resources=resources)

    installed_project = make_project(old_resources)
    project_module = types.ModuleType(FIXTURE_MODULE_NAME)
    project_module.project = make_project(new_resources)
    monkeypatch.setitem(sys.modules, FIXTURE_MODULE_NAME, project_module)

    loaded_paths = []
    reloaded_collection = Collection()

    def load_resources(path):
        loaded_paths.append(path)
        return reloaded_collection

    monkeypatch.setattr("sonolus.build.dev_server.load_resources_files_to_collection", load_resources)
    monkeypatch.setattr("sonolus.build.dev_server.build_project_to_existing_collection", lambda *args, **kwargs: None)

    import sonolus.build.cli

    monkeypatch.setattr(sonolus.build.cli, "write_collection", lambda *args, **kwargs: None)

    config = BuildConfig()
    server_state = ServerState(
        project=installed_project,
        project_module_name=FIXTURE_MODULE_NAME,
        core_module_names=set(sys.modules),
        build_dir=tmp_path / "build",
        config=config,
        project_state=ProjectContextState.from_build_config(config),
        base_collection=Collection(),
        collection=Collection(),
        last_build_time=time() + 3600,
    )

    RebuildCommand().execute(server_state)

    assert loaded_paths == [new_resources]
    assert server_state.base_collection is reloaded_collection


INSTALLED_MAPPINGS = {"FIRST-ARCHETYPE-MESSAGE": 1, "SECOND-ARCHETYPE-MESSAGE": 2}


def _make_rollback_server_state(tmp_path, monkeypatch):
    # A server whose installed build succeeded, plus a fixture module holding the different project object the
    # next rebuild imports, so a commit of either project or project_state is visible by identity.
    resources = tmp_path / "resources"
    resources.mkdir()

    def make_project():
        return Project(engine=Engine(name="dev_server_test_engine", data=EngineData()), resources=resources)

    installed_project = make_project()
    project_module = types.ModuleType(FIXTURE_MODULE_NAME)
    project_module.project = make_project()
    monkeypatch.setitem(sys.modules, FIXTURE_MODULE_NAME, project_module)

    config = BuildConfig()
    installed_state = ProjectContextState.from_build_config(config)
    installed_state.debug_str_mappings.update(INSTALLED_MAPPINGS)

    server_state = ServerState(
        project=installed_project,
        project_module_name=FIXTURE_MODULE_NAME,
        # Snapshotted after the fixture module is in place, so the module purge leaves it alone.
        core_module_names=set(sys.modules),
        build_dir=tmp_path / "build",
        config=config,
        project_state=installed_state,
        base_collection=Collection(),
        collection=Collection(),
        # Ahead of now, so the resources mtime gate stays shut and only the build decides the outcome.
        last_build_time=time() + 3600,
    )
    return server_state, installed_state, installed_project


def _record_partial_mapping(project_state):
    # Codes are handed out by first-touch order, so a build that stops partway does not merely lose codes: it
    # rebinds ones the running build already issued.
    project_state.debug_str_mappings["NEWLY-ADDED-MESSAGE"] = 1
    project_state.debug_str_mappings["FIRST-ARCHETYPE-MESSAGE"] = 2


def _assert_installed_build_survived(server_state, installed_state, installed_project, last_build_time):
    assert server_state.project_state is installed_state
    assert server_state.project_state.debug_str_mappings == INSTALLED_MAPPINGS
    assert server_state.project is installed_project
    assert server_state.last_build_time == last_build_time


def test_rebuild_failing_to_compile_keeps_the_installed_debug_message_mapping(tmp_path, monkeypatch, capsys):
    server_state, installed_state, installed_project = _make_rollback_server_state(tmp_path, monkeypatch)
    last_build_time = server_state.last_build_time
    build_states = []

    def failing_build(project, collection, config, project_state=None):
        build_states.append(project_state)
        _record_partial_mapping(project_state)
        raise CompilationError("Try statements are not supported")

    monkeypatch.setattr("sonolus.build.dev_server.build_project_to_existing_collection", failing_build)

    RebuildCommand().execute(server_state)

    # The build must number the new mappings from scratch rather than extend the installed ones.
    assert len(build_states) == 1
    assert build_states[0] is not installed_state
    _assert_installed_build_survived(server_state, installed_state, installed_project, last_build_time)

    capsys.readouterr()
    DecodeCommand(message_code=1).execute(server_state)
    assert capsys.readouterr().out.strip() == "FIRST-ARCHETYPE-MESSAGE"


def test_rebuild_failing_with_an_unhandled_error_keeps_the_installed_debug_message_mapping(tmp_path, monkeypatch):
    # Only CompilationError is caught here; anything else reaches run_server's catch-all, which prints and keeps
    # the same server state, so the rollback cannot live in the CompilationError handler.
    server_state, installed_state, installed_project = _make_rollback_server_state(tmp_path, monkeypatch)
    last_build_time = server_state.last_build_time

    def failing_build(project, collection, config, project_state=None):
        _record_partial_mapping(project_state)
        raise RuntimeError("build blew up")

    monkeypatch.setattr("sonolus.build.dev_server.build_project_to_existing_collection", failing_build)

    with pytest.raises(RuntimeError, match="build blew up"):
        RebuildCommand().execute(server_state)

    _assert_installed_build_survived(server_state, installed_state, installed_project, last_build_time)


def test_rebuild_failing_to_write_keeps_the_installed_debug_message_mapping(tmp_path, monkeypatch):
    # The client keeps running the previously served build until write_collection succeeds, so a write failure
    # must leave the mapping alone too.
    server_state, installed_state, installed_project = _make_rollback_server_state(tmp_path, monkeypatch)
    last_build_time = server_state.last_build_time

    def succeeding_build(project, collection, config, project_state=None):
        _record_partial_mapping(project_state)

    def failing_write(collection, build_dir, clear=True):
        raise OSError("cannot write the collection")

    # An earlier rebuild's module purge can leave sonolus.build.cli out of sys.modules while sonolus.build keeps
    # the stale attribute, so import it back first: that decides which module object gets patched, and the
    # snapshot then keeps this rebuild's deferred import from reloading it.
    import sonolus.build.cli

    monkeypatch.setattr("sonolus.build.dev_server.build_project_to_existing_collection", succeeding_build)
    monkeypatch.setattr(sonolus.build.cli, "write_collection", failing_write)
    server_state.core_module_names = set(sys.modules)

    with pytest.raises(OSError, match="cannot write the collection"):
        RebuildCommand().execute(server_state)

    _assert_installed_build_survived(server_state, installed_state, installed_project, last_build_time)


def _rebuild_output_for(server_state, failing_build, monkeypatch, capsys) -> str:
    monkeypatch.setattr("sonolus.build.dev_server.build_project_to_existing_collection", failing_build)
    capsys.readouterr()
    RebuildCommand().execute(server_state)
    return capsys.readouterr().out


def test_rebuild_failure_with_no_compiled_frame_omits_the_verbose_hint(tmp_path, monkeypatch, capsys):
    # A failure raised outside compiled code, such as an optimizer-stage one, already prints in full, so the
    # hint would promise detail -v does not add. The CLI applies the same gate.
    server_state, _, _ = _make_rollback_server_state(tmp_path, monkeypatch)

    def failing_build(project, collection, config, project_state=None):
        raise CompilationError("Optimization failed for callback 'update'")

    assert "--verbose" not in _rebuild_output_for(server_state, failing_build, monkeypatch, capsys)


def test_rebuild_failure_in_compiled_code_keeps_the_verbose_hint(tmp_path, monkeypatch, capsys):
    # The other direction, so the gate cannot be tightened into always-off. `_filter_traceback_` in a frame's
    # globals is what the visitor marks compiled code with, and it is what makes the summary drop frames.
    server_state, _, _ = _make_rollback_server_state(tmp_path, monkeypatch)

    def failing_build(project, collection, config, project_state=None):
        raise CompilationError("Try statements are not supported")

    from_compiled_code = types.FunctionType(
        failing_build.__code__,
        {**failing_build.__globals__, "_filter_traceback_": True},
        argdefs=failing_build.__defaults__,
    )

    assert "--verbose" in _rebuild_output_for(server_state, from_compiled_code, monkeypatch, capsys)
