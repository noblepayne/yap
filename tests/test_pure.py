import json
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
_get_yap_done_tool = yap._get_yap_done_tool
_detect_yap_done = yap._detect_yap_done
API_URL = yap.API_URL
TIMEOUT = yap.TIMEOUT
MAX_HISTORY = yap.MAX_HISTORY
MAX_PUSH_ITERATIONS = yap.MAX_PUSH_ITERATIONS
NUDGE_MESSAGE = yap.NUDGE_MESSAGE
YAP_DONE_TOOL_NAME = yap.YAP_DONE_TOOL_NAME
PUSH_MODE_DISCLOSURE = yap.PUSH_MODE_DISCLOSURE
PUSH_MODE_SUMMARY_REQUEST = yap.PUSH_MODE_SUMMARY_REQUEST


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
    assert MAX_PUSH_ITERATIONS == 10
    assert "yap__done" in NUDGE_MESSAGE
    assert "system state" in NUDGE_MESSAGE.lower()
    assert "immediate inspection" in NUDGE_MESSAGE.lower()
    assert "VERIFIED" in NUDGE_MESSAGE
    assert "stuck or done" in NUDGE_MESSAGE


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
    assert chars == 42
    assert tokens == 10


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
    # "thinking" (8) + tool_calls JSON (82) + "result" (6) + 2*16 metadata = 128
    assert chars == 128
    assert tokens == 32  # 128 // 4


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
    assert "[TOOL: get_weather | ID: no-id]" in formatted
    assert '{"temp": 72}' in formatted


def test_count_context_full():
    history = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
    ]
    chars, tokens = _count_context("system", history)
    # "system" (6) + "hello" (5) + "world" (5) = 16 content, plus 3 messages * 16 overhead = 48 total
    assert chars == 48
    assert tokens == 12  # 48 // 4


def test_derive_session_id():
    import os

    path1 = "chat_history.jsonl"
    path2 = "./chat_history.jsonl"
    id1 = yap.derive_session_id(path1)
    id2 = yap.derive_session_id(path2)
    assert id1 == id2
    assert len(id1) == 16

    abs_path = os.path.abspath(path1)
    id3 = yap.derive_session_id(abs_path)
    assert id1 == id3


def test_unify_message():
    # String content + thought field
    msg = {"role": "assistant", "content": "hi", "thought": "thinking"}
    unified = yap._unify_message(msg)
    assert unified["content"] == [
        {"type": "thinking", "thinking": "thinking"},
        {"type": "text", "text": "hi"},
    ]

    # Block content + reasoning_content
    msg = {
        "role": "assistant",
        "content": [{"type": "text", "text": "hi"}],
        "reasoning_content": "thinking",
    }
    unified = yap._unify_message(msg)
    assert unified["content"] == [
        {"type": "thinking", "thinking": "thinking"},
        {"type": "text", "text": "hi"},
    ]


def test_extract_thoughts_blocks():
    blocks = [
        {"type": "thinking", "thinking": "I should say hello"},
        {"type": "text", "text": "Hello world"},
    ]
    thoughts, text = yap._extract_thoughts(blocks)
    assert thoughts == ["I should say hello"]
    assert text == "Hello world"


def test_extract_thoughts_string():
    content = "Hello world"
    thoughts, text = yap._extract_thoughts(content)
    assert thoughts == []
    assert text == "Hello world"


def test_format_chat_display_with_thoughts():
    history = [
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "plan"},
                {"type": "text", "text": "result"},
            ],
        }
    ]
    display = yap._format_chat_display(history)
    assert "▶ Reasoning" in display
    assert "plan" in display
    assert "result" in display


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


def test_yap_done_tool_schema():
    tool = _get_yap_done_tool()
    assert tool["type"] == "function"
    assert tool["function"]["name"] == "yap__done"
    assert "summary" in tool["function"]["parameters"]["properties"]
    assert tool["function"]["parameters"]["required"] == ["summary"]


def test_build_payload_with_tools():
    tools = [_get_yap_done_tool()]
    payload = _build_payload("gpt-4", [{"role": "user", "content": "hi"}], tools=tools)
    assert payload["model"] == "gpt-4"
    assert payload["messages"] == [{"role": "user", "content": "hi"}]
    assert payload["tools"] == tools


def test_build_payload_without_tools():
    payload = _build_payload("gpt-4", [{"role": "user", "content": "hi"}])
    assert payload["model"] == "gpt-4"
    assert payload["messages"] == [{"role": "user", "content": "hi"}]
    assert "tools" not in payload


def test_detect_yap_done_with_done_call():
    tool_calls = [
        {"function": {"name": "yap__done"}, "id": "call_1"},
    ]
    assert _detect_yap_done(tool_calls) is True


def test_detect_yap_done_without_done_call():
    tool_calls = [
        {"function": {"name": "other_tool"}, "id": "call_1"},
    ]
    assert _detect_yap_done(tool_calls) is False


def test_detect_yap_done_empty_list():
    assert _detect_yap_done([]) is False


def test_detect_yap_done_none():
    assert _detect_yap_done(None) is False


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


def test_build_payload_with_push_mode_on():
    payload = _build_payload(
        "gpt-4", [{"role": "user", "content": "hi"}], push_mode=True
    )
    assert payload["model"] == "gpt-4"
    assert "Push Mode" in payload["messages"][0]["content"]
    assert "Status: ON" in payload["messages"][0]["content"]


def test_build_payload_with_push_mode_off():
    payload = _build_payload(
        "gpt-4", [{"role": "user", "content": "hi"}], push_mode=False
    )
    assert payload["model"] == "gpt-4"
    assert "Push Mode" in payload["messages"][0]["content"]
    assert "Status: OFF" in payload["messages"][0]["content"]


def test_build_payload_with_system_and_push_mode():
    payload = _build_payload(
        "gpt-4",
        [{"role": "user", "content": "hi"}],
        "You are helpful",
        push_mode=True,
    )
    assert payload["model"] == "gpt-4"
    system_content = payload["messages"][0]["content"]
    assert "Push Mode" in system_content
    assert "Status: ON" in system_content
    assert "You are helpful" in system_content
    # Push mode disclosure should come FIRST (before user prompt)
    assert system_content.index("Push Mode") < system_content.index("You are helpful")


def test_build_payload_push_mode_none_no_disclosure():
    payload = _build_payload(
        "gpt-4", [{"role": "user", "content": "hi"}], push_mode=None
    )
    assert payload["model"] == "gpt-4"
    assert len(payload["messages"]) == 1


def test_push_mode_disclosure_contains_key_info():
    formatted = PUSH_MODE_DISCLOSURE.format(status="ON")
    assert "yap__done()" in formatted
    assert "re-submitted" in formatted
    assert "nudge" in formatted
    assert "final clean response" in formatted
    assert "no tools" in formatted


def test_push_mode_summary_request_includes_summary():
    assert "complete summary" in PUSH_MODE_SUMMARY_REQUEST
    assert "plain markdown" in PUSH_MODE_SUMMARY_REQUEST
    assert "yap__done" in PUSH_MODE_SUMMARY_REQUEST


def test_prepare_history_for_request_strips_thinking():
    history = [
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "thought1"},
                {"type": "text", "text": "text1"},
            ],
            "_meta": {"provider": "anthropic"},
        }
    ]
    # Same provider
    projected = yap._prepare_history_for_request(history, "anthropic")
    assert projected[0]["content"][0]["type"] == "thinking"
    assert "_meta" not in projected[0]

    # Different provider
    projected = yap._prepare_history_for_request(history, "openai")
    assert projected[0]["content"][0]["type"] == "text"
    assert "omitted" in projected[0]["content"][0]["text"]
    assert "_meta" not in projected[0]


def test_parse_footer_valid():
    import base64
    import hmac
    import hashlib
    import os

    secret = "test-secret"
    os.environ["INJECTOR_HMAC_SECRET"] = secret

    data = json.dumps(
        {
            "turns": [
                {"role": "assistant", "content": [{"type": "text", "text": "sub-turn"}]}
            ]
        }
    )
    hmac_val = hmac.new(secret.encode(), data.encode(), hashlib.sha256).hexdigest()
    envelope = json.dumps({"data": data, "hmac": hmac_val})
    b64_envelope = base64.b64encode(envelope.encode()).decode()

    text = f"Final answer\n\n<!-- x-injector-v1\n{b64_envelope}\n-->"
    payload, clean_text = yap._parse_footer(text)

    assert clean_text == "Final answer"
    assert payload["turns"][0]["role"] == "assistant"


def test_validate_turns_rejects_user():
    turns = [
        {"role": "user", "content": [{"type": "text", "text": "hack"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
    ]
    validated = yap._validate_turns(turns)
    assert len(validated) == 1
    assert validated[0]["role"] == "assistant"


def test_yap_done_tool_name_constant():
    assert YAP_DONE_TOOL_NAME == "yap__done"
    assert _get_yap_done_tool()["function"]["name"] == YAP_DONE_TOOL_NAME
