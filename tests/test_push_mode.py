"""Integration tests for push mode building blocks over real HTTP.

Uses the shared ChatServer (helpers.py) in scripted mode — one response
per request, last entry repeats. These tests exercise the pieces the push
loop is built from (_build_payload, _http_chat, _parse_response,
_detect_yap_done, NUDGE_MESSAGE). The full TUI loop is covered in
test_tui.py.
"""

import threading
import time

import pytest
import requests

from helpers import ChatServer, make_chunks, make_toolcall_chunks
from yap_module import yap

DONE_CALL = {"id": "call_1", "name": "yap__done", "arguments": '{"summary": "Task completed"}'}
OTHER_CALL = {"id": "call_1", "name": "other_tool", "arguments": "{}"}


@pytest.fixture
def server(monkeypatch):
    """ChatServer with yap.API_URL pointed at it (auto-restored)."""
    s = ChatServer(script=[[]]).start()
    monkeypatch.setattr(yap, "API_URL", s.url)
    yield s
    s.stop()


def _payload(history):
    return yap._build_payload(
        "test-model", history, None, [yap._get_yap_done_tool()]
    )


def test_push_mode_done_on_first(server):
    """yap__done tool call round-trips and is detected."""
    server.script = [make_toolcall_chunks([DONE_CALL])]
    data = yap._http_chat(yap.API_URL, _payload([{"role": "user", "content": "go"}]), 5)
    message = yap._parse_response(data["data"])
    assert message.get("tool_calls") is not None
    assert yap._detect_yap_done(message["tool_calls"])


def test_push_mode_nudge_then_done(server):
    """Non-done call → nudge appended → second round calls yap__done.

    Also verifies the nudge actually reaches the wire: request 2 must
    contain NUDGE_MESSAGE as a user message.
    """
    server.script = [
        make_toolcall_chunks([OTHER_CALL], content="Let me think about this..."),
        make_toolcall_chunks([DONE_CALL], content="Done!"),
    ]
    history = [{"role": "user", "content": "test message"}]

    data = yap._http_chat(yap.API_URL, _payload(history), 5)
    message = yap._parse_response(data["data"])
    assert not yap._detect_yap_done(message.get("tool_calls"))

    history.append(message)
    history.append({"role": "user", "content": yap.NUDGE_MESSAGE})

    data = yap._http_chat(yap.API_URL, _payload(history), 5)
    message = yap._parse_response(data["data"])
    assert yap._detect_yap_done(message["tool_calls"])

    # Contract: the nudge went out on the wire
    sent_messages = server.requests[1]["messages"]
    assert any(
        m.get("role") == "user" and m.get("content") == yap.NUDGE_MESSAGE
        for m in sent_messages
    ), f"NUDGE_MESSAGE missing from second request: {sent_messages}"


def test_push_mode_max_iterations(server, monkeypatch):
    """Loop stops at MAX_PUSH_ITERATIONS when model never calls yap__done."""
    monkeypatch.setattr(yap, "MAX_PUSH_ITERATIONS", 3)
    server.script = [make_chunks("Thinking...")]  # repeats forever

    history = [{"role": "user", "content": "test message"}]
    iteration = 0
    while iteration < yap.MAX_PUSH_ITERATIONS:
        data = yap._http_chat(yap.API_URL, _payload(history), 5)
        message = yap._parse_response(data["data"])
        if yap._detect_yap_done(message.get("tool_calls")):
            break
        history.append(message)
        history.append({"role": "user", "content": yap.NUDGE_MESSAGE})
        iteration += 1

    assert iteration == yap.MAX_PUSH_ITERATIONS


def test_push_mode_error_handling(server):
    """Success then HTTP 500 — retries exhaust and raise (~16s of backoff).

    Intentionally slow: validates real retry-exhaustion behavior.
    """
    server.script = [
        make_chunks("First response"),
        [{"status": 500}],
    ]
    history = [{"role": "user", "content": "test message"}]

    data = yap._http_chat(yap.API_URL, _payload(history), 5)
    message = yap._parse_response(data["data"])
    assert message["content"][0]["text"] == "First response"

    history.append(message)
    history.append({"role": "user", "content": yap.NUDGE_MESSAGE})

    with pytest.raises(requests.exceptions.RequestException):
        yap._http_chat(yap.API_URL, _payload(history), 5)


def test_push_mode_detect_yap_done():
    """Pure: _detect_yap_done truth table."""
    done = [{"id": "c1", "type": "function",
             "function": {"name": "yap__done", "arguments": '{"summary": "done"}'}}]
    other = [{"id": "c1", "type": "function",
              "function": {"name": "other_tool", "arguments": "{}"}}]
    assert yap._detect_yap_done(done) is True
    assert yap._detect_yap_done(other) is False
    assert yap._detect_yap_done([]) is False
    assert yap._detect_yap_done(None) is False


def test_cancel_during_retry_backoff(server):
    """Regression: cancel during tenacity backoff must unblock immediately."""
    server.script = [[{"status": 502}]]  # repeats

    cancel_event = threading.Event()
    session = requests.Session()
    payload = yap._build_payload("test-model", [{"role": "user", "content": "hi"}])

    def cancel_after_first_failure():
        time.sleep(0.3)
        cancel_event.set()
        session.close()

    cancel_thread = threading.Thread(target=cancel_after_first_failure)
    cancel_thread.start()

    start = time.time()
    with pytest.raises(Exception):
        yap._http_chat(yap.API_URL, payload, 5, session, cancel_event)
    elapsed = time.time() - start

    cancel_thread.join()

    # Must finish in under 1s — tenacity's min backoff is 2s,
    # so if cancel didn't interrupt the sleep this would take 2s+
    assert elapsed < 1.0, f"Cancel took {elapsed:.1f}s, should be <1s"
