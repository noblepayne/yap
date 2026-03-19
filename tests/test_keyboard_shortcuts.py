"""Tests for keyboard shortcuts."""

import sys
from pathlib import Path

import importlib.util

root = Path(__file__).parent.parent
sys.path.insert(0, str(root))

spec = importlib.util.spec_from_file_location("yap", root / "yap.py")
yap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(yap)

ChatInput = yap.ChatInput
Yap = yap.Yap


def test_chat_input_has_ctrl_enter_binding():
    """ChatInput has ctrl+enter binding."""
    assert hasattr(ChatInput, "BINDINGS")
    keys = [b.key for b in ChatInput.BINDINGS]
    assert "ctrl+enter" in keys


def test_chat_input_ctrl_enter_priority():
    """Ctrl+enter binding has priority=True to override TextArea defaults."""
    binding = next(b for b in ChatInput.BINDINGS if b.key == "ctrl+enter")
    assert binding.priority is True


def test_chat_input_ctrl_enter_action():
    """Ctrl+enter binding calls send action."""
    binding = next(b for b in ChatInput.BINDINGS if b.key == "ctrl+enter")
    assert binding.action == "send"


def test_toggle_push_action_exists():
    """App has action_toggle_push method."""
    assert hasattr(Yap, "action_toggle_push")
    assert callable(Yap.action_toggle_push)


def test_toggle_push_in_bindings():
    """App BINDINGS includes toggle_push."""
    keys_and_actions = [
        (b[0], b[1]) if isinstance(b, tuple) else (b.key, b.action)
        for b in Yap.BINDINGS
    ]
    assert ("ctrl+p", "toggle_push") in keys_and_actions


def test_toggle_push_method_callable():
    """action_toggle_push can be called (exists and is method)."""
    app = Yap()
    assert hasattr(app, "action_toggle_push")
