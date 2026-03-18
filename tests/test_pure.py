import sys
from pathlib import Path

import importlib.util

root = Path(__file__).parent.parent
sys.path.insert(0, str(root))  # noqa: E402

spec = importlib.util.spec_from_file_location("yap", root / "yap.py")
yap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(yap)

_strip_ansi = yap._strip_ansi
_safe_write = yap._safe_write
_estimate_tokens = yap._estimate_tokens
_count_context = yap._count_context
_truncate_history = yap._truncate_history
_build_payload = yap._build_payload
_load_history = yap._load_history
_save_history = yap._save_history
_load_prompt_file = yap._load_prompt_file
API_URL = yap.API_URL
TIMEOUT = yap.TIMEOUT
MAX_HISTORY = yap.MAX_HISTORY


def test_strip_ansi_basic():
    assert _strip_ansi("hello") == "hello"
    assert _strip_ansi("") == ""


def test_strip_ansi_colors():
    assert _strip_ansi("\x1b[31mred\x1b[0m") == "red"
    assert _strip_ansi("\x1b[32mgreen\x1b[0m") == "green"


def test_strip_ansi_cursor():
    assert _strip_ansi("\x1b[2J") == ""  # clear screen
    assert _strip_ansi("\x1b[H") == ""  # cursor home


def test_strip_ansi_mixed():
    result = _strip_ansi("hello \x1b[1;32mworld\x1b[0m!")
    assert result == "hello world!"


def test_safe_write(tmp_path):
    test_file = tmp_path / "test.txt"
    _safe_write(test_file, "hello world")
    assert test_file.read_text() == "hello world"


def test_safe_write_overwrite(tmp_path):
    test_file = tmp_path / "test.txt"
    test_file.write_text("old")
    _safe_write(test_file, "new")
    assert test_file.read_text() == "new"


def test_config_defaults():
    assert API_URL == "http://lattice:8089/v1/chat/completions"
    assert TIMEOUT == 3600
    assert MAX_HISTORY == 50


def test_estimate_tokens():
    assert _estimate_tokens("hello") == 1
    assert _estimate_tokens("abcd") == 1
    assert _estimate_tokens("a" * 100) == 25
    assert _estimate_tokens("") == 0


def test_count_context_empty():
    chars, tokens = _count_context("", [])
    assert chars == 0
    assert tokens == 0


def test_count_context_with_prompt():
    chars, tokens = _count_context("system prompt", [])
    assert chars == 13
    assert tokens == 3


def test_count_context_with_history():
    history = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
    ]
    chars, tokens = _count_context("", history)
    assert chars == 10
    assert tokens == 2


def test_count_context_with_tool_calls():
    # history with tool calls should count toward context if they have content
    # (simplistic token counting here just counts chars in content)
    history = [
        {
            "role": "assistant",
            "content": "thinking",
            "tool_calls": [
                {
                    "id": "1",
                    "type": "function",
                    "function": {"name": "test", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "content": "result", "tool_call_id": "1", "name": "test"},
    ]
    chars, tokens = _count_context("", history)
    assert chars == 14  # "thinking" + "result"
    assert tokens == 3


def test_format_chat_display_tool_calls():
    _format_chat_display = yap._format_chat_display
    history = [
        {
            "role": "assistant",
            "content": "I will call a tool.",
            "tool_calls": [
                {
                    "id": "call_123",
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "arguments": '{"location": "San Francisco"}',
                    },
                }
            ],
        }
    ]
    formatted = _format_chat_display(history)
    assert "[ASSISTANT]" in formatted
    assert "I will call a tool." in formatted
    assert "[TOOL CALLS]" in formatted
    assert "get_weather" in formatted
    assert "San Francisco" in formatted


def test_format_chat_display_tool_role():
    _format_chat_display = yap._format_chat_display
    history = [{"role": "tool", "name": "get_weather", "content": '{"temp": 72}'}]
    formatted = _format_chat_display(history)
    assert "[TOOL: get_weather]" in formatted
    assert '{"temp": 72}' in formatted


def test_count_context_full():
    history = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
    ]
    chars, tokens = _count_context("system", history)
    assert chars == 16
    assert tokens == 4


def test_truncate_history():
    history = [{"role": "user", "content": str(i)} for i in range(100)]
    result = _truncate_history(history, 50)
    assert len(result) == 50
    assert result[0]["content"] == "50"


def test_truncate_history_under_limit():
    history = [{"role": "user", "content": str(i)} for i in range(10)]
    result = _truncate_history(history, 50)
    assert len(result) == 10


def test_build_payload_no_system():
    payload = _build_payload("gpt-4", [{"role": "user", "content": "hi"}])
    assert payload["model"] == "gpt-4"
    assert payload["messages"] == [{"role": "user", "content": "hi"}]


def test_build_payload_with_system():
    payload = _build_payload(
        "gpt-4", [{"role": "user", "content": "hi"}], "you are helpful"
    )
    assert payload["model"] == "gpt-4"
    assert payload["messages"][0] == {"role": "system", "content": "you are helpful"}
    assert payload["messages"][1] == {"role": "user", "content": "hi"}


def test_load_history_missing_file(tmp_path):
    result = _load_history(tmp_path / "nonexistent.jsonl")
    assert result == []


def test_load_history_empty_file(tmp_path):
    hist_file = tmp_path / "empty.jsonl"
    hist_file.write_text("")
    result = _load_history(hist_file)
    assert result == []


def test_load_history_valid(tmp_path):
    hist_file = tmp_path / "history.jsonl"
    hist_file.write_text(
        '{"role": "user", "content": "hello"}\n{"role": "assistant", "content": "world"}\n'
    )
    result = _load_history(hist_file)
    assert len(result) == 2
    assert result[0] == {"role": "user", "content": "hello"}
    assert result[1] == {"role": "assistant", "content": "world"}


def test_save_and_load_history(tmp_path):
    hist_file = tmp_path / "history.jsonl"
    history = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
    ]
    _save_history(hist_file, history)
    loaded = _load_history(hist_file)
    assert loaded == history


def test_save_history_empty(tmp_path):
    hist_file = tmp_path / "empty.jsonl"
    _save_history(hist_file, [])
    assert hist_file.read_text() == ""


def test_load_prompt_file(tmp_path):
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("You are a helpful assistant.")
    result = _load_prompt_file(prompt_file)
    assert result == "You are a helpful assistant."


def test_load_prompt_file_missing(tmp_path):
    import pytest

    with pytest.raises(FileNotFoundError):
        _load_prompt_file(tmp_path / "nonexistent.txt")
