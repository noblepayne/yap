import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from textual.widgets import TextArea

# Set up path to import yap.py
root = Path(__file__).parent.parent
sys.path.insert(0, str(root))

import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location("yap", root / "yap.py")
yap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(yap)

Yap = yap.Yap


@pytest.fixture
def app():
    return Yap()


def test_action_load_prompt_pushes_screen(app):
    """Verify that clicking Load Prompt pushes a FileOpen screen with a callback."""
    with patch.object(app, "push_screen") as mock_push:
        app._action_load_prompt()

        mock_push.assert_called_once()
        picker = mock_push.call_args[0][0]
        callback = mock_push.call_args[1].get("callback")

        # Verify picker type
        from textual_fspicker import FileOpen

        assert isinstance(picker, FileOpen)
        assert callable(callback)


def test_action_load_history_pushes_screen(app):
    """Verify that clicking Load History pushes a FileOpen screen with a callback."""
    with patch.object(app, "push_screen") as mock_push:
        app._action_load_history()

        mock_push.assert_called_once()
        picker = mock_push.call_args[0][0]
        callback = mock_push.call_args[1].get("callback")

        from textual_fspicker import FileOpen

        assert isinstance(picker, FileOpen)
        assert callable(callback)


def test_load_prompt_callback_updates_ui(app, tmp_path):
    """Verify that the callback from load_prompt updates the system-prompt TextArea."""
    prompt_file = tmp_path / "test_prompt.md"
    prompt_file.write_text("Test System Prompt Content")

    mock_text_area = MagicMock(spec=TextArea)

    with patch.object(app, "query_one", return_value=mock_text_area):
        with (
            patch.object(app, "_refresh_context_stats"),
            patch.object(app, "_update_status_text"),
        ):
            with patch.object(app, "push_screen") as mock_push:
                app._action_load_prompt()
                callback = mock_push.call_args[1].get("callback")

                # Execute the callback
                callback(prompt_file)

                # Assert that the text area was updated
                assert mock_text_area.text == "Test System Prompt Content"


def test_load_history_callback_updates_history(app, tmp_path):
    """Verify that the callback from load_history updates app.history."""
    history_file = tmp_path / "test_history.jsonl"
    history_file.write_text(
        '{"role": "user", "content": "hi"}\n{"role": "assistant", "content": "hello"}'
    )

    with (
        patch.object(app, "_refresh_chat_display"),
        patch.object(app, "_refresh_context_stats"),
        patch.object(app, "_update_status_text"),
        patch.object(app, "_save_history"),
    ):
        with patch.object(app, "push_screen") as mock_push:
            app._action_load_history()
            callback = mock_push.call_args[1].get("callback")

            # Execute the callback
            callback(history_file)

            assert len(app.history) == 2
            assert app.history[0]["content"] == "hi"
