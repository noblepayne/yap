"""Tests for keyboard shortcuts."""

from pathlib import Path


root = Path(__file__).parent.parent

from yap_module import yap

ChatInput = yap.ChatInput
Yap = yap.Yap
TextArea = yap.TextArea


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
    assert ("ctrl+m", "toggle_debug") in keys_and_actions


def test_toggle_push_method_callable():
    """action_toggle_push can be called (exists and is method)."""
    app = Yap()
    assert hasattr(app, "action_toggle_push")


def test_ctrl_s_send_in_bindings():
    """Ctrl+S is bound to send action."""
    keys_and_actions = [
        (b[0], b[1]) if isinstance(b, tuple) else (b.key, b.action)
        for b in Yap.BINDINGS
    ]
    assert ("ctrl+s", "send") in keys_and_actions


def test_ctrl_r_reset_session_in_bindings():
    """Ctrl+R is bound to reset_session action."""
    keys_and_actions = [
        (b[0], b[1]) if isinstance(b, tuple) else (b.key, b.action)
        for b in Yap.BINDINGS
    ]
    assert ("ctrl+r", "reset_session") in keys_and_actions
    assert hasattr(Yap, "action_reset_session")
    assert callable(Yap.action_reset_session)
