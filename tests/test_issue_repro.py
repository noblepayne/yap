from pathlib import Path

root = Path(__file__).parent.parent

from yap_module import yap

_count_context = yap._count_context


def test_count_context_none_content_crash():
    """Reproduce the crash when content is None (assistant message with tool calls)."""
    history = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_123",
                    "type": "function",
                    "function": {"name": "test_tool", "arguments": "{}"},
                }
            ],
        }
    ]
    # This should NOT crash
    chars, tokens = _count_context("system prompt", history)
    assert chars >= 13  # system prompt length
    assert tokens >= 3
