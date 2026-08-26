"""Smoke + e2e tests for the TUI (headless via app.run_test).

Verifies the per-message transcript (Tier B): Markdown assistant turns,
Collapsible reasoning, incremental append vs rebuild invalidation,
stream pane lifecycle, copy actions, and the full send→render loop.

No async pytest plugin — scenarios run through asyncio.run.
"""

import asyncio
import time

from textual.widgets import Collapsible, Markdown, Static

from helpers import ChatServer, make_chunks
from yap_module import yap


def _make_app(history=None):
    """Yap app with deterministic in-memory history."""
    app = yap.Yap()
    if history is not None:
        app.history = history
    return app


def _msg(role, text, **extra):
    msg = {"role": role, "content": [{"type": "text", "text": text}]}
    msg.update(extra)
    return msg


def _assistant(text, thoughts=None):
    content = []
    if thoughts:
        content.append({"type": "thinking", "thinking": thoughts})
    content.append({"type": "text", "text": text})
    return {"role": "assistant", "content": content}


def _md_sources(transcript):
    return [m.source for m in transcript.query(Markdown) if m.id != "stream-pane"]


def _sync(coro):
    asyncio.run(coro)


# --- Transcript structure ---


def test_transcript_renders_per_message():
    async def scenario():
        app = _make_app([
            _msg("user", "What is two plus two?"),
            _assistant("It is four.", thoughts="Simple arithmetic."),
        ])
        async with app.run_test():
            transcript = app.query_one("#transcript", yap.VerticalScroll)
            sources = _md_sources(transcript)
            assert any("four" in s for s in sources), f"markdown missing: {sources}"
            # Reasoning rendered as a collapsible block
            assert transcript.query(Collapsible)
            # Placeholder hidden once history exists
            assert not app.query_one("#transcript-empty", Static).display

    _sync(scenario())


def test_placeholder_returns_on_clear():
    async def scenario():
        app = _make_app([_msg("user", "hi")])
        async with app.run_test():
            app.history = []
            app._refresh_chat_display()
            assert app.query_one("#transcript-empty", Static).display

    _sync(scenario())


# --- Incremental invalidation (the G3 correctness surface) ---


def test_append_is_incremental():
    async def scenario():
        h0, h1 = _msg("user", "one"), _assistant("two")
        app = _make_app([h0])
        async with app.run_test():
            first_pass = len(app.query_one("#transcript", yap.VerticalScroll).children)
            old_widgets = set(app.query_one("#transcript", yap.VerticalScroll).children)
            app.history.append(h1)
            app._refresh_chat_display()
            transcript = app.query_one("#transcript", yap.VerticalScroll)
            # Incremental path keeps existing widgets mounted
            assert old_widgets <= set(transcript.children)
            assert len(transcript.children) > first_pass
            assert app._rendered_count == 2
            assert app._rendered_tail is h1

    _sync(scenario())


def test_truncation_boundary_triggers_rebuild():
    """THE incremental-correctness test: at MAX_HISTORY the head evicts and
    counts stay equal — a naive length check would silently go stale."""
    async def scenario():
        monkey_history = [_msg("user", f"m{i}") for i in range(3)]
        app = _make_app(monkey_history)
        original_max = yap.MAX_HISTORY
        yap.MAX_HISTORY = 3
        try:
            async with app.run_test() as pilot:
                before_sources = _md_sources(app.query_one("#transcript", yap.VerticalScroll))
                new_msg = _assistant("fresh")
                app.history = yap._truncate_history(app.history + [new_msg], yap.MAX_HISTORY)
                assert len(app.history) == 3  # head evicted
                app._refresh_chat_display()
                # remove() is async in Textual — pump the loop so the
                # rebuild's teardown completes before asserting.
                await pilot.pause()
                await pilot.pause()
                after_sources = _md_sources(app.query_one("#transcript", yap.VerticalScroll))
                # Rebuild fired: evicted head gone, new tail present
                joined_after = " ".join(after_sources)
                assert any("fresh" in s for s in after_sources)
                assert app._rendered_count == 3
                assert app._rendered_tail is app.history[-1]
        finally:
            yap.MAX_HISTORY = original_max

    _sync(scenario())


def test_reasoning_toggle_is_css_only():
    async def scenario():
        app = _make_app([
            _assistant("answer one", thoughts="thoughts one"),
            _assistant("answer two", thoughts="thoughts two"),
        ])
        async with app.run_test():
            transcript = app.query_one("#transcript", yap.VerticalScroll)
            widgets_before = list(transcript.children)

            class Ev:
                value = False  # toggle OFF → suppress

            app._on_show_reasoning_changed(Ev())
            blocks = list(transcript.query(".reasoning-block"))
            assert blocks and all(b.has_class("suppressed") for b in blocks)
            # No rebuild: same widget objects still mounted
            assert widgets_before == list(transcript.children)

            class EvOn:
                value = True

            app._on_show_reasoning_changed(EvOn())
            assert all(not b.has_class("suppressed") for b in transcript.query(".reasoning-block"))

    _sync(scenario())


def test_blocks_mount_suppressed_when_toggle_off():
    async def scenario():
        app = _make_app()
        app.show_reasoning = False
        async with app.run_test():
            app.history.append(_assistant("ans", thoughts="secret"))
            app._refresh_chat_display()
            blocks = list(app.query_one("#transcript", yap.VerticalScroll).query(".reasoning-block"))
            assert blocks and all(b.has_class("suppressed") for b in blocks)

    _sync(scenario())


# --- Stream pane ---


def test_stream_pane_hidden_by_default():
    async def scenario():
        app = _make_app([])
        async with app.run_test():
            pane = app.query_one("#stream-pane", Markdown)
            assert not pane.has_class("streaming")

    _sync(scenario())


def test_stream_update_shows_and_resets_pane():
    async def scenario():
        app = _make_app([])
        async with app.run_test():
            app._update_stream_display("partial answer")
            pane = app.query_one("#stream-pane", Markdown)
            assert pane.has_class("streaming")
            # Refresh (post-completion) clears the in-flight pane — both branches
            app.history.append(_assistant("done"))
            app._refresh_chat_display()
            assert not pane.has_class("streaming")

    _sync(scenario())


# --- Copy actions (history-based, widget-independent) ---


def test_copy_last_response_formats_tool_call():
    async def scenario():
        tool_call = {
            "id": "call_x",
            "type": "function",
            "function": {"name": "calculator", "arguments": '{"expr": "2+2"}'},
        }
        app = _make_app([
            _msg("user", "What is two plus two?"),
            {**_assistant("It is four."), "tool_calls": [tool_call]},
        ])
        copied = []
        async with app.run_test():
            app.copy_to_clipboard = lambda t: copied.append(t)
            app.action_copy_last_response()
        assert len(copied) == 1
        assert "four" in copied[0]
        assert "calculator(" in copied[0]

    _sync(scenario())


def test_copy_transcript_includes_both_roles():
    async def scenario():
        app = _make_app([
            _msg("user", "q"),
            _assistant("a"),
        ])
        copied = []
        async with app.run_test():
            app.copy_to_clipboard = lambda t: copied.append(t)
            app.action_copy_transcript()
        assert len(copied) == 1
        assert "[USER]" in copied[0] and "[ASSISTANT]" in copied[0]

    _sync(scenario())


# --- Config sidebar collapse ---


def test_toggle_config_flips_class():
    async def scenario():
        app = _make_app([])
        async with app.run_test():
            config = app.query_one("#config", yap.Vertical)
            assert not config.has_class("collapsed")
            app.action_toggle_config()
            assert config.has_class("collapsed")
            app.action_toggle_config()
            assert not config.has_class("collapsed")

    _sync(scenario())


def test_ctrl_g_binding_present():
    assert any(b[0] == "ctrl+g" and b[1] == "toggle_config" for b in yap.Yap.BINDINGS)


# --- Full loop e2e ---


def test_full_send_stream_render(tmp_path, monkeypatch):
    """E2E: input -> action_send -> real HTTP stream -> history + transcript.

    Final state only; bounded poll for the Markdown child so we never race
    call_from_thread ordering.
    """
    async def scenario():
        server = ChatServer(chunks=make_chunks("E2E reply")).start()
        try:
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
                assert len(server.requests) == 1, "action_send never sent a request"
                assert not app.is_loading, "request never completed"

                msg = app.history[-1]
                assert msg["role"] == "assistant"
                text = "".join(
                    b.get("text", "") for b in msg["content"] if b.get("type") == "text"
                )
                assert text == "E2E reply"

                # Bounded poll: transcript Markdown child must appear
                transcript = app.query_one("#transcript", yap.VerticalScroll)
                deadline = time.time() + 5
                while time.time() < deadline and not any(
                    "E2E reply" in s for s in _md_sources(transcript)
                ):
                    await pilot.pause(0.05)
                assert any(
                    "E2E reply" in s for s in _md_sources(transcript)
                ), f"not rendered: {_md_sources(transcript)}"
        finally:
            server.stop()

    asyncio.run(scenario())
