from hypothesis import given
from hypothesis import strategies as st

from sonolus.build.dev_server import (
    DecodeCommand,
    ExitCommand,
    HelpCommand,
    RebuildCommand,
    parse_dev_command,
)


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
