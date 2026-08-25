"""Smoke tests for the TUI (headless via app.run_test).

Down payment on full TUI e2e coverage. Verifies the Tier A display path:
RichLog transcript, Markdown stream pane, copy actions.

No async pytest plugin — scenarios run through asyncio.run.
"""

import asyncio

from textual.widgets import Markdown, RichLog

from yap_module import yap


def _make_app():
    """Yap app with deterministic in-memory history."""
    app = yap.Yap()
    app.history = [
        {
            "role": "user",
            "content": [{"type": "text", "text": "What is two plus two?"}],
        },
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "Simple arithmetic."},
                {"type": "text", "text": "It is four."},
            ],
            "tool_calls": [
                {
                    "id": "call_x",
                    "type": "function",
                    "function": {
                        "name": "calculator",
                        "arguments": '{"expr": "2+2"}',
                    },
                }
            ],
        },
    ]
    return app


def test_transcript_renders_into_richlog():
    async def scenario():
        app = _make_app()
        async with app.run_test():
            log = app.query_one("#chat-history", RichLog)
            assert len(log.lines) > 0, "transcript should render lines"

    asyncio.run(scenario())


def test_stream_pane_hidden_by_default():
    async def scenario():
        app = _make_app()
        async with app.run_test():
            pane = app.query_one("#stream-pane", Markdown)
            assert not pane.has_class("streaming")

    asyncio.run(scenario())


def test_stream_update_shows_and_resets_pane():
    async def scenario():
        app = _make_app()
        async with app.run_test():
            app._update_stream_display("partial answer")
            pane = app.query_one("#stream-pane", Markdown)
            assert pane.has_class("streaming")
            # Refresh (post-completion) clears the in-flight pane
            app._refresh_chat_display()
            assert not pane.has_class("streaming")

    asyncio.run(scenario())


def test_copy_last_response_formats_tool_call():
    async def scenario():
        app = _make_app()
        copied = []
        async with app.run_test():
            app.copy_to_clipboard = lambda t: copied.append(t)
            app.action_copy_last_response()
        assert len(copied) == 1
        assert "four" in copied[0]
        # Tool call formatted as ⚙ name(args), not a dict dump
        assert "calculator(" in copied[0]

    asyncio.run(scenario())


def test_copy_transcript_includes_both_roles():
    async def scenario():
        app = _make_app()
        copied = []
        async with app.run_test():
            app.copy_to_clipboard = lambda t: copied.append(t)
            app.action_copy_transcript()
        assert len(copied) == 1
        assert "[USER]" in copied[0] and "[ASSISTANT]" in copied[0]

    asyncio.run(scenario())
