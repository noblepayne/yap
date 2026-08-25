"""Integration tests for _http_chat streaming.

Uses the local SSE ChatServer from helpers.py — real HTTP, no mocks.
Tests exercise the full code path: request → SSE parse → delta callbacks →
assembled response, plus fallback, cancel, auth, and fixture replay.
"""

import json
import threading
from pathlib import Path

import requests

from helpers import ChatServer, FIXTURES_DIR, load_fixture, make_chunks
from yap_module import yap


# --- Streaming text (the main path) ---


def test_stream_text_deltas_fire(chat_server):
    """Each content delta triggers the on_delta callback."""
    chat_server.chunks = make_chunks("Hello streaming world")
    deltas = []
    result = yap._http_chat(
        chat_server.url,
        {"model": "test", "messages": [{"role": "user", "content": "hi"}]},
        10,
        on_delta=deltas.append,
    )
    assert len(deltas) == 3  # "Hello", " streaming", " world"
    assert "".join(deltas) == "Hello streaming world"


def test_stream_assembled_message_parses(chat_server):
    """Streamed result passes through _parse_response cleanly."""
    chat_server.chunks = make_chunks("test message")
    result = yap._http_chat(
        chat_server.url,
        {"model": "test", "messages": [{"role": "user", "content": "hi"}]},
        10,
        on_delta=lambda _: None,
    )
    msg = yap._parse_response(result["data"])
    assert msg["role"] == "assistant"
    assert msg["content"][0]["text"] == "test message"


def test_stream_finish_reason(chat_server):
    chat_server.chunks = make_chunks("done", finish_reason="length")
    result = yap._http_chat(
        chat_server.url,
        {"model": "test", "messages": [{"role": "user", "content": "hi"}]},
        10,
        on_delta=lambda _: None,
    )
    assert result["data"]["choices"][0]["finish_reason"] == "length"


def test_stream_captures_usage(chat_server):
    usage = {"prompt_tokens": 100, "completion_tokens": 25, "total_tokens": 125}
    chat_server.chunks = make_chunks("answer", usage=usage)
    result = yap._http_chat(
        chat_server.url,
        {"model": "test", "messages": [{"role": "user", "content": "hi"}]},
        10,
        on_delta=lambda _: None,
    )
    assert result["usage"]["total_tokens"] == 125


def test_stream_malformed_line_skipped(chat_server):
    """Malformed JSON in a data: line is tolerated, stream continues."""
    chat_server.chunks = [
        {"choices": [{"delta": {"role": "assistant"}}]},
        {"raw": "data: {not valid json\n\n"},
        {"choices": [{"delta": {"content": "ok"}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    ]
    result = yap._http_chat(
        chat_server.url,
        {"model": "test", "messages": [{"role": "user", "content": "hi"}]},
        10,
        on_delta=lambda _: None,
    )
    msg = yap._parse_response(result["data"])
    assert msg["content"][0]["text"] == "ok"


def test_stream_cancel(chat_server):
    """Cancel mid-stream closes connection and raises."""
    chat_server.chunks = [
        {"choices": [{"delta": {"content": "start"}}]},
        {"delay_ms": 500},
        {"choices": [{"delta": {"content": "never"}}]},
    ]
    cancel = threading.Event()
    # Cancel before the delay
    threading.Timer(0.05, cancel.set).start()
    try:
        yap._http_chat(
            chat_server.url,
            {"model": "test", "messages": [{"role": "user", "content": "hi"}]},
            10,
            cancel_event=cancel,
            on_delta=lambda _: None,
        )
        assert False, "Should have raised"
    except requests.exceptions.RequestException:
        pass  # expected: RequestException("cancelled") or connection teardown


# --- Non-streaming fallback ---


def test_nonstream_fallback(chat_server):
    """Server ignores stream:true and returns JSON — client falls back cleanly.

    Uses the real captured bifrost non-streaming body (reasoning field style),
    served verbatim regardless of the stream flag.
    """
    chat_server.json_body = json.loads((FIXTURES_DIR / "bifrost_nonstream.json").read_text())
    result = yap._http_chat(
        chat_server.url,
        {"model": "test", "messages": [{"role": "user", "content": "hi"}]},
        10,
        on_delta=lambda _: None,  # on_delta present forces the streaming code path
    )
    msg = yap._parse_response(result["data"])
    assert msg["role"] == "assistant"
    block_types = [b["type"] for b in msg["content"]]
    assert "thinking" in block_types, f"expected thinking from captured body: {block_types}"


# --- Auth ---


def test_auth_header_passthrough(chat_server):
    """Auth header from session is sent; payload has expected shape."""
    chat_server.require_auth = "Bearer test-token-123"
    chat_server.chunks = make_chunks("auth ok")
    session = requests.Session()
    session.headers["Authorization"] = "Bearer test-token-123"
    result = yap._http_chat(
        chat_server.url,
        {"model": "test", "messages": [{"role": "user", "content": "hi"}]},
        10,
        session=session,
        on_delta=lambda _: None,
    )
    assert len(chat_server.requests) == 1
    # Contract: what yap actually puts on the wire
    sent = chat_server.requests[0]
    assert sent["model"] == "test"
    assert sent["stream"] is True  # on_delta present → streaming requested
    assert sent["messages"][0]["content"] == "hi"


def test_auth_reject(chat_server):
    """Wrong auth gets 401; tenacity retries exhaust (~16s of backoff) then raises.

    Intentionally slow: this validates REAL retry-exhaustion behavior.
    """
    chat_server.require_auth = "Bearer real-token"
    chat_server.chunks = make_chunks("nope")
    try:
        yap._http_chat(
            chat_server.url,
            {"model": "test", "messages": [{"role": "user", "content": "hi"}]},
            5,
            on_delta=lambda _: None,
        )
        assert False, "Should have raised after retries"
    except requests.exceptions.RequestException:
        pass  # expected: HTTPError(401) reraised after 5 attempts


# --- Fixture replay (real captured behavior) ---


def test_replay_bifrost_text():
    """Bifrost text fixture: reasoning stream + usage at end."""
    server = ChatServer(chunks=load_fixture("bifrost_text")).start()
    try:
        result = yap._http_chat(
            server.url,
            {"model": "test", "messages": [{"role": "user", "content": "hi"}]},
            10,
            on_delta=lambda _: None,
        )
        msg = yap._parse_response(result["data"])
        block_types = [b["type"] for b in msg["content"]]
        assert "thinking" in block_types, f"Expected thinking block, got: {block_types}"
        assert result.get("usage") is not None
    finally:
        server.stop()


def test_replay_hermes_buffered():
    """Hermes fixture: single-delta server-buffered response."""
    server = ChatServer(chunks=load_fixture("hermes")).start()
    try:
        deltas = []
        result = yap._http_chat(
            server.url,
            {"model": "test", "messages": [{"role": "user", "content": "hi"}]},
            10,
            on_delta=deltas.append,
        )
        assert len(deltas) >= 1
        msg = yap._parse_response(result["data"])
        text = msg["content"][0]["text"] if msg["content"] else ""
        assert len(text) > 0
    finally:
        server.stop()


def test_replay_bifrost_reasoning_and_text():
    """Rich bifrost fixture: stream produces BOTH thinking and text blocks."""
    server = ChatServer(chunks=load_fixture("bifrost_reasoning_text")).start()
    try:
        deltas = []
        result = yap._http_chat(
            server.url,
            {"model": "test", "messages": [{"role": "user", "content": "hi"}]},
            10,
            on_delta=deltas.append,
        )
        # Text deltas streamed incrementally to the UI
        assert len(deltas) > 1, "content deltas should fire on_delta"
        msg = yap._parse_response(result["data"])
        block_types = [b["type"] for b in msg["content"]]
        assert "thinking" in block_types, f"missing thinking: {block_types}"
        assert "text" in block_types, f"missing text: {block_types}"
        text = "".join(b["text"] for b in msg["content"] if b["type"] == "text")
        assert "Hello streaming world" in text
    finally:
        server.stop()
