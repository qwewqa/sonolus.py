import copy
import json
import sys
import types
from pathlib import Path
from time import time

from hypothesis import given
from hypothesis import strategies as st

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
