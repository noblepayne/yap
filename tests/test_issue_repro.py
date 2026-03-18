
import sys
from pathlib import Path
import importlib.util
import json

root = Path(__file__).parent.parent
sys.path.insert(0, str(root))

spec = importlib.util.spec_from_file_location("yap", root / "yap.py")
yap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(yap)

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
                    "function": {"name": "test_tool", "arguments": "{}"}
                }
            ]
        }
    ]
    # This should NOT crash
    chars, tokens = _count_context("system prompt", history)
    assert chars >= 13  # system prompt length
    assert tokens >= 3
