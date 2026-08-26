"""Smoke tests for the TUI (headless via app.run_test).

Down payment on full TUI e2e coverage. Verifies the Tier A display path:
RichLog transcript, Markdown stream pane, copy actions.

No async pytest plugin — scenarios run through asyncio.run.
"""

import asyncio
import time

from textual.widgets import Markdown, RichLog

from helpers import ChatServer, make_chunks
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


def test_full_send_stream_render(tmp_path, monkeypatch):
    """E2E: input -> action_send -> real HTTP stream -> history + RichLog.

    The complete user-visible loop against the local ChatServer. Final
    state only — no mid-stream timing assertions.
    """
    async def scenario():
        server = ChatServer(chunks=make_chunks("E2E reply")).start()
        try:
            # Patch BEFORE Yap() construction: __init__ reads HISTORY_FILE
            monkeypatch.setattr(yap, "API_URL", server.url)
            monkeypatch.setattr(yap, "HISTORY_FILE", tmp_path / "history.jsonl")
            monkeypatch.setattr(yap, "LAST_RESPONSE_FILE", tmp_path / "last.md")

            app = yap.Yap()
            async with app.run_test() as pilot:
                await pilot.pause()
                inp = app.query_one("#user-input", yap.ChatInput)
                inp.text = "hello server"
                app.action_send()

                deadline = time.time() + 15
                while time.time() < deadline:
                    await pilot.pause(0.05)
                    if len(server.requests) == 1 and not app.is_loading:
                        break
                # Diagnose silent-no-send in ms, not at the 15s deadline
                assert len(server.requests) == 1, "action_send never sent a request"
                assert not app.is_loading, "request never completed"

                msg = app.history[-1]
                assert msg["role"] == "assistant"
                text = "".join(
                    b.get("text", "") for b in msg["content"] if b.get("type") == "text"
                )
                assert text == "E2E reply"

                log = app.query_one("#chat-history", RichLog)
                rendered = "\n".join(strip.text for strip in log.lines)
                normalized = " ".join(rendered.split())
                assert "E2E reply" in normalized
        finally:
            server.stop()

    asyncio.run(scenario())
